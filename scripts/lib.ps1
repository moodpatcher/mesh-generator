# Shared helpers of generate.ps1 and ai.ps1 (dot-sourced).

function Find-Python([string]$Root) {
    # the portable one from setup.ps1 -Python first, then a real Python (the Store alias in WindowsApps is not one)
    $local = Join-Path $Root ".tools\python\python.exe"
    if (Test-Path $local) { return @{ Exe = $local; Pre = @(); Note = "" } }
    foreach ($cmd in @("py", "python", "python3")) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" } | Select-Object -First 1
        if ($c) { return @{ Exe = $c.Source; Pre = $(if ($cmd -eq "py") { @("-3") } else { @() }); Note = "" } }
    }
    $p = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($p) { return @{ Exe = $p.FullName; Pre = @(); Note = "" } }
    # pipeline.py uses only the standard library (the image keying runs in Docker): Blender's own Python is enough
    $b = Get-ChildItem "C:\Program Files\Blender Foundation\Blender *\*\python\bin\python.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($b) { return @{ Exe = $b.FullName; Pre = @(); Note = "no Python installed: using Blender's (works with Docker)" } }
    throw "Python 3.10+ was not found. Install it from python.org (tick 'Add to PATH') - pipeline.py needs no packages."
}

function Find-Docker {
    $c = Get-Command docker -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    $p = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $p) { return $p }
}

function Find-BlenderGui {
    # the Blender window for the AI step (a GUI is needed; the pipeline itself uses the Docker one)
    if ($env:BLENDER -and (Test-Path $env:BLENDER)) { return $env:BLENDER }
    $c = Get-Command blender -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    foreach ($base in @("$env:ProgramFiles\Blender Foundation", "$env:LOCALAPPDATA\Programs\Blender Foundation")) {
        if (Test-Path $base) {
            $e = Get-ChildItem $base -Recurse -Filter blender.exe -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
            if ($e) { return $e.FullName }
        }
    }
}

function Test-Interactive([switch]$NoAsk) {
    return ([Environment]::UserInteractive -and $Host.Name -eq "ConsoleHost" -and -not $NoAsk -and `
        -not [Environment]::GetCommandLineArgs().Contains("-NonInteractive"))
}

function Get-FreePort {
    for ($i = 0; $i -lt 50; $i++) {
        $port = Get-Random -Minimum 40000 -Maximum 49999
        try {
            $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
            $l.Start(); $l.Stop()
            return $port
        } catch { }
    }
    throw "No free TCP port found."
}
