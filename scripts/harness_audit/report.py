"""报告渲染模块：输出 findings.json（契约 v25 兼容）+ 中文 Markdown 报告。"""
from __future__ import annotations

import json
import os
from datetime import datetime

from .evidence import CHECK_ITEMS, ItemEvidence
from .findings import Finding
from .scoring import DimensionScore


def build_contract(target: str, items: list[ItemEvidence], dims: list[DimensionScore],
                   findings: list[Finding], locale: str = "zh") -> dict:
    """构建 findings.json（对齐 better-harness reportContractVersion 25 契约）。"""
    return {
        "summary": {
            "projectName": os.path.basename(os.path.abspath(target)),
            "target": os.path.abspath(target),
            "locale": locale,
            "modelId": "agent-work-loop-v1-deepcode",
            "reportContractVersion": 25,
            "generatedAt": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "overview": "基于 Agent Work Loop 五维度模型的 DEEPCODE 工作流自评。",
            "dimensions": [
                {
                    "id": d.dimension,
                    "label": d.label,
                    "score": d.score,
                    "maxEvidenceState": d.max_state,
                    "ceiling": d.ceiling,
                    "summary": _dimension_summary(d),
                }
                for d in dims
            ],
            "aiAgentPractice": {
                "inspectedSurfaces": [
                    "Rules", "Skills", "Permissions", "Hooks",
                    "Checkpoint", "Tests", "Telemetry", "Memory",
                ],
                "coverageRows": [
                    {"surface": "Rules", "scopes": ["Project"], "count": 2,
                     "paths": ["AGENTS.md", "CLAUDE.md"]},
                    {"surface": "Skills", "scopes": ["Project"], "count": 1,
                     "paths": [".deepcode/skills/"]},
                    {"surface": "Tests", "scopes": ["Project"], "count": 1,
                     "paths": ["tests/"]},
                ],
            },
        },
        "findings": [
            {
                "id": f.id,
                "title": f.title,
                "severity": f.severity,
                "reason": f.reason,
                "dimensionRefs": f.dimensionRefs,
                "itemRefs": f.itemRefs,
                "aiFixPrompt": f.aiFixPrompt,
                "expectedArtifact": f.expectedArtifact,
                "expectedOutput": f.expectedOutput,
            }
            for f in findings
        ],
        "items": [
            {
                "dimension": it.dimension,
                "item": it.item,
                "label": it.label,
                "state": it.state,
                "ceiling": it.ceiling,
                "evidence": it.evidence,
            }
            for it in items
        ],
    }


def _dimension_summary(d: DimensionScore) -> str:
    states = ", ".join(f"{it.label}:{it.state}" for it in d.items)
    return f"最高证据状态 {d.max_state}（上限 {d.ceiling}）| {states}"


def render_markdown(target: str, items: list[ItemEvidence], dims: list[DimensionScore],
                    findings: list[Finding], overall: int) -> str:
    """渲染中文 Markdown 报告。"""
    lines = [
        "# DEEPCODE Harness Audit 报告",
        "",
        f"- **目标仓库**：`{os.path.abspath(target)}`",
        f"- **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **模型**：Agent Work Loop 五维度评估（DEEPCODE 落地版 v1）",
        f"- **总分**：**{overall}** / 100",
        "",
        "## 五维度评分",
        "",
        "| 维度 | 分数 | 最高证据状态 | 绝对上限 |",
        "|---|---|---|---|",
    ]
    for d in dims:
        lines.append(f"| {d.label} | **{d.score}** | {d.max_state} | {d.ceiling} |")
    lines.append("")

    # 15 检查项明细
    lines += ["## 检查项明细（15 项）", "",
              "| 维度 | 检查项 | 证据状态 | 证据 |", "|---|---|---|---|"]
    for it in items:
        ev = "；".join(it.evidence) if it.evidence else "（无证据）"
        lines.append(f"| {it.label} | `{it.item}` | **{it.state}** | {ev} |")
    lines.append("")

    # Findings
    lines += ["## Findings", ""]
    if not findings:
        lines += ["🎉 未发现低于最低要求的差距项。", ""]
    else:
        lines += [f"共 **{len(findings)}** 条发现（分数不产生/抑制发现，仅差距+修复+验证路径）。", ""]
        for i, f in enumerate(findings, 1):
            lines += [
                f"### {i}. [{f.severity}] {f.title}",
                "",
                f"- **证据链**：{f.reason}",
                f"- **关联维度**：{', '.join(f.dimensionRefs)} / 检查项：{', '.join(f.itemRefs)}",
                f"- **修复指令（给 Agent）**：{f.aiFixPrompt}",
                f"- **预期产物**：{f.expectedArtifact}",
                f"- **验证路径**：{'; '.join(f.expectedOutput)}",
                "",
            ]
    return "\n".join(lines)


def write_outputs(out_dir: str, contract: dict, markdown: str) -> dict:
    """写入 findings.json + report.md，返回文件路径映射。"""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "findings.json")
    md_path = os.path.join(out_dir, "report.md")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(contract, fp, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write(markdown)
    return {"findings.json": json_path, "report.md": md_path}
