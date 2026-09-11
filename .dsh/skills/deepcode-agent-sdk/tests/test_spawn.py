"""spawn_deepcode 工具测试 — mock subprocess，不真派生进程"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, r"F:\DEEPCODE\.deepcode\skills\deepcode-agent-sdk")
os.environ["DEEPSEEK_API_KEY"] = "test-key"

DB = os.path.join(tempfile.gettempdir(), "agent_sdk_test_spawn.db")
WORKER = os.path.join(tempfile.gettempdir(), "agent_sdk_test_worker")
if os.path.exists(DB):
    os.remove(DB)

from agent_sdk_server import DeepCodeAgent  # noqa: E402

results = {}


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def main():
    # T1: 未授权 → 拒绝，不启动进程
    os.environ["DEEPCODE_AGENT_ALLOW_SPAWN"] = "0"
    os.environ["DEEPCODE_AGENT_DEPTH"] = "0"
    a1 = DeepCodeAgent(workspace=r"F:\DEEPCODE", db_path=DB)
    with patch("subprocess.run") as mock_run:
        r = await a1.execute("spawn_deepcode", {"prompt": "任务"})
    results["T1_denied"] = "未授权" in str(r["result"])
    results["T1_no_process"] = not mock_run.called

    # T2: 深度封顶 (depth=3) → 拒绝，不启动进程
    os.environ["DEEPCODE_AGENT_ALLOW_SPAWN"] = "1"
    os.environ["DEEPCODE_AGENT_DEPTH"] = "3"
    a2 = DeepCodeAgent(workspace=r"F:\DEEPCODE", db_path=DB)
    with patch("subprocess.run") as mock_run:
        r = await a2.execute("spawn_deepcode", {"prompt": "任务"})
    results["T2_depth_capped"] = "最大派生深度" in str(r["result"])
    results["T2_no_process"] = not mock_run.called

    # T3: 授权 + 深度1 → 正常 spawn，验证命令构造与环境传播
    os.environ["DEEPCODE_AGENT_ALLOW_SPAWN"] = "1"
    os.environ["DEEPCODE_AGENT_DEPTH"] = "1"
    a3 = DeepCodeAgent(workspace=r"F:\DEEPCODE", db_path=DB)
    fake_out = '{"type":"agent","msg":{"type":"task_complete","stop_reason":"completed"}}\n'
    fake = FakeCompleted(0, fake_out)
    with patch("subprocess.run", return_value=fake) as mock_run:
        r = await a3.execute("spawn_deepcode", {
            "prompt": "写个测试", "workspace": WORKER,
            "max_iterations": 5, "allow_spawn": True,
        })
    res = r["result"]
    results["T3_exit_code"] = res.get("exit_code")
    results["T3_summary_has_complete"] = "task_complete" in res.get("summary", "")
    results["T3_depth_2"] = res.get("depth") == 2
    results["T3_workspace"] = res.get("workspace") == WORKER
    results["T3_output_truncated_ok"] = "task_complete" in res.get("output", "")

    cmd = mock_run.call_args.args[0]
    results["T3_cmd_has_exec_cli"] = any("cli.exec_cli" in c for c in cmd)
    results["T3_cmd_has_json"] = "--json" in cmd
    results["T3_cmd_has_ws"] = WORKER in cmd
    results["T3_cmd_has_iter"] = "5" in cmd
    kw = mock_run.call_args.kwargs
    results["T3_env_depth"] = kw["env"].get("DEEPCODE_AGENT_DEPTH") == "2"
    results["T3_env_allow"] = kw["env"].get("DEEPCODE_AGENT_ALLOW_SPAWN") == "1"
    results["T3_shell_false"] = kw.get("shell") is False
    results["T3_cwd"] = kw.get("cwd") == "F:/DEEPCODE"

    # T3b: allow_spawn=False → 子进程 env 禁止再派生
    with patch("subprocess.run", return_value=fake) as mock_run:
        await a3.execute("spawn_deepcode", {"prompt": "任务", "workspace": WORKER})
    results["T3b_worker_cannot_spawn"] = mock_run.call_args.kwargs["env"].get("DEEPCODE_AGENT_ALLOW_SPAWN") == "0"

    # T4: 摘要提取 — 无 task_complete 时返回空
    results["T4_extract_empty"] = a3._extract_task_summary("line1\nline2") == ""

    # T5: 超时 → 返回错误
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 300)):
        r = await a3.execute("spawn_deepcode", {"prompt": "任务"})
    results["T5_timeout_error"] = "超时" in str(r["result"])

    # T6: 启动失败 → 返回错误
    with patch("subprocess.run", side_effect=FileNotFoundError("no cli")):
        r = await a3.execute("spawn_deepcode", {"prompt": "任务"})
    results["T6_start_fail"] = "启动失败" in str(r["result"])


asyncio.run(main())
print("TEST_SPAWN_JSON_START")
print(json.dumps(results, ensure_ascii=False, indent=2))
