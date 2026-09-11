#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量补生成历史复盘 — 对缺失交易日逐个: 先准备数据 → 再生成复盘
════════════════════════════════════════════════════════════════
用法:
  python batch_review.py                  # 补全部缺失交易日 (默认自动先拉数据)
  python batch_review.py --date 20260713 20260714   # 指定日期 (可多个)
  python batch_review.py --dry-run        # 只列出待补清单
  python batch_review.py --skip-prepare   # 跳过数据准备 (数据已齐, 快速)

说明:
  - 复盘内部 --prepare 默认开启, 每个日期生成前自动拉缺失数据
  - 数据源无法回补的历史缺口 (如 sector_fund_flow 7/8~7/15、妖股核心候选)
    复盘章节自动省略, 不编造
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Windows 控制台 UTF-8 兼容
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "quant_trading" / "kline_cache.db"
VAULT_NOTES = ROOT / "knowledge-vault" / "notes"
REVIEW_SCRIPT = Path(__file__).parent / "daily_review.py"


def log(msg: str) -> None:
    print(f"[batch] {msg}", flush=True)


def missing_dates() -> list[str]:
    """所有有效交易日 - 已有复盘文件 = 待补清单"""
    con = sqlite3.connect(str(DB_PATH))
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT trade_date FROM daily_review_meta WHERE total_limit_up>0 "
        "ORDER BY trade_date")]
    con.close()
    have = {p.stem.replace("市场复盘_", "") for p in VAULT_NOTES.glob("市场复盘_*.md")}
    return [d for d in dates if d not in have]


def generate_one(date: str, skip_prepare: bool) -> tuple[bool, str]:
    """生成单日复盘, 返回 (成功?, 说明)"""
    args = [sys.executable, str(REVIEW_SCRIPT), "--date", date]
    if skip_prepare:
        args.append("--no-prepare")
    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    tail = (proc.stdout or "").strip().splitlines()
    info = tail[-1] if tail else (proc.stderr or "").strip().splitlines()[-1]
    ok = proc.returncode == 0 and (VAULT_NOTES / f"市场复盘_{date}.md").exists()
    return ok, f"{info} ({time.time()-t0:.0f}s)"


def main() -> int:
    ap = argparse.ArgumentParser(prog="batch_review", description="批量补生成历史复盘")
    ap.add_argument("--date", nargs="+", default=[], help="指定日期 (默认补全部缺失)")
    ap.add_argument("--dry-run", action="store_true", help="只列出待补清单")
    ap.add_argument("--skip-prepare", action="store_true", help="跳过数据准备")
    args = ap.parse_args()

    dates = sorted(set(args.date)) if args.date else missing_dates()
    if not dates:
        log("🎉 没有缺失的复盘，全部已生成")
        return 0
    log(f"📋 待补 {len(dates)} 天: {dates}")

    if args.dry_run:
        return 0

    ok_days, fail_days = [], []
    for i, d in enumerate(dates, start=1):
        log(f"[{i}/{len(dates)}] 🔄 {d} 数据准备 + 复盘生成 ...")
        ok, info = generate_one(d, args.skip_prepare)
        if ok:
            ok_days.append(d)
            log(f"   ✅ {d}: {info}")
        else:
            fail_days.append(d)
            log(f"   ❌ {d}: {info}")

    log(f"📊 完成: 成功 {len(ok_days)} 天, 失败 {len(fail_days)} 天")
    if fail_days:
        log(f"   ❌ 失败: {fail_days}")
        return 1
    log("🎉 全部补生成完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
