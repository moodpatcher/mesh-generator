<#
.SYNOPSIS
  git without installing git: runs git in a small Docker image (docker/git.Dockerfile: alpine + git + openssh-client).
.DESCRIPTION
  The repository folder is mounted into the container. For commands that talk to the remote (push, pull, fetch, clone,
  ls-remote) the SSH key that ~/.ssh/config names for github.com, and known_hosts, are copied into a temporary folder
  that is mounted read-only; no other key of yours is exposed, and the folder is deleted afterwards. The container
  is removed after every command.
  Commits use the author of the last commit unless KEP3D_GIT_NAME / KEP3D_GIT_EMAIL are set.
.EXAMPLE
  .\git.ps1 status
  .\git.ps1 add -A
  .\git.ps1 commit -F .git\msg.txt
  .\git.ps1 push
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
. (Join-Path $root "scripts\lib.ps1")
$docker = Find-Docker
if (-not $docker) { throw "Docker was not found (see install.md)." }
$image = "kep3d-git:1"
& $docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) {
    & $docker build -q -t $image -f (Join-Path $root "docker\git.Dockerfile") (Join-Path $root "docker") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not build $image." }
}
if (-not $GitArgs) { $GitArgs = @("status") }

$run = @("run", "--rm", "-v", "${root}:/repo", "-w", "/repo")
$tmp = $null
$net = $GitArgs[0] -in "push", "pull", "fetch", "clone", "ls-remote"
if ($net) {
    $sshDir = Join-Path $env:USERPROFILE ".ssh"
    $keyName = $null
    $cfg = Join-Path $sshDir "config"
    if (Test-Path $cfg) {
        $inGithub = $false
        foreach ($l in Get-Content $cfg) {
            if ($l -match '^\s*Host\s+(.+)$') { $inGithub = ($Matches[1] -split '\s+') -contains "github.com" }
            elseif ($inGithub -and $l -match '^\s*IdentityFile\s+(.+)$') { $keyName = Split-Path ($Matches[1].Trim()) -Leaf; break }
        }
    }
    if (-not $keyName) { $keyName = "id_ed25519" }
    if (-not (Test-Path (Join-Path $sshDir $keyName))) { throw "No SSH key '$keyName' in $sshDir (set 'Host github.com / IdentityFile' in ~/.ssh/config)." }
    $tmp = Join-Path $env:TEMP "kep3d-git-$PID"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Copy-Item (Join-Path $sshDir $keyName) $tmp
    if (Test-Path (Join-Path $sshDir "known_hosts")) { Copy-Item (Join-Path $sshDir "known_hosts") $tmp }
    [IO.File]::WriteAllText((Join-Path $tmp "config"), "Host github.com`n    HostName github.com`n    User git`n    IdentityFile ~/.ssh/$keyName`n    IdentitiesOnly yes`n    StrictHostKeyChecking yes`n", (New-Object System.Text.UTF8Encoding($false)))
    $run += @("-v", "${tmp}:/ssh-in:ro")
}

$id = @()
if ($GitArgs[0] -in "commit", "merge", "rebase", "tag", "cherry-pick", "am", "revert") {
    $name = $env:KEP3D_GIT_NAME; $mail = $env:KEP3D_GIT_EMAIL
    if (-not $name -or -not $mail) {
        $last = & $docker run --rm -v "${root}:/repo" -w /repo $image log -1 --format="%an|%ae" 2>$null
        if ($last -match '^(.+)\|(.+)$') { $name = $Matches[1]; $mail = $Matches[2] }
    }
    if ($name -and $mail) { $id = @("-c", "user.name=$name", "-c", "user.email=$mail") }
}

try {
    & $docker @run $image @id @GitArgs
    exit $LASTEXITCODE
} finally {
    if ($tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
}
