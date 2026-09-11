# 修复 Quant 计划任务 — 使用完整 python 路径 + 正确工作目录
$ErrorActionPreference = "Stop"
$py = "C:\Users\raymo\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"
$qdir = "F:\DEEPCODE\quant_trading"
$scriptPath = "F:\DEEPCODE\quant_trading\daily_auto_update.py"

# 2. QuantEveningUpdate: 完整 python 路径 (sina_eod 收盘同步)
$a2 = New-ScheduledTaskAction -Execute $py -Argument ('"' + $scriptPath + '"') -WorkingDirectory $qdir
Set-ScheduledTask -TaskName "QuantEveningUpdate" -Action $a2 | Out-Null
Write-Host "QuantEveningUpdate fixed"

# 3. QuantMorningUpdate: 完整 python 路径 (tushare-only)
$a3 = New-ScheduledTaskAction -Execute $py -Argument ('"' + $scriptPath + '" --tushare-only') -WorkingDirectory $qdir
Set-ScheduledTask -TaskName "QuantMorningUpdate" -Action $a3 | Out-Null
Write-Host "QuantMorningUpdate fixed"

# 验证全部 4 个任务
Write-Host ""
Get-ScheduledTask -TaskName "QuantDailyUpdate","QuantEveningUpdate","QuantMorningUpdate","QuantPrewarm" | ForEach-Object {
  $t = $_
  $i = $_ | Get-ScheduledTaskInfo
  $a = $_.Actions[0]
  Write-Host ("{0} | EXEC={1} | ARGS={2} | WORKDIR={3} | LastResult=0x{4:X} | NextRun={5}" -f $t.TaskName, $a.Execute, $a.Arguments, $a.WorkingDirectory, $i.LastTaskResult, $i.NextRunTime)
}
