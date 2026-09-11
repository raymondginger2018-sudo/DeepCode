#!/usr/bin/env python3
"""解码 reasonix.exe DeepSeek 定价表 double + 关键字符串。"""
import struct

path = r'C:/Users/raymo/AppData/Roaming/npm/node_modules/reasonix/node_modules/@reasonix/cli-win32-x64/bin/reasonix.exe'
with open(path, 'rb') as f:
    data = f.read()


def va_to_off(va):
    if 0x140000000 <= va < 0x1414c2000:   # .text
        return va - 0x140001000 + 0x600
    if 0x1414c2000 <= va < 0x143092000:   # .rdata
        return va - 0x1414c2000 + 0x14c0c00
    if 0x143092000 <= va < 0x145490a5f:   # .data
        return va - 0x143092000 + 0x3090a00
    return None


def cstr(va, maxlen=80):
    off = va_to_off(va)
    if off is None:
        return f'<OOR 0x{va:x}>'
    s = data[off:off + maxlen].split(b'\x00')[0]
    return s.decode('utf-8', 'replace')


print('=== DeepSeekV4PricesForCurrency 价格 double ===')
prices = {
    'flash_CNY[0]': 0x3f947ae147ae147b,
    'flash_CNY[1]': 0x3ff0000000000000,
    'flash_CNY[2]': 0x4000000000000000,
    'pro_CNY[0]':   0x3f9999999999999a,
    'pro_CNY[1]':   0x4008000000000000,
    'pro_CNY[2]':   0x4018000000000000,
    'flash_USD[0]': 0x3f66f0068db8bac7,
    'flash_USD[1]': 0x3fc1eb851eb851ec,
    'flash_USD[2]': 0x3fd1eb851eb851ec,
    'pro_USD[0]':   0x3f6db22d0e560419,
    'pro_USD[1]':   0x3fdbd70a3d70a3d7,
    'pro_USD[2]':   0x3febd70a3d70a3d7,
}
for k, v in prices.items():
    d = struct.unpack('<d', struct.pack('<Q', v))[0]
    print(f'  {k:14s} = {d:.10f}')

print()
print('=== 货币代码字符串 ===')
print('  0x141b71294:', repr(cstr(0x141b71294, 6)))
print('  0x141b71297:', repr(cstr(0x141b71297, 6)))

print()
print('=== 模型名常量 ===')
print('  DAT_141b94c18 (17B):', repr(cstr(0x141b94c18, 20)))
print('  DAT_141b8eb3e (15B):', repr(cstr(0x141b8eb3e, 20)))

print()
print('=== lazySpawn / 风暴破环器字符串 ===')
print('  DAT_141b77e93 (7B):', repr(cstr(0x141b77e93, 7)))
print('  DAT_141ba2778 (21B):', repr(cstr(0x141ba2778, 21)))
print('  DAT_141c31191 (60B):', repr(cstr(0x141c31191, 60)))
print('  DAT_141c2890c (90B):', repr(cstr(0x141c2890c, 90)))
print('  DAT_141bbf1bc (31B):', repr(cstr(0x141bbf1bc, 31)))
