"""harness_audit CLI 入口。

用法：
    # 单仓库评估（可注入动态证据）
    python -m scripts.harness_audit.main --target F:\\DEEPCODE --since 30 --format both
    python -m scripts.harness_audit.main --target F:\\DEEPCODE --dynamic dynamic_evidence.json

    # 多仓库批量评估
    python -m scripts.harness_audit.main --targets F:\\DEEPCODE,C:\\repo2

    # 历史分数对比（读取各仓库历史 findings.json）
    python -m scripts.harness_audit.main --targets F:\\DEEPCODE,C:\\repo2 --compare

输出：
    <target>/analysis_output/harness_audit/YYYY-MM-DD/findings.json
    <target>/analysis_output/harness_audit/YYYY-MM-DD/report.md
    --compare 额外输出:
    <target1>/analysis_output/harness_audit/compare/compare-YYYY-MM-DD.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

from .evidence import EvidenceCollector
from .findings import generate_findings
from .report import build_contract, render_markdown, write_outputs
from .scoring import overall_score, score_all

DIM_ORDER = ("task-understanding", "controlled-execution",
             "change-validation", "reliable-delivery", "learning-capture")


def _load_dynamic(path: str) -> dict | None:
    """读取动态证据 JSON 文件。格式见 evidence.merge_dynamic docstring。"""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(f"[warn] 动态证据文件应为 JSON 对象: {path}", file=sys.stderr)
            return None
        return data
    except OSError as e:
        print(f"[error] 无法读取动态证据文件 {path}: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[error] 动态证据 JSON 解析失败 {path}: {e}", file=sys.stderr)
        return None


def audit_one(target: str, since: int, dynamic_path: str | None,
              fmt: str, out_dir: str | None) -> dict:
    """对单个仓库执行完整评估（证据收集 → 动态合并 → 评分 → 发现 → 报告）。"""
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise FileNotFoundError(f"目标目录不存在: {target}")

    # 1. 收集证据（只读静态扫描）
    collector = EvidenceCollector(target, since_days=since)
    items = collector.collect()

    # 2. 合并动态证据（MCP 采集通道，只提升不降级）
    dynamic = _load_dynamic(dynamic_path) if dynamic_path else None
    applied: list[str] = []
    if dynamic:
        applied = collector.merge_dynamic(dynamic)
        # 动态合并后刷新分数上限（merge_dynamic 内已处理，此处兜底）
        from .evidence import SCORE_CEILING
        for it in items:
            it.ceiling = SCORE_CEILING.get(it.state, 59)

    # 3. 评分
    dims = score_all(items)
    total = overall_score(dims)

    # 4. 生成发现
    findings = generate_findings(items)

    # 5. 渲染输出
    contract = build_contract(target, items, dims, findings)
    markdown = render_markdown(target, items, dims, findings, total)

    out = out_dir or os.path.join(
        target, "analysis_output", "harness_audit",
        datetime.now().strftime("%Y-%m-%d"))
    paths = write_outputs(out, contract, markdown)

    return {
        "target": target,
        "contract": contract,
        "markdown": markdown,
        "paths": paths,
        "total": total,
        "dims": dims,
        "findings": findings,
        "dynamic_applied": applied,
    }


def _print_result(r: dict, fmt: str) -> None:
    print(f"[harness-audit] 目标: {r['target']}")
    if r["dynamic_applied"]:
        print(f"[harness-audit] 动态证据已合并: {len(r['dynamic_applied'])} 条")
        for d in r["dynamic_applied"]:
            print(f"  + {d}")
    print(f"[harness-audit] 总分: {r['total']}/100")
    for d in r["dims"]:
        print(f"  {d.label:<8} {d.score:>3}  ({d.max_state}, 上限 {d.ceiling})")
    print(f"[harness-audit] Findings: {len(r['findings'])}")
    for p, v in r["paths"].items():
        print(f"[harness-audit] 已输出: {v}")
    if fmt == "json":
        print(json.dumps(r["contract"], ensure_ascii=False, indent=2))


def _history_rows(target: str) -> list[dict]:
    """扫描 <target>/analysis_output/harness_audit/*/findings.json 提取历史分数。"""
    pattern = os.path.join(target, "analysis_output", "harness_audit", "*", "findings.json")
    rows: list[dict] = []
    for fp in sorted(glob.glob(pattern)):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        summary = data.get("summary") or {}
        dims = {d.get("id"): d.get("score") for d in (summary.get("dimensions") or [])}
        scores = [dims.get(k) for k in DIM_ORDER]
        if any(s is None for s in scores):
            continue
        rows.append({
            "date": (summary.get("generatedAt") or "")[:10],
            "scores": scores,
            "total": round(sum(scores) / len(scores)),
        })
    return rows


def _render_compare(targets: list[str]) -> str:
    """渲染对比 Markdown：每个仓库一段历史趋势表 + 一张跨仓库最新对比表。"""
    lines: list[str] = []
    lines.append("# Harness Audit 多仓库历史对比")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 对比仓库: {', '.join(targets)}")
    lines.append("")

    header = "| 仓库 | 任务理解 | 受控执行 | 变更验证 | 可靠交付 | 学习沉淀 | 总分 |"
    sep = "|---|---|---|---|---|---|---|"
    latest_rows: list[tuple[str, list[int], int]] = []

    for t in targets:
        rows = _history_rows(t)
        name = os.path.basename(t) or t
        lines.append(f"## {name} ({t})")
        if not rows:
            lines.append("")
            lines.append("> 无历史评估记录（先运行单仓库评估生成 findings.json）。")
            lines.append("")
            continue
        lines.append(header)
        lines.append(sep)
        for r in rows:
            cells = [f"{s:>3}" for s in r["scores"]]
            lines.append(f"| {r['date']} | " + " | ".join(cells) + f" | {r['total']:>3} |")
        lines.append("")
        latest_rows.append((name, rows[-1]["scores"], rows[-1]["total"]))

    if latest_rows:
        lines.append("## 最新一轮对比")
        lines.append("")
        lines.append(header)
        lines.append(sep)
        for name, scores, total in latest_rows:
            cells = [f"{s:>3}" for s in scores]
            lines.append(f"| {name} | " + " | ".join(cells) + f" | {total:>3} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness-audit",
                                     description="Agent Work Loop 五维度评估引擎")
    parser.add_argument("--target", default=None,
                        help="目标仓库路径（默认当前目录；与 --targets 二选一）")
    parser.add_argument("--targets", default=None,
                        help="多仓库批量评估（逗号分隔路径，如 a,b,c）")
    parser.add_argument("--since", type=int, default=30,
                        help="证据时间窗（天，默认 30）")
    parser.add_argument("--format", choices=["json", "md", "both"], default="both",
                        help="输出格式（默认 both）")
    parser.add_argument("--out", default=None,
                        help="输出目录（默认 analysis_output/harness_audit/YYYY-MM-DD）")
    parser.add_argument("--dynamic", default=None,
                        help="动态证据 JSON 文件路径（MCP 采集通道，只提升不降级）")
    parser.add_argument("--compare", action="store_true",
                        help="历史分数对比模式（读取各仓库历史 findings.json 渲染对比表）")
    args = parser.parse_args(argv)

    # 解析目标列表
    if args.targets:
        targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    else:
        targets = [args.target or os.path.abspath(".")]

    missing = [t for t in targets if not os.path.isdir(os.path.abspath(t))]
    if missing:
        print(f"[error] 目标目录不存在: {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.compare:
        md = _render_compare(targets)
        out_dir = os.path.join(os.path.abspath(targets[0]),
                               "analysis_output", "harness_audit", "compare")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"compare-{datetime.now().strftime('%Y-%m-%d')}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[harness-audit] 对比报告已输出: {out_path}")
        print(md)
        return 0

    # 常规（单仓库或多仓库）评估
    for t in targets:
        try:
            r = audit_one(t, args.since, args.dynamic, args.format, args.out)
        except FileNotFoundError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        _print_result(r, args.format)
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
