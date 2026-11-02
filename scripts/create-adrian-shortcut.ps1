param([string]$DesktopPath = [Environment]::GetFolderPath('Desktop'))
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$launcherPath = Join-Path $projectRoot 'scripts\start-adrian.cmd'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) { throw '未找到Adrian启动脚本。' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw '未找到项目venv，请先按README安装环境。' }
if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) { throw '当前桌面目录不可用。' }
$shortcutPath = Join-Path ([IO.Path]::GetFullPath($DesktopPath)) 'Adrian.lnk'
$commandPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
$arguments = '/d /c ""{0}""' -f $launcherPath
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -ine $commandPath -or $existing.Arguments -cne $arguments) {
        throw '已有Adrian快捷方式指向其他程序；已保留，未覆盖。'
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $commandPath
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Adrian本地工作台：真实Runtime与按需只读长期Memory'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ', 13'
$shortcut.Save()
Write-Output ('已创建：' + $shortcutPath)
