<#
.SYNOPSIS
  Image -> finished, textured, rigged low-poly 3D model + check pictures. Drag the image onto generate.cmd.
.DESCRIPTION
  Runs scripts\pipeline.py, then opens the check sheets and the output folder.
  Output: out\<image name>\ (model.glb, model_rigged.glb for characters, check\sheet-*.png, report.json).
  An existing output folder is never overwritten (the generation takes minutes): a new one, -2, -3, ..., is made.
.EXAMPLE
  .\generate.ps1 james.png
  .\generate.ps1 barrel.png -Kind object -Triangles 600 -Texture 256 -Height 0.9
  .\generate.ps1 harry.png -Triangles 1000 -Texture 128 -Ps1
  .\generate.ps1 james.png -Hires examples\james\hires.glb      # no generation (free, about a minute)
  .\generate.ps1 -ModelOnlyHelp                                 # the options of pipeline.py
#>
param(
    [Parameter(Position = 0)][string]$Image,
    [ValidateSet("character", "creature", "object")][string]$Kind = "character",
    [int]$Triangles = 0,
    [ValidateSet(0, 64, 128, 256, 512, 1024, 2048)][int]$Texture = 0,
    [double]$Height = 0,
    [switch]$Ps1,
    [switch]$Flat,
    [string]$Hires,
    [string]$Out,
    [switch]$NoAsk,
    [ValidateSet("", "claude", "codex")][string]$AI = "",
    [switch]$NoAI,
    [switch]$NoRig,
    [switch]$NoProject,
    [switch]$DryRun,
    [switch]$NoOpen,
    [switch]$ModelOnlyHelp,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Extra
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pipeline = Join-Path $root "scripts\pipeline.py"

. (Join-Path $root "scripts\lib.ps1")
$py = Find-Python $root

if ($ModelOnlyHelp) { & $py.Exe @($py.Pre) $pipeline --help; exit }
if (-not $Image) { throw "Drag an image onto generate.cmd, or: .\generate.ps1 image.png" }
if (-not (Test-Path -LiteralPath $Image)) { throw "No such image: $Image" }
$Image = (Resolve-Path -LiteralPath $Image).Path

if (-not $Out) {
    $base = [IO.Path]::GetFileNameWithoutExtension($Image) -replace '[^\w\-\.]+', '_'
    $Out = Join-Path $root "out\$base"
    $n = 2
    while (Test-Path -LiteralPath $Out) { $Out = Join-Path $root "out\$base-$n"; $n++ }
}

# Questions (only in an interactive window, i.e. when you drop an image on generate.cmd; -NoAsk skips them, and what
# you gave as a parameter is not asked again): the style, then the maximum number of triangles.
$interactive = Test-Interactive -NoAsk:$NoAsk
if ($interactive -and -not $PSBoundParameters.ContainsKey("Ps1") -and $Texture -le 0 -and $Triangles -le 0) {
    $a = Read-Host "Style: 1 = PS1 (few polys, 128 px texture, dithered), 2 = PS2 / modern (large texture) [Enter = 2]"
    if ($a.Trim() -eq "1") { $Ps1 = $true }
}
if ($interactive -and $Triangles -le 0) {
    $def = if ($Ps1) { if ($Kind -eq "object") { 500 } else { 1000 } } else { if ($Kind -eq "object") { 1500 } else { 4000 } }
    $a = Read-Host "Maximum number of triangles (polys)? Hint: PS1 300-1500, PS2 2000-5000 [Enter = $def]"
    if ($a.Trim() -match '^\d+$' -and [int]$a.Trim() -ge 100) { $Triangles = [int]$a.Trim() }
}

# defaults: the quality of the photo (2048 atlas, 4000 triangles); -Ps1 gives the PS1 numbers (128 px, 1000 triangles)
if ($Texture -le 0) { $Texture = $(if ($Ps1) { 128 } else { 2048 }) }
$argv = @($pipeline, $Image, $Out, "--kind", $Kind, "--texture", "$Texture")
if ($Triangles -le 0) { $Triangles = $(if ($Ps1) { $(if ($Kind -eq "object") { 500 } else { 1000 }) } else { $(if ($Kind -eq "object") { 1500 } else { 4000 }) }) }
$argv += @("--triangles", "$Triangles")
if ($Flat) { $argv += "--flat" }
if ($Height -gt 0) { $argv += @("--height", "$Height") }
if ($Ps1) { $argv += "--ps1" }
if ($Hires) { $argv += @("--hires", (Resolve-Path -LiteralPath $Hires).Path) }
if ($NoRig) { $argv += "--no-rig" }
if ($NoProject) { $argv += "--no-project" }
if ($Extra) { $argv += $Extra }

if ($py.Note) { Write-Host $py.Note -ForegroundColor Yellow }
Write-Host ("python " + (($argv | ForEach-Object { if ($_ -match " ") { "`"$_`"" } else { $_ } }) -join " ")) -ForegroundColor Cyan
if ($DryRun) { exit }

$ok = $false
try {
    & $py.Exe @($py.Pre) @argv
    $ok = ($LASTEXITCODE -eq 0)
} catch {
    Write-Host $_ -ForegroundColor Red
}

$report = Join-Path $Out "report.json"
if ($ok -and (Test-Path $report)) {
    $r = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host ""
    Write-Host "Verdicts:" -ForegroundColor Green
    foreach ($p in $r.verdicts.PSObject.Properties) {
        $color = switch ($p.Value) { "ok" { "Green" } "warn" { "Yellow" } default { "Red" } }
        Write-Host ("  {0,-12} {1}" -f $p.Name, $p.Value) -ForegroundColor $color
    }
    if ($r.post.texture) {
        $t = $r.post.texture
        Write-Host ("  texture: {0} px/m body, {1} px/m head, reference projected: {2}" -f $t.px_per_m, $t.head_px_per_m, $t.projected)
    }
    Write-Host ""
    Write-Host "Static model : $(Join-Path $Out 'model.glb')"
    if ($r.rig) { Write-Host "Rigged model : $(Join-Path $Out 'model_rigged.glb')  ($($r.rig.bones) bones, clips: $(($r.rig.clips | ForEach-Object { $_.name }) -join ', '))" }
    Write-Host "Pictures     : $(Join-Path $Out 'check')"
    Write-Host "Look at the sheets before you call it done (faces, hands, seams, the back is the generator's guess)." -ForegroundColor Yellow
    if (-not $NoOpen) {
        foreach ($f in @("walk.gif", "sheet-full.png", "sheet-closeup-90.png")) {
            $p = Join-Path $Out "check\$f"
            if (Test-Path $p) { Start-Process $p }
        }
        Start-Process explorer.exe $Out
    }
} elseif (-not $ok) {
    Write-Host ""
    Write-Host "The run failed - see the message above$(if (Test-Path $report) { " and $report" })." -ForegroundColor Red
}

# AI edits through Blender MCP (after the generation; see ai.ps1 for what runs where and what leaves this PC)
if ($ok -and -not $NoAI -and -not $DryRun -and ($interactive -or $AI)) {
    $go = [bool]$AI
    if (-not $go) {
        Write-Host ""
        Write-Host "Do you want to make AI edits to the model through Blender MCP?" -ForegroundColor Cyan
        Write-Host "  (an AI agent works in a Blender window; your prompts and pictures go to its provider - the generation itself stayed local)" -ForegroundColor DarkGray
        $go = (Read-Host "[y/N]").Trim().ToLower() -in "y", "yes"
    }
    if ($go) {
        try { & (Join-Path $root "ai.ps1") -Model $Out -Agent $AI -Yes } catch { Write-Host $_ -ForegroundColor Red }
    }
}

if ($Host.Name -eq "ConsoleHost" -and -not [Environment]::GetCommandLineArgs().Contains("-NonInteractive")) {
    Read-Host "`nPress Enter to close"
}
