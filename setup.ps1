<#
.SYNOPSIS
  Checks the environment, and (only when asked) sets up what is missing. Everything heavy lives in Docker; the host
  only needs Docker Desktop + the NVIDIA driver, and a Python for pipeline.py (a portable one, inside this folder).
.DESCRIPTION
  .\setup.ps1                      check only: changes nothing, says what is missing and what each fix would touch
  .\setup.ps1 -Python              download a portable Python (about 10 MB) into .tools\python  (nothing installed)
  .\setup.ps1 -Images tools,blender   build the small Docker images (blender 4.5.15 ~1 GB; default of -Images)
  .\setup.ps1 -Images trellis2     build the TRELLIS.2 image (~20 GB, can take hours; the weights come on first use)
  .\setup.ps1 -WslMemory 24        write memory=24GB into %USERPROFILE%\.wslconfig (a backup is made) - this does NOT
                                   restart WSL; run `wsl --shutdown` and restart Docker Desktop yourself
  Every action is opt-in: nothing outside this folder is touched without its switch.
#>
param(
    [switch]$Python,
    [string[]]$Images,
    [int]$WslMemory = 0
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pyVersion = "3.12.8"
$pyHome = Join-Path $root ".tools\python"

function Row($name, $state, $detail) {
    $color = switch ($state) { "ok" { "Green" } "warn" { "Yellow" } "info" { "Gray" } default { "Red" } }
    Write-Host ("  {0,-14} {1,-5} {2}" -f $name, $state, $detail) -ForegroundColor $color
}

function Find-Docker {
    $c = Get-Command docker -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $p) { return $p }
}

function Find-RealPython {
    $local = Join-Path $pyHome "python.exe"
    if (Test-Path $local) { return $local }
    foreach ($cmd in @("py", "python", "python3")) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" } | Select-Object -First 1
        if ($c) { return $c.Source }
    }
}

# --- actions (only when asked) ---------------------------------------------------------------------------------------

if ($Python) {
    if (Test-Path (Join-Path $pyHome "python.exe")) {
        Write-Host "Portable Python is already in $pyHome" -ForegroundColor Green
    } else {
        $url = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip"
        $zip = Join-Path $env:TEMP "python-$pyVersion-embed-amd64.zip"
        Write-Host "Downloading $url" -ForegroundColor Cyan
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        New-Item -ItemType Directory -Force $pyHome | Out-Null
        Expand-Archive -Path $zip -DestinationPath $pyHome -Force
        Remove-Item $zip
        & (Join-Path $pyHome "python.exe") -c "import json, subprocess, argparse; print('portable Python', __import__('sys').version.split()[0], 'works')"
    }
}

if ($PSBoundParameters.ContainsKey("Images")) {
    $py = Find-RealPython
    if (-not $py) { throw "No Python yet - run: .\setup.ps1 -Python" }
    $names = if ($Images) { $Images } else { @("tools", "blender") }
    & $py (Join-Path $root "scripts\pipeline.py") --setup @names
    if ($LASTEXITCODE -ne 0) { throw "Image build failed - a network error: run it again, the finished layers are cached." }
}

if ($WslMemory -gt 0) {
    $cfg = Join-Path $env:USERPROFILE ".wslconfig"
    if (Test-Path $cfg) { Copy-Item $cfg "$cfg.bak-$(Get-Date -Format yyyyMMdd-HHmmss)" }
    $lines = New-Object System.Collections.ArrayList
    if (Test-Path $cfg) { Get-Content $cfg | ForEach-Object { [void]$lines.Add($_) } }
    if (-not $lines.Contains("[wsl2]")) { [void]$lines.Add("[wsl2]") }
    $idx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*memory\s*=") { $idx = $i } }
    if ($idx -ge 0) { $lines[$idx] = "memory=${WslMemory}GB" }
    else { $lines.Insert($lines.IndexOf("[wsl2]") + 1, "memory=${WslMemory}GB") }
    Set-Content -Path $cfg -Value $lines -Encoding ASCII
    Write-Host "memory=${WslMemory}GB written to $cfg (backup next to it). Now run: wsl --shutdown   and restart Docker Desktop." -ForegroundColor Yellow
}

# --- the check (always) -------------------------------------------------------------------------------------------------

Write-Host ""
Write-Host "Environment:" -ForegroundColor Cyan

$docker = Find-Docker
$running = $false
if ($docker) {
    & $docker info --format "{{.ServerVersion}}" *> $null
    $running = ($LASTEXITCODE -eq 0)
    if ($running) { Row "docker" "ok" "Docker Desktop is running" } else { Row "docker" "fail" "installed, but not running - start Docker Desktop" }
} else {
    Row "docker" "fail" "not installed - Docker Desktop (WSL2 backend): winget install Docker.DockerDesktop  (needs admin + restart)"
}

$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $g = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) -join "; "
    Row "nvidia gpu" "ok" $g
    if ($running) {
        & $docker run --rm --gpus all --entrypoint nvidia-smi kep3d-tools:1 -L *> $null
        if ($LASTEXITCODE -eq 0) { Row "gpu in docker" "ok" "containers see the GPU" } else { Row "gpu in docker" "warn" "could not test (tools image missing?) - needed for TRELLIS.2 / TripoSR" }
    }
} else {
    Row "nvidia gpu" "warn" "nvidia-smi not found - no generation (TRELLIS.2 needs it); the post-processing and checks still work"
}

$cfg = Join-Path $env:USERPROFILE ".wslconfig"
$mem = $null
if (Test-Path $cfg) { $m = Select-String -Path $cfg -Pattern "^\s*memory\s*=\s*(\d+)\s*GB" | Select-Object -First 1; if ($m) { $mem = [int]$m.Matches[0].Groups[1].Value } }
if ($mem -ge 24) { Row "wsl memory" "ok" "${mem}GB" }
elseif ($mem) { Row "wsl memory" "warn" "${mem}GB - TRELLIS.2 needs about 16 GB free; suggest: .\setup.ps1 -WslMemory 24" }
else { Row "wsl memory" "warn" "not set (WSL2 default: half of the RAM) - for TRELLIS.2 suggest: .\setup.ps1 -WslMemory 24" }

$py = Find-RealPython
if ($py) { Row "python" "ok" $py } else { Row "python" "warn" "none (the Store alias does not count) - .\setup.ps1 -Python  (portable, in .tools\python)" }

if ($running) {
    foreach ($img in @(@("tools", "kep3d-tools:1", "small"), @("blender", "kep3d-blender:4.5.15", "~1 GB"), @("trellis2", "kep3d-trellis2:1", "~20 GB, hours"), @("triposr", "kep3d-triposr:1", "smaller"), @("blender-mcp", "kep3d-blender-mcp:2.2.1", "small, only for the AI step (ai.ps1)"))) {
        & $docker image inspect $img[1] *> $null
        if ($LASTEXITCODE -eq 0) { Row "image $($img[0])" "ok" $img[1] }
        else { Row "image $($img[0])" $(if ($img[0] -in "tools", "blender") { "warn" } else { "info" }) "not built ($($img[2])) - builds itself on first use, or: .\setup.ps1 -Images $($img[0])" }
    }
}

# the optional AI step (ai.ps1): a Blender window + an agent CLI on the host
. (Join-Path $root "scripts\lib.ps1")
$bg = Find-BlenderGui
if ($bg) { Row "blender window" "ok" "$bg (for the AI step)" } else { Row "blender window" "info" "not found - only needed for the AI step (ai.ps1): winget install BlenderFoundation.Blender" }
foreach ($ag in "claude", "codex") {
    $c = Get-Command $ag -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { Row "agent $ag" "ok" $c.Source } else { Row "agent $ag" "info" "not found - only needed for the AI step (ai.ps1)" }
}

$cache = Join-Path $root "cache"
if (Test-Path $cache) { Row "weights" "ok" ("cache\ {0:N1} GB" -f ((Get-ChildItem $cache -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB)) }
else { Row "weights" "info" "cache\ is created on the first generation (a few GB, once)" }
Write-Host ""
