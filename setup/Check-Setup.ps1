<#
.SYNOPSIS
  First-time setup helper for Video Game Asset Creator. Looks first, reports, then (only if asked) installs the parts
  that live entirely inside the project folder.

.DESCRIPTION
  Default mode (no switches) CHANGES NOTHING. It prints one table, each line Ready, Missing or Needs you, with the next
  step in plain words, and a one-line summary.

  -Install   After the report, offers the project-folder steps (each one asks first):
               1. create backend\.venv and install backend\requirements.txt
               2. npm install and npm run build in frontend\
               3. npm install in desktop\
               4. python backend\scripts\make_sample_glb.py
             A step that is already done is skipped. Nothing is upgraded or replaced. No existing settings file is
             edited: when the paths on this PC differ from backend\config.yaml or desktop\config.json, a proposed copy
             is written beside it (config.proposed.yaml / config.proposed.json) and the script says so.
  -Yes       Answers "yes" to every question in -Install mode.

  The ComfyUI models folders are never listed. Model files are checked by exact name, through each ComfyUI's own
  answers (the same way the app's own check does), and add-ons by their exact path. ComfyUI itself, its node packs, the
  models, Blender, Unity, Git, Node and Python are never installed by this script.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File setup\Check-Setup.ps1
  powershell -ExecutionPolicy Bypass -File setup\Check-Setup.ps1 -Install
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Yes
)

$ErrorActionPreference = 'Continue'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rows = New-Object System.Collections.ArrayList

function Add-Row([string]$Item, [string]$Status, [string]$Found, [string]$Next = '') {
    [void]$rows.Add([pscustomobject]@{ Item = $Item; Status = $Status; Found = $Found; 'Next step' = $Next })
}

function Read-Text([string]$Path) {
    if (Test-Path -LiteralPath $Path) { return [System.IO.File]::ReadAllText($Path) }
    return ''
}

function Get-Cmd([string]$Name) {
    $c = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}

function Invoke-Quiet([string]$Exe, [string[]]$ArgList) {
    # Runs a read-only command, returns its first output line (or $null). Never throws.
    try { $o = & $Exe @ArgList 2>$null | Select-Object -First 1; if ($o) { return "$o".Trim() } } catch { }
    return $null
}

function Get-Json([string]$Url, [int]$Seconds = 6) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Seconds
        return ($r.Content | ConvertFrom-Json)
    } catch { return $null }
}

# --- settings (read only, with plain regex because Windows PowerShell has no YAML reader) -------------------------------
$cfgText = Read-Text (Join-Path $Root 'backend\config.yaml')
function Cfg-Value([string]$Key) {
    $m = [regex]::Match($cfgText, "(?m)^$Key\s*:\s*(.+?)\s*$")
    if ($m.Success) { return $m.Groups[1].Value.Trim('"', "'") }
    return $null
}
function Cfg-Url([string]$Inst) {
    $m = [regex]::Match($cfgText, "(?ms)^  ${Inst}:\s*\r?\n\s+url:\s*(\S+)")
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}
$cfgBlender = Cfg-Value 'blender_exe'
$cfgUnity   = Cfg-Value 'unity_exe'
$lorasDir   = Cfg-Value 'loras_dir'
$urlA = Cfg-Url 'a'; if (-not $urlA) { $urlA = 'http://127.0.0.1:8188' }
$urlB = Cfg-Url 'b'; if (-not $urlB) { $urlB = 'http://127.0.0.1:8189' }

# Tool paths: the settings file first (when its path exists), then PATH, then the usual places. Never a fixed drive.
function Find-Blender {
    if ($cfgBlender -and (Test-Path -LiteralPath $cfgBlender)) { return @{ Path = $cfgBlender; From = 'backend\config.yaml' } }
    $onPath = Get-Cmd 'blender'
    if ($onPath) { return @{ Path = $onPath; From = 'PATH' } }
    $dirs = @((Join-Path $Root 'tools'), "$env:ProgramFiles\Blender Foundation", "${env:ProgramFiles(x86)}\Blender Foundation", "$env:LOCALAPPDATA\Programs")
    foreach ($d in $dirs) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        $hit = Get-ChildItem -LiteralPath $d -Filter 'blender.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return @{ Path = $hit.FullName; From = 'a usual place' } }
    }
    return $null
}
function Find-Unity {
    if ($cfgUnity -and (Test-Path -LiteralPath $cfgUnity)) { return @{ Path = $cfgUnity; From = 'backend\config.yaml' } }
    $hubs = @("$env:ProgramFiles\Unity\Hub\Editor", "$env:LOCALAPPDATA\Programs\Unity\Hub\Editor")
    foreach ($h in $hubs) {
        if (-not (Test-Path -LiteralPath $h)) { continue }
        $hit = Get-ChildItem -LiteralPath $h -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'Editor\Unity.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($hit) { return @{ Path = $hit; From = 'the Unity Hub folder' } }
    }
    return $null
}
$foundBlender = Find-Blender
$foundUnity = Find-Unity
$blenderExe = if ($foundBlender) { $foundBlender.Path } else { $cfgBlender }
$unityExe = if ($foundUnity) { $foundUnity.Path } else { $cfgUnity }

# --- 1. Windows ---------------------------------------------------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
if ($os) {
    $build = [int]$os.BuildNumber
    if ($build -ge 19041) { Add-Row 'Windows' 'Ready' "$($os.Caption), build $build" }
    else { Add-Row 'Windows' 'Needs you' "$($os.Caption), build $build" 'Update to Windows 10 (2004) or newer, or Windows 11.' }
} else { Add-Row 'Windows' 'Needs you' 'Could not read the Windows version.' 'Check that this is Windows 10 or 11.' }

# --- 2. Graphics cards --------------------------------------------------------------------------------------------------
$gpus = @()
$smi = Get-Cmd 'nvidia-smi'
if ($smi) {
    try { $gpus = @(& $smi '--query-gpu=name,memory.total' '--format=csv,noheader,nounits' 2>$null | Where-Object { $_ }) } catch { }
}
if ($gpus.Count -eq 0) {
    Add-Row 'Graphics card(s)' 'Needs you' 'No NVIDIA card answered (nvidia-smi not found or empty).' 'Install an NVIDIA graphics driver by hand. The app needs an NVIDIA card (CUDA).'
} else {
    $desc = ($gpus | ForEach-Object { $p = $_ -split ',\s*'; "$($p[0]) ($([math]::Round([double]$p[1] / 1024, 0)) GB)" }) -join '; '
    $maxGb = ($gpus | ForEach-Object { [double](($_ -split ',\s*')[1]) / 1024 } | Measure-Object -Maximum).Maximum
    if ($gpus.Count -ge 2) { Add-Row 'Graphics cards' 'Ready' "$($gpus.Count) cards: $desc" }
    elseif ($maxGb -ge 10) {
        Add-Row 'Graphics card (one)' 'Ready' "1 card: $desc. Fine for one card. The app was built on two cards, so with one it runs one graphics step at a time." 'In the app (Health page, "Graphics cards") switch off the card you do not have, so its work moves to the one you do. See docs\FIRST-TIME-SETUP.md, "One graphics card".'
    } else {
        Add-Row 'Graphics card (one)' 'Needs you' "1 card: $desc. Under 10 GB of graphics memory: untested; the largest steps (the 3D model, the side views) may run out of memory." 'Try it and read the app''s error messages; see docs\FIRST-TIME-SETUP.md, "One graphics card".'
    }
}

# --- 3. Disk ------------------------------------------------------------------------------------------------------------
try {
    $drive = New-Object System.IO.DriveInfo ($Root.Substring(0, 1))
    $freeGb = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    if ($freeGb -ge 20) { Add-Row "Free disk ($($drive.Name))" 'Ready' "$freeGb GB free on the project's drive" }
    elseif ($freeGb -ge 5) { Add-Row "Free disk ($($drive.Name))" 'Needs you' "$freeGb GB free" 'Free up space: assets and exports need at least 20 GB here.' }
    else { Add-Row "Free disk ($($drive.Name))" 'Needs you' "$freeGb GB free (very low)" 'Free up space before building anything.' }
} catch { Add-Row 'Free disk' 'Needs you' 'Could not read the project drive.' }

# --- 4. Python, Node, npm, Git ------------------------------------------------------------------------------------------
$py313 = $null
$pyLauncher = Get-Cmd 'py'
if ($pyLauncher) {
    $v = Invoke-Quiet $pyLauncher @('-3.13', '--version')
    if ($v -match 'Python 3\.13') { $py313 = @($pyLauncher, '-3.13') ; $pyText = $v }
}
if (-not $py313) {
    $pyExe = Get-Cmd 'python'
    if ($pyExe) {
        $v = Invoke-Quiet $pyExe @('--version')
        if ($v -match 'Python 3\.13') { $py313 = @($pyExe); $pyText = $v }
        elseif ($v) { $pyOther = $v }
    }
}
if ($py313) { Add-Row 'Python 3.13' 'Ready' $pyText }
elseif ($pyOther) { Add-Row 'Python 3.13' 'Missing' "Found $pyOther, not 3.13." 'Install Python 3.13 from python.org (tick "Add to PATH"), then run this check again.' }
else { Add-Row 'Python 3.13' 'Missing' 'Not found.' 'Install Python 3.13 from python.org (tick "Add to PATH"), then run this check again.' }

$nodeExe = Get-Cmd 'node'
$npmCmd = Get-Cmd 'npm.cmd'; if (-not $npmCmd) { $npmCmd = Get-Cmd 'npm' }
$nodeV = $null; if ($nodeExe) { $nodeV = Invoke-Quiet $nodeExe @('--version') }
$npmV = $null; if ($npmCmd) { $npmV = Invoke-Quiet $npmCmd @('--version') }
if ($nodeV -and $npmV) { Add-Row 'Node and npm' 'Ready' "Node $nodeV, npm $npmV" }
else { Add-Row 'Node and npm' 'Missing' 'Not found.' 'Install the Node.js LTS version from nodejs.org, then run this check again.' }

$gitExe = Get-Cmd 'git'
$gitV = $null; if ($gitExe) { $gitV = Invoke-Quiet $gitExe @('--version') }
if ($gitV) { Add-Row 'Git' 'Ready' $gitV }
else { Add-Row 'Git' 'Missing' 'Not found.' 'Install Git for Windows from git-scm.com (only needed to download or update the project).' }

# --- 5. Blender and Unity (configured paths) ----------------------------------------------------------------------------
$cfgBlenderOk = ($foundBlender -and $foundBlender.From -eq 'backend\config.yaml')
if ($foundBlender) {
    $bv = Invoke-Quiet $blenderExe @('--version')
    $m = [regex]::Match("$bv", 'Blender\s+(\d+)\.(\d+)')
    $note = if ($cfgBlenderOk) { '' } else { " (found in $($foundBlender.From); backend\config.yaml does not point to it: set blender_exe)" }
    if ($m.Success -and [int]$m.Groups[1].Value -ge 5) { Add-Row 'Blender' $(if ($cfgBlenderOk) { 'Ready' } else { 'Needs you' }) "$bv at $blenderExe$note" $(if ($cfgBlenderOk) { '' } else { 'Set blender_exe in backend\config.yaml to this path (-Install writes a proposed copy).' }) }
    else { Add-Row 'Blender' 'Needs you' "Found at $blenderExe but version reads '$bv'." 'The app needs Blender 5.0 or newer (5.2.3 is what it was built with).' }
} else {
    Add-Row 'Blender' 'Missing' 'Not on PATH, not in the configured path, not in the usual places.' 'Unzip Blender 5.2.3 (portable) from blender.org anywhere you like, then set blender_exe in backend\config.yaml.'
}
if ($foundUnity) {
    $cfgUnityOk = ($foundUnity.From -eq 'backend\config.yaml')
    Add-Row 'Unity Editor' $(if ($cfgUnityOk) { 'Ready' } else { 'Needs you' }) "Found at $unityExe (the licence cannot be checked from here)$(if (-not $cfgUnityOk) { '; backend\config.yaml does not point to it' })" $(if ($cfgUnityOk) { '' } else { 'Set unity_exe in backend\config.yaml to this path (-Install writes a proposed copy).' })
} else {
    Add-Row 'Unity Editor' 'Needs you' 'No Unity Editor found in the configured path or the Unity Hub folder.' 'Install Unity Hub, sign in, install Editor 6000.4.3f1 and activate a free Personal licence by hand. Then set unity_exe in backend\config.yaml.'
}

# --- 6. ComfyUI engines -------------------------------------------------------------------------------------------------
function Find-Engine([string]$Url, [string]$CardMatch, [int[]]$ExtraPorts) {
    # Returns @{ Url; Card } when something answers /system_stats (on the configured URL, then on the extra ports if the
    # card name matches); $null otherwise. Read-only GETs.
    $tries = @($Url)
    foreach ($p in $ExtraPorts) { $tries += "http://127.0.0.1:$p" }
    foreach ($u in ($tries | Select-Object -Unique)) {
        $s = Get-Json ($u.TrimEnd('/') + '/system_stats') 4
        if ($s -and $s.devices) {
            $name = "$($s.devices[0].name)"
            if ($u -eq $Url -or ($CardMatch -and $name -match $CardMatch)) { return @{ Url = $u.TrimEnd('/'); Card = $name } }
        }
    }
    return $null
}
$engines = @()
foreach ($u in (@($urlA, $urlB) + @(8188, 8189, 8190, 8191, 8000 | ForEach-Object { "http://127.0.0.1:$_" }) | ForEach-Object { $_.TrimEnd('/') } | Select-Object -Unique)) {
    $e = Find-Engine $u '' @()
    if ($e) { $engines += $e }
}
$engA = $engines | Where-Object { $_.Url -eq $urlA.TrimEnd('/') } | Select-Object -First 1
$engB = $engines | Where-Object { $_.Url -eq $urlB.TrimEnd('/') } | Select-Object -First 1
if ($engines.Count -eq 0) {
    Add-Row 'ComfyUI (graphics engine)' 'Needs you' "Nothing answers at $urlA, $urlB or the usual ports." 'Install ComfyUI Desktop by hand (comfy.org), start it, and run this check again. The model checks below need it running.'
} else {
    foreach ($e in $engines) {
        $role = if ($e -eq $engA) { ' (the app''s engine A, 3D models)' } elseif ($e -eq $engB) { ' (the app''s engine B, pictures and rigging)' } else { ' (not at a configured address)' }
        $fix = if ($e -eq $engA -or $e -eq $engB) { '' } else { 'Set this address under instances in backend\config.yaml, or the app will not use it.' }
        Add-Row "ComfyUI at $($e.Url)" $(if ($fix) { 'Needs you' } else { 'Ready' }) "Answers$role; card: $($e.Card)" $fix
    }
    if ($gpus.Count -eq 1 -and $engines.Count -gt 1) { Add-Row 'Two ComfyUIs on one card' 'Needs you' 'More than one ComfyUI is running but there is one graphics card.' 'Run only one at a time, or expect slow or failed steps from sharing the card.' }
}

# --- 7. Model files (exact names only) ----------------------------------------------------------------------------------
# models.yaml id -> loader node, its file input, which engine runs it (default stage plan: pictures and rigging on B,
# 3D on A), required or optional. Same table as backend\vgac\preflight.py MODEL_LOADERS.
$loaders = [ordered]@{
    zturbo              = @('CheckpointLoaderSimple', 'ckpt_name', 'B', $true)
    zbase               = @('UNETLoader', 'unet_name', 'B', $false)
    klein               = @('UNETLoader', 'unet_name', 'B', $false)
    anima               = @('UNETLoader', 'unet_name', 'B', $false)
    pixel               = @('LoraLoaderModelOnly', 'lora_name', 'B', $false)
    chroma              = @('UNETLoader', 'unet_name', 'B', $false)
    qwen_edit           = @('UNETLoader', 'unet_name', 'B', $true)
    qwen_angles_lora    = @('LoraLoaderModelOnly', 'lora_name', 'B', $true)
    qwen_lightning_lora = @('LoraLoaderModelOnly', 'lora_name', 'B', $true)
    birefnet            = @('LoadBackgroundRemovalModel', 'bg_removal_name', 'B', $true)
    trellis2            = @('UNETLoader', 'unet_name', 'A', $true)
    hy3d_mv             = @('ImageOnlyCheckpointLoader', 'ckpt_name', 'A', $false)
    skintoken           = @('SkinTokenRigTrimesh', 'ckpt_name', 'B', $true)
}
$modelsText = Read-Text (Join-Path $Root 'backend\vgac\data\models.yaml')
$modelInfo = @{}
foreach ($m in [regex]::Matches($modelsText, '(?m)^\s{2}(\w+):\s*\{name:\s*(.+?),\s*file:\s*"?([^,"}]*)"?,')) {
    $modelInfo[$m.Groups[1].Value] = @{ Name = $m.Groups[2].Value.Trim('"'); File = $m.Groups[3].Value.Trim() }
}
$infoCache = @{}
function Get-Choices($Eng, [string]$Class, [string]$InputName) {
    $key = "$($Eng.Url)|$Class"
    if (-not $infoCache.ContainsKey($key)) { $infoCache[$key] = Get-Json "$($Eng.Url)/object_info/$Class" 20 }
    $info = $infoCache[$key]
    if (-not $info) { return $null }
    $node = $info.$Class
    if (-not $node) { return $null }
    foreach ($sec in 'required', 'optional') {
        $spec = $node.input.$sec.$InputName
        if ($spec) {
            if ($spec[0] -is [System.Array]) { return @($spec[0]) }
            if ("$($spec[0])" -eq 'COMBO' -and $spec.Count -gt 1 -and $spec[1].options) { return @($spec[1].options) }
        }
    }
    return $null
}
$modelIds = @($loaders.Keys | Where-Object { $modelInfo[$_] -and $modelInfo[$_].File })
if ($engines.Count -eq 0) {
    Add-Row "Model files ($($modelIds.Count))" 'Needs you' 'Not checked: no ComfyUI is answering, so the model files cannot be looked for.' 'Start ComfyUI and run this check again. Required files are listed in docs\FIRST-TIME-SETUP.md.'
    $modelIds = @()
}
foreach ($id in $modelIds) {
    $cls, $inp, $which, $req = $loaders[$id]
    $mi = $modelInfo[$id]
    $label = "Model: $($mi.Name)"
    $eng = if ($which -eq 'A') { $engA } else { $engB }
    if (-not $eng) { $eng = $engines[0]; $which = 'the running one' }   # one-engine PC: ask the engine that is there
    $optTag = if ($req) { '' } else { ' (optional)' }
    $choices = Get-Choices $eng $cls $inp
    if ($null -eq $choices) { Add-Row "$label$optTag" 'Needs you' "Not checked: ComfyUI $which does not list files for $cls (the node pack may be missing)." 'Install the matching custom node pack in ComfyUI (ComfyUI Manager can), restart it, run this check again.'; continue }
    $want = ($mi.File -replace '\\', '/').Split('/')[-1]
    $found = $false
    foreach ($c in $choices) { if (("$c" -replace '\\', '/').Split('/')[-1] -ieq $want) { $found = $true; break } }
    $choices = $null  # only found or missing is kept
    if ($found) { Add-Row "$label$optTag" 'Ready' "$($mi.File) is installed on ComfyUI $which" }
    else { Add-Row "$label$optTag" 'Missing' "$($mi.File) is not installed on ComfyUI $which" "Download $($mi.File) from its official page (see docs\FIRST-TIME-SETUP.md) into ComfyUI's models folder for the $cls loader, restart ComfyUI." }
}

# --- 8. Add-ons (exact paths under loras_dir) ---------------------------------------------------------------------------
$addText = Read-Text (Join-Path $Root 'backend\vgac\data\addons.yaml')
if (-not $lorasDir -or -not (Test-Path -LiteralPath $lorasDir)) {
    Add-Row 'Add-ons (small extra files)' 'Needs you' 'Not checked: loras_dir in backend\config.yaml is missing or not found on this PC.' 'Set loras_dir to the "loras" folder of your ComfyUI models, then run this check again.'
} else {
    foreach ($blk in ([regex]::Split($addText, '(?m)^\s*- id:\s*') | Select-Object -Skip 1)) {
        $id = ($blk -split '\r?\n')[0].Trim()
        $f = [regex]::Match($blk, '(?m)^\s+file:\s*(\S+)').Groups[1].Value
        if (-not $f) { continue }
        $req = $blk -match '(?m)^\s+required:\s*true'
        $path = Join-Path $lorasDir ($f -replace '/', '\')
        $tag = if ($req) { '' } else { ' (optional)' }
        if (Test-Path -LiteralPath $path) { Add-Row "Add-on: $id$tag" 'Ready' $f }
        else { Add-Row "Add-on: $id$tag" 'Missing' "$f not found under loras_dir" "Download it from its official page (docs\FIRST-TIME-SETUP.md) into the loras folder at that path." }
    }
}

# --- 9. Project parts ---------------------------------------------------------------------------------------------------
$venvPy = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPy) {
    $chk = Invoke-Quiet $venvPy @('-B', '-c', "import fastapi, sqlalchemy, trimesh; print('ok')")
    if ($chk -eq 'ok') { Add-Row 'Backend venv (Python packages)' 'Ready' 'backend\.venv exists and loads its packages' }
    else { Add-Row 'Backend venv (Python packages)' 'Needs you' 'backend\.venv exists but its packages did not load.' 'Not touched by -Install. Check by hand: backend\.venv\Scripts\python -m pip install -r backend\requirements.txt' }
} else { Add-Row 'Backend venv (Python packages)' 'Missing' 'backend\.venv not found.' 'Run this script with -Install (needs Python 3.13).' }

function Check-Folder([string]$Label, [string]$Rel, [string]$Marker, [string]$FixText) {
    $p = Join-Path $Root $Rel
    if (Test-Path -LiteralPath (Join-Path $p $Marker)) { Add-Row $Label 'Ready' "$Rel is present" }
    else { Add-Row $Label 'Missing' "$Rel not found." $FixText }
}
Check-Folder 'Frontend packages' 'frontend\node_modules' '.package-lock.json' 'Run this script with -Install (needs Node and npm).'
Check-Folder 'Frontend build (the screens)' 'frontend\dist' 'index.html' 'Run this script with -Install (needs Node and npm).'
Check-Folder 'Desktop launcher packages' 'desktop\node_modules' '.package-lock.json' 'Run this script with -Install (needs Node and npm).'
Check-Folder 'Sample 3D file' 'library\samples' 'sample.glb' 'Run this script with -Install (needs the backend venv).'

$packFiles = @{ ual1 = 'AnimationLibrary_Unity_Standard.fbx'; ual2 = 'UAL2_Standard.fbx' }
foreach ($id in 'ual1', 'ual2') {
    $m = [regex]::Match($cfgText, "(?m)^\s+${id}:\s*""([^""]+)""")
    $pth = if ($m.Success) { $m.Groups[1].Value } else { '' }
    $dir = Join-Path $Root "data\animations\$id"
    $hit = $null
    if ($pth -and (Test-Path -LiteralPath $pth)) { $hit = $pth }
    elseif (Test-Path -LiteralPath $dir) { $hit = Get-ChildItem -LiteralPath $dir -Filter $packFiles[$id] -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName }
    if ($hit -and $pth -and $hit -eq $pth) { Add-Row "Animation pack $id" 'Ready' 'Found' }
    elseif ($hit) { Add-Row "Animation pack $id" 'Needs you' "Found in data\animations\$id, but clips_libraries in backend\config.yaml points elsewhere." 'Set clips_libraries to the found path (-Install writes a proposed copy).' }
    else { Add-Row "Animation pack $id" 'Missing' "$($packFiles[$id]) not found under data\animations\$id." 'Download the free CC0 pack (see docs\FIRST-TIME-SETUP.md) into data\animations, keeping its folder names.' }
}
$hair = Join-Path $Root 'backend\vgac\data\hairlib\catalogue.json'
if (Test-Path -LiteralPath $hair) { Add-Row 'Hair library' 'Ready' 'backend\vgac\data\hairlib is present' }
else { Add-Row 'Hair library' 'Missing' 'backend\vgac\data\hairlib\catalogue.json not found.' 'It ships with the project. Re-download or restore the project folder.' }

# --- 10. Report ---------------------------------------------------------------------------------------------------------
''
"Video Game Asset Creator: setup check  ($Root)"
''
($rows | Format-Table -AutoSize -Wrap | Out-String -Width 250).TrimEnd()
''
$nReady = @($rows | Where-Object Status -eq 'Ready').Count
$nMiss  = @($rows | Where-Object Status -eq 'Missing').Count
$nYou   = @($rows | Where-Object Status -eq 'Needs you').Count
"Summary: $nReady ready, $nMiss missing, $nYou need you (of $($rows.Count) checked). Nothing was changed."
if (-not $Install) {
    if ($nMiss -gt 0) { 'Run again with -Install to set up the parts that live inside the project folder (it asks before each step).' }
    return
}

# --- 11. Install (project folder only) ----------------------------------------------------------------------------------
''
'Install mode: project-folder steps only. Each step asks first; anything already there is skipped.'

function Confirm-Step([string]$Question) {
    if ($Yes) { Write-Host "$Question  yes (-Yes)"; return $true }
    try { $a = Read-Host "$Question [y/N]" } catch { return $false }
    return ($a -match '^(y|yes)$')
}
function Run-Step([string]$Label, [string]$Done, [scriptblock]$Action) {
    if ($Done) { Write-Host "[skip] $Label : $Done"; return }
    if (-not (Confirm-Step "Do this now: $Label ?")) { Write-Host "[left] $Label : not done."; return }
    try { & $Action; Write-Host "[done] $Label" } catch { Write-Host "[fail] $Label : $($_.Exception.Message)" }
}

$backend = Join-Path $Root 'backend'
$frontend = Join-Path $Root 'frontend'
$desktop = Join-Path $Root 'desktop'

# 1. venv + requirements. A venv that already exists is never touched, even if a package is missing.
$venvDone = if (Test-Path -LiteralPath $venvPy) { 'backend\.venv already exists (left alone).' } else { '' }
if (-not $venvDone -and -not $py313) { Write-Host '[blocked] backend venv : Python 3.13 is not installed. Install it by hand first.' }
else {
    Run-Step 'create backend\.venv and install backend\requirements.txt' $venvDone {
        $exe = $py313[0]; $pre = @(); if ($py313.Count -gt 1) { $pre = @($py313[1..($py313.Count - 1)]) }
        & $exe @pre -m venv (Join-Path $backend '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
        & $venvPy -m pip install -r (Join-Path $backend 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    }
}

# 2. frontend
if (-not $npmCmd) { Write-Host '[blocked] frontend and desktop : Node and npm are not installed. Install Node LTS by hand first.' }
else {
    $feMods = Test-Path -LiteralPath (Join-Path $frontend 'node_modules')
    Run-Step 'npm install in frontend\' $(if ($feMods) { 'frontend\node_modules already exists.' } else { '' }) {
        Push-Location $frontend; try { & $npmCmd install; if ($LASTEXITCODE -ne 0) { throw 'npm install failed' } } finally { Pop-Location }
    }
    $feDist = Test-Path -LiteralPath (Join-Path $frontend 'dist\index.html')
    Run-Step 'npm run build in frontend\' $(if ($feDist) { 'frontend\dist already built.' } else { '' }) {
        Push-Location $frontend; try { & $npmCmd run build; if ($LASTEXITCODE -ne 0) { throw 'npm run build failed' } } finally { Pop-Location }
    }
    # 3. desktop
    $dkMods = Test-Path -LiteralPath (Join-Path $desktop 'node_modules')
    Run-Step 'npm install in desktop\' $(if ($dkMods) { 'desktop\node_modules already exists.' } else { '' }) {
        Push-Location $desktop; try { & $npmCmd install; if ($LASTEXITCODE -ne 0) { throw 'npm install failed' } } finally { Pop-Location }
    }
}

# 4. sample glb
$glbDone = if (Test-Path -LiteralPath (Join-Path $Root 'library\samples\sample.glb')) { 'library\samples\sample.glb already exists.' } else { '' }
if (-not $glbDone -and -not (Test-Path -LiteralPath $venvPy)) { Write-Host '[blocked] sample 3D file : needs the backend venv first.' }
else {
    Run-Step 'write the sample 3D file (backend\scripts\make_sample_glb.py)' $glbDone {
        Push-Location $backend; try { & $venvPy scripts\make_sample_glb.py; if ($LASTEXITCODE -ne 0) { throw 'script failed' } } finally { Pop-Location }
    }
}

# 5. Proposed settings. Existing files are never edited. Only the project folder path and (if found in the standard
# Unity Hub folder) the Unity path are proposed; ComfyUI paths and Blender are reported, not guessed.
''
$cfgPath = Join-Path $Root 'backend\config.yaml'
$propYaml = $cfgText
$fwd = $Root -replace '\\', '/'
$propYaml = $propYaml.Replace('F:\Projects\VideoGameAssetCreator', $Root).Replace('F:/Projects/VideoGameAssetCreator', $fwd)   # same paths, this PC's folder
if ($cfgUnity -and $foundUnity -and $foundUnity.From -ne 'backend\config.yaml') { $propYaml = $propYaml.Replace($cfgUnity, ($foundUnity.Path -replace '\\', '/')) }
if ($cfgBlender -and $foundBlender -and $foundBlender.From -ne 'backend\config.yaml') { $propYaml = $propYaml.Replace($cfgBlender, ($foundBlender.Path -replace '\\', '/')) }
$cfgDiffers = ($propYaml -ne $cfgText)
$proposedYaml = Join-Path $Root 'backend\config.proposed.yaml'
if ($cfgDiffers) {
    if (Test-Path -LiteralPath $proposedYaml) { Write-Host 'backend\config.proposed.yaml already exists; not overwritten.' }
    else { [System.IO.File]::WriteAllText($proposedYaml, $propYaml); Write-Host 'Wrote backend\config.proposed.yaml (backend\config.yaml was NOT changed). Compare the two and copy over what you want.' }
} else { Write-Host 'backend\config.yaml already matches this PC: no proposed copy needed.' }

$dkCfgPath = Join-Path $Root 'desktop\config.json'
$dkText = Read-Text $dkCfgPath
$jsRoot = ($Root -replace '\\', '\\')
$dkProp = [regex]::Replace($dkText, '"projectRoot"\s*:\s*"[^"]*"', [System.Text.RegularExpressions.MatchEvaluator]{ param($m) '"projectRoot": "' + $jsRoot + '"' })
if ($dkProp -ne $dkText) {
    $proposedJson = Join-Path $Root 'desktop\config.proposed.json'
    if (Test-Path -LiteralPath $proposedJson) { Write-Host 'desktop\config.proposed.json already exists; not overwritten.' }
    else { [System.IO.File]::WriteAllText($proposedJson, $dkProp); Write-Host 'Wrote desktop\config.proposed.json (desktop\config.json was NOT changed).' }
} else { Write-Host 'desktop\config.json already matches this PC: no proposed copy needed.' }
Write-Host 'Not proposed (by hand): the ComfyUI paths inside runtime\start-comfy-a.ps1 and start-comfy-b.ps1, desktopExe in desktop\config.json, comfy_input_dir, loras_dir and the Blender path.'
''
'Install mode finished. Run the check again (without -Install) to see what is left.'
