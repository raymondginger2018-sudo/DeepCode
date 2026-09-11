#!/usr/bin/env python3
"""打印 reasonix.exe 中 .env 相关关键字符串的完整 ASCII 上下文。"""
data = open(r'C:/Users/raymo/AppData/Roaming/npm/node_modules/reasonix/node_modules/@reasonix/cli-win32-x64/bin/reasonix.exe', 'rb').read()

def printable(b):
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in b)

targets = [
    (0x01b7c0f8 - 0x100, 'home .env 前文'),
    (0x01b7c0f8 - 0x20, 'home .env 起点'),
    (0x01b81f69 - 0x60, 'write .env 上下文'),
    (0x01b84e7d - 0x60, 'project .env 上下文'),
    (0x01b938f5 - 0x80, 'DEEPSEEK_API_KEY= 上下文'),
    (0x01b9d528 - 0x80, 'Reasonix credentials 上下文'),
    (0x01b7c0f8 + 0x20, 'home .env 之后'),
]
for off, label in targets:
    print(f'=== {label} @ 0x{off:08x} ===')
    print(printable(data[off:off + 0x1e0]))
    print()
