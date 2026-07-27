#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ShadowWorkspace v1.0 — Cursor 风格的隔离影子工作区
=================================================
参考: Cursor shadow-workspace extension

在完全隔离的临时目录中执行变更操作，验证通过后同步回真实工作区。

用法:
  ws = ShadowWorkspace("/path/to/project")
  async with ws.sandbox() as sandbox:
      # 在影子区操作
      sandbox.write("file.py", "new content")
      sandbox.run("pytest")
  # 自动: 成功→同步, 失败→丢弃
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class SandboxResult:
    """影子工作区操作结果"""
    success: bool
    file_path: str = ""
    error: str = ""
    stdout: str = ""
    stderr: str = ""


class ShadowWorkspace:
    """
    隔离影子工作区 — Cursor shadow-workspace 风格

    每次变更在隔离的 git worktree / temp 目录中进行，
    通过后批量同步回真正的工作区。
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)
        self._ws_name = Path(workspace_path).name
        self._sandboxes: Dict[str, str] = {}
        self._stats = {
            "sandboxes_created": 0,
            "syncs_successful": 0,
            "syncs_failed": 0,
            "discards": 0,
        }

    @property
    def name(self) -> str:
        return self._ws_name

    def _create_sandbox_dir(self) -> str:
        """创建隔离的影子工作区目录"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        sandbox_dir = tempfile.mkdtemp(prefix=f"deepcode_shadow_{self._ws_name}_{ts}_")
        return sandbox_dir

    # ── 公开 API ──

    def create_sandbox(self, strategy: str = "copy") -> str:
        """
        创建影子工作区

        Args:
            strategy: 
              "copy" — 复制整个工作区 (通用, 安全)
              "worktree" — 使用 git worktree (仅git项目, 快速)

        Returns:
            sandbox_id
        """
        import uuid
        sandbox_id = str(uuid.uuid4())[:8]

        if strategy == "worktree" and os.path.exists(
            os.path.join(self.workspace_path, ".git")):
            # 使用 git worktree — 快速且节省空间
            sandbox_dir = tempfile.mkdtemp(prefix=f"deepcode_worktree_{self._ws_name}_")
            try:
                subprocess.run(
                    ["git", "worktree", "add", sandbox_dir],
                    cwd=self.workspace_path,
                    capture_output=True, text=True, check=True, timeout=30
                )
            except Exception:
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                strategy = "copy"  # fallback to copy
            else:
                self._sandboxes[sandbox_id] = sandbox_dir
                self._stats["sandboxes_created"] += 1
                return sandbox_id

        # 默认: 完整复制
        sandbox_dir = self._create_sandbox_dir()
        self._copy_workspace(self.workspace_path, sandbox_dir)
        self._sandboxes[sandbox_id] = sandbox_dir
        self._stats["sandboxes_created"] += 1
        return sandbox_id

    def _copy_workspace(self, src: str, dst: str, exclude_dirs=None):
        """复制工作区到影子目录 (排除大目录)"""
        if exclude_dirs is None:
            exclude_dirs = {".git", "node_modules", "__pycache__",
                           ".venv", "venv", ".deepcode", ".claude",
                           ".playwright-mcp", "target", "build", "dist"}

        def ignore_fn(d, names):
            return [n for n in names if n in exclude_dirs]

        shutil.copytree(src, dst, ignore=ignore_fn,
                        dirs_exist_ok=True, symlinks=False)

    def get_sandbox_path(self, sandbox_id: str) -> Optional[str]:
        return self._sandboxes.get(sandbox_id)

    # ── 同步 / 丢弃 ──

    def sync_to_real(self, sandbox_id: str,
                     file_filter: Callable = None) -> int:
        """
        将影子区的变更同步回真实工作区

        Args:
            sandbox_id: 影子区 ID
            file_filter: 文件过滤回调 (path) -> bool

        Returns:
            同步的文件数量
        """
        sandbox_path = self._sandboxes.get(sandbox_id)
        if not sandbox_path:
            return 0

        synced = 0
        for root, _, files in os.walk(sandbox_path):
            for fname in files:
                src_path = os.path.join(root, fname)
                rel_path = os.path.relpath(src_path, sandbox_path)
                dst_path = os.path.join(self.workspace_path, rel_path)

                if file_filter and not file_filter(rel_path):
                    continue

                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                synced += 1

        self._stats["syncs_successful"] += 1
        return synced

    def discard(self, sandbox_id: str):
        """丢弃影子工作区"""
        sandbox_path = self._sandboxes.pop(sandbox_id, None)
        if sandbox_path and os.path.exists(sandbox_path):
            # 如果是 git worktree, 先移除
            if os.path.exists(os.path.join(sandbox_path, ".git")):
                subprocess.run(
                    ["git", "worktree", "remove", sandbox_path],
                    capture_output=True, text=True, timeout=30
                )
            shutil.rmtree(sandbox_path, ignore_errors=True)
            self._stats["discards"] += 1

    def diff(self, sandbox_id: str) -> Dict[str, str]:
        """
        获取影子区与真实工作区的差异

        Returns:
            {相对路径: diff文本}
        """
        sandbox_path = self._sandboxes.get(sandbox_id)
        if not sandbox_path:
            return {}

        import difflib
        diffs = {}
        for root, _, files in os.walk(sandbox_path):
            for fname in files:
                src_path = os.path.join(root, fname)
                rel = os.path.relpath(src_path, sandbox_path)
                dst_path = os.path.join(self.workspace_path, rel)

                if not os.path.exists(dst_path):
                    diffs[rel] = "(new file)"
                    continue

                try:
                    with open(src_path, encoding="utf-8") as f:
                        src_lines = f.readlines()
                    with open(dst_path, encoding="utf-8") as f:
                        dst_lines = f.readlines()
                except Exception:
                    continue

                diff = "".join(difflib.unified_diff(
                    dst_lines, src_lines,
                    fromfile=f"a/{rel}", tofile=f"b/{rel}"
                ))
                if diff.strip():
                    diffs[rel] = diff

        return diffs

    # ── 上下文管理器 ──

    def sandbox(self, strategy: str = "copy"):
        """上下文管理器 — 自动创建和清理"""
        return _ShadowContext(self, strategy)

    # ── 查询 ──

    def list_sandboxes(self) -> List[Dict]:
        return [{
            "id": sid,
            "path": spath,
            "exists": os.path.exists(spath),
        } for sid, spath in self._sandboxes.items()]

    def get_stats(self) -> dict:
        return dict(self._stats)


class _ShadowContext:
    """影子工作区上下文 — 自动 sync/discard"""
    def __init__(self, ws: ShadowWorkspace, strategy: str):
        self.ws = ws
        self.strategy = strategy
        self.sandbox_id: str = ""
        self.sandbox_path: str = ""

    async def __aenter__(self):
        self.sandbox_id = self.ws.create_sandbox(self.strategy)
        self.sandbox_path = self.ws.get_sandbox_path(self.sandbox_id)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.ws.discard(self.sandbox_id)
        else:
            self.ws.sync_to_real(self.sandbox_id)
            self.ws.discard(self.sandbox_id)

    def write(self, rel_path: str, content: str):
        """在影子区写文件"""
        full = os.path.join(self.sandbox_path, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def read(self, rel_path: str) -> Optional[str]:
        """在影子区读文件"""
        full = os.path.join(self.sandbox_path, rel_path)
        if os.path.exists(full):
            with open(full, encoding="utf-8") as f:
                return f.read()
        return None

    def run(self, cmd: str, timeout_ms: int = 30000) -> subprocess.CompletedProcess:
        """在影子区执行命令"""
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout_ms / 1000, cwd=self.sandbox_path
        )

    def copy_file(self, src_rel: str, dst_rel: str):
        """在影子区内复制文件"""
        src = os.path.join(self.sandbox_path, src_rel)
        dst = os.path.join(self.sandbox_path, dst_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    def delete_file(self, rel_path: str):
        """在影子区内删除文件"""
        full = os.path.join(self.sandbox_path, rel_path)
        if os.path.exists(full):
            os.remove(full)

    def exists(self, rel_path: str) -> bool:
        return os.path.exists(os.path.join(self.sandbox_path, rel_path))

    def diff(self) -> Dict[str, str]:
        """获取影子区差异"""
        return self.ws.diff(self.sandbox_id)
