"""实测单个 deepcode exec 子进程的峰值内存 (Windows WorkingSetSize 采样)"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
GetProcessMemoryInfo = k32.K32GetProcessMemoryInfo
GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
GetProcessMemoryInfo.restype = wintypes.BOOL
OpenProcess = k32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE
CloseHandle = k32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_INFORMATION = 0x0400


def main():
    ws = tempfile.mkdtemp(prefix="dc_worker_")
    env = dict(os.environ)
    env.setdefault("DEEPSEEK_API_KEY", "test-key")
    cmd = [sys.executable, "-m", "cli.exec_cli", "test worker memory probe",
           "--workspace", ws, "--json", "--max-iterations", "0"]
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd="F:/DEEPCODE", env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    peak = 0
    while True:
        h = OpenProcess(PROCESS_QUERY_INFORMATION, False, p.pid)
        if h:
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                peak = max(peak, pmc.WorkingSetSize)
            CloseHandle(h)
        if p.poll() is not None:
            break
        time.sleep(0.05)

    out, err = p.communicate(timeout=120)
    elapsed = time.time() - t0
    print(f"EXIT_CODE={p.returncode}")
    print(f"PEAK_MEM_MB={peak / 1024 / 1024:.1f}")
    print(f"ELAPSED_S={elapsed:.1f}")
    print(f"STDOUT_TAIL={(out or '').strip()[-300:]!r}")
    print(f"STDERR_TAIL={(err or '').strip()[-1500:]!r}")


if __name__ == "__main__":
    main()
