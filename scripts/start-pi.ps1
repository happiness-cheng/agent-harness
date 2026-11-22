# 通用四工具工作台：read / write / edit / bash（pi 式）。
# 默认工作区是仓库根；bash 以当前用户权限运行，不是沙箱。
param(
    [int]$Port = 8766,
    [string]$Database = 'data/adrian-pi.sqlite3',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
$url = "http://127.0.0.1:$Port/"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Host '未找到项目 .venv，请先按 README 安装环境。' -ForegroundColor Yellow
    Read-Host '按回车关闭'
    exit 1
}
$env:PYTHONPATH = Join-Path $root 'src'

if (-not $NoBrowser) {
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
        "Start-Sleep -Seconds 4; Start-Process '$url'"
    ) | Out-Null
}

# --generic-tools 只注册 read/write/edit/bash；--workspace-root 显式限定工作区。
# 该模式不经过 Plan/Build 目标确认门；bash 能访问工作区外文件，请只在可信目录使用。
& $python -X utf8 -m min_agent.web --qianwen --generic-tools --database $Database --port $Port --workspace-root $root
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host "启动失败（退出码 $code）。常见原因：端口被占用、数据库已有服务、凭据文件缺 qianwen_api_key。" -ForegroundColor Yellow
    Read-Host '按回车关闭'
    exit $code
}