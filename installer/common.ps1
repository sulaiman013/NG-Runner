<#
    What install.ps1 and uninstall.ps1 share (each dot-sources this file from its own folder).

    Why one file: both scripts must agree on what an install is (the folder layout, the
    install record, the processes that belong to it) and both remove folders, which must
    never follow a link or junction out of the install. Windows PowerShell 5.1's own
    recursion can follow junctions, so folders are walked here one level at a time, a link
    anywhere stops the walk before anything is removed, and only what the walk listed is
    deleted, deepest first.
#>

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$AppName = 'NGRunner'
$AppExe = 'NGRunner.exe'
$LauncherExe = 'NGRunnerLauncher.exe'
$Marker = 'ngrunner-install.json'
$TaskNames = @('NGRunner-logon', 'NGRunner-ensure')
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunValue = 'NGRunner'
$ShortcutName = 'NG Runner.lnk'
$StopEvent = 'Local\NGRunner.Stop'
$VersionPattern = '^[0-9]+(\.[0-9]+){0,3}$'

function Say([string]$Text) { Write-Host $Text }

function Fail([string]$Text, [int]$Code = 1) {
    Say $Text
    exit $Code
}

function Assert-FullLanguage {
    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
        Fail ('PowerShell runs scripts in ' + $ExecutionContext.SessionState.LanguageMode + ' mode on this PC' +
              ' (Smart App Control or App Control), so this script can''t run here unsigned. See the runbook, "Install".')
    }
}

function Resolve-Root([string]$Root) {
    # The install folder: a full path to a folder on a drive, written the way Windows writes it.
    if (-not $Root) { $Root = Join-Path $env:LOCALAPPDATA 'Programs\NGRunner' }
    $full = $null
    if ($Root -match '^[A-Za-z]:\\[^\\]') {
        try { $full = [IO.Path]::GetFullPath($Root) } catch { $full = $null }
    }
    if ($null -eq $full -or $full.TrimEnd('\') -ne $Root.TrimEnd('\')) {
        Fail ('-Root must be the full path of a folder, like C:\Users\<you>\AppData\Local\Programs\NGRunner' +
              ' (it was: ' + $Root + ').') 2
    }
    return $full.TrimEnd('\')
}

function Get-PlainTree([string]$Path) {
    # Everything in $Path, $Path first and each folder before what it holds. It stops at the
    # first link, junction or other reparse point, so nothing is ever reached through one.
    $items = New-Object System.Collections.Generic.List[object]
    $todo = New-Object System.Collections.Generic.Stack[string]
    $todo.Push($Path)
    while ($todo.Count -gt 0) {
        $item = Get-Item -LiteralPath $todo.Pop() -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw ($Path + ' holds a link or junction (' + $item.FullName + '), so nothing in it was removed or changed.')
        }
        $items.Add($item)
        if ($item.PSIsContainer) {
            foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) { $todo.Push($child.FullName) }
        }
    }
    return ,$items
}

function Remove-OwnTree([string]$Path) {
    # Remove $Path and all it holds; nothing at all when anything in it is a link.
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $items = Get-PlainTree $Path
    for ($i = $items.Count - 1; $i -ge 0; $i--) {
        $item = $items[$i]
        if ($item.PSIsContainer) {
            [IO.Directory]::Delete($item.FullName, $false)
        } else {
            if ($item.IsReadOnly) { $item.IsReadOnly = $false }
            [IO.File]::Delete($item.FullName)
        }
    }
}

function Get-TreeHashes([string]$Dir, [string]$Prefix) {
    # sha256 of every file in $Dir, by "<Prefix>/<path below Dir>" as manifest.json names them.
    $found = @{}
    if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return $found }
    $base = $Dir.TrimEnd('\').Length + 1
    foreach ($item in (Get-PlainTree $Dir)) {
        if ($item.PSIsContainer) { continue }
        $name = $Prefix + '/' + $item.FullName.Substring($base).Replace('\', '/')
        $found[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash.ToLowerInvariant()
    }
    return $found
}

function Compare-Hashes([hashtable]$Expected, [hashtable]$Found) {
    $problems = @()
    foreach ($name in @($Expected.Keys | Sort-Object)) {
        if (-not $Found.ContainsKey($name)) { $problems += ($name + ' is missing') }
        elseif ($Found[$name] -ne $Expected[$name]) { $problems += ($name + ' has changed') }
    }
    foreach ($name in @($Found.Keys | Sort-Object)) {
        if (-not $Expected.ContainsKey($name)) { $problems += ($name + ' is not in the manifest') }
    }
    return ,$problems
}

function ConvertTo-VersionKey([string]$Version) {
    $parts = @($Version.Split('.') | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) { $parts += 0 }
    return New-Object Version($parts[0], $parts[1], $parts[2], $parts[3])
}

function Get-InstalledVersions([string]$Root) {
    # The app-<version> folders that hold NGRunner.exe, oldest first.
    $found = @()
    foreach ($dir in @(Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue)) {
        if ($dir.Name -match '^app-([0-9]+(\.[0-9]+){0,3})$' -and
            -not ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
            (Test-Path -LiteralPath (Join-Path $dir.FullName $AppExe) -PathType Leaf)) {
            $found += $Matches[1]
        }
    }
    return ,@($found | Sort-Object { ConvertTo-VersionKey $_ })
}

function Save-InstallRecord([string]$Root) {
    # ngrunner-install.json marks the folder as an NG Runner install; uninstall.ps1 removes
    # nothing from a folder without it.
    $versions = Get-InstalledVersions $Root    # already an array: @() around it would nest it
    $record = [ordered]@{
        app = $AppName
        versions = $versions
        updated_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    $json = ConvertTo-Json -InputObject $record -Depth 3
    [IO.File]::WriteAllText((Join-Path $Root $Marker), $json, (New-Object Text.UTF8Encoding($false)))
}

function Get-ProcessesUnder([string]$Folder) {
    $prefix = $Folder.TrimEnd('\') + '\'
    return ,@(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $path = $null
        try { $path = $_.Path } catch { $path = $null }
        $path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Stop-NGRunner([string]$Folder, [int]$WaitSeconds = 120) {
    # Ask the NG Runner running from $Folder to stop (it finishes what it is doing first),
    # then end whatever is still running from there once $WaitSeconds have passed.
    if ((Get-ProcessesUnder $Folder).Count -eq 0) { return }
    try {
        $signal = [Threading.EventWaitHandle]::OpenExisting($StopEvent)
        [void]$signal.Set()
        $signal.Dispose()
        Say 'Asked NG Runner to stop.'
    } catch {
        Say 'NG Runner is not listening for a stop request.'
    }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-ProcessesUnder $Folder).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    $left = Get-ProcessesUnder $Folder
    if ($left.Count -gt 0) {
        Say ('NG Runner did not stop within ' + $WaitSeconds + ' seconds, so it was ended.')
        $left | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

function Register-NGRunner([string]$Root, [string]$App) {
    # The two tasks from <Root>\tasks, the Run-key fallback and the Start Menu item (made by the
    # app itself: the item carries NG Runner's AUMID). Returns what could not be done, or ''.
    $launcher = Join-Path $Root "launcher\$LauncherExe"
    foreach ($name in $TaskNames) {
        $xml = [IO.File]::ReadAllText((Join-Path $Root "tasks\$name.xml"), [Text.Encoding]::Unicode)
        Register-ScheduledTask -TaskName $name -Xml $xml -Force | Out-Null
    }
    New-ItemProperty -Path $RunKey -Name $RunValue -Value ('"' + $launcher + '" --logon') -PropertyType String -Force | Out-Null
    & $App install-shortcut --target $launcher | Out-Host   # shown, never returned with the result
    if ($LASTEXITCODE -ne 0) {
        return ('the Start Menu item could not be made (install-shortcut stopped with exit code ' + $LASTEXITCODE +
                '), so NG Runner''s notifications will not show')
    }
    return ''
}

function Unregister-NGRunner([string]$StartMenu = '') {
    # Undo Register-NGRunner: each part only if it is there.
    if (-not $StartMenu) { $StartMenu = [Environment]::GetFolderPath('Programs') }
    foreach ($name in $TaskNames) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Say ('Removed the scheduled task ' + $name + '.')
        }
    }
    if (Get-ItemProperty -Path $RunKey -Name $RunValue -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $RunKey -Name $RunValue
        Say 'Removed the Run-key fallback.'
    }
    $shortcut = Join-Path $StartMenu $ShortcutName
    if (Test-Path -LiteralPath $shortcut -PathType Leaf) {
        [IO.File]::Delete($shortcut)
        Say 'Removed the Start Menu item.'
    }
}

function Get-DataFolder {
    if ($env:NGR_APP_DATA) { return $env:NGR_APP_DATA }
    return (Join-Path $env:LOCALAPPDATA 'NGRunner')
}
