import sys
data = open(r'F:\DEEPCODE\LAOWU SHARE\AI逆向自动\IDA Pro 9.2.250908\initIDA92.exe', 'rb').read()
# Find all 'MEI' occurrences
pos = 0
while True:
    idx = data.find(b'MEI', pos)
    if idx < 0:
        break
    ctx = data[idx:idx+16]
    hex_str = ' '.join(f'{b:02x}' for b in ctx)
    print(f'0x{idx:08x}: {hex_str}')
    pos = idx + 1
