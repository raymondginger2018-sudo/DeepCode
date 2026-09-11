#!/usr/bin/env python3
"""
PE 链接方式分析器 — 检测 Windows 可执行文件是否为全静态链接

用法:
  python pe_linkage_analyzer.py <exe/dll 路径>
  python pe_linkage_analyzer.py <路径> --json    # JSON 格式输出
  python pe_linkage_analyzer.py <路径> --verbose  # 显示导入函数详情

全静态链接特征:
  - 只依赖 ntdll.dll / kernel32.dll 等极少系统 DLL
  - Go / Rust / Nim 等语言编译产物
  - 无第三方 DLL 依赖 (如 msvcrt.dll, vcruntime*.dll)
"""

import struct
import sys
import os

IMAGE_DIRECTORY_ENTRY_IMPORT = 1
IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT = 13

# 系统白名单 DLL（全静态链接允许的）
SYSTEM_DLL_WHITELIST = {
    "ntdll.dll", "ntdll", "kernel32.dll", "kernel32",
    "kernelbase.dll", "kernelbase",
    "user32.dll", "user32",
    "gdi32.dll", "gdi32",
    "advapi32.dll", "advapi32",
    "ole32.dll", "ole32",
    "oleaut32.dll", "oleaut32",
    "shell32.dll", "shell32",
    "shlwapi.dll", "shlwapi",
    "comdlg32.dll", "comdlg32",
    "comctl32.dll", "comctl32",
    "ws2_32.dll", "ws2_32",
    "winhttp.dll", "winhttp",
    "bcrypt.dll", "bcrypt",
    "crypt32.dll", "crypt32",
    "wininet.dll", "wininet",
    "psapi.dll", "psapi",
    "iphlpapi.dll", "iphlpapi",
    "version.dll", "version",
    "secur32.dll", "secur32",
    "winmm.dll", "winmm",
    "setupapi.dll", "setupapi",
    "uxtheme.dll", "uxtheme",
    "dwmapi.dll", "dwmapi",
    "imm32.dll", "imm32",
    "rpcrt4.dll", "rpcrt4",
    "oleacc.dll", "oleacc",
    "mpr.dll", "mpr",
    "netapi32.dll", "netapi32",
    "dnsapi.dll", "dnsapi",
    "msi.dll", "msi",
    "wintrust.dll", "wintrust",
    "imagehlp.dll", "imagehlp",
    "dbghelp.dll", "dbghelp",
    "dbgeng.dll", "dbgeng",
    "userenv.dll", "userenv",
    "wtsapi32.dll", "wtsapi32",
    "winspool.drv", "winspool",
    "msimg32.dll", "msimg32",
    "mswsock.dll", "mswsock",
    "nsi.dll", "nsi",
    "fwpuclnt.dll", "fwpuclnt",
    "clbcatq.dll", "clbcatq",
    "propsys.dll", "propsys",
    "thumbcache.dll", "thumbcache",
}

# 第三方运行时 DLL（全静态链接不应包含这些）
THIRD_PARTY_RUNTIME = {
    "msvcrt.dll", "msvcp.dll", "msvcr.dll",
    "vcruntime.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "concrt140.dll",
    "ucrtbase.dll",
    "api-ms-win-crt-*.dll",
    "mscorlib.dll", "clr.dll",
}

# 编译器/语言特征字符串
LANG_FEATURES = [
    ("go1.", "Go"),
    ("go build", "Go"),
    ("runtime.", "Go"),        # Go runtime 引用
    ("goroutine", "Go"),
    ("main.main", "Go"),
    ("libc++", "C++ (Clang/LLVM)"),
    ("libstdc++", "C++ (GCC)"),
    ("__gxx_personality", "C++ (GCC)"),
    ("_GLOBAL__sub_I", "C++ (GCC/Clang)"),
    ("__clang__", "C (Clang)"),
    ("GCC:", "C/C++ (GCC)"),
    ("rustc", "Rust"),
    ("rust_begin_unwind", "Rust"),
    ("core::", "Rust"),
    ("_main", "Go (Plan 9)"),
    ("nim", "Nim"),
    ("NimMain", "Nim"),
    ("D main", "D语言"),
    ("Lua", "Lua (嵌入)"),
    ("python", "Python (嵌入)"),
    ("node::", "Node.js (嵌入)"),
    ("swift_", "Swift"),
    ("mono", "Mono/.NET"),
]

# ── PE 解析 ───────────────────────────────────────────────

def read_le(fmt, data, offset):
    size = struct.calcsize(fmt)
    return struct.unpack_from(fmt, data, offset)[0], offset + size


def read_pe(data: bytes) -> dict:
    """解析 PE 文件，返回导入表等信息"""
    result = {
        "file_size": len(data),
        "imports": [],          # [(dll_name, [functions])]
        "delay_imports": [],
        "sections": [],
        "compiler_hints": [],
    }

    # 验证 DOS header
    if data[:2] != b'MZ':
        raise ValueError("Not a valid PE file (no MZ header)")

    # 获取 PE header offset
    pe_offset = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe_offset:pe_offset+4] != b'PE\x00\x00':
        raise ValueError("Not a valid PE file (no PE signature)")

    offset = pe_offset + 4  # skip PE signature

    # 读取 FileHeader
    machine, offset = read_le('<H', data, offset)
    sections_count, offset = read_le('<H', data, offset)
    timestamp, offset = read_le('<I', data, offset)
    ptr_symtab, offset = read_le('<I', data, offset)
    num_syms, offset = read_le('<I', data, offset)
    opt_header_size, offset = read_le('<H', data, offset)
    characteristics, offset = read_le('<H', data, offset)
    
    result["machine"] = {0x8664: "x86-64", 0x14c: "x86-32", 0xaa64: "ARM64", 
                         0x1c0: "ARM", 0x1c4: "ARM Thumb"}.get(machine, f"0x{machine:x}")

    # Optional header
    magic, _ = read_le('<H', data, offset)
    is_pe32 = magic == 0x10b
    is_pe32plus = magic == 0x20b

    if is_pe32plus:
        opt_offset = offset
        # PE32+ 结构
        entry, offset = read_le('<H', data, offset + 16)  # AddressOfEntryPoint
        # 跳到 DataDirectory
        offset = opt_offset + 24 + 68 + 4 + 4 + 4 + 4 + 4 + 4  # PE32+ specific offsets
        data_dir_count, offset = read_le('<I', data, offset)
    elif is_pe32:
        opt_offset = offset
        offset = opt_offset + 16
        entry, offset = read_le('<H', data, offset)
        offset = opt_offset + 24 + 60 + 4 + 4 + 4 + 4 + 4 + 4  # PE32 specific
        data_dir_count, offset = read_le('<I', data, offset)
    else:
        raise ValueError("Unknown optional header magic")

    # DataDirectory
    data_dir_offset = offset
    # Read Import Directory
    import_dir_rva, _ = read_le('<I', data, data_dir_offset + IMAGE_DIRECTORY_ENTRY_IMPORT * 8)
    # Read Delay Import Directory
    delay_import_rva, _ = read_le('<I', data, data_dir_offset + IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT * 8)

    # Section headers
    sections_start = offset
    for i in range(sections_count):
        sec_offset = sections_start + i * 40
        name_raw = data[sec_offset:sec_offset+8]
        name = name_raw.split(b'\x00')[0].decode('ascii', errors='replace').strip()
        vsize = struct.unpack_from('<I', data, sec_offset + 8)[0]
        vaddr = struct.unpack_from('<I', data, sec_offset + 12)[0]
        rsize = struct.unpack_from('<I', data, sec_offset + 16)[0]
        raddr = struct.unpack_from('<I', data, sec_offset + 20)[0]
        result["sections"].append({
            "name": name if name else f"(section_{i})",
            "virtual_address": vaddr,
            "virtual_size": vsize,
            "raw_size": rsize,
            "raw_offset": raddr,
        })

    # 解析导入表
    if import_dir_rva:
        imports = _parse_imports(data, import_dir_rva, result["sections"])
        result["imports"] = imports

    # 解析延迟导入表
    if delay_import_rva:
        delay_imports = _parse_delay_imports(data, delay_import_rva, result["sections"])
        result["delay_imports"] = delay_imports

    # 检测编译器/语言特征
    result["compiler_hints"] = _detect_language(data)

    return result


def _rva_to_offset(rva: int, sections: list) -> int:
    """RVA 转文件偏移"""
    for sec in sections:
        if sec["virtual_address"] <= rva < sec["virtual_address"] + sec["virtual_size"]:
            if rva < sec["virtual_address"]:
                continue
            offset_in_sec = rva - sec["virtual_address"]
            if offset_in_sec < sec["raw_size"]:
                return sec["raw_offset"] + offset_in_sec
    return 0


def _parse_imports(data: bytes, import_rva: int, sections: list) -> list:
    """解析导入表"""
    imports = []
    offset = _rva_to_offset(import_rva, sections)
    if not offset:
        return imports

    while True:
        # IMAGE_IMPORT_DESCRIPTOR
        if offset + 20 > len(data):
            break
        ilt_rva = struct.unpack_from('<I', data, offset)[0]  # OriginalFirstThunk
        name_rva = struct.unpack_from('<I', data, offset + 4)[0]  # Name
        iat_rva = struct.unpack_from('<I', data, offset + 12)[0]  # FirstThunk
        if ilt_rva == 0 and name_rva == 0:
            break

        # 获取 DLL 名
        name_off = _rva_to_offset(name_rva, sections)
        if name_off and name_off < len(data):
            dll_name = data[name_off:data.index(b'\x00', name_off)].decode('ascii', errors='replace').lower()
        else:
            dll_name = f"unknown_{name_rva:x}"

        # 获取导入函数
        funcs = []
        thunk_rva = ilt_rva if ilt_rva else iat_rva
        thunk_off = _rva_to_offset(thunk_rva, sections)

        if thunk_off:
            is_64bit = machine_type_is_64bit(data[:2])
            while thunk_off + 8 <= len(data):
                if is_64bit:
                    thunk = struct.unpack_from('<Q', data, thunk_off)[0]
                    thunk_off += 8
                else:
                    thunk = struct.unpack_from('<I', data, thunk_off)[0]
                    thunk_off += 4

                if thunk == 0:
                    break
                if thunk & (1 << (63 if is_64bit else 31)):  # Ordinal import
                    ordinal = thunk & 0xffff
                    funcs.append(f"ordinal_{ordinal}")
                else:  # Name import
                    hint_off = _rva_to_offset(thunk & 0x7fffffffffffffff, sections)
                    if hint_off and hint_off + 2 < len(data):
                        import_name_start = hint_off + 2
                        import_name_end = data.index(b'\x00', import_name_start) if b'\x00' in data[import_name_start:import_name_start+256] else import_name_start + 1
                        if import_name_start < len(data):
                            fname = data[import_name_start:import_name_end].decode('ascii', errors='replace')
                            funcs.append(fname)

        imports.append((dll_name, funcs))
        offset += 20

    return imports


def _parse_delay_imports(data: bytes, delay_rva: int, sections: list) -> list:
    """解析延迟导入表"""
    imports = []
    offset = _rva_to_offset(delay_rva, sections)
    if not offset:
        return imports

    while True:
        if offset + 32 > len(data):
            break
        attrs = struct.unpack_from('<I', data, offset)[0]
        if attrs == 0:
            # 可能是最后一个条目
            name_rva = struct.unpack_from('<I', data, offset + 4)[0]
            if name_rva == 0:
                break
        
        name_rva = struct.unpack_from('<I', data, offset + 4)[0]
        mod_nname_rva = struct.unpack_from('<I', data, offset + 8)[0]
        iat_rva = struct.unpack_from('<I', data, offset + 16)[0]
        
        # Name of DLL
        name_off = _rva_to_offset(mod_nname_rva, sections)
        if name_off and name_off < len(data):
            try:
                end = data.index(b'\x00', name_off)
                dll_name = data[name_off:end].decode('ascii', errors='replace').lower()
                imports.append((dll_name, ["(delay)"]))
            except:
                pass
        offset += 32

    return imports


def machine_type_is_64bit(data_header: bytes) -> bool:
    """判断是否 64 位"""
    machine = struct.unpack_from('<H', data_header, 0)[0]
    return machine == 0x8664 or machine == 0xaa64


def _detect_language(data: bytes) -> list:
    """通过特征字符串检测编译器/语言"""
    hints = []
    found_features = set()

    # 在文件的前 2MB 和后 2MB 搜索
    search_regions = [data[:min(len(data), 2*1024*1024)]]
    if len(data) > 2*1024*1024:
        search_regions.append(data[-2*1024*1024:])

    for region in search_regions:
        for pattern, lang in LANG_FEATURES:
            if lang in found_features:
                continue
            if pattern.encode() in region:
                hints.append({"language": lang, "pattern": pattern, "confidence": "high"})
                found_features.add(lang)

    return hints


def analyze_linkage(result: dict) -> dict:
    """分析链接方式"""
    all_dlls = set()
    for dll_name, funcs in result.get("imports", []):
        all_dlls.add(dll_name.split(".")[0].lower())
    for dll_name, funcs in result.get("delay_imports", []):
        all_dlls.add(dll_name.split(".")[0].lower())

    # 区分系统 DLL 和第三方 DLL
    system_dlls = set()
    third_party_dlls = set()
    for dll in all_dlls:
        if dll in SYSTEM_DLL_WHITELIST or dll + ".dll" in SYSTEM_DLL_WHITELIST:
            system_dlls.add(dll)
        else:
            third_party_dlls.add(dll)

    # 判断是否为全静态链接
    # 全静态: 没有第三方 DLL 或只有系统白名单 DLL
    is_fully_static = len(third_party_dlls) == 0

    # 如果没有导入表或导入表为空，也可能是全静态
    has_imports = len(all_dlls) > 0

    # 检测运行时特征
    compiler_hints = result.get("compiler_hints", [])
    detected_langs = set(h["language"] for h in compiler_hints)
    has_go_runtime = "Go" in detected_langs
    has_cpp_runtime = any("C++" in l for l in detected_langs)
    has_rust = "Rust" in detected_langs
    has_nim = "Nim" in detected_langs

    # 如果有 Go/Rust 特征一般是全静态
    if has_go_runtime or has_rust or has_nim:
        is_fully_static = True

    # 判断编译器类型
    if has_go_runtime:
        compiler = "Go"
    elif has_rust:
        compiler = "Rust"
    elif has_nim:
        compiler = "Nim"
    elif has_cpp_runtime:
        compiler = "C/C++ (静态链接)"
    elif is_fully_static:
        compiler = "未知 (静态链接)"
    else:
        compiler = "C/C++ (动态链接)"

    return {
        "is_fully_static": is_fully_static,
        "compiler": compiler,
        "total_dll_dependencies": len(all_dlls),
        "system_dlls": sorted(system_dlls),
        "third_party_dlls": sorted(third_party_dlls),
        "has_imports": has_imports,
        "detected_features": [{ "language": h["language"], "pattern": h["pattern"] } for h in compiler_hints],
    }


def format_report(result: dict, verbose: bool = False) -> str:
    """格式化为可读报告"""
    linkage = analyze_linkage(result)
    lines = []

    lines.append(f"文件大小: {result['file_size'] / 1024:.0f} KB ({result['file_size'] / 1024 / 1024:.1f} MB)")
    lines.append(f"CPU 架构: {result.get('machine', '未知')}")
    lines.append("")

    # 链接分析
    lines.append("━" * 50)
    lines.append("链接分析")
    lines.append("━" * 50)
    
    if linkage["is_fully_static"]:
        lines.append(f"  ✅ 全静态链接 [{linkage['compiler']}]")
    else:
        lines.append("  ❌ 动态链接 (依赖第三方 DLL)")
    
    lines.append(f"  DLL 依赖数: {linkage['total_dll_dependencies']}")

    if linkage["system_dlls"]:
        lines.append(f"  系统 DLL: {', '.join(linkage['system_dlls'])}")
    if linkage["third_party_dlls"]:
        lines.append(f"  第三方 DLL: {', '.join(linkage['third_party_dlls'])}")
    
    # 编译器检测
    if linkage["detected_features"]:
        lines.append("")
        lines.append("━" * 50)
        lines.append("编译器/语言检测")
        lines.append("━" * 50)
        for f in linkage["detected_features"]:
            lines.append(f"  🏷 {f['language']} (特征: {f['pattern']})")

    # Section 信息
    lines.append("")
    lines.append("━" * 50)
    lines.append("Section 信息")
    lines.append("━" * 50)
    for sec in result.get("sections", []):
        if sec["raw_size"] > 0 or sec["virtual_size"] > 0:
            vsize = sec["virtual_size"]
            rsize = sec["raw_size"]
            unit = "MB" if vsize > 1024*1024 else "KB"
            val = vsize / (1024*1024 if unit == "MB" else 1024)
            rval = rsize / (1024*1024 if unit == "MB" else 1024)
            flag = " ⬅️ 最大" if vsize == max((s["virtual_size"] for s in result.get("sections", []) if s["raw_size"] > 0), default=0) else ""
            lines.append(f"  {sec['name']:>12}: VSize={val:.1f}{unit} RSize={rval:.1f}{unit}{flag}")

    # 导入详情
    if verbose and result.get("imports"):
        lines.append("")
        lines.append("━" * 50)
        lines.append("导入函数详情")
        lines.append("━" * 50)
        for dll_name, funcs in result["imports"]:
            if funcs:
                lines.append(f"  {dll_name}: {len(funcs)} 个导入")
                for f in funcs[:10]:
                    lines.append(f"    - {f}")
                if len(funcs) > 10:
                    lines.append(f"    ... 还有 {len(funcs) - 10} 个")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    verbose = "--verbose" in sys.argv
    json_output = "--json" in sys.argv

    if not os.path.isfile(path):
        print(f"[ERROR] 文件不存在: {path}")
        sys.exit(1)

    try:
        with open(path, 'rb') as f:
            data = f.read()
        
        result = read_pe(data)
        linkage = analyze_linkage(result)

        if json_output:
            import json as json_mod
            output = {
                "file": os.path.basename(path),
                "file_size_bytes": result["file_size"],
                "architecture": result.get("machine", "unknown"),
                "linkage": linkage,
                "sections_count": len(result.get("sections", [])),
                "sections": result.get("sections", []),
            }
            print(json_mod.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(f"\n{'='*50}")
            print(f"  PE 链接分析: {os.path.basename(path)}")
            print(f"{'='*50}")
            print(format_report(result, verbose))
            
            # 结论
            print()
            print("━" * 50)
            print("结论")
            print("━" * 50)
            if linkage["is_fully_static"]:
                print(f"  ✅ 全静态链接 — [{linkage['compiler']}]")
                print(f"     不依赖第三方 DLL，可以导入 Ghidra 进行完整逆向")
                print(f"     (所有代码已静态编译到单一二进制中)")
            else:
                print(f"  ❌ 动态链接")
                print(f"     依赖第三方 DLL，逆向时需同时分析依赖的 DLL")
            
    except Exception as e:
        print(f"[ERROR] 分析失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
