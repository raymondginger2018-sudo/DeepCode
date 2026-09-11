#!/usr/bin/env python3
"""Analyze Claude Code formatted source structure"""
import re

with open('F:/DEEPCODE/targets/claude_code_formatted.js', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')
print(f"Total lines: {len(lines)}")
print(f"Total chars: {len(content)}")
print(f"Total bytes: {len(content.encode('utf-8'))}")

# Find all function definitions
funcs = []
for i, line in enumerate(lines):
    m = re.search(r'(?:async\s+)?function\s+(\w+)', line)
    if m:
        funcs.append((i+1, m.group(1), 'fn'))
        continue
    m = re.search(r'(?:var|let|const)\s+(\w+)\s*=\s*(?:async\s*)?\(', line)
    if m and ('=>' in line or 'function' in line):
        funcs.append((i+1, m.group(1), '=>'))

print(f"\n=== Functions: {len(funcs)} ===")
for line_no, name, typ in funcs:
    print(f"  L{line_no:4d} [{typ}] {name}")

# Find require() calls
print(f"\n=== Requires ===")
requires = set()
for i, line in enumerate(lines):
    for m in re.finditer(r'require\(["\']([^"\']+)["\']\)', line):
        mod = m.group(1)
        if mod not in requires:
            requires.add(mod)
            print(f"  L{i+1}: {mod}")

# Find all module.exports = 
print(f"\n=== Module Exports ===")
for i, line in enumerate(lines):
    if 'module.exports' in line or 'exports.' in line:
        print(f"  L{i+1}: {line.strip()[:120]}")

# Search for key patterns
print(f"\n=== Key Module Patterns ===")
patterns = {
    'agent': ['agent', 'Agent', 'AGENT'],
    'mcp': ['mcp', 'MCP'],
    'permission': ['permission', 'Permission', 'perm'],
    'tool': ['\\btool\\b', 'Tool'],  
    'session': ['session', 'Session'],
    'server': ['server', 'serve'],
    'config': ['config', 'Config'],
}
for category, kws in patterns.items():
    count = 0
    for i, line in enumerate(lines):
        if count >= 8: break
        for kw in kws:
            if re.search(kw, line, re.IGNORECASE):
                s = line.strip()
                if len(s) > 10 and not s.startswith('//'):
                    print(f"  [{category}] L{i+1}: {s[:130]}")
                    count += 1
                    break
