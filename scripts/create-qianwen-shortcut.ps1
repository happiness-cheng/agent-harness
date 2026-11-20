param([string]$DesktopPath = [Environment]::GetFolderPath('Desktop'))
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$launcherPath = Join-Path $projectRoot 'scripts\start-qianwen.ps1'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) { throw '未找到千问启动脚本。' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw '未找到项目venv，请先按README安装环境。' }
if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) { throw '当前桌面目录不可用。' }
$shortcutPath = Join-Path ([IO.Path]::GetFullPath($DesktopPath)) 'Adrian 千问工作台.lnk'
$commandPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $launcherPath
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -ine $commandPath -or $existing.Arguments -cne $arguments) {
        throw '桌面已有同名快捷方式但指向其他程序；已保留，未覆盖。'
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $commandPath
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Adrian 千问工作台：千问平台模型（默认 qwen3.8-flash），可在会话里切换模型'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\imageres.dll') + ', 109'
$shortcut.Save()
Write-Output ('已创建：' + $shortcutPath)