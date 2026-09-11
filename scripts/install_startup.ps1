# 添加 DeepCode PR 监控到开机启动项
 = Join-Path  = New-Object -ComObject WScript.Shell
.CreateShortcut(.TargetPath = 'wscript.exe'
.WorkingDirectory = 'F:\DEEPCODE'
.Description = 'DeepCode PR Merge Monitor'

