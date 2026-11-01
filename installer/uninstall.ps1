<#
.SYNOPSIS
    Removes NG Runner (or one version of it) for the signed-in Windows user. No admin needed.

.DESCRIPTION
    Why it exists: removing NG Runner must give every job back to Jim's chat and take away
    everything the installer added, and nothing else (spec 6.7; design section 5: "Pause,
    uninstall and Release all jobs to chat release every lease").

    A full uninstall: unregisters the two scheduled tasks, removes the Run-key value and the
    Start Menu item, asks NG Runner to stop, releases every job to chat (release-all), then
    removes the app-<version> folders, the launcher, the installer copy and the task XML.
    It keeps NG Runner's data folder (%LOCALAPPDATA%\NGRunner: the database, settings, logs
    and backups) and says where it is.

    With -Version, only that version's folder goes: a manual rollback. The launcher then
    starts the newest other version that passed its self-test.

    It removes nothing from a folder without the install record (ngrunner-install.json),
    and nothing at all if anything it would remove holds a link or junction: each folder
    is checked before the first file goes.

    Exit codes: 0 removed; 1 not removed (the last line says why); 2 a parameter is wrong.

.PARAMETER Root
    Where NG Runner is installed. Default: %LOCALAPPDATA%\Programs\NGRunner.

.PARAMETER Version
    Remove only this version (never the only one installed).

.PARAMETER NoRegister
    Leave the scheduled tasks, the Run key and the Start Menu item alone, release no jobs
    and start nothing (tests, and installs made with install.ps1 -NoRegister).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\NGRunner\installer\uninstall.ps1"
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Version = '',
    [switch]$NoRegister
)

. (Join-Path $PSScriptRoot 'common.ps1')

function Remove-OneVersion([string]$Root, [string]$Version, [string[]]$Versions) {
    if ($Version -notmatch $VersionPattern) { Fail ('-Version must be a version like 0.1.2 (it was: ' + $Version + ').') 2 }
    $target = Join-Path $Root "app-$Version"
    if ($Versions -notcontains $Version) { Fail ('NG Runner ' + $Version + ' is not installed in ' + $Root + ', so nothing was removed.') }
    if (@($Versions | Where-Object { $_ -ne $Version }).Count -eq 0) {
        Fail ($Version + ' is the only version installed, so it was not removed on its own. To remove NG Runner, run this script without -Version.')
    }
    [void](Get-PlainTree $target)
    Stop-NGRunner $target
    Remove-OwnTree $target
    Save-InstallRecord $Root
    if (-not $NoRegister) {
        Start-Process -FilePath (Join-Path $Root "launcher\$LauncherExe") -ArgumentList '--logon' -WorkingDirectory (Join-Path $Root 'launcher')
    }
    Say ('NG Runner ' + $Version + ' was removed. The launcher starts the newest other version that passed its self-test.')
    exit 0
}

function Release-AllJobs([string]$Root, [string[]]$Versions) {
    # Hand every job back to Jim's chat, with the newest version that is still installed.
    if ($Versions.Count -eq 0) { return }
    $app = Join-Path $Root ('app-' + $Versions[-1] + '\' + $AppExe)
    & $app release-all | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Say ('Releasing the jobs to chat did not finish (exit code ' + $LASTEXITCODE + '). Leases end by themselves within 2 and a half hours;' +
             ' until then, chat leaves those jobs alone.')
    }
}

# ----- the uninstall

Assert-FullLanguage
$Root = Resolve-Root $Root
try {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $Marker) -PathType Leaf)) {
        Fail ($Root + ' is not an NG Runner install (it has no ' + $Marker + '), so nothing was removed.')
    }
    $versions = Get-InstalledVersions $Root
    if ($Version) { Remove-OneVersion $Root $Version $versions }

    $parts = @(@($versions | ForEach-Object { Join-Path $Root "app-$_" }) + @('launcher', 'installer', 'tasks' | ForEach-Object { Join-Path $Root $_ }) |
               Where-Object { Test-Path -LiteralPath $_ })
    foreach ($part in $parts) { [void](Get-PlainTree $part) }   # every part is checked before anything goes

    if (-not $NoRegister) { Unregister-NGRunner }
    Stop-NGRunner $Root
    if (-not $NoRegister) { Release-AllJobs $Root $versions }
    foreach ($part in $parts) { Remove-OwnTree $part }
    [IO.File]::Delete((Join-Path $Root $Marker))
    if (@(Get-ChildItem -LiteralPath $Root -Force).Count -eq 0) { [IO.Directory]::Delete($Root, $false) }
    else { Say ('Other files are still in ' + $Root + '; they were not NG Runner''s, so they were left.') }
    Say ('NG Runner was removed from ' + $Root + '. NG Runner''s data folder was kept: ' + (Get-DataFolder) +
         ' (remove it by hand only after the last backup has been checked).')
    exit 0
} catch {
    Fail ('NG Runner was not removed: ' + $_.Exception.Message)
}
