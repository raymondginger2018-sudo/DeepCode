#!/usr/bin/env python3
"""第五阶段字符串解码: apply* 守卫相关常量。"""
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

def cstr(va, maxlen=120):
    off = va_to_off(va)
    if off is None:
        return f'<OOR 0x{va:x}>'
    s = data[off:off + maxlen].split(b'\x00')[0]
    return s.decode('utf-8', 'replace')

targets = [
    # (VA, 长度, 标签)
    (0x141b8009d, 0x40, 'applyMutationDependencyBarrier dep target A'),
    (0x141b7819c, 0x40, 'applyMutationDependencyBarrier dep src A'),
    (0x141b82d80, 0x40, 'applyMutationDependencyBarrier dep target B'),
    (0x141c264ed, 0xa0, 'applyMutationDependencyBarrier log msg'),
    (0x141bc1227, 0x20, 'applyRecoveryAndPermission err'),
    (0x141b7df25, 0x20, 'applyRecoveryAndPermission prefix'),
    (0x141b7df1c, 0x20, 'applyPlanModeAndProxy fmt'),
    (0x141c1c576, 0x68, 'applyPlanModeAndProxy err fmt'),
    (0x141c3911e, 0x1fd, 'applyDeliveryPolicyGates big msg'),
    (0x141b926ae, 0x10, 'applyRecoveryAndPermission fmt1'),
    (0x141b8359f, 0x20, 'applyRecoveryAndPermission fmt2'),
    (0x141b8fa11, 0x20, 'applyToolResultMaintenanceView ref str'),
    (0x141badf05, 0x20, 'applyPlanModeAndProxy err str1'),
    (0x141bc6a1a, 0x30, 'applyPlanModeAndProxy err str2'),
    (0x141bb9167, 0x20, 'applyRecoveryAndPermission fmt3'),
]
for va, ln, label in targets:
    print(f'{label:48s} @ 0x{va:x}: {cstr(va, ln)!r}')
