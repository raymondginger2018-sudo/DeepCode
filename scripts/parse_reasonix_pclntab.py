#!/usr/bin/env python3
"""解析 reasonix.exe 的 Go pclntab，恢复函数名 -> 地址映射。"""
import struct
import sys

path = r'C:/Users/raymo/AppData/Roaming/npm/node_modules/reasonix/node_modules/@reasonix/cli-win32-x64/bin/reasonix.exe'

with open(path, 'rb') as f:
    data = f.read()

print(f"文件大小: {len(data)} bytes")

# 扫描 pclntab 魔数 0xFFFFFFF0 (f0 ff ff ff)
candidates = []
idx = 0
while True:
    idx = data.find(b'\xf0\xff\xff\xff', idx)
    if idx == -1:
        break
    candidates.append(idx)
    idx += 4

print(f"找到 {len(candidates)} 个 f0ffffff 魔数候选")

def check_header(off):
    if off + 72 > len(data):
        return None
    magic = struct.unpack_from('<I', data, off)[0]
    pad1, pad2 = data[off+4], data[off+5]
    minLC, ptrSize = data[off+6], data[off+7]
    if magic != 0xFFFFFFF0 or pad1 != 0 or pad2 != 0:
        return None
    nfunc = struct.unpack_from('<q', data, off+8)[0]
    nfiles = struct.unpack_from('<Q', data, off+16)[0]
    textStart = struct.unpack_from('<Q', data, off+24)[0]
    funcnameOff = struct.unpack_from('<Q', data, off+32)[0]
    cuOff = struct.unpack_from('<Q', data, off+40)[0]
    filetabOff = struct.unpack_from('<Q', data, off+48)[0]
    pctabOff = struct.unpack_from('<Q', data, off+56)[0]
    pclnOff = struct.unpack_from('<Q', data, off+64)[0]
    if not (0 < nfunc < 200000):
        return None
    if not (0 < nfiles < 100000):
        return None
    if ptrSize not in (4, 8):
        return None
    if minLC not in (1, 2, 4, 8):
        return None
    return dict(off=off, nfunc=nfunc, nfiles=nfiles, textStart=textStart,
                funcnameOff=funcnameOff, cuOff=cuOff, filetabOff=filetabOff,
                pctabOff=pctabOff, pclnOff=pclnOff, minLC=minLC, ptrSize=ptrSize)

valid = []
for c in candidates:
    h = check_header(c)
    if h:
        valid.append(h)

print(f"合法 pcHeader 候选: {len(valid)}")
for h in valid[:10]:
    print(f"  offset=0x{h['off']:x} nfunc={h['nfunc']} nfiles={h['nfiles']} "
          f"textStart=0x{h['textStart']:x} funcnameOff=0x{h['funcnameOff']:x} "
          f"pclnOff=0x{h['pclnOff']:x} minLC={h['minLC']} ptrSize={h['ptrSize']}")
