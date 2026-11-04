# Creates "VGAC.lnk" on the user's desktop.
# Target: the portable exe in desktop\dist\ when one exists, otherwise the from-source launcher
# (node_modules\electron\dist\electron.exe . in desktop\, or cmd /c npm run start as a last resort).
# Usage: powershell -ExecutionPolicy Bypass -File make-shortcut.ps1 [-Source]
param(
    [switch]$Source   # force the from-source launcher even when a built exe exists
)

$ErrorActionPreference = 'Stop'
$desktopDir = $PSScriptRoot
$lnkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'VGAC.lnk'
$icon = Join-Path $desktopDir 'build\icon.ico'

$exe = $null
if (-not $Source) {
    $exe = Get-ChildItem -Path (Join-Path $desktopDir 'dist') -Filter '*.exe' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike '*Uninstall*' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.Description = 'Video Game Asset Creator'

if ($exe) {
    $lnk.TargetPath = $exe.FullName
    $lnk.WorkingDirectory = $exe.DirectoryName
    $lnk.IconLocation = "$($exe.FullName),0"
    $target = $exe.FullName
} else {
    $electron = Join-Path $desktopDir 'node_modules\electron\dist\electron.exe'
    if (Test-Path $electron) {
        $lnk.TargetPath = $electron
        $lnk.Arguments = '.'
        $target = "$electron ."
    } else {
        $lnk.TargetPath = $env:ComSpec
        $lnk.Arguments = '/c npm run start'
        $target = 'cmd /c npm run start'
    }
    $lnk.WorkingDirectory = $desktopDir
    if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
}
$lnk.Save()
"created $lnkPath -> $target"
