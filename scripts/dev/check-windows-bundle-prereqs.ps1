[CmdletBinding()]
param(
    [string]$Msys2Root = "C:\msys64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$resolvedRoot = [IO.Path]::GetFullPath($Msys2Root).TrimEnd('\', '/')
$bashPath = Join-Path $resolvedRoot "usr\bin\bash.exe"
$rsyncPath = Join-Path $resolvedRoot "usr\bin\rsync.exe"
$cygpathPath = Join-Path $resolvedRoot "usr\bin\cygpath.exe"

foreach ($required in @(
    @{ Label = "MSYS2 Bash"; Path = $bashPath },
    @{ Label = "MSYS2 rsync"; Path = $rsyncPath },
    @{ Label = "MSYS2 cygpath"; Path = $cygpathPath }
)) {
    if (-not (Test-Path -LiteralPath $required.Path -PathType Leaf)) {
        throw "$($required.Label) is required for Windows production bundling: $($required.Path)"
    }
}

$nodeCommand = Get-Command node.exe -ErrorAction Stop
$npmCommand = Get-Command npm.cmd -ErrorAction Stop
$nodeDirectory = Split-Path -Parent $nodeCommand.Source
if (-not $nodeDirectory.Equals((Split-Path -Parent $npmCommand.Source), [StringComparison]::OrdinalIgnoreCase)) {
    throw "Node.js and npm must resolve from the same Windows directory."
}
$msysNodeDirectory = (& $cygpathPath -u $nodeDirectory).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($msysNodeDirectory)) {
    throw "MSYS2 could not translate the Node.js directory: $nodeDirectory"
}

$shellCheck = 'set -euo pipefail; export PATH="$PATH:{0}"; command -v bash | grep -Fx /usr/bin/bash; command -v rsync | grep -Fx /usr/bin/rsync; rsync --version | head -n 1; command -v node; node --version; command -v npm; npm --version' -f $msysNodeDirectory

& $bashPath @("-lc", $shellCheck)
if ($LASTEXITCODE -ne 0) {
    throw "MSYS2 Bash could not resolve a coherent rsync, Node.js, and npm build environment."
}

Write-Host "Windows production-bundle prerequisites are valid."
Write-Host "MSYS2 root: $resolvedRoot"
Write-Host "Bundler shell: $bashPath"
Write-Host "rsync: $rsyncPath"
