# 千问工作台启动器：前台运行（日志可见，Ctrl+C 停止），就绪后自动打开页面。
# 只用项目 venv 与项目外凭据文件；不读取系统代理，不需要命令行参数。
param(
    [int]$Port = 8765,
    [string]$Database = 'data/adrian-qianwen.sqlite3',
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

# 1) 端口上已有服务时，先分清是哪一个：避免误以为启动了千问。
$provider = $null
try {
    $identity = Invoke-RestMethod -Uri ($url + '_adrian/identity') -TimeoutSec 2
    $provider = [string]$identity.provider
} catch {
    $provider = $null
}
if ($provider) {
    if ($provider -eq 'qianwen.chat') {
        if ($NoBrowser) {
            Write-Host "千问工作台已在运行：$url" -ForegroundColor Green
        } else {
            Start-Process $url
            Write-Host "千问工作台已在运行，已打开页面：$url" -ForegroundColor Green
        }
        exit 0
    }
    Write-Host "端口 $Port 已被其他服务占用（provider=$provider），未启动千问。" -ForegroundColor Yellow
    Write-Host '请先在原页面点右上角“关闭服务”，再双击本快捷方式。'
    if (-not $NoBrowser) { Start-Process $url }
    Read-Host '按回车关闭'
    exit 1
}

# 2) 后台等 4 秒再开浏览器，前台保持日志可见。
if (-not $NoBrowser) {
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
        "Start-Sleep -Seconds 4; Start-Process '$url'"
    ) | Out-Null
}

# 3) 前台启动：--qianwen 使用凭据文件里的 qianwen_api_key，默认 qwen3.8-flash。
# --workspace-root 显式开启只读文件工具（fs_list / fs_read），根目录就是项目目录。
& $python -X utf8 -m min_agent.web --qianwen --database $Database --port $Port --workspace-root $root
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host "启动失败（退出码 $code）。常见原因：端口被占用、该数据库已有服务、凭据文件缺 qianwen_api_key。" -ForegroundColor Yellow
    Read-Host '按回车关闭'
    exit $code
}