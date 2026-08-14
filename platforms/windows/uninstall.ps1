[CmdletBinding()]
param([string]$PythonPath = '')

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PythonArgs = @()

if ($PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "找不到指定的 Python：$PythonPath"
    }
} else {
    $CodexPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $CodexPython -PathType Leaf) {
        $PythonPath = $CodexPython
    } else {
        $PythonCommand = Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $PythonCommand) {
            $PythonCommand = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if (-not $PythonCommand) {
            $PythonCommand = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($PythonCommand) {
                $PythonArgs = @('-3')
            }
        }
        if ($PythonCommand) {
            $PythonPath = $PythonCommand.Source
        }
    }
    if (-not $PythonPath) {
        throw '找不到 Python 3，無法執行卸載程式。'
    }
}

& $PythonPath @PythonArgs (Join-Path $ProjectDir 'bridge\inbox_bridge.py') uninstall
if ($LASTEXITCODE -ne 0) {
    throw "卸載失敗，橋接器回傳代碼 $LASTEXITCODE"
}

Write-Host ''
Write-Host '請再到 chrome://extensions 移除「微博 Evernote 本機收件匣」。'
Write-Host '卸載不會刪除本機微博、圖片、SQLite 或 ENEX。'
