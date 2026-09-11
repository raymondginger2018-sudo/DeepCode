#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
② 每日复盘自动生成器 — 读 SQLite 行情 → 本地 qwen 生成复盘 → 落盘 knowledge-vault
══════════════════════════════════════════════════════════════════════════════
数据源: kline_cache.db (daily_review_meta / daily_ladder / daily_sector)
输出:   knowledge-vault/notes/市场复盘_YYYYMMDD.md (Obsidian)

用法:
  # 最新交易日
  python daily_review.py

  # 指定日期
  python daily_review.py --date 20260731

  # 只打印不写文件
  python daily_review.py --date 20260731 --dry-run

  # 自定义数据源 / 输出路径
  python daily_review.py --db path/to/kline_cache.db --out path/to/review.md
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ollama_client import ensure_ollama, generate, model_available, LLM_MODEL, YG_MODEL

DEFAULT_DB = Path(__file__).resolve().parents[2] / "quant_trading/kline_cache.db"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "knowledge-vault/notes"

SYSTEM_PROMPT = (
    "你是资深 A 股短线复盘分析师。根据提供的结构化数据，撰写一篇简洁专业的每日复盘，"
    "使用 Markdown，观点明确，数据准确引用，不编造数据。"
)


def fetch_data(db_path: Path, trade_date: str) -> dict:
    """从 kline_cache.db 拉取复盘所需数据"""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        meta = con.execute(
            "SELECT * FROM daily_review_meta WHERE trade_date=?", (trade_date,)
        ).fetchone()
        if meta is None:
            # 自动找最近的有效交易日 (涨停数>0, 避免空数据/非交易日如 20260801)
            if not trade_date or not meta:
                meta = con.execute(
                    "SELECT * FROM daily_review_meta WHERE total_limit_up > 0 "
                    "ORDER BY trade_date DESC LIMIT 1"
                ).fetchone()
            if meta is None:
                raise RuntimeError("daily_review_meta 无任何有效数据")
        date_used = meta["trade_date"]

        sectors = [dict(r) for r in con.execute(
            "SELECT sector_name, change_pct, leading_stock_name, fund_flow, up_count, "
            "down_count, turnover_bn, momentum_5d, rank FROM daily_sector "
            "WHERE trade_date=? ORDER BY rank LIMIT 10", (date_used,)
        )]

        # 扩展维度: 板块资金流 (主力净流入 Top / 净流出 Top)
        sector_flows = [dict(r) for r in con.execute(
            "SELECT sector_name, main_net_inflow FROM sector_fund_flow "
            "WHERE trade_date=? ORDER BY main_net_inflow DESC LIMIT 8", (date_used,)
        )]
        sector_flows_out = [dict(r) for r in con.execute(
            "SELECT sector_name, main_net_inflow FROM sector_fund_flow "
            "WHERE trade_date=? AND main_net_inflow < 0 "
            "ORDER BY main_net_inflow ASC LIMIT 5", (date_used,)
        )]

        # 板块热度榜: daily_sector 按热度分排序 (涨幅*50 + 资金流*0.5 + 5日动量*5, 取 top8)
        sector_heat = [dict(r) for r in con.execute(
            "SELECT sector_name, change_pct, fund_flow, momentum_5d, leading_stock_name, "
            "(COALESCE(change_pct,0)*50 + COALESCE(fund_flow,0)*0.5 + COALESCE(momentum_5d,0)*5) AS heat_score "
            "FROM daily_sector WHERE trade_date=? "
            "ORDER BY heat_score DESC LIMIT 8", (date_used,)
        )]

        # 扩展维度: 市场状态历史
        market_state = None
        ms = con.execute(
            "SELECT state, score, reversal_risk, trend_duration_days, dow_phase FROM market_state_history "
            "WHERE trade_date=?", (date_used,)
        ).fetchone()
        if ms:
            market_state = dict(ms)
            # dow_phase 是 JSON 字符串，只取 phase/confidence
            try:
                dp = json.loads(market_state["dow_phase"] or "{}")
                market_state["dow_phase"] = f"{dp.get('phase', '—')}(置信度{dp.get('confidence', '—')})"
            except Exception:
                market_state["dow_phase"] = "—"

        # 妖股引擎: 从知识库笔记解析 (情绪周期 + 核心候选)
        yaogu = fetch_yaogu(DEFAULT_OUT_DIR, date_used)

        return {"date": date_used, "meta": dict(meta), "sectors": sectors,
                "sector_flows": sector_flows, "sector_flows_out": sector_flows_out,
                "sector_heat": sector_heat,
                "market_state": market_state, "yaogu": yaogu}
    finally:
        con.close()


def fetch_yaogu(vault_dir: Path, trade_date: str) -> dict:
    """从知识库妖股笔记解析当日妖股引擎数据 (情绪周期 + 核心候选)"""
    result: dict = {"cycle": None, "position": "", "candidates": []}

    # ① 妖股情绪周期日历: 匹配当日行 日期|周期|得分|涨停|炸板|最高板|晋级率|炸板率|溢价
    cal = vault_dir / "妖股情绪周期日历-近30日.md"
    if cal.exists():
        for line in cal.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("|") and f"| {trade_date} " in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 9:
                    result["cycle"] = {
                        "date": cells[0], "phase": cells[1], "score": cells[2],
                        "limit_up": cells[3], "zha_ban": cells[4], "max_board": cells[5],
                        "promo_rate": cells[6], "zha_rate": cells[7], "premium": cells[8],
                    }
                break

    # ② 妖股核心候选: {trade_date}-妖股核心候选.md (排名|代码|名称|连板|辨识度|席位|理由)
    cand = vault_dir / f"{trade_date}-妖股核心候选.md"
    if cand.exists():
        text = cand.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"仓位建议:\s*([^\n]+)", text)
        if m:
            result["position"] = m.group(1).strip()
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 6 and cells[0].isdigit() and cells[1].isdigit():
                result["candidates"].append({
                    "rank": cells[0], "code": cells[1], "name": cells[2],
                    "board": cells[3], "recog": cells[4], "seat": cells[5],
                    "reason": cells[6] if len(cells) > 6 else "",
                })
    return result


def build_prompt(data: dict) -> str:
    meta = data["meta"]
    top_themes = meta.get("top_themes") or ""
    # 兼容 JSON 字符串
    if isinstance(top_themes, str) and top_themes.strip().startswith("{"):
        try:
            themes = json.loads(top_themes)
            top_themes = "、".join(f"{k}({v}个)" for k, v in sorted(
                themes.items(), key=lambda x: -x[1])[:8])
        except Exception:
            pass

    def _v(v, suffix="", default="—"):
        return f"{v}{suffix}" if v not in (None, "", 0) else default

    prompt = [f"交易日期: {data['date']}", "\n【市场概况】"]
    prompt.append(f"- 涨停家数: {meta.get('total_limit_up', 0)} | 最高连板: {meta.get('max_board', 1)}")
    prompt.append(f"- 连板结构: 2板 {meta.get('board_2_count', 0)} 家, 3板 {meta.get('board_3_count', 0)} 家, "
                  f"4板及以上 {meta.get('board_4plus_count', 0)} 家, 首板 {meta.get('new_board_count', 0)} 家")
    prompt.append(f"- 上证指数: {_v(meta.get('sh_index'))} ({_v(meta.get('sh_change_pct'), '%')}) | "
                  f"深证成指: {_v(meta.get('sz_index'))} ({_v(meta.get('sz_change_pct'), '%')})")
    prompt.append(f"- 两市成交额: {_v(meta.get('total_turnover_bn'), ' 亿元')} | 北向净流入: {_v(meta.get('north_net_flow'), ' 亿')}")
    prompt.append(f"- 涨/跌/平家数: {meta.get('up_stock_count', 0)} / {meta.get('down_stock_count', 0)} / {meta.get('flat_stock_count', 0)}")
    prompt.append(f"- 情绪值: {_v(meta.get('sentiment'))} | 市场状态: {_v(meta.get('market_state'))} | 市场评分: {_v(meta.get('market_score'))}")
    if top_themes:
        prompt.append(f"- 热门题材: {top_themes}")
    if meta.get("key_observation"):
        prompt.append(f"- 关键观察: {meta['key_observation']}")

    prompt.append("\n【板块表现 Top10】")
    if data["sectors"]:
        for s in data["sectors"]:
            prompt.append(f"- {s['sector_name']} 涨{s['change_pct']}% 领涨{s.get('leading_stock_name') or '—'} "
                          f"资金流{s.get('fund_flow') or 0}亿 5日动量{s.get('momentum_5d')}%")
    else:
        prompt.append("- (无数据)")

    prompt.append("\n【板块主力资金净流入 Top】")
    if data["sector_flows"]:
        for s in data["sector_flows"]:
            prompt.append(f"- {s['sector_name']} 主力净流入{s['main_net_inflow']}亿")
    else:
        prompt.append("- (无数据)")

    prompt.append("\n【板块主力资金净流出 Top】")
    if data["sector_flows_out"]:
        for s in data["sector_flows_out"]:
            prompt.append(f"- {s['sector_name']} 主力净流出{abs(s['main_net_inflow'])}亿")
    else:
        prompt.append("- (无数据)")

    prompt.append("\n【市场状态 (市场状态历史)】")
    ms = data["market_state"]
    if ms:
        prompt.append(f"- 状态: {ms.get('state', '—')} | 评分: {ms.get('score', '—')} | "
                      f"反转风险: {ms.get('reversal_risk', '—')} | 趋势持续: {ms.get('trend_duration_days', '—')}天 | "
                      f"道氏阶段: {ms.get('dow_phase', '—')}")
    else:
        prompt.append("- (无数据)")

    prompt.append("\n【板块热度榜 Top8】")
    if data["sector_heat"]:
        for s in data["sector_heat"]:
            prompt.append(f"- {s['sector_name']} 热度分{s['heat_score']:.0f} 涨{s['change_pct']}% "
                          f"资金流{s.get('fund_flow') or 0}亿 5日动量{s.get('momentum_5d') or 0}% "
                          f"领涨{s.get('leading_stock_name') or '—'}")
    else:
        prompt.append("- (无数据)")

    prompt.append("\n【妖股引擎】")
    yg = data["yaogu"]
    cyc = yg.get("cycle")
    if cyc:
        prompt.append(f"- 情绪周期: {cyc['phase']} (得分 {cyc['score']}) | 涨停 {cyc['limit_up']} | "
                      f"炸板 {cyc['zha_ban']} | 最高 {cyc['max_board']}板 | "
                      f"晋级率 {cyc['promo_rate']} | 炸板率 {cyc['zha_rate']} | 溢价 {cyc['premium']}")
    if yg.get("position"):
        prompt.append(f"- 仓位建议: {yg['position']}")
    if yg.get("candidates"):
        for c in yg["candidates"][:5]:
            prompt.append(f"- {c['rank']}. {c['name']}({c['code']}) {c['board']} 辨识度{c['recog']} "
                          f"席位{c.get('seat') or '—'} {c.get('reason') or ''}")
    if not cyc and not yg.get("candidates"):
        prompt.append("- (无数据)")

    prompt.append("\n请输出复盘报告，严格按以下结构组织，每节只使用对应数据: ")
    prompt.append("## 一、市场概况 (只用【市场概况】和【市场状态】数据，含情绪)\n"
                  "## 二、板块热度 (只用【板块热度榜】数据: 涨幅/资金流/动量/领涨股)\n"
                  "## 三、妖股引擎 (只用【妖股引擎】数据: 情绪周期 + 仓位 + 核心候选，重点分析)\n"
                  "## 四、板块轮动 (只用【板块表现】和【板块主力资金】数据: 涨幅/领涨股/动量 + 主力净流入方向)\n"
                  "## 五、资金与信号 (只用【板块主力资金】数据: 净流入/净流出排行 + 资金信号解读)\n"
                  "## 六、风险提示与明日关注 (总结以上)")
    prompt.append("要求: 数据准确引用；标注(无数据)的章节必须完全省略，不要输出其标题；逻辑清晰，每节 2-4 句，整体 400-600 字。")
    return "\n".join(prompt)


def clean_markdown(content: str) -> str:
    """删除内容为'无数据'的空章节 (qwen 3b 常不遵守'省略标题'指令)"""
    sections = re.split(r"(?m)^(## .+)$", content)
    out = []
    i = 0
    while i < len(sections):
        if sections[i].startswith("## ") and i + 1 < len(sections):
            # 标题本身带"(无数据)" → 整节删除
            if "无数据" in sections[i] and len(sections[i]) <= 30:
                i += 2
                continue
            body = sections[i + 1]
            stripped = "".join(body.split())[:20]
            if ("无数据" in stripped or "未发现" in stripped) and len(stripped) <= 15:
                i += 2  # 跳过标题和空 body
                continue
            out.append(sections[i])
        else:
            out.append(sections[i])
        i += 1
    result = "".join(out)
    # 清理多余空行
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def render_markdown(date_str: str, content: str) -> str:
    dt = datetime.strptime(date_str, "%Y%m%d")
    return (
        "---\n"
        "type: daily\n"
        f"date: {dt.strftime('%Y-%m-%d')}\n"
        "tags: [复盘, 每日, 市场温度]\n"
        "generator: ollama-tools/daily_review\n"
        "---\n\n"
        f"# 市场复盘 {dt.strftime('%Y-%m-%d')}\n\n"
        f"{content.strip()}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(prog="daily_review", description="本地 qwen 生成每日复盘")
    ap.add_argument("--date", default="", help="交易日期 YYYYMMDD (默认最新)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="kline_cache.db 路径")
    ap.add_argument("--out", default="", help="输出文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    ap.add_argument("--prepare", action="store_true", default=True, help="生成前先准备数据 (默认开启)")
    ap.add_argument("--no-prepare", action="store_true", help="跳过数据准备")
    ap.add_argument("--model", default=YG_MODEL, help=f"生成模型 (默认 {YG_MODEL}, 不可用自动回退 qwen2.5:3b)")
    args = ap.parse_args()

    if not ensure_ollama():
        return 1

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[daily_review] ❌ 数据库不存在: {db_path}", file=sys.stderr)
        return 1

    # ① 先确定目标交易日 (取数据最新日, prepare 用同一日期)
    try:
        data = fetch_data(db_path, args.date)
    except Exception as e:
        print(f"[daily_review] ❌ 读取数据失败: {e}", file=sys.stderr)
        return 1
    target_date = data["date"]

    # ② 数据准备: 检查并拉取缺失数据源 (复盘数据源不能缺失)
    if args.prepare and not args.no_prepare and not args.dry_run:
        print(f"[daily_review] 🔄 开始数据准备 ({target_date}) ...", file=sys.stderr)
        prepare_script = str(Path(__file__).parent / "prepare_data.py")
        import subprocess
        code = subprocess.call([sys.executable, prepare_script, "--date", target_date])
        if code != 0:
            print(f"[daily_review] ⚠️ 数据准备未完全就绪 (缺章节将自动省略), 继续生成", file=sys.stderr)
        # 准备后重新读取 (可能补了新数据)
        try:
            data = fetch_data(db_path, target_date)
        except Exception as e:
            print(f"[daily_review] ❌ 重新读取数据失败: {e}", file=sys.stderr)
            return 1

    print(f"[daily_review] 📅 复盘日期: {data['date']} "
          f"(涨停 {data['meta'].get('total_limit_up')} 家, 最高板 {data['meta'].get('max_board')})", file=sys.stderr)

    # 模型选择: 默认 yaogu-analyst (领域定制), 不可用回退 qwen2.5:3b
    model = args.model
    if model == YG_MODEL and not model_available(YG_MODEL):
        print("[daily_review] ⚠️ yaogu-analyst 不可用, 回退 qwen2.5:3b", file=sys.stderr)
        model = LLM_MODEL
    content = generate(build_prompt(data), model=model, system=SYSTEM_PROMPT, temperature=0.4)
    if not content:
        print("[daily_review] ❌ 本地模型未返回内容", file=sys.stderr)
        return 1

    md = render_markdown(data["date"], clean_markdown(content))
    if args.dry_run:
        print(md)
        return 0

    out_path = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"市场复盘_{data['date']}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[daily_review] ✅ 复盘已生成: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
