"""评分模块：五维度 × 15 检查项评分（对齐 Agent Work Loop 模型）。

评分规则（QoderAI/better-harness 约束）：
- 分数是**上限约束**，不是公式：维度分数 ≤ 该维度最高证据状态的绝对上限
- 维度分数 = 基于检查项证据状态均值映射到上限区间内的整数
- Learning Capture 特殊规则：单一 Agent 整数 35–100；null 属未解决槽位
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import DIMENSION_LABELS, ItemEvidence, SCORE_CEILING

# 状态排序值（供插值）
_RANK_ORDER = ("Missing", "Present", "Wired", "Exercised", "Outcome-supported")
_RANK_VAL = {s: i for i, s in enumerate(_RANK_ORDER)}
_CEIL_BY_RANK = [SCORE_CEILING[s] for s in _RANK_ORDER]  # [59, 74, 84, 94, 100]


@dataclass
class DimensionScore:
    """单个维度的评分结果。"""
    dimension: str
    label: str
    score: int
    max_state: str          # 最高证据状态
    ceiling: int            # 绝对上限
    items: list = field(default_factory=list)   # 该维度检查项证据


def _dimension_score(items: list[ItemEvidence]) -> DimensionScore:
    """对某维度的 3 个检查项评分（上限约束 + 线性插值）。"""
    if not items:
        return DimensionScore(dimension="?", label="?", score=59,
                              max_state="Missing", ceiling=59, items=[])

    dim = items[0].dimension
    label = DIMENSION_LABELS.get(dim, dim)
    ranks = [_RANK_VAL.get(it.state, 0) for it in items]
    avg = sum(ranks) / len(ranks)

    # 上限约束：最高证据状态对应的绝对上限
    max_rank = max(ranks)
    ceiling = _CEIL_BY_RANK[max_rank]

    # 线性插值：floor(avg) 与 ceil(avg) 之间的区间内插值
    lo_rank = int(avg)
    hi_rank = min(lo_rank + 1, 4)
    lo_ceil = _CEIL_BY_RANK[lo_rank]
    hi_ceil = _CEIL_BY_RANK[hi_rank]
    score = lo_ceil + int(round((avg - lo_rank) * (hi_ceil - lo_ceil)))

    # 上限硬约束（防插值越界）
    score = min(score, ceiling)
    score = max(score, 35)   # 35 地板：完成有界审查

    return DimensionScore(dimension=dim, label=label, score=score,
                          max_state=_RANK_ORDER[max_rank], ceiling=ceiling,
                          items=list(items))


def score_all(items: list[ItemEvidence]) -> list[DimensionScore]:
    """对全部检查项按维度分组评分，返回 5 个维度分数。"""
    by_dim: dict[str, list[ItemEvidence]] = {}
    for it in items:
        by_dim.setdefault(it.dimension, []).append(it)

    results = []
    for dim in ("task-understanding", "controlled-execution",
                "change-validation", "reliable-delivery", "learning-capture"):
        results.append(_dimension_score(by_dim.get(dim, [])))
    return results


def overall_score(dims: list[DimensionScore]) -> int:
    """总分：五维度均值（保留与子分数一致的上限约束语义）。"""
    if not dims:
        return 0
    return int(round(sum(d.score for d in dims) / len(dims)))
