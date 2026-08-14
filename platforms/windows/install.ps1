[CmdletBinding()]
param(
    [string]$ArchiveDir = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Weibo Evernote Inbox'),
    [string]$PythonPath = ''
)

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
        throw '找不到 Python 3。請先安裝 Python 3.9 或更新版本，再重新執行。'
    }
}

$VersionText = & $PythonPath @PythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or [version]$VersionText -lt [version]'3.9') {
    throw "需要 Python 3.9 或更新版本；目前偵測到 $VersionText"
}

& $PythonPath @PythonArgs (Join-Path $ProjectDir 'bridge\inbox_bridge.py') install `
    --extension-dir (Join-Path $ProjectDir 'extension') `
    --archive-dir $ArchiveDir
if ($LASTEXITCODE -ne 0) {
    throw "安裝失敗，橋接器回傳代碼 $LASTEXITCODE"
}

Write-Host ''
Write-Host '下一步：'
Write-Host '1. 在 Chrome 打開 chrome://extensions'
Write-Host '2. 開啟右上角「開發人員模式」'
Write-Host '3. 點「載入未封裝項目」，選擇：'
Write-Host "   $(Join-Path $ProjectDir 'extension')"
Write-Host '4. 重新載入已打開的微博頁面'
