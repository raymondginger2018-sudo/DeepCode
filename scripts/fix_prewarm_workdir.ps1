# 修复 QuantPrewarm 计划任务 — WORKDIR 指向项目根目录 (quant_trader.py 所在位置)
$ErrorActionPreference = "Stop"
$py = "C:\Users\raymo\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"
$root = "F:\DEEPCODE"
$trader = "F:\DEEPCODE\quant_trader.py"

# QuantPrewarm: 用绝对路径运行 quant_trader.py prewarm
$a = New-ScheduledTaskAction -Execute $py -Argument ('-X utf8 "' + $trader + '" prewarm') -WorkingDirectory $root
Set-ScheduledTask -TaskName "QuantPrewarm" -Action $a | Out-Null
Write-Host "QuantPrewarm fixed"

# 验证
Get-ScheduledTask -TaskName "QuantPrewarm" | ForEach-Object {
  $t = $_
  $i = $_ | Get-ScheduledTaskInfo
  $a = $_.Actions[0]
  Write-Host ("{0} | EXEC={1} | ARGS={2} | WORKDIR={3} | LastResult=0x{4:X} | NextRun={5}" -f $t.TaskName, $a.Execute, $a.Arguments, $a.WorkingDirectory, $i.LastTaskResult, $i.NextRunTime)
}
