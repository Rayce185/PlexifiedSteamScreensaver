#Requires -Version 5.1
<#
.SYNOPSIS
    PSS - Plexified Steam Screensaver — Updater
.DESCRIPTION
    Downloads the latest version from GitHub, backs up data/, extracts new
    files, restores data/, and updates dependencies.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$DataDir = Join-Path $ScriptDir 'data'
$BackupDir = Join-Path $ScriptDir '_update_backup'
$ZipUrl = 'https://github.com/Rayce185/PlexifiedSteamScreensaver/archive/refs/heads/main.zip'
$TempZip = Join-Path $env:TEMP 'pss_update.zip'
$TempDir = Join-Path $env:TEMP 'pss_extract'

function Write-Step {
    param([int]$N, [string]$Message)
    Write-Host "  [$N/6] " -ForegroundColor DarkCyan -NoNewline
    Write-Host $Message
}

Write-Host ''
Write-Host '  ============================================' -ForegroundColor Cyan
Write-Host '   PSS — Updater' -ForegroundColor Cyan
Write-Host '  ============================================' -ForegroundColor Cyan
Write-Host ''

# Check if server is running
$running = Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like '*PSS*' -or $_.CommandLine -like '*pss.server*' }
if ($running) {
    Write-Host '  [!] PSS server appears to be running. Stop it first.' -ForegroundColor Red
    Read-Host '  Press Enter to exit'
    exit 1
}

# Step 1: Backup
if (Test-Path $DataDir) {
    Write-Step 1 'Backing up data folder...'
    if (Test-Path $BackupDir) { Remove-Item $BackupDir -Recurse -Force }
    Copy-Item $DataDir $BackupDir -Recurse -Force
    Write-Host '        Backed up to _update_backup\' -ForegroundColor DarkGray
} else {
    Write-Step 1 'No data folder — fresh install'
}

# Step 2: Download
Write-Step 2 'Downloading latest from GitHub...'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $ZipUrl -OutFile $TempZip -UseBasicParsing
} catch {
    Write-Host '  [!] Download failed. Check your internet connection.' -ForegroundColor Red
    Write-Host '      Your data is safe in _update_backup\' -ForegroundColor Yellow
    Read-Host '  Press Enter to exit'
    exit 1
}

# Step 3: Extract
Write-Step 3 'Extracting...'
if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force }
Expand-Archive -Path $TempZip -DestinationPath $TempDir -Force
$Src = Join-Path $TempDir 'PlexifiedSteamScreensaver-main'
if (-not (Test-Path $Src)) {
    Write-Host '  [!] Extraction failed — unexpected folder structure.' -ForegroundColor Red
    Read-Host '  Press Enter to exit'
    exit 1
}

# Step 4: Copy new files (skip data/)
Write-Step 4 'Updating files...'
Get-ChildItem $Src -Force | Where-Object { $_.Name -ne 'data' } | ForEach-Object {
    $dest = Join-Path $ScriptDir $_.Name
    if ($_.PSIsContainer) {
        Copy-Item $_.FullName $dest -Recurse -Force
    } else {
        Copy-Item $_.FullName $dest -Force
    }
}

# Step 5: Restore data
if (Test-Path $BackupDir) {
    Write-Step 5 'Restoring data...'
    if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }
    Copy-Item "$BackupDir\*" $DataDir -Recurse -Force
    Remove-Item $BackupDir -Recurse -Force
    Write-Host '        Data restored successfully.' -ForegroundColor DarkGray
} else {
    Write-Step 5 'No data to restore.'
}

# Step 6: Dependencies
Write-Step 6 'Installing dependencies...'
& pip install -r (Join-Path $ScriptDir 'requirements.txt') -q 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  [!] pip install failed. Try: pip install -r requirements.txt' -ForegroundColor Yellow
} else {
    Write-Host '        Dependencies up to date.' -ForegroundColor DarkGray
}

# Cleanup
Remove-Item $TempZip -Force -ErrorAction SilentlyContinue
Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '  ============================================' -ForegroundColor Green
Write-Host '   Update complete!' -ForegroundColor Green
Write-Host ''
Write-Host '   Run ' -NoNewline
Write-Host '.\Start-PSS.ps1' -ForegroundColor Yellow -NoNewline
Write-Host ' to launch the server.'
Write-Host '  ============================================' -ForegroundColor Green
Write-Host ''
Read-Host '  Press Enter to exit'
