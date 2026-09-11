#!/usr/bin/env python3
"""Analyze claude.exe .bun section (166.5MB)"""
import struct, os, math, re

path = "F:/DEEPCODE/CLAUDE CODE/claude.exe"
size = os.path.getsize(path)

# .bun section: at offset 0x4fca600, size 166.5MB
BUN_OFF = 0x4fca600
BUN_SIZE = 166.5 * 1024 * 1024

print(f"Reading .bun section at offset 0x{BUN_OFF:x}, size ~{BUN_SIZE/1024/1024:.1f} MB")

with open(path, 'rb') as f:
    f.seek(BUN_OFF)
    # Read first 64KB for header analysis
    header = f.read(65536)
    
    # First 256 bytes hex dump
    print(f"\n=== .bun section header (first 256 bytes) ===")
    for off in range(0, 256, 16):
        hex_str = ' '.join(f'{b:02x}' for b in header[off:off+16])
        ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in header[off:off+16])
        print(f"  {off:04x}: {hex_str}  |{ascii_str}|")
    
    # Check for bun header magic
    print(f"\n=== Magic/Signature detection ===")
    magics = [
        (b'Bun\x00', 'Bun header'), (b'bun\x00', 'bun header'),
        (b'\x00\x00\x00\x00', 'Null header'), (b'BUNL', 'BUNL magic'),
        (b'FSp\x00', 'FSp magic'), (b'JSC\x00', 'JSC magic'),
        (b'\xef\xbb\xbf', 'UTF-8 BOM'), (b'#!/', 'Shebang'),
        (b'require', 'Node require'), (b'\x1f\x8b', 'GZip'),
        (b'\x1f\x9d', 'Compress'), (b'\xfd7zXZ', 'XZ'),
        (b'PK\x03\x04', 'ZIP'), (b'BZh', 'BZip2'),
        (b'\x89PNG', 'PNG'),
    ]
    for sig, name in magics:
        if sig in header[:256]:
            idx = header.index(sig)
            print(f"  Found '{name}' at offset 0x{idx:x}")
    
    # Find first non-null offset
    first_non_null = 0
    for i, b in enumerate(header):
        if b != 0:
            first_non_null = i
            break
    print(f"  First non-null byte at offset 0x{first_non_null:x}")
    
    if first_non_null < 16:
        print("  --> Section starts with non-null data immediately")
    else:
        print(f"  --> Section has {first_non_null} bytes of null padding")
    
    # Search for string patterns in .bun
    print(f"\n=== .bun section string search (first 2MB) ===")
    f.seek(BUN_OFF)
    data = f.read(2 * 1024 * 1024)
    
    # Extract all strings
    current = b""
    strings = []
    for i, b in enumerate(data):
        if 32 <= b <= 126:
            current += bytes([b])
        else:
            if len(current) >= 8:
                strings.append((i - len(current), current.decode('ascii', errors='replace')))
            current = b""
    
    # Filter interesting strings
    print(f"Total strings (len>=8): {len(strings)}")
    
    keywords = [
        'package.json', 'node_modules', '.js', '.ts', '.json',
        'require(', 'import ', 'export ', 'module.exports',
        'claude', 'CLAUDE', 'anthropic', 'ANTHROPIC',
        'http', 'https', 'api.', 'server', 'config',
        'function ', 'class ', 'const ', 'var ', 'let ',
        'Error', 'error', 'Warning', 'warning',
        '/usr', '/tmp', '/home', 'C:\\', 'D:\\',
        'process.env', 'process.cwd',
        '.exe', '.dll', '.node',
    ]
    
    interesting = []
    for offset, s in strings:
        s_lower = s.lower()
        for kw in keywords:
            if kw.lower() in s_lower:
                interesting.append((offset, s, kw))
                break
    
    print(f"Interesting strings: {len(interesting)}")
    shown = 0
    seen_categories = set()
    for offset, s, kw in interesting[:80]:
        if shown >= 50: break
        # Deduplicate similar strings
        cat = kw
        if cat in seen_categories and shown > 10:
            continue
        seen_categories.add(cat)
        
        display = s[:120] + ('...' if len(s) > 120 else '')
        print(f"  [{kw:15s}] +0x{offset:x}: {display}")
        shown += 1
    
    # Check overall character distribution (code vs data vs strings)
    types = {'null': 0, 'ascii': 0, 'high': 0, 'other': 0}
    for b in data:
        if b == 0: types['null'] += 1
        elif 32 <= b <= 126: types['ascii'] += 1
        elif b >= 128: types['high'] += 1
        else: types['other'] += 1
    
    total = len(data)
    print(f"\n=== Data type distribution (first 2MB) ===")
    for k, v in types.items():
        print(f"  {k}: {v} ({v/total*100:.1f}%)")
    
    # Check for V8 bytecode markers
    print(f"\n=== V8 bytecode markers ===")
    v8_markers = [
        (b'BytecodeArray', 'BytecodeArray string'),
        (b'SourceHash', 'SourceHash string'),
        (b'\x1b\x00\x00\x00', 'V8 magic 0x1b'),
        (b'\x07\x00\x00\x00', 'V8 magic 0x07'),
    ]
    for sig, name in v8_markers:
        if sig in data:
            positions = []
            pos = -1
            while True:
                pos = data.find(sig, pos+1)
                if pos == -1: break
                positions.append(pos)
            print(f"  '{name}': found {len(positions)} times (first at +0x{positions[0]:x})")

print("\nDone.")
