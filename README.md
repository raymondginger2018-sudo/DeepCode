# DEEPCODE 配置同步仓库 (sync/config)

这是 DEEPCODE 两台电脑之间**双向同步**的中枢仓库（分支 `sync/config`）。
只同步 **Skills + 配置 + 脚本 + 文档**，**不同步**密钥、构建产物、大目录。

## 同步内容

| 路径 | 内容 | 说明 |
|------|------|------|
| `.deepcode/skills/` | DeepCode CLI Skills | 不含 harmony-next（大，单独 clone） |
| `.dsh/skills/` | DSH Skills | 同上 |
| `.claude/skills/` | Claude Code Skills | |
| `.claude/settings.json` | Claude Code 配置 | 不含密钥 |
| `.dsh/mcp-servers/` | DSH MCP 服务器配置 | |
| `.mcp.json` | MCP 服务器清单 | **已脱敏**（`${...}` 占位符） |
| `.deepcode/rules/` | DeepCode 规则 | 不含 github-token.md |
| `.deepcode/policies/` | DeepCode 策略 | |
| `scripts/` `commands/` | 辅助脚本 | |
| `AGENTS.md` `CLAUDE.md` `DEPLOY.md` | 项目文档 | |

## 两台电脑的同步工作流

### 机器 A（有改动的人）

```powershell
cd F:\DEEPCODE\.dc-sync

# 1. 把活动目录的最新 Skills/配置 收进 sync 仓库
python sync-build.py

# 2. 提交并推送
git add -A
git commit -m "sync: update skills/config"
git push origin sync/config
```

### 机器 B（要更新的人）

```powershell
cd F:\DEEPCODE\.dc-sync

# 1. 拉取最新
git pull origin sync/config

# 2. 部署到活动位置（Skills → .deepcode/ .dsh/ .claude/）
powershell -ExecutionPolicy Bypass -File .\sync-deploy.ps1
```

### 新电脑首次安装

```bash
# 已有 DEEPCODE 主仓库 clone
cd F:/DEEPCODE
git fetch origin sync/config
git worktree add .dc-sync sync/config

cd .dc-sync
powershell -ExecutionPolicy Bypass -File .\sync-deploy.ps1

# 补密钥（.mcp.json 是占位符，需要手动替换）
#   copy .dc-sync\.mcp.json .mcp.json
#   替换 ${TUSHARE_TOKEN} ${GITHUB_PAT} ${DEEPSEEK_API_KEY}
```

## 安全规则（铁律）

- `.mcp.json` / `settings.json` **绝不提交真实密钥**，统一用 `${PLACEHOLDER}`。
- 本机已有 `.mcp.json` 时，`sync-deploy.ps1` **不会覆盖**，保护密钥。
- 新增含密钥的文件时，必须加到 `.gitignore` 或脱敏后再提交。

## 不同步的内容

- `harmony-next/` — 100MB+ 文档集，单独 clone:
  ```bash
  git clone https://github.com/linhay/harmony-next.skills.git F:/DEEPCODE/.deepcode/skills/harmony-next
  ```
- `tools/` — 16GB，不入库
- `node_modules/`、`__pycache__/`、`*.db`、`*.bak*`、`*.log`
- `.env`、`credentials.json`、`deepcode_config.json`

## 代码同步

代码不走这个仓库。每个子仓库各自推 GitHub:

```bash
# 主仓库
cd F:/DEEPCODE && git push origin pr/deepcode-genai

# DSH
cd F:/DEEPCODE/deepseek-harness && git push origin master
```

`cli/` 和 `deepcode-engine-mcp/` 目前无远端，需要时再各自建 GitHub 仓库。