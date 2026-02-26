#Requires -Version 5.1
<#
.SYNOPSIS
    PSS - Plexified Steam Screensaver — Server Launcher
.DESCRIPTION
    Validates environment, loads .env, launches the PSS server, and opens
    the customizer in the default browser.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LogFile = Join-Path $ScriptDir 'start.log'
$Host.UI.RawUI.WindowTitle = 'PSS — Plexified Steam Screensaver'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $LogFile -Value "$ts [$Level] $Message"
    switch ($Level) {
        'OK'    { Write-Host "  [OK] $Message" -ForegroundColor Green }
        'WARN'  { Write-Host "  [WARN] $Message" -ForegroundColor Yellow }
        'ERROR' { Write-Host "  [ERROR] $Message" -ForegroundColor Red }
        default { Write-Host "  $Message" }
    }
}

"PSS Start Log — $(Get-Date)" | Set-Content $LogFile

Set-Location $ScriptDir

# .env
$envFile = Join-Path $ScriptDir '.env'
if (-not (Test-Path $envFile)) {
    Write-Log '.env not found — run Install-PSS.ps1 first!' 'ERROR'
    Read-Host '  Press Enter to exit'
    exit 1
}
Write-Log '.env found' 'OK'

# Load .env into environment
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), 'Process')
    }
}

# Python
try {
    $pyVer = & python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Log $pyVer 'OK'
} catch {
    Write-Log 'Python not found — run Install-PSS.ps1 first!' 'ERROR'
    Read-Host '  Press Enter to exit'
    exit 1
}

# FastAPI
try {
    $faVer = & python -c "import fastapi; print(f'FastAPI {fastapi.__version__}')" 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Log $faVer 'OK'
} catch {
    Write-Log 'FastAPI not installed — run Install-PSS.ps1 first!' 'ERROR'
    Read-Host '  Press Enter to exit'
    exit 1
}

# Database
$dbPath = Join-Path $ScriptDir 'data\pss.db'
if (Test-Path $dbPath) {
    $dbSize = [math]::Round((Get-Item $dbPath).Length / 1MB, 1)
    Write-Log "Database exists (${dbSize} MB)" 'OK'
} else {
    Write-Log 'No database yet — first run will fetch your Steam library'
}

Write-Host ''
Write-Host '  ====================================' -ForegroundColor Cyan
Write-Host '   PSS is running!' -ForegroundColor Cyan
Write-Host '' 
Write-Host '   Customizer:  ' -NoNewline
Write-Host 'http://localhost:8787/customizer' -ForegroundColor Yellow
Write-Host '   Screensaver: ' -NoNewline
Write-Host 'http://localhost:8787/screensaver' -ForegroundColor Yellow
Write-Host ''
Write-Host '   Press Ctrl+C to stop.' -ForegroundColor DarkGray
Write-Host '  ====================================' -ForegroundColor Cyan
Write-Host ''

# Open browser after 3s delay
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process 'http://localhost:8787/customizer'
} | Out-Null

# Launch server
try {
    & python -m pss.server
} finally {
    Write-Log "Server exited at $(Get-Date)"
    Write-Host ''
    Write-Host '  Server stopped.' -ForegroundColor Yellow
    Read-Host '  Press Enter to exit'
}
