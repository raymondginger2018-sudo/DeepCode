# sync-push.ps1 — 收集本机改动并推送到 GitHub
# 用法: powershell -ExecutionPolicy Bypass -File .\sync-push.ps1 [-Message "自定义提交信息"]
#
# 等价于: python sync-build.py && git add -A && git commit && git push

param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║  DEEPCODE Sync — Collect & Push      ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

# 1. 收集活动目录最新内容
Write-Host "── 收集本机 Skills/配置 ──"
python sync-build.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ sync-build.py 失败" -ForegroundColor Red
    exit 1
}

# 2. 检查是否有变化
git add -A
$changes = git status --porcelain
if (-not $changes) {
    Write-Host "✅ 没有变化，无需推送"
    exit 0
}
Write-Host "改动:"
$changes | ForEach-Object { Write-Host "    $_" }

# 3. 提交
if (-not $Message) {
    $Message = "sync: update $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}
git commit -m $Message --no-verify
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ commit 失败" -ForegroundColor Red
    exit 1
}

# 4. 推送
Write-Host "── 推送 ──"
git push origin sync/config
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ 推送完成！" -ForegroundColor Green
} else {
    Write-Host "❌ push 失败（可能需要先 pull）" -ForegroundColor Red
    exit 1
}