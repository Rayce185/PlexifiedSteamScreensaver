#Requires -Version 5.1
<#
.SYNOPSIS
    PSS - Plexified Steam Screensaver — First-Time Setup
.DESCRIPTION
    Checks prerequisites, installs Python dependencies, configures .env with
    Steam API key and auto-detected Steam path.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LogFile = Join-Path $ScriptDir 'install.log'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$Level] $Message"
    Add-Content -Path $LogFile -Value "$ts $line"
    switch ($Level) {
        'OK'    { Write-Host "  [OK] $Message" -ForegroundColor Green }
        'WARN'  { Write-Host "  [WARN] $Message" -ForegroundColor Yellow }
        'ERROR' { Write-Host "  [ERROR] $Message" -ForegroundColor Red }
        default { Write-Host "  $Message" }
    }
}

# Header
"PSS Install Log — $(Get-Date)" | Set-Content $LogFile
Write-Host ''
Write-Host '  ====================================' -ForegroundColor Cyan
Write-Host '   PSS — Plexified Steam Screensaver' -ForegroundColor Cyan
Write-Host '   First-Time Setup' -ForegroundColor Cyan
Write-Host '  ====================================' -ForegroundColor Cyan
Write-Host ''

# Python
try {
    $pyVer = & python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Log $pyVer 'OK'
} catch {
    Write-Log 'Python not found. Install from https://www.python.org/downloads/' 'ERROR'
    Write-Log 'Make sure "Add Python to PATH" is checked during installation.' 'ERROR'
    Read-Host '  Press Enter to exit'
    exit 1
}

# pip
try {
    $pipVer = & pip --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Log $pipVer 'OK'
} catch {
    Write-Log 'pip not found.' 'ERROR'
    Read-Host '  Press Enter to exit'
    exit 1
}

# Dependencies
Write-Host ''
Write-Log 'Installing dependencies...'
& pip install -r (Join-Path $ScriptDir 'requirements.txt') 2>&1 | Out-File $LogFile -Append
if ($LASTEXITCODE -ne 0) {
    Write-Log 'pip install failed. Check install.log for details.' 'ERROR'
    Read-Host '  Press Enter to exit'
    exit 1
}
Write-Log 'Dependencies installed.' 'OK'

# .env check
$envFile = Join-Path $ScriptDir '.env'
if (Test-Path $envFile) {
    Write-Host ''
    Write-Log '.env already exists — skipping setup.' 'OK'
} else {
    Write-Host ''
    Write-Host '  -----------------------------------------------' -ForegroundColor DarkGray
    Write-Host '  You need a Steam Web API Key to use PSS.'
    Write-Host '  Get one here: ' -NoNewline
    Write-Host 'https://steamcommunity.com/dev/apikey' -ForegroundColor Cyan
    Write-Host '  -----------------------------------------------' -ForegroundColor DarkGray
    Write-Host ''

    $steamKey = Read-Host '  Enter your Steam API Key'
    if ([string]::IsNullOrWhiteSpace($steamKey)) {
        Write-Log 'No key entered.' 'ERROR'
        Read-Host '  Press Enter to exit'
        exit 1
    }

    # Auto-detect Steam
    $steamDir = $null
    $candidates = @(
        "${env:ProgramFiles(x86)}\Steam",
        "$env:ProgramFiles\Steam",
        "$env:USERPROFILE\Steam"
    )
    foreach ($p in $candidates) {
        if (Test-Path (Join-Path $p 'config')) {
            $steamDir = $p
            Write-Log "Steam found at: $p" 'OK'
            break
        }
    }
    if (-not $steamDir) {
        Write-Log 'Steam not found at default paths.' 'WARN'
        $steamDir = Read-Host '  Enter Steam install path'
    }

    # Write .env
    @(
        "STEAM_API_KEY=$steamKey",
        "STEAM_PATH=$steamDir"
    ) | Set-Content $envFile
    Write-Log 'Configuration saved to .env' 'OK'
}

# Create directories
@('data', 'logs') | ForEach-Object {
    $dir = Join-Path $ScriptDir $_
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

Write-Host ''
Write-Host '  ====================================' -ForegroundColor Green
Write-Host '   Setup complete!' -ForegroundColor Green
Write-Host ''
Write-Host '   To start PSS: ' -NoNewline
Write-Host '.\Start-PSS.ps1' -ForegroundColor Yellow
Write-Host '  ====================================' -ForegroundColor Green
Write-Host ''
Read-Host '  Press Enter to exit'
