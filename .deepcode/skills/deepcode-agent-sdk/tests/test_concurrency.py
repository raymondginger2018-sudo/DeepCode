"""并发上限验证 — 7 个并发 spawn 请求，实际同时执行不得超过 5"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, r"F:\DEEPCODE\.deepcode\skills\deepcode-agent-sdk")
os.environ["DEEPSEEK_API_KEY"] = "test-key"
os.environ["DEEPCODE_AGENT_ALLOW_SPAWN"] = "1"
os.environ["DEEPCODE_AGENT_DEPTH"] = "0"

DB = os.path.join(tempfile.gettempdir(), "agent_sdk_test_conc.db")
if os.path.exists(DB):
    os.remove(DB)

from agent_sdk_server import DeepCodeAgent  # noqa: E402

results = {}

lock = threading.Lock()
_current = 0
_max_seen = 0
_entered = 0


def fake_run(cmd, **kw):
    """模拟子进程: 同步阻塞 0.15s，统计同时进入的最大数"""
    global _current, _max_seen, _entered
    with lock:
        _current += 1
        _max_seen = max(_max_seen, _current)
        _entered += 1
    time.sleep(0.15)
    with lock:
        _current -= 1
    return type("R", (), {
        "returncode": 0,
        "stdout": '{"id":"1","msg":{"type":"task_complete","stop_reason":"completed"}}',
        "stderr": "",
    })()


async def main():
    agent = DeepCodeAgent(workspace=r"F:\DEEPCODE", db_path=DB)
    ws = os.path.join(tempfile.gettempdir(), "agent_sdk_test_conc_ws")

    with patch("subprocess.run", side_effect=fake_run):
        tasks = [agent.execute("spawn_deepcode", {"prompt": f"任务{i}", "workspace": ws})
                 for i in range(7)]
        results_list = await asyncio.gather(*tasks)

    results["spawned_total"] = _entered
    results["max_concurrent"] = _max_seen
    results["concurrency_capped_at_5"] = _max_seen <= 5
    results["all_succeeded"] = all(
        r["result"].get("exit_code") == 0 for r in results_list)
    results["all_workspace"] = all(
        r["result"].get("workspace") == ws for r in results_list)


asyncio.run(main())
print("TEST_CONCURRENCY_JSON_START")
print(json.dumps(results, ensure_ascii=False, indent=2))
