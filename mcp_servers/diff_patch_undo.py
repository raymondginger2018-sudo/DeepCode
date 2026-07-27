#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiffPatch + UndoRedo — Cursor 风格结构化编辑与操作历史
====================================================
DiffPatch: 基于 Hunks 的结构化文件编辑
UndoRedo:  操作历史栈，支持撤销/重做
"""

import difflib, hashlib, os, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════
# Diff/Patch 系统
# ══════════════════════════════════════════════

@dataclass
class Hunk:
    """差异块 — Cursor Hunk 风格"""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str]

    def apply_to(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        covered = sum(1 for l in self.lines if l[0:1] in (' ', '-'))
        result = lines[:self.old_start - 1]
        for l in self.lines:
            if l.startswith('+'):
                result.append(l[1:] + '\n' if not l[1:].endswith('\n') else l[1:])
            elif l.startswith('-'):
                pass
            else:
                result.append(l[1:] + '\n' if not l[1:].endswith('\n') else l[1:])
        result.extend(lines[self.old_start - 1 + covered:])
        return ''.join(result)


@dataclass
class FileDiff:
    """文件差异 — Cursor FileDiff 风格"""
    path: str
    hunks: List[Hunk] = field(default_factory=list)
    new_file: bool = False
    deleted_file: bool = False

    def apply_to(self, text: str) -> str:
        for h in self.hunks:
            text = h.apply_to(text)
        return text


class Differ:
    """差异生成器"""
    @staticmethod
    def from_text(old: str, new: str, path: str = "") -> FileDiff:
        fd = FileDiff(path=path)
        old_l = old.splitlines(keepends=True)
        new_l = new.splitlines(keepends=True)
        m = difflib.SequenceMatcher(None, old_l, new_l)
        for op, i1, i2, j1, j2 in m.get_opcodes():
            if op == 'equal':
                continue
            lines = []
            for l in old_l[i1:i2]:
                lines.append(f'-{l.rstrip()}')
            for l in new_l[j1:j2]:
                lines.append(f'+{l.rstrip()}')
            fd.hunks.append(Hunk(i1 + 1, i2 - i1, j1 + 1, j2 - j1, lines))
        return fd

    @staticmethod
    def from_file(fpath: str, new_text: str) -> FileDiff:
        if not os.path.exists(fpath):
            fd = FileDiff(path=fpath, new_file=True)
            lines = new_text.splitlines(keepends=True)
            fd.hunks.append(Hunk(0, 0, 1, len(lines), [f'+{l.rstrip()}' for l in lines]))
            return fd
        with open(fpath, encoding='utf-8') as f:
            return Differ.from_text(f.read(), new_text, fpath)


class Patcher:
    """补丁应用器"""
    @staticmethod
    def apply(diff: FileDiff, base_dir: str = "") -> bool:
        path = os.path.join(base_dir, diff.path) if base_dir else diff.path
        if diff.deleted_file:
            if os.path.exists(path): os.remove(path)
            return True
        text = "" if diff.new_file else open(path, encoding='utf-8').read()
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(diff.apply_to(text))
        return True


# ══════════════════════════════════════════════
# Undo/Redo 栈
# ══════════════════════════════════════════════

@dataclass
class Operation:
    """操作记录"""
    id: str = field(default_factory=lambda: hashlib.md5(str(time.time()).encode()).hexdigest()[:8])
    type: str = "edit"
    description: str = ""
    diffs: List[FileDiff] = field(default_factory=list)
    snapshot: Dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict = field(default_factory=dict)


class UndoRedoStack:
    """
    操作历史栈 — Cursor 风格
    
    用法:
      ur = UndoRedoStack()
      snap = ur.snapshot_before(["file.py"])  # 修改前
      # ... 修改文件 ...
      ur.commit("edit", "改了啥", ["file.py"], [diff], snap)  # 修改后
      
      ur.undo()  # 撤销
      ur.redo()  # 重做
    """
    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir
        self._undo: List[Operation] = []
        self._redo: List[Operation] = []
        self._stats = {"total_ops": 0, "undos": 0, "redos": 0}

    def snapshot_before(self, files: List[str]) -> Dict[str, str]:
        """修改文件前调用, 保存快照"""
        snap = {}
        for fp in files:
            full = os.path.join(self.base_dir, fp) if self.base_dir else fp
            if os.path.exists(full):
                with open(full, encoding='utf-8') as f:
                    snap[fp] = f.read()
        return snap

    def commit(self, type: str, desc: str, files: List[str],
               diffs: List[FileDiff] = None,
               snapshot: Dict[str, str] = None,
               meta: Dict = None) -> Operation:
        """提交操作到历史栈"""
        op = Operation(type=type, description=desc, diffs=diffs or [],
                       snapshot=snapshot or {}, metadata=meta or {})
        self._undo.append(op)
        self._redo.clear()
        self._stats["total_ops"] += 1
        return op

    def undo(self) -> Optional[Operation]:
        """撤销: 用快照恢复"""
        if not self._undo:
            return None
        op = self._undo.pop()
        for fp, content in op.snapshot.items():
            full = os.path.join(self.base_dir, fp) if self.base_dir else fp
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)
        self._redo.append(op)
        self._stats["undos"] += 1
        return op

    def redo(self) -> Optional[Operation]:
        """重做: 用 diff 重新应用"""
        if not self._redo:
            return None
        op = self._redo.pop()
        for fd in op.diffs:
            Patcher.apply(fd, self.base_dir)
        self._undo.append(op)
        self._stats["redos"] += 1
        return op

    def to_dict(self) -> dict:
        return {"undo_count": len(self._undo), "redo_count": len(self._redo),
                "stats": dict(self._stats)}
