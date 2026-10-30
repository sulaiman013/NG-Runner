<#
.SYNOPSIS
    Installs or updates NG Runner for the signed-in Windows user. No admin rights needed.

.DESCRIPTION
    Why it exists: NG Runner runs on Jim's own PC for his own account, and Sulaiman installs
    and updates it over a remote session (design section 6, "Install" and "Updates"; spec 6.7).
    This script is the whole install, safe to run again:

    1. It checks the release folder against its manifest.json: every file's sha256, nothing
       missing, nothing extra. A copy damaged on its way to this PC is never installed.
    2. It copies the app into <Root>\app-<version>\, beside earlier versions and never over
       one, and the launcher into <Root>\launcher\ when that changed. Each copy is made under
       a temporary name, checked again, then renamed into place.
    3. It writes the two scheduled tasks' XML, filled in for this user, into <Root>\tasks\
       (the nightly drift check compares the registered tasks with these files).
    4. It runs the new version's self-test (NGRunner.exe --self-test --record). The launcher
       only ever starts a version that passed it; a version that fails is removed again.
    5. Unless -NoRegister, it registers the logon task and the 10-minute "ensure running"
       task from that XML, sets the Run-key fallback and makes the Start Menu item (which
       carries NG Runner's AUMID, so its notifications show). Unless -NoStart as well, it
       asks a running NG Runner to stop, then starts the launcher, which starts the newest
       version that passed its self-test (the old one stays as its fallback).

    It writes nowhere but <Root>, apart from the tasks, the Run key and the Start Menu item
    in step 5, and what the self-test makes (and removes) in NG Runner's data folder.

    Exit codes: 0 installed; 1 not installed, or not completely (the last line says why);
    2 a parameter is wrong.

.PARAMETER Root
    Where NG Runner is installed. Default: %LOCALAPPDATA%\Programs\NGRunner.

.PARAMETER Build
    The release folder, holding app\, launcher\, installer\ and manifest.json. Default: the
    folder above this script, which is where the script sits in a release.

.PARAMETER ManifestSha256
    The sha256 of the release's manifest.json, as the build printed it. When given, a release
    with any other manifest is refused.

.PARAMETER NoRegister
    Install the files and run the self-test only: no tasks, no Run key, no Start Menu item,
    and NG Runner is neither stopped nor started (tests, and installs only to look at).

.PARAMETER NoStart
    Do everything but stop or start NG Runner (a restore onto a new PC: install, restore the
    database, then start NG Runner from the Start Menu).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\<you>\Downloads\NGRunner-0.1.0\installer\install.ps1" -ManifestSha256 <sha256 the build printed>
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [string]$Build = '',
    [string]$ManifestSha256 = '',
    [switch]$NoRegister,
    [switch]$NoStart
)

. (Join-Path $PSScriptRoot 'common.ps1')

# ----- the release

function Read-Release([string]$Build) {
    $path = Join-Path $Build 'manifest.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Fail ('NG Runner was not installed: the release folder ' + $Build + ' has no manifest.json.')
    }
    if ($ManifestSha256) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
        if ($actual -ne $ManifestSha256.Trim()) {
            Fail ('NG Runner was not installed: manifest.json is not the one expected (its sha256 is ' + $actual.ToLowerInvariant() + ').')
        }
    }
    try { $manifest = [IO.File]::ReadAllText($path) | ConvertFrom-Json } catch { $manifest = $null }
    if ($null -eq $manifest -or $manifest.app -ne $AppName -or $manifest.format -ne 1 -or
        ('' + $manifest.version) -notmatch $VersionPattern -or $null -eq $manifest.files) {
        Fail 'NG Runner was not installed: manifest.json is not an NG Runner release manifest.'
    }
    $files = @{}
    foreach ($entry in $manifest.files.PSObject.Properties) { $files[$entry.Name] = ('' + $entry.Value).ToLowerInvariant() }
    foreach ($needed in @("app/$AppExe", "launcher/$LauncherExe", 'installer/uninstall.ps1', 'installer/common.ps1')) {
        if (-not $files.ContainsKey($needed)) { Fail ('NG Runner was not installed: the release has no ' + $needed + '.') }
    }
    return @{ Version = '' + $manifest.version; Files = $files }
}

function Select-Prefix([hashtable]$Files, [string]$Prefix) {
    $chosen = @{}
    foreach ($name in $Files.Keys) { if ($name.StartsWith($Prefix + '/', [StringComparison]::OrdinalIgnoreCase)) { $chosen[$name] = $Files[$name] } }
    return $chosen
}

function Assert-Release([string]$Build, [hashtable]$Files) {
    $found = @{}
    foreach ($folder in @('app', 'launcher', 'installer')) {
        $part = Get-TreeHashes (Join-Path $Build $folder) $folder
        foreach ($name in $part.Keys) { $found[$name] = $part[$name] }
    }
    $problems = Compare-Hashes $Files $found
    if ($problems.Count -gt 0) {
        Fail ('NG Runner was not installed: the release does not match its manifest: ' + ($problems -join '; ') + '.')
    }
}

# ----- copying into the install

function Copy-Checked([string]$From, [string]$To, [string]$Prefix, [hashtable]$Files) {
    # Copy $From to a new $To: under a temporary name first, checked against the manifest,
    # then renamed into place, so $To never exists half copied.
    $partial = $To + '.partial-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    Copy-Item -LiteralPath $From -Destination $partial -Recurse -Force
    $problems = Compare-Hashes $Files (Get-TreeHashes $partial $Prefix)
    if ($problems.Count -gt 0) {
        Remove-OwnTree $partial
        Fail ('NG Runner was not installed: the copy in ' + $To + ' did not match the release: ' + ($problems -join '; ') + '.')
    }
    [IO.Directory]::Move($partial, $To)
}

function Update-Folder([string]$From, [string]$To, [string]$Prefix, [hashtable]$Files) {
    # Replace $To with $From when it differs; the old copy is removed once the new one is in.
    if ((Test-Path -LiteralPath $To) -and (Compare-Hashes $Files (Get-TreeHashes $To $Prefix)).Count -eq 0) { return $false }
    if (-not (Test-Path -LiteralPath $To)) {
        Copy-Checked $From $To $Prefix $Files
        return $true
    }
    $old = $To + '.old-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-ProcessesUnder $To).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    [IO.Directory]::Move($To, $old)
    try {
        Copy-Checked $From $To $Prefix $Files
    } catch {
        if (-not (Test-Path -LiteralPath $To)) { [IO.Directory]::Move($old, $To) }
        throw
    }
    Remove-OwnTree $old
    return $true
}

function Write-TaskXml([string]$Template, [string]$Target, [hashtable]$Values) {
    # Fill in a task template (XML-escaped) and save it as UTF-16, as Windows exports tasks.
    $text = [IO.File]::ReadAllText($Template, [Text.Encoding]::UTF8)
    foreach ($key in $Values.Keys) {
        $text = $text.Replace('{{' + $key + '}}', [Security.SecurityElement]::Escape([string]$Values[$key]))
    }
    if ($text -match '\{\{[A-Z_]+\}\}') { Fail ('NG Runner was not installed: ' + $Template + ' asks for a value this script does not fill in: ' + $Matches[0] + '.') }
    $text = $text.Replace('<?xml version="1.0" encoding="UTF-8"?>', '<?xml version="1.0" encoding="UTF-16"?>')
    [IO.File]::WriteAllText($Target, $text, [Text.Encoding]::Unicode)
}

# ----- the install

Assert-FullLanguage
$Root = Resolve-Root $Root
if (-not $Build) { $Build = Split-Path -Parent $PSScriptRoot }
try {
    $Build = [IO.Path]::GetFullPath($Build)
    $release = Read-Release $Build
    $version = $release.Version
    Say ('Checking the NG Runner ' + $version + ' release in ' + $Build + ' ...')
    Assert-Release $Build $release.Files

    if (Test-Path -LiteralPath $Root) {
        $hasMarker = Test-Path -LiteralPath (Join-Path $Root $Marker) -PathType Leaf
        if (-not $hasMarker -and @(Get-ChildItem -LiteralPath $Root -Force).Count -gt 0) {
            Fail ('NG Runner was not installed: ' + $Root + ' is not an NG Runner install (it holds other files), so nothing was put there.')
        }
    } else {
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
    }

    $target = Join-Path $Root "app-$version"
    $appFiles = Select-Prefix $release.Files 'app'
    $madeNow = $false
    if (Test-Path -LiteralPath $target) {
        if ((Compare-Hashes $appFiles (Get-TreeHashes $target 'app')).Count -gt 0) {
            Fail ('NG Runner was not installed: app-' + $version + ' is already installed with other files;' +
                  ' give the new build a new version (pyproject.toml), then install that.')
        }
        Say ('NG Runner ' + $version + ' is already installed; its self-test runs again.')
    } else {
        Copy-Checked (Join-Path $Build 'app') $target 'app' $appFiles
        $madeNow = $true
    }
    if (Update-Folder (Join-Path $Build 'launcher') (Join-Path $Root 'launcher') 'launcher' (Select-Prefix $release.Files 'launcher')) {
        Say 'The launcher was updated.'
    }
    [void](Update-Folder (Join-Path $Build 'installer') (Join-Path $Root 'installer') 'installer' (Select-Prefix $release.Files 'installer'))

    $tasks = Join-Path $Root 'tasks'
    if (-not (Test-Path -LiteralPath $tasks)) { New-Item -ItemType Directory -Path $tasks | Out-Null }
    $values = @{
        USER_SID = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        LAUNCHER = Join-Path $Root "launcher\$LauncherExe"
        WORKING_DIR = Join-Path $Root 'launcher'
        START_BOUNDARY = (Get-Date).ToString("yyyy-MM-dd'T'HH:mm:ss")
    }
    foreach ($name in $TaskNames) {
        Write-TaskXml (Join-Path $Root "installer\tasks\$name.xml") (Join-Path $tasks "$name.xml") $values
    }
    Save-InstallRecord $Root

    $app = Join-Path $target $AppExe
    Say ('Running the self-test of NG Runner ' + $version + ' ...')
    & $app --self-test --record
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        if ($madeNow) {
            Remove-OwnTree $target
            Save-InstallRecord $Root
            Fail ('NG Runner was not installed: ' + $version + ' failed its self-test (exit code ' + $code + '), so it was removed again; the lines above say why.')
        }
        Fail ('NG Runner ' + $version + ' failed its self-test (exit code ' + $code + '); the launcher will not start it. The lines above say why.')
    }

    $missing = ''
    if (-not $NoRegister) {
        $missing = Register-NGRunner $Root $app
        if (-not $NoStart) {
            Stop-NGRunner $Root
            Start-Process -FilePath (Join-Path $Root "launcher\$LauncherExe") -ArgumentList '--logon' -WorkingDirectory (Join-Path $Root 'launcher')
            Say 'NG Runner was started.'
        }
    }
    if ($missing) { Fail ('NG Runner ' + $version + ' is installed in ' + $Root + ', but not completely: ' + $missing + '.') }
    Say ('NG Runner ' + $version + ' is installed in ' + $Root + '.')
    exit 0
} catch {
    Fail ('NG Runner was not installed: ' + $_.Exception.Message)
}
