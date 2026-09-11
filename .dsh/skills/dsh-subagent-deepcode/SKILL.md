---
name: dsh-subagent-deepcode
description: 安装/修复 DSH 的 SUBAGENT DEEPCODE（DeepCode 子代理委派）和 DEEPCODE 状态灯（ui-deepcode-status 侧边栏卡片），含 0.1.2+ 升级适配与全套调试技术。当用户提到 subagent_deepcode、DeepCode 子代理、DEEPCODE 状态灯、DeepCode 委派、状态灯不亮/不更新时使用。
---

# DSH SUBAGENT DEEPCODE + 状态灯 模块手册

## 模块架构

### SUBAGENT DEEPCODE（DeepCode 子代理委派）
- 包: `@deepseek-ai/dsh-subagent-deepcode`（host 平面，provider 名 `deepcode`）
- 模型工具: `subagent_deepcode`（one-shot，maxDepth: provider-managed）
- 运行: `<python> <repoRoot>/cli/exec_cli.py --json --workspace <cwd> --trust <task>`
- one-shot 子代理不落盘持久化会话 → 靠 subagent/start|end 生命周期事件上报活动

### DEEPCODE 状态灯（ui-deepcode-status）
- 包: `@deepseek-ai/dsh-client-ui-deepcode-status`（client 插件）
- 挂载: `sidebar.workspaces.status` 槽位（ui-workspace 声明，会话树上方）
- 显示: "DeepCode 空闲" / "DeepCode：N 个任务运行中"（绿点脉冲）
- 计数: catalog 中 provider==='deepcode' 的 running children + deepcode/activity 远程事件

## 关键配置（packages/bundle/base/cordis.patch.yml）

```yaml
- id: subagent-deepcode
  name: '@deepseek-ai/dsh-subagent-deepcode'
  config:
    repoRoot: !!js (process.env.DEEPCODE_ROOT || 'F:/DEEPCODE') + '/DeepCode'
    command: 'F:/DEEPCODE/.venv/Scripts/python.exe'
    env:
      PYTHONPATH: !!js (process.env.DEEPCODE_ROOT || 'F:/DEEPCODE') + '/DeepCode'
      DEEPCODE_HOME: !!js (process.env.DEEPCODE_ROOT || 'F:/DEEPCODE') + '/.deepcode-subagent'
      DEEPSEEK_API_KEY: !!js process.env.DEEPSEEK_API_KEY
```

> ⚠️ `!!js` 表达式必须保持纯标量（不能含 `: ` 冒号空格）。

## 调试技术（按问题链）

1. **repoRoot 指向错误** → DEEPCODE_ROOT 与 DeepCode 仓库实际路径不同，拼 `/DeepCode` 后缀
2. **ModuleNotFoundError: No module named 'cli'** → env 加 `PYTHONPATH=仓库根`
3. **database schema 17 > supported 16** → `DEEPCODE_HOME` 指独立目录（.deepcode-subagent），复制 deepcode_config.json（deepseek provider）
4. **This workspace is not trusted yet** → `run.ts` deepCodeArgv 加 `--trust`（同步 3 处测试断言）
5. **DEEPSEEK_API_KEY not set** → env 加 `DEEPSEEK_API_KEY: !!js process.env.DEEPSEEK_API_KEY`
6. **状态灯永远"空闲"**（0.1.2+）→ 恢复 deepcode/activity 三件套（见下）
7. **ui-deepcode-status 被冻结**（0.1.2 升级）→ git 历史恢复 + 适配新 API（见下）

## 状态灯 deepcode/activity 三件套（0.1.2+ 恢复方法）

1. `packages/api/remotes/src/remote-events.ts`:
   - allowlist 加 `{event:'deepcode/activity',mode:'emit'}`
   - `declare module '@deepseek-ai/cordis' { interface Events { 'deepcode/activity'(count:number):void } }`
   - `declare module '@deepseek-ai/dsh-typert-protocol' { interface TypertRemoteEventSelection { 'deepcode/activity': true } }`
2. `packages/api/remotes/src/index.ts` remoteEventSource: 监听 `subagent/start|end`（过滤 provider==='deepcode'）维护计数，`queue.push({event:'deepcode/activity',args:[count]})`
3. 卡片 `DeepCodeStatusCard` 恢复 `DeepCodeStatusRemoteFace` + `$on('deepcode/activity')` 订阅；client/index.ts 恢复 `inject=['slots','sessions','locale','remote']`

## 0.1.2 升级适配清单（ui-deepcode-status）

- `SessionListState` ← `@deepseek-ai/dsh-api-session-controller/client`
- `ClientContext` ← `@deepseek-ai/cordis`
- `SlotRegistry` ← `@deepseek-ai/dsh-client-ui-renderer/client`
- 服务端 `SubagentListEntry` 加回 `provider` 字段（projection-types.ts / projection.ts / list-children.ts / control-types.ts）
- ui-workspace: slots.ts 加 `'sidebar.workspaces.status'` 槽位 + WorkspaceBrowser `renderSlot('sidebar.workspaces.status', {})` + client/index.ts children 声明
- package.json `dsh.client.inject`: dsh-client-runtime → dsh-api-session-controller
- tsconfig.client.json: 从 exclude 移除 + references 加回
- web-app bundle: cordis.patch.yml 加插件行 + package.json 依赖
- `pnpm install --no-frozen-lockfile` 更新 lockfile

## 测试与验证

- `npx vitest run packages/subagent/subagent/tests/list-children.spec.ts`（provider 改动后测试期望同步，provider:'spawn'）
- `npx vitest run packages/client/ui-deepcode-status/tests`（9 个测试）
- 状态灯实测: Playwright 开 GUI（token 从 dsh-web-server.log 的 `?token=` 取）→ 后台派 subagent_deepcode → find "DeepCode：1 个任务运行中" → 结束 find "DeepCode 空闲"

## PowerShell 批量改文件铁律

- `-replace` 替换字符串用**单引号**（双引号 `$1` 被插值成空！）
- 读写含中文文件必须 `[IO.File]::ReadAllLines/WriteAllLines` + `UTF8Encoding($false)`（Get-Content/Set-Content ANSI 会破坏 UTF-8）
- 杀 web 服务会中断会话 → 后台 kill（run_in_background）

## 遗留问题

- DeepCode 复杂任务偶发 "execution runtime cleanup did not finish before the shutdown timeout"（内部时序，不影响状态灯；可调大 disposeGraceMs）
