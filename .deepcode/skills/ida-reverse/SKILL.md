---
name: ida-reverse
description: >
  IDA Pro 二进制逆向分析方法论（适配 DeepCode 工具链）。当用户提到逆向、反编译、分析二进制
  /PE/ELF/APK/DLL/SO、破解、找密码、漏洞分析、病毒分析、固件分析，或需要分析 exe/dll/so/elf/
  macho 文件时使用。方法论围绕 ida-pro-mcp 的 idapro_* 工具，DeepCode 环境下可用 ghidra-mcp /
  decompiler 工具链替代执行。
  源自 zhaoxuya520/reverse-skill 的 ida-reverse skill，适配 DeepCode。
version: 1.0.0
author: DeepCode (adapted from zhaoxuya520/reverse-skill)
source: https://github.com/zhaoxuya520/reverse-skill
date: 2026-08-01
tags: [reverse, ida, binary, decompile, pe, elf, malware, security]
---

# IDA Pro 二进制逆向分析

> 二进制文件（exe/dll/so/elf/macho）逆向分析的方法论与工作流。

## 适用范围

用户提到以下任一意图时使用本 skill：
- 逆向 / 反编译 / 分析二进制、PE、ELF、APK、DLL、SO、固件
- 破解 / 找密码 / 注册机 / 序列号分析
- 漏洞分析 / 病毒分析 / 恶意样本分析
- "看看这个 exe" / "分析这个 dll" 等

> 目标是 APK → 走 mobile-reverse；目标是前端 JS → 走 js-reverse；目标是 JS 虚拟机 → 走 dsl-vm-reverse。

## 工具链适配（DeepCode）

| 能力 | ida-pro-mcp 工具（方法论参考） | DeepCode 可用替代 |
|------|------------------------------|------------------|
| 概况分析 | `idapro_survey_binary` | `mcp__ghidra-mcp__binary_analyze_report` / `scan_pe_file` |
| 反编译 | `idapro_decompile` | `mcp__deepcode-decompiler__decompile_pe` / `decompile_bytes` |
| 反汇编 | `idapro_disasm` | `mcp__deepcode-decompiler__lift_ir` / `disasm_bytes` |
| 交叉引用 | `idapro_xrefs_to` | `mcp__ghidra-mcp__debugger_*` / 静态 grep |
| 字节模式 | `idapro_find_bytes` | `scan_pe_file`（全量 .rdata 扫描） |
| 调用图 | `idapro_callgraph` | `mcp__deepcode-decompiler__analyze_cfg` |
| 签名 | `idapro_make_signature` | `mcp__ghidra-mcp__ida_flirt_scan` / `ida_import_sigs_from_ghidra` |
| Python 执行 | `idapro_py_eval` | `mcp__deepcode-sandbox__sandbox_run_python` |

> 若环境已安装 IDA Pro + ida-pro-mcp（`idapro_*` 工具可用），优先用 idapro 工具；否则用上表 DeepCode 替代链。

## 逆向分析完整工作流

### Step 1: 准备分析环境
- 确保目标文件可访问（System32 等受限目录先复制到临时目录）
- 若用 ida-pro-mcp：`scripts/start.ps1` 启动服务器，`scripts/open.ps1` 打开文件
- 若用 ghidra-mcp：`mcp__ghidra-mcp__import_file` 导入二进制

### Step 2: 全局概览（先看全景）
- 架构（x86/x64/ARM）、入口点（main/WinMain/DllMain）
- 有趣的字符串（URL、路径、错误消息）→ 用 `scan_pe_file` / 字符串提取
- 导入分类（加密函数？网络 API？文件操作？）
- 热门函数（高引用计数的函数通常是关键逻辑）

### Step 3: 深入关键函数
- 反编译伪代码（decompile）
- 反汇编 + IR（lift_ir / disasm）
- 函数综合分析：伪代码 + 字符串 + 常量 + 调用者 + 被调用者

### Step 4: 数据流与交叉引用
- 查谁引用目标地址/字符串（xrefs_to）
- 调用图（callgraph / analyze_cfg，max_depth 3）
- 数据流追踪（forward/backward）

### Step 5: 记录与优化
- 持续加注释、批量重命名函数/变量（提升后续分析准确性）
- 识别库函数（FLIRT 签名）后再分析业务逻辑

### Step 6: 输出报告
- 生成 report.md：发现、证据（命令/脚本/截图）、结论

## Prompt 工程准则

1. **不要手动算进制** — 用工具做进制转换
2. **先 survey 后深入** — 先看概况再针对性分析
3. **持续加注释和重命名** — 分析过程中不断更新符号
4. **跟踪交叉引用** — 发现有趣的数据/字符串，看谁引用了它
5. **遇到混淆代码** — 先做字符串解密、导入哈希去除、控制流平坦化去除等预处理
6. **C++ STL 代码** — 先用 FLIRT/Lumina 识别库函数，再分析业务逻辑
7. **不要暴力破解** — 分析应从反汇编中推导解决方案，用脚本辅助计算
8. **"No database bound"** — 还没打开任何二进制文件，先导入目标

## 已知踩坑（精简）

- **ida-pro-mcp 的 `idalib_open` 在部分 AI 客户端有 schema 校验 BUG** → 用 open 脚本走 HTTP 直调绕过
- **System32 文件无权限** → 自动复制到临时目录再打开
- **带自动分析打开大文件可能长时间无返回** → 设置较长超时（如 600s），不要误判为卡死
- **超时可能留下孤儿 worker 锁文件（.id0/.id1/.nam）** → 用 `taskkill /F /T` 杀进程树

## 任务完成自检（声称完成前 MUST 通过）

- [ ] 我是否执行了工作流的每一步（而不是只阅读）？
- [ ] 我是否产出了可复现证据（命令/脚本/截图/报告）？
- [ ] 关键函数是否已重命名/注释，结论是否可追溯？
