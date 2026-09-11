#!/usr/bin/env python3
"""Analyze claude.exe (v2.1.88) for entry points and version info"""
import re

with open('F:/DEEPCODE/LAOWU SHARE/claude.exe', 'rb') as f:
    data = f.read(10*1024*1024)

print("=== Version strings ===")
seen = set()
for m in re.finditer(rb'[0-9]+\.[0-9]+\.[0-9]+', data):
    ctx = data[max(0,m.start()-30):m.end()+30]
    try:
        s = ctx.decode('ascii', errors='replace')
        trimmed = s.strip()
        if trimmed not in seen:
            seen.add(trimmed)
            print(f"  0x{m.start():x}: {trimmed}")
    except:
        pass

print("\n=== Key entry/Claude paths ===")
for m in re.finditer(rb'[a-zA-Z_/]*(?:entrypoint|cli|serve|agent|claude)[a-zA-Z_/]*\.(?:js|ts)', data[:5*1024*1024]):
    s = m.group().decode('ascii', errors='replace')
    print(f"  0x{m.start():x}: {s}")

print("\n=== Bun build paths ===")
for m in re.finditer(rb'(?:src/entrypoints|B:/~BUN)[a-zA-Z0-9_/.-]{5,80}', data):
    s = m.group().decode('ascii', errors='replace')
    print(f"  0x{m.start():x}: {s}")

# Check version difference between claude.exe and formatted source
print("\n=== v2.1.88 vs v2.1.177 comparison ===")
print("claude.exe in Ghidra: v2.1.88 (Bun standalone binary, 88095 functions)")
print("claude_code_formatted.js: v2.1.177 (extracted bytecode, 348 pure JS functions)")
print("")
print("The JS source v2.1.177 is NEWER than the claude.exe binary v2.1.88")
print("Key difference: v2.1.88 compiled JS is INSIDE claude.exe as V8 bytecode")
print("v2.1.177 source was extracted from a DIFFERENT bytecode dump")
