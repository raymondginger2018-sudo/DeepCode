#!/usr/bin/env python3
"""从 reasonix.exe 的 Go pclntab 完整恢复函数名 → 地址映射。

关键地址（已通过多轮验证）:
  pcHeader      文件偏移 0x0205a1a0, VA 0x14205b5a0
  funcnametab   VA 0x14205b5e8 (pcHeader+0x48), 长度 0x3fc118
  ftab/pclntable VA 0x14284ca80 (moduledata+0x80)
  moduledata    文件偏移 0x03091e00, .data 段
  moduledata.text = 0x140001000
  nfunc = 59144, nfiles = 2394
"""
import struct
import os
import sys

TARGET = r'C:/Users/raymo/AppData/Roaming/npm/node_modules/reasonix/node_modules/@reasonix/cli-win32-x64/bin/reasonix.exe'
OUT_CSV = r'F:/DEEPCODE/scripts/reasonix_symbols.csv'
OUT_TXT = r'F:/DEEPCODE/scripts/reasonix_symbols.txt'

# 关键常量
PC_HEADER_OFF = 0x0205a1a0
MODULEDATA_OFF = 0x03091e00
FUNCNAMETAB_VA = 0x14205b5e8
FTAB_VA = 0x14284ca80
TEXT_VA = 0x140001000  # moduledata.text

# 段映射: VA -> 文件偏移
def va_to_off(va):
    if 0x140000000 <= va < 0x1414c2000:   # .text
        return va - 0x140001000 + 0x600
    if 0x1414c2000 <= va < 0x143092000:   # .rdata
        return va - 0x1414c2000 + 0x14c0c00
    if 0x143092000 <= va < 0x145490a5f:   # .data
        return va - 0x143092000 + 0x3090a00
    return None

def main():
    with open(TARGET, 'rb') as f:
        data = f.read()
    print(f'文件大小: {len(data)} bytes')

    # 1. pcHeader
    ph = data[PC_HEADER_OFF:PC_HEADER_OFF+72]
    magic, = struct.unpack_from('<I', ph, 0)
    pad1, pad2 = ph[4], ph[5]
    minLC, ptrSize = ph[6], ph[7]
    nfunc, = struct.unpack_from('<q', ph, 8)
    nfiles, = struct.unpack_from('<Q', ph, 16)
    textStart, = struct.unpack_from('<Q', ph, 24)
    funcnameOff, = struct.unpack_from('<Q', ph, 32)
    print(f'pcHeader: magic=0x{magic:08x} pad={pad1},{pad2} minLC={minLC} ptrSize={ptrSize} '
          f'nfunc={nfunc} nfiles={nfiles} textStart=0x{textStart:x}')

    # 2. 表位置
    funcnametab_off = va_to_off(FUNCNAMETAB_VA)
    ftab_off = va_to_off(FTAB_VA)
    print(f'funcnametab 文件偏移 0x{funcnametab_off:x}, ftab 文件偏移 0x{ftab_off:x}')

    # 3. 遍历 functab (nfunc+1 条, 每条 8 字节)
    symbols = []  # (name, va)
    bad = 0
    for i in range(nfunc + 1):
        e = data[ftab_off + i*8 : ftab_off + i*8 + 8]
        entryoff, funcoff = struct.unpack('<II', e)
        # _func 位于 pclntable + funcoff (pclntable 起点 = FTAB_VA)
        func_va = FTAB_VA + funcoff
        fo = va_to_off(func_va)
        if fo is None or fo + 8 > len(data):
            bad += 1
            continue
        entry_off_field, nameoff = struct.unpack_from('<iI', data, fo)
        # 函数真实地址
        func_addr = TEXT_VA + entryoff
        # 函数名: funcnametab + nameoff
        if nameoff >= 0:
            no = funcnametab_off + nameoff
            end = data.find(b'\x00', no)
            if end == -1 or end - no > 512:
                name = f'<nameoff_{nameoff}>'
                bad += 1
            else:
                name = data[no:end].decode('utf-8', 'replace')
        else:
            name = f'<neg_{nameoff}>'
            bad += 1
        symbols.append((name, func_addr))

    print(f'成功解析 {len(symbols)} 个函数, 异常 {bad}')

    # 4. 输出 CSV
    with open(OUT_CSV, 'w', encoding='utf-8') as f:
        f.write('name,va,file_offset\n')
        for name, va in symbols:
            fo = va_to_off(va)
            f.write(f'{name},0x{va:x},{0 if fo is None else fo}\n')

    # 5. 输出 TXT (人类可读)
    with open(OUT_TXT, 'w', encoding='utf-8') as f:
        f.write(f'# reasonix.exe 符号表 (Go pclntab 恢复)\n')
        f.write(f'# 共 {len(symbols)} 个函数, text=0x{TEXT_VA:x}, Go {magic:08x}\n')
        for name, va in symbols:
            f.write(f'0x{va:016x} {name}\n')

    print(f'CSV 已写入: {OUT_CSV}')
    print(f'TXT 已写入: {OUT_TXT}')

    # 6. 关键函数检索
    print('\n=== 关键函数 ===')
    keywords = ['main.main', 'main.runWithCrashCapture', 'responses.streamedCall',
                'main.init', 'reasonix', 'DeepSeek', 'anthropic', 'config.PluginEntry',
                'provider.APIError', 'cli', 'MCP', 'mcp']
    for kw in keywords:
        hits = [s for s in symbols if kw in s[0]]
        print(f'--- 包含 "{kw}" 的函数: {len(hits)} 个 ---')
        for name, va in hits[:8]:
            print(f'    0x{va:016x} {name}')
        if len(hits) > 8:
            print(f'    ... 共 {len(hits)} 个')

if __name__ == '__main__':
    main()
