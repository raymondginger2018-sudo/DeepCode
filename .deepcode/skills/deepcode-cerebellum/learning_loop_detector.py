"""learning_loop_detector — 学习循环检测器（10 模式，纯规则，只读）。

对齐 QoderAI/better-harness `references/loop-engineering/learning-loop-patterns.md`：
检测"学习循环"断裂点（brokenStage 七阶段）并输出候选干预（candidate）。

设计约束（503 降级兼容）：
- 纯规则实现，不调用 LLM / 外部 API（provenance = deterministic-derived）
- 只读 cerebellum.db + 文件系统 mtime，不做任何写操作
- 数据源映射：
    repeated-rediscovery   → session_summaries embedding 两两相似
    recurring-correction   → skill_signals 同 skill 重复失败
    present-but-not-routed → skills 目录 vs INDEX.md 路由
    routed-but-not-applied → INDEX.md 条目 vs 会话/语义条目引用
    stale-or-conflicting-asset → 文件 mtime 过期仍被引用
    cross-asset-duplication → experience_entries / consolidated_memories 两两相似
    correction-not-promoted → skill_signals 失败未沉淀为经验
    asset-updated-not-reexercised → 资产 mtime 新但无重新演练证据
    wrong-durable-owner    → 资产放置位置不合理
    unvalidated-intervention → 提案已应用但无验证记录

用法（独立运行）：
    python learning_loop_detector.py [--db path] [--min-priority 30]
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "cerebellum.db"
SKILLS_DIR = Path(__file__).resolve().parent.parent  # .deepcode/skills/
INDEX_MD = SKILLS_DIR / "INDEX.md"

# 与 better-harness learning-loop-patterns 一致的 10 个模式定义
PATTERNS: dict[str, dict] = {
    "repeated-rediscovery": {
        "claimType": "opportunity", "brokenStage": "generalize",
        "severity": "Medium", "title": "同一知识被反复重新发现",
        "owner": "deepcode-cerebellum",
        "intervention": "将重复会话摘要合并为 consolidated_memories，避免每次重新发现",
    },
    "recurring-correction": {
        "claimType": "opportunity", "brokenStage": "generalize",
        "severity": "High", "title": "同一 skill 反复失败被纠正",
        "owner": "deepcode-cerebellum",
        "intervention": "失败信号累积触发 skill_evolution_propose，将纠正固化为资产",
    },
    "present-but-not-routed": {
        "claimType": "opportunity", "brokenStage": "route",
        "severity": "Medium", "title": "SKILL.md 存在但未注册到 INDEX.md",
        "owner": "INDEX.md 维护者",
        "intervention": "将 skill 条目加入 INDEX.md 快速路由表",
    },
    "routed-but-not-applied": {
        "claimType": "opportunity", "brokenStage": "exercise",
        "severity": "Low", "title": "INDEX.md 已注册但从未被引用",
        "owner": "router-mcp",
        "intervention": "检查 skill 描述与触发词是否匹配实际任务",
    },
    "stale-or-conflicting-asset": {
        "claimType": "readiness", "brokenStage": "maintain",
        "severity": "Medium", "title": "资产过期但仍被引用",
        "owner": "deepcode-cerebellum",
        "intervention": "刷新资产或更新引用，避免陈旧知识误导",
    },
    "cross-asset-duplication": {
        "claimType": "readiness", "brokenStage": "maintain",
        "severity": "Low", "title": "多条经验/记忆高度重复",
        "owner": "deepcode-cerebellum",
        "intervention": "Dreaming 合并重复条目，保留一条权威版本",
    },
    "correction-not-promoted": {
        "claimType": "opportunity", "brokenStage": "codify",
        "severity": "High", "title": "失败信号未沉淀为经验",
        "owner": "deepcode-cerebellum",
        "intervention": "将 failure 信号自动转写为 experience_entries 教训",
    },
    "asset-updated-not-reexercised": {
        "claimType": "readiness", "brokenStage": "exercise",
        "severity": "Medium", "title": "资产已更新但未重新演练",
        "owner": "router-mcp",
        "intervention": "资产变更后触发一次验证会话（benchmark / smoke test）",
    },
    "wrong-durable-owner": {
        "claimType": "readiness", "brokenStage": "maintain",
        "severity": "Low", "title": "资产放置位置不合理",
        "owner": "项目维护者",
        "intervention": "将资产迁移到正确目录并更新引用",
    },
    "unvalidated-intervention": {
        "claimType": "readiness", "brokenStage": "evaluate",
        "severity": "Medium", "title": "进化提案已应用但无验证记录",
        "owner": "deepcode-cerebellum",
        "intervention": "对 applied 提案补充 benchmark 或反馈验证",
    },
}

# 阈值（规则可调，不依赖 LLM）
SIM_REDISCOVERY = 0.82   # 会话摘要两两相似 → 重复发现
SIM_DUPLICATION = 0.85   # 经验/记忆两两相似 → 重复沉淀
STALE_DAYS = 45          # 资产超过 N 天未更新 → 过期
RECENT_UPDATE_DAYS = 7   # 资产最近 N 天内更新过
FAILURE_TRIGGER = 2      # 同 skill 失败信号 >= N 触发


@dataclass
class LearningLoopCandidate:
    pattern_id: str
    claim_type: str
    broken_stage: str
    severity: str
    title: str
    observed_behavior: str
    source_episodes: list = field(default_factory=list)
    current_cost: str = ""
    recommended_owner: str = ""
    intervention: str = ""
    confidence: float = 0.5
    priority_score: int = 0

    def to_dict(self) -> dict:
        return {
            "patternId": self.pattern_id,
            "claimType": self.claim_type,
            "brokenStage": self.broken_stage,
            "severity": self.severity,
            "title": self.title,
            "observedBehavior": self.observed_behavior,
            "sourceEpisodes": self.source_episodes,
            "currentCost": self.current_cost,
            "recommendedOwner": self.recommended_owner,
            "intervention": self.intervention,
            "provenance": "deterministic-derived",
            "confidence": round(self.confidence, 2),
            "priorityScore": self.priority_score,
        }


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_embedding(row, col: str = "embedding") -> list[float] | None:
    raw = row[col] if col in row.keys() else None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return [float(x) for x in data]
    except (ValueError, TypeError):
        pass
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（本地实现，零依赖）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _priority(severity: str, confidence: float) -> int:
    base = {"High": 60, "Medium": 40, "Low": 25}.get(severity, 25)
    return min(100, base + int(confidence * 30))


def _make(pattern_id: str, observed: str, episodes: list,
          confidence: float, current_cost: str = "") -> LearningLoopCandidate:
    p = PATTERNS[pattern_id]
    c = LearningLoopCandidate(
        pattern_id=pattern_id,
        claim_type=p["claimType"],
        broken_stage=p["brokenStage"],
        severity=p["severity"],
        title=p["title"],
        observed_behavior=observed,
        source_episodes=episodes,
        current_cost=current_cost,
        recommended_owner=p["owner"],
        intervention=p["intervention"],
        confidence=confidence,
    )
    c.priority_score = _priority(c.severity, confidence)
    return c


# ─────────────────────────────────────────────
# 10 个检测器
# ─────────────────────────────────────────────
def _detect_repeated_rediscovery(conn) -> list[LearningLoopCandidate]:
    """session_summaries 两两 embedding 相似 → 同一知识反复重新发现。"""
    rows = conn.execute(
        "SELECT id, session_id, summary, embedding FROM session_summaries "
        "ORDER BY id DESC LIMIT 50").fetchall()
    vecs = [(r["id"], r["session_id"], _load_embedding(r)) for r in rows]
    out: list[LearningLoopCandidate] = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            a_id, a_sid, a_vec = vecs[i]
            b_id, b_sid, b_vec = vecs[j]
            if a_vec is None or b_vec is None or a_sid == b_sid:
                continue
            sim = _cosine(a_vec, b_vec)
            if sim >= SIM_REDISCOVERY:
                out.append(_make(
                    "repeated-rediscovery",
                    f"会话 {a_id} 与 {b_id} 摘要语义相似度 {sim:.2f}，"
                    f"疑似同一主题被反复重新发现",
                    [f"session:{a_id}", f"session:{b_id}"],
                    confidence=sim,
                    current_cost="每次重新发现重复消耗 LLM token",
                ))
                break  # 每个会话只报一条，避免爆炸
    return out


def _detect_recurring_correction(conn) -> list[LearningLoopCandidate]:
    """skill_signals 同 skill 失败 >= FAILURE_TRIGGER → 反复纠正。"""
    rows = conn.execute(
        "SELECT skill_name, signal_type, COUNT(*) AS n FROM skill_signals "
        "WHERE signal_type='failure' GROUP BY skill_name HAVING n >= ?",
        (FAILURE_TRIGGER,)).fetchall()
    out = []
    for r in rows:
        out.append(_make(
            "recurring-correction",
            f"skill [{r['skill_name']}] 有 {r['n']} 条失败信号，"
            f">= 阈值 {FAILURE_TRIGGER}，纠正未固化为资产",
            [f"skill_signals:{r['skill_name']}×{r['n']}"],
            confidence=min(1.0, 0.4 + 0.1 * r["n"]),
        ))
    return out


def _detect_present_but_not_routed(conn=None) -> list[LearningLoopCandidate]:
    """skills/ 下有 SKILL.md 但 INDEX.md 无对应条目 → 未路由。"""
    if not SKILLS_DIR.is_dir() or not INDEX_MD.is_file():
        return []
    index_text = INDEX_MD.read_text(encoding="utf-8", errors="ignore")
    out = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").is_file():
            continue
        name = skill_dir.name
        # 条目名可能带反引号/横线，宽松匹配
        if name not in index_text and f"`{name}`" not in index_text:
            out.append(_make(
                "present-but-not-routed",
                f"skill [{name}] 存在 SKILL.md 但 INDEX.md 未注册",
                [f"skills/{name}/SKILL.md"],
                confidence=0.9,
            ))
    return out


def _detect_routed_but_not_applied(conn) -> list[LearningLoopCandidate]:
    """INDEX.md 有条目但 semantic_entries / session 从未引用 → 从未被使用。"""
    if not INDEX_MD.is_file():
        return []
    index_text = INDEX_MD.read_text(encoding="utf-8", errors="ignore")
    refs = conn.execute(
        "SELECT content, source_key FROM semantic_entries LIMIT 500").fetchall()
    ref_text = " ".join(f"{r['content']} {r['source_key']}" for r in refs)
    out = []
    # 只检查 DeepCode 引擎配套表条目（以 `xxx` 反引号包裹的 skill 名）
    for m in set(name.strip("`") for name in index_text.split("`")[1::2]):
        if not m or "/" in m or " " in m or "." in m:
            continue
        if m in ref_text or m in index_text and False:
            continue
        if m in ("SKILL", "skills", "INDEX"):
            continue
        out.append(_make(
            "routed-but-not-applied",
            f"skill [{m}] 已注册 INDEX.md 但语义条目从未引用",
            [f"INDEX.md:{m}"],
            confidence=0.6,
        ))
    return out[:8]


def _detect_stale_asset(conn=None) -> list[LearningLoopCandidate]:
    """资产（SKILL.md）mtime 超过 STALE_DAYS 未更新 → 过期资产。"""
    if not SKILLS_DIR.is_dir():
        return []
    now = datetime.now().timestamp()
    out = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue
        age_days = (now - md.stat().st_mtime) / 86400
        if age_days > STALE_DAYS:
            out.append(_make(
                "stale-or-conflicting-asset",
                f"{md.relative_to(SKILLS_DIR)} 已 {age_days:.0f} 天未更新",
                [str(md.relative_to(SKILLS_DIR))],
                confidence=0.7,
            ))
    return out


def _detect_cross_asset_duplication(conn) -> list[LearningLoopCandidate]:
    """experience_entries 两两 embedding 相似 >= SIM_DUPLICATION → 重复沉淀。"""
    rows = conn.execute(
        "SELECT id, task, lesson, embedding FROM experience_entries "
        "ORDER BY id DESC LIMIT 60").fetchall()
    vecs = [(r["id"], _load_embedding(r)) for r in rows]
    out = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            a_id, a_vec = vecs[i]
            b_id, b_vec = vecs[j]
            if a_vec is None or b_vec is None:
                continue
            sim = _cosine(a_vec, b_vec)
            if sim >= SIM_DUPLICATION:
                out.append(_make(
                    "cross-asset-duplication",
                    f"经验 {a_id} 与 {b_id} 语义相似度 {sim:.2f}，疑似重复沉淀",
                    [f"experience:{a_id}", f"experience:{b_id}"],
                    confidence=sim,
                ))
                break
    return out


def _detect_correction_not_promoted(conn) -> list[LearningLoopCandidate]:
    """skill 有失败信号但 experience_entries 无对应 lesson → 未沉淀。"""
    fails = conn.execute(
        "SELECT skill_name, COUNT(*) AS n FROM skill_signals "
        "WHERE signal_type='failure' GROUP BY skill_name").fetchall()
    if not fails:
        return []
    exp_text = " ".join(
        f"{r['task']} {r['lesson']}" for r in conn.execute(
            "SELECT task, lesson FROM experience_entries").fetchall())
    out = []
    for r in fails:
        if r["skill_name"].lower() not in exp_text.lower():
            out.append(_make(
                "correction-not-promoted",
                f"skill [{r['skill_name']}] 有 {r['n']} 条失败信号，"
                f"但经验库无对应教训沉淀",
                [f"skill_signals:{r['skill_name']}"],
                confidence=0.8,
            ))
    return out


def _detect_asset_updated_not_reexercised(conn) -> list[LearningLoopCandidate]:
    """SKILL.md 最近 RECENT_UPDATE_DAYS 内更新过但无新演练证据。"""
    if not SKILLS_DIR.is_dir():
        return []
    now = datetime.now().timestamp()
    refs = conn.execute(
        "SELECT content, source_key FROM semantic_entries LIMIT 500").fetchall()
    ref_text = " ".join(f"{r['content']} {r['source_key']}" for r in refs)
    out = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue
        age_days = (now - md.stat().st_mtime) / 86400
        if 0 <= age_days <= RECENT_UPDATE_DAYS:
            name = skill_dir.name
            if name not in ref_text and name not in (INDEX_MD.read_text(
                    encoding="utf-8", errors="ignore") if INDEX_MD.is_file() else ""):
                continue
            out.append(_make(
                "asset-updated-not-reexercised",
                f"skill [{name}] 最近 {age_days:.0f} 天内更新过，"
                f"但语义条目中无重新演练证据",
                [f"skills/{name}/SKILL.md"],
                confidence=0.55,
            ))
    return out


def _detect_wrong_durable_owner(conn=None) -> list[LearningLoopCandidate]:
    """资产放置位置不合理：scripts/ 下的文档 or skills/ 下的可执行脚本。"""
    if not SKILLS_DIR.is_dir():
        return []
    out = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        # skill 目录下出现不属于 skill 常规结构的可疑文件
        for f in skill_dir.iterdir():
            if f.is_file() and f.name not in (
                    "SKILL.md", "README.md", "AGENTS.md") and f.suffix in (
                    ".db", ".sqlite", ".log", ".tmp"):
                out.append(_make(
                    "wrong-durable-owner",
                    f"{f.relative_to(SKILLS_DIR)} 疑似数据/临时文件放错位置",
                    [str(f.relative_to(SKILLS_DIR))],
                    confidence=0.5,
                ))
    return out


def _detect_unvalidated_intervention(conn) -> list[LearningLoopCandidate]:
    """skill_evolution_proposals 已 applied 但 benchmark_runs 无后续验证。"""
    applied = conn.execute(
        "SELECT id, skill_name, suggested_change FROM skill_evolution_proposals "
        "WHERE status='applied'").fetchall()
    if not applied:
        return []
    has_benchmark = conn.execute(
        "SELECT COUNT(*) AS n FROM benchmark_runs").fetchone()["n"] > 0
    out = []
    for r in applied:
        out.append(_make(
            "unvalidated-intervention",
            f"提案 #{r['id']}（{r['skill_name']}）已应用，"
            f"但{'有' if has_benchmark else '无'} benchmark 验证记录佐证效果",
            [f"proposal:{r['id']}"],
            confidence=0.65 if not has_benchmark else 0.35,
        ))
    return out


# ─────────────────────────────────────────────
# 对外 API
# ─────────────────────────────────────────────
DETECTORS = {
    "repeated-rediscovery": _detect_repeated_rediscovery,
    "recurring-correction": _detect_recurring_correction,
    "present-but-not-routed": _detect_present_but_not_routed,
    "routed-but-not-applied": _detect_routed_but_not_applied,
    "stale-or-conflicting-asset": _detect_stale_asset,
    "cross-asset-duplication": _detect_cross_asset_duplication,
    "correction-not-promoted": _detect_correction_not_promoted,
    "asset-updated-not-reexercised": _detect_asset_updated_not_reexercised,
    "wrong-durable-owner": _detect_wrong_durable_owner,
    "unvalidated-intervention": _detect_unvalidated_intervention,
}


def detect_all(db_path: Path = DEFAULT_DB,
               min_priority: int = 0) -> list[dict]:
    """运行全部 10 个检测器，返回候选干预列表（按优先级降序）。"""
    conn = _connect(db_path)
    try:
        candidates: list[LearningLoopCandidate] = []
        for pattern_id, fn in DETECTORS.items():
            try:
                candidates.extend(fn(conn))
            except Exception as e:  # 单个检测器失败不阻断整体
                print(f"[warn] 检测器 {pattern_id} 失败: {e}")
        candidates.sort(key=lambda c: c.priority_score, reverse=True)
        if min_priority > 0:
            candidates = [c for c in candidates if c.priority_score >= min_priority]
        return [c.to_dict() for c in candidates]
    finally:
        conn.close()


def detect(pattern_id: str, db_path: Path = DEFAULT_DB) -> list[dict]:
    """运行单个检测器。"""
    fn = DETECTORS.get(pattern_id)
    if fn is None:
        raise KeyError(f"未知检测模式: {pattern_id}，可选: {', '.join(DETECTORS)}")
    conn = _connect(db_path)
    try:
        return [c.to_dict() for c in fn(conn)]
    finally:
        conn.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="学习循环检测器（10 模式）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="cerebellum.db 路径")
    parser.add_argument("--pattern", default="", help="只运行单个模式（默认全部）")
    parser.add_argument("--min-priority", type=int, default=0,
                        help="最低优先级过滤（默认 0）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    db = Path(args.db)
    if args.pattern:
        cands = detect(args.pattern, db)
    else:
        cands = detect_all(db, min_priority=args.min_priority)

    if args.json:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return
    print(f"学习循环候选干预: {len(cands)} 条")
    for c in cands:
        print(f"  [{c['severity']:<6} prio={c['priorityScore']:>3}] "
              f"{c['patternId']}: {c['title']} ({c['brokenStage']})")
        print(f"      {c['observedBehavior']}")


if __name__ == "__main__":
    main()
