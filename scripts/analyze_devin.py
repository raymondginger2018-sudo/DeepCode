#!/usr/bin/env python3
"""Analyze devin.exe — the Windsurf/Devin agent backend."""
import struct, os, re

path = r'C:\Users\raymo\AppData\Local\Programs\Devin\resources\app\extensions\windsurf\devin\bin\devin.exe'
size = os.path.getsize(path)
print(f"devin.exe: {size/1024/1024:.0f} MB")

with open(path, 'rb') as f:
    head = f.read(4096).decode('latin-1')

    # Language detection
    if 'go build' in head.lower() or 'runtime.' in head:
        print("Language: Go")
    elif 'libc++' in head or '_ZSt' in head:
        print("Language: C++")
    elif 'rustc' in head or 'rust_' in head:
        print("Language: Rust")
    elif 'python' in head.lower():
        print("Language: Python (embedded)")
    else:
        print("Language: Unknown (likely Go/C++)")

    # Check for PE sections
    f.seek(0x3C)
    pe_off = struct.unpack('<I', f.read(4))[0]
    f.seek(pe_off + 6)
    num_sections = struct.unpack('<H', f.read(2))[0]
    f.seek(pe_off + 24 + 240)
    print(f"Sections: {num_sections}")
    for i in range(num_sections):
        off = pe_off + 24 + 240 + i * 40
        f.seek(off)
        sname = f.read(8).rstrip(b'\x00').decode('ascii', errors='replace')
        vsize = struct.unpack('<I', f.read(4))[0]
        vaddr = struct.unpack('<I', f.read(4))[0]
        print(f"  {sname:8s} VA=0x{vaddr:08X} Size={vsize//1024}KB")

    # Scan for interesting strings in first 5MB
    f.seek(0)
    data = f.read(min(size, 5*1024*1024))
    text = data.decode('latin-1', errors='replace')

    patterns = {
        'Version': r'v?\d+\.\d+\.\d+',
        'Agent': r'(?:cascade|agent|devin|windsurf|codeium)[a-z_]*',
        'Protocol': r'(?:mcp|acp|grpc|protobuf|websocket)',
        'API': r'(?:/api/|/v1/|/v2/|/v3/)',
        'Model': r'(?:gpt|claude|gemini|deepseek|llama)[a-z0-9_-]*',
        'Tool': r'(?:tool|plugin|function)[a-z_]*',
    }

    for category, pat in patterns.items():
        matches = set()
        for m in re.findall(pat, text, re.IGNORECASE):
            m = m.strip()
            if 2 < len(m) < 60:
                matches.add(m)
        if matches:
            print(f"\n[{category}] ({len(matches)} matches):")
            for m in sorted(matches)[:15]:
                print(f"  {m}")
