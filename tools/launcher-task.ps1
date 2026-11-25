# Manage the Windows scheduled task that owns the VGAC launcher.
#
# The task runs VGAC.exe --start for the current user, only while that user is
# logged on (so the window and tray icon show). It has no trigger: it starts on
# demand only. Built-in Windows commands only (ScheduledTasks module).
#
# Usage (from any folder):
#   powershell -ExecutionPolicy Bypass -File tools\launcher-task.ps1 -Register
#   powershell -ExecutionPolicy Bypass -File tools\launcher-task.ps1 -Run
#   powershell -ExecutionPolicy Bypass -File tools\launcher-task.ps1 -Status
#
# -Register creates the task, or updates it when it already exists.
# -Run starts the task now.
# -Status prints the task's state and last result.

[CmdletBinding()]
param(
    [switch]$Register,
    [switch]$Run,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$TaskName    = 'VGAC launcher'
$ProjectDir  = 'F:\Projects\VideoGameAssetCreator'
$ExePath     = Join-Path $ProjectDir 'VGAC.exe'
$Argument    = '--start'

function Get-VgacTask {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Show-VgacStatus {
    $task = Get-VgacTask
    if ($null -eq $task) {
        Write-Output "Task '$TaskName' is not registered."
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Output "Task:        $TaskName"
    Write-Output "State:       $($task.State)"
    Write-Output "Last run:    $($info.LastRunTime)"
    Write-Output "Last result: $($info.LastTaskResult)"
    Write-Output "Next run:    $($info.NextRunTime)"
    Write-Output "Priority:    $($task.Settings.Priority) (4 = Normal)"
    Write-Output "Action:      $($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
    Write-Output "Work dir:    $($task.Actions[0].WorkingDirectory)"
}

function Register-VgacTask {
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "VGAC.exe not found at $ExePath"
    }
    $user = "$env:USERDOMAIN\$env:USERNAME"

    $action = New-ScheduledTaskAction -Execute $ExePath -Argument $Argument -WorkingDirectory $ProjectDir

    # Interactive logon only: runs while this user is logged on, so the window and tray show.
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

    # No time limit; a second start while one runs is ignored.
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -Hidden:$false `
        -Priority 4

    # No -Trigger: the task starts on demand only.
    Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal `
        -Settings $settings -Description 'Runs the VGAC launcher (VGAC.exe --start) for the logged-on user. Started on demand.' `
        -Force | Out-Null

    Write-Output "Registered '$TaskName' for $user."
}

function Run-VgacTask {
    if ($null -eq (Get-VgacTask)) {
        throw "Task '$TaskName' is not registered. Run with -Register first."
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Started '$TaskName'."
}

$chosen = @($Register, $Run, $Status) | Where-Object { $_ }
if ($chosen.Count -ne 1) {
    Write-Output 'Give exactly one of -Register, -Run or -Status.'
    exit 2
}

if ($Register) { Register-VgacTask }
elseif ($Run)  { Run-VgacTask }
else           { Show-VgacStatus }
