#!/usr/bin/env python3
"""解析 RunWithBuildInfo 子命令处理器 + 默认执行器表的符号名。"""
import sys

syms = {}
with open(r'F:/DEEPCODE/scripts/reasonix_symbols.txt', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(' ', 1)
        if len(parts) != 2:
            continue
        try:
            va = int(parts[0], 16)
        except ValueError:
            continue
        syms[va] = parts[1].strip()

targets = {
    'cap':        0x141310440,
    'bot':        0x141313d40,
    'cmp':        0x141390880,
    'run':        0x14135bda0,
    'task':       0x14141d3e0,
    'hook':       0x141387be0,
    'init':       0x141365860,
    'hooks':      0x141387be0,
    'serve':      0x14135ef40,
    'setup':      0x141365060,
    'config':     0x14136a160,
    'doctor':     0x14137dcc0,
    'plugin':     0x1413b6360,
    'report':     0x1413c0280,
    'update':     0x1413cc720,
    'review':     0x1413d2aa0,
    'session':    0x1413e1120,
    'upgrade':    0x141434620,
    'version':    0x14131dba0,
    'subagent':   0x141417c00,
    'compile':    0x1413ff040,
    'doc-config': 0x14137d120,
}
print('=== 子命令处理函数 ===')
for cmd, addr in targets.items():
    name = syms.get(addr, '<未找到>')
    print(f'  {cmd:12s} 0x{addr:x} -> {name}')

print()
print('=== 默认执行器表 (0x141fcf428 的 8 项) ===')
exec_addrs = [0x141361160, 0x14143da40, 0x14143df80, 0x14143de40,
              0x14143de60, 0x14143dfe0, 0x14143df60, 0x14143df20]
for i, addr in enumerate(exec_addrs):
    name = syms.get(addr, '<未找到>')
    print(f'  [{i}] 0x{addr:x} -> {name}')
