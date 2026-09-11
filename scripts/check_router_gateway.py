#!/usr/bin/env python3
"""Router 网关健康检查脚本。

请求 http://127.0.0.1:8090/healthz 判断网关是否存活：
存活则打印状态与响应耗时并退出 0，失败则打印警告与重启命令并退出 1。
"""

import sys
import time
import urllib.request
import urllib.error

# 健康检查目标地址
HEALTHZ_URL = "http://127.0.0.1:8090/healthz"
# 失败时的重启命令
RESTART_CMD = "cd /f/DEEPCODE/core/mcp_servers && python3 router_mcp_gateway.py"


def check_health() -> int:
    """执行健康检查，返回进程退出码（0 表示存活，1 表示失败）。"""
    try:
        # 记录请求开始时间，用于计算响应耗时
        start = time.monotonic()
        with urllib.request.urlopen(HEALTHZ_URL, timeout=5) as resp:
            status = resp.status
        elapsed_ms = (time.monotonic() - start) * 1000
        print(f"[OK] Router 网关存活，状态码 {status}，响应耗时 {elapsed_ms:.1f} ms")
        return 0
    except urllib.error.URLError as exc:
        # 连接或 HTTP 层错误（含超时、拒绝连接）
        print(f"[WARN] Router 网关健康检查失败: {exc}")
    except Exception as exc:  # 兜底捕获其余异常，避免脚本崩溃
        print(f"[WARN] Router 网关健康检查异常: {exc}")

    # 打印重启命令并返回失败退出码
    print(f"[WARN] 请重启网关: {RESTART_CMD}")
    return 1


if __name__ == "__main__":
    sys.exit(check_health())
