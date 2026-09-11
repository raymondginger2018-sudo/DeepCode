# -*- coding: utf-8 -*-
"""临时验证: HooksEngine.run_pre_compact 链路 → cerebellum-pre-compact 落库"""
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, r"F:/DEEPCODE")
sys.path.insert(0, r"F:/DEEPCODE/core")

from core.harness.hooks.discovery import discover_hooks
from core.harness.hooks.engine import HooksEngine

DB = r"F:/DEEPCODE/.dsh/skills/deepcode-cerebellum/data/cerebellum.db"


def db_max_id():
    try:
        conn = sqlite3.connect(DB)
        row = conn.execute(
            "SELECT MAX(id), COUNT(*) FROM session_summaries"
        ).fetchone()
        conn.close()
        return row
    except Exception as e:  # noqa: BLE001
        return (str(e),)


async def main():
    home = os.path.expanduser("~")
    result = discover_hooks(r"F:/DEEPCODE", home=home)
    print(f"handlers: {len(result.handlers)}, warnings: {len(result.warnings)}")
    for w in result.warnings[:5]:
        print("WARN:", w)

    pre = [h for h in result.handlers if h.event_name == "PreCompact"]
    print("PreCompact handlers:")
    for h in pre:
        print(f"  - order={h.display_order}: {h.command[:90]}")

    engine = HooksEngine(
        result.handlers, cwd=r"F:/DEEPCODE", session_id="e2e-precompact-verify"
    )
    print("has_event(PreCompact):", engine.has_event("PreCompact"))
    print("has_event(SessionEnd):", engine.has_event("SessionEnd"))

    print("DB 基线 max(id):", db_max_id())
    print("=== 触发 run_pre_compact(trigger='manual') ===")
    try:
        out = await asyncio.wait_for(
            engine.run_pre_compact(trigger="manual"), timeout=240
        )
        print("block:", out.block)
        print("block_reason:", out.block_reason)
        ctx = list(getattr(out, "additional_contexts", None) or [])
        print(f"additional_contexts: {len(ctx)} 条")
        for c in ctx[:2]:
            print("  ctx 前 200 字:", c[:200].replace("\n", " | "))
    except asyncio.TimeoutError:
        print("!! run_pre_compact 超时 (240s)")
    except Exception as e:  # noqa: BLE001
        print("!! 异常:", type(e).__name__, e)

    print("DB 落库后 max(id):", db_max_id())


asyncio.run(main())
