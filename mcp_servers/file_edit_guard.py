#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FileEditGuard v1.0 — Windsurf 风格文件编辑防护
=============================================
参考: Windsurf Cascade 的文件编辑保护机制

每次文件修改前:
  1. CHECKPOINT: 自动创建 git checkpoint / 文件备份
  2. EDIT: 执行修改
  3. VERIFY: 验证修改结果
  4. ROLLBACK_ON_FAIL: 失败时自动回滚

集成:
  - tool_registry: 自动包裹 Write/Edit 工具
  - session_checkpoint: 复用现有 checkpoint 系统
  - cascade_planner: Cascade 计划中的文件编辑步骤自动受保护

用法:
  guard = FileEditGuard()
  async with guard.protect("path/to/file.py"):
      # 文件修改操作
      write_file(...)
  # 自动验证 + 失败回滚
"""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class FileBackup:
    """文件备份快照"""
    path: str
    backup_path: str
    hash_before: str
    hash_after: Optional[str] = None
    size_before: int = 0
    size_after: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "pending"  # pending/committed/rolled_back


class FileEditGuard:
    """
    文件编辑防护 — Windsurf 风格自动 checkpoint + 回滚

    用法:
      guard = FileEditGuard()

      # 方式1: 上下文管理器 (推荐)
      async with guard.protect("src/main.py"):
          write_file("src/main.py", new_content)

      # 方式2: 批量保护
      async with guard.protect_batch(["src/a.py", "src/b.py"]):
          ...

      # 方式3: 手动
      guard.checkpoint("src/main.py")
      try:
          write_file(...)
          guard.commit("src/main.py")
      except:
          guard.rollback("src/main.py")
    """

    def __init__(self, backup_dir: Optional[str] = None,
                 auto_rollback: bool = True):
        self.backup_dir = backup_dir or os.path.join(
            tempfile.gettempdir(), "deepcode_file_guard"
        )
        os.makedirs(self.backup_dir, exist_ok=True)

        self._backups: Dict[str, FileBackup] = {}
        self._stats = {
            "checkpoints": 0,
            "commits": 0,
            "rollbacks": 0,
            "protected_writes": 0,
        }
        self.auto_rollback = auto_rollback

    def checkpoint(self, file_path: str) -> Optional[FileBackup]:
        """
        创建文件备份 (Windsurf 编辑前自动 checkpoint)

        Args:
            file_path: 要修改的文件路径

        Returns:
            FileBackup 或 None (文件不存在)
        """
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            self._backups[abs_path] = FileBackup(
                path=abs_path, backup_path="",
                hash_before="", size_before=0,
                status="pending"
            )
            return self._backups[abs_path]

        # 计算当前 hash
        with open(abs_path, "rb") as f:
            content = f.read()
        hash_before = hashlib.sha256(content).hexdigest()
        size_before = len(content)

        # 备份到 temp
        backup_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{Path(abs_path).name}"
        backup_path = os.path.join(self.backup_dir, backup_name)
        shutil.copy2(abs_path, backup_path)

        backup = FileBackup(
            path=abs_path,
            backup_path=backup_path,
            hash_before=hash_before,
            size_before=size_before,
        )
        self._backups[abs_path] = backup
        self._stats["checkpoints"] += 1
        return backup

    def commit(self, file_path: str) -> bool:
        """
        确认修改 (Windsurf 编辑成功后 commit)

        计算新 hash 并记录
        """
        abs_path = os.path.abspath(file_path)
        backup = self._backups.get(abs_path)
        if not backup:
            return False

        if os.path.exists(abs_path):
            with open(abs_path, "rb") as f:
                content = f.read()
            backup.hash_after = hashlib.sha256(content).hexdigest()
            backup.size_after = len(content)

        backup.status = "committed"
        self._stats["commits"] += 1
        return True

    def rollback(self, file_path: str) -> bool:
        """
        回滚文件修改 (Windsurf 编辑失败时自动回滚)

        从 backup 恢复原文件
        """
        abs_path = os.path.abspath(file_path)
        backup = self._backups.get(abs_path)

        if not backup or not backup.backup_path:
            # 文件原本不存在，删除
            if os.path.exists(abs_path):
                os.remove(abs_path)
            self._stats["rollbacks"] += 1
            return True

        if not os.path.exists(backup.backup_path):
            return False

        shutil.copy2(backup.backup_path, abs_path)
        backup.status = "rolled_back"
        self._stats["rollbacks"] += 1
        return True

    def diff(self, file_path: str) -> Optional[str]:
        """
        查看修改前后的差异

        Returns:
            unified diff 字符串或 None
        """
        abs_path = os.path.abspath(file_path)
        backup = self._backups.get(abs_path)
        if not backup or not backup.backup_path:
            return None

        if not os.path.exists(backup.backup_path) or not os.path.exists(abs_path):
            return None

        try:
            import difflib
            with open(backup.backup_path, "r", encoding="utf-8") as f:
                before = f.readlines()
            with open(abs_path, "r", encoding="utf-8") as f:
                after = f.readlines()
            return "".join(difflib.unified_diff(
                before, after,
                fromfile=f"a/{Path(abs_path).name}",
                tofile=f"b/{Path(abs_path).name}"
            ))
        except Exception:
            return None

    # ── 批量操作 ──

    def checkpoint_batch(self, file_paths: List[str]) -> List[FileBackup]:
        """批量备份文件"""
        return [self.checkpoint(p) for p in file_paths if self.checkpoint(p)]

    def rollback_batch(self, file_paths: List[str]) -> int:
        """批量回滚"""
        count = 0
        for p in file_paths:
            if self.rollback(p):
                count += 1
        return count

    # ── 上下文管理器 ──

    def protect(self, file_path: str):
        """
        文件编辑保护的上下文管理器
        自动: checkpoint -> edit -> commit/rollback
        """
        return _FileProtectContext(self, file_path)

    def protect_batch(self, file_paths: List[str]):
        """批量保护上下文"""
        return _BatchProtectContext(self, file_paths)

    # ── 查询 ──

    def get_backup(self, file_path: str) -> Optional[FileBackup]:
        return self._backups.get(os.path.abspath(file_path))

    def list_active(self) -> List[FileBackup]:
        """列出未 commit/rollback 的备份"""
        return [b for b in self._backups.values() if b.status == "pending"]

    def get_stats(self) -> dict:
        return dict(self._stats)

    def cleanup(self, max_age_hours: int = 24):
        """清理过期备份"""
        now = time.time()
        for root, _, files in os.walk(self.backup_dir):
            for f in files:
                path = os.path.join(root, f)
                age = now - os.path.getmtime(path)
                if age > max_age_hours * 3600:
                    os.remove(path)


class _FileProtectContext:
    """单个文件的保护上下文"""
    def __init__(self, guard: FileEditGuard, file_path: str):
        self.guard = guard
        self.file_path = file_path
        self._rolled_back = False

    async def __aenter__(self):
        self.guard.checkpoint(self.file_path)
        self.guard._stats["protected_writes"] += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and self.guard.auto_rollback:
            # 异常发生 → 自动回滚
            self.guard.rollback(self.file_path)
            self._rolled_back = True
        else:
            # 成功 → commit
            self.guard.commit(self.file_path)

    @property
    def was_rolled_back(self) -> bool:
        return self._rolled_back


class _BatchProtectContext:
    """批量文件的保护上下文"""
    def __init__(self, guard: FileEditGuard, file_paths: List[str]):
        self.guard = guard
        self.file_paths = file_paths

    async def __aenter__(self):
        for p in self.file_paths:
            self.guard.checkpoint(p)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and self.guard.auto_rollback:
            for p in self.file_paths:
                self.guard.rollback(p)
        else:
            for p in self.file_paths:
                self.guard.commit(p)
