#!/usr/bin/env python3
"""分析 reasonix 符号表：包结构统计 + main.main 定位。"""
from collections import Counter

sym_path = r'F:/DEEPCODE/scripts/reasonix_symbols.txt'
lines = open(sym_path, encoding='utf-8').read().splitlines()

pkgs = Counter()
funcs = []
for line in lines:
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
    name = parts[1].strip()
    funcs.append((va, name))
    if name.startswith('reasonix/'):
        rest = name[len('reasonix/'):]
        top = rest.split('/')[0] if '/' in rest else rest
        pkgs[top] += 1

print(f'=== 总函数数: {len(funcs)} ===')
print()
print('=== reasonix 内部包函数统计 (Top 30) ===')
for k, v in pkgs.most_common(30):
    print(f'  {k:44s} {v}')
print()

# 关键函数定位
targets = ['main.main', 'main.runWithCrashCapture', 'main.runWithCrashCapture.func1',
           'main.init.func1', 'reasonix/internal/config.MigrateLegacyDeepSeekProtocolUserConfig']
print('=== 关键函数 ===')
for va, name in funcs:
    for t in targets:
        if name == t:
            fo = va - 0x140001000 + 0x600
            print(f'  {name:60s} VA=0x{va:x} 文件偏移=0x{fo:x}')
            break

# DeepSeek / anthropic / MCP 统计
print()
print('=== 关键词统计 ===')
for kw in ['deepseek', 'anthropic', 'mcp', 'bun', 'v8', 'plugin', 'config', 'router']:
    cnt = sum(1 for _, n in funcs if kw.lower() in n.lower())
    print(f'  {kw:12s} {cnt}')
