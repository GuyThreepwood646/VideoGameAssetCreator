# Sets the SKINTOKEN_BLENDER_BIN environment variable to the Blender that setup found, so the SkinToken rigging node
# in ComfyUI can start Blender. Writes only the Windows environment variable of this account (or of this process, with
# -Target Process, which the tests use). It never touches a ComfyUI folder or its configuration.
#
# Exit codes: 0 = set now or already right, 1 = the Blender file is not there, 2 = the value could not be read back.
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [ValidateSet('User', 'Process')][string]$Target = 'User',
    [switch]$DryRun
)

$Name = 'SKINTOKEN_BLENDER_BIN'
if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
    Write-Host "  [fail] $Exe is not a file, so $Name is not set."
    exit 1
}
$now = [Environment]::GetEnvironmentVariable($Name, $Target)
if ($now -eq $Exe) {
    Write-Host "  [ok] $Name already points at $Exe ($Target)."
    exit 0
}
if ($DryRun) {
    Write-Host "  [dry run] would set $Name to $Exe in the $Target environment."
    exit 0
}
[Environment]::SetEnvironmentVariable($Name, $Exe, $Target)
if ([Environment]::GetEnvironmentVariable($Name, $Target) -ne $Exe) {
    Write-Host "  [fail] $Name could not be read back after setting it."
    exit 2
}
Write-Host "  [done] $Name is now $Exe for this Windows account."
Write-Host '  Restart ComfyUI so it reads the new value. A window that was already open keeps the old one.'
exit 0
