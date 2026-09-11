#!/usr/bin/env python3
"""从符号表反查崩溃捕获链中各内部函数的名称。"""
import sys

sym = {}
with open(r'F:/DEEPCODE/scripts/reasonix_symbols.txt', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split(' ', 1)
        if len(parts) != 2:
            continue
        try:
            va = int(parts[0], 16)
        except ValueError:
            continue
        sym[va] = parts[1].strip()

targets = [
    0x14004dfa0, 0x14063be40, 0x140149060, 0x141308ba0, 0x1400840a0,
    0x14008a240, 0x14008a2e0, 0x14008c220, 0x1400f0840, 0x1431be980,
]

sorted_vas = sorted(sym.keys())

def find_name(t):
    name = sym.get(t)
    if name:
        return name, 0
    # 找最近的上一地址
    for va in reversed(sorted_vas):
        if va <= t:
            return sym[va], t - va
    return '?', -1

for t in targets:
    name, delta = find_name(t)
    if delta == 0:
        print(f'0x{t:016x} -> {name}')
    else:
        print(f'0x{t:016x} -> {name} (prev, +0x{delta:x})')
