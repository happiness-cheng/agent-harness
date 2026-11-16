# 面试演示一键启动：优先复用已运行的服务，不重复写同一个数据库。
# 用法：
#   .\scripts\start-interview-demo.ps1                 # 本地 Ollama（默认，免费、离线）
#   .\scripts\start-interview-demo.ps1 -Mode live      # DeepSeek（需余额和网络）
#   .\scripts\start-interview-demo.ps1 -Mode demo      # 离线演示（不调模型）
param(
    [ValidateSet("ollama", "live", "demo")]
    [string]$Mode = "ollama",
    [int]$Port = 8765,
    [string]$Database = "data/interview-demo.sqlite3",
    [string]$OllamaModel = "qwen3.5:4b",
    [string]$CredentialsFile = "$env:USERPROFILE\agent-harness-credentials.json"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "找不到虚拟环境：$python（先在项目根目录创建 .venv 并安装依赖）" }

function Open-Workbench([string]$Url) {
    $edge = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($edge) { Start-Process $edge $Url } else { Start-Process $Url }
}

function Test-Port([int]$Value) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Value -ErrorAction SilentlyContinue)
}

Set-Location $root

# 已经有服务：直接打开，绝不重复启动（同一个 SQLite 只允许一个写库进程）。
if (Test-Port $Port) {
    Write-Host "端口 $Port 已有服务，直接打开页面（不重复启动）。"
    Open-Workbench "http://127.0.0.1:$Port/"
    exit 0
}

# 数据库已有锁：说明后台在跑但端口不同，先让用户处理，避免两个进程写同一个库。
$lease = Join-Path $root "$Database.writer.lock"
if (Test-Path $lease) {
    $meta = Get-Content $lease -Raw -ErrorAction SilentlyContinue
    if ($meta -and $meta.Trim().Length -gt 0) {
        Write-Host "该数据库可能已有后台占用：$Database"
        Write-Host "先关闭原服务（网页“关闭服务”或结束对应 python 进程），再运行本脚本。"
    }
}

$arguments = @("-X", "utf8", "-m", "min_agent.web", "--database", $Database, "--port", "$Port", "--no-browser")
switch ($Mode) {
    "ollama" {
        $installed = & ollama list 2>$null | Select-String -SimpleMatch $OllamaModel
        if (-not $installed) { throw "Ollama 未安装模型 $OllamaModel；先运行：ollama pull $OllamaModel" }
        $arguments += @("--ollama", "--ollama-model", $OllamaModel, "--ollama-keep-alive", "8h")
    }
    "live" {
        if (-not (Test-Path $CredentialsFile)) { throw "找不到凭据文件：$CredentialsFile" }
        $arguments += @("--live", "--credentials-file", $CredentialsFile)
    }
    "demo" {
        $arguments += @("--demo")
    }
}

$out = Join-Path $root "data\interview-web-$Port.out.log"
$err = Join-Path $root "data\interview-web-$Port.err.log"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null

Write-Host "启动模式：$Mode；数据库：$Database；端口：$Port"
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
    -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $identity = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/_adrian/identity" -TimeoutSec 2
        if ($identity.ready) { $ready = $true; break }
    } catch { }
}

if (-not $ready) {
    Write-Host "服务未在 30 秒内就绪。看日志："
    Write-Host "  $out"
    Write-Host "  $err"
    exit 1
}

Write-Host "已就绪：provider=$($identity.provider)，PID=$($identity.pid)"
Open-Workbench "http://127.0.0.1:$Port/"
Write-Host "页面已打开：http://127.0.0.1:$Port/"
Write-Host "关闭：网页“关闭服务”，或 Stop-Process -Id $($identity.pid)"
if ($Mode -eq "ollama") { Write-Host "首次调用会加载模型（约 2 分钟），之后单轮约 10 秒。" }