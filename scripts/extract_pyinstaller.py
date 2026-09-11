#!/usr/bin/env python3
"""
Proper PyInstaller CArchive extractor.
Handles the PyInstaller 5+ CArchive format.
"""
import struct
import zlib
import io
import sys
import marshal
from pathlib import Path

MAGIC_SIZE = 8

class CArchiveReader:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.filesize = len(self.data)
    
    def find_archive(self):
        """Find CArchive cookie in data."""
        for off in range(len(self.data) - 24, 0, -1):
            if self.data[off:off+3] == b'MEI':
                # Check magic bytes: MEI\0xx\0xx\0xx or MEI\0x\0xx\0xx
                if self.data[off+4:off+6] == b'\x0b\x0a' or (self.data[off+4] in (0x0b, 0x0c, 0x0d) and self.data[off+5:off+7] == b'\x0a\x0b'):
                    return off
        return -1
    
    def parse_toc(self, offset):
        """Parse CArchive TOC entries."""
        pos = offset
        magic = self.data[pos:pos+MAGIC_SIZE]
        print(f"  MAGIC: {magic.hex()}")
        pos += MAGIC_SIZE
        
        # Read python version string length
        pyver_len = self.data[pos]
        pos += 1
        
        if pyver_len > 0 and pyver_len < 30:
            pyver = self.data[pos:pos+pyver_len]
            print(f"  Python version: {pyver}")
            pos += pyver_len
        
        # Read TOC length (pack_toc)
        toc_len_raw = self.data[pos:pos+8]
        # Try different unpack methods
        try:
            toc_len = int.from_bytes(toc_len_raw, 'little', signed=False)
            # But if it's a huge number, maybe it's the packed format
        except:
            toc_len = struct.unpack('<Q', toc_len_raw)[0]
        
        print(f"  TOC length (raw): {toc_len_raw.hex()} = {toc_len}")
        pos += 8
        
        # Parse TOC entries
        entries = []
        toc_end = pos + toc_len
        # toc_len might be bytes or entry count - let's try both
        
        while pos < self.filesize and pos < offset + 500000:
            try:
                entry_start = pos
                name_len = struct.unpack('<i', self.data[pos:pos+4])[0]
                pos += 4
                if name_len <= 0 or name_len > 4096 or pos + name_len > self.filesize:
                    if len(entries) == 0:
                        # Maybe toc_len was in bytes, try using it as byte count
                        pos = entry_start
                        break
                    else:
                        break
                
                name = self.data[pos:pos+name_len].rstrip(b'\x00')
                pos += name_len
                
                packed_size = struct.unpack('<i', self.data[pos:pos+4])[0]
                pos += 4
                unpacked_size = struct.unpack('<i', self.data[pos:pos+4])[0]
                pos += 4
                comp_flag = struct.unpack('<i', self.data[pos:pos+4])[0]
                pos += 4
                type_flag = struct.unpack('<i', self.data[pos:pos+4])[0]
                pos += 4
                
                entries.append({
                    'name': name.decode('utf-8', errors='replace'),
                    'packed_size': packed_size,
                    'unpacked_size': unpacked_size,
                    'compression': comp_flag,
                    'type': chr(type_flag) if 32 <= type_flag < 127 else f'?',
                })
            except Exception as e:
                if len(entries) == 0:
                    # Try with toc_len as byte count
                    break
                else:
                    print(f"  [!] Parse error at entry {len(entries)}: {e}")
                    break
            
            if pos >= toc_end:
                break
        
        return entries, pos
    
    def extract(self, out_dir):
        off = self.find_archive()
        if off < 0:
            print(f"[!] No CArchive found in {self.filesize} byte file")
            return
        
        print(f"[+] CArchive found at 0x{off:08x} (end offset: {self.filesize - off})")
        
        entries, payload_start = self.parse_toc(off)
        
        if not entries:
            print("[!] No TOC entries parsed. Trying alternate approach...")
            return
        
        print(f"\n[+] Found {len(entries)} TOC entries")
        
        # Show summary
        pycs = [e for e in entries if e['name'].endswith('.pyc')]
        dlls = [e for e in entries if e['name'].lower().endswith(('.dll','.pyd','.so'))]
        dirs = [e for e in entries if e['name'] in ('.','..')]
        others = [e for e in entries if e not in pycs and e not in dlls and e not in dirs]
        
        print(f"  .pyc: {len(pycs)}")
        print(f"  .dll/.pyd: {len(dlls)}")
        print(f"  dirs: {len(dirs)}")
        print(f"  other: {len(others)}")
        
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        
        # Extract files
        # Data goes backwards from the archive start
        # Actually for PyInstaller >= 5, data starts after the CArchive TOC
        data_pos = payload_start
        
        print(f"\n[*] Extracting to {out}...")
        extracted = 0
        for entry in entries:
            if entry['name'] in ('.', '..'):
                continue
            try:
                raw = self.data[data_pos:data_pos+entry['packed_size']]
                if entry['compression'] == 1 and entry['packed_size'] != entry['unpacked_size']:
                    content = zlib.decompress(raw)
                else:
                    content = raw
                
                fpath = out / entry['name'].replace('\\', '/')
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_bytes(content)
                extracted += 1
                data_pos += entry['packed_size']
            except Exception as e:
                print(f"  [!] {entry['name']}: {e}")
                data_pos += entry['packed_size']
        
        print(f"\n[+] Extracted {extracted}/{len(entries)-len(dirs)} files")
        
        # If any .pyc files, try decompile first one
        if pycs:
            sample = out / pycs[0]['name']
            if sample.exists():
                print(f"\n[*] Sample .pyc: {pycs[0]['name']} ({sample.stat().st_size} bytes)")
        return out


if __name__ == '__main__':
    reader = CArchiveReader(
        r'F:\DEEPCODE\LAOWU SHARE\AI逆向自动\IDA Pro 9.2.250908\initIDA92.exe'
    )
    reader.extract(
        r'F:\DEEPCODE\LAOWU SHARE\AI逆向自动\IDA Pro 9.2.250908\initIDA92_extracted'
    )
