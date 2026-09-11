"""模式 3 落地：子线程结果摘要预算 —— 按父上下文 headroom 动态分配 + 超限 spill 落盘。

对标 Hermes:
  - tools/delegate_tool_results.py
      `_parent_summary_char_budget()`  → 用父 context_length - prompt_tokens - compressor.max_tokens
                                         得到剩余 headroom, 取其 50% 作为全部子摘要的总额度
      `_apply_summary_budget()`        → per-summary cap = MIN(动态额度 / N, 静态上限)
      `_trim_summary_with_footer()`    → 超限摘要裁成 ~75% 头 + 25% 尾, 全文 spill 到文件,
                                         footer 给出 `read_file offset=` 续读指引
      `_rollup_children_cost()`        → 子线程花费并入父会话, 保证账单不丢

设计取舍 (DEEPCODE 场景):
  - DEEPCODE 无 in-process 父 agent 对象, 父上下文用量从持久化的线程元数据读取
    (metadata_json.headroom / context_length / prompt_tokens)。
  - 缺失元数据时退化为静态上限, 保证永不因预算计算失败而丢结果。
  - spill 目录默认 `WORKERS_ROOT/summaries`, 可注入覆盖便于测试。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ── 常量（对标 Hermes delegate_tool_results.py）─────────────────────────

DEFAULT_MAX_SUMMARY_CHARS = 24_000
"""单条摘要静态上限（无论父 headroom 多大都不超过）。"""

SUMMARY_HEADROOM_FRACTION = 0.5
"""父剩余 headroom 中可用于子摘要的比例。"""

MIN_SUMMARY_CHARS = 2_000
"""单条摘要下限 —— 预算再紧也至少给这么多，避免摘要被压成空。"""

_HEAD_SHARE = 0.75
"""截断时头部占比，其余给尾部（尾部常含结论/下一步）。"""

DEFAULT_SPILL_DIR = Path(__file__).resolve().parent / "cache" / "delegation" / "summaries"


# ── 预算计算 ────────────────────────────────────────────────────────────


def parent_summary_char_budget(
    *,
    context_length: int = 0,
    prompt_tokens: int = 0,
    compressor_max_tokens: int = 0,
    n_summaries: int = 1,
    chars_per_token: float = 4.0,
) -> int:
    """按父上下文剩余 headroom 计算**单条**摘要的字符预算。

    对标 Hermes `_parent_summary_char_budget()`:
        剩余 token = context_length - prompt_tokens - compressor_max_tokens
        可用额度   = 剩余 token * HEADROOM_FRACTION
        单条预算   = 可用额度 / n_summaries, 并按 chars_per_token 折算为字符

    元数据缺失（context_length<=0）时返回静态上限，保证永不因预算失败丢结果。
    """
    if context_length <= 0:
        return DEFAULT_MAX_SUMMARY_CHARS
    remaining_tokens = context_length - max(0, prompt_tokens) - max(0, compressor_max_tokens)
    if remaining_tokens <= 0:
        return MIN_SUMMARY_CHARS
    usable_tokens = remaining_tokens * SUMMARY_HEADROOM_FRACTION
    per_summary_tokens = usable_tokens / max(1, n_summaries)
    per_summary_chars = int(per_summary_tokens * chars_per_token)
    return max(MIN_SUMMARY_CHARS, min(DEFAULT_MAX_SUMMARY_CHARS, per_summary_chars))


# ── 截断 + spill ────────────────────────────────────────────────────────


@dataclass
class TrimmedSummary:
    """一次摘要裁剪的结果。"""

    text: str
    spilled: bool = False
    spill_path: str = ""
    original_chars: int = 0
    kept_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "spilled": self.spilled,
            "spill_path": self.spill_path,
            "original_chars": self.original_chars,
            "kept_chars": self.kept_chars,
        }


def _safe_slug(value: str, fallback: str = "x") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip())
    cleaned = cleaned.strip("-.")
    return cleaned[:64] or fallback


def trim_summary_with_footer(
    summary: str,
    *,
    cap: int,
    label: str = "",
    spill_dir: Optional[Path] = None,
    spill: bool = True,
) -> TrimmedSummary:
    """把 summary 裁到 cap 字符以内；超限时头部+尾部保留，全文 spill 到文件。

    对标 Hermes `_trim_summary_with_footer()`:
      - 未超限 → 原样返回
      - 超限   → 取 ~75% 头 + ~25% 尾，中间插入省略标记
      - spill  → 全文写盘，footer 附文件路径与 read_file 续读指引
    """
    text = summary or ""
    original_chars = len(text)
    if cap <= 0:
        cap = MIN_SUMMARY_CHARS
    if original_chars <= cap:
        return TrimmedSummary(text=text, original_chars=original_chars,
                              kept_chars=original_chars)

    head_chars = int(cap * _HEAD_SHARE)
    tail_chars = cap - head_chars
    head = text[:head_chars]
    tail = text[-tail_chars:] if tail_chars > 0 else ""

    spill_path = ""
    if spill:
        try:
            directory = Path(spill_dir) if spill_dir else DEFAULT_SPILL_DIR
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            fname = f"subagent-summary-{_safe_slug(label)}-{stamp}.txt"
            full = directory / fname
            full.write_text(text, encoding="utf-8")
            spill_path = str(full)
        except Exception:
            spill_path = ""

    omitted = original_chars - head_chars - tail_chars
    if spill_path:
        footer = (
            f"\n... [{omitted} chars omitted] ...\n"
            f"[full summary: {spill_path} — "
            f"read_file(path=\"{spill_path}\", offset={head_chars}) to continue]\n"
        )
    else:
        footer = f"\n... [{omitted} chars omitted] ...\n"

    trimmed = head + footer + tail
    return TrimmedSummary(text=trimmed, spilled=bool(spill_path), spill_path=spill_path,
                          original_chars=original_chars, kept_chars=len(trimmed))


@dataclass
class BudgetedResult:
    """一条子线程结果的预算化产物。"""

    thread_id: str = ""
    summary: TrimmedSummary = field(default_factory=lambda: TrimmedSummary(text=""))
    cost_usd: float = 0.0
    status: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = self.summary.to_dict()
        d.update({"thread_id": self.thread_id, "cost_usd": self.cost_usd, "status": self.status})
        return d


def apply_summary_budget(
    results: Iterable[Dict[str, Any]],
    *,
    parent_meta: Optional[Dict[str, Any]] = None,
    spill_dir: Optional[Path] = None,
    spill: bool = True,
) -> List[BudgetedResult]:
    """对一批子线程结果统一施加摘要预算。

    results 每项形如::

        {"thread_id": "at_xxx", "summary": "……", "cost_usd": 0.012, "status": "completed"}

    parent_meta 可含 ``context_length`` / ``prompt_tokens`` / ``compressor_max_tokens``
    （缺失即退化为静态上限）。
    """
    items = list(results)
    n = max(1, len(items))
    meta = parent_meta or {}
    cap = parent_summary_char_budget(
        context_length=int(meta.get("context_length", 0) or 0),
        prompt_tokens=int(meta.get("prompt_tokens", 0) or 0),
        compressor_max_tokens=int(meta.get("compressor_max_tokens", 0) or 0),
        n_summaries=n,
    )
    out: List[BudgetedResult] = []
    for item in items:
        label = str(item.get("thread_id", "") or "")
        trimmed = trim_summary_with_footer(
            str(item.get("summary", "") or ""),
            cap=cap,
            label=label,
            spill_dir=spill_dir,
            spill=spill,
        )
        out.append(BudgetedResult(
            thread_id=label,
            summary=trimmed,
            cost_usd=float(item.get("cost_usd", 0.0) or 0.0),
            status=str(item.get("status", "") or ""),
        ))
    return out


# ── 成本汇总 ────────────────────────────────────────────────────────────


def rollup_children_cost(
    children: Iterable[Dict[str, Any]],
    *,
    parent_cost_usd: float = 0.0,
) -> Dict[str, Any]:
    """把子线程花费汇总进父会话，保证账单不丢（对标 Hermes `_rollup_children_cost`）。

    返回::

        {"parent_cost_usd": 总花费, "children_cost_usd": 子合计, "n_children": n}
    """
    items = list(children)
    children_total = 0.0
    for item in items:
        try:
            children_total += float(item.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return {
        "parent_cost_usd": round(float(parent_cost_usd) + children_total, 6),
        "children_cost_usd": round(children_total, 6),
        "n_children": len(items),
    }


def load_parent_meta(metadata_json: str) -> Dict[str, Any]:
    """从线程 metadata_json 解析预算相关元数据（容错：坏 JSON 返回空 dict）。"""
    if not metadata_json:
        return {}
    try:
        data = json.loads(metadata_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    keys = ("context_length", "prompt_tokens", "compressor_max_tokens",
            "session_estimated_cost_usd")
    return {k: data.get(k) for k in keys if k in data}
