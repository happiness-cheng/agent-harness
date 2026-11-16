param([switch]$Install)
# 构建 chat-demo 外壳：可选安装依赖（需要代理），产物复制到 Python 包内供 FastAPI 托管。
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$shell = Join-Path $root 'shell'
$target = Join-Path $root 'src\min_agent\web\shell'
if (-not (Test-Path (Join-Path $shell 'package.json'))) { throw 'shell 源码目录缺失。' }

Push-Location $shell
try {
    if ($Install -or -not (Test-Path (Join-Path $shell 'node_modules'))) {
        if (-not $env:HTTPS_PROXY) { Write-Host '提示：安装依赖通常需要代理，可先设置 HTTPS_PROXY。' -ForegroundColor Yellow }
        corepack pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'pnpm install 失败' }
    }
    $env:NODE_ENV = 'production'
    corepack pnpm exec rspack build -c rspack.config.ts
    if ($LASTEXITCODE -ne 0) { throw 'rspack build 失败' }
} finally {
    Pop-Location
}

if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Path $target | Out-Null
Copy-Item (Join-Path $shell 'dist\*') $target -Recurse -Force
$count = (Get-ChildItem $target -Recurse -File | Measure-Object).Count
$size = [math]::Round(((Get-ChildItem $target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 2)
Write-Output "已构建：$target（$count 个文件，$size MB）"