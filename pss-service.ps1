#Requires -Version 5.1
<#
.SYNOPSIS
    PSS Service Manager — Install, start, stop, and manage PSS as a background service.
.DESCRIPTION
    Uses Windows Task Scheduler to run PSS as a background process that starts
    automatically at logon. No console window. Logs to logs/ directory.
.PARAMETER Action
    install   — Register PSS as an auto-start background service
    uninstall — Remove the scheduled task and stop the server
    start     — Start the background server now
    stop      — Stop the running server
    restart   — Stop then start
    status    — Show current state (running/stopped, PID, uptime)
.EXAMPLE
    .\pss-service.ps1 install
    .\pss-service.ps1 status
    .\pss-service.ps1 stop
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'restart', 'status')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TaskName = 'PSS_Server'
$PidFile = Join-Path $ScriptDir 'data\pss.pid'

function Write-Status { param([string]$Msg, [string]$Color = 'White'); Write-Host "  $Msg" -ForegroundColor $Color }
function Write-Ok     { param([string]$Msg); Write-Status "[OK] $Msg" 'Green' }
function Write-Err    { param([string]$Msg); Write-Status "[ERROR] $Msg" 'Red' }
function Write-Warn   { param([string]$Msg); Write-Status "[WARN] $Msg" 'Yellow' }

function Get-PSSProcess {
    Get-Process python*, pythonw* -ErrorAction SilentlyContinue |
        Where-Object {
            try { $_.CommandLine -like '*pss.server*' -or $_.CommandLine -like '*pss_tray*' }
            catch { $false }
        }
}

function Find-Python {
    # Prefer pythonw (no console window)
    foreach ($name in 'pythonw', 'python') {
        $p = Get-Command $name -ErrorAction SilentlyContinue
        if ($p) { return $p.Source }
    }
    return $null
}

function Test-Installed {
    try { $null = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop; return $true }
    catch { return $false }
}

# ── INSTALL ──
function Do-Install {
    $pyPath = Find-Python
    if (-not $pyPath) { Write-Err 'Python not found in PATH.'; return }

    $envFile = Join-Path $ScriptDir '.env'
    if (-not (Test-Path $envFile)) { Write-Err '.env not found — run Install-PSS.ps1 first.'; return }

    # Use pythonw if available (no console window)
    $usePythonw = $pyPath -like '*pythonw*'
    if (-not $usePythonw) {
        $pythonwPath = Join-Path (Split-Path $pyPath) 'pythonw.exe'
        if (Test-Path $pythonwPath) { $pyPath = $pythonwPath; $usePythonw = $true }
    }

    $action = New-ScheduledTaskAction `
        -Execute $pyPath `
        -Argument '-m pss.server' `
        -WorkingDirectory $ScriptDir

    $trigger = New-ScheduledTaskTrigger -AtLogon

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval ([TimeSpan]::FromMinutes(1))

    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    if (Test-Installed) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
        Write-Ok "Updated existing task '$TaskName'."
    } else {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
        Write-Ok "Registered task '$TaskName'."
    }

    Write-Status "Python:    $pyPath" 'DarkGray'
    Write-Status "Console:   $(if ($usePythonw) {'Hidden (pythonw)'} else {'Visible (python)'})" 'DarkGray'
    Write-Status "Autostart: At logon" 'DarkGray'
    Write-Status "WorkDir:   $ScriptDir" 'DarkGray'
    Write-Host ''
    Write-Ok "PSS will auto-start at logon."
    Write-Status "Run '.\pss-service.ps1 start' to start now." 'Yellow'
}

# ── UNINSTALL ──
function Do-Uninstall {
    Do-Stop
    if (Test-Installed) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok "Task '$TaskName' removed."
    } else {
        Write-Warn "Task '$TaskName' not found."
    }
}

# ── START ──
function Do-Start {
    $existing = Get-PSSProcess
    if ($existing) {
        Write-Warn "PSS is already running (PID: $($existing.Id -join ', '))."
        return
    }

    if (Test-Installed) {
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        $proc = Get-PSSProcess
        if ($proc) {
            Write-Ok "PSS started (PID: $($proc.Id -join ', '))."
            Write-Status "Customizer:  http://localhost:8787/customizer" 'Cyan'
            Write-Status "Screensaver: http://localhost:8787/screensaver" 'Cyan'
        } else {
            Write-Err "Task started but process not found. Check logs."
        }
    } else {
        Write-Warn "Not installed as service. Run '.\pss-service.ps1 install' first."
        Write-Status "Or use '.\Start-PSS.ps1' for foreground mode." 'DarkGray'
    }
}

# ── STOP ──
function Do-Stop {
    $procs = Get-PSSProcess
    if (-not $procs) {
        Write-Status "PSS is not running."
        return
    }
    foreach ($p in $procs) {
        try { $p.Kill(); Write-Ok "Stopped PID $($p.Id)." }
        catch { Write-Warn "Could not stop PID $($p.Id): $_" }
    }
}

# ── STATUS ──
function Do-Status {
    $installed = Test-Installed
    $procs = Get-PSSProcess

    Write-Host ''
    Write-Host '  PSS Service Status' -ForegroundColor Cyan
    Write-Host '  ─────────────────────────────' -ForegroundColor DarkGray

    if ($installed) {
        $task = Get-ScheduledTask -TaskName $TaskName
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Status "Installed:   Yes ($($task.State))" 'Green'
        if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) {
            Write-Status "Last run:    $($info.LastRunTime)" 'DarkGray'
        }
    } else {
        Write-Status "Installed:   No" 'Yellow'
    }

    if ($procs) {
        foreach ($p in $procs) {
            $uptime = (Get-Date) - $p.StartTime
            $uptimeStr = '{0}d {1}h {2}m' -f $uptime.Days, $uptime.Hours, $uptime.Minutes
            Write-Status "Running:     Yes (PID $($p.Id), uptime $uptimeStr)" 'Green'
            $mem = [math]::Round($p.WorkingSet64 / 1MB, 0)
            Write-Status "Memory:      ${mem} MB" 'DarkGray'
        }
        Write-Host ''
        Write-Status "Customizer:  http://localhost:8787/customizer" 'Cyan'
        Write-Status "Screensaver: http://localhost:8787/screensaver" 'Cyan'
    } else {
        Write-Status "Running:     No" 'Yellow'
    }

    # Log file
    $logDir = Join-Path $ScriptDir 'logs'
    $latest = Get-ChildItem $logDir -Filter 'pss*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        $logSize = [math]::Round($latest.Length / 1KB, 0)
        Write-Status "Log:         $($latest.Name) (${logSize} KB)" 'DarkGray'
    }
    Write-Host ''
}

# ── DISPATCH ──
switch ($Action) {
    'install'   { Do-Install }
    'uninstall' { Do-Uninstall }
    'start'     { Do-Start }
    'stop'      { Do-Stop }
    'restart'   { Do-Stop; Start-Sleep -Seconds 2; Do-Start }
    'status'    { Do-Status }
}
