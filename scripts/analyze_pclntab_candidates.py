#!/usr/bin/env python3
"""分析 reasonix.exe 中所有 f0ffffff 魔数候选，寻找被 Bun 修改过的 pcHeader。"""
import struct

path = r'C:/Users/raymo/AppData/Roaming/npm/node_modules/reasonix/node_modules/@reasonix/cli-win32-x64/bin/reasonix.exe'
with open(path, 'rb') as f:
    data = f.read()

print(f"文件大小: {len(data)} bytes")

cands = []
idx = 0
while True:
    idx = data.find(b'\xf0\xff\xff\xff', idx)
    if idx == -1:
        break
    cands.append(idx)
    idx += 4

print(f"候选总数: {len(cands)}")


def region(off):
    if off < 0x600:
        return "headers"
    if off < 0x14c0600:
        return ".text"
    if off < 0x3090a00:
        return ".rdata"
    if off < 0x3212200:
        return ".data"
    if off < 0x32ab200:
        return ".pdata"
    if off < 0x32ab400:
        return ".xdata"
    if off < 0x32aba00:
        return ".idata"
    if off < 0x3335600:
        return ".reloc"
    return "other"


from collections import Counter
dist = Counter(region(c) for c in cands)
print("分布:", dict(dist))

print("\n--- 前 60 个候选的 32 字节 ---")
for off in cands[:60]:
    chunk = data[off:off+32]
    hexs = chunk.hex(' ')
    magic = struct.unpack_from('<I', chunk, 0)[0]
    pad1, pad2 = chunk[4], chunk[5]
    minLC, ptrSize = chunk[6], chunk[7]
    nfunc = struct.unpack_from('<q', chunk, 8)[0]
    nfiles = struct.unpack_from('<Q', chunk, 16)[0] if len(chunk) >= 24 else 0
    flag = ""
    if magic == 0xFFFFFFF0 and minLC in (1, 2, 4, 8) and ptrSize in (4, 8):
        if 0 < nfunc < 200000 and 0 < nfiles < 100000:
            flag = "  <<< 疑似 pcHeader!"
    print(f"0x{off:08x} [{region(off):8s}] {hexs}  pad={pad1},{pad2} minLC={minLC} ptr={ptrSize} nfunc={nfunc} nfiles={nfiles}{flag}")

# 宽松校验：只要求 magic + minLC/ptrSize 合理，nfunc 在更宽范围
print("\n--- 宽松校验通过的候选 ---")
loose = []
for off in cands:
    if off + 72 > len(data):
        continue
    magic = struct.unpack_from('<I', data, off)[0]
    pad1, pad2 = data[off+4], data[off+5]
    minLC, ptrSize = data[off+6], data[off+7]
    if magic != 0xFFFFFFF0:
        continue
    if minLC not in (1, 2, 4, 8) or ptrSize not in (4, 8):
        continue
    nfunc = struct.unpack_from('<q', data, off+8)[0]
    nfiles = struct.unpack_from('<Q', data, off+16)[0]
    textStart = struct.unpack_from('<Q', data, off+24)[0]
    # 宽松范围
    if not (10 < nfunc < 500000):
        continue
    if not (1 < nfiles < 200000):
        continue
    loose.append((off, nfunc, nfiles, textStart, pad1, pad2, minLC, ptrSize))

print(f"宽松候选: {len(loose)}")
for off, nfunc, nfiles, ts, p1, p2, mlc, ps in loose[:30]:
    print(f"  0x{off:08x} nfunc={nfunc} nfiles={nfiles} textStart=0x{ts:x} pad={p1},{p2} minLC={mlc} ptr={ps}")
