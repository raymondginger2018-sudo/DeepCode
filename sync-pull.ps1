# sync-pull.ps1 — 从 GitHub 拉取最新配置并部署到本机活动位置
# 用法: powershell -ExecutionPolicy Bypass -File .\sync-pull.ps1
#
# 等价于: git pull origin sync/config && sync-deploy.ps1

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║  DEEPCODE Sync — Pull & Deploy       ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

# 1. 检查未提交改动
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "⚠️  sync 仓库有未提交改动:" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "    $_" }
    Write-Host ""
    $ans = Read-Host "先提交再拉取? (y=提交并继续 / n=放弃改动 / c=取消)"
    if ($ans -eq 'c' -or $ans -eq 'C') { Write-Host "取消"; exit 0 }
    if ($ans -eq 'y' -or $ans -eq 'Y') {
        git add -A
        git commit -m "sync: auto-commit before pull $(Get-Date -Format 'yyyy-MM-dd HH:mm')" --no-verify
    } else {
        git checkout -- .
        git clean -fd
    }
}

# 2. 拉取
Write-Host "── git pull ──"
git pull --rebase origin sync/config
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ pull 失败，请手动解决冲突后重试" -ForegroundColor Red
    exit 1
}

# 3. 部署
Write-Host ""
Write-Host "── 部署到活动位置 ──"
& "$Here\sync-deploy.ps1"

Write-Host ""
Write-Host "✅ 拉取并部署完成！" -ForegroundColor Green