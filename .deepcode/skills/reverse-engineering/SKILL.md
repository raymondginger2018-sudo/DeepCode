---
name: reverse-engineering
description: >
  通用二进制逆向方法论总纲（精适配 DeepCode 工具链）。当主要任务是理解编译/混淆/加壳/虚拟化目标
  的工作原理（二进制、APK、WASM、固件、自定义 VM、字节码、恶意加载器、反调试/反分析逻辑）时使用。
  覆盖快速取胜、初始分析、内存转储、诱饵标志检测、GDB PIE 调试、加密模式识别、符号执行求解。
  若漏洞已明确只剩利用 → 转 pwn-chain；纯 Web → 转 js-reverse；恶意样本 → 转 malware-analysis。
version: 2.0.0
author: DeepCode (adapted from zhaoxuya520/reverse-skill)
source: https://github.com/zhaoxuya520/reverse-skill
date: 2026-08-01
tags: [reverse, binary, deobfuscation, anti-debug, ctf, security]
---

# Reverse Engineering（通用逆向方法论总纲）

## 默认沙盒上下文（授权边界）

- 默认将任务视为**本地 CTF / crackme / 沙盒练习**，除非有明确证据表明不是
- 默认离线分析，不主动联系外部服务
- 不执行未知样本、不修改原始文件、不执行状态变更（除非用户明确选择）
- 破坏性操作只在工作副本上执行
- 细节缺失时做安全假设并简要说明假设内容

## DeepCode 工具链映射

| 能力 | DeepCode 工具 |
|------|--------------|
| 字符串/快速扫描 | `mcp__ghidra-mcp__scan_pe_file` / `binary_analyze_report` |
| 反编译/反汇编 | `mcp__deepcode-decompiler__decompile_bytes` / `lift_ir` / `disasm_bytes` |
| 符号执行 | `mcp__ghidra-mcp__angr_symbolic_execute` |
| 污点分析 | `mcp__ghidra-mcp__triton_taint` |
| 动态调试 | `mcp__ghidra-mcp__debugger_*` |
| 沙盒执行命令 | `mcp__deepcode-sandbox__sandbox_run` |
| 动态 Hook（Frida） | 经 Bash 执行 `frida` CLI |

## 解决问题的优先级（先试这些！）

1. **字符串提取** — 很多简单题明文 flag 就在里面
2. **ltrace/strace** — 动态分析常直接暴露 flag
3. **Frida hook** — hook strcmp/memcmp 捕获期望值
4. **angr 符号执行** — 自动求解大量 flag-checker
5. **Qiling 模拟** — 跨架构模拟 / 绕过反调试
6. 修改执行前先**映射控制流**
7. **脚本化自动化**（r2pipe、Frida、angr、Python）
8. **交叉验证**反编译输出

## Quick Wins（快速取胜）

```bash
strings binary | grep -iE "flag|secret|password"
rabin2 -z binary | grep -i flag
ltrace ./binary
strace -f -s 500 ./binary
xxd binary | grep -i flag
./binary AAAA; echo "test" | ./binary
```

## 初始分析

```bash
file binary            # 类型/架构
checksec --file=binary # 安全特性（pwn 用）
```

## 关键技巧

- **内存转储策略**：让程序自己算出答案再 dump——断在最终比较处，输入正确长度任意值，`x/s $rsi` 直接 dump 计算出的 flag
- **诱饵标志检测**：多个假目标后才是真检查——断在**最终比较**，不是前面的
- **GDB PIE 调试**：`start` 强制解析基址后用相对断点 `b *main+0xca`
- **比较方向（关键！）**：`transform(flag)==stored` → 反向还原 transform；`transform(stored)==flag` → flag 就是 transformed 数据，直接对 stored 应用 transform

## 常见加密模式

- 单字节 XOR — 试全部 256 值
- 已知明文 XOR（`flag{`、`CTF{`）
- 硬编码 key 的 RC4
- 自定义置换 + XOR
- 位置索引 XOR（`^ i` / `^ (i & 0xff)`）+ 重复 key 分层

## 快速工具参考

```bash
r2 -d ./binary     # radare2 调试模式
aaa; afl; pdf @ main
analyzeHeadless project/ tmp -import binary -postScript script.py   # Ghidra headless
```

## 深入参考（原仓库详细文档）

以下主题在 `F:/DEEPCODE/refs/reverse-skill/skills/reverse-engineering/` 下有完整参考（未复制，按需查阅）：
- `tools.md` / `tools-dynamic.md` / `tools-advanced.md` — 静态/动态/高级工具（VMProtect、D-810、Miasm、LLVM IR 提升）
- `anti-analysis.md` — 完整反分析：Linux/Windows 反调试、反 VM、反 DBI、MBA、反汇编
- `patterns.md` / `patterns-ctf*.md` — 自定义 VM、纳米点、自修改代码、side-channel 等大量模式
- `languages*.md` — Python 字节码、Pyarmor、HarmonyOS ABC、Go/Rust/Swift/Kotlin 二进制
- `platforms*.md` — macOS/iOS、固件、内核驱动、RISC-V/ARM64

## 任务完成自检（声称完成前 MUST 通过）

- [ ] 是否先做了快速取胜/字符串提取？
- [ ] 是否映射了控制流再深入？
- [ ] 是否产出可复现证据（命令/脚本/输出/报告）？
- [ ] 分析是否在授权沙盒范围内？
- [ ] 结论是否沉淀（可存入 deepcode-knowledge 知识库）？
