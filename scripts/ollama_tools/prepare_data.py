#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复盘数据准备器 — 生成复盘前确保所有数据源齐备 (检查 → 缺失则拉取)
═══════════════════════════════════════════════════════════════════
数据源清单 (与 daily_review.py 复盘模板一一对应):
  1. daily_review_meta / daily_ladder / daily_sector  ← daily_review_db.py update
     (daily_sector 同时供给「板块热度榜」章节, 每天全覆盖)
  2. sector_fund_flow          ← sector_data_engine (东财板块资金流)
  3. 妖股笔记 (情绪周期日历+核心候选) ← run_monster_update.py --skip-backfill

用法:
  python prepare_data.py                  # 准备最近交易日全部数据源
  python prepare_data.py --date 20260731  # 指定交易日
  python prepare_data.py --check-only     # 只检查不拉取
  python prepare_data.py --source yaogu   # 只准备指定源 (review/flow/yaogu)

退出码: 0=全部就绪, 1=有源缺失或拉取失败
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Windows 控制台 UTF-8 兼容 (emoji/中文输出，避免 GBK UnicodeEncodeError)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 环境路径
ROOT = Path(__file__).resolve().parents[2]                      # F:\DEEPCODE
QUANT_DIR = ROOT / "quant_trading"                              # 量化模块
DB_PATH = QUANT_DIR / "kline_cache.db"
VAULT_NOTES = ROOT / "knowledge-vault" / "notes"

# 包根必须最先入 sys.path (否则 quant_trading 会被解析成空 namespace package 并缓存, 后续无法修正)
sys.path.insert(0, str(QUANT_DIR.parent))  # F:\DEEPCODE — quant_trading 包根
sys.path.insert(0, str(QUANT_DIR))
try:
    from quant_trading.holiday_calendar import is_trading_day
except Exception:
    is_trading_day = None

TIMEOUT_REVIEW = 180    # daily_review_db 更新超时
TIMEOUT_MONSTER = 600   # 妖股引擎超时 (含回填，较慢)


def log(msg: str) -> None:
    print(f"[prepare] {msg}", flush=True)


def latest_trade_date() -> str:
    """最近有效交易日 (优先: 库中涨停数>0 的最新日期, 避免空数据/非交易日)"""
    try:
        con = sqlite3.connect(str(DB_PATH))
        r = con.execute(
            "SELECT MAX(trade_date) FROM daily_review_meta WHERE total_limit_up > 0"
        ).fetchone()
        con.close()
        if r and r[0]:
            return r[0]
    except Exception:
        pass
    # 兜底: holiday_calendar 往回找
    d = datetime.now().date()
    if is_trading_day:
        for _ in range(30):
            if is_trading_day(d):
                return d.strftime("%Y%m%d")
            d -= timedelta(days=1)
    return datetime.now().strftime("%Y%m%d")


# ═══════════════════════════════════ 检查 ═══════════════════════════════════

def check_table(db: Path, table: str, col: str, date: str) -> int:
    """返回某表在目标日期的行数"""
    try:
        con = sqlite3.connect(str(db))
        n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (date,)).fetchone()[0]
        con.close()
        return n
    except Exception:
        return -1


def check_yaogu_notes(date: str) -> bool:
    """妖股引擎数据可用: 情绪周期日历含目标日 或 核心候选文件存在 (任一即可)
    (核心候选为当日分析产物, 历史日无法回补; 有情绪周期即可生成妖股引擎章节)"""
    cal = VAULT_NOTES / "妖股情绪周期日历-近30日.md"
    ok_cal = False
    if cal.exists():
        ok_cal = f"| {date} " in cal.read_text(encoding="utf-8", errors="replace")
    ok_cand = (VAULT_NOTES / f"{date}-妖股核心候选.md").exists()
    return ok_cal or ok_cand


def check_review(date: str) -> bool:
    """复盘基础数据齐备: meta/ladder/sector 三表都有目标日数据"""
    n_meta = check_table(DB_PATH, "daily_review_meta", "trade_date", date)
    n_ladder = check_table(DB_PATH, "daily_ladder", "trade_date", date)
    n_sector = check_table(DB_PATH, "daily_sector", "trade_date", date)
    return n_meta > 0 and n_ladder > 0 and n_sector > 0


def check_all(date: str) -> dict:
    """检查全部数据源，返回 {源: 状态描述}"""
    return {
        "review(meta/ladder/sector)": "✅" if check_review(date) else "❌",
        "sector_fund_flow": f"{check_table(DB_PATH, 'sector_fund_flow', 'trade_date', date)} 行",
        "yaogu_notes": "✅" if check_yaogu_notes(date) else "❌",
    }


# ═══════════════════════════════════ 拉取 ═══════════════════════════════════

def run_cmd(args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """执行子命令，返回 (退出码, 末尾输出)。PYTHONPATH 指向 quant_trading 父目录 (包导入)"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(QUANT_DIR.parent)  # F:\DEEPCODE — quant_trading 包根
    try:
        proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
        tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-200:]
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return -1, f"超时 (>={timeout}s)"
    except Exception as e:
        return -2, str(e)


def prepare_review(date: str) -> bool:
    """daily_review_meta + daily_ladder + daily_sector"""
    log(f"🔄 拉取复盘基础数据 (daily_review_db update {date}) ...")
    code, tail = run_cmd([sys.executable, "reports/daily_review_db.py", "update", "--date", date],
                         QUANT_DIR, TIMEOUT_REVIEW)
    ok = code == 0
    log(f"   {'✅' if ok else '⚠️ 失败'} (exit={code}) {tail.strip()[:200]}")
    return ok


def prepare_sector_flow(date: str) -> bool:
    """sector_fund_flow — 优先 Tushare (moneyflow_ind_ths), 回退东财"""
    # ① Tushare 官方数据源 (符合回退链规范)
    try:
        sys.path.insert(0, str(QUANT_DIR.parent))  # 包根优先
        sys.path.insert(0, str(QUANT_DIR))
        from quant_trading.data.tushare_engine import fetch_sector_moneyflow
        df = fetch_sector_moneyflow(date)
        if df is not None and len(df) > 0:
            rows = [(str(r.get("industry", "")), float(r.get("net_amount") or 0))
                    for _, r in df.iterrows() if r.get("industry")]
            if rows:
                con = sqlite3.connect(str(DB_PATH))
                con.executemany(
                    "INSERT INTO sector_fund_flow (sector_name, trade_date, main_net_inflow) "
                    "VALUES (?,?,?) ON CONFLICT(sector_name, trade_date) DO UPDATE SET "
                    "main_net_inflow=excluded.main_net_inflow",
                    [(name, date, v) for name, v in rows])
                con.commit()
                con.close()
                log(f"   ✅ Tushare 写入 {len(rows)} 条板块资金流 (net_amount 亿元)")
                return True
        log("   ⚠️ Tushare 资金流无数据, 回退东财")
    except Exception as e:
        log(f"   ⚠️ Tushare 资金流失败: {e}, 回退东财")

    # ② 回退: 东财板块资金流
    try:
        sys.path.insert(0, str(QUANT_DIR))
        from data.sector_data_engine import _fetch_sector_fund_flow_em, _save_sector_fund_flow
        df = _fetch_sector_fund_flow_em()
        if df is None or df.empty:
            log("   ⚠️ 东财资金流无数据 (可能非交易时段)")
            return False
        n = _save_sector_fund_flow(df)
        log(f"   ✅ 东财写入 {n} 条板块资金流")
        return n > 0
    except Exception as e:
        log(f"   ⚠️ 东财拉取失败: {e}")
        return False


def prepare_yaogu(date: str) -> bool:
    """妖股笔记 — run_monster_update (回填 + 核心候选 + 情绪周期日历)"""
    log(f"🔄 拉取妖股引擎 (run_monster_update --skip-backfill {date}) ...")
    code, tail = run_cmd(
        [sys.executable, "scripts/run_monster_update.py", "--skip-backfill", "--date", date],
        QUANT_DIR, TIMEOUT_MONSTER)
    ok = code == 0 and check_yaogu_notes(date)
    log(f"   {'✅' if ok else '⚠️ 失败'} (exit={code}) {tail.strip()[:200]}")
    return ok


# ═══════════════════════════════════ 调度 ═══════════════════════════════════

SOURCES = {
    "review": (prepare_review, "daily_review_meta", "trade_date"),
    "flow": (prepare_sector_flow, "sector_fund_flow", "trade_date"),
    "yaogu": (prepare_yaogu, None, None),
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="prepare_data", description="复盘数据准备器")
    ap.add_argument("--date", default="", help="交易日 YYYYMMDD (默认最近交易日)")
    ap.add_argument("--check-only", action="store_true", help="只检查不拉取")
    ap.add_argument("--source", default="", help="只准备指定源: review/flow/yaogu")
    args = ap.parse_args()

    date = args.date or latest_trade_date()
    log(f"🎯 目标交易日: {date}")

    # 检查全部
    status = check_all(date)
    for k, v in status.items():
        ok = "✅" in str(v) or (str(v).strip().endswith("行") and int(str(v).split()[0]) > 0)
        log(f"   {'✅' if ok else '❌'} {k}: {v}")

    if args.check_only:
        missing = [k for k, v in status.items()
                   if "✅" not in str(v) and (not str(v).strip().endswith("行") or int(str(v).split()[0]) == 0)]
        log(f"检查完成: 缺失 {len(missing)} 项 → {missing}")
        return 0 if not missing else 1

    # 拉取缺失项
    missing = []
    for name, (func, table, col) in SOURCES.items():
        if args.source and name != args.source:
            continue
        need = not check_review(date) if name == "review" else (
            check_table(DB_PATH, table, col, date) == 0 if table else not check_yaogu_notes(date))
        if not need:
            log(f"   ✅ {name} 已有目标日数据，跳过")
            continue
        t0 = time.time()
        ok = func(date)
        elapsed = time.time() - t0
        log(f"   耗时 {elapsed:.0f}s")
        if not ok:
            missing.append(name)
            continue
        # 拉取后复查目标日
        if table:
            if check_table(DB_PATH, table, col, date) == 0:
                missing.append(name)
        elif not check_yaogu_notes(date):
            missing.append(name)

    if missing:
        log(f"❌ 仍有 {len(missing)} 个数据源未就绪: {missing} (复盘对应章节将省略)")
        return 1
    log("🎉 全部数据源就绪，可以生成复盘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
