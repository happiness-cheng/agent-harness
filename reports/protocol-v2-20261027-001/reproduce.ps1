param([Parameter(Mandatory=$true)][string]$OutputName)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if ($OutputName -notmatch '^[A-Za-z0-9_-]+$') { throw 'OutputName只允许新报告目录名' }
$outputDir = Join-Path $projectRoot ('reports/' + $OutputName)
if (Test-Path -LiteralPath $outputDir) { throw '输出已存在，拒绝覆盖' }
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '先按README准备本地依赖环境；本脚本不联网安装' }
New-Item -ItemType Directory -Path $outputDir | Out-Null
foreach ($name in @('prepare.py','wire.py','protocol.py','preflight.py','audit.py','test_resource_entry.py','test_research_last_read.py','export_diffs.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $outputDir $name)
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'implementation') -Destination (Join-Path $outputDir 'implementation') -Recurse
Push-Location $projectRoot
try {
    & $pythonExe -X utf8 (Join-Path $outputDir 'prepare.py')
    if ($LASTEXITCODE -ne 0) { throw '准备失败' }
    & $pythonExe -X utf8 (Join-Path $outputDir 'wire.py')
    if ($LASTEXITCODE -ne 0) { throw '装配失败' }
    foreach ($variant in @('baseline','candidate')) {
        Copy-Item -LiteralPath (Join-Path $outputDir 'test_resource_entry.py') -Destination (Join-Path $outputDir ($variant + '/workspace/tests/integration/test_research_resource_entry_v2.py'))
    }
    Copy-Item -LiteralPath (Join-Path $outputDir 'test_research_last_read.py') -Destination (Join-Path $outputDir 'candidate/workspace/tests/integration/test_research_last_read.py')
    & $pythonExe -X utf8 (Join-Path $outputDir 'preflight.py') --out (Join-Path $outputDir 'preflight-02')
    if ($LASTEXITCODE -ne 0) { throw '离线预检失败' }
    & $pythonExe -X utf8 (Join-Path $outputDir 'audit.py')
    if ($LASTEXITCODE -ne 0) { throw '独立核对失败' }
} finally { Pop-Location }
# 本脚本没有live、credentials或Codex CLI路径；不运行付费生成。
