# sync-deploy.ps1 — 将 .dc-sync/ 中的同步内容部署到 DEEPCODE 活动位置
# 用法（在 F:\DEEPCODE\.dc-sync 下）:
#   powershell -ExecutionPolicy Bypass -File .\sync-deploy.ps1 [-DryRun]
#
# 安全规则:
#   - .mcp.json 和 settings.json 含真实密钥，部署时【不会】覆盖本机现有文件
#   - 新机器首次部署若缺 .mcp.json，会复制模板并提示你填入密钥

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$SyncRoot = Split-Path -Parent $MyInvocation.MyCommand.Path   # .dc-sync/
$LiveRoot = Split-Path -Parent $SyncRoot                      # F:/DEEPCODE/

if ($DryRun) {
    Write-Host "[DRY-RUN] 仅预览，不复制" -ForegroundColor Yellow
}

function Copy-SyncItem {
    param(
        [string]$Relative,
        [switch]$PreserveLiveIfExists   # 本机已有则不覆盖（保护密钥）
    )
    $src = Join-Path $SyncRoot $Relative
    $dst = Join-Path $LiveRoot $Relative
    if (-not (Test-Path $src)) {
        Write-Host "  - SKIP $Relative (sync 中没有)"
        return
    }
    if ($PreserveLiveIfExists -and (Test-Path $dst)) {
        Write-Host "  - KEEP $Relative (本机已有，保留密钥)"
        return
    }
    if ($DryRun) {
        Write-Host "  > COPY $Relative"
        return
    }
    $dstParent = Split-Path -Parent $dst
    if (-not (Test-Path $dstParent)) { New-Item -ItemType Directory -Force -Path $dstParent | Out-Null }
    if (Test-Path $dst -PathType Container) {
        Copy-Item -Path $src\* -Destination $dst -Recurse -Force
    } else {
        Copy-Item -Path $src -Destination $dst -Force
    }
    Write-Host "  ✓ COPY $Relative"
}

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║  DEEPCODE Sync — Deploy              ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

# 1. Skills（三个位置都部署）
Write-Host "── Skills ──"
Copy-SyncItem ".deepcode\skills"
Copy-SyncItem ".dsh\skills"
Copy-SyncItem ".claude\skills"

# 2. Claude Code 配置（不含 memory.db）
Write-Host "── Claude Code ──"
Copy-SyncItem ".claude\settings.json"
Copy-SyncItem ".claude\proven-config.json"
Copy-SyncItem ".claude\agents"
Copy-SyncItem ".claude\commands"
Copy-SyncItem ".claude\helpers"

# 3. DSH MCP servers
Write-Host "── DSH MCP ──"
Copy-SyncItem ".dsh\mcp-servers"

# 4. 根配置（mcp.json 保护本机密钥）
Write-Host "── 根配置 ──"
Copy-SyncItem ".mcp.json" -PreserveLiveIfExists
Copy-SyncItem "AGENTS.md"
Copy-SyncItem "CLAUDE.md"
Copy-SyncItem "DEPLOY.md"

# 5. 脚本/命令
Write-Host "── 脚本 ──"
Copy-SyncItem "scripts"
Copy-SyncItem "commands"

# 6. 提示
Write-Host ""
if (-not $DryRun) {
    Write-Host "✅ 部署完成！"
    Write-Host ""
    Write-Host "提示:"
    Write-Host "  - 如果这是新机器且还没有 .mcp.json:"
    Write-Host "      copy .dc-sync\.mcp.json .mcp.json"
    Write-Host "      然后把 \${TUSHARE_TOKEN} \${GITHUB_PAT} \${DEEPSEEK_API_KEY} 替换成真实密钥"
    Write-Host "  - harmony-next 不在同步中，需要单独:"
    Write-Host "      git clone https://github.com/linhay/harmony-next.skills.git .deepcode\skills\harmony-next"
}