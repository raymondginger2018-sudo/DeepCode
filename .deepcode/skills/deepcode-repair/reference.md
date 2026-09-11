---
# DeepCode 基础设施修复参考

本文件是 `deepcode-repair` skill 的详细参考：历史案例台账 + 基础设施诊断树 + 修复模式细节。
每次修复完成后必须在「历史案例台账」追加一行（见 SKILL.md 收尾沉淀）。

---

## 历史案例台账

| 日期 | 模块/症状 | 根因 | 修复 | 教训 |
|------|-----------|------|------|------|
| 2026-09-01 | deepcode-agent-sdk：所有工具超时死锁，UnicodeDecodeError | `subprocess.run(text=True)` 严格 UTF-8 解码子进程二进制输出（gitleaks 扫 .png/.woff）崩溃，一个线程崩→整个 server 死锁 | `errors='replace'` 加在 L524/L660 两处 | 子进程输出可能含任意字节；text=True 必须配 errors='replace' |
| 2026-09-01 | bash 工具 `spawn bash.exe ENOENT`，会话所有 bash 坏死 | sessionWorkingDirs 持久化无效 cwd（`\\tmp\\ci_logs`），spawn cwd 无效→ENOENT | bash-handler.ts 的 get/updateSessionCwd 加 `isUsableCwd()` 校验（statSync.isDirectory），无效回退 fallback 并清除 | 会话状态持久化必须先校验路径有效性 |
| 2026-09-01 | deepcode-agent agent_thread_spawn 返回 created 永不推进 | spawn 只写 DB 记录，无执行器接管 goal | spawn 后调 `_launch_auto_executor`：Popen 子进程 `auto_spawn.py exec` 真执行 + 状态流转 | 只建记录不接执行器 = 假任务；spawn 必须配执行通道 |
| 2026-09-01 | Playwright browser_run_code_unsafe 沙箱禁 require/process | 设计如此（VM 沙箱隔离） | 非 bug，不改；换用 CLI/Python 执行通道 | 沙箱限制是设计而非故障，评估后再决定修不修 |
| 2026-08-31 | Security CI secret-scan 红：gitleaks 4 处泄漏 | 历史 commit 含 JWT fixture + 真实 Tushare token | 当前文件 token→`${TUSHARE_TOKEN}`；历史泄漏→.gitleaksignore 指纹 | 见模式 4；.gitleaksignore 注释勿写真实密钥模式 |
| 2026-08-31 | Security CI dependency-audit 红：pip_audit 1 漏洞 | `pip==26.1.2` PYSEC-2026-3721 | lock 文件 pip→26.2 | 锁文件升级即可，无需改安装脚本 |
| 2026-08-27 | Security CI secret-scan 红：pip 26.1.2 漏洞 + gitleaks | 见上 | 已处理（#200/#199） | 同上 |
| 2026-09-10 | deepcode-cerebellum MCP failed/disconnected，CLI 无法启动 | 项目 settings.json 中 `mcpServers.deepcode-cerebellum` 指向 `core/mcp_servers/cerebellum_mcp_server.py`（文件不存在）+ 缺 `--mcp` 标志 + `python3` 命令（Windows 可能不可达），覆盖了用户级正确配置 | 改 `.deepcode/skills/...` 实际路径 + 补 `--mcp` + 切 `python` + 加完整环境变量 | 项目级 mcpServers 配置优先级高于用户级，误配可导致 MCP server 完全不可启动；注册任何 MCP server 必须确认路径存在、`--mcp` 标志存在、命令能在 Windows CreateProcess 中找到 |

---

## 基础设施诊断树（由外向内）

```
用户报告 DeepCode 基础设施故障
│
├─ 1. 探活 / 复现
│    ├─ MCP server 工具超时 → 查该 server 的 stderr / 日志 / 进程是否还活着
│    ├─ bash 工具 ENOENT → 查 startCwd 是否有效路径（statSync）
│    └─ CLI 崩溃 → 复现命令 + 退出码
│
├─ 2. 判定是否"代码 bug vs 环境问题 vs 设计如此"
│    ├─ 设计如此（沙箱隔离等）→ 不改，换通道
│    ├─ 环境问题（路径无效/依赖缺失）→ 修复环境
│    └─ 代码 bug → 进入定位
│
├─ 3. 定位（rg -n 找关键调用，读源码确认根因）
│
├─ 4. 修复（最小改动 + 注释 why）
│
├─ 5. 验证
│    ├─ 语法：python -m py_compile / npm run build
│    ├─ 行为：真实触发场景测试
│    └─ 加载链：重启 MCP server（/mcp 重连）或重启 CLI
│
└─ 6. 收尾沉淀（强制）：reference.md 追加 + 提炼教训 + bump 版本
```

### MCP server 故障专项

- **判断是否活着**：`Get-CimInstance Win32_Process` 查 python 进程 CommandLine 是否含目标 server 名。
- **死锁定位**：子进程输出含二进制 → 看 stderr 的 `UnicodeDecodeError`，而非只盯工具超时。
- **修复生效**：改完源码必须重启对应 MCP server：
  - 方式 A：`/mcp` 界面选 failed server → Enter 重连（不丢对话）。
  - 方式 B：`/exit` 重启整个 CLI 会话（所有 server 用新代码 spawn）。
  - CLI 不会自动重连（onServerCrash 只清理标记 failed，无 auto-restart）。

### 修复生效加载链检查清单

- [ ] Python 模块：`python -m py_compile <file>` 通过
- [ ] 若改 TS/JS：构建产物替换到实际生效路径（如 dist/cli.js）
- [ ] 若改 MCP server：进程已重启，`/mcp` 状态 connected
- [ ] 真实触发场景复测通过

---

## 修复模式细节

### 子进程二进制输出解码（UnicodeDecodeError）

```python
# ❌ 会崩：text=True 默认严格 UTF-8
subprocess.run(cmd, capture_output=True, text=True, timeout=30)

# ✅ 修复：errors='replace' 把非法字节替换为 U+FFFD
subprocess.run(cmd, capture_output=True, text=True, errors='replace', timeout=30)
```

适用：所有 `capture_output=True, text=True` 的 subprocess 调用——
执行命令可能扫描二进制文件（gitleaks、file 工具、压缩包遍历）时必加。

### 会话 cwd 校验（bash ENOENT）

```ts
function isUsableCwd(cwd: string): boolean {
  if (!cwd) return false;
  try { return fs.statSync(cwd).isDirectory(); } catch { return false; }
}
// getSessionCwd: stored 有效则用，否则删除并回退 fallback
// updateSessionCwd: 新 cwd 无效则 delete 而不 set
```

适用：任何把 cwd 持久化到会话状态的代码路径（bash handler、MCP server）。

### spawn 后必须接执行器（假任务陷阱）

```python
# spawn 创建记录后：
mgr.update_status(thread_id, ThreadStatus.RUNNING)
subprocess.Popen([sys.executable, auto_spawn_py, "exec", payload],
                 cwd=..., env=..., stdout=DEVNULL, stderr=DEVNULL)
```

适用：任何"创建状态记录"型 API（agent thread、job queue、后台任务）——
创建后必须有真实执行链接管，并完成 created→running→completed/failed 状态流转。

---

## 常见坑（快速速查）

- 改 Python 源码不 `py_compile` → 缩进/语法错误运行才暴露。
- 改 MCP server 源码不重启 → 旧进程跑旧代码，误判"没修好"。
- 只盯工具超时 → 忽略 server stderr 里的解码崩溃。
- Windows 工具链用 `/tmp` POSIX 路径 → 转成 `\\tmp` 无效路径。
- 叠加改动一起测 → 失败无法归因到层（一修一验）。
- `.gitleaksignore` 注释写真实密钥示例 → 自泄漏新告警。
- 把"设计如此"（沙箱限制）当 bug 修 → 浪费时间和改动面。
