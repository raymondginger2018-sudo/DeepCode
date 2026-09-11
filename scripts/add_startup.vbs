' DeepCode PR Monitor - 添加到开机启动项
Dim startupPath, shortcutPath, shell, shortcut

startupPath = CreateObject("WScript.Shell").SpecialFolders("Startup")
shortcutPath = startupPath & "\DeepCode PRWatch.lnk"

Set shell = CreateObject("WScript.Shell")
Set shortcut = shell.CreateShortcut(shortcutPath)
shortcut.TargetPath = "wscript.exe"
shortcut.Arguments = "F:\DEEPCODE\scripts\start_pr_watch.vbs"
shortcut.WorkingDirectory = "F:\DEEPCODE"
shortcut.WindowStyle = 7
shortcut.Description = "DeepCode PR Merge Monitor"
shortcut.Save

MsgBox "[OK] 开机启动项已添加!" & vbCrLf & vbCrLf & "位置: " & shortcutPath, vbInformation, "DeepCode PR 监控"
