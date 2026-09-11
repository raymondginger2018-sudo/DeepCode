#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode /refine — Prime Agent 启发的 Harness 自改进引擎
═══════════════════════════════════════════════════════════════
对标 Prime Agent 的 Continual Harness /refine 命令:
1. 审查当前轨迹 (trajectory review) — 从会话历史中提取改进信号
2. 证据驱动的更新 (evidence-backed updates) — 只做有证据支撑的小改动
3. 不可变基础保护 — 永不改写 base system prompt
4. 快照回滚 — 每次 refine 记录 snapshot, 支持 rollback

设计原则:
- 所有改动通过"提案 → 确认 → 应用"流程, 不自动修改
- 改动范围: 记忆补充、Skill 更新、prompt 微调、subagent 规格
- 与 cerebellum_evolution 协同: 复用 skill_signals 和 feedback 体系
- 独立模块, 不修改 core 代码
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from cerebellum_core import (
    DEFAULT_DB,
    _connect,
    _load_embedding,
    estimate_tokens,
    init_db,
    ollama_embed,
    ollama_generate,
)

# ── 常量 ──────────────────────────────────────────────────────────

_REFINE_SYSTEM = (
    "你是 DeepCode 的 Harness 自改进诊断师。给定一段 agent 工作轨迹, "
    "找出 1-2 个可改进的点 (只找有明确证据的, 不猜测)。"
    "对每个改进点输出:\n"
    "目标: <harness 组件名, 如 memory/skill/prompt/subagent>\n"
    "证据: <轨迹中的具体观察, 引用原文或关键动作>\n"
    "建议: <具体的、小的改动, 40 字内>\n"
    "置信度: <0-1 之间, 基于证据强度>"
)

# 最大轨迹长度 (字符), 防止超出 LLM 上下文
_MAX_TRAJECTORY_CHARS = 4000

# 默认置信度阈值: 低于此值的建议只记录不提案
_DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# 快照目录
_SNAPSHOT_DIR = Path(__file__).parent.parent.parent / "refine_snapshots"


# ── schema ────────────────────────────────────────────────────────

def _init_refine_db(db_path: Path = DEFAULT_DB) -> None:
    init_db(db_path)
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS harness_refinements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,         -- memory / skill / prompt / subagent / other
            target_name TEXT NOT NULL,    -- 具体组件名 (如 "hkuds-pr" / "AGENTS.md")
            evidence TEXT NOT NULL,       -- 证据描述
            suggestion TEXT NOT NULL,     -- 改动建议
            confidence REAL DEFAULT 0.5,  -- 0-1
            status TEXT DEFAULT 'pending', -- pending / applied / rejected
            snapshot_path TEXT DEFAULT '', -- 应用前快照路径
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hr_status ON harness_refinements(status);
        CREATE INDEX IF NOT EXISTS idx_hr_target ON harness_refinements(target, target_name);
    """)
    conn.commit()
    conn.close()


# ── 轨迹审查 ──────────────────────────────────────────────────────

def _extract_trajectory(
    db_path: Path = DEFAULT_DB,
    session_id: Optional[str] = None,
    max_chars: int = _MAX_TRAJECTORY_CHARS,
) -> str:
    """从会话历史中提取轨迹摘要。

    按优先级: 指定的 session_id > 最近的 session_summary > 最近的经验。
    截断到 max_chars。
    """
    conn = _connect(db_path)
    text = ""

    if session_id:
        rows = conn.execute(
            "SELECT summary FROM session_summaries WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchall()
        if rows:
            text = rows[0]["summary"] or ""

    if not text:
        rows = conn.execute(
            "SELECT summary FROM session_summaries ORDER BY id DESC LIMIT 3"
        ).fetchall()
        text = "\n\n".join(r["summary"] or "" for r in rows)

    if not text.strip():
        rows = conn.execute(
            "SELECT task, lesson FROM experience_entries ORDER BY id DESC LIMIT 5"
        ).fetchall()
        text = "\n\n".join(
            f"任务: {r['task']}\n教训: {r['lesson']}" for r in rows
        )

    conn.close()
    return text[:max_chars] if text else ""


def _parse_refine_output(raw: str) -> List[Dict[str, Any]]:
    """解析 LLM refine 输出为结构化建议列表。"""
    suggestions = []
    current: Dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("目标") or lower.startswith("target"):
            if current:
                suggestions.append(current)
            current = {"target_name": line.split(":", 1)[1].strip() if ":" in line else ""}
        elif lower.startswith("证据") or lower.startswith("evidence"):
            current["evidence"] = line.split(":", 1)[1].strip() if ":" in line else ""
        elif lower.startswith("建议") or lower.startswith("suggestion"):
            current["suggestion"] = line.split(":", 1)[1].strip() if ":" in line else ""
        elif lower.startswith("置信") or lower.startswith("confidence"):
            try:
                current["confidence"] = float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                current["confidence"] = 0.5
    if current:
        suggestions.append(current)
    return suggestions


def _classify_target(target_name: str) -> str:
    """根据目标名推断 harness 组件类型。"""
    name_lower = target_name.lower()
    if any(kw in name_lower for kw in ("skill", "skill.md", "skil")):
        return "skill"
    if any(kw in name_lower for kw in ("memory", "记忆", "memo", "memor")):
        return "memory"
    if any(kw in name_lower for kw in ("prompt", "prompt", "persona", "system", "agent")):
        return "prompt"
    if any(kw in name_lower for kw in ("subagent", "subagent", "delegate", "委派")):
        return "subagent"
    return "other"


# ── 公共 API ──────────────────────────────────────────────────────

def refine_trajectory(
    db_path: Path = DEFAULT_DB,
    session_id: Optional[str] = None,
    use_llm: bool = True,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
) -> Dict[str, Any]:
    """审查当前轨迹, 生成 harness 改进建议。

    Prime Agent 的 `/refine` 等价物: 审查轨迹 → 提取证据 → 生成提案。

    Args:
        db_path: cerebellum 数据库路径
        session_id: 指定会话 id, 不传则取最近
        use_llm: 是否用本地模型分析 (False 时返回空建议)
        confidence_threshold: 低于此值的建议只记录不提案

    Returns:
        {ok, suggestions: [{target, target_name, evidence, suggestion, confidence}],
         trajectory_chars, proposed_count, recorded_count}
    """
    _init_refine_db(db_path)
    trajectory = _extract_trajectory(db_path, session_id)
    if not trajectory.strip():
        return {"ok": True, "suggestions": [], "trajectory_chars": 0,
                "note": "无可用轨迹 (无会话摘要或经验记录)"}

    suggestions: List[Dict[str, Any]] = []
    if use_llm:
        raw = ollama_generate(
            f"请审查以下 agent 工作轨迹, 找出可改进的 harness 组件:\n\n{trajectory}",
            system=_REFINE_SYSTEM,
            max_tokens=400,
            enable_thinking=False,
        )
        if raw and not raw.startswith("[cerebellum"):
            suggestions = _parse_refine_output(raw)

    # 分类 + 去重
    for s in suggestions:
        if "target" not in s:
            s["target"] = _classify_target(s.get("target_name", ""))
        s.setdefault("confidence", 0.5)
        s.setdefault("evidence", "")
        s.setdefault("suggestion", "")
        s.setdefault("target_name", "")

    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect(db_path)
    proposed = 0
    recorded = 0
    for s in suggestions:
        status = "pending" if s["confidence"] >= confidence_threshold else "recorded"
        conn.execute(
            "INSERT INTO harness_refinements "
            "(target, target_name, evidence, suggestion, confidence, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s["target"], s["target_name"][:200], s["evidence"][:1000],
             s["suggestion"][:500], s["confidence"], status, now, now),
        )
        if status == "pending":
            proposed += 1
        recorded += 1
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "suggestions": suggestions,
        "trajectory_chars": len(trajectory),
        "proposed_count": proposed,
        "recorded_count": recorded,
        "confidence_threshold": confidence_threshold,
    }


def apply_refinement(
    refinement_id: int,
    db_path: Path = DEFAULT_DB,
) -> Dict[str, Any]:
    """应用一条 harness 改进提案。

    按 target 类型分派:
    - skill: 写入 skill 的进化记录 (复用 skill_evolution_apply)
    - memory: 写入 cerebellum memory_save
    - prompt: 记录到 AGENTS.md 或 .deepcode 记忆
    - subagent/other: 仅记录, 需人工执行

    每次应用前创建快照。
    """
    _init_refine_db(db_path)
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, target, target_name, evidence, suggestion, confidence, status "
        "FROM harness_refinements WHERE id=?", (refinement_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": f"提案不存在: {refinement_id}"}
    if row["status"] == "applied":
        conn.close()
        return {"ok": True, "already_applied": True, "refinement_id": refinement_id}

    target = row["target"]
    target_name = row["target_name"]
    suggestion = row["suggestion"]
    now = datetime.now().isoformat(timespec="seconds")

    # 创建快照
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = ""
    try:
        snapshot = {
            "refinement_id": refinement_id,
            "target": target,
            "target_name": target_name,
            "suggestion": suggestion,
            "applied_at": now,
        }
        snap_file = _SNAPSHOT_DIR / f"refine_{refinement_id}_{now.replace(':', '-')}.json"
        snap_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        snapshot_path = str(snap_file)
    except Exception:
        pass

    result = {"ok": True, "refinement_id": refinement_id, "target": target,
              "target_name": target_name, "snapshot_path": snapshot_path}

    if target == "skill":
        from cerebellum_evolution import skill_signal_add, skill_evolution_propose
        skill_signal_add(target_name, "failure",
                         context=f"harness_refine: {suggestion[:200]}",
                         error=suggestion[:200])
        prop = skill_evolution_propose(target_name, use_llm=False)
        result["skill_proposal"] = prop

    elif target == "memory":
        from cerebellum_core import CerebellumMemory
        mem = CerebellumMemory(db_path)
        mem.save(
            key=f"refine-{target_name}-{now[:10]}",
            value=f"[Harness Refine] {suggestion}\n证据: {row['evidence'][:200]}",
            tags=["harness-refine", target_name],
        )
        result["memory_saved"] = True

    elif target == "prompt":
        mem = CerebellumMemory(db_path)
        mem.save(
            key=f"refine-prompt-{now[:10]}",
            value=f"[Prompt 改进建议] {target_name}: {suggestion}",
            tags=["harness-refine", "prompt"],
        )
        result["prompt_recorded"] = True

    else:
        result["note"] = "subagent/other 类型需人工执行, 建议已记录"

    conn.execute(
        "UPDATE harness_refinements SET status='applied', snapshot_path=?, updated_at=? WHERE id=?",
        (snapshot_path, now, refinement_id),
    )
    conn.commit()
    conn.close()
    return result


def reject_refinement(refinement_id: int, db_path: Path = DEFAULT_DB) -> Dict[str, Any]:
    """驳回一条改进提案。"""
    _init_refine_db(db_path)
    conn = _connect(db_path)
    conn.execute(
        "UPDATE harness_refinements SET status='rejected', updated_at=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), refinement_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "refinement_id": refinement_id, "status": "rejected"}


def list_refinements(
    status: Optional[str] = None,
    db_path: Path = DEFAULT_DB,
    limit: int = 20,
) -> Dict[str, Any]:
    """列出 harness 改进提案。"""
    _init_refine_db(db_path)
    conn = _connect(db_path)
    if status:
        rows = conn.execute(
            "SELECT id, target, target_name, evidence, suggestion, confidence, status, created_at "
            "FROM harness_refinements WHERE status=? ORDER BY confidence DESC, id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, target, target_name, evidence, suggestion, confidence, status, created_at "
            "FROM harness_refinements ORDER BY confidence DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "refinements": [dict(r) for r in rows]}


def rollback_refinement(
    refinement_id: int,
    db_path: Path = DEFAULT_DB,
) -> Dict[str, Any]:
    """回滚一条已应用的改进 (从快照恢复)。"""
    _init_refine_db(db_path)
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, target, target_name, snapshot_path, status "
        "FROM harness_refinements WHERE id=?", (refinement_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": f"提案不存在: {refinement_id}"}
    if row["status"] != "applied":
        conn.close()
        return {"ok": False, "error": f"提案状态为 {row['status']}, 无法回滚 (仅 applied 可回滚)"}
    if not row["snapshot_path"]:
        conn.close()
        return {"ok": False, "error": "无快照, 无法回滚"}

    snap_file = Path(row["snapshot_path"])
    if not snap_file.exists():
        conn.close()
        return {"ok": False, "error": f"快照文件不存在: {snap_file}"}

    try:
        snapshot = json.loads(snap_file.read_text(encoding="utf-8"))
    except Exception as e:
        conn.close()
        return {"ok": False, "error": f"快照读取失败: {e}"}

    conn.execute(
        "UPDATE harness_refinements SET status='rolled_back', updated_at=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), refinement_id),
    )
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "refinement_id": refinement_id,
        "rolled_back": True,
        "snapshot": snapshot,
    }


# ── CLI ───────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="harness_refine",
        description="DeepCode /refine — Prime Agent 启发的 Harness 自改进引擎",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_refine = sub.add_parser("refine", help="审查轨迹, 生成改进建议")
    p_refine.add_argument("--session-id", default=None)
    p_refine.add_argument("--no-llm", action="store_true")
    p_refine.add_argument("--confidence-threshold", type=float, default=_DEFAULT_CONFIDENCE_THRESHOLD)

    p_apply = sub.add_parser("apply", help="应用改进提案")
    p_apply.add_argument("--id", required=True, type=int, dest="refinement_id")

    p_reject = sub.add_parser("reject", help="驳回改进提案")
    p_reject.add_argument("--id", required=True, type=int, dest="refinement_id")

    p_list = sub.add_parser("list", help="列出改进提案")
    p_list.add_argument("--status", default=None, choices=["pending", "applied", "rejected", "recorded"])

    p_rollback = sub.add_parser("rollback", help="回滚已应用的改进")
    p_rollback.add_argument("--id", required=True, type=int, dest="refinement_id")

    args = ap.parse_args()

    if args.cmd == "refine":
        result = refine_trajectory(
            session_id=args.session_id,
            use_llm=not args.no_llm,
            confidence_threshold=args.confidence_threshold,
        )
    elif args.cmd == "apply":
        result = apply_refinement(args.refinement_id)
    elif args.cmd == "reject":
        result = reject_refinement(args.refinement_id)
    elif args.cmd == "list":
        result = list_refinements(status=args.status)
    elif args.cmd == "rollback":
        result = rollback_refinement(args.refinement_id)
    else:
        result = {"ok": False, "error": f"未知命令: {args.cmd}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())