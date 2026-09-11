#!/usr/bin/env python3
"""Analyze claude.exe PE structure"""
import struct, os, math

path = "F:/DEEPCODE/CLAUDE CODE/claude.exe"
size = os.path.getsize(path)
print(f"File: {size} bytes ({size/1024/1024:.1f} MB)")

with open(path, 'rb') as f:
    dos = f.read(64)
    pe_off = struct.unpack('<I', dos[0x3c:0x40])[0]
    f.seek(pe_off)
    assert f.read(4) == b'PE\x00\x00', "Not PE"
    
    hdr = f.read(20)
    machine, nsec, ts = struct.unpack('<HHI', hdr[:8])
    opt_sz = struct.unpack('<H', hdr[16:18])[0]
    print(f"PE: {nsec} sections, timestamp={ts}")
    
    # Section table
    sec_off = pe_off + 4 + 20 + opt_sz
    f.seek(sec_off)
    sections = []
    for i in range(nsec):
        raw = f.read(40)
        name = raw[:8].rstrip(b'\x00').decode('ascii', errors='replace')
        if not name.strip(): name = f"section_{i}"
        vsize, va, rsize, roff = struct.unpack('<IIII', raw[8:24])
        char = struct.unpack('<I', raw[36:40])[0]
        sections.append({'name': name, 'vsize': vsize, 'va': va, 'rsize': rsize, 'roff': roff, 'char': char})
        
        flags = f"EXE={'Y' if char&0x20000000 else 'N'} CODE={'Y' if char&0x20 else 'N'} INIT={'Y' if char&0x40 else 'N'}"
        ratio = rsize/vsize*100 if vsize else 0
        print(f"  [{i:2d}] '{name}' vsize={vsize/1024/1024:.1f}MB va=0x{va:x} rsize={rsize/1024/1024:.1f}MB roff=0x{roff:x} ratio={ratio:.0f}% {flags}")
        
        # Entropy
        if rsize > 100:
            f2 = open(path, 'rb')
            f2.seek(roff)
            raw_data = f2.read(min(rsize, 5*1024*1024))
            f2.close()
            freq = [0]*256
            for b in raw_data: freq[b] += 1
            ent = -sum((c/len(raw_data))*math.log2(c/len(raw_data)) for c in freq if c>0)
            print(f"       entropy={ent:.3f}")
    
    # Check for signatures
    f.seek(0)
    d2 = f.read(min(size, 2*1024*1024))
    for sig_name, sig_bytes in [("UPX!",b"UPX!"),("MPRESS",b"MPRESS"),("VMProtect",b"VMProtect"),
                                ("node_modules",b"node_modules"),("WebKit",b"WebKit"),("Bun",b"Bun"),
                                ("JavaScriptCore",b"JavaScriptCore"),("Electron",b"Electron"),
                                ("node:",b"node:"),("require(",b"require(")]:
        if sig_bytes in d2:
            idx = d2.index(sig_bytes)
            print(f"Found '{sig_name}' at offset 0x{idx:x}")

    # Section 0 analysis
    sec0 = sections[0]
    if sec0['rsize'] > 0 and sec0['roff'] > 0:
        f.seek(sec0['roff'])
        s0_data = f.read(min(4096, sec0['rsize']))
        printable = sum(1 for b in s0_data if 32 <= b <= 126)
        print(f"\n=== Section 0 '{sec0['name']}' first 4KB ===")
        print(f"Printable ASCII: {printable}/4096 ({printable/4096*100:.1f}%)")
        
        # Hex dump
        for off in range(0, min(len(s0_data), 256), 16):
            hex_str = ' '.join(f'{b:02x}' for b in s0_data[off:off+16])
            ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in s0_data[off:off+16])
            if any(b != 0 for b in s0_data[off:off+16]):
                print(f"  {off:04x}: {hex_str}  |{ascii_str}|")
        
        # Section 0 string extraction
        f.seek(sec0['roff'])
        s0_full = f.read(min(sec0['rsize'], 2*1024*1024))
        
        current = b""
        strings = []
        for i, b in enumerate(s0_full):
            if 32 <= b <= 126:
                current += bytes([b])
            else:
                if len(current) >= 6:
                    strings.append((sec0['roff'] + i - len(current), current.decode('ascii', errors='replace')))
                current = b""
        if current and len(current) >= 6:
            strings.append((sec0['roff'] + len(s0_full) - len(current), current.decode('ascii', errors='replace')))
        
        print(f"\n=== Section 0 Strings (total: {len(strings)}, showing interesting ones) ===")
        keywords = ['claude', 'node', 'api', 'http', 'server', 'config', 'agent', 'tool',
                    'permission', 'session', 'version', 'module', 'require', 'export',
                    'function', 'process', 'path', '.js', '.ts', '.json', '.exe',
                    'error', 'import', 'class', 'const ', 'var ', 'let ']
        shown = 0
        for offset, s in strings:
            if shown >= 40: break
            s_lower = s.lower()
            for kw in keywords:
                if kw in s_lower:
                    display = s[:120] + ('...' if len(s) > 120 else '')
                    print(f"  0x{offset:x}: [{kw}] {display}")
                    shown += 1
                    break

    # Check all sections for JS content
    print(f"\n=== JS/Node content search in all sections ===")
    for sec in sections:
        if sec['rsize'] < 1000: continue
        f.seek(sec['roff'])
        data = f.read(min(sec['rsize'], 2*1024*1024))
        
        # Count typical JS patterns
        js_indicators = ['function', 'require', 'module', 'import', 'export', './', '../', '.js']
        counts = {k: data.lower().count(k.encode()) for k in js_indicators}
        if sum(counts.values()) > 10:
            print(f"  Section '{sec['name']}': JS indicators = {counts}")
        
        # Check for PE/DLL/MZ embedded
        if b'MZ' in data:
            idx = data.index(b'MZ')
            print(f"  Section '{sec['name']}': Embedded MZ at offset {idx}")
        if b'PE\x00\x00' in data:
            idx = data.index(b'PE\x00\x00')
            print(f"  Section '{sec['name']}': Embedded PE at offset {idx}")

print("\nDone.")
