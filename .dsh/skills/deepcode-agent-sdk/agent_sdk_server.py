#!/usr/bin/env python3
"""
DeepCode Agent SDK Server v2.0 — Claude Code v2.1.216 Agent SDK 移植升级
═══════════════════════════════════════════════════════════════════════
对标官方 @anthropic-ai/claude-agent-sdk v0.3.220 补齐缺口：
  1. 事件流    — AgentEvent (init / assistant_delta / tool_call / tool_result / result)
  2. 多轮循环  — agent_run() ReAct 循环 (LLM 推理 → 工具调用 → 回填结果)
  3. 权限+预算 — permission_mode / allowed_tools / disallowed_tools / can_use_tool
                 / max_turns / max_budget_usd / 成本估算
  4. 会话持久化 — SQLite sessions/events 表，会话可查询/删除 (对标 listSessions)

三种模式：
  1. HTTP API  — RESTful + SSE 流式 (/agent/stream)，端口 8088
  2. MCP stdio — tools/call 支持 execute 与 agent_run
  3. Embedded  — from agent_sdk_server import DeepCodeAgent

用法:
  python agent_sdk_server.py --http --port 8088
  python agent_sdk_server.py --mcp
  from agent_sdk_server import DeepCodeAgent
  agent = DeepCodeAgent()
  async for ev in agent.stream_agent("分析项目结构"):
      print(ev.type, ev.data)
"""

import asyncio
import json
import os
import sqlite3
import sys
import uuid
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import StreamingResponse
    HAS_HTTP = True
except ImportError:
    HAS_HTTP = False

DEFAULT_DB = Path.home() / ".deepcode" / "agent_sdk_sessions.db"

# 派生子进程并发上限 (防进程风暴 + DeepSeek API 限流)
# 环境变量 DEEPCODE_SPAWN_MAX_CONCURRENCY 统一配额: 随 os.environ 传播到 spawn 的子进程,
# 使主进程 + 各分身使用相同上限 (默认 5)。子进程若也内嵌 agent-sdk (MCP 模式) 会读到同一值。
_SPAWN_MAX = max(1, int(os.environ.get("DEEPCODE_SPAWN_MAX_CONCURRENCY", "5") or "5"))
_SPAWN_SEM = asyncio.Semaphore(_SPAWN_MAX)

# ── 事件类型 (对标官方 SDKMessage 精简) ──────────────────────────

EVENT_INIT = "init"               # 会话初始化 (含 session_id)
EVENT_DELTA = "assistant_delta"   # 模型增量输出
EVENT_TOOL_CALL = "tool_call"     # 工具调用请求
EVENT_TOOL_RESULT = "tool_result" # 工具执行结果
EVENT_RESULT = "result"           # 最终结果 (success / error_max_turns / error_max_budget_usd)
EVENT_SYSTEM = "system"           # 系统级事件

# 写操作工具 (permission_mode=default 时保守拒绝，可被 can_use_tool 覆盖)
_WRITE_TOOLS = {"write_file", "execute_command", "edit_file", "delete_file"}
_EDIT_TOOLS = {"write_file", "edit_file"}


@dataclass
class AgentEvent:
    """事件流单元 — 对标官方 SDKMessage"""
    type: str
    data: Dict[str, Any]
    seq: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "seq": self.seq, "data": self.data}


@dataclass
class AgentOptions:
    """agent_run 选项 — 对标官方 SDK Options 精简"""
    model: str = "deepseek-chat"
    system_prompt: str = ""
    permission_mode: str = "default"            # default/acceptEdits/bypassPermissions/plan/dontAsk
    allowed_tools: Optional[List[str]] = None   # None=全部可用
    disallowed_tools: Optional[List[str]] = None
    can_use_tool: Optional[Callable] = None     # (tool_name, params) -> bool
    max_turns: int = 10
    max_budget_usd: float = 0.0                 # 0=不限制
    temperature: float = 0.3
    title: str = ""
    price_in_per_m: float = 0.27                # 成本单价 USD / 1M tokens
    price_out_per_m: float = 1.10
    llm_client: Any = None                      # 注入 mock 用；None 时自动构建

    @classmethod
    def from_dict(cls, d: Optional[Dict]) -> "AgentOptions":
        if not d:
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


# ── SQLite 会话存储 (对标官方 SessionStore / listSessions) ───────

class SessionStore:
    """会话持久化 — sessions + events 两表，遵循 db/sql-style 规范"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DEFAULT_DB)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # busy_timeout=30s: 多进程(主 DEEPCODE + spawn 分身)并发写同一库时等待而非立即报 BUSY
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass  # WAL 切换失败时降级回 rollback 模式，不阻断启动
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
            """
        )
        self.conn.commit()

    def create_session(self, title: str = "") -> str:
        sid = str(uuid.uuid4())
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, title, now, now),
        )
        self.conn.commit()
        return sid

    def touch(self, session_id: str):
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        self.conn.commit()

    def append_event(self, session_id: str, ev: AgentEvent) -> AgentEvent:
        ev.seq = self.next_seq(session_id)
        self.conn.execute(
            "INSERT INTO events (session_id, seq, type, payload, created_at) VALUES (?,?,?,?,?)",
            (session_id, ev.seq, ev.type, json.dumps(ev.data, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return ev

    def next_seq(self, session_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS n FROM events WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row["n"])

    def load_events(self, session_id: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT seq, type, payload FROM events WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [{"seq": r["seq"], "type": r["type"], "data": json.loads(r["payload"])} for r in rows]

    def list_sessions(self, limit: int = 50) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        self.conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
        cur = self.conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def rebuild_messages(self, session_id: str, extra_prompt: str = "") -> List[Dict]:
        """从事件重建 LLM 消息历史 (对标 resume: 恢复对话)"""
        messages: List[Dict] = []
        for ev in self.load_events(session_id):
            d = ev["data"]
            if ev["type"] == EVENT_DELTA and d.get("content"):
                messages.append({"role": "assistant", "content": d["content"]})
            elif ev["type"] == EVENT_TOOL_CALL:
                messages.append(self._tool_call_msg(d, ev["seq"]))
            elif ev["type"] == EVENT_TOOL_RESULT:
                messages.append({
                    "role": "tool",
                    "tool_call_id": d.get("call_id", f"call_{ev['seq']}"),
                    "content": json.dumps(d.get("output", d.get("error", "")), ensure_ascii=False),
                })
            elif ev["type"] == EVENT_INIT and d.get("role") == "user":
                messages.append({"role": "user", "content": d["content"]})
        if extra_prompt:
            messages.append({"role": "user", "content": extra_prompt})
        return messages

    @staticmethod
    def _tool_call_msg(d: Dict, seq: int) -> Dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": d.get("call_id", f"call_{seq}"),
                "type": "function",
                "function": {"name": d["name"],
                             "arguments": json.dumps(d["arguments"], ensure_ascii=False)},
            }],
        }


# ── 工具注册表 (保持原实现 + OpenAI schema) ──────────────────────

ToolHandler = Callable[..., Any]


class ToolRegistry:
    """Agent 可调用的工具注册表"""

    def __init__(self):
        self._tools: Dict[str, Dict] = {}

    def register(self, name_or_handler, handler: ToolHandler = None,
                 description: str = "", parameters: Dict = None):
        if handler is None:
            func = name_or_handler
            meta = getattr(func, "__mcp_tool__", {})
            name = meta.get("name", func.__name__)
            desc = meta.get("description", func.__doc__ or "")
            params = meta.get("parameters", {})
            self._tools[name] = {"handler": func, "description": desc, "parameters": params}
            return func
        self._tools[name_or_handler] = {
            "handler": handler, "description": description, "parameters": parameters or {},
        }

    def get_tool(self, name: str) -> Optional[Dict]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict]:
        return [
            {
                "name": name,
                "description": info["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": info["parameters"],
                    "required": [k for k, v in info["parameters"].items() if v.get("required", False)],
                },
            }
            for name, info in self._tools.items()
        ]

    def to_openai_schemas(self) -> List[Dict]:
        """转 OpenAI tools 格式 — 供 LLM 函数调用"""
        schemas = []
        for name, info in self._tools.items():
            props = {k: {kk: vv for kk, vv in v.items() if kk != "required"}
                     for k, v in info["parameters"].items()}
            required = [k for k, v in info["parameters"].items() if v.get("required", False)]
            schema: Dict = {"type": "object", "properties": props}
            if required:
                schema["required"] = required
            schemas.append({
                "type": "function",
                "function": {"name": name, "description": info["description"], "parameters": schema},
            })
        return schemas

    def call(self, name: str, **kwargs) -> Any:
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        return tool["handler"](**kwargs)


# ── Agent 运行时 (多轮循环 + 权限 + 预算) ────────────────────────

class AgentRuntime:
    """事件驱动 Agent 循环 — 对标官方 SDK Query/streamQuery"""

    def __init__(self, agent: "DeepCodeAgent", store: SessionStore):
        self.agent = agent
        self.store = store

    async def run(self, prompt: str, options: Optional[AgentOptions] = None) -> AsyncGenerator[AgentEvent, None]:
        """主循环: 推理 → 工具调用 → 回填，直到最终答案或达到上限"""
        opts = options or AgentOptions()
        sid = self.store.create_session(opts.title or prompt[:40])
        yield self._event(EVENT_INIT, {
            "session_id": sid, "role": "user", "content": prompt,
            "model": opts.model, "permission_mode": opts.permission_mode,
            "tools": list(self.agent.tools._tools.keys()),
        })
        messages: List[Dict] = [{"role": "user", "content": prompt}]
        turns, cost = 0, 0.0
        client = opts.llm_client or self.agent._make_llm_client()

        while turns < opts.max_turns:
            resp = await asyncio.to_thread(self._llm_chat, client, messages, opts)
            cost += self._estimate_cost(getattr(resp, "usage", None), opts)
            if self._over_budget(cost, opts):
                yield self._event(EVENT_RESULT, {
                    "subtype": "error_max_budget_usd", "result": "超出预算上限",
                    "total_cost_usd": round(cost, 6), "num_turns": turns})
                return
            msg = resp.choices[0].message
            if getattr(msg, "content", None):
                yield self._event(EVENT_DELTA, {"content": msg.content})
            if not getattr(msg, "tool_calls", None):
                yield self._event(EVENT_RESULT, {
                    "subtype": "success", "result": msg.content or "",
                    "total_cost_usd": round(cost, 6), "num_turns": turns})
                return
            for tc in msg.tool_calls:
                call_ev, result_ev = await self._execute_tool_call(tc, opts)
                yield call_ev
                yield result_ev
                messages.extend(self._tool_messages(tc, result_ev))
            turns += 1
        yield self._event(EVENT_RESULT, {
            "subtype": "error_max_turns", "result": f"达到最大轮数上限 ({opts.max_turns})",
            "total_cost_usd": round(cost, 6), "num_turns": turns})

    # ── 单次工具调用 (权限检查 + 执行) ─────────────────────────

    async def _execute_tool_call(self, tc, opts: AgentOptions):
        fn = tc.function
        name, raw_args = fn.name, fn.arguments or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        decision = await self._check_permission(name, args, opts)
        if decision == "deny":
            err = "权限拒绝: 未获授权执行该工具"
            return (
                self._event(EVENT_TOOL_CALL, {"name": name, "arguments": args,
                                              "call_id": tc.id, "allowed": False}),
                self._event(EVENT_TOOL_RESULT, {"name": name, "call_id": tc.id, "error": err}),
            )
        yield_ev = self._event(EVENT_TOOL_CALL, {"name": name, "arguments": args,
                                                 "call_id": tc.id, "allowed": True})
        try:
            result = self.agent.tools.call(name, **args)
            if asyncio.iscoroutine(result):
                result = await result
            output = result if isinstance(result, dict) else {"result": result}
        except Exception as e:
            output = {"error": str(e), "traceback": traceback.format_exc()[-800:]}
        result_ev = self._event(EVENT_TOOL_RESULT, {"name": name, "call_id": tc.id, "output": output})
        return yield_ev, result_ev

    @staticmethod
    def _tool_messages(tc, result_ev: AgentEvent) -> List[Dict]:
        """把一次工具调用追加为 LLM 消息 (assistant tool_call + tool result)"""
        raw_args = tc.function.arguments or "{}"
        return [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": raw_args}}]},
            {"role": "tool", "tool_call_id": tc.id,
             "content": json.dumps(result_ev.data.get("output", result_ev.data.get("error", "")),
                                   ensure_ascii=False)},
        ]

    # ── LLM 调用与成本 ────────────────────────────────────────

    def _llm_chat(self, client, messages: List[Dict], opts: AgentOptions):
        """同步 LLM 调用 (DeepSeek function calling)；可被 mock 注入"""
        return client.chat.completions.create(
            model=opts.model,
            messages=messages,
            tools=self.agent.tools.to_openai_schemas(),
            temperature=opts.temperature,
        )

    @staticmethod
    def _estimate_cost(usage, opts: AgentOptions) -> float:
        """按 token 估算成本 (USD) — 单价可配"""
        if not usage:
            return 0.0
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        return (inp * opts.price_in_per_m + out * opts.price_out_per_m) / 1_000_000

    @staticmethod
    def _over_budget(cost: float, opts: AgentOptions) -> bool:
        return opts.max_budget_usd > 0 and cost > opts.max_budget_usd

    # ── 权限决策 (对标官方 permissionMode + canUseTool) ─────────

    async def _check_permission(self, name: str, args: Dict, opts: AgentOptions) -> str:
        if opts.disallowed_tools and name in opts.disallowed_tools:
            return "deny"
        if opts.allowed_tools and name not in opts.allowed_tools:
            return "deny"
        mode = opts.permission_mode
        if mode == "bypassPermissions":
            return "allow"
        if mode == "plan":
            return "deny"          # 计划模式只读，不执行
        if mode == "dontAsk":
            return "deny"          # 未预授权一律拒绝
        if mode == "acceptEdits" and name in _EDIT_TOOLS:
            return "allow"
        if mode == "default" and name in _WRITE_TOOLS:
            return await self._ask_or_deny(name, args, opts)   # 无交互时保守拒绝
        if opts.can_use_tool:
            return await self._ask_or_deny(name, args, opts)
        return "allow"

    @staticmethod
    async def _ask_or_deny(name: str, args: Dict, opts: AgentOptions) -> str:
        if not opts.can_use_tool:
            return "deny"
        try:
            r = opts.can_use_tool(name, args)
            if asyncio.iscoroutine(r):
                r = await r
            return "allow" if r else "deny"
        except Exception:
            return "deny"

    @staticmethod
    def _event(ev_type: str, data: Dict) -> AgentEvent:
        return AgentEvent(type=ev_type, data=data)


# ── Agent SDK 核心 (保持向后兼容 + 新增事件能力) ────────────────

class DeepCodeAgent:
    """DeepCode Agent — 移植自 Claude Code agentSdk.ts，v2.0 加入事件驱动"""

    def __init__(self, workspace: str = None, allowed_dirs: List[str] = None,
                 db_path: Optional[str] = None):
        self.agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        self.workspace = workspace or os.getcwd()
        self.allowed_dirs = allowed_dirs or [self.workspace]
        self.tools = ToolRegistry()
        self.store = SessionStore(db_path)
        self.runtime = AgentRuntime(self, self.store)
        self._task_history: List[Dict] = []
        # 派生子进程治理: 深度/授权通过环境变量继承 (总管→工人→组长→工人)
        # DEEPCODE_AGENT_ALLOW_SPAWN: 当前 agent 是否被父授权可派生 (默认否)
        # DEEPCODE_AGENT_DEPTH:       当前 agent 的派生深度 (总管=0, 工人=1, 组长=2, 封顶 3)
        self._spawn_allowed = os.environ.get("DEEPCODE_AGENT_ALLOW_SPAWN", "0") == "1"
        self._spawn_depth = int(os.environ.get("DEEPCODE_AGENT_DEPTH", "0") or "0")
        self._register_default_tools()

    def register_tool(self, name: str, handler: ToolHandler,
                      description: str = "", parameters: Dict = None):
        self.tools.register(name, handler, description, parameters)

    def _register_default_tools(self):
        """注册内置工具 — 对标 Claude Code agentSdk.ts 能力"""

        @self.tools.register
        async def read_file(path: str):
            """读取文件内容"""
            resolved = self._resolve_path(path)
            if not resolved:
                return {"error": f"Access denied: {path}"}
            content = Path(resolved).read_text(encoding="utf-8", errors="replace")
            return {"path": str(resolved), "content": content, "size": len(content)}

        read_file.__mcp_tool__ = {
            "name": "read_file",
            "description": "读取指定文件的内容",
            "parameters": {"path": {"type": "string", "description": "文件路径", "required": True}},
        }

        @self.tools.register
        async def write_file(path: str, content: str):
            """写入文件"""
            resolved = self._resolve_path(path)
            if not resolved:
                return {"error": f"Access denied: {path}"}
            resolved.parent.mkdir(parents=True, exist_ok=True)
            Path(resolved).write_text(content, encoding="utf-8")
            return {"path": str(resolved), "size": len(content), "status": "written"}

        write_file.__mcp_tool__ = {
            "name": "write_file",
            "description": "写入内容到指定文件",
            "parameters": {
                "path": {"type": "string", "description": "文件路径", "required": True},
                "content": {"type": "string", "description": "文件内容", "required": True},
            },
        }

        @self.tools.register
        async def execute_command(command: str, timeout: int = 30):
            """执行 Shell 命令"""
            import subprocess
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True,
                    timeout=timeout, cwd=self.workspace
                )
                return {"stdout": result.stdout, "stderr": result.stderr,
                        "exit_code": result.returncode}
            except subprocess.TimeoutExpired:
                return {"error": f"Command timed out after {timeout}s"}
            except Exception as e:
                return {"error": str(e)}

        execute_command.__mcp_tool__ = {
            "name": "execute_command",
            "description": "执行 Shell 命令 (Bash)",
            "parameters": {
                "command": {"type": "string", "description": "要执行的命令", "required": True},
                "timeout": {"type": "integer", "description": "超时秒数"},
            },
        }

        @self.tools.register
        async def search_files(pattern: str, path: str = "."):
            """搜索文件"""
            import fnmatch
            base = self._resolve_path(path) or self.workspace
            matches = []
            for root, dirs, files in os.walk(base):
                for f in files:
                    if fnmatch.fnmatch(f, pattern):
                        matches.append(os.path.join(root, f))
            return {"matches": matches[:100], "total": len(matches)}

        search_files.__mcp_tool__ = {
            "name": "search_files",
            "description": "搜索匹配 pattern 的文件",
            "parameters": {
                "pattern": {"type": "string", "description": "通配符模式", "required": True},
                "path": {"type": "string", "description": "搜索起始目录"},
            },
        }

        @self.tools.register
        async def list_directory(path: str = "."):
            """列出目录内容"""
            resolved = self._resolve_path(path)
            if not resolved:
                return {"error": f"Access denied: {path}"}
            items = []
            for entry in sorted(Path(resolved).iterdir()):
                items.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
            return {"path": str(resolved), "items": items}

        list_directory.__mcp_tool__ = {
            "name": "list_directory",
            "description": "列出目录内容",
            "parameters": {"path": {"type": "string", "description": "目录路径"}},
        }

        @self.tools.register
        async def agent_query(query: str, model: str = "auto"):
            """向 DeepSeek 模型发送查询"""
            try:
                from openai import OpenAI
                client = OpenAI(
                    api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
                    base_url="https://api.deepseek.com",
                )
                actual_model = model if model != "auto" else "deepseek-chat"
                response = client.chat.completions.create(
                    model=actual_model,
                    messages=[{"role": "user", "content": query}],
                    max_tokens=4096,
                )
                return {"response": response.choices[0].message.content}
            except ImportError:
                return {"error": "openai not installed. pip install openai"}
            except Exception as e:
                return {"error": str(e)}

        agent_query.__mcp_tool__ = {
            "name": "agent_query",
            "description": "向 AI 模型发送查询",
            "parameters": {
                "query": {"type": "string", "description": "查询内容", "required": True},
                "model": {"type": "string", "description": "模型名称 (auto=自动)"},
            },
        }

        @self.tools.register
        async def agent_status():
            """获取 Agent 状态信息"""
            return {
                "agent_id": self.agent_id,
                "workspace": self.workspace,
                "allowed_dirs": self.allowed_dirs,
                "tools_count": len(self.tools._tools),
                "tools": list(self.tools._tools.keys()),
                "tasks_completed": len(self._task_history),
                "sessions": len(self.store.list_sessions(100)),
                "uptime": datetime.now().isoformat(),
            }

        agent_status.__mcp_tool__ = {
            "name": "agent_status",
            "description": "获取 Agent 运行状态",
            "parameters": {},
        }

        @self.tools.register
        async def spawn_deepcode(prompt: str, workspace: str = None,
                                 max_iterations: int = 20, timeout: int = 300,
                                 max_output_chars: int = 8000, model: str = None,
                                 allow_spawn: bool = False):
            """派生一个子 DEEPCODE 进程执行任务 (deepcode exec headless 通道)"""
            if not self._spawn_allowed:
                return {"error": "当前上下文未授权派生子进程 (父任务需 allow_spawn=True 显式升格)"}
            if self._spawn_depth >= 3:
                return {"error": f"达到最大派生深度 3 (当前深度 {self._spawn_depth})，拒绝派生"}
            import subprocess as sp
            ws = workspace or self.workspace
            Path(ws).mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, "-m", "cli.exec_cli", "--", prompt,
                   "--workspace", ws, "--json",
                   "--max-iterations", str(max_iterations)]
            if model:
                cmd += ["--model", model]
            env = dict(os.environ)
            env["DEEPCODE_AGENT_DEPTH"] = str(self._spawn_depth + 1)
            env["DEEPCODE_AGENT_ALLOW_SPAWN"] = "1" if allow_spawn else "0"
            env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "") + os.pathsep + "F:/DEEPCODE"
            try:
                async with _SPAWN_SEM:
                    result = await asyncio.to_thread(
                        sp.run, cmd, capture_output=True, text=True,
                        timeout=timeout, cwd="F:/DEEPCODE", env=env, shell=False)
            except sp.TimeoutExpired:
                return {"error": f"子 DEEPCODE 超时 ({timeout}s)，已终止"}
            except Exception as e:
                return {"error": f"子 DEEPCODE 启动失败: {e}"}
            stdout = result.stdout or ""
            return {
                "exit_code": result.returncode,
                "summary": self._extract_task_summary(stdout),
                "output": stdout[:max_output_chars],
                "stderr": (result.stderr or "")[:2000],
                "depth": self._spawn_depth + 1,
                "workspace": ws,
            }

        spawn_deepcode.__mcp_tool__ = {
            "name": "spawn_deepcode",
            "description": "派生一个子 DEEPCODE 进程执行任务 (headless)。"
                           "默认工人 (allow_spawn=False) 不能再派生；"
                           "组长 (allow_spawn=True) 可再派生一层；最大深度 3。"
                           "子进程用 --workspace 隔离工作目录，输出 NDJSON 截断返回。",
            "parameters": {
                "prompt": {"type": "string", "description": "子任务描述", "required": True},
                "workspace": {"type": "string", "description": "子进程工作目录 (隔离, 默认同父)"},
                "max_iterations": {"type": "integer", "description": "子 agent 最大轮次 (默认 20)"},
                "timeout": {"type": "integer", "description": "超时秒数 (默认 300)"},
                "max_output_chars": {"type": "integer", "description": "输出截断字符数 (默认 8000)"},
                "model": {"type": "string", "description": "模型覆盖 (默认继承)"},
                "allow_spawn": {"type": "boolean",
                                "description": "允许子进程再派生 (显式升格为组长)"},
            },
        }

    # ── 路径解析与安全 ────────────────────────────────────────

    def _resolve_path(self, path: str) -> Optional[Path]:
        p = Path(path)
        if not p.is_absolute():
            p = Path(self.workspace) / p
        p = p.resolve()
        for allowed in self.allowed_dirs:
            try:
                p.relative_to(Path(allowed).resolve())
                return p
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_task_summary(stdout: str) -> str:
        """从子 DEEPCODE 的 NDJSON 事件流提取 task_complete 摘要"""
        for line in stdout.splitlines():
            if "task_complete" not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = ev.get("msg", ev)
            return json.dumps(msg, ensure_ascii=False)[:500]
        return ""

    # ── LLM 客户端 ────────────────────────────────────────────

    def _make_llm_client(self):
        from openai import OpenAI
        return OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

    # ── 任务执行 (向后兼容: 单工具调用) ────────────────────────

    async def execute(self, tool_name: str, params: Dict = None) -> Dict:
        params = params or {}
        start = datetime.now()
        try:
            result = await self.tools.call(tool_name, **params)
            status = "success"
        except Exception as e:
            result = {"error": str(e), "traceback": traceback.format_exc()}
            status = "error"
        duration = (datetime.now() - start).total_seconds()
        self._task_history.append({
            "tool": tool_name, "params": params, "status": status,
            "duration": duration, "timestamp": start.isoformat(),
        })
        return {"result": result, "meta": {"status": status, "duration": duration, "tool": tool_name}}

    async def execute_batch(self, tasks: List[Dict]) -> List[Dict]:
        results = []
        for task in tasks:
            r = await self.execute(task.get("tool"), task.get("params", {}))
            results.append(r)
        return results

    # ── 事件驱动 Agent 循环 (新) ──────────────────────────────

    async def stream_agent(self, prompt: str, options: Optional[Dict] = None) -> AsyncGenerator[AgentEvent, None]:
        """流式事件生成器 — 对标官方 streamQuery，事件自动入库"""
        opts = AgentOptions.from_dict(options)
        sid = ""
        async for ev in self.runtime.run(prompt, opts):
            if ev.type == EVENT_INIT:
                sid = ev.data.get("session_id", "")
            if sid:
                ev = self.store.append_event(sid, ev)
            yield ev

    async def run_agent(self, prompt: str, options: Optional[Dict] = None) -> Dict:
        """一次跑完多轮循环，返回完整事件列表 — 兼容 MCP/HTTP 请求-响应"""
        opts = AgentOptions.from_dict(options)
        events: List[Dict] = []
        sid = ""
        async for ev in self.runtime.run(prompt, opts):
            if ev.type == EVENT_INIT:
                sid = ev.data.get("session_id", "")
            if sid:
                ev = self.store.append_event(sid, ev)
            events.append(ev.to_dict())
        last = events[-1] if events else {}
        return {
            "session_id": sid,
            "events": events,
            "result": last.get("data") if last.get("type") == EVENT_RESULT else None,
        }

    # ── 会话管理 (对标官方 listSessions / getSessionMessages) ──

    def list_sessions(self, limit: int = 50) -> List[Dict]:
        return self.store.list_sessions(limit)

    def get_session_events(self, session_id: str) -> List[Dict]:
        return self.store.load_events(session_id)

    def resume_session(self, session_id: str, prompt: str = "",
                       options: Optional[Dict] = None) -> Dict:
        """恢复会话消息 (对标官方 resume) — 从事件重建 LLM 消息历史"""
        opts = AgentOptions.from_dict(options)
        return {
            "session_id": session_id,
            "history": self.store.load_events(session_id),
            "messages": self.store.rebuild_messages(session_id, prompt),
            "options": {k: v for k, v in opts.__dict__.items() if not callable(v)},
        }

    # ── MCP 协议适配 ─────────────────────────────────────────

    async def handle_mcp_request(self, request: Dict) -> Dict:
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id", str(uuid.uuid4()))

        if method == "initialize":
            # 标准 MCP 握手 — 客户端必须先调此方法 (core/agent_runtime/tools/mcp.py 的 session.initialize())
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "deepcode-agent-sdk", "version": "2.0.0"},
                },
            }
        if method.startswith("notifications/"):
            # 通知类消息 (initialized/cancelled 等) — 静默接收，不响应
            return None
        if method == "tools/list":
            tools = self.tools.list_tools()
            tools.append({
                "name": "agent_run",
                "description": "运行多轮 Agent 循环 (事件流) — 参数: prompt 必填, options 可选 "
                             "(model/permission_mode/allowed_tools/disallowed_tools/max_turns/"
                             "max_budget_usd/temperature)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "任务提示词"},
                        "options": {"type": "object", "description": "AgentOptions 选项"},
                    },
                    "required": ["prompt"],
                },
            })
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            if name == "agent_run":
                result = await self.run_agent(args.get("prompt", ""), args.get("options"))
            else:
                result = await self.execute(name, args)
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text",
                                            "text": json.dumps(result, ensure_ascii=False, indent=2)}]}}
        if method == "ping":
            # 标准 MCP ping — result 为空对象
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}}


# ── MCP stdio 模式 ──────────────────────────────────────────────

async def run_mcp_stdio(agent: DeepCodeAgent):
    # 标准 MCP stdio: 不主动推送任何消息，被动等待客户端 initialize 握手
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = await agent.handle_mcp_request(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}),
                  flush=True)


# ── HTTP Server 模式 ────────────────────────────────────────────

def _sse(event: Dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_http_app(agent: DeepCodeAgent):
    if not HAS_HTTP:
        raise RuntimeError("FastAPI not installed. pip install fastapi uvicorn")

    app = FastAPI(title="DeepCode Agent SDK", version="2.0.0",
                  description="DeepCode Agent — 外部程序可调用的 AI Agent (事件驱动)")

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent_id": agent.agent_id}

    @app.get("/tools")
    async def list_tools():
        return {"tools": agent.tools.list_tools()}

    @app.post("/execute")
    async def execute_tool(request: Request):
        body = await request.json()
        tool = body.get("tool")
        params = body.get("params", {})
        if not tool:
            raise HTTPException(status_code=400, detail="缺少 'tool' 字段")
        return await agent.execute(tool, params)

    @app.post("/agent/run")
    async def agent_run(request: Request):
        """一次跑完多轮循环，返回完整事件列表"""
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(status_code=400, detail="缺少 'prompt' 字段")
        return await agent.run_agent(prompt, body.get("options"))

    @app.post("/agent/stream")
    async def agent_stream(request: Request):
        """SSE 流式事件 — 对标官方 streamQuery"""
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(status_code=400, detail="缺少 'prompt' 字段")
        opts = AgentOptions.from_dict(body.get("options", {}))
        sid = ""

        async def gen():
            nonlocal sid
            async for ev in agent.runtime.run(prompt, opts):
                if ev.type == EVENT_INIT:
                    sid = ev.data.get("session_id", "")
                if sid:
                    ev = agent.store.append_event(sid, ev)
                yield _sse(ev.to_dict())
            yield "event: done\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/batch")
    async def batch_execute(request: Request):
        body = await request.json()
        tasks = body.get("tasks", [])
        return {"results": await agent.execute_batch(tasks)}

    @app.get("/history")
    async def get_history(limit: int = 10):
        return {"history": agent._task_history[-limit:]}

    @app.get("/sessions")
    async def list_sessions(limit: int = 50):
        return {"sessions": agent.list_sessions(limit)}

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str):
        s = agent.store.get_session(session_id)
        if not s:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"session": s, "events": agent.get_session_events(session_id)}

    @app.post("/sessions/{session_id}/resume")
    async def resume_session(session_id: str, request: Request):
        body = await request.json()
        return agent.resume_session(session_id, body.get("prompt", ""), body.get("options"))

    @app.delete("/sessions/{session_id}")
    async def delete_session(session_id: str):
        return {"deleted": agent.store.delete_session(session_id)}

    return app


# ── CLI 入口 ────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="DeepCode Agent SDK Server v2.0")
    parser.add_argument("--http", action="store_true", help="以 HTTP Server 模式启动")
    parser.add_argument("--mcp", action="store_true", help="以 MCP stdio 模式启动")
    parser.add_argument("--port", type=int, default=8088, help="HTTP 端口 (默认 8088)")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 绑定地址 (默认 127.0.0.1)")
    parser.add_argument("--workspace", default=os.getcwd(), help="Agent 工作目录")
    parser.add_argument("--db", default=None, help="SQLite 会话库路径")
    parser.add_argument("--allow-dir", action="append", dest="allow_dirs",
                        help="允许访问的目录 (可多次指定)")

    args = parser.parse_args()

    allowed = args.allow_dirs or [args.workspace]
    agent = DeepCodeAgent(workspace=args.workspace, allowed_dirs=allowed, db_path=args.db)

    if args.http:
        if not HAS_HTTP:
            print("Error: HTTP mode requires fastapi+uvicorn. pip install fastapi uvicorn")
            sys.exit(1)
        app = create_http_app(agent)
        print(f"[agent-sdk] HTTP Server starting on {args.host}:{args.port}")
        print(f"[agent-sdk] Tools: {list(agent.tools._tools.keys())}")
        print(f"[agent-sdk] Sessions DB: {agent.store.db_path}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    elif args.mcp:
        print(f"[agent-sdk] MCP stdio mode starting", file=sys.stderr)
        print(f"[agent-sdk] Workspace: {args.workspace}", file=sys.stderr)
        print(f"[agent-sdk] Sessions DB: {agent.store.db_path}", file=sys.stderr)
        asyncio.run(run_mcp_stdio(agent))
    else:
        print("[agent-sdk] Interactive mode. Type 'help' for commands.")

        async def interactive():
            while True:
                try:
                    cmd = input("agent> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not cmd:
                    continue
                if cmd == "help":
                    print("Available commands:")
                    for t in agent.tools.list_tools():
                        print(f"  {t['name']}: {t['description']}")
                    print("  run <prompt>: 多轮 Agent 循环")
                    print("  sessions: 列出会话")
                    print("  exit/quit: 退出")
                elif cmd in ("exit", "quit"):
                    break
                elif cmd == "sessions":
                    print(json.dumps(agent.list_sessions(10), ensure_ascii=False, indent=2))
                elif cmd.startswith("run "):
                    prompt = cmd[4:]
                    result = await agent.run_agent(prompt, {"permission_mode": "acceptEdits"})
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                elif cmd == "status":
                    result = await agent.execute("agent_status")
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    parts = cmd.split(maxsplit=1)
                    tool = parts[0]
                    params = json.loads(parts[1]) if len(parts) > 1 else {}
                    result = await agent.execute(tool, params)
                    print(json.dumps(result, ensure_ascii=False, indent=2))

        asyncio.run(interactive())


if __name__ == "__main__":
    main()
