#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode 小脑 — MCP Server
═══════════════════════════
让大脑 (DeepSeek) 通过 MCP 工具访问小脑记忆引擎:
  - 设置快照/查询/搜索
  - 统一记忆存取
  - 经验记录/搜索
  - 会话摘要/最近会话
  - 知识库索引
  - 小脑健康状态

注册到 settings.json:
  "deepcode-cerebellum": {
    "command": "python",
    "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-cerebellum/cerebellum_mcp_server.py", "--mcp"]
  }
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cerebellum_core import (
    CerebellumMemory,
    consolidated_search,
    experience_graph,
    experience_graph_query,
    experience_record,
    experience_search,
    index_vault,
    ollama_status,
    overview,
    session_recent,
    session_summarize,
    settings_analyses_history,
    settings_analyze,
    settings_latest,
    settings_search,
    settings_snapshot,
)
# 记忆进化引擎 (Dreaming / 反馈闭环 / Skill 进化信号 / 评测)
from cerebellum_evolution import (
    benchmark_list,
    benchmark_run,
    dreaming_history,
    dreaming_run,
    feedback_add,
    feedback_list,
    skill_evolution_apply,
    skill_evolution_list,
    skill_evolution_propose,
    skill_evolution_reject,
    skill_signal_add,
    skill_signals_list,
    learning_loop_detect,
    learning_loop_list,
    learning_loop_save,
)

# ═══════════════════════════════════════════
# MCP 工具定义
# ═══════════════════════════════════════════

TOOLS = {
    "cerebellum_overview": {
        "description": "小脑全景状态 — Ollama 健康/模型/记忆统计/数据库位置",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_ollama_status": {
        "description": "小脑硬件层 (Ollama) 健康检查 — 模型列表/可用性",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_settings_snapshot": {
        "description": "快照 settings.json 到记忆库 (含变更检测), scope: all/project/user",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "all|project|user, 默认 all"}},
        },
    },
    "cerebellum_settings_latest": {
        "description": "读取最新设置快照 — 含完整版与脱敏版 (密钥指纹展示)",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "project|user, 默认 project"}},
        },
    },
    "cerebellum_settings_search": {
        "description": "在设置快照中搜索配置项 — 关键词 + 语义双通道 (记不清原词也能找到)",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索词, 如 '模型路由' 'hooks' 'mcp 服务器'"}},
        },
        "required": ["query"],
    },
    "cerebellum_memory_save": {
        "description": "保存记忆 — 统一收编到全部记忆后端 + 语义索引",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "required": ["key", "value"],
    },
    "cerebellum_memory_load": {
        "description": "读取记忆 (统一后端 + 语义兜底)",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
        },
        "required": ["key"],
    },
    "cerebellum_memory_search": {
        "description": "搜索记忆 — 语义优先, 关键词兜底, 返回双通道结果",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        },
        "required": ["query"],
    },
    "cerebellum_memory_list": {
        "description": "列出所有记忆键名",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_experience_record": {
        "description": "记录一条经验教训 (PostTask 提炼, 本地模型)",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "任务描述"}},
        },
        "required": ["task"],
    },
    "cerebellum_experience_search": {
        "description": "搜索历史经验教训 — 语义相似度匹配",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "required": ["query"],
    },
    "cerebellum_consolidated_search": {
        "description": "检索已巩固的精华记忆/知识 (Dreaming 产物) — 可按 kind 过滤, 支持 as_of 时态检索",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "description": "session|experience|knowledge, 默认全部"},
                "limit": {"type": "integer", "description": "默认 5"},
                "as_of": {"type": "string", "description": "时态截止 (ISO 时间), 只返回该时刻前已存在的巩固记忆"},
                "strict": {"type": "boolean", "description": "严格模式: 存在被时态过滤的未来记忆时抛 ClockDomainError"},
            },
        },
        "required": ["query"],
    },
    "cerebellum_session_summarize": {
        "description": "生成会话摘要并持久化 (PreCompact, 本地模型)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "对话内容 (截断到 4000 字符)"},
                "session_id": {"type": "string"},
            },
        },
    },
    "cerebellum_session_recent": {
        "description": "查看最近会话摘要",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 5"}},
        },
    },
    "cerebellum_index_vault": {
        "description": "索引知识库 vault 笔记到语义检索 (支持跨笔记语义搜索)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_settings_analyze": {
        "description": "用 deepseek-r1:1.5b 语义分析 settings.json — 配置语义/风险/建议 (幂等缓存)",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "project|user, 默认 project"}},
        },
    },
    "cerebellum_settings_analyses_history": {
        "description": "查看设置语义分析历史",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 5"},
            },
        },
    },
    "cerebellum_experience_graph": {
        "description": "构建/查看跨会话经验关联图谱 — embedding 相似度建边 (rebuild=True 重建)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_similarity": {"type": "number", "description": "建边阈值, 默认 0.5"},
                "rebuild": {"type": "boolean", "description": "True 重建全部边"},
            },
        },
    },
    "cerebellum_experience_graph_query": {
        "description": "按任务语义查询经验图谱 — 返回最相关经验节点及其关联邻居",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "required": ["query"],
    },
    # ── 记忆进化引擎 (对标 MindMemOS: Dreaming / 反馈闭环 / Skill 进化信号 / 评测) ──
    "cerebellum_dreaming_run": {
        "description": "离线记忆巩固 (Dreaming) — 聚类未整合会话摘要/经验并 LLM 合并归档",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "session|experience|knowledge, 默认 session"},
                "use_llm": {"type": "boolean", "description": "是否用本地模型合并, 默认 true"},
            },
        },
    },
    "cerebellum_dreaming_history": {
        "description": "查看 Dreaming 巩固历史 (dreaming_runs)",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 5"}},
        },
    },
    "cerebellum_feedback_add": {
        "description": "添加检索反馈 (显式/隐式) — 净评分回灌排序 (rating: 1 有用 / -1 无用 / -2 严重错误)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_type": {"type": "string", "description": "memory|experience|session|note"},
                "target_key": {"type": "string", "description": "语义条目 source_key (记忆 key / 经验 id / 会话 id / 笔记名)"},
                "rating": {"type": "integer", "description": "1 | -1 | -2"},
                "source": {"type": "string", "description": "explicit|implicit|on_error, 默认 explicit"},
                "comment": {"type": "string", "description": "反馈说明"},
            },
        },
        "required": ["target_type", "target_key", "rating"],
    },
    "cerebellum_feedback_list": {
        "description": "查看反馈记录 — 可按类型/目标过滤",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_type": {"type": "string"},
                "target_key": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 20"},
            },
        },
    },
    "cerebellum_skill_signal_add": {
        "description": "采集 Skill 执行信号 (失败/成功) — 失败信号累积 >=3 可触发进化提案",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill 名 (如 deepcode-cerebellum)"},
                "signal_type": {"type": "string", "description": "failure|success, 默认 failure"},
                "context": {"type": "string", "description": "触发上下文"},
                "error": {"type": "string", "description": "错误信息/失败痕迹"},
            },
        },
        "required": ["skill_name"],
    },
    "cerebellum_skill_signals_list": {
        "description": "查看 Skill 信号记录",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 20"},
            },
        },
    },
    "cerebellum_skill_evolution_propose": {
        "description": "生成 Skill 进化提案 — 失败信号 >=3 时 LLM 分析失败模式, 写入 pending 提案 (人工确认后应用)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "use_llm": {"type": "boolean", "description": "是否用本地模型分析, 默认 true"},
            },
        },
        "required": ["skill_name"],
    },
    "cerebellum_skill_evolution_list": {
        "description": "查看 Skill 进化提案 (pending/applied/rejected)",
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string", "description": "pending|applied|rejected, 默认全部"}},
        },
    },
    "cerebellum_skill_evolution_apply": {
        "description": "应用进化提案 — 在 SKILL.md 末尾追加 '## 进化记录' 章节 (不删除原内容)",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "integer"}},
        },
        "required": ["proposal_id"],
    },
    "cerebellum_skill_evolution_reject": {
        "description": "拒绝进化提案",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "integer"}},
        },
        "required": ["proposal_id"],
    },
    "cerebellum_benchmark_run": {
        "description": "自评测记忆检索 — Recall@1/@k + MRR, 输出 Markdown 报告 (基线分)",
        "inputSchema": {
            "type": "object",
            "properties": {"top_k": {"type": "integer", "description": "默认 5"}},
        },
    },
    "cerebellum_benchmark_list": {
        "description": "查看评测历史 (benchmark_runs)",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 5"}},
        },
    },
    "cerebellum_learning_loop_detect": {
        "description": "运行学习循环检测器 (10 模式, 纯规则, 只读 cerebellum.db) — 返回候选干预, 可选持久化",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_priority": {"type": "integer", "description": "最低优先级 0-100, 默认 0"},
                "limit": {"type": "integer", "description": "返回数量上限, 默认 50"},
                "persist": {"type": "boolean", "description": "True 时把候选写入 learning_loop_candidates (幂等去重)"},
            },
        },
    },
    "cerebellum_learning_loop_save": {
        "description": "持久化一条学习循环候选 (status=pending, 幂等去重) — 输入 detector 输出的 camelCase 候选对象",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate": {"type": "object", "description": "候选 JSON 对象 (patternId/observedBehavior 必填)"},
            },
        },
        "required": ["candidate"],
    },
    "cerebellum_learning_loop_list": {
        "description": "查看已保存的学习循环候选 (按 priority_score 降序)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "pending/applied/rejected/dismissed, 默认全部"},
                "limit": {"type": "integer", "description": "默认 50"},
            },
        },
    },
    # ── MCP 网关 (按需唤醒/睡眠托管 MCP server) ──
    "cerebellum_mcp_list": {
        "description": "列出休眠池中可托管 MCP server 的状态 — 睡眠/唤醒 + 工具数 + 已注册工具名, 决定要不要 wake。池内 4 个: ghidra-mcp (Ghidra 逆向分析, 288 工具), deepcode-decompiler (字节码/PE 反编译, 5 工具), tushareMcp (Tushare 股票数据), winapp (Windows UI 自动化, 54 工具)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cerebellum_mcp_wake": {
        "description": "唤醒休眠池中的 MCP server — spawn 子进程 → 握手 → 注册其全部工具 → 发 list_changed 通知 CLI 热刷新",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "休眠池 server 名: ghidra-mcp / deepcode-decompiler / tushareMcp / winapp"}},
        },
        "required": ["name"],
    },
    "cerebellum_mcp_sleep": {
        "description": "让已唤醒的 MCP server 进入睡眠 — 注销其全部工具 → kill 子进程 → 发 list_changed 通知 CLI 热刷新 (释放 token)",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "已唤醒的 server 名"}},
        },
        "required": ["name"],
    },
}


# ═══════════════════════════════════════════
# MCP 网关 — 按需唤醒/睡眠托管 MCP server
# ═══════════════════════════════════════════

# 休眠池: 被小脑托管、按需唤醒的 MCP server (settings.json 中保持禁用, 由小脑直接 spawn)
MCP_SLEEP_POOL = {
    "ghidra-mcp": {
        "command": "F:/DEEPCODE/tools/ghidra-mcp/.venv/Scripts/bridge-mcp-ghidra.exe",
        "args": ["--transport", "stdio"],
        "env": {"GHIDRA_MCP_URL": "http://127.0.0.1:8089", "PYTHONIOENCODING": "utf-8"},
        "description": "Ghidra 逆向分析桥 (需 Ghidra 服务在 127.0.0.1:8089 运行, 288 工具)",
        "tool_prefix": "",
    },
    "deepcode-decompiler": {
        "command": "python",
        "args": ["F:/DEEPCODE/tools/mcp_decompiler_server.py"],
        "env": {"PYTHONIOENCODING": "utf-8"},
        "description": "字节码/PE 反编译引擎: decompile_bytes/PE/pcode + CFG 分析 (5 工具)",
        "tool_prefix": "",
    },
    "tushareMcp": {
        "command": "python",
        "args": ["F:/DEEPCODE/core/mcp_servers/tushare_mcp_bridge.py"],
        "env": {"TUSHARE_TOKEN": "1a41dc66d0be5585c2a382cfd66fceb5793ee3ad348c372f8118fc14"},
        "description": "Tushare 股票数据: 行情/财务/交易日历/股东数据",
        "tool_prefix": "",
    },
    "winapp": {
        "command": "C:/Users/raymo/AppData/Roaming/npm/winapp-mcp.cmd",
        "args": [],
        "env": {},
        "description": "Windows UI 自动化: 窗口/鼠标/键盘控制 (54 工具)",
        "tool_prefix": "",
    },
}

# 已唤醒的 server: name -> {"proc": Popen, "tools": {tool_name: tool_schema}}
_active_servers: dict[str, dict] = {}
# 动态工具注册表: tool_name -> {"server": name, "schema": {...}}
_dynamic_tools: dict[str, dict] = {}
_tools_dirty = False
_pool_log_dir = Path(__file__).parent / "data" / "mcp_pool_logs"
_pool_log_dir.mkdir(parents=True, exist_ok=True)
_rpc_threadpool = ThreadPoolExecutor(max_workers=4)
_pool_id_counter = 0


def _next_pool_id() -> int:
    global _pool_id_counter
    _pool_id_counter += 1
    return _pool_id_counter


def _pool_log(name: str, line: str) -> None:
    try:
        with open(_pool_log_dir / f"{name}.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
    except Exception:
        pass


class _MCPRemoteError(Exception):
    """子进程正常返回了 MCP 协议级 error (如参数校验失败) — 业务错误, 子进程仍存活, 不应触发自动睡眠"""


def _rpc_call(proc: subprocess.Popen, method: str, params: dict, timeout: float = 15.0) -> dict:
    """向子进程发送 JSON-RPC 请求并同步等待响应 (线程池实现超时)

    子进程可能在正式响应前先输出无 id 的 JSON-RPC 通知 (如 ghidra-mcp
    动态注册工具后发出的 notifications/tools/list_changed)。此类通知行会被
    循环跳过, 直到读到 id 匹配 req_id 的正式响应, 避免误判为响应不匹配。
    """
    req_id = _next_pool_id()
    payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()

    def _read_until_response():
        while True:
            line = proc.stdout.readline()
            if not line:
                return None  # EOF
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                return ("__nonjson__", line)
            if "id" in msg:
                return msg
            # 无 id 字段 → JSON-RPC 通知 (如 tools/list_changed), 跳过继续读

    future = _rpc_threadpool.submit(_read_until_response)
    try:
        resp = future.result(timeout=timeout)
    except Exception:
        future.cancel()
        raise TimeoutError(f"子进程 {method} 响应超时 ({timeout}s)")
    if resp is None:
        raise ConnectionError("子进程已退出 (EOF)")
    if isinstance(resp, tuple) and resp and resp[0] == "__nonjson__":
        raise ConnectionError(f"子进程返回非 JSON: {resp[1][:200]}")
    if resp.get("id") != req_id:
        raise ConnectionError(f"响应 id 不匹配: 期望 {req_id}, 实际 {resp.get('id')}")
    if "error" in resp:
        raise _MCPRemoteError(f"{resp['error']}")
    return resp.get("result", {})


def _send_notification(proc: subprocess.Popen, method: str, params: dict) -> None:
    """向子进程发送 JSON-RPC 通知 (无 id, 服务器不回复) — 只写不读"""
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()


def _spawn_server(name: str, cfg: dict) -> subprocess.Popen:
    env = dict(os.environ)
    # ${VAR} 占位符展开 (如 ${DEEPSEEK_API_KEY} / ${GITHUB_PERSONAL_ACCESS_TOKEN})
    env.update({k: os.path.expandvars(v) for k, v in cfg.get("env", {}).items()})
    cmd_list = [cfg["command"]] + list(cfg.get("args", []))
    # Windows 下 .cmd/.bat 无法直接被 Popen 执行 (shell=False), 需经 cmd /c 包装
    if os.name == "nt" and cmd_list[0].lower().endswith((".cmd", ".bat")):
        cmd_list = ["cmd", "/c"] + cmd_list
    proc = subprocess.Popen(
        cmd_list,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",  # 子进程输出非 UTF-8 字节时替换而非崩溃
        env=env,
    )

    # stderr 排空线程, 防止管道缓冲阻塞子进程
    def _drain_stderr():
        try:
            for line in proc.stderr:
                _pool_log(name, line.rstrip())
        except Exception:
            pass

    threading.Thread(target=_drain_stderr, daemon=True).start()
    return proc


def _kill_server(proc: subprocess.Popen) -> None:
    """终止子进程 (Windows 优先 taskkill /F /T)"""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _mark_tools_dirty() -> None:
    global _tools_dirty
    _tools_dirty = True


def _wake_server(name: str) -> dict:
    if name not in MCP_SLEEP_POOL:
        return {"ok": False, "error": f"休眠池中没有 {name}, 可用: {sorted(MCP_SLEEP_POOL)}"}
    if name in _active_servers:
        # 幂等: 已唤醒 → 直接返回当前状态, 不重复 spawn
        return {"ok": True, "alreadyAwake": True, "server": name,
                "tools": len(_active_servers[name]["tools"])}

    cfg = MCP_SLEEP_POOL[name]
    tool_prefix = cfg.get("tool_prefix", "")
    try:
        proc = _spawn_server(name, cfg)
        # 握手: initialize → notifications/initialized → tools/list
        # 标准 MCP SDK 服务器 (duckdb/github 等) 严格要求 initialize 带完整 params
        _rpc_call(proc, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "deepcode-cerebellum-gateway", "version": "1.0.0"},
        }, timeout=20)
        _send_notification(proc, "notifications/initialized", {})
        result = _rpc_call(proc, "tools/list", {}, timeout=20)
    except Exception as e:
        if "proc" in locals() and proc.poll() is None:
            _kill_server(proc)
        return {"ok": False, "error": f"{name} 唤醒失败: {e}"}

    tools = result.get("tools", [])
    registered = {}   # base_name -> schema (子进程工具名剥离 tool_prefix 后)
    wire_map = {}     # base_name -> 子进程原始工具名 (转发 tools/call 时使用)
    for t in tools:
        tname = t.get("name")
        if not tname:
            continue
        base_name = tname[len(tool_prefix):] if tool_prefix and tname.startswith(tool_prefix) else tname
        registered[base_name] = {
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
        }
        wire_map[base_name] = tname
    _active_servers[name] = {"proc": proc, "tools": registered}
    _dynamic_tools.update({
        f"{name}__{bn}": {"server": name, "wire_name": wire_map[bn], "schema": ts}
        for bn, ts in registered.items()
    })
    _mark_tools_dirty()
    _pool_log(name, f"[gateway] 唤醒成功, 注册 {len(registered)} 个工具")
    return {
        "ok": True, "server": name, "tools": len(registered),
        "toolCount": len(registered),
        "toolNames": sorted(f"{name}__{bn}" for bn in registered),
    }


def _sleep_server(name: str) -> dict:
    if name not in _active_servers:
        return {"ok": False, "error": f"{name} 未唤醒"}
    entry = _active_servers.pop(name)
    removed = [tn for tn in _dynamic_tools if tn.startswith(f"{name}__")]
    for tn in removed:
        _dynamic_tools.pop(tn, None)
    _kill_server(entry["proc"])
    _mark_tools_dirty()
    _pool_log(name, f"[gateway] 睡眠, 注销 {len(removed)} 个工具")
    return {"ok": True, "server": name, "removedTools": len(removed), "toolNames": removed}


def _pool_status() -> dict:
    rows = []
    for name, cfg in MCP_SLEEP_POOL.items():
        active = _active_servers.get(name)
        rows.append({
            "server": name,
            "state": "awake" if active else "sleeping",
            "description": cfg["description"],
            "tools": len(active["tools"]) if active else 0,
            "toolNames": sorted(f"{name}__{tn}" for tn in (active["tools"] if active else {})),
        })
    return {"ok": True, "pool": rows}


def _call_dynamic_tool(name: str, args: dict) -> dict:
    """将工具调用转发给已唤醒的子进程"""
    entry = _dynamic_tools.get(name)
    if not entry:
        return {"ok": False, "error": f"动态工具 {name} 未注册"}
    server_name = entry["server"]
    active = _active_servers.get(server_name)
    if not active:
        return {"ok": False, "error": f"{server_name} 已睡眠, 工具不可用 (请先 cerebellum_mcp_wake)"}
    # 还原子进程原始工具名 (wire_name 在唤醒时记录, 可能含子进程自带前缀)
    raw_name = entry.get("wire_name") or name[len(server_name) + 2:]
    try:
        result = _rpc_call(active["proc"], "tools/call", {"name": raw_name, "arguments": args}, timeout=120.0)
    except _MCPRemoteError as e:
        # MCP 协议级 error (如参数校验失败) — 业务错误, 子进程仍存活, 不睡眠
        return {"ok": False, "error": f"{server_name} 工具调用被拒绝: {e}"}
    except Exception as e:
        # 通信故障 (超时/EOF/非 JSON) → 子进程不可用, 自动睡眠释放资源
        _sleep_server(server_name)
        return {"ok": False, "error": f"{server_name} 工具调用失败: {e} (已自动睡眠)"}
    content = result.get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    text = "\n".join(texts)
    try:
        return json.loads(text) if text else {"ok": True, "empty": True}
    except json.JSONDecodeError:
        return {"ok": True, "text": text}


def _call(name: str, args: dict) -> dict:
    """分发工具调用"""
    if name == "cerebellum_overview":
        return overview()
    if name == "cerebellum_ollama_status":
        return ollama_status()
    if name == "cerebellum_settings_snapshot":
        return settings_snapshot(args.get("scope", "all"))
    if name == "cerebellum_settings_latest":
        return settings_latest(args.get("scope", "project"))
    if name == "cerebellum_settings_search":
        return settings_search(args["query"])
    if name == "cerebellum_memory_save":
        return CerebellumMemory().save(args["key"], args["value"], args.get("tags"))
    if name == "cerebellum_memory_load":
        return CerebellumMemory().load(args["key"])
    if name == "cerebellum_memory_search":
        return CerebellumMemory().search(args["query"], limit=args.get("limit", 10))
    if name == "cerebellum_memory_list":
        return {"ok": True, "keys": CerebellumMemory().list()}
    if name == "cerebellum_experience_record":
        return experience_record(args["task"])
    if name == "cerebellum_experience_search":
        return experience_search(args["query"])
    if name == "cerebellum_consolidated_search":
        return consolidated_search(args["query"],
                                   kind=args.get("kind"),
                                   limit=args.get("limit", 5),
                                   as_of=args.get("as_of"),
                                   strict=bool(args.get("strict", False)))
    if name == "cerebellum_session_summarize":
        return session_summarize(args.get("context", ""), args.get("session_id", "adhoc"))
    if name == "cerebellum_session_recent":
        return session_recent(limit=args.get("limit", 5))
    if name == "cerebellum_index_vault":
        return index_vault()
    if name == "cerebellum_settings_analyze":
        return settings_analyze(args.get("scope", "project"))
    if name == "cerebellum_settings_analyses_history":
        return settings_analyses_history(args.get("scope", "project"),
                                         limit=args.get("limit", 5))
    if name == "cerebellum_experience_graph":
        return experience_graph(
            min_similarity=float(args.get("min_similarity", 0.5)),
            rebuild=bool(args.get("rebuild", False)))
    if name == "cerebellum_experience_graph_query":
        return experience_graph_query(args["query"])
    # ── 记忆进化引擎 (Dreaming / 反馈闭环 / Skill 进化信号 / 评测) ──
    if name == "cerebellum_dreaming_run":
        return dreaming_run(kind=args.get("kind", "session"),
                            use_llm=bool(args.get("use_llm", True)))
    if name == "cerebellum_dreaming_history":
        return dreaming_history(limit=args.get("limit", 5))
    if name == "cerebellum_feedback_add":
        return feedback_add(args["target_type"], args["target_key"], args["rating"],
                            source=args.get("source", "explicit"),
                            comment=args.get("comment", ""))
    if name == "cerebellum_feedback_list":
        return feedback_list(target_type=args.get("target_type"),
                             target_key=args.get("target_key"),
                             limit=args.get("limit", 20))
    if name == "cerebellum_skill_signal_add":
        return skill_signal_add(args["skill_name"],
                                signal_type=args.get("signal_type", "failure"),
                                context=args.get("context", ""),
                                error=args.get("error", ""))
    if name == "cerebellum_skill_signals_list":
        return skill_signals_list(skill_name=args.get("skill_name"),
                                  limit=args.get("limit", 20))
    if name == "cerebellum_skill_evolution_propose":
        return skill_evolution_propose(args["skill_name"],
                                       use_llm=bool(args.get("use_llm", True)))
    if name == "cerebellum_skill_evolution_list":
        return skill_evolution_list(status=args.get("status"))
    if name == "cerebellum_skill_evolution_apply":
        return skill_evolution_apply(args["proposal_id"])
    if name == "cerebellum_skill_evolution_reject":
        return skill_evolution_reject(args["proposal_id"])
    if name == "cerebellum_benchmark_run":
        return benchmark_run(top_k=args.get("top_k", 5))
    if name == "cerebellum_benchmark_list":
        return benchmark_list(limit=args.get("limit", 5))
    if name == "cerebellum_learning_loop_detect":
        return learning_loop_detect(min_priority=args.get("min_priority", 0),
                                    limit=args.get("limit", 50),
                                    persist=bool(args.get("persist", False)))
    if name == "cerebellum_learning_loop_save":
        return learning_loop_save(candidate=args.get("candidate"))
    if name == "cerebellum_learning_loop_list":
        return learning_loop_list(status=args.get("status"),
                                  limit=args.get("limit", 50))
    # ── MCP 网关 (按需唤醒/睡眠) ──
    if name == "cerebellum_mcp_list":
        return _pool_status()
    if name == "cerebellum_mcp_wake":
        return _wake_server(args.get("name", ""))
    if name == "cerebellum_mcp_sleep":
        return _sleep_server(args.get("name", ""))
    # ── 动态工具 (已唤醒的托管 MCP server) ──
    if name in _dynamic_tools:
        return _call_dynamic_tool(name, args)
    return {"ok": False, "error": f"未知工具: {name}"}


# ═══════════════════════════════════════════
# stdio JSON-RPC 2.0 循环
# ═══════════════════════════════════════════

def run_mcp() -> None:
    global _tools_dirty  # 循环末尾会读写该标志
    # Windows 下强制 UTF-8, 避免中文参数 surrogate 崩溃 (同 deepcode-agent 修复)
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        rid = req.get("id", None)

        if method == "initialize":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "deepcode-cerebellum", "version": "1.0.0"},
                },
            }), flush=True)

        elif method == "tools/list":
            tools = [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]}
                     for k, v in TOOLS.items()]
            tools += [{"name": k, "description": v["schema"].get("description", ""),
                       "inputSchema": v["schema"].get("inputSchema") or {"type": "object", "properties": {}}}
                      for k, v in _dynamic_tools.items()]
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}),
                  flush=True)

        elif method == "tools/call":
            name = req.get("params", {}).get("name", "")
            args = req.get("params", {}).get("arguments", {}) or {}
            try:
                result = _call(name, args)
                content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                out = {"jsonrpc": "2.0", "id": rid, "result": {"content": content}}
            except Exception as e:
                out = {"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32000, "message": f"{type(e).__name__}: {e}",
                                 "data": traceback.format_exc()[-2000:]}}
            print(json.dumps(out, ensure_ascii=False), flush=True)

        elif method == "notifications/initialized":
            pass  # 无操作
        elif method == "ping":
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}), flush=True)

        # 工具列表变更 → 通知 CLI 热刷新 (响应之后发送, 保证顺序)
        if _tools_dirty:
            _tools_dirty = False
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}}),
                  flush=True)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="cerebellum_mcp_server")
    ap.add_argument("--mcp", action="store_true", help="MCP stdio 模式")
    ap.add_argument("cmd", nargs="?", default=None,
                    help="CLI 命令: overview/settings_snapshot/settings_latest/settings_search/memory_save/memory_search/experience_record/session_summarize/index_vault")
    args = ap.parse_args()

    if args.mcp:
        run_mcp()
        return 0
    if args.cmd:
        # 代理到 core CLI
        from cerebellum_core import main as core_main
        sys.argv = [sys.argv[0], args.cmd] + sys.argv[3:]
        core_main()
        return 0
    print(json.dumps(overview(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
