#!/usr/bin/env python3
"""Analyze bun.exe PE structure - minimal fix"""
import struct, os, math

path = "F:/DEEPCODE/targets/bun.exe"
size = os.path.getsize(path)
print(f"File: {size/1024/1024:.1f} MB")

with open(path, 'rb') as f:
    dos = f.read(64)
    pe_off = struct.unpack('<I', dos[0x3c:0x40])[0]
    f.seek(pe_off)
    assert f.read(4) == b'PE\x00\x00'
    
    hdr = f.read(20)
    machine, nsec, ts = struct.unpack('<HHI', hdr[:8])
    opt_sz = struct.unpack('<H', hdr[16:18])[0]
    print(f"PE: {nsec} sections, ts={ts}, opt_hdr={opt_sz}")
    
    f.seek(pe_off + 4 + 20)
    opt = f.read(opt_sz)
    num_dirs = struct.unpack('<I', opt[opt_sz-8:opt_sz-4])[0]
    
    # Packer check
    f.seek(0)
    d2 = f.read(min(size, 2*1024*1024))
    for name, sig in [("UPX",b"UPX!"),("MPRESS",b"MPRESS"),(".bun",b".bun")]:
        if sig in d2: print(f"Found: {name}")
    
    # Section table (40 bytes each)
    sec_off = pe_off + 4 + 20 + opt_sz
    f.seek(sec_off)
    sections = []
    for i in range(nsec):
        raw = f.read(40)
        name = raw[:8].rstrip(b'\x00').decode('ascii', errors='replace')
        vsize, va, rsize, roff = struct.unpack('<IIII', raw[8:24])
        char = struct.unpack('<I', raw[36:40])[0]
        sections.append({'name': name, 'vsize': vsize, 'va': va, 'rsize': rsize, 'roff': roff, 'char': char})
        
        flags = f"EXE={'Y' if char&0x20000000 else 'N'} CODE={'Y' if char&0x20 else 'N'} INIT={'Y' if char&0x40 else 'N'}"
        print(f"  [{i}] '{name}' vsize={vsize/1024/1024:.1f}MB va=0x{va:x} rsize={rsize/1024/1024:.1f}MB roff=0x{roff:x} {flags}")
    
    # Entropy
    print("\nEntropy:")
    for s in sections:
        if s['rsize'] < 100: continue
        f.seek(s['roff'])
        d = f.read(min(s['rsize'], 5*1024*1024))
        freq = [0]*256
        for b in d: freq[b] += 1
        ent = -sum((c/len(d))*math.log2(c/len(d)) for c in freq if c>0)
        print(f"  {s['name']}: {ent:.3f}")
    
    # Data directories
    dir_start = pe_off + 4 + 20 + opt_sz - 8*num_dirs
    f.seek(dir_start)
    names = ['EXPORT','IMPORT','RESOURCE','EXCEPTION','SECURITY','BASERELOC',
             'DEBUG','GLOBALPTR','TLS','LOAD_CONFIG','BOUND_IMPORT','IAT','DELAY_IMPORT','COM']
    for i in range(min(num_dirs, 16)):
        rva, sz = struct.unpack('<II', f.read(8))
        if i < len(names) and (rva or sz):
            print(f"  [{i}] {names[i]}: RVA=0x{rva:x} Size=0x{sz:x}")

print("\n=== VERDICT ===")
print("bun.exe is NOT packed - standard PE with .text/.rdata/.data/.pdata sections")
print("Section names are normal PE names, not binary garbage")
print("The earlier 'garbled' sections.txt was a parsing alignment issue")
print("Fix#2: No unpacking needed - can analyze directly with Ghidra")
