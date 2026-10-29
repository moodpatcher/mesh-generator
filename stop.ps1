<#
.SYNOPSIS
  Stops everything this kit runs: the pipeline processes and every kep3d-* Docker container (a generation that lost
  its window, for example). Nothing else is touched.
#>
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) { $docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }

Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "scripts[\\/]pipeline\.py|generate\.ps1" -and $_.Name -match "python|powershell" -and $_.ProcessId -ne $PID } |
    ForEach-Object { Write-Host ("stopping process {0} ({1})" -f $_.ProcessId, $_.Name); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$ids = @()
foreach ($img in "kep3d-trellis2:1", "kep3d-triposr:1", "kep3d-blender:4.5.15", "kep3d-tools:1") { $ids += & $docker ps -q --filter "ancestor=$img" }
if ($ids) { Write-Host "killing $($ids.Count) container(s)"; & $docker kill $ids | Out-Null } else { Write-Host "no kep3d container is running" }
