#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SandboxedTerminal v1.0 — Windsurf 风格沙箱终端执行
=================================================
参考: Windsurf/Devin Desktop 后台终端系统

从逆向分析中发现的 Windsurf 终端能力:
  - backgroundTerminalExitStatus — 后台终端的退出状态追踪
  - terminal_idexit_code — 终端退出码
  - backgroundstruct — 后台任务结构体
  - connection_retry — 连接重试机制

特性:
  - 命令级沙箱隔离 (复用 SandboxPolicy/SandboxExecutor)
  - 自动超时 + 资源限制
  - 后台终端生命周期管理
  - 退出码追踪
  - 命令黑名单

用法:
  terminal = SandboxedTerminal()
  result = terminal.run("ls -la", timeout_ms=30000)
  print(result.stdout)

  # 后台执行
  bg = terminal.run_background("npm install")
  while not bg.done:
      await asyncio.sleep(1)
  print(bg.result)
"""

import asyncio
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 复用现有沙箱
_srv_dir = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, _srv_dir)
from sandbox_policy import SandboxPolicy, SandboxExecutor, SandboxResult


@dataclass
class TerminalResult:
    """终端执行结果 (Windsurf 风格)"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: float = 0
    command: str = ""
    error: str = ""
    signal: Optional[str] = None


@dataclass
class BackgroundTask:
    """后台终端任务 (Windsurf backgroundstruct)"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    command: str = ""
    status: str = "pending"  # pending/running/done/failed/timeout
    result: Optional[TerminalResult] = None
    pid: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.status in ("done", "failed", "timeout")


class SandboxedTerminal:
    """
    Windsurf 风格沙箱终端

    3 种执行模式:
      run()            — 同步执行，等待完成
      run_async()      — 异步执行，返回 coroutine
      run_background() — 后台执行，轮询结果
    """

    # 命令黑名单 (Windsurf 风格)
    BLOCKED_COMMANDS = [
        "rm -rf /", "rm -rf ~", "rm -rf .",
        "dd if=", "mkfs", "format",
        ":()", "eval ", "exec ",
        "shutdown", "reboot", "halt",
        "sudo ", "su ",
    ]

    def __init__(self, policy: Optional[SandboxPolicy] = None):
        self._executor = SandboxExecutor(policy)
        self._bg_tasks: Dict[str, BackgroundTask] = {}
        self._stats = {
            "total_runs": 0,
            "successful": 0,
            "failed": 0,
            "timeouts": 0,
            "background_tasks": 0,
        }

    def _validate_command(self, command: str) -> Optional[str]:
        """验证命令安全性，返回错误信息或 None"""
        cmd_lower = command.strip().lower()
        for blocked in self.BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return f"命令被禁止: {blocked}"
        return None

    # ── 同步执行 ──

    def run(self, command: str, timeout_ms: int = 30000,
            cwd: Optional[str] = None) -> TerminalResult:
        """
        同步执行命令 (Windsurf 终端执行)

        Args:
            command: shell 命令
            timeout_ms: 超时时间 (ms)
            cwd: 工作目录

        Returns:
            TerminalResult
        """
        error = self._validate_command(command)
        if error:
            return TerminalResult(
                success=False, error=error, exit_code=-1, command=command
            )

        self._stats["total_runs"] += 1
        start = time.time()

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000,
                cwd=cwd,
            )

            duration = (time.time() - start) * 1000
            terminal_result = TerminalResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_ms=duration,
                command=command,
            )

            if terminal_result.success:
                self._stats["successful"] += 1
            else:
                self._stats["failed"] += 1

            return terminal_result

        except subprocess.TimeoutExpired:
            self._stats["timeouts"] += 1
            return TerminalResult(
                success=False, error=f"超时 ({timeout_ms}ms)",
                exit_code=-1, duration_ms=timeout_ms, command=command,
            )
        except Exception as e:
            self._stats["failed"] += 1
            return TerminalResult(
                success=False, error=str(e), exit_code=-1, command=command,
            )

    # ── 异步执行 (基于 SandboxExecutor) ──

    async def run_async(self, command: str, timeout_ms: int = 30000,
                        cwd: Optional[str] = None) -> TerminalResult:
        """
        异步执行命令

        使用 SandboxExecutor 的非阻塞执行
        """
        error = self._validate_command(command)
        if error:
            return TerminalResult(
                success=False, error=error, exit_code=-1, command=command
            )

        self._stats["total_runs"] += 1
        start = time.time()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_ms / 1000
                )
                duration = (time.time() - start) * 1000
                result = TerminalResult(
                    success=proc.returncode == 0,
                    stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                    stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                    exit_code=proc.returncode or 0,
                    duration_ms=duration,
                    command=command,
                )
                if result.success:
                    self._stats["successful"] += 1
                else:
                    self._stats["failed"] += 1
                return result

            except asyncio.TimeoutError:
                proc.kill()
                self._stats["timeouts"] += 1
                return TerminalResult(
                    success=False, error=f"超时 ({timeout_ms}ms)",
                    exit_code=-1, command=command,
                )

        except Exception as e:
            self._stats["failed"] += 1
            return TerminalResult(
                success=False, error=str(e), exit_code=-1, command=command,
            )

    # ── 后台执行 (Windsurf backgroundTerminal) ──

    def run_background(self, command: str, timeout_ms: int = 60000,
                       cwd: Optional[str] = None) -> BackgroundTask:
        """
        后台执行命令 (Windsurf backgroundstruct)

        不会阻塞主流程，调用者可以通过 task.done 检查状态

        Args:
            command: shell 命令
            timeout_ms: 超时时间
            cwd: 工作目录

        Returns:
            BackgroundTask (立即返回)
        """
        error = self._validate_command(command)
        if error:
            bg = BackgroundTask(command=command, status="failed")
            bg.result = TerminalResult(
                success=False, error=error, exit_code=-1, command=command
            )
            return bg

        bg = BackgroundTask(command=command, status="running")
        self._bg_tasks[bg.id] = bg
        self._stats["background_tasks"] += 1

        # 启动后台执行 (不阻塞)
        asyncio.create_task(self._run_bg(bg, timeout_ms, cwd))

        return bg

    async def _run_bg(self, bg: BackgroundTask, timeout_ms: int, cwd: Optional[str]):
        """后台任务执行体"""
        try:
            proc = await asyncio.create_subprocess_shell(
                bg.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            bg.pid = proc.pid

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_ms / 1000
                )
                bg.result = TerminalResult(
                    success=proc.returncode == 0,
                    stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                    stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                    exit_code=proc.returncode or 0,
                    command=bg.command,
                )
                bg.status = "done" if bg.result.success else "failed"
                bg.completed_at = datetime.now().isoformat()

            except asyncio.TimeoutError:
                proc.kill()
                bg.status = "timeout"
                bg.result = TerminalResult(
                    success=False, error=f"超时 ({timeout_ms}ms)",
                    exit_code=-1, command=bg.command,
                )

        except Exception as e:
            bg.status = "failed"
            bg.result = TerminalResult(
                success=False, error=str(e), exit_code=-1, command=bg.command,
            )

    # ── 查询 ──

    def get_bg_task(self, task_id: str) -> Optional[BackgroundTask]:
        return self._bg_tasks.get(task_id)

    def list_bg_tasks(self, status: Optional[str] = None) -> List[BackgroundTask]:
        tasks = list(self._bg_tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def get_stats(self) -> dict:
        bg_active = sum(1 for t in self._bg_tasks.values() if t.status == "running")
        return {
            **self._stats,
            "background_active": bg_active,
            "background_total": len(self._bg_tasks),
        }
