<#
.SYNOPSIS
  Sets up the suggested worn item finder for Video game asset creator: its own Python environment and two free models.

.DESCRIPTION
  Default mode CHANGES NOTHING. It prints what is there and what -Install would download.
  -Install  creates local\finder\venv (CPU PyTorch, transformers, Pillow, numpy) and downloads the two models into
            local\finder\hf. It asks once before the download and again before the environment is made.
  -DryRun   with -Install: prints the steps and the sizes, and does neither.

  Models (licence and source, also in backend\vgac\data\models.yaml under finders):
    IDEA-Research/grounding-dino-tiny   Apache-2.0   about 690 MB   https://huggingface.co/IDEA-Research/grounding-dino-tiny
    facebook/sam2.1-hiera-small         Apache-2.0   about 185 MB   https://huggingface.co/facebook/sam2.1-hiera-small
  The PyTorch CPU wheel is about 200 MB. Total downloads are under 1.1 GB.

  Nothing is installed into the backend venv, and nothing needs a graphics card. The app runs the finder only when the
  person asks for a suggestion, and offers none while these models are missing.

.PARAMETER Install
  Do the install (asks first).
.PARAMETER DryRun
  With -Install: print the steps only.
.PARAMETER Python
  The Python 3.10 or newer to make the environment with (default: python on PATH).
#>
[CmdletBinding()]
param(
  [switch]$Install,
  [switch]$DryRun,
  [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$finder = Join-Path $root 'local\finder'
$venv = Join-Path $finder 'venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$hf = Join-Path $finder 'hf'
$models = @('IDEA-Research/grounding-dino-tiny', 'facebook/sam2.1-hiera-small')

$haveEnv = Test-Path $venvPy
$haveModels = (Test-Path (Join-Path $hf 'hub')) -and ((Get-ChildItem (Join-Path $hf 'hub') -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*grounding-dino-tiny*' -or $_.Name -like '*sam2.1-hiera-small*' }).Count -eq 2)
Write-Host "Item finder: environment $(if ($haveEnv) { 'present' } else { 'missing' }), models $(if ($haveModels) { 'present' } else { 'missing' })."

if (-not $Install) {
  Write-Host 'Nothing changed. Run with -Install to set it up (you are asked first). -DryRun -Install shows the steps.'
  exit 0
}

$steps = @(
  "make the environment at $venv with $Python",
  'install PyTorch (CPU build), transformers, timm, Pillow and numpy into it (about 300 MB)',
  "download the two models into $hf (about 875 MB)"
)
if ($DryRun) {
  $steps | ForEach-Object { Write-Host "would: $_" }
  exit 0
}
$answer = Read-Host "Set up the item finder now? Type y to continue"
if ($answer -notmatch '^(y|yes)$') { Write-Host 'Stopped. Nothing changed.'; exit 0 }

New-Item -ItemType Directory -Force -Path $finder, $hf | Out-Null
if (-not $haveEnv) {
  & $Python -m venv $venv
  & $venvPy -m pip install --quiet --upgrade pip
  & $venvPy -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu
  & $venvPy -m pip install --quiet transformers timm pillow numpy
}
$env:HF_HOME = $hf
$code = @"
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection, Sam2Processor, Sam2Model
AutoProcessor.from_pretrained('IDEA-Research/grounding-dino-tiny')
AutoModelForZeroShotObjectDetection.from_pretrained('IDEA-Research/grounding-dino-tiny')
Sam2Processor.from_pretrained('facebook/sam2.1-hiera-small')
Sam2Model.from_pretrained('facebook/sam2.1-hiera-small')
print('models ok')
"@
& $venvPy -c $code
Write-Host 'Item finder set up. Restart the backend so it finds the environment.'
