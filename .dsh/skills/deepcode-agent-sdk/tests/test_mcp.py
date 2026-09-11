"""MCP 协议 + HTTP 依赖快速验证"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, r"F:\DEEPCODE\.deepcode\skills\deepcode-agent-sdk")
os.environ["DEEPSEEK_API_KEY"] = "test-key"

DB = os.path.join(tempfile.gettempdir(), "agent_sdk_test_mcp.db")
if os.path.exists(DB):
    os.remove(DB)

from agent_sdk_server import DeepCodeAgent, HAS_HTTP  # noqa: E402


class FakeUsage:
    prompt_tokens = 5
    completion_tokens = 3


class FakeToolCall:
    id = "call_mcp"
    function = type("F", (), {"name": "list_directory", "arguments": '{"path": "."}'})()


class FakeMsg:
    content = None
    tool_calls = [FakeToolCall()]


class FakeResp:
    def __init__(self):
        self.choices = [type("C", (), {"message": FakeMsg()})()]
        self.usage = FakeUsage()


class FakeClient:
    chat = type("CH", (), {"completions": type("CP", (), {
        "create": staticmethod(lambda **kw: FakeResp())})()})()


results = {}


async def main():
    agent = DeepCodeAgent(workspace=r"F:\DEEPCODE", db_path=DB)

    # MCP tools/list
    r = await agent.handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = r["result"]["tools"]
    results["mcp_tools_count"] = len(tools)
    results["mcp_tools_has_agent_run"] = any("agent_run" in str(t) for t in tools)

    # MCP tools/call → agent_run (多轮)
    r2 = await agent.handle_mcp_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "agent_run",
                   "arguments": {"prompt": "测试", "options": {"llm_client": FakeClient(), "max_turns": 2}}},
    })
    text = json.loads(r2["result"]["content"][0]["text"])
    results["mcp_agent_run_session"] = bool(text.get("session_id"))
    results["mcp_agent_run_result"] = text.get("result", {}).get("subtype")

    # MCP initialize (标准握手)
    r0 = await agent.handle_mcp_request({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1.0"}},
    })
    results["mcp_initialize_ok"] = r0["result"]["serverInfo"]["name"] == "deepcode-agent-sdk"
    results["mcp_initialize_proto"] = r0["result"]["protocolVersion"]

    # MCP notification → 静默 (返回 None 不响应)
    r_notif = await agent.handle_mcp_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    results["mcp_notification_silent"] = r_notif is None

    # MCP ping → 标准空对象
    r3 = await agent.handle_mcp_request({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    results["mcp_ping"] = r3["result"] == {}

    # MCP unknown method
    r4 = await agent.handle_mcp_request({"jsonrpc": "2.0", "id": 4, "method": "nope"})
    results["mcp_unknown_error"] = r4["error"]["code"] == -32601

    # HTTP 依赖
    results["http_available"] = HAS_HTTP


asyncio.run(main())
print("TEST_MCP_RESULT_JSON_START")
print(json.dumps(results, ensure_ascii=False, indent=2))
