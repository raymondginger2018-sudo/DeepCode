# -*- coding: utf-8 -*-
"""注册 Ollama 开机自启 + 删除死代理环境变量 (用户级)"""
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENV_KEY = r"Environment"

def register_ollama() -> None:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, "Ollama", 0, winreg.REG_SZ,
                      '"C:\\Users\\raymo\\AppData\\Local\\Programs\\Ollama\\ollama app.exe" --hidden')
    winreg.CloseKey(key)

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
    val, _ = winreg.QueryValueEx(key, "Ollama")
    winreg.CloseKey(key)
    print(f"[OK] Ollama 自启已注册: {val}")

def remove_proxy_vars() -> None:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE)
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        try:
            winreg.DeleteValue(key, name)
            print(f"[OK] 已删除用户环境变量 {name}")
        except FileNotFoundError:
            print(f"[SKIP] {name} 不存在（已删或本来就没有）")
    winreg.CloseKey(key)

    # 广播环境变量变更 (WM_SETTINGCHANGE)，让新启动的程序生效
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            2, 5000, None)
        print("[OK] 已广播环境变量变更通知")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 广播变更失败: {e}")

def verify() -> None:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY)
    found = []
    i = 0
    while True:
        try:
            name, _, _ = winreg.EnumValue(key, i)
            if "proxy" in name.lower():
                found.append(name)
            i += 1
        except OSError:
            break
    winreg.CloseKey(key)
    print("[VERIFY] 剩余 proxy 相关用户变量:", found if found else "(无)")

if __name__ == "__main__":
    register_ollama()
    remove_proxy_vars()
    verify()
    print("完成。注意: 已打开的终端/CLI 环境变量是启动时快照, 需重启才完全生效。")
