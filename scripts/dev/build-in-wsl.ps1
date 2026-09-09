[CmdletBinding()]
param(
    [ValidateSet("Check", "Build")]
    [string]$Mode = "Check",
    [string]$Distribution = "Ubuntu",
    [string]$MirrorDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$gitCommonDirectory = (& git -c "safe.directory=$($repositoryRoot.Replace('\', '/'))" -C $repositoryRoot rev-parse --path-format=absolute --git-common-dir).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $gitCommonDirectory -PathType Container)) {
    throw "Unable to resolve the canonical text-to-cad Git directory."
}
$sourceRepository = Split-Path -Parent $gitCommonDirectory
$commit = (& git -c "safe.directory=$($repositoryRoot.Replace('\', '/'))" -C $repositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') {
    throw "Unable to resolve the current text-to-cad commit."
}

$linuxSource = (& wsl.exe -d $Distribution --exec wslpath -a $sourceRepository).Trim()
$linuxUser = (& wsl.exe -d $Distribution --exec id -un).Trim()
if ($LASTEXITCODE -ne 0 -or @($linuxSource, $linuxUser) -contains "") {
    throw "Unable to resolve paths and user in WSL distribution '$Distribution'."
}
if ([string]::IsNullOrWhiteSpace($MirrorDirectory)) {
    $MirrorDirectory = "/home/$linuxUser/.cache/text-to-cad/build-mirror"
}

$temporaryDriver = [IO.Path]::Combine([IO.Path]::GetTempPath(), "text-to-cad-wsl-build-$([guid]::NewGuid().ToString('N')).sh")
try {
    $driverText = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "wsl-build.sh")).Replace("`r`n", "`n")
    [IO.File]::WriteAllText($temporaryDriver, $driverText, [Text.UTF8Encoding]::new($false))
    $linuxDriver = (& wsl.exe -d $Distribution --exec wslpath -a $temporaryDriver).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($linuxDriver)) {
        throw "Unable to stage the WSL build driver."
    }

    & wsl.exe -d $Distribution --exec bash $linuxDriver `
        --source $linuxSource `
        --commit $commit `
        --mirror $MirrorDirectory `
        --mode $Mode.ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "WSL CAD $($Mode.ToLowerInvariant()) failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item -LiteralPath $temporaryDriver -Force -ErrorAction SilentlyContinue
}
