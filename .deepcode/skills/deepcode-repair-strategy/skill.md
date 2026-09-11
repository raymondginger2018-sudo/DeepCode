---
name: deepcode-repair-strategy
description: >
  DeepCode 修复策略规范。修复Docker Desktop时，连续6次尝试手动WSL命令（wsl --shutdown、wsl --unregis
---
# DeepCode 修复策略规范（自进化生成）

生成时间：2026-08-26 19:10
触发失败：修复Docker Desktop时，连续6次尝试手动WSL命令（wsl --shutdown、wsl --unregister、重置vm-data），每失败一次才换一种方法，最终要用winget upgrade重装才解决，浪费了大量时间。

---

## 改进规则

[{'before': '出现Docker无法启动时，直接连续6次尝试wsl --shutdown、wsl --unregister等命令。', 'after': '对Docker类问题，先执行docker info/diagnose收集信息；若WSL相关错误，最多尝试2次WSL重置，无效则立即用winget upgrade/重装Docker Desktop。'}, {'before': '每次失败后仅更换WSL命令，未改变修复层级。', 'after': '设定修复层级：快速检查→WSL重置（最多2次）→备份数据后重装/升级Docker。同一层级失败立即升级。'}, {'before': '没有利用包管理器自动修复能力。', 'after': 'Windows环境优先使用winget upgrade或安装文件修复，避免手动操作底层子系统。'}, {'before': '修复过程无时间或次数限制，无限试错。', 'after': '任何修复分支最多尝试2次，超时后自动切换到更高层级的修复方案。'}, {'before': '修复时未记录尝试历史，导致重复无效操作。', 'after': '记录每次尝试的命令和结果，出现重复失败时强制转向替代方案。'}]

## 建议写入 System Prompt 的文本

```
你是一个自进化修复系统。修复任何软件故障时，必须遵循分层修复策略：第一层为快速诊断与最小化重置（最多尝试2次），第二层为软件升级或完整性修复（如winget upgrade、重新安装），第三层为系统级环境重置。禁止在同一层级无限试错，每次失败必须分析原因并升级策略。
```

## 常见陷阱

- **Docker Desktop无法启动，反复执行wsl --shutdown、wsl --unregister、重置vm-data等命令均无效。**：立即停止WSL层操作，运行winget upgrade Docker.DockerDesktop或从官网下载安装包进行覆盖安装/重装。
