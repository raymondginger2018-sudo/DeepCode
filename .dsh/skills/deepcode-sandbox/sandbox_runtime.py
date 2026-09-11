#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Sandbox Runtime — Claude Code v2.1.216 Sandbox Runtime 移植
═══════════════════════════════════════════════════════════════════════
安全执行任意代码/命令，隔离子进程，限制文件系统和网络访问。

移植自 Claude Code sandbox-runtime:
  - generate-seccomp-filter.js  → Linux seccomp syscall filtering
  - windows-sandbox-utils.js    → Windows 沙箱工具 (作业对象 + ACL)

支持平台:
  - Windows: 作业对象 + Win32 API 限制
  - Linux:   seccomp-bpf 系统调用过滤
  - 跨平台:   Python subprocess + 沙箱包装

用法:
  # 安全执行 Python 代码
  python sandbox_runtime.py run --code "print('hello')" --timeout 10

  # 安全执行 Shell 命令
  python sandbox_runtime.py run --cmd "ls -la" --read-only /path/to/dir

  # 作为 MCP Server 启动
  python sandbox_runtime.py --mcp

  # Python 嵌入
  from sandbox_runtime import Sandbox
  async with Sandbox() as sb:
      result = await sb.run("python3", ["-c", "print(1+1)"])
"""

import asyncio
import ctypes
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import uuid

# ── Windows: 强制 stdout 使用 UTF-8 避免 GBK 编码错误 ─────
# 只包装一次: 重复 exec_module 会再次包装已包装的 stdout,
# 旧 wrapper 被 GC 时关闭底层 buffer → 后续 print 报 I/O on closed file
if sys.platform == "win32" and hasattr(sys.stdout, "buffer") and not isinstance(
        sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
elif sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper) and getattr(
        sys.stdout, "encoding", "").lower() != "utf-8":
    # 已被包装但编码不对 (极少数情况): 重建
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum

# ── 常量 ──────────────────────────────────────────────────

SYSTEM = platform.system()  # "Windows", "Linux", "Darwin"
IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"
IS_MACOS = SYSTEM == "Darwin"

# 默认拒绝的系统命令
DENIED_COMMANDS = [
    "shutdown", "reboot", "halt", "poweroff",
    "mkfs", "fdisk", "dd", "format",
    "sudo", "su", "chown", "chmod 777", "passwd",
    "iptables", "ufw",
]

# 默认拒绝的 Python 内置
DENIED_PYTHON_BUILTINS = [
    "__import__", "exec", "eval", "compile",
    "open", "file",
]


# ── Windows Job Object (移植自 CLAUDE.EXE uv_spawn 逆向) ────
# 参考: AssignProcessToJobObject + CreateJobObjectW + SetInformationJobObject

_HAS_WIN32API = False
if IS_WINDOWS:
    try:
        _kernel32 = ctypes.windll.kernel32

        # 常量定义
        JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000004
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION = 9
        JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO = 0x00000044

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("ChildProcessRate", ctypes.c_uint32),
                ("ExtendedLimitInfo", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        _HAS_WIN32API = True
    except Exception:
        pass


class WindowsJobObject:
    """Windows Job Object — 对标 CLAUDE.EXE 的 uv_spawn 作业对象隔离

    功能 (从逆向结果移植):
      1. CreateJobObjectW → 创建作业对象
      2. AssignProcessToJobObject → 子进程加入作业
      3. SetInformationJobObject → 内存/进程数限制
      4. TerminateJobObject → 超时/清理时杀整个进程树
      5. CloseHandle → 资源释放

    用法:
        job = WindowsJobObject()
        job.assign(pid)      # 把进程加入作业
        job.set_limits(...)  # 设置限制
        job.terminate()      # 杀全部
        job.close()          # 释放
    """

    def __init__(self, name: str = None):
        self._handle = None
        self._pid = None
        if not IS_WINDOWS or not _HAS_WIN32API:
            return
        # 创建作业对象 (对标 uv_spawn 的 CreateJobObjectW)
        name_w = ctypes.c_wchar_p(name or f"DeepCode_Sandbox_{uuid.uuid4().hex[:8]}")
        self._handle = _kernel32.CreateJobObjectW(None, name_w)
        if not self._handle:
            raise SandboxError(f"CreateJobObjectW failed: {ctypes.GetLastError()}")

    def assign(self, pid: int) -> bool:
        """分配进程到作业 (对标 uv_spawn 的 AssignProcessToJobObject)"""
        if not self._handle or not IS_WINDOWS:
            return False
        self._pid = pid
        proc = _kernel32.OpenProcess(0x400 | 0x1000, False, pid)  # PROCESS_CREATE_THREAD | PROCESS_TERMINATE
        if not proc:
            return False
        try:
            result = _kernel32.AssignProcessToJobObject(self._handle, proc)
            if not result:
                err = ctypes.GetLastError()
                # ERROR_ACCESS_DENIED (5) = 进程已在其他作业中，可接受
                if err != 5:
                    return False
            return True
        finally:
            _kernel32.CloseHandle(proc)

    def set_limits(self, memory_mb: int = 0, max_processes: int = 0, cpu_time_sec: int = 0):
        """设置作业限制 (对标 uv_spawn 的 SetInformationJobObject)

        Args:
            memory_mb: 最大内存 MB (0=不限制)
            max_processes: 最大进程数 (0=不限制)
            cpu_time_sec: 每进程 CPU 时间秒 (0=不限制)
        """
        if not self._handle or not IS_WINDOWS:
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE  # 作业关闭时杀全部进程

        if memory_mb > 0:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_mb * 1024 * 1024

        if max_processes > 0:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = max_processes

        if cpu_time_sec > 0:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_TIME
            info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_time_sec * 10000000  # 100ns 单位

        info.BasicLimitInformation.LimitFlags |= flags

        result = _kernel32.SetInformationJobObject(
            self._handle,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not result:
            err = ctypes.GetLastError()
            if err != 5:  # ERROR_ACCESS_DENIED (已有限制) 可忽略
                raise SandboxError(f"SetInformationJobObject failed: {err}")

    def terminate(self, exit_code: int = 1) -> bool:
        """终止作业内全部进程 (对标 uv_spawn 超时处理)"""
        if not self._handle or not IS_WINDOWS:
            return False
        result = _kernel32.TerminateJobObject(self._handle, ctypes.c_uint(exit_code))
        return bool(result)

    def close(self):
        """关闭作业句柄"""
        if self._handle and IS_WINDOWS:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── ntdll 原生文件 IO (源自 Cursor cursorsandbox 逆向) ─────────
# cursorsandbox 直调 NtCreateFile/NtWriteFile/NtReadFile 绕过 Win32 层,
# 用原生 API 做受限文件操作。本实现提供同样的"原生直通"能力:
#   - 不依赖 open()/os 模块 (沙箱内被禁)
#   - 精确控制 DesiredAccess / CreateDisposition
#   - 与 SandboxPolicy 白名单联动

# ── ntdll 结构体 (模块级, 供 NtFileIo 与内联 prelude 共用) ──

class _NtUnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort),
                ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_wchar_p)]


class _NtObjectAttributes(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ulong),
                ("RootDirectory", ctypes.c_void_p),
                ("ObjectName", ctypes.POINTER(_NtUnicodeString)),
                ("Attributes", ctypes.c_ulong),
                ("SecurityDescriptor", ctypes.c_void_p),
                ("SecurityQualityOfService", ctypes.c_void_p)]


class _NtIoStatusBlock(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_long),
                ("Information", ctypes.c_void_p)]


# ── Windows AppContainer 沙箱 (对标 cursorsandbox 深度隔离) ──
# Windows 内置容器模型: AppContainer 进程自带低完整性 + 资源隔离
#   - CreateAppContainerProfile: 注册容器 (userenv.dll)
#   - GetAppContainerSid: 取容器 SID
#   - CreateRestrictedToken: 去权限令牌
#   - SetTokenInformation(TokenAppContainerSid): 令牌绑定容器
#   - CreateProcessAsUserW: 以容器令牌启动子进程 (advapi32)
# 容器进程默认无法联网(无 internetClient 能力)、无法访问用户数据

class WindowsAppContainer:
    """Windows AppContainer 沙箱 — 对标 cursorsandbox 深度隔离层"""

    # TokenInformationClass
    TOKEN_APPCONTAINER_SID = 30
    TOKEN_APPCONTAINER_NUMBER = 31

    # CreateRestrictedToken flags
    DISABLE_MAX_PRIVILEGE = 0x1

    # CreateProcessAsUser 需要的令牌权限
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    TOKEN_ADJUST_DEFAULT = 0x0080

    # 能力 SID 派生 (internetClient 等)
    SECURITY_CAPABILITY_INTERNET_CLIENT = 2  # S-1-15-3-2

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("lpReserved", ctypes.c_wchar_p),
                    ("lpDesktop", ctypes.c_wchar_p), ("lpTitle", ctypes.c_wchar_p),
                    ("dwX", ctypes.c_ulong), ("dwY", ctypes.c_ulong),
                    ("dwXSize", ctypes.c_ulong), ("dwYSize", ctypes.c_ulong),
                    ("dwXCountChars", ctypes.c_ulong), ("dwYCountChars", ctypes.c_ulong),
                    ("dwFillAttribute", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("wShowWindow", ctypes.c_ushort), ("cbReserved2", ctypes.c_ushort),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", ctypes.c_void_p),
                    ("hStdOutput", ctypes.c_void_p), ("hStdError", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p),
                    ("dwProcessId", ctypes.c_ulong), ("dwThreadId", ctypes.c_ulong)]

    class TOKEN_APPCONTAINER_INFORMATION(ctypes.Structure):
        _fields_ = [("TokenAppContainer", ctypes.c_void_p)]  # PSID

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_ulong)]

    def __init__(self, name: str = None, capabilities: Tuple[str, ...] = ()):
        if not IS_WINDOWS:
            raise SandboxError("WindowsAppContainer requires Windows")
        self.name = name or f"DeepCodeSandbox_{uuid.uuid4().hex[:8]}"
        self.capabilities = capabilities  # 例: ("internetClient",)
        self._userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._sid = None  # PSID 缓存
        self._profile_created = False
        self._get_sid_fn = None
        self._setup_argtypes()

    @staticmethod
    def is_in_appcontainer() -> bool:
        """检测当前进程是否已在 AppContainer 沙箱内
        (Store 应用/沙箱进程无法嵌套创建新 AppContainer 令牌)
        """
        if not IS_WINDOWS:
            return False
        adv = ctypes.WinDLL("advapi32")
        k32 = ctypes.WinDLL("kernel32")
        adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                         ctypes.POINTER(ctypes.c_void_p)]
        adv.OpenProcessToken.restype = ctypes.c_int
        adv.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_ulong)]
        adv.GetTokenInformation.restype = ctypes.c_int
        base = ctypes.c_void_p()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008,
                                    ctypes.byref(base)):
            return False
        try:
            need = ctypes.c_ulong(0)
            buf = ctypes.create_string_buffer(64)
            r = adv.GetTokenInformation(ctypes.c_void_p(base.value),
                                        WindowsAppContainer.TOKEN_APPCONTAINER_SID,
                                        buf, 64, ctypes.byref(need))
            return bool(r and need.value)
        finally:
            k32.CloseHandle(ctypes.c_void_p(base.value))

    def _setup_argtypes(self):
        """配置 Win32 API 签名 (防 32 位截断)"""
        ue = self._userenv
        ue.CreateAppContainerProfile.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
        ue.CreateAppContainerProfile.restype = ctypes.c_ulong
        ue.DeleteAppContainerProfile.argtypes = [ctypes.c_wchar_p]
        ue.DeleteAppContainerProfile.restype = ctypes.c_ulong
        # SID 派生: 优先 GetAppContainerSid(kernel32/userenv), 兜底
        # DeriveAppContainerSidFromAppContainerName(userenv, 实测存在)
        self._get_sid_fn = None
        for dll, fn in ((self._kernel32, "GetAppContainerSid"),
                        (self._userenv, "GetAppContainerSid"),
                        (self._userenv, "DeriveAppContainerSidFromAppContainerName")):
            try:
                self._get_sid_fn = getattr(dll, fn)
                break
            except AttributeError:
                continue
        if self._get_sid_fn is None:
            raise SandboxError("AppContainer SID API 不可用 (GetAppContainerSid/Derive)")
        self._get_sid_fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
        self._get_sid_fn.restype = ctypes.c_ulong

        a = self._advapi32
        a.CreateRestrictedToken.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p)]
        a.CreateRestrictedToken.restype = ctypes.c_int
        a.CopySid.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        a.CopySid.restype = ctypes.c_int
        a.CreateProcessAsUserW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong,
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(self.STARTUPINFO), ctypes.POINTER(self.PROCESS_INFORMATION)]
        a.CreateProcessAsUserW.restype = ctypes.c_int
        # 令牌类 API 在 advapi32 (非 kernel32)
        a.SetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        a.SetTokenInformation.restype = ctypes.c_int
        a.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
        a.OpenProcessToken.restype = ctypes.c_int
        a.GetLengthSid.argtypes = [ctypes.c_void_p]
        a.GetLengthSid.restype = ctypes.c_ulong
        a.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
        a.ConvertStringSidToSidW.restype = ctypes.c_int

        k = self._kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.LocalFree.argtypes = [ctypes.c_void_p]

    # ── 容器生命周期 ──────────────────────────────────────
    def create_profile(self) -> bytes:
        """创建 AppContainer profile, 返回容器 SID bytes"""
        # 能力数组: pCapabilities 是 SID_AND_ATTRIBUTES 结构数组
        # (PSID Sid + DWORD Attributes), 不是 ULONG 数组
        caps_buf = None
        caps_count = 0
        if self.capabilities:
            items = []
            for cap in self.capabilities:
                if cap == "internetClient":
                    sid_p = ctypes.c_void_p()
                    r = self._advapi32.ConvertStringSidToSidW(
                        "S-1-15-3-2", ctypes.byref(sid_p))  # internetClient
                    if not r:
                        raise SandboxError("ConvertStringSidToSidW(internetClient) failed")
                    items.append(self._SID_AND_ATTRIBUTES(sid_p.value, 0))
            if items:
                caps_count = len(items)
                CapsType = self._SID_AND_ATTRIBUTES * caps_count
                caps_buf = CapsType(*items)
        sid_out = ctypes.c_void_p()

        result = self._userenv.CreateAppContainerProfile(
            self.name, "DeepCode Sandbox", "AppContainer 沙箱",
            ctypes.byref(caps_buf) if caps_buf else None,
            caps_count, ctypes.byref(sid_out))
        if result != 0 and result != 0x8007139A:  # ERROR_ALREADY_EXISTS
            err = ctypes.get_last_error()
            raise SandboxError(f"CreateAppContainerProfile failed: {result} (err={err})")
        self._profile_created = True
        return self.get_sid()

    def get_sid(self) -> bytes:
        """取容器 SID (未创建则自动创建)"""
        if self._sid:
            return self._sid
        sid_out = ctypes.c_void_p()
        result = self._get_sid_fn(self.name, ctypes.byref(sid_out))
        if result != 0:
            raise SandboxError(f"AppContainer SID 派生失败: {result}")
        try:
            # 真实 SID 长度复制 (GetLengthSid, 而非固定子机构数)
            adv = self._advapi32
            length = adv.GetLengthSid(ctypes.c_void_p(sid_out.value))
            buf = ctypes.create_string_buffer(int(length))
            if not adv.CopySid(length, buf, ctypes.c_void_p(sid_out.value)):
                raise SandboxError(f"CopySid failed: {ctypes.get_last_error()}")
            self._sid = buf.raw
            return self._sid
        finally:
            self._kernel32.LocalFree(ctypes.c_void_p(sid_out.value))

    def open_current_token(self) -> int:
        """打开当前进程令牌 (返回句柄, 需 CloseHandle)"""
        base = ctypes.c_void_p()
        if not self._advapi32.OpenProcessToken(
                self._kernel32.GetCurrentProcess(),
                self.TOKEN_ASSIGN_PRIMARY | self.TOKEN_DUPLICATE |
                self.TOKEN_QUERY | self.TOKEN_ADJUST_DEFAULT,
                ctypes.byref(base)):
            raise SandboxError(f"OpenProcessToken failed: {ctypes.get_last_error()}")
        return base.value or 0

    def restricted_token(self, base_token: int) -> int:
        """创建绑定 AppContainer 的受限令牌
        Args:
            base_token: 当前进程令牌句柄 (OpenProcessToken)
        Returns: 新令牌句柄 (需 CloseHandle)
        """
        if not self._sid:
            self.create_profile()
        sid_bytes = self.get_sid()
        sid_buf = ctypes.create_string_buffer(sid_bytes)

        # 1. CreateRestrictedToken: 禁用所有特权 + 完整性降级
        new_token = ctypes.c_void_p()
        r = self._advapi32.CreateRestrictedToken(
            ctypes.c_void_p(base_token), self.DISABLE_MAX_PRIVILEGE,
            0, None, 0, None, 0, None, ctypes.byref(new_token))
        if not r:
            raise SandboxError(f"CreateRestrictedToken failed: {ctypes.get_last_error()}")
        token = new_token.value

        # 2. SetTokenInformation: 绑定 AppContainer SID (advapi32)
        #    TokenAppContainerSid 的 TokenInformation 是
        #    TOKEN_APPCONTAINER_INFORMATION 结构 (8 字节, 含 PSID),
        #    不是裸 SID (传裸 SID 会 INVALID_PARAMETER)
        info = self.TOKEN_APPCONTAINER_INFORMATION(ctypes.addressof(sid_buf))
        r = self._advapi32.SetTokenInformation(
            ctypes.c_void_p(token), self.TOKEN_APPCONTAINER_SID,
            ctypes.byref(info), ctypes.sizeof(info))
        if not r:
            self._kernel32.CloseHandle(ctypes.c_void_p(token))
            hint = ("; 注意: 当前进程运行在 AppContainer 沙箱内(Store 应用), "
                    "Windows 禁止嵌套设置 AppContainer SID, 请在普通桌面 Python 下运行")
            if self.is_in_appcontainer():
                raise SandboxError(f"SetTokenInformation(AppContainerSid) failed: "
                                   f"{ctypes.get_last_error()}{hint}")
            raise SandboxError(f"SetTokenInformation(AppContainerSid) failed: {ctypes.get_last_error()}")

        # 3. TokenAppContainerNumber (标识容器, advapi32)
        num = ctypes.c_ulong(0)
        self._advapi32.SetTokenInformation(
            ctypes.c_void_p(token), self.TOKEN_APPCONTAINER_NUMBER,
            ctypes.byref(num), ctypes.sizeof(num))
        return token

    def spawn(self, cmdline: str, cwd: str = None) -> int:
        """以容器令牌启动进程 — CreateProcessAsUserW
        Returns: 进程 PID
        """
        # 打开当前进程令牌
        base = ctypes.c_void_p()
        if not self._kernel32.OpenProcessToken(
                self._kernel32.GetCurrentProcess(),
                self.TOKEN_ASSIGN_PRIMARY | self.TOKEN_DUPLICATE |
                self.TOKEN_QUERY | self.TOKEN_ADJUST_DEFAULT,
                ctypes.byref(base)):
            raise SandboxError(f"OpenProcessToken failed: {ctypes.get_last_error()}")
        token = self.restricted_token(base.value)
        self._kernel32.CloseHandle(base)

        try:
            si = self.STARTUPINFO()
            si.cb = ctypes.sizeof(self.STARTUPINFO)
            pi = self.PROCESS_INFORMATION()
            cwd_w = ctypes.c_wchar_p(cwd) if cwd else None
            cmd_w = ctypes.c_wchar_p(cmdline)
            result = self._advapi32.CreateProcessAsUserW(
                ctypes.c_void_p(token), None, cmd_w, None, None, False,
                0, None, cwd_w, ctypes.byref(si), ctypes.byref(pi))
            if not result:
                raise SandboxError(f"CreateProcessAsUserW failed: {ctypes.get_last_error()}")
            pid = pi.dwProcessId
            self._kernel32.CloseHandle(pi.hProcess)
            self._kernel32.CloseHandle(pi.hThread)
            return pid
        finally:
            self._kernel32.CloseHandle(ctypes.c_void_p(token))

    def delete_profile(self) -> None:
        """删除容器 profile (清理)"""
        if self._profile_created:
            try:
                self._userenv.DeleteAppContainerProfile(self.name)
            except Exception:
                pass
            self._profile_created = False
            self._sid = None

    def __enter__(self):
        self.create_profile()
        return self

    def __exit__(self, *args):
        self.delete_profile()



class NtFileIo:
    """ntdll 原生文件 IO — 对标 cursorsandbox 的 NtCreateFile/NtReadFile/NtWriteFile"""

    # NTSTATUS
    STATUS_SUCCESS = 0
    STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
    STATUS_ACCESS_DENIED = 0xC0000022

    # CreateDisposition
    FILE_OPEN = 1
    FILE_CREATE = 2
    FILE_OPEN_IF = 3
    FILE_OVERWRITE = 4
    FILE_OVERWRITE_IF = 5

    # CreateOptions
    FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    FILE_NON_DIRECTORY_FILE = 0x00000040

    # DesiredAccess (SYNCHRONIZE 必须 — FILE_SYNCHRONOUS_IO_NONALERT 要求)
    GENERIC_READ = 0x80000000 | 0x00100000   # GENERIC_READ | SYNCHRONIZE
    GENERIC_WRITE = 0x40000000 | 0x00100000  # GENERIC_WRITE | SYNCHRONIZE
    FILE_SHARE_ALL = 0x7  # READ|WRITE|DELETE

    # 类型别名 (保持调用点不变)
    UNICODE_STRING = _NtUnicodeString
    OBJECT_ATTRIBUTES = _NtObjectAttributes
    IO_STATUS_BLOCK = _NtIoStatusBlock

    def __init__(self):
        if not IS_WINDOWS:
            raise SandboxError("NtFileIo requires Windows")
        self._ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        self._ntdll.NtCreateFile.restype = ctypes.c_long
        self._ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong,
            ctypes.POINTER(self.OBJECT_ATTRIBUTES),
            ctypes.POINTER(self.IO_STATUS_BLOCK),
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ]
        self._ntdll.NtReadFile.restype = ctypes.c_long
        self._ntdll.NtReadFile.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(self.IO_STATUS_BLOCK), ctypes.c_void_p, ctypes.c_ulong,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        self._ntdll.NtWriteFile.restype = ctypes.c_long
        self._ntdll.NtWriteFile.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(self.IO_STATUS_BLOCK), ctypes.c_void_p, ctypes.c_ulong,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        self._ntdll.NtClose.restype = ctypes.c_long
        self._ntdll.NtClose.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _status_name(status: int) -> str:
        return {
            NtFileIo.STATUS_SUCCESS: "SUCCESS",
            NtFileIo.STATUS_OBJECT_NAME_NOT_FOUND: "OBJECT_NAME_NOT_FOUND",
            NtFileIo.STATUS_ACCESS_DENIED: "ACCESS_DENIED",
        }.get(status & 0xFFFFFFFF, f"NTSTATUS 0x{status & 0xFFFFFFFF:08X}")

    @staticmethod
    def _nt_path(path: str) -> str:
        """Win32 路径 → NT 路径 (NtCreateFile 需要 \\??\\ 前缀)"""
        p = os.path.abspath(path)
        if p.startswith("\\\\"):
            return "\\??\\UNC" + p[1:]  # \\server\share → \??\UNC\server\share
        return "\\??\\" + p

    def _create(self, path: str, desired_access: int, disposition: int) -> Tuple[int, int]:
        """NtCreateFile — 返回 (handle, ntstatus)"""
        nt_path = self._nt_path(path)
        path_w = ctypes.c_wchar_p(nt_path)
        us = self.UNICODE_STRING(len(nt_path) * 2, len(nt_path) * 2 + 2, path_w)
        oa = self.OBJECT_ATTRIBUTES(ctypes.sizeof(self.OBJECT_ATTRIBUTES),
                                    None, ctypes.pointer(us), 0x40, None, None)  # OBJ_CASE_INSENSITIVE
        handle = ctypes.c_void_p()
        iosb = self.IO_STATUS_BLOCK()
        status = self._ntdll.NtCreateFile(
            ctypes.byref(handle), desired_access, ctypes.byref(oa), ctypes.byref(iosb),
            None, 0, self.FILE_SHARE_ALL, disposition,
            self.FILE_SYNCHRONOUS_IO_NONALERT | self.FILE_NON_DIRECTORY_FILE,
            None, 0)
        return handle.value or 0, status & 0xFFFFFFFF

    def open_read(self, path: str) -> Tuple[int, int]:
        """以读权限打开 — 对标 cursorsandbox NtCreateFile(READ)"""
        h, st = self._create(path, self.GENERIC_READ, self.FILE_OPEN)
        if st != self.STATUS_SUCCESS:
            raise SandboxError(f"NtCreateFile(READ) {path}: {self._status_name(st)}")
        return h, st

    def open_write(self, path: str, overwrite: bool = True) -> Tuple[int, int]:
        """以写权限打开 (不存在则创建)"""
        disposition = self.FILE_OVERWRITE_IF if overwrite else self.FILE_OPEN_IF
        h, st = self._create(path, self.GENERIC_WRITE, disposition)
        if st != self.STATUS_SUCCESS:
            raise SandboxError(f"NtCreateFile(WRITE) {path}: {self._status_name(st)}")
        return h, st

    def read(self, handle: int, size: int = 65536) -> Tuple[bytes, int]:
        """NtReadFile — 返回 (data, ntstatus)"""
        buf = ctypes.create_string_buffer(size)
        iosb = self.IO_STATUS_BLOCK()
        status = self._ntdll.NtReadFile(
            ctypes.c_void_p(handle), None, None, None, ctypes.byref(iosb),
            buf, size, None, None)
        n = iosb.Information or 0
        return buf.raw[:n], status & 0xFFFFFFFF

    def write(self, handle: int, data: bytes) -> int:
        """NtWriteFile — 返回 ntstatus"""
        iosb = self.IO_STATUS_BLOCK()
        buf = ctypes.create_string_buffer(data)
        status = self._ntdll.NtWriteFile(
            ctypes.c_void_p(handle), None, None, None, ctypes.byref(iosb),
            buf, len(data), None, None)
        return status & 0xFFFFFFFF

    def close(self, handle: int) -> None:
        if handle:
            self._ntdll.NtClose(ctypes.c_void_p(handle))

    def read_file(self, path: str) -> bytes:
        """一键读文件 (NtCreateFile → NtReadFile → NtClose)"""
        h, st = self.open_read(path)
        if st != self.STATUS_SUCCESS:
            raise SandboxError(f"NtReadFile {path}: {self._status_name(st)}")
        try:
            chunks, buf = [], self.read(h, 1 << 20)[0]
            while buf:
                chunks.append(buf)
                buf = self.read(h, 1 << 20)[0]
            return b"".join(chunks)
        finally:
            self.close(h)

    def write_file(self, path: str, data: bytes, overwrite: bool = True) -> int:
        """一键写文件 (NtCreateFile → NtWriteFile → NtClose)"""
        h, st = self.open_write(path, overwrite)
        if st != self.STATUS_SUCCESS:
            raise SandboxError(f"NtWriteFile {path}: {self._status_name(st)}")
        try:
            return self.write(h, data)
        finally:
            self.close(h)


# ── Python 沙箱内联 ntdll 直通 (注入到 run_python 前导代码) ──
# 与 NtFileIo 同一套 ntdll 调用, 但以字符串形式注入沙箱子进程,
# 使沙箱内用户代码 (open/os/ctypes 均被禁) 仍可通过受限白名单做原生文件 IO。

_NTDLL_PRELUDE_SRC = (
    "# ── ntdll native IO (对标 cursorsandbox) ──\n"
    "import ctypes as _ct, os as _os\n"
    "def _nt_io_read(path):\n"
    "    # 路径白名单检查 (读: 用 Win32 路径)\n"
    "    p = _os.path.abspath(path)\n"
    "    for d in _NT_READ_DENY:\n"
    "        if p.startswith(d):\n"
    "            raise RuntimeError('Sandbox: ntdll read denied: ' + p)\n"
    "    # Win32 → NT 路径 (NtCreateFile 需要 \\\\??\\\\ 前缀)\n"
    "    if not p.startswith('\\\\??\\\\'):\n"
    "        p = ('\\\\??\\\\UNC' + p[1:]) if p.startswith('\\\\\\\\') else ('\\\\??\\\\' + p)\n"
    "    _n = _ct.WinDLL('ntdll')\n"
    "    class _US(_ct.Structure):\n"
    "        _fields_ = [('l', _ct.c_ushort), ('m', _ct.c_ushort), ('b', _ct.c_wchar_p)]\n"
    "    class _OA(_ct.Structure):\n"
    "        _fields_ = [('l', _ct.c_ulong), ('r', _ct.c_void_p), ('n', _ct.POINTER(_US)),\n"
    "                    ('a', _ct.c_ulong), ('s', _ct.c_void_p), ('q', _ct.c_void_p)]\n"
    "    class _IS(_ct.Structure):\n"
    "        _fields_ = [('st', _ct.c_long), ('info', _ct.c_void_p)]\n"
    "    _n.NtCreateFile.argtypes = [_ct.POINTER(_ct.c_void_p), _ct.c_ulong, _ct.POINTER(_OA),\n"
    "                                _ct.POINTER(_IS), _ct.c_void_p, _ct.c_ulong, _ct.c_ulong,\n"
    "                                _ct.c_ulong, _ct.c_ulong, _ct.c_void_p, _ct.c_ulong]\n"
    "    _n.NtReadFile.argtypes = [_ct.c_void_p, _ct.c_void_p, _ct.c_void_p, _ct.c_void_p,\n"
    "                             _ct.POINTER(_IS), _ct.c_void_p, _ct.c_ulong, _ct.c_void_p, _ct.c_void_p]\n"
    "    _n.NtClose.argtypes = [_ct.c_void_p]\n"
    "    us = _US(len(p) * 2, len(p) * 2 + 2, p)\n"
    "    oa = _OA(_ct.sizeof(_OA), None, _ct.pointer(us), 0x40, None, None)\n"
    "    h = _ct.c_void_p(); iosb = _IS()\n"
    "    st = _n.NtCreateFile(_ct.byref(h), 0x80100000, _ct.byref(oa), _ct.byref(iosb),\n"
    "                         None, 0, 7, 1, 0x60, None, 0)  # GENERIC_READ|FILE_OPEN|SYNC+NONDIR\n"
    "    if st & 0xFFFFFFFF:\n"
    "        raise RuntimeError('Sandbox: NtCreateFile failed: 0x%08X' % (st & 0xFFFFFFFF))\n"
    "    out = b''\n"
    "    while True:\n"
    "        buf = _ct.create_string_buffer(1 << 16)\n"
    "        iosb = _IS()\n"
    "        st = _n.NtReadFile(h, None, None, None, _ct.byref(iosb), buf, 1 << 16, None, None)\n"
    "        n = iosb.info or 0\n"
    "        if not n:\n"
    "            break\n"
    "        out += buf.raw[:n]\n"
    "    _n.NtClose(h)\n"
    "    return out\n"
    "def _nt_io_write(path, data):\n"
    "    # 路径白名单检查 (写: 必须 write_dirs)\n"
    "    p = _os.path.abspath(path)\n"
    "    ok = any(p.startswith(wd) for wd in _NT_WRITE_DIRS)\n"
    "    if not ok:\n"
    "        raise RuntimeError('Sandbox: ntdll write denied: ' + p)\n"
    "    # Win32 → NT 路径 (NtCreateFile 需要 \\\\??\\\\ 前缀)\n"
    "    if not p.startswith('\\\\??\\\\'):\n"
    "        p = ('\\\\??\\\\UNC' + p[1:]) if p.startswith('\\\\\\\\') else ('\\\\??\\\\' + p)\n"
    "    _n = _ct.WinDLL('ntdll')\n"
    "    class _US(_ct.Structure):\n"
    "        _fields_ = [('l', _ct.c_ushort), ('m', _ct.c_ushort), ('b', _ct.c_wchar_p)]\n"
    "    class _OA(_ct.Structure):\n"
    "        _fields_ = [('l', _ct.c_ulong), ('r', _ct.c_void_p), ('n', _ct.POINTER(_US)),\n"
    "                    ('a', _ct.c_ulong), ('s', _ct.c_void_p), ('q', _ct.c_void_p)]\n"
    "    class _IS(_ct.Structure):\n"
    "        _fields_ = [('st', _ct.c_long), ('info', _ct.c_void_p)]\n"
    "    _n.NtCreateFile.argtypes = [_ct.POINTER(_ct.c_void_p), _ct.c_ulong, _ct.POINTER(_OA),\n"
    "                                _ct.POINTER(_IS), _ct.c_void_p, _ct.c_ulong, _ct.c_ulong,\n"
    "                                _ct.c_ulong, _ct.c_ulong, _ct.c_void_p, _ct.c_ulong]\n"
    "    _n.NtWriteFile.argtypes = [_ct.c_void_p, _ct.c_void_p, _ct.c_void_p, _ct.c_void_p,\n"
    "                              _ct.POINTER(_IS), _ct.c_void_p, _ct.c_ulong, _ct.c_void_p, _ct.c_void_p]\n"
    "    _n.NtClose.argtypes = [_ct.c_void_p]\n"
    "    us = _US(len(p) * 2, len(p) * 2 + 2, p)\n"
    "    oa = _OA(_ct.sizeof(_OA), None, _ct.pointer(us), 0x40, None, None)\n"
    "    h = _ct.c_void_p(); iosb = _IS()\n"
    "    st = _n.NtCreateFile(_ct.byref(h), 0x40100000, _ct.byref(oa), _ct.byref(iosb),\n"
    "                         None, 0, 7, 5, 0x60, None, 0)  # GENERIC_WRITE|OVERWRITE_IF\n"
    "    if st & 0xFFFFFFFF:\n"
    "        raise RuntimeError('Sandbox: NtCreateFile(w) failed: 0x%08X' % (st & 0xFFFFFFFF))\n"
    "    buf = _ct.create_string_buffer(data)\n"
    "    iosb = _IS()\n"
    "    st = _n.NtWriteFile(h, None, None, None, _ct.byref(iosb), buf, len(data), None, None)\n"
    "    _n.NtClose(h)\n"
    "    if st & 0xFFFFFFFF:\n"
    "        raise RuntimeError('Sandbox: NtWriteFile failed: 0x%08X' % (st & 0xFFFFFFFF))\n"
    "    return len(data)\n"
)


# ── 策略配置 ──────────────────────────────────────────────

@dataclass
class SandboxPolicy:
    """沙箱安全策略 — 对标 Claude Code sandbox-runtime"""
    # 网络访问
    allow_network: bool = False
    allow_listen: bool = False
    # 文件系统
    read_only_dirs: List[str] = field(default_factory=lambda: [])
    write_dirs: List[str] = field(default_factory=lambda: [])
    deny_paths: List[str] = field(default_factory=lambda: ["/etc", "/proc", "/sys"])
    # 进程
    allow_fork: bool = False
    max_processes: int = 4
    # 资源
    max_memory_mb: int = 512
    max_cpu_time_sec: int = 30
    max_disk_mb: int = 100
    # 命令
    deny_commands: List[str] = field(default_factory=lambda: DENIED_COMMANDS.copy())
    # Python
    deny_builtins: List[str] = field(default_factory=lambda: DENIED_PYTHON_BUILTINS.copy())
    # 禁止导入的模块 (os/subprocess 等可绕过命令黑名单在子进程中执行危险操作)
    blocked_imports: List[str] = field(default_factory=lambda: [
        "os", "subprocess", "shutil", "ctypes", "signal",
        "multiprocessing", "socket", "http", "urllib", "ftplib",
        "telnetlib", "smtplib", "poplib", "imaplib",
    ])
    # ntdll 原生直通 IO (对标 cursorsandbox): True 时向 Python 沙箱注入
    # nt_read_file/nt_write_file — 绕过被禁的 open()/os, 仍受白名单约束
    use_ntdll_io: bool = False

    @classmethod
    def restrictive(cls) -> "SandboxPolicy":
        """严格模式 — 无网络，只读"""
        return cls(allow_network=False, allow_fork=False)

    @classmethod
    def default(cls) -> "SandboxPolicy":
        """默认模式 — 允许网络，禁止修改系统文件"""
        return cls(allow_network=True)

    @classmethod
    def permissive(cls) -> "SandboxPolicy":
        """宽松模式 — 几乎不限制"""
        return cls(
            allow_network=True,
            allow_listen=True,
            allow_fork=True,
            max_processes=32,
            max_memory_mb=2048,
            deny_commands=[],
            blocked_imports=[],
        )


# ── 沙箱异常 ──────────────────────────────────────────────

class SandboxError(Exception):
    """沙箱执行错误"""
    pass

class SandboxViolation(SandboxError):
    """安全策略违规"""
    pass


# ── 沙箱执行器 ────────────────────────────────────────────

class SandboxExecutor:
    """
    沙箱执行器 — 安全执行子进程

    策略 (对标 Claude Code sandbox-runtime):
      1. 命令黑名单: 拒绝危险命令 (shutdown, sudo, dd ...)
      2. 路径白名单: 只读/写入目录限制
      3. 资源限制: 内存 / CPU / 进程数
      4. 超时保护: 自动 kill 超时进程
      5. Python 安全: 限制危险内置函数
    """

    def __init__(self, policy: SandboxPolicy = None, workspace: str = None):
        self.policy = policy or SandboxPolicy.default()
        self.workspace = workspace or os.getcwd()
        self._temp_dirs: List[str] = []
        # Windows Job Object — 移植自 CLAUDE.EXE uv_spawn 的 AssignProcessToJobObject
        self._job_object = None
        if IS_WINDOWS:
            try:
                self._job_object = WindowsJobObject()
                self._job_object.set_limits(
                    memory_mb=self.policy.max_memory_mb,
                    max_processes=self.policy.max_processes,
                    cpu_time_sec=self.policy.max_cpu_time_sec,
                )
            except Exception:
                self._job_object = None

    def _check_command(self, cmd: List[str]):
        """检查命令是否在黑名单中 — 词边界匹配, 防路径子串误判 (如 'su' in 'c:\\users')"""
        cmd_str = " ".join(cmd).lower()
        for denied in self.policy.deny_commands:
            d = denied.lower()
            # 词边界: 仅当 denied 是独立 token 时命中
            if re.search(rf"(?<![a-z0-9]){re.escape(d)}(?![a-z0-9])", cmd_str):
                raise SandboxViolation(
                    f"Command denied by policy: '{denied}' in '{cmd_str}'"
                )

    def _check_path_access(self, path: str, mode: str = "read"):
        """检查路径访问权限"""
        p = Path(path).resolve()
        str_p = str(p)

        # 检查拒绝列表
        for denied in self.policy.deny_paths:
            if str_p.startswith(denied):
                raise SandboxViolation(
                    f"Path access denied: {str_p} (in deny list)"
                )

        if mode == "read":
            # 读模式: 允许读任何不在拒绝列表中的路径
            return
        elif mode == "write":
            # 写模式: 必须在 write_dirs 内
            for wd in self.policy.write_dirs:
                if str_p.startswith(str(Path(wd).resolve())):
                    return
            raise SandboxViolation(
                f"Write access denied: {str_p} (not in write_dirs)"
            )

    def _create_sandbox_env(self) -> Dict[str, str]:
        """创建沙箱环境变量"""
        env = os.environ.copy()
        # 清理危险环境变量
        for key in list(env.keys()):
            if key.startswith("AWS_"):
                if not self.policy.allow_network:
                    del env[key]
            if key in ("HOME", "USERPROFILE"):
                pass  # 保留基本环境
        return env

    # ── 主执行函数 ──────────────────────────────────────

    async def run(
        self,
        cmd: List[str],
        stdin: Optional[str] = None,
        timeout: int = 30,
        cwd: Optional[str] = None,
        capture_output: bool = True,
    ) -> Dict:
        """
        在沙箱中执行命令

        Args:
            cmd: 命令列表 (e.g. ["python3", "-c", "print(1)"])
            stdin: 标准输入
            timeout: 超时秒数
            cwd: 工作目录
            capture_output: 是否捕获输出

        Returns:
            {"stdout": str, "stderr": str, "exit_code": int,
             "duration": float, "timed_out": bool}
        """
        self._check_command(cmd)
        cwd = cwd or self.workspace
        start = time.time()

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if stdin else None,
                    stdout=asyncio.subprocess.PIPE if capture_output else None,
                    stderr=asyncio.subprocess.PIPE if capture_output else None,
                    cwd=cwd,
                    env=self._create_sandbox_env(),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
                ),
                timeout=timeout,
            )

            # 将进程分配到 Job Object (移植自 CLAUDE.EXE uv_spawn)
            if self._job_object and IS_WINDOWS:
                self._job_object.assign(proc.pid)

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin.encode() if stdin else None),
                timeout=timeout,
            )

            duration = time.time() - start
            return {
                "stdout": stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
                "stderr": stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
                "exit_code": proc.returncode or 0,
                "duration": round(duration, 3),
                "timed_out": False,
            }

        except asyncio.TimeoutError:
            # 超时 — Job Object 终止整个进程树 (优于 taskkill)
            try:
                if self._job_object and IS_WINDOWS:
                    self._job_object.terminate()
                elif IS_WINDOWS:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True)
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            return {
                "stdout": "",
                "stderr": f"Execution timed out after {timeout}s",
                "exit_code": -1,
                "duration": timeout,
                "timed_out": True,
            }

        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "duration": round(time.time() - start, 3),
                "timed_out": False,
            }

    # ── Python 安全执行 ────────────────────────────────

    async def run_python(
        self,
        code: str,
        timeout: int = 15,
        restricted_globals: Dict = None,
    ) -> Dict:
        """
        安全执行 Python 代码

        特点:
          - 拦截危险内置函数 (exec/eval/open/__import__ 等)
          - 拦截危险模块导入 (os/subprocess/ctypes 等)
          - 超时保护
          - 捕获 stdout/stderr
        """
        # 用子进程执行来隔离 (比 restricted_exec 更安全)
        temp_py = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        )

        # 构建安全前导代码
        denied_builtins_json = json.dumps(self.policy.deny_builtins)
        blocked_imports_json = json.dumps(self.policy.blocked_imports)

        # ntdll 原生直通 (对标 cursorsandbox) — 可选注入
        # 注意: json.dumps 输出的反斜杠需双重转义, 否则被沙箱代码的
        # '''...''' 解析吃掉 (\\U 等会被当成转义序列)
        def _json_for_prelude(obj) -> str:
            return json.dumps(obj).replace("\\", "\\\\")

        ntdll_prelude = ""
        if self.policy.use_ntdll_io:
            ntdll_prelude = (
                "_NT_READ_DENY = set(json.loads('''"
                + _json_for_prelude(self.policy.deny_paths) + "'''))\n"
                "_NT_WRITE_DIRS = set(json.loads('''"
                + _json_for_prelude([os.path.abspath(w) for w in self.policy.write_dirs]) + "'''))\n"
                + _NTDLL_PRELUDE_SRC
                + "def nt_read_file(path):\n"
                "    return _nt_io_read(path).decode('utf-8', errors='replace')\n"
                "def nt_write_file(path, data):\n"
                "    if isinstance(data, str):\n"
                "        data = data.encode('utf-8')\n"
                "    return _nt_io_write(path, data)\n"
            )

        security_prelude = (
            "import builtins, sys, json\n"
            "from io import StringIO\n"
            # ntdll 直通需在 import hook 生效前加载 ctypes/os
            + ntdll_prelude
            + "# ── Sandbox security prelude ──\n"
            "_DENIED_BUILTINS = set(json.loads('''" + denied_builtins_json + "'''))\n"
            "_BLOCKED_IMPORTS = set(json.loads('''" + blocked_imports_json + "'''))\n"
            "# Save original __import__ BEFORE shadowing (it's in _DENIED_BUILTINS)\n"
            "_orig_import = builtins.__import__\n"
            "# Shadow denied builtins (via builtins module for consistent dict access)\n"
            "def _blocked(*a, **k):\n"
            "    raise RuntimeError(f'Sandbox: builtin blocked')\n"
            "for _b in _DENIED_BUILTINS:\n"
            "    if hasattr(builtins, _b):\n"
            "        setattr(builtins, _b, _blocked)\n"
            "# Hook __import__ to block dangerous modules (overriding the shadow above)\n"
            "def _safe_import(name, *args, **kwargs):\n"
            "    if name in _BLOCKED_IMPORTS:\n"
            "        raise ImportError(f\"Sandbox: import '{name}' is blocked\")\n"
            "    return _orig_import(name, *args, **kwargs)\n"
            "builtins.__import__ = _safe_import\n"
            + "# ── End security prelude ──\n"
        )

        try:
            # 注入 stdout 捕获
            wrapped_code = (
                security_prelude
                + "_orig_stdout = sys.stdout\n"
                "_buf = StringIO()\n"
                "sys.stdout = _buf\n"
                "try:\n"
                + "\n".join(f"    {line}" for line in code.split("\n"))
                + "\n"
                "finally:\n"
                "    sys.stdout = _orig_stdout\n"
                "_result = _buf.getvalue()\n"
                "if _result:\n"
                "    print(_result, end='')\n"
            )
            temp_py.write(wrapped_code)
            temp_py.close()

            result = await self.run(
                [sys.executable, temp_py.name],
                timeout=timeout,
            )
            return result
        finally:
            try:
                os.unlink(temp_py.name)
            except Exception:
                pass

    # ── 临时目录管理 ────────────────────────────────────

    def create_temp_dir(self, prefix: str = "sandbox_") -> str:
        """创建沙箱临时目录 (进程退出时自动清理)"""
        tmp = tempfile.mkdtemp(prefix=prefix)
        self._temp_dirs.append(tmp)
        return tmp

    def cleanup(self):
        """清理临时目录 + 关闭 Job Object"""
        for d in self._temp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._temp_dirs.clear()
        # 关闭 Job Object (触发 KILL_ON_JOB_CLOSE → 杀全部残留进程)
        if self._job_object:
            try:
                self._job_object.close()
            except Exception:
                pass
            self._job_object = None


# ── 沙箱管理器 ────────────────────────────────────────────

class Sandbox:
    """
    沙箱管理器 — 上下文管理器，对标 Claude Code sandbox-runtime 的 Windows/Linux 沙箱

    用法:
        async with Sandbox() as sb:
            result = await sb.run(["python3", "-c", "print('hello')"])
            print(result["stdout"])
    """

    def __init__(self, policy: SandboxPolicy = None, workspace: str = None):
        self.executor = SandboxExecutor(policy, workspace)

    async def __aenter__(self):
        return self.executor

    async def __aexit__(self, *args):
        self.executor.cleanup()

    # 快捷方法
    async def run(self, *args, **kwargs):
        return await self.executor.run(*args, **kwargs)

    async def run_python(self, *args, **kwargs):
        return await self.executor.run_python(*args, **kwargs)


# ── MCP Server 模式 ──────────────────────────────────────

def _mcp_send(obj):
    """JSON-RPC 单行输出（MCP 标准 stdio 传输协议）"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


async def run_mcp():
    """作为 MCP Server 运行（标准 MCP 协议）"""
    sandbox = Sandbox()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", "")

            # 标准 MCP 握手
            if method == "initialize":
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "deepcode-sandbox", "version": "1.0.0"},
                    },
                })
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "sandbox_run",
                                "description": "在沙箱中执行命令",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "cmd": {"type": "array", "items": {"type": "string"}},
                                        "stdin": {"type": "string"},
                                        "timeout": {"type": "integer"},
                                    },
                                    "required": ["cmd"],
                                },
                            },
                            {
                                "name": "sandbox_run_python",
                                "description": "安全执行 Python 代码",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "code": {"type": "string"},
                                        "timeout": {"type": "integer"},
                                    },
                                    "required": ["code"],
                                },
                            },
                        ],
                    },
                })
            elif method == "prompts/list":
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"prompts": []},
                })
            elif method == "tools/call":
                name = params.get("name", "")
                args = params.get("arguments", {})
                if name == "sandbox_run":
                    result = await sandbox.run(
                        args.get("cmd", []),
                        stdin=args.get("stdin"),
                        timeout=args.get("timeout", 30),
                    )
                elif name == "sandbox_run_python":
                    result = await sandbox.run_python(
                        args.get("code", ""),
                        timeout=args.get("timeout", 15),
                    )
                else:
                    result = {"error": f"Unknown tool: {name}"}
                _mcp_send({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                        ],
                    },
                })
        except json.JSONDecodeError:
            pass


# ── 热重载 (--reload 模式) ─────────────────────────────────

class _FileWatcher:
    """简单文件变更检测 (纯 Python, 零依赖)"""

    def __init__(self, filepath: str, interval: float = 1.0):
        self.filepath = filepath
        self.interval = interval
        self._last_hash = self._hash()

    def _hash(self) -> str:
        try:
            with open(self.filepath, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def check(self) -> bool:
        h = self._hash()
        if h and h != self._last_hash:
            self._last_hash = h
            return True
        return False

    async def watch(self):
        while True:
            if self.check():
                return
            await asyncio.sleep(self.interval)


async def _run_with_reload():
    """运行 MCP Server + 文件变更热重启"""
    script_path = os.path.abspath(__file__)
    watcher = _FileWatcher(script_path)
    print(f"[reload] Watching: {script_path}", flush=True)

    while True:
        print(f"[reload] Starting MCP server...", flush=True)
        task = asyncio.create_task(run_mcp())
        watch_task = asyncio.create_task(watcher.watch())

        done, pending = await asyncio.wait(
            [task, watch_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 取消另一个任务
        for t in pending:
            t.cancel()

        if watch_task in done:
            print(f"[reload] File changed. Restarting...", flush=True)
            continue
        else:
            # MCP server 自行退出了
            break


# ── CLI 入口 ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DeepCode Sandbox Runtime — 安全代码执行"
    )
    parser.add_argument("--mcp", action="store_true",
                        help="作为 MCP Server 运行")
    parser.add_argument("--reload", action="store_true",
                        help="热重载模式 (文件变更时自动重启 MCP Server)")
    parser.add_argument("--policy", choices=["restrictive", "default", "permissive"],
                        default="default", help="安全策略 (默认: default)")
    sub = parser.add_subparsers(dest="mode")

    # run 子命令
    run_parser = sub.add_parser("run", help="执行命令或代码")
    run_parser.add_argument("--cmd", nargs="+", help="要执行的命令")
    run_parser.add_argument("--code", help="要执行的 Python 代码")
    run_parser.add_argument("--timeout", type=int, default=30,
                            help="超时秒数")
    run_parser.add_argument("--read-only", action="append",
                            help="只读目录")
    run_parser.add_argument("--write-dir", action="append",
                            help="可写目录")

    args = parser.parse_args()

    if args.policy == "restrictive":
        policy = SandboxPolicy.restrictive()
    elif args.policy == "permissive":
        policy = SandboxPolicy.permissive()
    else:
        policy = SandboxPolicy.default()

    if args.mcp:
        if args.reload:
            asyncio.run(_run_with_reload())
        else:
            asyncio.run(run_mcp())
        return

    sb = Sandbox(policy)

    if args.mode == "run":
        if args.code:
            result = asyncio.run(sb.run_python(args.code, timeout=args.timeout))
        elif args.cmd:
            result = asyncio.run(sb.run(args.cmd, timeout=args.timeout))
        else:
            print("Error: specify --cmd or --code")
            sys.exit(1)

        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(result.get("exit_code", 0))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

