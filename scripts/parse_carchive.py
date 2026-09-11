import struct
filepath = r'F:\DEEPCODE\LAOWU SHARE\AI逆向自动\IDA Pro 9.2.250908\initIDA92.exe'
data = open(filepath, 'rb').read()

# Scan all MEI occurrences with more context
pos = 0
matches = []
while True:
    idx = data.find(b'MEI', pos)
    if idx < 0:
        break
    # Get 64 bytes context
    ctx = data[idx:idx+64]
    matches.append((idx, ctx))
    pos = idx + 1

for idx, ctx in matches:
    magic_ver = ctx[3:7].hex()  # version bytes after MEI
    py_ver_len = ctx[7]
    py_ver = ""
    if 0 < py_ver_len < 30 and idx + 8 + py_ver_len < len(data):
        py_ver = data[idx+8:idx+8+py_ver_len].decode('ascii', errors='replace')
    
    print(f"\n=== CArchive @ 0x{idx:08x} (file end at 0x{len(data):08x}, offset from end: {len(data)-idx}) ===")
    print(f"  Magic version bytes: {magic_ver}")
    print(f"  Python version: {py_ver}")
    print(f"  Hex dump:")
    for i in range(0, min(64, len(ctx)), 16):
        line = ' '.join(f'{b:02x}' for b in ctx[i:i+16])
        print(f"    {line}")
