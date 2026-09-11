"""agent_sdk_server v2.0 冒烟测试 — mock LLM，不产生真实 API 调用"""
import asyncio
import json
import os
import sys
import tempfile

MODULE_DIR = r"F:\DEEPCODE\.deepcode\skills\deepcode-agent-sdk"
sys.path.insert(0, MODULE_DIR)
os.environ["DEEPSEEK_API_KEY"] = "test-key"

DB_PATH = os.path.join(tempfile.gettempdir(), "agent_sdk_test_v2.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

from agent_sdk_server import (  # noqa: E402
    DeepCodeAgent, AgentOptions, EVENT_INIT, EVENT_DELTA,
    EVENT_TOOL_CALL, EVENT_TOOL_RESULT, EVENT_RESULT,
)


# ── mock LLM (仿 OpenAI SDK 响应结构) ────────────────────────────

class FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class FakeToolCall:
    def __init__(self, name, arguments, tid="call_1"):
        self.id = tid
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeResp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]
        self.usage = FakeUsage()


class FakeCompletions:
    """按脚本序列依次返回响应，超出后重复最后一条"""

    def __init__(self, script):
        self.script = script
        self.calls = 0

    def create(self, **kw):
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return r


class FakeClient:
    def __init__(self, script):
        self.chat = type("CH", (), {"completions": FakeCompletions(script)})()


# ── 测试主体 ──────────────────────────────────────────────────────

results = {}


async def main():
    agent = DeepCodeAgent(workspace=r"F:\DEEPCODE", db_path=DB_PATH)

    # A: 纯文本回复 → init/delta/result(success) 事件序列
    script_a = [FakeResp(FakeMsg(content="你好，我是测试回复"))]
    evs_a = [ev async for ev in agent.runtime.run("测试", AgentOptions(llm_client=FakeClient(script_a), max_turns=3))]
    results["A_sequence"] = [e.type for e in evs_a]
    results["A_result_subtype"] = evs_a[-1].data.get("subtype") if evs_a else None

    # B: 多轮工具调用 → tool_call → tool_result → 最终答案
    script_b = [
        FakeResp(FakeMsg(tool_calls=[FakeToolCall("list_directory", '{"path": "."}')])),
        FakeResp(FakeMsg(content="目录已列出")),
    ]
    evs_b = [ev async for ev in agent.runtime.run("看看目录", AgentOptions(llm_client=FakeClient(script_b), max_turns=3))]
    results["B_sequence"] = [e.type for e in evs_b]
    tc = [e for e in evs_b if e.type == EVENT_TOOL_CALL]
    tr = [e for e in evs_b if e.type == EVENT_TOOL_RESULT]
    results["B_tool_call_name"] = tc[0].data["name"] if tc else None
    results["B_tool_call_allowed"] = tc[0].data.get("allowed") if tc else None
    results["B_tool_result_has_output"] = bool(tr and "output" in tr[0].data)

    # C: 权限拒绝 — default 模式 + write_file → 拒绝
    script_c = [FakeResp(FakeMsg(tool_calls=[FakeToolCall("write_file", '{"path":"x.txt","content":"hi"}')]))]
    evs_c = [ev async for ev in agent.runtime.run("写文件", AgentOptions(llm_client=FakeClient(script_c), max_turns=2))]
    denied = [e for e in evs_c if e.type == EVENT_TOOL_CALL and e.data.get("allowed") is False]
    results["C_write_denied"] = bool(denied)

    # C2: acceptEdits 模式 → 允许写文件
    script_c2 = [FakeResp(FakeMsg(tool_calls=[FakeToolCall("write_file", '{"path":"x.txt","content":"hi"}')]))]
    evs_c2 = [ev async for ev in agent.runtime.run("写文件2", AgentOptions(llm_client=FakeClient(script_c2), max_turns=2, permission_mode="acceptEdits"))]
    allowed = [e for e in evs_c2 if e.type == EVENT_TOOL_CALL and e.data.get("allowed") is True]
    results["C2_write_allowed"] = bool(allowed)

    # D: 预算超限 → error_max_budget_usd
    script_d = [FakeResp(FakeMsg(content="x"))]
    evs_d = [ev async for ev in agent.runtime.run("预算", AgentOptions(llm_client=FakeClient(script_d), max_budget_usd=0.0000001))]
    results["D_budget_subtype"] = evs_d[-1].data.get("subtype") if evs_d else None

    # E: 多轮上限 → error_max_turns (LLM 一直返回 tool_call)
    script_e = [FakeResp(FakeMsg(tool_calls=[FakeToolCall("list_directory", '{"path":"."}')]))] * 10
    evs_e = [ev async for ev in agent.runtime.run("轮次", AgentOptions(llm_client=FakeClient(script_e), max_turns=2))]
    results["E_max_turns_subtype"] = evs_e[-1].data.get("subtype") if evs_e else None

    # F: run_agent 持久化 + 会话管理 + resume 重建
    res = await agent.run_agent("持久化测试", {"llm_client": FakeClient([FakeResp(FakeMsg(content="done"))]), "max_turns": 2})
    sid = res["session_id"]
    evs_f = agent.get_session_events(sid)
    results["F_session_created"] = bool(sid)
    results["F_events_persisted"] = len(evs_f)
    results["F_sessions_listed"] = any(s["id"] == sid for s in agent.list_sessions(50))
    results["F_resume_messages"] = len(agent.resume_session(sid)["messages"])
    results["F_delete"] = agent.store.delete_session(sid)

    # G: 向后兼容 execute / execute_batch
    ex = await agent.execute("agent_status")
    results["G_execute_ok"] = ex["meta"]["status"] == "success"
    batch = await agent.execute_batch([{"tool": "agent_status"}, {"tool": "agent_status"}])
    results["G_batch_ok"] = len(batch) == 2

    # H: allowed_tools / disallowed_tools 过滤
    script_h = [FakeResp(FakeMsg(tool_calls=[FakeToolCall("list_directory", '{"path":"."}')]))]
    evs_h = [ev async for ev in agent.runtime.run("过滤", AgentOptions(
        llm_client=FakeClient(script_h), max_turns=2, allowed_tools=["read_file"]))]
    h_denied = [e for e in evs_h if e.type == EVENT_TOOL_CALL and e.data.get("allowed") is False]
    results["H_allowed_filter_denies"] = bool(h_denied)


asyncio.run(main())
print("TEST_RESULT_JSON_START")
print(json.dumps(results, ensure_ascii=False, indent=2))
