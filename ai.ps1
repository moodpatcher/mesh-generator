<#
.SYNOPSIS
  AI edits through Blender MCP: opens the finished model in a Blender window and starts an AI agent (Claude Code or
  Codex) that works on it directly. You type prompts and paste image paths ("put THIS on her face").
.DESCRIPTION
  What runs where:
    - a Blender window on this PC (your Blender install, started with --factory-startup; nothing is installed into it)
      with the model and the add-on of mcp-for-blender (MIT, loaded from a file) listening on localhost only;
    - the MCP server (mcp-for-blender, version pinned) in a Docker container, telemetry off;
    - the agent CLI (claude / codex) in this window, with only that MCP server.
  Privacy: your prompts, the pictures you give it and what it looks at in Blender go to the agent's provider
  (Anthropic for Claude Code, OpenAI for Codex). The model generation itself stays local; this step does not.
  Online services: only Poly Haven (free, CC0). Generators, Sketchfab, Poly Pizza, telemetry: off.
  The agent saves to <model folder>\model_ai.glb and model_ai.blend; model.glb / model_rigged.glb are never touched.
.EXAMPLE
  .\ai.ps1 out\james                      # the folder of a finished run (model_rigged.glb, else model.glb)
  .\ai.ps1 out\james\model.glb -Agent claude
#>
param(
    [Parameter(Position = 0)][string]$Model,
    [ValidateSet("", "claude", "codex")][string]$Agent = "",
    [string]$Out,
    [switch]$Yes,
    [switch]$DryRun,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
. (Join-Path $root "scripts\lib.ps1")

$mcpImage = "kep3d-blender-mcp:2.2.1"
$addon = Join-Path $root ".tools\mcp-for-blender\addon.py"

# --- the model ----------------------------------------------------------------------------------------------------------
if (-not $Model) { throw "Give a finished run: .\ai.ps1 out\<name>   (or drop a .glb on ai.cmd)" }
if (-not (Test-Path -LiteralPath $Model)) { throw "No such file or folder: $Model" }
$Model = (Resolve-Path -LiteralPath $Model).Path
if ((Get-Item -LiteralPath $Model).PSIsContainer) {
    $dir = $Model
    $Model = @("model_rigged.glb", "model.glb") | ForEach-Object { Join-Path $dir $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Model) { throw "No model_rigged.glb / model.glb in $dir" }
}
if (-not $Out) { $Out = Split-Path $Model -Parent }
New-Item -ItemType Directory -Force $Out | Out-Null

# --- what the user has to know, and the choice ---------------------------------------------------------------------------
$interactive = Test-Interactive
Write-Host ""
Write-Host "AI edits through Blender MCP" -ForegroundColor Cyan
Write-Host "  model : $Model"
Write-Host "  output: $Out\model_ai.glb  (+ model_ai.blend)"
Write-Host "  PRIVACY: your prompts, the pictures you give the agent and what it sees in Blender go to its provider" -ForegroundColor Yellow
Write-Host "           (Anthropic for Claude Code, OpenAI for Codex). The 3D generation itself stayed on this PC." -ForegroundColor Yellow
Write-Host "  Online services the agent may use: Poly Haven only (free, CC0). No generators, no telemetry."
if (-not $Yes -and $interactive) {
    if ((Read-Host "Continue? [y/N]").Trim().ToLower() -notin "y", "yes") { Write-Host "Cancelled."; exit }
}
if (-not $Agent) {
    if ($interactive) {
        $a = Read-Host "Agent: 1 = Claude Code, 2 = Codex [Enter = 1]"
        $Agent = if ($a.Trim() -eq "2") { "codex" } else { "claude" }
    } else { $Agent = "claude" }
}

# --- checks ------------------------------------------------------------------------------------------------------------
$agentExe = (Get-Command $Agent -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $agentExe) {
    $hint = if ($Agent -eq "claude") { "winget install Anthropic.ClaudeCode" } else { "npm i -g @openai/codex  (or see the Codex docs)" }
    throw "'$Agent' was not found on the PATH. Install it first: $hint"
}
$blender = Find-BlenderGui
if (-not $blender) { throw "Blender was not found (the AI step needs a Blender window). Install it (winget install BlenderFoundation.Blender) or set the BLENDER environment variable." }
$docker = Find-Docker
if (-not $docker) { throw "Docker was not found. The MCP server runs in Docker (see install.md)." }
& $docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker is not running. Start Docker Desktop." }

& $docker image inspect $mcpImage *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Building the MCP server image $mcpImage (once; it installs the pinned mcp-for-blender from PyPI inside Docker)..." -ForegroundColor Cyan
    $py = Find-Python $root
    & $py.Exe @($py.Pre) (Join-Path $root "scripts\pipeline.py") --setup blender-mcp
    if ($LASTEXITCODE -ne 0) { throw "The image build failed (a network error? run it again: the finished layers are cached)." }
}
if (-not (Test-Path $addon)) {
    Write-Host "Taking the Blender add-on out of the MCP server image -> $addon" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force (Split-Path $addon -Parent) | Out-Null
    $cid = & $docker create $mcpImage
    & $docker cp "${cid}:/usr/local/lib/python3.12/site-packages/blender_mcp/bundled/addon.py" $addon
    & $docker rm $cid *> $null
    if (-not (Test-Path $addon)) { throw "Could not extract the add-on from the image." }
}

# --- session files ------------------------------------------------------------------------------------------------------
$session = Join-Path $Out "ai-session"
New-Item -ItemType Directory -Force $session | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$systemPrompt = (Get-Content (Join-Path $root "scripts\ai-session-prompt.txt") -Raw -Encoding UTF8).Replace("<OUT>", $Out)
$promptFile = Join-Path $session "system-prompt.txt"
[IO.File]::WriteAllText($promptFile, $systemPrompt, $utf8)

# Blender gets ASCII paths (non-ASCII folder names have given trouble); the agent saves to $Out itself
$tmp = Join-Path $env:TEMP "kep3d-ai-$PID"
New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item -LiteralPath $Model (Join-Path $tmp "model.glb") -Force
$port = Get-FreePort
$mcpName = "kep3d-mcp-$PID"
$dockerArgs = @("run", "-i", "--rm", "--name", $mcpName, "-e", "DISABLE_TELEMETRY=true",
                "-e", "BLENDER_HOST=host.docker.internal", "-e", "BLENDER_PORT=$port", $mcpImage)
$mcpJson = Join-Path $session "mcp.json"
[IO.File]::WriteAllText($mcpJson, (@{ mcpServers = @{ blender = @{ command = $docker; args = $dockerArgs } } } | ConvertTo-Json -Depth 6), $utf8)

$startPrompt = "Blender is open with the model ($(Split-Path $Model -Leaf) of $(Split-Path $Out -Leaf)). Look at it first (get_scene_info, look) and tell me what you see; then wait for my requests."
$blenderArgs = @("--factory-startup", "--python", (Join-Path $root "scripts\ai_blender.py"), "--", (Join-Path $tmp "model.glb"), $addon, "$port")

if ($DryRun) {
    Write-Host "blender: $blender $($blenderArgs -join ' ')"
    Write-Host "mcp    : $docker $($dockerArgs -join ' ')"
    Write-Host "agent  : $agentExe ($Agent)"
    exit
}

# --- Blender window + wait for the add-on's socket ------------------------------------------------------------------------
Write-Host "Starting Blender (port $port)..." -ForegroundColor Cyan
$bl = Start-Process -FilePath $blender -ArgumentList ($blenderArgs | ForEach-Object { if ($_ -match " ") { "`"$_`"" } else { $_ } }) -PassThru
$ready = $false
for ($i = 0; $i -lt 120 -and -not $ready; $i++) {
    if ($bl.HasExited) { throw "Blender closed before the MCP add-on started." }
    try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $port); $c.Close(); $ready = $true } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw "The add-on did not start listening on localhost:$port in 60 s (look at the Blender window / its console)." }
if ($SelfTest) {
    # no agent, nothing leaves this PC: the real MCP server in Docker talks to the add-on in the Blender window
    Write-Host "Self test: MCP handshake + tools/list + get_scene_info ..." -ForegroundColor Cyan
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $docker
    $psi.Arguments = ($dockerArgs | ForEach-Object { if ($_ -match " ") { "`"$_`"" } else { $_ } }) -join " "
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $mp = $null
    $pending = $null
    # Windows PowerShell 5.1 builds the stdin writer of a child process from Console.InputEncoding, whose UTF-8 has a
    # BOM: it would sit in front of the first JSON line and the MCP server would refuse it
    try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
    $send = {  # raw bytes: the StreamWriter of Windows PowerShell 5.1 would put a BOM in front of the first JSON line
        param($o)
        $b = [System.Text.Encoding]::UTF8.GetBytes((($o | ConvertTo-Json -Depth 8 -Compress) + "`n"))
        $mp.StandardInput.BaseStream.Write($b, 0, $b.Length)
        $mp.StandardInput.BaseStream.Flush()
    }
    $read = {
        param($id)
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            if ($null -eq $script:pending) { $script:pending = $mp.StandardOutput.ReadLineAsync() }
            if (-not $script:pending.Wait(5000)) { continue }   # the same read stays pending until a line arrives
            $line = $script:pending.Result
            $script:pending = $null
            if ($null -eq $line) { return $null }
            try { $m = $line | ConvertFrom-Json } catch { continue }
            if ($m.id -eq $id) { return $m }
        }
    }
    $fail = $false
    try {
        $mp = [System.Diagnostics.Process]::Start($psi)
        & $send @{ jsonrpc = "2.0"; id = 1; method = "initialize"; params = @{ protocolVersion = "2025-03-26"; capabilities = @{}; clientInfo = @{ name = "kep3d-selftest"; version = "1" } } }
        $r = & $read 1
        Write-Host ("  initialize     : " + $(if ($r -and $r.result) { "ok ($($r.result.serverInfo.name) $($r.result.serverInfo.version))" } else { "FAILED"; $fail = $true }))
        & $send @{ jsonrpc = "2.0"; method = "notifications/initialized" }
        & $send @{ jsonrpc = "2.0"; id = 2; method = "tools/list" }
        $r = & $read 2
        $tools = if ($r -and $r.result) { @($r.result.tools | ForEach-Object { $_.name }) } else { @() }
        Write-Host ("  tools          : " + $(if ($tools) { $tools -join ", " } else { "FAILED"; $fail = $true }))
        & $send @{ jsonrpc = "2.0"; id = 3; method = "tools/call"; params = @{ name = "get_scene_info"; arguments = @{} } }
        $r = & $read 3
        $txt = if ($r -and $r.result) { ($r.result.content | Where-Object { $_.type -eq "text" } | ForEach-Object { $_.text }) -join "`n" } else { "" }
        Write-Host ("  get_scene_info : " + $(if ($txt) { "ok" } else { "FAILED"; $fail = $true }))
        if ($txt) { ($txt -split "`n" | Select-Object -First 12) | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray } }
    } finally {
        try { $mp.StandardInput.Close() } catch { }
        & $docker kill $mcpName *> $null 2>&1
        if (-not $bl.HasExited) { $bl.Kill() }
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    if ($fail) { Write-Host "SELF TEST FAILED" -ForegroundColor Red; exit 1 } else { Write-Host "SELF TEST OK" -ForegroundColor Green; exit 0 }
}
Write-Host "Blender is ready. Starting $Agent. Type what you want changed; give pictures as file paths." -ForegroundColor Green
Write-Host "(Claude Code asks before it runs code in Blender; pick 'always allow' only if you trust the session.)" -ForegroundColor DarkGray

# --- the agent -----------------------------------------------------------------------------------------------------------
Push-Location $Out
try {
    if ($Agent -eq "claude") {
        & $agentExe --mcp-config $mcpJson --strict-mcp-config --append-system-prompt-file $promptFile `
            --allowedTools mcp__blender__get_scene_info mcp__blender__look mcp__blender__get_addon_status `
            --disallowedTools mcp__blender__generate_3d mcp__blender__record_trajectory_feedback mcp__blender__disable_telemetry `
            $startPrompt
    } else {
        # Codex (not tested here): the MCP server given with -c overrides, so ~/.codex/config.toml stays untouched. Codex has
        # no per-tool deny list that we know of; the add-on has every generator switched off and no key, and the prompt
        # forbids them.
        $q = { param($s) '\"' + ($s -replace '\\', '\\\\' -replace '"', '\\\"') + '\"' }
        $argsToml = "[" + (($dockerArgs | ForEach-Object { & $q $_ }) -join ",") + "]"
        & $agentExe -C $Out -c ("mcp_servers.blender.command=" + (& $q $docker)) -c ("mcp_servers.blender.args=" + $argsToml) `
            ($systemPrompt + "`n`n" + $startPrompt)
    }
} finally {
    Pop-Location
    & $docker kill $mcpName *> $null
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "The agent has ended. The Blender window is still open: close it when you are done (unsaved edits are lost)." -ForegroundColor Cyan
foreach ($f in "model_ai.glb", "model_ai.blend") {
    $p = Join-Path $Out $f
    Write-Host ("  {0,-16} {1}" -f $f, $(if (Test-Path $p) { "saved ($((Get-Item $p).LastWriteTime))" } else { "not saved" }))
}
