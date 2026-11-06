<#
.SYNOPSIS
  First-time setup, part 2 for Video Game Asset Creator: models and add-ons, ComfyUI node packs, Blender, animation and
  hair packs, and proposed settings files. Looks first; downloads only when asked.

.DESCRIPTION
  Default mode CHANGES NOTHING. It prints what each part would offer and one table (Ready, Missing or Needs you).
  -Install  does the parts you choose, asking before every single item. Nothing that already exists is replaced.
  -DryRun   with -Install: prints exactly what would be downloaded or written, and does neither.
  -Yes      answers yes to the plain questions. It never answers the licence questions (see below).

  Parts (none named = all): -Models  -Nodes  -Blender  -Packs  -Settings

  -ComfyRoot     the ComfyUI folder that holds "models" and "custom_nodes" (ask your ComfyUI install). Models and
                 node packs need it. No models folder is ever listed: only exact file paths are looked at.
  -ComfyInstall  the ComfyUI install folder that holds ComfyUI\main.py (used for the proposed start scripts only).
  -Only          with -Models: only these ids (for example -Only pixel_art,birefnet); ids are in the first column of downloads.json
  -ProjectRoot   defaults to the folder above this script (for tests against a throwaway copy).

  Licence questions: Hunyuan3D-2mv, Anima and Gemma 3 each need a sentence typed by you. This script never accepts
  terms for you, and -Yes does not answer them.

  Models are never bundled. Sources are in setup\downloads.json (edit a link there, no code change). Your own
  additions go in setup\downloads.local.json (same shape).

  Windows PowerShell 5.1, ASCII only.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File setup\Setup-Extras.ps1
  powershell -ExecutionPolicy Bypass -File setup\Setup-Extras.ps1 -Models -ComfyRoot D:\ComfyUI -Install -DryRun
#>
[CmdletBinding()]
param(
    [switch]$Models, [switch]$Nodes, [switch]$Blender, [switch]$Packs, [switch]$Settings,
    [string]$ComfyRoot = '', [string]$ComfyInstall = '', [string]$ProjectRoot = '',
    [string]$ModelPathsConfig = '', [string[]]$Only = @(), [string]$Ual1Zip = '', [string]$Ual2Zip = '',
    [switch]$Install, [switch]$Yes, [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$Only = @($Only | ForEach-Object { $_ -split ',' } | Where-Object { $_ })   # -File passes a comma list as one string
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
$Root = if ($ProjectRoot) { (Resolve-Path $ProjectRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$rows = New-Object System.Collections.ArrayList
$Doing = $Install -and -not $DryRun

function Add-Row([string]$Item, [string]$Status, [string]$Found, [string]$Next = '') {
    $o = [pscustomobject]@{ Item = $Item; Status = $Status; Found = $Found; 'Next step' = $Next }
    [void]$rows.Add($o); $script:lastRow = $o
}
function Mark-Done($Row, [string]$Found) { if ($Row) { $Row.Status = 'Ready'; $Row.Found = $Found; $Row.'Next step' = '' } }
function Read-Text([string]$Path) { if (Test-Path -LiteralPath $Path) { return [System.IO.File]::ReadAllText($Path) } return '' }
function Get-Cmd([string]$Name) { $c = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { return $c.Source }; return $null }
function Fmt-Size([double]$Bytes) {
    if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N0} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -gt 0) { return ('{0:N0} KB' -f ($Bytes / 1KB)) }
    return 'size unknown'
}
function Get-Prop($Obj, [string]$Name) { if ($null -eq $Obj) { return $null }; $p = $Obj.PSObject.Properties[$Name]; if ($p) { return $p.Value }; return $null }

# --- asking -------------------------------------------------------------------------------------------------------------
function Ask-YesNo([string]$Question) {
    if ($Yes) { Write-Host "$Question  yes (-Yes)"; return $true }
    try { $a = Read-Host "$Question [y/N]" } catch { return $false }
    return ($a -match '^(y|yes)$')
}
function Ask-Phrase([string[]]$Notice, [string]$Phrase) {
    # Licence step: the person must type the sentence themselves. -Yes never answers it.
    foreach ($l in $Notice) { Write-Host "    $l" }
    if ($Yes) { Write-Host '    Left alone: -Yes does not answer licence questions. Run again without -Yes to read and answer it.'; return $false }
    Write-Host "    To go ahead, type exactly:  $Phrase"
    try { $a = Read-Host '    Your answer (anything else skips this item)' } catch { return $false }
    return ("$a".Trim() -ieq $Phrase)
}

# --- download with resume and size/checksum checks -----------------------------------------------------------------------
function Save-Download([string]$Url, [string]$Dest, [long]$Size = 0, [string]$Sha256 = '') {
    # Never replaces a finished file. Writes Dest.part, resumes it when the server allows, checks size and SHA-256 (when
    # known), then renames. Returns $true on a finished, checked file.
    if (Test-Path -LiteralPath $Dest) { Write-Host "    [skip] $Dest already exists."; return $true }
    $dir = Split-Path -Parent $Dest
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $part = "$Dest.part"
    $have = 0L
    if (Test-Path -LiteralPath $part) { $have = (Get-Item -LiteralPath $part).Length }
    if ($Size -gt 0 -and $have -gt $Size) { Remove-Item -LiteralPath $part -Force; $have = 0L }
    if (-not ($Size -gt 0 -and $have -eq $Size)) {
        $req = [Net.HttpWebRequest]::Create($Url)
        $req.UserAgent = 'VGAC-setup'; $req.AllowAutoRedirect = $true; $req.Timeout = 60000; $req.ReadWriteTimeout = 60000
        if ($have -gt 0) { $req.AddRange([long]$have) }
        $resp = $req.GetResponse()
        try {
            $append = ($have -gt 0 -and [int]$resp.StatusCode -eq 206)
            if ($have -gt 0 -and -not $append) { $have = 0L }   # the server ignored the range: start over
            if ($append) { Write-Host "    Resuming from $(Fmt-Size $have)." }
            $mode = if ($append) { [IO.FileMode]::Append } else { [IO.FileMode]::Create }
            $fs = New-Object IO.FileStream($part, $mode, [IO.FileAccess]::Write)
            try {
                $in = $resp.GetResponseStream(); $buf = New-Object byte[] (1MB)
                $total = if ($Size -gt 0) { $Size } else { $have + $resp.ContentLength }
                $done = $have; $tick = [DateTime]::UtcNow
                while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                    $fs.Write($buf, 0, $n); $done += $n
                    if (([DateTime]::UtcNow - $tick).TotalSeconds -ge 2) {
                        $tick = [DateTime]::UtcNow
                        $pct = if ($total -gt 0) { [int](100 * $done / $total) } else { 0 }
                        Write-Progress -Activity "Downloading $(Split-Path -Leaf $Dest)" -Status "$(Fmt-Size $done) of $(Fmt-Size $total)" -PercentComplete ([math]::Min($pct, 100))
                    }
                }
            } finally { $fs.Dispose(); Write-Progress -Activity 'Downloading' -Completed }
        } finally { $resp.Close() }
    }
    $len = (Get-Item -LiteralPath $part).Length
    if ($Size -gt 0 -and $len -ne $Size) { throw "Size is $len bytes, expected $Size. The partial file is kept so the next run can resume." }
    if ($Sha256) {
        $h = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash
        if ($h -ine $Sha256) { Remove-Item -LiteralPath $part -Force; throw 'The checksum does not match the published one. The bad file was removed; run again.' }
        Write-Host '    Checksum matches.'
    }
    Move-Item -LiteralPath $part -Destination $Dest
    return $true
}

function Expand-Safe([string]$Zip, [string]$Dir) {
    # Unpacks without overwriting and without letting an entry climb out of $Dir.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [IO.Compression.ZipFile]::OpenRead($Zip)
    $count = 0
    try {
        $base = [IO.Path]::GetFullPath($Dir).TrimEnd('\') + '\'
        foreach ($e in $z.Entries) {
            if (-not $e.Name) { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $Dir ($e.FullName -replace '/', '\')))
            if (-not $target.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe path in the zip: $($e.FullName)" }
            if (Test-Path -LiteralPath $target) { continue }
            $td = Split-Path -Parent $target
            if (-not (Test-Path -LiteralPath $td)) { New-Item -ItemType Directory -Path $td -Force | Out-Null }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target)
            $count++
        }
    } finally { $z.Dispose() }
    return $count
}

# --- facts about this PC (read only) -------------------------------------------------------------------------------------
function Get-Gpus {
    $out = @()
    $smi = Get-Cmd 'nvidia-smi'
    if ($smi) {
        try {
            $i = 0
            foreach ($l in @(& $smi '--query-gpu=name,memory.total' '--format=csv,noheader,nounits' 2>$null | Where-Object { $_ })) {
                $p = $l -split ',\s*'; $out += [pscustomobject]@{ Index = $i; Name = $p[0]; Gb = [math]::Round([double]$p[1] / 1024, 1) }; $i++
            }
        } catch { }
    }
    return $out
}
$cfgPath = Join-Path $Root 'backend\config.yaml'
$cfgText = Read-Text $cfgPath
function Cfg-Value([string]$Key) {
    $m = [regex]::Match($cfgText, "(?m)^$Key\s*:\s*(.+?)\s*$")
    if ($m.Success) { return $m.Groups[1].Value.Trim('"', "'") }
    return $null
}
function Find-Blender {
    $cfgB = Cfg-Value 'blender_exe'
    if ($cfgB -and (Test-Path -LiteralPath $cfgB)) { return @{ Path = $cfgB; From = 'backend\config.yaml' } }
    $onPath = Get-Cmd 'blender'
    if ($onPath) { return @{ Path = $onPath; From = 'PATH' } }
    foreach ($d in @((Join-Path $Root 'tools'), "$env:ProgramFiles\Blender Foundation", "$env:LOCALAPPDATA\Programs")) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        $hit = Get-ChildItem -LiteralPath $d -Filter 'blender.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return @{ Path = $hit.FullName; From = 'a usual place' } }
    }
    return $null
}
function Find-Unity {
    $cfgU = Cfg-Value 'unity_exe'
    if ($cfgU -and (Test-Path -LiteralPath $cfgU)) { return $cfgU }
    foreach ($h in @("$env:ProgramFiles\Unity\Hub\Editor", "$env:LOCALAPPDATA\Programs\Unity\Hub\Editor")) {
        if (-not (Test-Path -LiteralPath $h)) { continue }
        $hit = Get-ChildItem -LiteralPath $h -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'Editor\Unity.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($hit) { return $hit }
    }
    return $null
}
function Get-BlenderVersion([string]$Exe) {
    try { $o = & $Exe '--version' 2>$null | Select-Object -First 1; $m = [regex]::Match("$o", 'Blender\s+(\d+)\.(\d+)\.?(\d*)'); if ($m.Success) { return $m } } catch { }
    return $null
}

# --- data files -----------------------------------------------------------------------------------------------------------
$dlPath = Join-Path $PSScriptRoot 'downloads.json'
$dl = $null
if (Test-Path -LiteralPath $dlPath) { try { $dl = Get-Content -LiteralPath $dlPath -Raw | ConvertFrom-Json } catch { Write-Host "Could not read $dlPath : $($_.Exception.Message)" } }
$dlLocal = $null
$dlLocalPath = Join-Path $PSScriptRoot 'downloads.local.json'
if (Test-Path -LiteralPath $dlLocalPath) { try { $dlLocal = Get-Content -LiteralPath $dlLocalPath -Raw | ConvertFrom-Json } catch { Write-Host "Could not read $dlLocalPath : $($_.Exception.Message)" } }

$OptIn = @{
    hunyuan = @{ Phrase = 'I have read this and I am not in the EU, UK or South Korea'
        Notice = @('Hunyuan3D-2mv is under the Tencent Hunyuan Community Licence. Read it first: https://huggingface.co/tencent/Hunyuan3D-2mv/blob/main/LICENSE',
                   'It is NOT granted in the European Union, the United Kingdom or South Korea: people there must not use it.',
                   'A request to Tencent is needed above 1 million monthly users of your products. It is off by default.') }
    anima = @{ Phrase = 'I have read this'
        Notice = @('Anima: the model itself is non-commercial. What it makes may be used commercially. Do not sell or host the model file itself.',
                   'Page: https://huggingface.co/circlestone-labs/Anima') }
    gemma = @{ Phrase = 'I accept the Gemma terms myself'
        Notice = @("Gemma 3 is under Google's Gemma Terms of Use and Prohibited Use Policy: https://ai.google.dev/gemma/terms",
                   'This helper never accepts terms for you. Read them, and accept only if you agree.') }
}
$requiredModels = @('zturbo', 'qwen_edit', 'birefnet', 'trellis2', 'skintoken', 'trellis2_dino')
$loraAsAddon = @{ pixel = 'pixel_art'; qwen_angles_lora = 'qwen_angles'; qwen_lightning_lora = 'qwen_lightning' }
$altFolder = @{ diffusion_models = 'unet'; text_encoders = 'clip' }

function New-Item2($Kind, $Id, $Name, $RelPath, $Licence, $Required, $Dl) {
    $leaf = Split-Path -Leaf $RelPath
    return [pscustomobject]@{
        Kind = $Kind; Id = $Id; Name = $Name; File = $leaf; Rel = $RelPath; Licence = $Licence; Required = $Required
        Url = (Get-Prop $Dl 'url'); Page = (Get-Prop $Dl 'page'); Size = [long]($(if (Get-Prop $Dl 'size') { Get-Prop $Dl 'size' } else { 0 }))
        Sha = (Get-Prop $Dl 'sha256'); Publisher = (Get-Prop $Dl 'publisher'); Note = (Get-Prop $Dl 'note'); Optin = (Get-Prop $Dl 'optin')
        ByHand = [bool](Get-Prop $Dl 'by_hand'); Reason = (Get-Prop $Dl 'reason'); SaveAs = (Get-Prop $Dl 'save_as'); Status = ''
    }
}
function Build-Items {
    $list = @()
    $y = Read-Text (Join-Path $Root 'backend\vgac\data\models.yaml')
    $seen = @{}
    foreach ($m in [regex]::Matches($y, '(?m)^\s{2}(\w+):\s*\{name:\s*(.+?),\s*file:\s*"?([^,"}]*)"?,\s*licence:\s*("[^"]*"|[^,}]+)')) {
        $id = $m.Groups[1].Value; $file = $m.Groups[3].Value.Trim()
        if (-not $file -or $loraAsAddon.ContainsKey($id) -or $id -in @('ual1', 'ual2', 'hair_library', 'blender', 'unity')) { continue }
        $d = if ($dl) { Get-Prop $dl.models $id } else { $null }
        if (-not $d -and $dlLocal) { $d = Get-Prop (Get-Prop $dlLocal 'models') $id }
        $folder = if ($d -and (Get-Prop $d 'dest')) { Get-Prop $d 'dest' } else { 'checkpoints' }
        $list += New-Item2 'model' $id $m.Groups[2].Value.Trim('"') ("$folder\$file") $m.Groups[4].Value.Trim().Trim('"') ($requiredModels -contains $id) $d
        $seen[$id] = $true
    }
    foreach ($src in @($dl, $dlLocal)) {
        if (-not $src) { continue }
        $ms = Get-Prop $src 'models'
        if (-not $ms) { continue }
        foreach ($p in $ms.PSObject.Properties) {
            if ($seen.ContainsKey($p.Name) -or $loraAsAddon.ContainsKey($p.Name)) { continue }
            $d = $p.Value; $save = Get-Prop $d 'save_as'
            if (-not $save) { continue }
            $req = ($requiredModels -contains $p.Name) -or [bool](Get-Prop $d 'required')
            $list += New-Item2 'extra' $p.Name (Get-Prop $d 'name') ("$(Get-Prop $d 'dest')\$save") (Get-Prop $d 'licence') $req $d
            $seen[$p.Name] = $true
        }
    }
    $a = Read-Text (Join-Path $Root 'backend\vgac\data\addons.yaml')
    foreach ($blk in ([regex]::Split($a, '(?m)^\s*- id:\s*') | Select-Object -Skip 1)) {
        $id = ($blk -split '\r?\n')[0].Trim()
        $f = [regex]::Match($blk, '(?m)^\s+file:\s*(\S+)').Groups[1].Value
        if (-not $f) { continue }
        $label = [regex]::Match($blk, '(?m)^    label:\s*(.+?)\s*$').Groups[1].Value
        $lic = [regex]::Match($blk, '(?m)^    licence:\s*(.+?)\s*$').Groups[1].Value
        $req = $blk -match '(?m)^\s+required:\s*true'
        $d = if ($dl) { Get-Prop $dl.addons $id } else { $null }
        $list += New-Item2 'addon' $id "Add-on: $label" ('loras\' + ($f -replace '/', '\')) $lic $req $d
    }
    return $list
}
function Item-Path($Item, [string]$Alt = '') {
    $rel = $Item.Rel
    if ($Alt) { $rel = $rel -replace '^[^\\]+', $Alt }
    return (Join-Path (Join-Path $ComfyRoot 'models') $rel)
}
function Item-Present($Item) {
    if (Test-Path -LiteralPath (Item-Path $Item)) { return $true }
    $top = ($Item.Rel -split '\\')[0]
    if ($altFolder.ContainsKey($top) -and (Test-Path -LiteralPath (Item-Path $Item $altFolder[$top]))) { return $true }
    return $false
}

if ($MyInvocation.InvocationName -eq '.') { return }   # dot-sourced by a test: functions only

# --- which parts ---------------------------------------------------------------------------------------------------------
if (-not ($Models -or $Nodes -or $Blender -or $Packs -or $Settings)) { $Models = $Nodes = $Blender = $Packs = $Settings = $true }
$gpus = @(Get-Gpus)
$mode = if ($Doing) { 'INSTALL mode (asks before each item)' } elseif ($Install) { 'DRY RUN (prints what it would do, does nothing)' } else { 'LOOK ONLY (changes nothing)' }
''
"Video Game Asset Creator: setup part 2  ($Root)"
"Mode: $mode"
''

# ======================================================================================================================
# 1. Models and add-ons
# ======================================================================================================================
if ($Models) {
    '== Models and add-ons =='
    'Built and tested on 10 GB and 12 GB NVIDIA cards. Less than 10 GB is untested and may fail.'
    if ($gpus.Count -eq 0) { Write-Host 'Warning: no NVIDIA card answered (nvidia-smi). The app needs one.'; Add-Row 'Graphics memory' 'Needs you' 'nvidia-smi gave no card.' 'Install the NVIDIA driver.' }
    else {
        $maxGb = ($gpus | Measure-Object Gb -Maximum).Maximum
        $desc = ($gpus | ForEach-Object { "$($_.Name) ($($_.Gb) GB)" }) -join '; '
        if ($maxGb -lt 10) { Write-Host "[!] Warning: the biggest card here has $maxGb GB, under 10 GB. That is untested and may fail: $desc"; Add-Row 'Graphics memory' 'Needs you' "$desc. Under 10 GB." 'Untested: expect some steps to fail.' }
        else { Add-Row 'Graphics memory' 'Ready' $desc }
    }
    $items = @(Build-Items)
    $haveRoot = $ComfyRoot -and (Test-Path -LiteralPath (Join-Path $ComfyRoot 'models'))
    if (-not $ComfyRoot) { Write-Host 'No -ComfyRoot given, so nothing is checked for presence. Listing what the app can use:' }
    elseif (-not $haveRoot) { Write-Host "Warning: $ComfyRoot has no 'models' folder. Pass the ComfyUI folder that holds models and custom_nodes."; Add-Row 'ComfyUI folder' 'Needs you' "$ComfyRoot has no models folder." 'Pass -ComfyRoot with the ComfyUI folder.' }
    $disp = @()
    foreach ($it in $items) {
        $st = if ($haveRoot) { if (Item-Present $it) { 'present' } else { 'missing' } } else { '-' }
        $it.Status = $st
        $who = if ($it.ByHand) { 'by hand' } elseif ($null -eq $it.Url -or -not $it.Url) { 'no source known (by hand)' } elseif ($it.Publisher -eq $false) { 'community or repack' } else { 'author / ComfyUI' }
        $disp += [pscustomobject]@{ Item = $it.Name; File = $it.File; Size = $(if ($it.Size) { Fmt-Size $it.Size } else { '?' }); Licence = $it.Licence; Source = $who; Needed = $(if ($it.Required) { 'required' } else { 'optional' }); Status = $st }
    }
    ($disp | Format-Table -AutoSize -Wrap | Out-String -Width 250).TrimEnd()
    ''
    "Your own additions: add them to setup\downloads.local.json (same shape as downloads.json). The list stays open to them."
    $rowOf = @{}
    foreach ($it in $items) {
        if ($it.Status -eq 'present') { Add-Row $it.Name 'Ready' "$($it.File) is in place" }
        elseif ($it.Status -eq 'missing') {
            $nx = if ($it.ByHand -or -not $it.Url) { "By hand: $(if ($it.Page) { $it.Page } else { 'see its official page' })" } else { 'Run with -Models -ComfyRoot <folder> -Install to fetch it (asks first).' }
            Add-Row $it.Name 'Missing' "$($it.File) not at models\$($it.Rel)" $nx
            $rowOf[$it.Id] = $script:lastRow
        }
    }
    if ($Install -and -not $haveRoot) { Write-Host '[blocked] models: -ComfyRoot is needed to download.' }
    if ($Install -and $haveRoot) {
        ''
        'Per-item questions. The licence line and the source are shown first. Nothing already there is replaced.'
        $allRest = $false
        foreach ($it in ($items | Where-Object { $_.Status -eq 'missing' -and ($Only.Count -eq 0 -or $Only -contains $_.Id) })) {
            ''
            "- $($it.Name)  [$($it.File)]  $(if ($it.Size) { Fmt-Size $it.Size })  licence: $($it.Licence)"
            if ($it.ByHand) { Write-Host "    By hand: $($it.Reason)"; Write-Host "    Page: $($it.Page)"; continue }
            if (-not $it.Url) { Write-Host '    No download source is recorded for this one: get it from its official page.'; continue }
            Write-Host "    From: $($it.Page)   To: models\$($it.Rel)"
            if ($it.Publisher -eq $false) { Write-Host '    Warning: this file is a community upload or repack, not published by the model''s own authors. Check the page.' }
            if ($it.Note) { Write-Host "    Note: $($it.Note)" }
            if ($it.Optin) {
                if (-not (Ask-Phrase $OptIn[$it.Optin].Notice $OptIn[$it.Optin].Phrase)) { Write-Host '    [left] not confirmed, not downloaded.'; continue }
            } else {
                if (-not $allRest) {
                    if ($Yes) { $allRest = $true } else {
                        try { $a = Read-Host '    Download this? [y/N/a = yes to all the plain ones that follow]' } catch { $a = 'n' }
                        if ($a -match '^(a|all)$') { $allRest = $true } elseif ($a -notmatch '^(y|yes)$') { Write-Host '    [left] not downloaded.'; continue }
                    }
                }
            }
            $target = Item-Path $it
            if ($DryRun) { Write-Host "    [dry run] would download $($it.Url)"; Write-Host "              to $target ($(Fmt-Size $it.Size), checksum $($it.Sha))"; continue }
            try {
                $free = (New-Object IO.DriveInfo ($target.Substring(0, 1))).AvailableFreeSpace
                if ($it.Size -gt 0 -and $free -lt ($it.Size + 1GB)) { Write-Host "    [fail] only $(Fmt-Size $free) free on that drive."; continue }
                [void](Save-Download $it.Url $target $it.Size $it.Sha)
                Write-Host "    [done] $($it.File)"; Mark-Done $rowOf[$it.Id] 'downloaded and checked just now'
            } catch { Write-Host "    [fail] $($_.Exception.Message)" }
        }
        'Models done. Restart ComfyUI yourself so it sees new files; this script never restarts it.'
    }
    ''
}

# ======================================================================================================================
# 2. Node packs
# ======================================================================================================================
if ($Nodes) {
    '== ComfyUI node packs =='
    $cn = if ($ComfyRoot) { Join-Path $ComfyRoot 'custom_nodes' } else { '' }
    if (-not $cn -or -not (Test-Path -LiteralPath $cn)) {
        Write-Host 'Pass -ComfyRoot (the ComfyUI folder that holds custom_nodes) to check or install the packs.'
        Add-Row 'Node packs' 'Needs you' 'No ComfyUI custom_nodes folder to look at.' 'Pass -ComfyRoot <ComfyUI folder>.'
    } elseif ($dl) {
        foreach ($p in $dl.node_packs.PSObject.Properties) {
            $np = $p.Value; $dest = Join-Path $cn $np.folder
            if (Test-Path -LiteralPath $dest) { Add-Row "Node pack $($np.name)" 'Ready' "$($np.folder) is in custom_nodes (left alone)"; continue }
            Add-Row "Node pack $($np.name)" 'Missing' "$($np.folder) not in custom_nodes" 'Run with -Nodes -ComfyRoot <folder> -Install (asks first).'
            $myRow = $script:lastRow
            if (-not $Install) { continue }
            ''
            "- $($np.name): $($np.what)  licence: $($np.licence)"
            if (-not (Ask-YesNo "  Install it into $cn ?")) { Write-Host '  [left] not installed.'; continue }
            if ($DryRun) { Write-Host "  [dry run] would $(if (Get-Prop $np 'local') { 'copy ' + (Join-Path $Root $np.local) } else { 'download ' + $np.zip }) into $dest"; continue }
            try {
                if (Get-Prop $np 'local') {
                    Copy-Item -LiteralPath (Join-Path $Root $np.local) -Destination $dest -Recurse -Exclude '__pycache__'
                } else {
                    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('vgac-setup-' + [guid]::NewGuid().ToString('N'))
                    New-Item -ItemType Directory -Path $tmp | Out-Null
                    [void](Save-Download $np.zip (Join-Path $tmp 'pack.zip'))
                    $ex = Join-Path $tmp 'x'; [void](Expand-Safe (Join-Path $tmp 'pack.zip') $ex)
                    $inner = @(Get-ChildItem -LiteralPath $ex -Directory)
                    $src = if ($inner.Count -eq 1) { $inner[0].FullName } else { $ex }
                    Move-Item -LiteralPath $src -Destination $dest
                    Remove-Item -LiteralPath $tmp -Recurse -Force
                }
                Write-Host "  [done] $($np.name)"; Mark-Done $myRow 'installed just now (restart ComfyUI yourself)'
                if (Test-Path -LiteralPath (Join-Path $dest 'requirements.txt')) { Write-Host "  Note: this pack has a requirements.txt. Install it into ComfyUI's own Python by hand (this script never does): <ComfyUI python> -m pip install -r `"$(Join-Path $dest 'requirements.txt')`"" }
            } catch { Write-Host "  [fail] $($_.Exception.Message)" }
        }
        if ($Install) { 'Restart ComfyUI yourself so it loads new packs; this script never restarts it.' }
    }
    ''
}

# ======================================================================================================================
# 3. Blender
# ======================================================================================================================
if ($Blender -and $dl) {
    '== Blender =='
    $bl = $dl.blender
    'Only Blender 5.2.3 is tested. Any 5.x is accepted at your own risk; older than 5.0 is refused.'
    $fb = Find-Blender
    $needFetch = $true
    if ($fb) {
        $v = Get-BlenderVersion $fb.Path
        if ($v -and [int]$v.Groups[1].Value -ge 5) {
            $full = $v.Groups[0].Value -replace 'Blender\s+', ''
            if ($full -eq $bl.version) { Add-Row 'Blender' 'Ready' "$full at $($fb.Path) (found in $($fb.From))" }
            else { Add-Row 'Blender' 'Needs you' "$full at $($fb.Path). Accepted, but only $($bl.version) is tested; other 5.x versions are your own risk." 'Fine to keep, or fetch 5.2.3 with -Blender -Install.' }
            $needFetch = $false
        } else { Add-Row 'Blender' 'Needs you' "Found at $($fb.Path) but it reads below 5.0 (or not at all). The app needs 5.0 or newer." 'Fetch 5.2.3 with -Blender -Install (it goes into the project''s tools folder; the old one is not touched).' }
    } else { Add-Row 'Blender' 'Missing' 'No Blender found in config, PATH or the usual places.' "Fetch $($bl.version) with -Blender -Install (goes into the project's tools folder), or download it from $($bl.page)." }
    $blRow = $script:lastRow
    if ($Install -and $needFetch) {
        $tools = Join-Path $Root 'tools'; $final = Join-Path $tools $bl.folder
        ''
        "- Blender $($bl.version) portable, about $($bl.size_mb) MB, licence: $($bl.licence) (a tool the app runs; it does not ship it)"
        Write-Host "  From: $($bl.zip)"; Write-Host "  To:   $final"
        Write-Host "  Warning: $($bl.unconfirmed)"
        if (Test-Path -LiteralPath $final) { Write-Host '  [skip] that folder already exists.' }
        elseif (-not (Ask-YesNo '  Download and unpack it now?')) { Write-Host '  [left] not downloaded.' }
        elseif ($DryRun) { Write-Host "  [dry run] would download the zip, check it against $($bl.sha256_file), and unpack it to $final" }
        else {
            try {
                $shaText = ''
                try { $shaText = (Invoke-WebRequest -Uri $bl.sha256_file -UseBasicParsing -TimeoutSec 30).Content } catch { }
                $m = [regex]::Match("$shaText", "(?m)^([0-9a-fA-F]{64})\s+\*?$([regex]::Escape((Split-Path -Leaf $bl.zip)))")
                if (-not $m.Success) { throw "Blender's checksum file could not be read, so the download is not started. Download it by hand from $($bl.page) and unzip it into $tools." }
                $zip = Join-Path $tools ('_download\' + (Split-Path -Leaf $bl.zip))
                [void](Save-Download $bl.zip $zip 0 $m.Groups[1].Value)
                $n = Expand-Safe $zip $tools
                if (-not (Test-Path -LiteralPath (Join-Path $final 'blender.exe'))) { throw "Unpacked $n files but $final\blender.exe is not there." }
                Remove-Item -LiteralPath (Split-Path -Parent $zip) -Recurse -Force
                Mark-Done $blRow "just installed: $final"
                Write-Host "  [done] Blender is at $final\blender.exe. -Settings proposes blender_exe for it."
            } catch { Write-Host "  [fail] $($_.Exception.Message)" }
        }
    }
    ''
}

# ======================================================================================================================
# 4. Animation and hair packs
# ======================================================================================================================
if ($Packs -and $dl) {
    '== Animation packs (CC0, by Quaternius) =='
    $animRoot = Join-Path $Root 'data\animations'
    $zips = @{ ual1 = $Ual1Zip; ual2 = $Ual2Zip }
    foreach ($p in $dl.animation_packs.PSObject.Properties) {
        $id = $p.Name; $ap = $p.Value
        $dir = Join-Path $animRoot $id
        $hit = $null
        if (Test-Path -LiteralPath $dir) { $hit = Get-ChildItem -LiteralPath $dir -Filter $ap.check_file -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 }
        ''
        "- $($ap.name): $($ap.what)  (about $($ap.size_mb) MB, $($ap.licence))"
        if ($hit) { Add-Row "Animation pack $id" 'Ready' "$($ap.check_file) found under data\animations\$id"; Write-Host '  Already there.'; continue }
        Add-Row "Animation pack $id" 'Missing' "$($ap.check_file) not under data\animations\$id" "Download the zip from $($ap.page) , then run -Packs -Install and give its path."
        $apRow = $script:lastRow
        Write-Host "  Download page: $($ap.page)"
        Write-Host '  (itch.io needs a click-through, so you download the zip yourself; this script unpacks it for you.)'
        if (-not $Install) { continue }
        $z = $zips[$id]
        if (-not $z -and -not $Yes) { try { $z = (Read-Host "  Path to the downloaded zip or unpacked folder for $id (Enter to skip)").Trim('"', ' ') } catch { $z = '' } }
        if (-not $z) { Write-Host '  [left] skipped.'; continue }
        if (-not (Test-Path -LiteralPath $z)) { Write-Host "  [fail] $z not found."; continue }
        if ($DryRun) { Write-Host "  [dry run] would unpack $z into $dir (no file replaced)"; continue }
        try {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            if ((Get-Item -LiteralPath $z).PSIsContainer) {
                Get-ChildItem -LiteralPath $z -Recurse -File | ForEach-Object {
                    $rel = $_.FullName.Substring((Resolve-Path -LiteralPath $z).Path.TrimEnd('\').Length + 1); $t = Join-Path $dir $rel
                    if (-not (Test-Path -LiteralPath $t)) { New-Item -ItemType Directory -Path (Split-Path -Parent $t) -Force | Out-Null; Copy-Item -LiteralPath $_.FullName -Destination $t }
                }
            } else { [void](Expand-Safe $z $dir) }
            $hit = Get-ChildItem -LiteralPath $dir -Filter $ap.check_file -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($hit) { Mark-Done $apRow 'unpacked just now'; Write-Host "  [done] found $($hit.FullName). -Settings proposes clips_libraries for it." } else { Write-Host "  [fail] unpacked, but $($ap.check_file) is not inside. Wrong pack?" }
        } catch { Write-Host "  [fail] $($_.Exception.Message)" }
    }
    ''
    '== Hair packs (all CC0 except the app''s own rule-built styles) =='
    'The whole hair library (26 styles) ships inside the project, so every pack below is present; your pick decides which ones the app offers and credits.'
    'You can record which packs you want credited; the choice is written to library\hair-packs.json.'
    'The app and the credits page follow this file after the backend restarts: styles from packs you did not keep are hidden. Delete the file to get them all back.'
    $chosen = @()
    foreach ($hp in $dl.hair_packs) {
        ''
        "- $($hp.name): $($hp.styles) styles. $($hp.what)  ($($hp.author), $($hp.licence))"
        if ($hp.no_download) { Write-Host '  Made by the app; always on.'; $chosen += $hp; continue }
        if ($Install) { if (Ask-YesNo '  Keep this pack in your credits?') { $chosen += $hp } else { Write-Host '  [left] not kept.' } }
    }
    if ($Install) {
        $out = Join-Path $Root 'library\hair-packs.json'
        if (Test-Path -LiteralPath $out) { Write-Host "[skip] $out already exists (not replaced)." }
        elseif ($DryRun) { Write-Host "[dry run] would write $out with $($chosen.Count) packs." }
        else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $out) -Force | Out-Null
            $obj = [ordered]@{ note = 'Written by setup\Setup-Extras.ps1. Not read by the app yet.'; packs = @($chosen | ForEach-Object { [ordered]@{ id = $_.id; catalogue_pack = $_.catalogue_pack; catalogue_author = $_.catalogue_author } }) }
            [IO.File]::WriteAllText($out, ($obj | ConvertTo-Json -Depth 5))
            Write-Host "[done] wrote $out"
        }
    }
    ''
}

# ======================================================================================================================
# 5. Settings proposed from findings
# ======================================================================================================================
if ($Settings) {
    '== Proposed settings files (existing files are never edited or overwritten) =='
    $fwd = { param($p) ($p -replace '\\', '/') }
    $bExe = $null; $bNote = 'not found'; $fb = Find-Blender
    if ($fb) {
        $bv = Get-BlenderVersion $fb.Path
        if ($bv -and [int]$bv.Groups[1].Value -ge 5) { $bExe = $fb.Path } else { $bNote = "$($fb.Path) is older than 5.0 (or unreadable), so it is not proposed" }
    }
    $unity = Find-Unity
    $comfyDesk = Join-Path $env:LOCALAPPDATA 'Programs\Comfy Desktop\Comfy Desktop.exe'
    $oneCard = ($gpus.Count -eq 1)
    # Which card is engine A (3D models) and which is B (pictures, rigging): A = the card with most memory, ties to the higher index.
    $cardA = $null; $cardB = $null
    if ($gpus.Count -ge 2) { $sorted = @($gpus | Sort-Object @{ e = 'Gb'; Descending = $true }, @{ e = 'Index'; Descending = $true }); $cardA = $sorted[0]; $cardB = $sorted[1] }
    elseif ($gpus.Count -eq 1) { $cardB = $gpus[0] }
    $curInput = Cfg-Value 'comfy_input_dir'
    $inputDir = if ($ComfyRoot) { Join-Path $ComfyRoot 'input' } elseif ($curInput -and (Test-Path -LiteralPath $curInput)) { $curInput -replace '/', '\' } else { Join-Path $Root 'runtime\comfy-input' }
    $lorasDir = if ($ComfyRoot) { Join-Path $ComfyRoot 'models\loras' } else { $null }
    $findings = @()
    $findings += "Project folder: $Root"
    $findings += "Cards: $(if ($gpus.Count) { ($gpus | ForEach-Object { "[$($_.Index)] $($_.Name) $($_.Gb) GB" }) -join '; ' } else { 'none found' })$(if ($oneCard) { '  (one card: engine B only)' })"
    $findings += "ComfyUI folder (models, custom_nodes): $(if ($ComfyRoot) { $ComfyRoot } else { 'not given (-ComfyRoot)' })"
    $findings += "ComfyUI install (ComfyUI\main.py): $(if ($ComfyInstall) { $ComfyInstall } else { 'not given (-ComfyInstall)' })"
    $findings += "Blender: $(if ($bExe) { $bExe } else { $bNote })"
    $findings += "Unity: $(if ($unity) { $unity } else { 'not found' })"
    $findings += "ComfyUI Desktop app: $(if (Test-Path -LiteralPath $comfyDesk) { $comfyDesk } else { 'not found at the usual place' })"
    $findings | ForEach-Object { "  $_" }
    if ($ComfyInstall -and -not (Test-Path -LiteralPath (Join-Path $ComfyInstall 'ComfyUI\main.py'))) { Add-Row 'ComfyUI install folder' 'Needs you' "$ComfyInstall has no ComfyUI\main.py." 'Pass the install folder that holds ComfyUI\main.py.' }

    # backend\config.yaml
    $new = $cfgText
    $changes = New-Object System.Collections.ArrayList
    function Set-CfgLine([string]$Key, [string]$Value, [string]$Indent = '') {
        $m = [regex]::Match($script:new, "(?m)^$Indent$Key[ 	]*:[ 	]*(.*?)[ 	]*(?=
?$)")
        if ($m.Success -and $m.Groups[1].Value -ne $Value) { [void]$script:changes.Add("${Key}: $Value"); $script:new = $script:new.Remove($m.Index, $m.Length).Insert($m.Index, "${Indent}${Key}: $Value") }
    }
    $lib = Cfg-Value 'library_path'
    if ($lib) {
        $oldRoot = ($lib -replace '[\\/]library$', '')
        if ($oldRoot -and $oldRoot -ne $Root) { $new = $new.Replace($oldRoot, $Root).Replace(($oldRoot -replace '\\', '/'), (& $fwd $Root)); [void]$changes.Add("project folder: $oldRoot -> $Root (every path under it)") }
    }
    if ($bExe) { Set-CfgLine 'blender_exe' (& $fwd $bExe) }
    if ($unity) { Set-CfgLine 'unity_exe' (& $fwd $unity) }
    Set-CfgLine 'comfy_input_dir' (& $fwd $inputDir)
    if ($lorasDir) { Set-CfgLine 'loras_dir' (& $fwd $lorasDir) }
    Set-CfgLine 'output_dir' (& $fwd (Join-Path $Root 'runtime\comfy-b-output')) '    '
    foreach ($id in 'ual1', 'ual2') {
        $d = Join-Path $Root "data\animations\$id"
        if (Test-Path -LiteralPath $d) {
            $f = if ($id -eq 'ual1') { 'AnimationLibrary_Unity_Standard.fbx' } else { 'UAL2_Standard.fbx' }
            $hit = Get-ChildItem -LiteralPath $d -Filter $f -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($hit) { Set-CfgLine $id ('"' + (& $fwd $hit.FullName) + '"') '  ' }
        }
    }
    function Gpu-Token($c) { if ($c) { $m = [regex]::Match($c.Name, '\d{3,4}'); if ($m.Success) { return $m.Value } return $c.Name } return $null }
    # A card line is only rewritten when the config does not already name that card (so the author's own PC gets no change).
    $aFits = (-not $cardA) -or ($cfgText -match ('gpu_match:\s*"' + [regex]::Escape((Gpu-Token $cardA)) + '"'))
    $bFits = (-not $cardB) -or ($cfgText -match ('gpu_match:\s*"' + [regex]::Escape((Gpu-Token $cardB)) + '"'))
    if ($cardA -and -not $aFits) {
        $new = [regex]::Replace($new, '(?m)^(  a:[\s\S]*?\n    gpu:)\s*.+$', { param($m) $m.Groups[1].Value + ' ' + $cardA.Name })
        $new = $new.Replace('"--cuda-device 1"', "`"--cuda-device $($cardA.Index)`"")
        $new = [regex]::Replace($new, 'gpu_match: "3060"', "gpu_match: `"$(Gpu-Token $cardA)`"")
        [void]$changes.Add("engine A on card [$($cardA.Index)] $($cardA.Name)")
    }
    if ($cardB -and -not $bFits) {
        $new = [regex]::Replace($new, '(?m)^(  b:[\s\S]*?\n    gpu:)\s*.+$', { param($m) $m.Groups[1].Value + ' ' + $cardB.Name })
        $new = [regex]::Replace($new, 'gpu_match: "3080"', "gpu_match: `"$(Gpu-Token $cardB)`"")
        [void]$changes.Add("engine B on card [$($cardB.Index)] $($cardB.Name)")
    }
    if ($oneCard) {
        $new = $new.TrimEnd() + "`r`n`r`n# One graphics card (written by setup\Setup-Extras.ps1): everything that wants a card runs on b.`r`n# In the app (Health page, Graphics cards) switch card A off. One graphics step at a time. Untested below 10 GB.`r`nstage_instances: {concept: b, turnaround: b, mesh: b, prep: host, rig: b, export: host}`r`nmaterial_stage_instances: {concept: b, turnaround: b, mesh: b, prep: host, rig: b, export: host}`r`njudge_instance: b`r`n"
        [void]$changes.Add('one card: stage_instances, material_stage_instances and judge_instance all on b')
    }

    # desktop\config.json
    $dkText = Read-Text (Join-Path $Root 'desktop\config.json')
    $dkNew = $dkText
    $dkChanges = @()
    $jsRoot = $Root -replace '\\', '\\'
    $dkNew = [regex]::Replace($dkNew, '"projectRoot"\s*:\s*"[^"]*"', { param($m) '"projectRoot": "' + $jsRoot + '"' })
    $deskVal = if (Test-Path -LiteralPath $comfyDesk) { ($comfyDesk -replace '\\', '\\') } else { '' }
    $dkNew = [regex]::Replace($dkNew, '"desktopExe"\s*:\s*"[^"]*"', { param($m) '"desktopExe": "' + $deskVal + '"' })
    if ($cardA -and -not $aFits) { $dkNew = [regex]::Replace($dkNew, '"name"\s*:\s*"ComfyUI A[^"]*"', "`"name`": `"ComfyUI A (3D models, $($cardA.Name))`"") }
    if ($cardB -and -not $bFits) { $dkNew = [regex]::Replace($dkNew, '"name"\s*:\s*"ComfyUI B[^"]*"', "`"name`": `"ComfyUI B (images and rigging, $($cardB.Name))`"") }
    if ($dkNew -ne $dkText) { $dkChanges += "projectRoot -> $Root; desktopExe -> $(if ($deskVal) { $comfyDesk } else { '(empty: set it by hand)' })" }

    # start scripts
    $blenderLine = if ($bExe) { $bExe } else { 'REPLACE_WITH_BLENDER_EXE' }
    $inst = if ($ComfyInstall) { $ComfyInstall } else { 'REPLACE_WITH_COMFYUI_INSTALL_FOLDER' }
    function New-StartScript([string]$Name, [string]$Desc, [int]$Port, $Card, [string]$Tag) {
        $cuda = if ($Card) { $Card.Index } else { 0 }
        $mp = ''
        if ($ModelPathsConfig) { $mp = "    --extra-model-paths-config `"$ModelPathsConfig`" ``" }
        elseif ($ComfyRoot) { $mp = "    --base-directory `"$ComfyRoot`" ``" }
        $lines = @("# $Desc", '# Proposed by setup\Setup-Extras.ps1 from what it found on this PC. Check each path, then copy over the start script.',
            "`$env:SKINTOKEN_BLENDER_BIN = `"$blenderLine`"", "Set-Location `"$inst`"",
            '& ".\ComfyUI\.venv\Scripts\python.exe" -s ComfyUI\main.py `', "    --port $Port --listen 127.0.0.1 --cuda-device $cuda ``")
        if ($mp) { $lines += $mp }
        $lines += @("    --input-directory `"$inputDir`" ``", "    --user-directory `"$(Join-Path $Root "runtime\comfy-$Tag-user")`" ``", "    --output-directory `"$(Join-Path $Root "runtime\comfy-$Tag-output")`"")
        return ($lines -join "`r`n") + "`r`n"
    }
    $startB = New-StartScript 'b' 'Start ComfyUI instance B (images and rigging), port 8189.' 8189 $cardB 'b'
    $startA = if ($cardA) { New-StartScript 'a' 'Start ComfyUI instance A (3D models), port 8188.' 8188 $cardA 'a' } else { $null }

    $targets = @(
        @{ Rel = 'backend\config.proposed.yaml';        Text = $new;      Old = $cfgText; Why = ($changes -join '; ') },
        @{ Rel = 'desktop\config.proposed.json';        Text = $dkNew;    Old = $dkText;  Why = ($dkChanges -join '; ') },
        @{ Rel = 'runtime\start-comfy-b.proposed.ps1';  Text = $startB;   Old = '';       Why = "engine B, port 8189, card $(if ($cardB) { $cardB.Index } else { '?' })$(if ($oneCard) { ' (the only card)' })" })
    if ($startA) { $targets += @{ Rel = 'runtime\start-comfy-a.proposed.ps1'; Text = $startA; Old = ''; Why = "engine A, port 8188, card $($cardA.Index)" } }
    elseif ($oneCard) { Write-Host '  One card: no engine A start script is proposed.' }
    foreach ($t in $targets) {
        $path = Join-Path $Root $t.Rel
        ''
        "  $($t.Rel): $(if ($t.Why) { $t.Why } else { 'no change from the current file' })"
        if (-not $t.Why -and $t.Old) { Add-Row "Proposed $($t.Rel)" 'Ready' 'The current file already matches this PC.'; continue }
        if (Test-Path -LiteralPath $path) { Write-Host '  [skip] already exists, not replaced. Delete it to have a new one written.'; Add-Row "Proposed $($t.Rel)" 'Needs you' 'A proposed file already exists.' 'Compare it with the real file, or delete it and run again.'; continue }
        if (-not $Install) { Add-Row "Proposed $($t.Rel)" 'Missing' $t.Why 'Run -Settings -Install to write this proposed copy (asks first).'; continue }
        Add-Row "Proposed $($t.Rel)" 'Missing' $t.Why 'Written below if you say yes.'; $tRow = $script:lastRow
        if (-not (Ask-YesNo "  Write $($t.Rel) ?")) { Write-Host '  [left] not written.'; continue }
        if ($DryRun) { Write-Host "  [dry run] would write $path"; continue }
        [IO.File]::WriteAllText($path, $t.Text)
        Write-Host "  [done] wrote $($t.Rel). The real file was NOT changed."; Mark-Done $tRow 'proposed copy written; compare, then copy over by hand'
    }
    if (-not $ComfyInstall) { Add-Row 'ComfyUI install folder' 'Needs you' 'Not given, so the proposed start scripts hold a REPLACE_WITH_ placeholder.' 'Pass -ComfyInstall <folder that holds ComfyUI\main.py>.' }
    ''
}

# ======================================================================================================================
# Report
# ======================================================================================================================
if ($rows.Count -gt 0) {
    ($rows | Format-Table -AutoSize -Wrap | Out-String -Width 250).TrimEnd()
    ''
    $nR = @($rows | Where-Object Status -eq 'Ready').Count; $nM = @($rows | Where-Object Status -eq 'Missing').Count; $nY = @($rows | Where-Object Status -eq 'Needs you').Count
    "Summary: $nR ready, $nM missing, $nY need you (of $($rows.Count) checked)."
}
if ($Doing) { 'Install mode finished. Run again without -Install to see what is left.' }
elseif ($Install) { 'Dry run finished. Nothing was downloaded or written.' }
else { 'Nothing was changed. Add -Install to act (it asks before each item), or -Install -DryRun to preview.' }
