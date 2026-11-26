<#
.SYNOPSIS
  Sets up the suggested worn item finder for Video game asset creator: its own Python environment and two free models.

.DESCRIPTION
  Default mode CHANGES NOTHING. It prints what is there and what -Install would download.
  -Install  creates local\finder\venv (CPU PyTorch, transformers, Pillow, numpy) and fetches the two models' files into
            local\finder\hf. The files come through the app's own downloader (backend\vgac\finder_setup.py): each one is
            checked against the SHA-256 pinned in setup\downloads.json (finder_* entries), from its official Hugging Face
            address. A file already in place is checked, not fetched again; a file that does not match is never used.
  -DryRun   with -Install: prints the steps and the sizes, and does neither.

  Models (licence and source, also in backend\vgac\data\models.yaml under finders):
    IDEA-Research/grounding-dino-tiny   Apache-2.0   about 690 MB   https://huggingface.co/IDEA-Research/grounding-dino-tiny
    facebook/sam2.1-hiera-small         Apache-2.0   about 185 MB   https://huggingface.co/facebook/sam2.1-hiera-small
  The PyTorch CPU wheel is about 200 MB. Total downloads are under 1.1 GB.

  Nothing is installed into the backend venv, and nothing needs a graphics card. The app runs the finder only when the
  person asks for a suggestion, and offers none while these models are missing. The backend venv (backend\.venv) must
  exist for the model files step; the environment step does not need it.

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
$backend = Join-Path $root 'backend'
$backendPy = Join-Path $backend '.venv\Scripts\python.exe'

$haveEnv = Test-Path $venvPy

# Runs the app's finder file step (backend\vgac\finder_setup.py) with the backend's Python. Returns its exit code:
# 0 present and matching (or fetched), 1 missing (check only), 2 a file does not match its pin, 3 failed or no backend.
function Invoke-FinderFiles([switch]$CheckOnly) {
  if (-not (Test-Path $backendPy)) {
    Write-Host '  backend\.venv is missing. Run setup\Check-Setup.ps1 -Install first.'
    return 3
  }
  $mode = @('-m', 'vgac.finder_setup')
  if ($CheckOnly) { $mode += '--check' }
  Push-Location $backend
  try {
    & $backendPy @mode | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
  } finally {
    Pop-Location
  }
}

$filesCode = Invoke-FinderFiles -CheckOnly
$haveModels = ($filesCode -eq 0)
Write-Host "Item finder: environment $(if ($haveEnv) { 'present' } else { 'missing' }), models $(if ($haveModels) { 'present' } else { 'missing' })."

if (-not $Install) {
  Write-Host 'Nothing changed. Run with -Install to set it up (you are asked first). -DryRun -Install shows the steps.'
  exit 0
}

$steps = @(
  "make the environment at $venv with $Python",
  'install PyTorch (CPU build), transformers, timm, Pillow and numpy into it (about 300 MB)',
  "fetch the model files into $hf through the app's downloader (missing files only, each checked against its pinned SHA-256; about 875 MB)"
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

$filesCode = Invoke-FinderFiles
if ($filesCode -ne 0) {
  throw "The item finder model files were not set up (exit $filesCode). Read the lines above, fix the cause, then run this again."
}

# Loads the models from the files just checked. local_files_only: this step cannot download anything.
$env:HF_HOME = $hf
$code = @"
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection, Sam2Processor, Sam2Model
AutoProcessor.from_pretrained('IDEA-Research/grounding-dino-tiny', local_files_only=True)
AutoModelForZeroShotObjectDetection.from_pretrained('IDEA-Research/grounding-dino-tiny', local_files_only=True)
Sam2Processor.from_pretrained('facebook/sam2.1-hiera-small', local_files_only=True)
Sam2Model.from_pretrained('facebook/sam2.1-hiera-small', local_files_only=True)
print('models ok')
"@
& $venvPy -c $code
Write-Host 'Item finder set up. Restart the backend so it finds the environment.'
