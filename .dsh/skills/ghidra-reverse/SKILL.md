---
name: ghidra-reverse
description: >
  Ghidra 二进制逆向分析（精适配 DeepCode 工具链）。无 IDA 许可证时的主逆向入口：
  批量 headless 反编译、导入分析、反编译伪代码、交叉引用、调用图、符号执行。
  DeepCode 环境下以 mcp__ghidra-mcp__* 工具为执行面，方法论源自 zhaoxuya520/reverse-skill。
version: 2.0.0
author: DeepCode (adapted from zhaoxuya520/reverse-skill)
source: https://github.com/zhaoxuya520/reverse-skill
date: 2026-08-27
note: Ghidra 已升级至 12.1.3（安装路径: F:\DEEPCODE\tools\ghidra_12.1.3_PUBLIC），旧版 12.1.2 保留在 F:\DEEPCODE\tools\ghidra_12.1.2_PUBLIC
tags: [reverse, ghidra, decompile, binary, headless, security]
---

# Ghidra Reverse Engineering（DeepCode 精适配版）

## 适用场景

- 无 IDA 许可证时的主逆向入口
- 批量 headless 分析 / 反编译
- 与 `binary-diff` / `patch-diff-exploit` 的 ghidriff 联动
- DeepCode 环境：直接经 ghidra-mcp 桥接分析

## 与其它 skill 分工

| 需求 | 优先 |
|------|------|
| IDA MCP 深挖（若已装） | `ida-reverse` |
| **开源 / 批量 / DeepCode 默认** | **本 skill（ghidra-mcp）** |
| 仅 CLI 快速侦察 | `radare2` |
| PE 快速扫描 / 字符串提取 | `scan_pe_file`（先于深度分析） |

## DeepCode 工具链映射（ghidra-mcp）

| 能力 | 工具 | 说明 |
|------|------|------|
| 导入二进制 | `mcp__ghidra-mcp__import_file` | 支持 ELF/PE/Mach-O，raw 固件可指定 language |
| 概况报告 | `mcp__ghidra-mcp__binary_analyze_report` | 函数/字符串/导入表 → LLM 分析上下文 |
| PE 快速扫描 | `mcp__ghidra-mcp__scan_pe_file` | 全量 .rdata 字符串 + 加密检测，先于深度分析 |
| 反编译 | `mcp__deepcode-decompiler__decompile_bytes` / `decompile_pe` | 字节码 → 结构化 C 伪代码 |
| 反汇编 / IR | `mcp__deepcode-decompiler__lift_ir` / `disasm_bytes` | 多后端（capstone/ghidra/xedparse） |
| 控制流图 | `mcp__deepcode-decompiler__analyze_cfg` | 分支/循环/调用图 |
| 符号执行 | `mcp__ghidra-mcp__angr_symbolic_execute` | 路径探索、find/avoid |
| 动态污点 | `mcp__ghidra-mcp__triton_taint` / `triton_analyze` | 数据传播追踪 |
| 动态调试 | `mcp__ghidra-mcp__debugger_*` | attach/断点/寄存器/内存/跟踪 |
| 签名/类型 | `mcp__ghidra-mcp__ida_flirt_scan` / `ida_import_sigs_from_ghidra` | 库函数识别 |
| 批注持久化 | `mcp__ghidra-mcp__ida_save_annotation` | 分析结论落库复用 |

> 已连接 Ghidra 实例时优先 `mcp__ghidra-mcp__*`（connect_instance 后自动注册全部工具组）；
> 未连接时可用 `deepcode-decompiler` 纯静态链（无需 Ghidra）。

## 工作流

### 1. 快速分诊（先于深度分析）
- `scan_pe_file`：全量字符串 + 加密检测 → 定位 URL/C2/可疑字符串
- `binary_analyze_report`：函数数、导入分类（加密/网络/文件 IO）、入口点
- 记录语言/编译器识别结果与基址

### 2. 导入与自动分析
- `import_file` 导入（ELF/PE/Mach-O 自动识别；raw 固件指定 `language` 如 `ARM:LE:32:Cortex`）
- auto_analyze 完成后从函数/字符串反查关键逻辑

### 3. 深入关键函数
- `decompile_bytes` / `decompile_pe` 还原算法
- `analyze_cfg` 看控制流（分支/循环/混淆）
- 重命名函数/变量，`ida_save_annotation` 落库
- 需要动态时交接 `debugger_*` 或 Frida/GDB

### 4. 高级分析（静态不够时）
- `angr_symbolic_execute`：符号执行找路径/约束
- `triton_taint`：污点追踪数据传播
- `ida_flirt_scan`：FLIRT 识别库函数后分析业务逻辑

### 5. Headless / 批量（无 MCP 时）
```bash
analyzeHeadless /path/to/project Proj -import sample.bin -postScript ExportDecomp.py
```

## 任务完成自检（声称完成前 MUST 通过）

- [ ] 是否先做了快速分诊（scan_pe_file / binary_analyze_report）？
- [ ] 是否基于真实工具/实例路径（不猜端口、不猜函数地址）？
- [ ] 关键函数是否反编译、重命名、注释并落库？
- [ ] 是否产出可复现证据（命令/伪代码/报告）？
- [ ] 结论是否沉淀（可存入 deepcode-knowledge 知识库）？
