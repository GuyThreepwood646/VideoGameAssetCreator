param([string]$Script, [string]$Root)
$src = Get-Content -Raw $Script
$a = $src.IndexOf('$minFile = '); $b = $src.IndexOf('if (-not $minimums) { Add-Row')
. ([scriptblock]::Create($src.Substring($a, $b - $a).Replace('$Root', "'$Root'")))
$cases = @(
 @('python','Python 3.13.0'),@('python','Python 3.12.9'),@('python','Python 3.19.2'),@('python','Python'),
 @('node','v22.12.0'),@('node','v22.11.9'),@('node','v40.0.0'),@('node','x'),
 @('blender','Blender 5.0.0'),@('blender','Blender 4.5.9'),@('blender','Blender 9.1.0'),@('blender',''),
 @('unity','6000.0.0f1'),@('unity','2022.3.60f1'),@('unity','7000.2.1f1'),@('unity',''),
 @('comfyui','0.34.0'),@('comfyui','0.33.9'),@('comfyui','3.0.0'),@('comfyui',''))
foreach ($c in $cases) { '{0,-8} {1,-16} {2}' -f $c[0], $c[1], (Test-Floor $c[0] $c[1]) }
