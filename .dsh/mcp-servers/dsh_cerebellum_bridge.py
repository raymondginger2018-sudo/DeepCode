#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh ↔ cerebellum 自动记忆桥接适配器（方案 B）

把 deepseek-harness (dsh) 的 hooks-claude-code 事件翻译为 cerebellum_cli.py 子命令：

    SessionStart  →  cerebellum_cli.py session_start        （设置快照 + vault 索引 + 简报）
    Stop          →  解压 dsh 会话 zstd → 提取对话文本
                     → 前缀标记（防 JSON 误判）→ stdin 管道传给
                     cerebellum_cli.py session_end --session-id <uuid>

用法（由 dsh hooks.json 调用，payload 从 stdin JSON 传入）：
    python dsh_cerebellum_bridge.py SessionStart
    python dsh_cerebellum_bridge.py Stop

设计要点：
- dsh 的 hook command 是静态字符串，payload 走 stdin JSON（hook_event_name/session_id/transcript_path/cwd）
- cerebellum 的 _read_session_context 对「非 JSON stdin」直接当对话文本返回；
  但对话文本可能是合法 JSON，必须加非 JSON 前缀标记（# dsh-session-transcript）避免误判
- dsh 会话文件是 zstd 压缩事件日志（session.jsonl.zstd），对话在
  user/message + assistant/message + tool/call + tool/result 事件中
- 任何异常 → 打印到 stderr 并退出 0（hook 非阻塞，绝不拖垮 dsh 会话）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# cerebellum 统一调度器入口 (DSH 资产区 — 2026-08-14 起所有提交者经此收口)
CEREBELLUM_CLI = Path(
    r"F:/DEEPCODE/.dsh/cerebellum-scheduler/scheduler.py"
)

# dsh 会话根目录（$DSH_HOME/sessions）
DSH_HOME = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
DSH_SESSIONS_DIR = DSH_HOME / "sessions"

# 非 JSON 前缀标记：确保传给 cerebellum 的 stdin 不是合法 JSON
# （对话文本若恰好是 JSON 会触发 payload 误判分支）
PREFIX_MARKER = "# dsh-session-transcript v1\n"

# cerebellum 对话上下文上限（与其 _read_session_context max_chars 一致）
MAX_CONTEXT_CHARS = 4000

# 单条消息截断
MAX_MSG_CHARS = 600


# ════════════════════════════════════════════════════════════════
# payload 读取
# ════════════════════════════════════════════════════════════════


def read_payload() -> dict:
    """读取 dsh hook 传入的 stdin JSON payload；失败返回空 dict。"""
    try:
        if sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
        if not data or not data.strip():
            return {}
        obj = json.loads(data)
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ════════════════════════════════════════════════════════════════
# dsh 会话 zstd 解压 + 对话提取
# ════════════════════════════════════════════════════════════════


def decompress_zstd(path: Path) -> str:
    """解压 zstd 文件为文本；优先 zstandard 库，回退 zstd CLI。"""
    try:
        import zstandard

        with open(path, "rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            reader = dctx.stream_reader(fh)
            return reader.read().decode("utf-8", errors="replace")
    except ImportError:
        try:
            result = subprocess.run(
                ["zstd", "-dc", str(path)],
                capture_output=True,
                timeout=30,
                check=False,
            )
            return result.stdout.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""
    except Exception:  # noqa: BLE001
        return ""


def _collect_text(value, out: list) -> None:
    """递归收集 dict 中的 text 字符串字段（user/assistant 消息内容）。"""
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            out.append(value["text"])
        for v in value.values():
            _collect_text(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_text(v, out)


def _truncate(text: str, limit: int = MAX_MSG_CHARS) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 20] + "\n...[截断]..."
    return text


def extract_dialogue(text: str) -> str:
    """从 dsh 事件日志 JSONL 中提取对话，格式化为 [user]/[assistant]/[tool] 行。"""
    lines_out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        etype = obj.get("type", "")
        data = obj.get("data") or {}
        if not isinstance(data, dict):
            continue

        if etype == "user/message":
            parts: list[str] = []
            _collect_text(data.get("content"), parts)
            if parts:
                lines_out.append(f"[user] {_truncate(' '.join(parts))}")

        elif etype == "assistant/message":
            msg = data.get("message") or {}
            parts = []
            _collect_text(msg.get("content"), parts)
            if parts:
                lines_out.append(f"[assistant] {_truncate(' '.join(parts))}")

        elif etype == "tool/call":
            name = data.get("name", "?")
            args = data.get("arguments", "")
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)
            lines_out.append(f"[tool] 调用: {name}({_truncate(str(args)[:200])})")

        elif etype == "tool/result":
            msg = data.get("message") or {}
            parts = []
            _collect_text(msg.get("content"), parts)
            _collect_text(data.get("content"), parts)
            if parts:
                lines_out.append(f"[tool] 结果: {_truncate(' '.join(parts))}")

    joined = "\n".join(lines_out).strip()
    return joined[:MAX_CONTEXT_CHARS]


def locate_transcript(payload: dict) -> Path | None:
    """定位 dsh 会话转录文件 session.jsonl.zstd。

    优先级：payload.transcript_path（可能是文件或目录）→ ~/.dsh/sessions 按 session_id 查找。
    """
    tp = payload.get("transcript_path") or ""
    if tp:
        p = Path(tp)
        if p.is_dir():
            for name in ("session.jsonl.zstd", "session.jsonl"):
                cand = p / name
                if cand.exists():
                    return cand
        elif p.exists():
            return p

    sid = payload.get("session_id") or ""
    if sid:
        for cand in DSH_SESSIONS_DIR.rglob("session.jsonl.zstd"):
            if sid in str(cand):
                return cand

    # 兜底：取最新的 session.jsonl.zstd
    all_files = sorted(DSH_SESSIONS_DIR.rglob("session.jsonl.zstd"), key=lambda f: f.stat().st_mtime, reverse=True)
    return all_files[0] if all_files else None


# ════════════════════════════════════════════════════════════════
# cerebellum 调用
# ════════════════════════════════════════════════════════════════


def run_cerebellum(args: list[str], input_text: str = "") -> int:
    """调用 cerebellum_cli.py，透传退出码；异常返回 0（非阻塞）。"""
    cmd = [sys.executable, str(CEREBELLUM_CLI), *args]
    try:
        result = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.stdout.strip():
            print(f"[dsh-cerebellum-bridge] stdout: {result.stdout.strip()[:500]}", flush=True)
        if result.stderr.strip():
            print(f"[dsh-cerebellum-bridge] stderr: {result.stderr.strip()[:500]}", flush=True)
        return result.returncode
    except Exception as exc:  # noqa: BLE001
        print(f"[dsh-cerebellum-bridge] 调用 cerebellum 异常: {exc}", flush=True)
        return 0


# ════════════════════════════════════════════════════════════════
# 事件处理
# ════════════════════════════════════════════════════════════════


def handle_session_start(payload: dict) -> int:
    """SessionStart → cerebellum session_start（设置快照 + vault 索引 + 简报）。"""
    hint = payload.get("prompt") or payload.get("source") or ""
    return run_cerebellum(["session_start", "--task-hint", hint[:200]])


def handle_stop(payload: dict) -> int:
    """Stop → 提取对话 → 前缀标记 → cerebellum session_end --session-id <uuid>。"""
    sid = payload.get("session_id") or "adhoc"
    transcript_path = locate_transcript(payload)
    if transcript_path is None:
        print("[dsh-cerebellum-bridge] 未找到 dsh 会话转录文件，跳过 session_end", flush=True)
        return 0

    raw = decompress_zstd(transcript_path)
    dialogue = extract_dialogue(raw)
    if not dialogue:
        print("[dsh-cerebellum-bridge] 会话无对话内容可提取，跳过 session_end", flush=True)
        return 0

    # 前缀标记：确保 stdin 不是合法 JSON（防 cerebellum 误判为 hook payload）
    text = PREFIX_MARKER + dialogue
    print(
        f"[dsh-cerebellum-bridge] session_end 会话={sid} 转录={transcript_path} 字符={len(dialogue)}",
        flush=True,
    )
    return run_cerebellum(["session_end", "--session-id", sid], input_text=text)


# ════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = read_payload()
    if event == "SessionStart":
        return handle_session_start(payload)
    if event == "Stop":
        return handle_stop(payload)
    print(f"[dsh-cerebellum-bridge] 未知事件: {event!r}（仅支持 SessionStart / Stop）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
