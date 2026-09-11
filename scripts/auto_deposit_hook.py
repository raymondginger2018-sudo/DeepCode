#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动沉淀 Hook — 浏览器操作序列 / 逆向分析 → 小脑语义库
========================================================
设计原则 (零开销):
  - PostToolUse 模式 (默认): 只把工具调用追加到会话缓冲文件 (毫秒级, 无 LLM)
  - --flush 模式 (SessionEnd): 把缓冲汇总沉淀到小脑 (browser-save / rev 更新, 幂等)

输入 (Hook stdin JSON, Claude Code 风格):
  { "session_id": "...", "tool_name": "mcp__playwright__browser_click",
    "tool_input": {...}, "tool_response": {...} }

用法:
  echo '<json>' | python3 scripts/auto_deposit_hook.py            # PostToolUse 缓冲
  echo '<json>' | python3 scripts/auto_deposit_hook.py --flush    # SessionEnd 冲刷
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CEREBELLUM_DIR = _PROJECT_ROOT / ".deepcode" / "skills" / "deepcode-cerebellum"
_BUFFER_FILE = _PROJECT_ROOT / "data" / "auto_deposit_buffer.json"

# ── 工具分类 ──────────────────────────────────────────────────
# playwright: 触发"新站点"的操作
NAVIGATE_TOOLS = {
    "browser_navigate", "browser_tabs", "browser_go_back",
}
# playwright: 追加为操作步骤
STEP_TOOLS = {
    "browser_click", "browser_fill", "browser_type", "browser_select_option",
    "browser_press_key", "browser_hover", "browser_drag", "browser_drop",
    "browser_evaluate", "browser_fill_form", "browser_file_upload",
    "browser_navigate_back", "browser_scroll",
}
# ghidra-mcp: 携带 filepath 的分析入口工具 (开启文件级记录)
REV_FILE_TOOLS = {
    "import_file", "scan_pe_file", "angr_load_binary", "angr_symbolic_execute",
    "angr_analyze_cfg", "v8_disassemble_jsc", "v8_patch_info",
    "parse_bun_binary", "bun_scan_bytecode", "bun2v8_convert",
    "decode_v8_bytecode", "analyze_js_bundle",
}
# ghidra-mcp: 通用分析工具 (关联到最近记录的文件)
REV_ANALYZE_TOOLS = {
    "decompile_bytes", "decompile_pe", "decompile_pcode", "analyze_cfg",
    "lift_ir", "ida_save_annotation", "ida_get_annotation",
    "ida_flirt_scan", "ida_analyze_pcode", "ida_detect_chains",
    "ida_z3_optimize", "ida_z3_types", "ida_z3_path",
    "disasm_bytes", "disasm_cross_ref", "miasm_disasm",
    "triton_analyze", "triton_taint", "binary_analyze_report",
    "v8_decompile_jsc", "v8_analyze_callgraph", "v8_analyze_obfuscation",
    "v8_analyze_complexity", "v8_analyze_full", "v8_symbolic_optimize",
}


def _load_buffer() -> dict:
    if _BUFFER_FILE.exists():
        try:
            return json.loads(_BUFFER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sessions": {}}


def _save_buffer(buf: dict) -> None:
    _BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BUFFER_FILE.write_text(
        json.dumps(buf, ensure_ascii=False, indent=1), encoding="utf-8")


def _get_session(buf: dict, session_id: str) -> dict:
    sessions = buf.setdefault("sessions", {})
    sess = sessions.setdefault(session_id, {"browser": {}, "rev": {}})
    return sess


def _import_cerebellum():
    """导入小脑核心库, 失败返回 None (冲刷时优雅降级)"""
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        sys.path.insert(0, str(_CEREBELLUM_DIR))
        from cerebellum_core import CerebellumMemory
        return CerebellumMemory
    except Exception:
        return None


def _append_browser_step(sess: dict, tool_name: str, tool_input: dict) -> None:
    """把浏览器操作追加到当前站点步骤列表"""
    browsers = sess.setdefault("browser", {})
    # navigate → 开启新站点
    short = tool_name.replace("mcp__playwright__", "")
    if short in NAVIGATE_TOOLS:
        url = str(tool_input.get("url") or "")
        if short == "browser_tabs" and tool_input.get("action") == "select":
            url = str(tool_input.get("index") or "")
        site = url or "unknown"
        browsers.setdefault(site, {"steps": [], "first_seen": datetime.now().isoformat(timespec="seconds")})
        step = {"action": short, "url": url}
        browsers[site]["steps"].append(step)
        return
    # 常规步骤 → 追加到最后访问的站点
    if not browsers:
        return
    site = list(browsers.keys())[-1]
    step = {"action": short}
    for k in ("target", "text", "value", "key"):
        if tool_input.get(k) is not None:
            step[k] = tool_input.get(k)
    browsers[site]["steps"].append(step)
    browsers[site]["last_seen"] = datetime.now().isoformat(timespec="seconds")


def _append_rev_event(sess: dict, tool_name: str, tool_input: dict) -> None:
    """记录逆向分析事件: 带 filepath 的工具开启文件记录, 分析工具关联到最近文件"""
    short = tool_name.replace("mcp__ghidra-mcp__", "").replace("mcp__deepcode-decompiler__", "")
    if short in REV_FILE_TOOLS:
        fp = str(tool_input.get("filepath") or tool_input.get("file_path") or tool_input.get("pe_path") or "")
        if not fp:
            return
        revs = sess.setdefault("rev", {})
        entry = revs.setdefault(fp, {
            "file": fp, "tools": [], "first_seen": datetime.now().isoformat(timespec="seconds"),
        })
        if short not in entry["tools"]:
            entry["tools"].append(short)
        entry["last_seen"] = datetime.now().isoformat(timespec="seconds")
        return
    if short in REV_ANALYZE_TOOLS:
        revs = sess.setdefault("rev", {})
        if not revs:
            return
        # 关联最近更新的文件
        fp = max(revs, key=lambda k: revs[k].get("last_seen", ""))
        if short not in revs[fp]["tools"]:
            revs[fp]["tools"].append(short)
        revs[fp]["last_seen"] = datetime.now().isoformat(timespec="seconds")


def cmd_buffer(session_id: str, tool_name: str, tool_input: dict) -> int:
    """PostToolUse: 轻量缓冲 (不调用 LLM)"""
    if not tool_name:
        return 0
    if tool_name.startswith("mcp__playwright__"):
        buf = _load_buffer()
        sess = _get_session(buf, session_id or "adhoc")
        _append_browser_step(sess, tool_name, tool_input or {})
        _save_buffer(buf)
    elif tool_name.startswith("mcp__ghidra-mcp__") or tool_name.startswith("mcp__deepcode-decompiler__"):
        buf = _load_buffer()
        sess = _get_session(buf, session_id or "adhoc")
        _append_rev_event(sess, tool_name, tool_input or {})
        _save_buffer(buf)
    return 0


def cmd_flush(session_id: str) -> int:
    """SessionEnd: 缓冲 → 小脑 (browser-save + rev 更新, 幂等)"""
    CerebellumMemory = _import_cerebellum()
    if CerebellumMemory is None:
        print("[auto-deposit] 小脑不可用, 跳过冲刷")
        return 2
    buf = _load_buffer()
    sess = buf.get("sessions", {}).get(session_id)
    if not sess:
        print("[auto-deposit] 无缓冲数据")
        return 0
    mem = CerebellumMemory()
    n_browser = 0
    n_rev = 0

    # 1) 浏览器操作序列 → browser:{site}:{task}
    for site, info in (sess.get("browser") or {}).items():
        steps = info.get("steps") or []
        if not steps:
            continue
        host = site.replace("https://", "").replace("http://", "").split("/")[0] or site[:30]
        task = f"自动记忆 {host}"
        key = f"browser:{site}:{task}"
        payload = {
            "site": site, "task": task, "steps": steps, "auto": True,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        mem.save(key=key, value=json.dumps(payload, ensure_ascii=False),
                 tags=["浏览器", "操作序列", "auto", f"site:{site}"])
        n_browser += 1

    # 2) 逆向分析 → rev:{sha256} (幂等: 已存在则跳过, 不重复反编译)
    import hashlib
    for fp, info in (sess.get("rev") or {}).items():
        path = Path(fp)
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = f"rev:{digest}"
        existing = mem.load(key).get("value")
        if existing:
            print(f"[auto-deposit] rev 已存在: {path.name} (sha256={digest[:12]}...), 跳过")
            continue
        payload = {
            "file": str(path), "name": path.name, "size": path.stat().st_size,
            "sha256": digest, "auto": True,
            "summary": f"自动记录: 会话内对该文件执行了 {'/'.join(info.get('tools') or [])}",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        mem.save(key=key, value=json.dumps(payload, ensure_ascii=False),
                 tags=["逆向", "反编译", "auto", f"sha256:{digest[:12]}"])
        n_rev += 1

    # 3) 清理缓冲
    buf.get("sessions", {}).pop(session_id, None)
    _save_buffer(buf)
    print(f"[auto-deposit] ✅ 冲刷完成: browser {n_browser} 条 + rev {n_rev} 条 (session={session_id})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="自动沉淀 Hook (浏览器/逆向 → 小脑)")
    ap.add_argument("--flush", action="store_true", help="SessionEnd 冲刷模式")
    ap.add_argument("--session-id", default="", help="会话 ID (命令行指定, 优先于 stdin)")
    args = ap.parse_args()

    data: dict = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
    except Exception:
        pass

    session_id = args.session_id or str(data.get("session_id") or "adhoc")
    if args.flush:
        return cmd_flush(session_id)
    return cmd_buffer(session_id, str(data.get("tool_name") or ""), data.get("tool_input") or {})


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
