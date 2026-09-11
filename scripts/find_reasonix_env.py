#!/usr/bin/env python3
"""搜索 reasonix.exe 中 .env / credentials 相关字符串，确认 global .env 读取逻辑与路径。"""
import struct

path = r'C:/Users/raymo/AppData/Roaming/npm/node_modules/reasonix/node_modules/@reasonix/cli-win32-x64/bin/reasonix.exe'
with open(path, 'rb') as f:
    data = f.read()


def va_to_off(va):
    if 0x140000000 <= va < 0x1414c2000:   # .text
        return va - 0x140001000 + 0x600
    if 0x1414c2000 <= va < 0x143092000:   # .rdata
        return va - 0x1414c2000 + 0x14c0c00
    if 0x143092000 <= va < 0x145490a5f:   # .data
        return va - 0x143092000 + 0x3090a00
    return None


def printable(b):
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in b)


# 搜索 .env / credentials 相关字节序列
for needle in [b'.env', b'global .env', b'credentials', b'.env file', b'loadEnv']:
    hits = []
    start = 0
    while True:
        i = data.find(needle, start)
        if i == -1:
            break
        hits.append(i)
        start = i + 1
    print(f"--- '{needle.decode()}' 出现 {len(hits)} 次 ---")
    for h in hits[:8]:
        ctx = data[max(0, h - 80):h + 100]
        print(f"  0x{h:08x}: ...{printable(ctx)}...")
    print()
