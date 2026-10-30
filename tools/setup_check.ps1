<#
.SYNOPSIS
    NG Runner Setup Check: a read-only look at Jim's PC before NG Runner is installed.

.DESCRIPTION
    Why this exists: NG Runner will run overnight on Jim's own PC. Whether that can work depends
    on facts only the PC can tell: sleep and Modern Standby, Smart App Control and antivirus,
    central management, automatic sign-in after updates (ARSO), long paths, WebView2,
    notifications, and the state of OneDrive (Mozaik Shared), Google Drive (G: and the CNC
    operations folder) and Mozaik. This script reads them once and writes a report for Sulaiman
    to review before Phase 1a (design section 6 "Install, in four parts", step 2; decisions 3
    and 13; risks E1-E4, F3 and F7).

    It changes nothing. It reads settings, file attributes and command output only. It never
    opens Mozaik or job files and never downloads OneDrive files (it reads attributes, not
    contents). The only files it writes are the report and its JSON copy. It never records
    passwords, keys or email addresses; for API-key variables it records only "set" or "not set".

    It runs in Windows PowerShell 5.1 as a normal user, and also in Constrained Language Mode
    (Smart App Control or a company App Control policy runs unsigned scripts that way), so it
    uses only cmdlets, native commands and core types. Checks that can hang (G:, OneDrive, WMI,
    dsregcmd) run as background jobs with time limits. A check that fails, times out or needs
    admin rights is reported as "could not check (reason)" and never stops the run.

    Exit codes: 0, the report was saved; 3, it could not be saved, so the window shows it and
    says to take a photo. Any other code means the script stopped before the end.

.PARAMETER OutDir
    Folder for the two report files. Default: the Desktop.

.PARAMETER CncPath
    Full path of the CNC operations folder, if it is not in one of the usual places on G:.

.PARAMETER NoJobs
    Run every check in this window, without background jobs or time limits (troubleshooting).

.PARAMETER NoPause
    Do not wait for Enter at the end. (It only waits when it was started from Explorer. When
    setup_check.cmd starts it, cmd.exe is the parent, so only the .cmd waits.)

.EXAMPLE
    setup_check.cmd
    What Jim double-clicks: it sits next to this script and passes any options through.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File "$((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders').Desktop)\setup_check.ps1"
    The guide's line for a PowerShell window: it finds the Desktop wherever Windows keeps it
    (OneDrive included), and works in Constrained Language Mode, where
    [Environment]::GetFolderPath does not.
#>
[CmdletBinding()]
param(
    [string]$OutDir = '',
    [string]$CncPath = '',
    [switch]$NoJobs,
    [switch]$NoPause
)

# ----- settings

$ScriptVersion = '1.0.2'
$Started = Get-Date
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

# What the design plans for this PC (design section 6, step 3), used to word the findings.
$PlannedActiveStart = 11          # Windows Update active hours 11:00 ...
$PlannedActiveEnd = 5             # ... to 05:00, so update restarts land after the night run
$PlannedTimeZone = 'Pacific Standard Time'
$LowDiskGB = 15
$StaleJobsDays = 14
$ScanBudgetSec = 120

# API-key variables: only "set" or "not set" is ever recorded, never a value.
$KeyVariables = @('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL')
$MustBeAbsent = @('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')

# Top-level folders of Mozaik Shared that may be named in the report. Any other name could be
# a job (a client surname), so it is reported only as "(other folder N)".
$MozaikTopNames = @('Data', 'Images', 'Insert Libraries', 'Jobs', 'Product Libraries', 'Textures', '_NG_Automation')

# Where the CNC operations folder usually is, relative to a Google Drive root. Shared folders
# added as shortcuts are found by trying $CncSuffixes under every .shortcut-targets-by-id folder.
$CncCandidates = @(
    'My Drive\Inspired Closets WC\Sales Folder\CNC operations',
    'Shared drives\Inspired Closets WC\Sales Folder\CNC operations',
    'My Drive\Sales Folder\CNC operations',
    'My Drive\CNC operations'
)
$CncSuffixes = @('Inspired Closets WC\Sales Folder\CNC operations', 'Sales Folder\CNC operations', 'CNC operations')

# Defender attack surface reduction rules that judge a program by reputation, so they can stop
# a new, rarely seen program such as NG Runner.
$ReputationAsrRules = @{
    '01443614-cd74-433a-b99e-2ecdc07bfc25' = 'Block executable files unless they meet a prevalence, age or trusted list criterion'
    'c1db55ab-c21a-4637-bb3f-a12568109d35' = 'Use advanced protection against ransomware'
}

$SectionOrder = @('This PC', 'Account and rights', 'Power and overnight', 'Security and management', 'Windows features NG Runner uses', 'OneDrive and Mozaik Shared', 'Google Drive', 'Mozaik', 'Tools')
# Checks whose failure to run goes on the "Needs attention" list. (Mozaik Shared and the CNC
# folder add their own, more specific item.)
$KeyChecks = @('windows', 'admin', 'env_keys', 'time_zone', 'disk_c', 'sleep_states', 'power_plan', 'buttons_lid', 'windows_update', 'arso', 'smart_app_control', 'app_control', 'antivirus', 'defender_rules', 'managed_device', 'long_paths', 'onedrive', 'google_drive', 'mozaik')
$ResultOrder = @('windows', 'hardware', 'powershell', 'time_zone', 'disk_c', 'admin', 'env_keys', 'sleep_states', 'power_plan', 'buttons_lid', 'windows_update', 'arso', 'remote_access', 'scheduled_tasks', 'smart_app_control', 'app_control', 'antivirus', 'defender_rules', 'managed_device', 'bitlocker', 'long_paths', 'webview2', 'notifications', 'onedrive', 'mozaik_shared', 'google_drive', 'cnc_folder', 'mozaik', 'python', 'claude_code', 'git')

$NotChecked = @(
    'The Google account type of the NG Fab Schedule owner (personal or Workspace) and Tracker_Ingest''s trigger runtime: check in Google Apps Script.',
    'Whether another PC (George''s) runs Mozaik on the shared OneDrive folder: ask Jim.',
    'Mozaik''s own version screen with a test job open: done on the call with Jim.',
    'Whether OneDrive is paused, and whether Drive folders are set to "Available offline": Windows does not show these to scripts; look at the OneDrive and Drive icons.',
    'Do not disturb and its automatic rules: Settings > System > Notifications.',
    'Whether Slack or Pushover notifications reach Jim''s phone: ask Jim.',
    'Whether files created on G: from another computer raise change events on this PC: NG Runner tests this later.',
    'Whether automatic sign-in after an update restart (ARSO) really works: proven by the restart in the unattended session.'
)

# ----- helpers (Constrained Language Mode safe: cmdlets, operators and core types only)

$ProfilePattern = ''
$ProfilePatternJson = ''
if ($env:USERPROFILE) {
    $ProfilePattern = '(?i)' + [regex]::Escape($env:USERPROFILE)
    $ProfilePatternJson = '(?i)' + [regex]::Escape(($env:USERPROFILE -replace '\\', '\\'))
}

function Hide-Profile {
    param([string]$Text)
    if (-not $Text -or -not $ProfilePattern) { return $Text }
    return ($Text -replace $ProfilePattern, '%USERPROFILE%')
}

function Hide-ProfileJson {
    # The same, for JSON text, where every backslash is written twice.
    param([string]$Json)
    if (-not $Json -or -not $ProfilePatternJson) { return $Json }
    return ($Json -replace $ProfilePatternJson, '%USERPROFILE%')
}

function Get-ShortError {
    param($Err)
    if ($null -eq $Err) { return 'unknown error' }
    $msg = ''
    if ($Err -is [System.Management.Automation.ErrorRecord]) { $msg = '' + $Err.Exception.Message }
    elseif ($Err -is [System.Exception]) { $msg = '' + $Err.Message }
    else { $msg = '' + $Err }
    $lines = @($msg -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    if ($lines.Count -gt 0) { $msg = $lines[0].Trim() } else { $msg = 'unknown error' }
    if ($msg -match 'denied|0x80070005|E_ACCESSDENIED' -and $msg -notmatch 'needs admin rights') { $msg = 'needs admin rights: ' + $msg }
    if ($msg.Length -gt 200) { $msg = $msg.Substring(0, 197) + '...' }
    return (Hide-Profile $msg)
}

function Get-RegProps {
    param([string]$Path)
    try { $p = Get-ItemProperty -LiteralPath $Path -ErrorAction Stop } catch { return $null }
    $h = [ordered]@{}
    foreach ($pr in $p.PSObject.Properties) {
        if ($pr.Name -notmatch '^PS(Path|ParentPath|ChildName|Drive|Provider)$') { $h[$pr.Name] = $pr.Value }
    }
    return $h
}

function Get-RegValue {
    param([string]$Path, [string]$Name)
    $h = Get-RegProps $Path
    if ($null -eq $h) { return $null }
    if (@($h.Keys) -contains $Name) { return $h[$Name] }
    return $null
}

function Test-RegValueSet {
    # True when the value exists and is not empty. The value itself is never kept.
    param([string]$Path, [string]$Name)
    try {
        $p = Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction Stop
        return (('' + $p.$Name).Length -gt 0)
    } catch { return $false }
}

function ConvertFrom-UnixSeconds {
    param($Value)
    $n = $Value -as [double]
    if ($null -eq $Value -or $null -eq $n -or $n -le 0) { return $null }
    return ([datetime]'1970-01-01T00:00:00Z').AddSeconds($n)
}

function Format-When {
    param($When)
    if ($null -eq $When -or ('' + $When) -eq '') { return '' }
    try { return ([datetime]$When).ToString('yyyy-MM-dd HH:mm') } catch { return ('' + $When) }
}

function Format-GB {
    param($Bytes)
    if ($null -eq $Bytes) { return '?' }
    return ('{0:N1} GB' -f ([double]$Bytes / 1073741824))
}

function Get-GB {
    param($Bytes)
    if ($null -eq $Bytes) { return $null }
    return ([double]([int64](([double]$Bytes / 1073741824) * 10)) / 10)
}

function Format-HexCode {
    param($Value)
    $n = $Value -as [int64]
    if ($null -eq $n) { return ('' + $Value) }
    if ($n -lt 0) { $n += 4294967296 }
    return ('0x{0:X8}' -f $n)
}

function Format-Seconds {
    param($Seconds)
    $s = $Seconds -as [int64]
    if ($null -eq $s) { return 'unknown' }
    if ($s -eq 0) { return 'never' }
    if ($s -lt 60) { return ('{0} s' -f $s) }
    if ($s -lt 3600) { return ('{0} min' -f [int64]($s / 60)) }
    return ('{0:N1} h' -f ($s / 3600))
}

function Format-Timeout {
    # A power timeout in seconds, as words: 0 means the PC never does it.
    param($Seconds)
    $s = $Seconds -as [int64]
    if ($null -eq $s) { return 'unknown' }
    if ($s -eq 0) { return 'never' }
    return ('after ' + (Format-Seconds $s))
}

function Format-Hour {
    param($Hour)
    $h = $Hour -as [int]
    if ($null -eq $h) { return ('' + $Hour) }
    return ('{0:00}:00' -f $h)
}

function Format-Wrapped {
    param([string]$Text, [int]$Width, [string]$FirstPrefix, [string]$NextPrefix)
    $out = @()
    $line = $FirstPrefix
    $hasWord = $false
    foreach ($w in @($Text -split ' ')) {
        if ($w -eq '') { continue }
        if ($hasWord -and ($line.Length + 1 + $w.Length) -gt $Width) {
            $out += $line
            $line = $NextPrefix + $w
        } elseif ($hasWord) {
            $line = $line + ' ' + $w
        } else {
            $line = $line + $w
        }
        $hasWord = $true
    }
    $out += $line
    return $out
}

function Invoke-NativeLines {
    # Runs a quick native command in this window. Empty input is piped in so a command that
    # asks a question (citool waits for Enter) sees end-of-input instead of waiting forever.
    param([string]$Exe, [string[]]$ArgList)
    $ErrorActionPreference = 'Continue'
    $out = '' | & $Exe @ArgList 2>&1
    $code = $LASTEXITCODE
    $lines = @()
    foreach ($o in @($out)) { $lines += ('' + $o) }
    return @{ exit = $code; lines = $lines }
}

function Get-InstalledPrograms {
    $list = @()
    foreach ($root in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall')) {
        foreach ($k in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $p = $null
            try { $p = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction Stop } catch { $p = $null }
            if ($p -and $p.DisplayName) {
                $scope = 'machine'
                if ($root -like 'HKCU*') { $scope = 'user' }
                $list += , @{ name = '' + $p.DisplayName; version = '' + $p.DisplayVersion; publisher = '' + $p.Publisher; location = '' + $p.InstallLocation; icon = '' + $p.DisplayIcon; scope = $scope }
            }
        }
    }
    return $list
}

# ----- results

$script:Results = @()
$script:Attention = @()
$script:Facts = @{}
$script:Timings = [ordered]@{}
$script:LeftRunning = @()
$script:OutputNotes = @()
$script:JobsUnavailable = ''
$script:UseJobs = -not $NoJobs

function Add-Result {
    param([string]$Id, [string]$Section, [string]$Title, [string]$Status, [string]$Summary, $Details = @(), $Data = $null, [string]$ErrorText = '')
    $lines = @()
    foreach ($d in @($Details)) { if ($null -ne $d -and ('' + $d) -ne '') { $lines += (Hide-Profile ('' + $d)) } }
    $script:Results += , ([ordered]@{
            id      = $Id
            section = $Section
            title   = $Title
            status  = $Status
            summary = (Hide-Profile $Summary)
            details = $lines
            data    = $Data
            error   = (Hide-Profile $ErrorText)
        })
}

function Add-Attention {
    # BLOCKS stops NG Runner until fixed; DECIDE needs a decision; SETUP is a change planned for
    # the unattended setup session; CHECK means it could not be checked and needs a person.
    param([string]$Tag, [string]$Id, [string]$Text)
    $rank = 9
    switch ($Tag) { 'BLOCKS' { $rank = 1 } 'DECIDE' { $rank = 2 } 'SETUP' { $rank = 3 } 'CHECK' { $rank = 4 } }
    $script:Attention += , ([ordered]@{ tag = $Tag; check = $Id; text = (Hide-Profile $Text); rank = $rank; seq = $script:Attention.Count })
}

function Invoke-Check {
    param([string]$Id, [string]$Section, [string]$Title, [scriptblock]$Body)
    $t = Get-Date
    try {
        $null = & $Body
    } catch {
        $reason = Get-ShortError $_
        Add-Result -Id $Id -Section $Section -Title $Title -Status 'could_not_check' -Summary ('could not check (' + $reason + ')') -ErrorText $reason
        if ($KeyChecks -contains $Id) { Add-Attention 'CHECK' $Id ($Title + ' could not be checked (' + $reason + '): confirm it by hand.') }
    }
    $script:Timings[$Id] = [int]((Get-Date) - $t).TotalMilliseconds
}

# ----- background probes (time-limited; run inline when jobs are unavailable or -NoJobs)

function Start-Probe {
    param([string]$Name, [scriptblock]$Body, $Cfg, [int]$TimeoutSec)
    $p = @{ name = $Name; timeout = $TimeoutSec; started = (Get-Date); job = $null; records = @(); error = ''; timedOut = $false; done = $false }
    if ($script:UseJobs) {
        try {
            $p['job'] = Start-Job -Name ('ngr-setup-' + $Name) -ScriptBlock $Body -ArgumentList $Cfg -ErrorAction Stop
        } catch {
            $script:UseJobs = $false
            $script:JobsUnavailable = Get-ShortError $_
        }
    }
    if ($null -eq $p['job']) {
        try { $p['records'] = @(& $Body $Cfg) } catch { $p['error'] = Get-ShortError $_ }
        $p['done'] = $true
        $script:Timings['probe:' + $Name] = [int]((Get-Date) - $p['started']).TotalMilliseconds
    }
    return $p
}

function Complete-Probe {
    param($Probe)
    if ($null -eq $Probe -or $Probe['done']) { return }
    $job = $Probe['job']
    $left = $Probe['timeout'] - [int]((Get-Date) - $Probe['started']).TotalSeconds
    if ($left -lt 1) { $left = 1 }
    $null = Wait-Job -Job $job -Timeout $left
    $state = '' + $job.State
    $Probe['records'] = @(Receive-Job -Job $job -ErrorAction SilentlyContinue)
    if ($state -eq 'Running' -or $state -eq 'NotStarted' -or $state -eq 'Blocked' -or $state -eq 'Stopping') {
        $Probe['timedOut'] = $true
        $Probe['error'] = ('timed out after {0} s' -f $Probe['timeout'])
        $script:LeftRunning += , $job
    } else {
        if ($state -eq 'Failed') {
            $reason = $null
            try { $reason = $job.ChildJobs[0].JobStateInfo.Reason } catch { $reason = $null }
            if ($reason) { $Probe['error'] = Get-ShortError $reason } else { $Probe['error'] = 'the background check failed' }
        }
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
    $Probe['done'] = $true
    $script:Timings['probe:' + $Probe['name']] = [int]((Get-Date) - $Probe['started']).TotalMilliseconds
}

function Stop-LeftoverProbes {
    # A probe that timed out is still running in its own powershell.exe; end it rather than
    # leave it behind (and rather than let this window wait for it on exit).
    if (@($script:LeftRunning).Count -eq 0) { return }
    try {
        foreach ($k in @(Get-CimInstance -ClassName Win32_Process -Filter ('ParentProcessId=' + $PID) -OperationTimeoutSec 10 -ErrorAction Stop)) {
            if ($k.Name -eq 'powershell.exe' -or $k.Name -eq 'pwsh.exe') { Stop-Process -Id $k.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    } catch {
        # Best effort: a leftover probe also ends when this window closes.
        $script:OutputNotes += ('A timed-out background check could not be ended early (' + (Get-ShortError $_) + ').')
    }
    foreach ($j in @($script:LeftRunning)) { Remove-Job -Job $j -Force -ErrorAction SilentlyContinue }
}

function Get-ProbeRecord {
    param($Probe, [string]$Key)
    if ($null -eq $Probe) { return $null }
    foreach ($r in @($Probe['records'])) {
        if ($r -is [System.Collections.IDictionary] -and ('' + $r['key']) -eq $Key) { return $r }
    }
    return $null
}

function Test-RecordOk {
    param($Rec)
    return ($null -ne $Rec -and [bool]$Rec['ok'])
}

function Get-RecordProblem {
    param($Probe, $Rec)
    if ($null -ne $Rec -and -not $Rec['ok']) { return (Get-ShortError ('' + $Rec['error'])) }
    if ($null -eq $Probe) { return 'not run' }
    if ($Probe['timedOut']) { return ('timed out after {0} s' -f $Probe['timeout']) }
    if ($Probe['error']) { return $Probe['error'] }
    return 'no result'
}

# Each probe body is self-contained (jobs do not see this script's functions) and emits one
# record per item as soon as it has it, so a probe that times out still returns what it got.

$CommandsBody = {
    param($Cfg)
    $ErrorActionPreference = 'Continue'
    foreach ($spec in @($Cfg['commands'])) {
        $parts = ('' + $spec) -split '\|', 3
        $rec = @{ key = $parts[0]; ok = $false; exit = $null; lines = @(); error = ''; ms = 0 }
        $begin = Get-Date
        try {
            $exe = $parts[1]
            $argList = @()
            if ($parts.Count -gt 2 -and $parts[2]) { $argList = @($parts[2] -split ' ') }
            $out = '' | & $exe @argList 2>&1
            $rec['exit'] = $LASTEXITCODE
            $lines = @()
            foreach ($o in @($out)) { $lines += ('' + $o) }
            $rec['lines'] = $lines
            $rec['ok'] = $true
        } catch {
            $rec['error'] = '' + $_.Exception.Message
        }
        $rec['ms'] = [int]((Get-Date) - $begin).TotalMilliseconds
        $rec
    }
}

$SystemBody = {
    param($Cfg)
    $ErrorActionPreference = 'Stop'
    function Rec {
        param([string]$RecKey, [scriptblock]$RecGet)
        $recBegin = Get-Date
        $recOut = @{ key = $RecKey; ok = $false; data = $null; error = ''; errorId = ''; ms = 0 }
        try { $recOut['data'] = & $RecGet; $recOut['ok'] = $true }
        catch { $recOut['error'] = '' + $_.Exception.Message; $recOut['errorId'] = '' + $_.FullyQualifiedErrorId }
        $recOut['ms'] = [int]((Get-Date) - $recBegin).TotalMilliseconds
        $recOut
    }
    function Measure-NgJobs {
        # A job folder carries the job's 6-digit date (Name_MMDDYY). NG Jobs also holds folders
        # such as Old and workshop or test ones, counted apart so the report never calls them
        # jobs. Counts only: the names could name clients.
        param([string]$Path)
        $jobs = 0
        $other = 0
        foreach ($d in @(Get-ChildItem -LiteralPath $Path -Force -Directory -ErrorAction Stop)) {
            if (('' + $d.Name) -match '(?<!\d)\d{6}(?!\d)') { $jobs++ } else { $other++ }
        }
        @{ jobs = $jobs; other = $other }
    }
    Rec 'os' {
        $o = Get-CimInstance -ClassName Win32_OperatingSystem -OperationTimeoutSec 20
        @{ caption = '' + $o.Caption; arch = '' + $o.OSArchitecture; version = '' + $o.Version; lastBoot = $o.LastBootUpTime }
    }
    Rec 'computer' {
        $c = Get-CimInstance -ClassName Win32_ComputerSystem -OperationTimeoutSec 20
        $dom = ''
        if ($c.PartOfDomain) { $dom = '' + $c.Domain }
        @{ maker = '' + $c.Manufacturer; model = '' + $c.Model; pcType = $c.PCSystemType; ram = $c.TotalPhysicalMemory; partOfDomain = [bool]$c.PartOfDomain; domain = $dom }
    }
    Rec 'enclosure' {
        $types = @()
        foreach ($e in @(Get-CimInstance -ClassName Win32_SystemEnclosure -OperationTimeoutSec 20)) { foreach ($ct in @($e.ChassisTypes)) { if ($null -ne $ct) { $types += [int]$ct } } }
        @{ types = $types }
    }
    Rec 'battery' {
        $b = @(Get-CimInstance -ClassName Win32_Battery -OperationTimeoutSec 20)
        @{ count = $b.Count }
    }
    Rec 'defender_status' {
        $m = Get-CimInstance -Namespace 'root/Microsoft/Windows/Defender' -ClassName MSFT_MpComputerStatus -OperationTimeoutSec 30
        @{ sac = '' + $m.SmartAppControlState; mode = '' + $m.AMRunningMode; service = $m.AMServiceEnabled; av = $m.AntivirusEnabled; rtp = $m.RealTimeProtectionEnabled; tamper = $m.IsTamperProtected; sigUpdated = $m.AntivirusSignatureLastUpdated; version = '' + $m.AMProductVersion }
    }
    Rec 'defender_prefs' {
        $p = Get-CimInstance -Namespace 'root/Microsoft/Windows/Defender' -ClassName MSFT_MpPreference -OperationTimeoutSec 30
        $ids = @()
        foreach ($i in @($p.AttackSurfaceReductionRules_Ids)) { if ($i) { $ids += ('' + $i) } }
        $acts = @()
        foreach ($a in @($p.AttackSurfaceReductionRules_Actions)) { if ($null -ne $a) { $acts += [int]$a } }
        @{ asrIds = $ids; asrActions = $acts; cfa = $p.EnableControlledFolderAccess }
    }
    Rec 'av_products' {
        $list = @()
        foreach ($a in @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName AntiVirusProduct -OperationTimeoutSec 30)) {
            if ($a) { $list += , @{ name = '' + $a.displayName; state = [int64]$a.productState; exe = '' + $a.pathToSignedProductExe } }
        }
        , $list
    }
    Rec 'deviceguard' {
        $d = Get-CimInstance -Namespace 'root/Microsoft/Windows/DeviceGuard' -ClassName Win32_DeviceGuard -OperationTimeoutSec 20
        @{ ci = $d.CodeIntegrityPolicyEnforcementStatus; umci = $d.UsermodeCodeIntegrityPolicyEnforcementStatus }
    }
    Rec 'bitlocker_cim' {
        $v = @(Get-CimInstance -Namespace 'root/cimv2/Security/MicrosoftVolumeEncryption' -ClassName Win32_EncryptableVolume -Filter "DriveLetter='C:'" -OperationTimeoutSec 20)
        if ($v.Count -eq 0) { throw 'no C: volume was listed' }
        @{ protection = [int]$v[0].ProtectionStatus }
    }
    Rec 'lsa_log' {
        $l = Get-WinEvent -ListLog 'Microsoft-Windows-LSA/Operational'
        @{ enabled = [bool]$l.IsEnabled; records = $l.RecordCount }
    }
    Rec 'arso_events' {
        $res = @{ c320 = 0; c321 = 0; c322 = 0; last320 = $null; last321 = $null; last322 = $null }
        try {
            foreach ($e in @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-LSA/Operational'; Id = 320, 321, 322 } -MaxEvents 500)) {
                $k = '' + $e.Id
                $res['c' + $k] = $res['c' + $k] + 1
                if ($null -eq $res['last' + $k] -or $e.TimeCreated -gt $res['last' + $k]) { $res['last' + $k] = $e.TimeCreated }
            }
        } catch {
            if (('' + $_.FullyQualifiedErrorId) -notlike 'NoMatchingEventsFound*') { throw }
        }
        $res
    }
    Rec 'tasks' {
        $list = @()
        $all = $null
        try { $all = @(Get-ScheduledTask -ErrorAction Stop) } catch { $all = $null }
        if ($null -eq $all) {
            # Restricted PowerShell may not load the ScheduledTasks module: use schtasks instead.
            # Only a few columns are kept; "Task To Run" (command lines) is dropped at once.
            $hdr = @()
            for ($h = 1; $h -le 30; $h++) { $hdr += ('c' + $h) }
            $rows = @('' | & schtasks.exe /query /fo csv /v /nh 2>$null | ConvertFrom-Csv -Header $hdr)
            if ($rows.Count -eq 0) { throw 'neither Get-ScheduledTask nor schtasks could list the tasks' }
            $seen = @{}
            foreach ($row in $rows) {
                $full = '' + $row.c2
                if (-not $full.StartsWith('\') -or $full -like '\Microsoft\*' -or $seen.ContainsKey($full)) { continue }
                $seen[$full] = $true
                $cut = $full.LastIndexOf('\')
                $list += , @{ path = $full.Substring(0, $cut + 1); name = $full.Substring($cut + 1); state = ('' + $row.c4).Trim(); triggers = ('' + $row.c19).Trim(); logon = ('' + $row.c5).Trim(); last = ($row.c6 -as [datetime]); next = ($row.c3 -as [datetime]); result = ($row.c7 -as [int64]) }
            }
            return , $list
        }
        foreach ($tk in $all) {
            if (('' + $tk.TaskPath) -like '\Microsoft\*') { continue }
            $info = $null
            try { $info = Get-ScheduledTaskInfo -InputObject $tk -ErrorAction Stop } catch { $info = $null }
            $trig = @()
            foreach ($tr in @($tk.Triggers)) {
                if ($tr) {
                    # Monthly triggers have no class of their own here, so they show as "Other".
                    $cls = (('' + $tr.CimClass.CimClassName) -replace '^MSFT_Task', '') -replace 'Trigger$', ''
                    if (-not $cls) { $cls = 'Other' }
                    $trig += $cls
                }
            }
            $last = $null; $next = $null; $result = $null
            if ($info) { $last = $info.LastRunTime; $next = $info.NextRunTime; $result = $info.LastTaskResult }
            $list += , @{ path = '' + $tk.TaskPath; name = '' + $tk.TaskName; state = '' + $tk.State; triggers = ($trig -join '+'); logon = '' + $tk.Principal.LogonType; last = $last; next = $next; result = $result }
        }
        , $list
    }
    Rec 'appx' {
        $list = @()
        foreach ($pat in @('*Claude*', '*PythonManager*', 'PythonSoftwareFoundation.Python.*')) {
            foreach ($p in @(Get-AppxPackage -Name $pat -ErrorAction SilentlyContinue)) { if ($p) { $list += , @{ name = '' + $p.Name; version = '' + $p.Version } } }
        }
        , $list
    }

    # Google Drive comes last: G: is the part most likely to hang, and everything above has
    # already been handed back by then.
    $volRec = Rec 'gd_volumes' {
        $v = @()
        foreach ($d in @(Get-CimInstance -ClassName Win32_LogicalDisk -OperationTimeoutSec 20)) {
            if (('' + $d.VolumeName) -eq 'Google Drive') { $v += , @{ letter = '' + $d.DeviceID; fs = '' + $d.FileSystem; size = $d.Size; free = $d.FreeSpace } }
        }
        , $v
    }
    $volRec
    $roots = @()
    foreach ($v in @($volRec['data'])) { if ($v -and $v['letter']) { $roots += ($v['letter'] + '\') } }
    foreach ($driveRoot in $roots) {
        Rec ('gd_top:' + $driveRoot) {
            $names = @()
            foreach ($i in @(Get-ChildItem -LiteralPath $driveRoot -Force -Directory -ErrorAction Stop)) { $names += ('' + $i.Name) }
            @{ root = $driveRoot; names = $names }
        }
    }
    $paths = @()
    if ($Cfg['extra']) { $paths += ('' + $Cfg['extra']) }
    foreach ($driveRoot in $roots) { foreach ($rel in @($Cfg['candidates'])) { $paths += (Join-Path $driveRoot $rel) } }
    foreach ($driveRoot in $roots) {
        $sc = Join-Path $driveRoot '.shortcut-targets-by-id'
        $ids = @()
        try {
            if (Test-Path -LiteralPath $sc) { foreach ($i in @(Get-ChildItem -LiteralPath $sc -Force -Directory -ErrorAction Stop | Select-Object -First 60)) { $ids += $i.FullName } }
        } catch { $ids = @() }
        foreach ($idDir in $ids) { foreach ($suf in @($Cfg['suffixes'])) { $paths += (Join-Path $idDir $suf) } }
    }
    $paths = @($paths | Select-Object -Unique)
    $findRec = Rec 'cnc_find' {
        $hit = ''
        $tried = 0
        $slowest = 0
        foreach ($cand in $paths) {
            $tried++
            $ts = Get-Date
            $isThere = Test-Path -LiteralPath $cand -PathType Container
            $ms = [int]((Get-Date) - $ts).TotalMilliseconds
            if ($ms -gt $slowest) { $slowest = $ms }
            if ($isThere) { $hit = $cand; break }
        }
        @{ found = $hit; tried = $tried; slowestMs = $slowest; extraGiven = [bool]$Cfg['extra']; volumes = $roots.Count }
    }
    $findRec
    $cnc = ''
    if ($findRec['ok'] -and $findRec['data']) { $cnc = '' + $findRec['data']['found'] }
    if ($cnc) {
        # Only counts and yes/no answers leave this job: child names could name clients.
        Rec 'cnc_detail' {
            $ts = Get-Date
            $kids = @(Get-ChildItem -LiteralPath $cnc -Force -ErrorAction Stop)
            $listMs = [int]((Get-Date) - $ts).TotalMilliseconds
            $names = @()
            foreach ($k in $kids) { $names += ('' + $k.Name) }
            $ngJobs = Join-Path $cnc 'NG Jobs'
            $ng = @{ jobs = $null; other = $null }
            $ngMs = $null
            if (Test-Path -LiteralPath $ngJobs -PathType Container) {
                $ts2 = Get-Date
                $ng = Measure-NgJobs $ngJobs
                $ngMs = [int]((Get-Date) - $ts2).TotalMilliseconds
            }
            @{ entries = $kids.Count; listMs = $listMs; hasNgJobs = ($names -contains 'NG Jobs'); ngJobFolders = $ng['jobs']; ngOtherFolders = $ng['other']; ngJobsListMs = $ngMs; hasNgConversion = ($names -contains 'NG Conversion program Claude') }
        }
    }
}

$ScanBody = {
    # Walks Mozaik Shared reading attributes only (never contents, so nothing is downloaded).
    param($Cfg)
    $ErrorActionPreference = 'Continue'
    $root = '' + $Cfg['root']
    $budget = [int]$Cfg['budget']
    $known = @($Cfg['known'])
    $begin = Get-Date
    $res = @{
        key = 'scan'; ok = $false; error = ''; partial = $false; folders = 0; files = 0; skipped = 0
        cloudOnly = 0; local = 0; pinned = 0; unpinned = 0; bytes = [int64]0; cloudBytes = [int64]0
        tops = @{}; rootAttr = $null; jobsAttr = $null; ngAutoAttr = $null; jobsPresent = $false; ngAutoPresent = $false
        jobFolders = 0; lck = 0; uns = 0; optLck = 0; newestJobs = $null; samples = @(); listErrors = 0; ms = 0
    }
    $sampleCount = @{}
    function Add-FileStat {
        param($Item, [string]$Top)
        $name = '' + $Item.Name
        if ($name -eq 'desktop.ini' -or $name.StartsWith('~$')) { $res['skipped']++; return }
        $a = $Item.Attributes.value__
        $len = [int64]$Item.Length
        $cloud = (($a -band 0x400000) -ne 0) -or (($a -band 0x1000) -ne 0)
        if (-not $res['tops'].ContainsKey($Top)) { $res['tops'][$Top] = @{ files = 0; cloudOnly = 0; local = 0; pinned = 0; bytes = [int64]0; cloudBytes = [int64]0 } }
        $b = $res['tops'][$Top]
        $res['files']++
        $b['files']++
        $res['bytes'] += $len
        $b['bytes'] += $len
        if ($cloud) { $res['cloudOnly']++; $b['cloudOnly']++; $res['cloudBytes'] += $len; $b['cloudBytes'] += $len }
        else { $res['local']++; $b['local']++ }
        if (($a -band 0x80000) -ne 0) { $res['pinned']++; $b['pinned']++ }
        if (($a -band 0x100000) -ne 0) { $res['unpinned']++ }
        if ($Top -eq 'Jobs') {
            $lower = $name.ToLower()
            if ($lower -eq 'mozaikjob.lck') { $res['lck']++ } elseif ($lower -eq 'mozaikjob.uns') { $res['uns']++ } elseif ($lower -eq 'optimizejob.lck') { $res['optLck']++ }
            if ($null -eq $res['newestJobs'] -or $Item.LastWriteTime -gt $res['newestJobs']) { $res['newestJobs'] = $Item.LastWriteTime }
        }
        $taken = 0
        if ($sampleCount.ContainsKey($Top)) { $taken = $sampleCount[$Top] }
        if ($taken -lt 2 -and @($res['samples']).Count -lt 6) {
            $sampleCount[$Top] = $taken + 1
            $rel = $name
            $full = '' + $Item.FullName
            if ($full.Length -gt $root.Length -and $full.ToLower().StartsWith($root.ToLower())) { $rel = $full.Substring($root.Length).TrimStart('\') }
            $parts = @($rel -split '\\')
            $shownName = '*' + $Item.Extension
            if ($name -match '^(Room\d+\.(des|bak|sbk)|MozaikJob\.\w+|OptimizeJob\.\w+)$') { $shownName = $name }
            $mid = ''
            if ($parts.Count -ge 3) {
                if ($Top -eq 'Jobs') { $mid = '<job>\'; if ($parts.Count -ge 4) { $mid = $mid + '...\' } } else { $mid = '...\' }
            }
            $state = 'on this PC'
            if ($cloud) { $state = 'online-only' } elseif (($a -band 0x80000) -ne 0) { $state = 'always kept on this PC' }
            $res['samples'] += , @{ path = $Top + '\' + $mid + $shownName; attr = ('0x{0:X8}' -f [int64]$a); state = $state; kb = [int64]($len / 1024); modified = $Item.LastWriteTime }
        }
    }
    try {
        $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
        $res['rootAttr'] = $rootItem.Attributes.value__
        $queue = @{}
        $queueTop = @{}
        $qCount = 0
        $other = 0
        $jobsPath = Join-Path $root 'Jobs'
        foreach ($it in @(Get-ChildItem -LiteralPath $root -Force -ErrorAction Stop)) {
            if ($it.PSIsContainer) {
                $label = ''
                foreach ($kn in $known) { if (('' + $it.Name) -eq $kn) { $label = $kn } }
                if (-not $label) { $other++; $label = '(other folder ' + $other + ')' }
                if ($label -eq 'Jobs') { $res['jobsPresent'] = $true; $res['jobsAttr'] = $it.Attributes.value__ }
                if ($label -eq '_NG_Automation') { $res['ngAutoPresent'] = $true; $res['ngAutoAttr'] = $it.Attributes.value__ }
                $res['folders']++
                $queue[$qCount] = $it.FullName
                $queueTop[$qCount] = $label
                $qCount++
            } else {
                Add-FileStat $it '(files at the top)'
            }
        }
        $i = 0
        while ($i -lt $qCount) {
            if (((Get-Date) - $begin).TotalSeconds -gt $budget -or $res['folders'] -gt 50000) { $res['partial'] = $true; break }
            $dir = $queue[$i]
            $top = $queueTop[$i]
            $queue.Remove($i)
            $queueTop.Remove($i)
            $i++
            $items = $null
            try { $items = @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop) } catch { $res['listErrors']++; continue }
            foreach ($it in $items) {
                if ($it.PSIsContainer) {
                    $res['folders']++
                    if ($top -eq 'Jobs' -and $dir -eq $jobsPath) { $res['jobFolders']++ }
                    $queue[$qCount] = $it.FullName
                    $queueTop[$qCount] = $top
                    $qCount++
                } else {
                    Add-FileStat $it $top
                }
            }
        }
        $res['ok'] = $true
    } catch {
        $res['error'] = '' + $_.Exception.Message
    }
    $res['ms'] = [int]((Get-Date) - $begin).TotalMilliseconds
    $res
}

# ----- start

Write-Host ('NG Runner Setup Check {0}: reading this PC''s settings. It changes nothing.' -f $ScriptVersion)
Write-Host 'This usually takes under a minute (at most about three).'

$script:Programs = @()
try { $script:Programs = @(Get-InstalledPrograms) } catch { $script:Programs = @() }

# ----- check: OneDrive (first, because the Mozaik Shared scan needs its folder)

Invoke-Check 'onedrive' 'OneDrive and Mozaik Shared' 'OneDrive' {
    $procs = @(Get-Process -Name OneDrive -ErrorAction SilentlyContinue)
    $exe = ''
    foreach ($p in $procs) { if ($p.Path) { $exe = '' + $p.Path; break } }
    $ver = '' + (Get-RegValue 'HKCU:\Software\Microsoft\OneDrive' 'Version')
    if (-not $ver -and $exe) { try { $ver = '' + (Get-Item -LiteralPath $exe).VersionInfo.ProductVersion } catch { $ver = '' } }
    $script:Facts['oneDriveRunning'] = ($procs.Count -gt 0)
    $accounts = @()
    foreach ($k in @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\OneDrive\Accounts' -ErrorAction SilentlyContinue)) {
        $v = Get-RegProps $k.PSPath
        if ($null -eq $v) { continue }
        $folder = '' + $v['UserFolder']
        if (-not $folder) { continue }
        $isWork = (('' + $v['Business']) -eq '1')
        $display = '' + $v['DisplayName']
        if (-not $display) { if ($isWork) { $display = '' + $k.PSChildName } else { $display = 'personal' } }
        $lastResult = $v['LastSignInResult'] -as [int64]
        $errorSince = ConvertFrom-UnixSeconds $v['SignInErrorStateStartTimestamp']
        $lastSignIn = ConvertFrom-UnixSeconds $v['LastSignInTime']
        $reauth = ConvertFrom-UnixSeconds $v['ReAuthEnteredTimestamp']
        $problem = ($null -ne $lastResult -and $lastResult -ne 0 -and $null -ne $errorSince)
        $mz = ''
        $direct = Join-Path $folder 'Mozaik Shared'
        if (Test-Path -LiteralPath $direct -PathType Container) { $mz = $direct }
        else {
            foreach ($d in @(Get-ChildItem -LiteralPath $folder -Directory -Force -ErrorAction SilentlyContinue)) { if (('' + $d.Name) -like '*Mozaik*Shared*') { $mz = $d.FullName; break } }
        }
        $accounts += , @{ key = '' + $k.PSChildName; work = $isWork; display = $display; folder = $folder; filesOnDemand = $v['LastKnownCloudFilesEnabled']; lastResult = $lastResult; errorSince = $errorSince; lastSignIn = $lastSignIn; reauth = $reauth; problem = $problem; mozaik = $mz; folderExists = (Test-Path -LiteralPath $folder -PathType Container) }
    }
    $mzAccount = $null
    foreach ($a in $accounts) { if ($a['mozaik'] -and $a['work']) { $mzAccount = $a; break } }
    if ($null -eq $mzAccount) { foreach ($a in $accounts) { if ($a['mozaik']) { $mzAccount = $a; break } } }
    if ($mzAccount) { $script:Facts['mozaikRoot'] = $mzAccount['mozaik']; $script:Facts['mozaikAccount'] = $mzAccount['display'] }
    $script:Facts['oneDriveAccounts'] = $accounts.Count

    $details = @()
    $data = [ordered]@{ running = ($procs.Count -gt 0); version = $ver; accounts = @() }
    foreach ($a in $accounts) {
        if ($a['work']) { $line = $a['key'] + ': "' + $a['display'] + '" (work or school account), folder ' + $a['folder'] }
        else { $line = $a['key'] + ': personal account, folder ' + $a['folder'] }
        if (-not $a['folderExists']) { $line += ' (folder missing)' }
        if ($null -ne $a['filesOnDemand']) { if (('' + $a['filesOnDemand']) -eq '1') { $line += ', Files On-Demand on' } else { $line += ', Files On-Demand off' } }
        if ($a['lastSignIn']) { $line += ', last sign-in ' + (Format-When $a['lastSignIn']) }
        if ($null -ne $a['lastResult'] -and $a['lastResult'] -ne 0) { $line += ', last sign-in result ' + (Format-HexCode $a['lastResult']) }
        if ($a['errorSince']) { $line += ', sign-in error state since ' + (Format-When $a['errorSince']) }
        if ($a['reauth'] -and $a['lastSignIn'] -and $a['reauth'] -gt $a['lastSignIn']) { $line += ', asked to sign in again since ' + (Format-When $a['reauth']) }
        if ($a['mozaik']) { $line += '; holds Mozaik Shared' }
        $details += $line
        $data['accounts'] += , ([ordered]@{ account = $a['key']; name = $a['display']; work = $a['work']; folder = (Hide-Profile $a['folder']); files_on_demand = ('' + $a['filesOnDemand']); last_sign_in = (Format-When $a['lastSignIn']); last_sign_in_result = $(if ($null -ne $a['lastResult']) { Format-HexCode $a['lastResult'] } else { '' }); sign_in_error_since = (Format-When $a['errorSince']); sign_in_problem = $a['problem']; holds_mozaik_shared = [bool]$a['mozaik'] })
    }
    if ($exe) { $details += ('Program: ' + $exe) }

    $status = 'ok'
    if ($procs.Count -gt 0) { $summary = 'OneDrive ' + $ver + ' is running' } else { $summary = 'OneDrive is not running' }
    $names = @()
    foreach ($a in $accounts) { if ($a['work']) { $names += ('"' + $a['display'] + '" (work)') } else { $names += 'personal' } }
    if ($accounts.Count -gt 0) { $summary += '; ' + $accounts.Count + ' account(s): ' + ($names -join ', ') } else { $summary += '; no OneDrive account is set up for this Windows user' }

    if ($accounts.Count -eq 0) {
        $status = 'attention'
        Add-Attention 'BLOCKS' 'onedrive' 'OneDrive is not signed in to any account for this Windows user, so Mozaik Shared is not on this PC.'
    } elseif ($procs.Count -eq 0) {
        $status = 'attention'
        Add-Attention 'BLOCKS' 'onedrive' 'OneDrive is not running, so Mozaik Shared is not syncing: start OneDrive and leave it running.'
    }
    foreach ($a in $accounts) {
        if ($a['problem']) {
            if ($a['mozaik']) {
                $status = 'attention'
                Add-Attention 'BLOCKS' 'onedrive' ('OneDrive account "' + $a['display'] + '" (it holds Mozaik Shared) has been in a sign-in error state since ' + (Format-When $a['errorSince']) + ' (last result ' + (Format-HexCode $a['lastResult']) + '): open OneDrive and sign in, or its files stop syncing.')
            } else {
                if ($status -eq 'ok') { $status = 'info' }
                $details += ('Account "' + $a['display'] + '" also reports a sign-in problem; it does not hold Mozaik Shared.')
            }
        }
    }
    Add-Result -Id 'onedrive' -Section 'OneDrive and Mozaik Shared' -Title 'OneDrive' -Status $status -Summary $summary -Details $details -Data $data
}

# ----- start the background probes

$specs = @(
    'powercfg_a|powercfg.exe|/a',
    'powercfg_scheme|powercfg.exe|/getactivescheme',
    'standbyidle|powercfg.exe|/qh SCHEME_CURRENT SUB_SLEEP STANDBYIDLE',
    'hibernateidle|powercfg.exe|/qh SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE',
    'rtcwake|powercfg.exe|/qh SCHEME_CURRENT SUB_SLEEP RTCWAKE',
    'unattendsleep|powercfg.exe|/qh SCHEME_CURRENT SUB_SLEEP UNATTENDSLEEP',
    'lidaction|powercfg.exe|/qh SCHEME_CURRENT SUB_BUTTONS LIDACTION',
    'pbuttonaction|powercfg.exe|/qh SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION',
    'sbuttonaction|powercfg.exe|/qh SCHEME_CURRENT SUB_BUTTONS SBUTTONACTION',
    'citool|citool.exe|-lp'
)
$pyCmd = @(Get-Command -Name py -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
if ($pyCmd) { $specs += ('py_list|' + $pyCmd.Source + '|--list') }
$gitCmd = @(Get-Command -Name git -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
if ($gitCmd) { $specs += ('git_version|' + $gitCmd.Source + '|--version') }
# dsregcmd goes last: it is the slowest command (it can run network tests).
$specs += 'dsregcmd|dsregcmd.exe|/status'

# Three background processes: commands, system (WMI, events, tasks, then G:), and the
# Mozaik Shared scan. Each hands back results one by one, so a timeout loses only what is left.
Write-Host 'Starting the checks that can be slow (power, management, Google Drive, OneDrive)...'
$PCommands = $null; $PSystem = $null; $PScan = $null
try {
    $PCommands = Start-Probe 'commands' $CommandsBody @{ commands = $specs } 90
    $PSystem = Start-Probe 'system' $SystemBody @{ candidates = $CncCandidates; suffixes = $CncSuffixes; extra = $CncPath } 120
    if ($script:Facts['mozaikRoot']) { $PScan = Start-Probe 'scan' $ScanBody @{ root = $script:Facts['mozaikRoot']; budget = $ScanBudgetSec; known = $MozaikTopNames } ($ScanBudgetSec + 45) }
} catch {
    $script:JobsUnavailable = 'could not start the background checks: ' + (Get-ShortError $_)
}
$PDsreg = $PCommands
$PDrive = $PSystem

Write-Host 'Reading Windows settings...'

# ----- check: PowerShell

Invoke-Check 'powershell' 'This PC' 'PowerShell' {
    $ver = $PSVersionTable.PSVersion.ToString()
    $mode = '' + $ExecutionContext.SessionState.LanguageMode
    $pol = @()
    $gpo = @()
    foreach ($e in @(Get-ExecutionPolicy -List)) {
        $pol += ('' + $e.Scope + '=' + $e.ExecutionPolicy)
        if ((('' + $e.Scope) -eq 'MachinePolicy' -or ('' + $e.Scope) -eq 'UserPolicy') -and ('' + $e.ExecutionPolicy) -ne 'Undefined') { $gpo += ('' + $e.Scope + '=' + $e.ExecutionPolicy) }
    }
    $bits = '64-bit'
    if ($env:PROCESSOR_ARCHITEW6432) { $bits = '32-bit on 64-bit Windows' } elseif ($env:PROCESSOR_ARCHITECTURE -eq 'x86') { $bits = '32-bit' }
    $jobs = 'on'
    if ($NoJobs) { $jobs = 'off (-NoJobs): checks ran without time limits' }
    elseif ($script:JobsUnavailable) { $jobs = 'unavailable (' + $script:JobsUnavailable + '); checks ran without time limits' }
    $summary = 'Windows PowerShell ' + $ver + ', ' + $mode + ', ' + $bits
    $details = @(('Background checks: ' + $jobs), ('Execution policy: ' + ($pol -join ', ')))
    if ($gpo.Count -gt 0) { $details += ('Execution policy is set by Group Policy: ' + ($gpo -join ', ')) }
    $status = 'ok'
    if ($mode -ne 'FullLanguage') {
        $status = 'attention'
        Add-Attention 'DECIDE' 'powershell' ('PowerShell ran this script in ' + $mode + ' mode: an App Control policy (Smart App Control On, or a company policy) restricts unsigned scripts and programs, so NG Runner must be signed or allowed (decision 13). Some checks could not run in this mode.')
    }
    if ($env:PROCESSOR_ARCHITEW6432) {
        $status = 'attention'
        Add-Attention 'CHECK' 'powershell' 'This ran in 32-bit PowerShell, which sees some settings differently: run it again in the normal Windows PowerShell.'
    }
    $script:Facts['languageMode'] = $mode
    Add-Result -Id 'powershell' -Section 'This PC' -Title 'PowerShell' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ version = $ver; language_mode = $mode; bitness = $bits; background_checks = $jobs; execution_policy = ($pol -join ', ') })
}

# ----- check: time zone

Invoke-Check 'time_zone' 'This PC' 'Time zone' {
    $tz = Get-TimeZone
    $now = Get-Date
    $dst = $now.IsDaylightSavingTime()
    $auto = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Services\tzautoupdate' 'Start'
    $autoText = 'unknown'
    if ($null -ne $auto) { if ([int]$auto -eq 4) { $autoText = 'off' } elseif ([int]$auto -eq 3) { $autoText = 'on' } else { $autoText = 'service start type ' + $auto } }
    $dstText = 'no'
    if ($dst) { $dstText = 'yes' }
    $summary = '' + $tz.Id + '; daylight saving time now: ' + $dstText
    $details = @(('Windows name: ' + $tz.DisplayName), ('Local time at the check: ' + $now.ToString('yyyy-MM-dd HH:mm') + ' (UTC' + (Get-Date -Format 'zzz') + ')'), ('Set time zone automatically: ' + $autoText))
    $status = 'ok'
    if (('' + $tz.Id) -ne $PlannedTimeZone) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'time_zone' ('The time zone is ' + $tz.Id + ': the night run (1:30 to 4:30 AM) and the 6:30 AM digest are planned in Pacific time.')
    }
    Add-Result -Id 'time_zone' -Section 'This PC' -Title 'Time zone' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ id = '' + $tz.Id; display = '' + $tz.DisplayName; utc_offset_now = (Get-Date -Format 'zzz'); daylight_saving_now = $dst; automatic = $autoText; checked_at = $now.ToString('yyyy-MM-dd HH:mm:ss') })
}

# ----- check: free space on C:

Invoke-Check 'disk_c' 'This PC' 'Disk space' {
    $d = Get-PSDrive -Name C -PSProvider FileSystem -ErrorAction Stop
    $free = [int64]$d.Free
    $total = [int64]$d.Free + [int64]$d.Used
    $script:Facts['cFree'] = $free
    $summary = 'C: has ' + (Format-GB $free) + ' free of ' + (Format-GB $total)
    $status = 'ok'
    if ($free -lt ([int64]$LowDiskGB * 1073741824)) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'disk_c' ('Only ' + (Format-GB $free) + ' is free on C: (less than ' + $LowDiskGB + ' GB): free up space before Mozaik Shared folders are pinned to this PC.')
    }
    Add-Result -Id 'disk_c' -Section 'This PC' -Title 'Disk space' -Status $status -Summary $summary -Data ([ordered]@{ free_gb = (Get-GB $free); total_gb = (Get-GB $total) })
}

# ----- check: admin rights

Invoke-Check 'admin' 'Account and rights' 'Admin rights' {
    $g = Invoke-NativeLines 'whoami.exe' @('/groups', '/fo', 'csv', '/nh')
    if ($g['exit'] -ne 0 -or @($g['lines']).Count -eq 0) { throw ('whoami /groups failed (exit ' + $g['exit'] + ')') }
    $rows = @($g['lines'] | ConvertFrom-Csv -Header 'Name', 'Type', 'SID', 'Attributes')
    $isAdmin = $false
    $level = 'unknown'
    foreach ($row in $rows) {
        $sid = '' + $row.SID
        if ($sid -eq 'S-1-5-32-544') { $isAdmin = $true }
        if ($sid -eq 'S-1-16-4096') { $level = 'low' }
        elseif ($sid -eq 'S-1-16-8192') { $level = 'medium' }
        elseif ($sid -eq 'S-1-16-8448') { $level = 'medium plus' }
        elseif ($sid -eq 'S-1-16-12288') { $level = 'high (elevated)' }
        elseif ($sid -eq 'S-1-16-16384') { $level = 'system' }
    }
    $elevated = ($level -eq 'high (elevated)' -or $level -eq 'system')
    $u = Invoke-NativeLines 'whoami.exe' @('/user', '/fo', 'csv', '/nh')
    $userSid = ''
    if ($u['exit'] -eq 0) { $ur = @($u['lines'] | ConvertFrom-Csv -Header 'Name', 'SID'); if ($ur.Count -gt 0) { $userSid = '' + $ur[0].SID } }
    $script:Facts['userSid'] = $userSid
    $script:Facts['isAdmin'] = $isAdmin
    $uac = Get-RegProps 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    $lua = $null
    $consent = $null
    if ($uac) { $lua = $uac['EnableLUA']; $consent = $uac['ConsentPromptBehaviorAdmin'] }
    $user = '' + $env:USERNAME
    if ($isAdmin) {
        if ($elevated) { $summary = $user + ' is an administrator; this window is elevated' }
        else { $summary = $user + ' is an administrator (this window is not elevated, as intended)' }
    } else { $summary = $user + ' is a standard user, not an administrator' }
    $details = @('Integrity level of this window: ' + $level)
    if ($null -ne $lua) {
        $luaText = 'on'
        if ([int]$lua -eq 0) { $luaText = 'off' }
        $details += ('User Account Control: ' + $luaText + ' (EnableLUA=' + $lua + ', ConsentPromptBehaviorAdmin=' + $consent + ')')
    }
    $status = 'ok'
    if (-not $isAdmin) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'admin' ('The Windows account ' + $user + ' is not an administrator: the elevated steps of the unattended session (long paths, power and update settings) need someone who can sign in as an administrator.')
    }
    if ($elevated) {
        $status = 'attention'
        Add-Attention 'CHECK' 'admin' 'This check ran as administrator. Run it again normally: G: and OneDrive can look different to an elevated window.'
    }
    if ($null -ne $lua -and [int]$lua -eq 0) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'admin' 'User Account Control is off, so every program runs as administrator: Windows notifications do not show for elevated programs, and they may not see G:.'
    }
    Add-Result -Id 'admin' -Section 'Account and rights' -Title 'Admin rights' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ user = $user; administrator = $isAdmin; elevated = $elevated; integrity = $level; uac_enabled = $(if ($null -eq $lua) { $null } else { [int]$lua -ne 0 }) })
}

# ----- check: API-key environment variables (presence only)

Invoke-Check 'env_keys' 'Account and rights' 'API key variables' {
    $data = [ordered]@{}
    $setNames = @()
    $details = @()
    foreach ($n in $KeyVariables) {
        $scopes = @()
        $isSet = $false
        try { $isSet = (('' + (Get-Item -LiteralPath ('env:' + $n) -ErrorAction Stop).Value).Length -gt 0) } catch { $isSet = $false }
        if ($isSet) { $scopes += 'this window' }
        if (Test-RegValueSet 'HKCU:\Environment' $n) { $scopes += 'user' }
        if (Test-RegValueSet 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' $n) { $scopes += 'machine' }
        $state = 'not set'
        if ($scopes.Count -gt 0) { $state = 'set (' + ($scopes -join ', ') + ')'; $setNames += $n }
        $data[$n] = $state
        $details += ($n + ': ' + $state)
    }
    $status = 'ok'
    if ($setNames.Count -eq 0) { $summary = 'None of the ' + $KeyVariables.Count + ' API key variables is set' }
    else { $summary = 'Set: ' + ($setNames -join ', ') }
    foreach ($n in $setNames) {
        $status = 'attention'
        if ($MustBeAbsent -contains $n) {
            Add-Attention 'BLOCKS' 'env_keys' ($n + ' is ' + $data[$n] + ': remove it. Anthropic tools use a key in the environment automatically; NG Runner keeps its key in Credential Manager only, and the design requires this variable to be absent.')
        } else {
            Add-Attention 'DECIDE' 'env_keys' ($n + ' is ' + $data[$n] + ': NG Runner never reads keys from the environment; decide whether it should stay.')
        }
    }
    Add-Result -Id 'env_keys' -Section 'Account and rights' -Title 'API key variables' -Status $status -Summary $summary -Details $details -Data $data
}

# ----- check: Windows Update active hours, policies and pending restarts

Invoke-Check 'windows_update' 'Power and overnight' 'Windows Update' {
    $ux = Get-RegProps 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
    $pol = Get-RegProps 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
    $au = Get-RegProps 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
    $mdm = Get-RegProps 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device\Update'
    $start = $null
    $end = $null
    $source = ''
    if ($ux -and (@($ux.Keys) -contains 'ActiveHoursStart')) { $start = $ux['ActiveHoursStart']; $end = $ux['ActiveHoursEnd']; $source = 'Settings' }
    $smart = $null
    if ($ux -and (@($ux.Keys) -contains 'SmartActiveHoursState')) { $smart = $ux['SmartActiveHoursState'] }
    $policyHours = $false
    if ($pol -and ('' + $pol['SetActiveHours']) -eq '1') { $policyHours = $true; $start = $pol['ActiveHoursStart']; $end = $pol['ActiveHoursEnd']; $source = 'Group Policy' }
    if ($mdm -and (@($mdm.Keys) -contains 'ActiveHoursStart')) { $policyHours = $true; $start = $mdm['ActiveHoursStart']; $end = $mdm['ActiveHoursEnd']; $source = 'device management (MDM)' }
    $rebootWU = Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    $rebootCBS = Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
    $pending = ($rebootWU -or $rebootCBS)
    if ($null -ne $start -and $null -ne $end) { $summary = 'Active hours ' + (Format-Hour $start) + ' to ' + (Format-Hour $end) + ' (set in ' + $source + ')' }
    else { $summary = 'Active hours: not set (Windows chooses them)' }
    if ($pending) { $summary += '; a restart is pending' } else { $summary += '; no restart pending' }
    $details = @()
    if ($null -ne $smart) {
        $smartText = 'SmartActiveHoursState=' + $smart
        if (('' + $smart) -eq '1') { $smartText = 'on (SmartActiveHoursState=1)' } elseif (('' + $smart) -eq '0') { $smartText = 'off (SmartActiveHoursState=0)' }
        $details += ('Adjust active hours automatically: ' + $smartText)
    }
    if ($ux -and $ux['PauseUpdatesExpiryTime']) { $details += ('Updates paused until: ' + $ux['PauseUpdatesExpiryTime']) }
    if ($pol) { foreach ($k in @($pol.Keys)) { $details += ('Policy WindowsUpdate\' + $k + ' = ' + $pol[$k]) } }
    if ($au) { foreach ($k in @($au.Keys)) { $details += ('Policy WindowsUpdate\AU\' + $k + ' = ' + $au[$k]) } }
    if ($mdm) {
        $mdmNames = @()
        foreach ($k in @($mdm.Keys)) { if ($k -notlike '*_ProviderSet' -and $k -notlike '*_WinningProvider') { $mdmNames += $k } }
        if ($mdmNames.Count -gt 0) { $details += ('Update policies from device management: ' + ($mdmNames -join ', ')) }
    }
    $details += ('Restart pending: Windows Update ' + $(if ($rebootWU) { 'yes' } else { 'no' }) + ', servicing ' + $(if ($rebootCBS) { 'yes' } else { 'no' }))
    $status = 'ok'
    $planned = (Format-Hour $PlannedActiveStart) + ' to ' + (Format-Hour $PlannedActiveEnd)
    if ($policyHours) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'windows_update' ('Windows Update active hours are set by ' + $source + ' (' + (Format-Hour $start) + ' to ' + (Format-Hour $end) + '), so they cannot be changed in Settings: the plan needs ' + $planned + '; an administrator changes the policy, or the plan adapts.')
    } elseif ($null -eq $start -or ([int]$start -ne $PlannedActiveStart) -or ([int]$end -ne $PlannedActiveEnd) -or ('' + $smart) -eq '1') {
        $status = 'attention'
        $now = 'not set'
        if ($null -ne $start) { $now = (Format-Hour $start) + ' to ' + (Format-Hour $end) }
        Add-Attention 'SETUP' 'windows_update' ('Windows Update active hours are ' + $now + ': the unattended session sets them by hand to ' + $planned + ', so update restarts land after the night run.')
    }
    Add-Result -Id 'windows_update' -Section 'Power and overnight' -Title 'Windows Update' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ active_hours_start = $start; active_hours_end = $end; set_in = $source; set_by_policy = $policyHours; automatic = ('' + $smart); restart_pending = $pending })
}

# ----- check: remote access (Chrome Remote Desktop)

Invoke-Check 'remote_access' 'Power and overnight' 'Remote access' {
    $svc = Get-Service -Name chromoting -ErrorAction SilentlyContinue
    $crd = @($script:Programs | Where-Object { $_['name'] -like 'Chrome Remote Desktop*' })
    $others = @($script:Programs | Where-Object { $_['name'] -match '^(AnyDesk|TeamViewer)' })
    $details = @()
    foreach ($p in $crd) { $details += ('Installed: ' + $p['name'] + ' ' + $p['version']) }
    foreach ($p in $others) { $details += ('Also installed: ' + $p['name'] + ' ' + $p['version']) }
    $status = 'ok'
    if ($svc) {
        $summary = 'Chrome Remote Desktop host service: ' + $svc.Status + ' (start: ' + $svc.StartType + ')'
        if (('' + $svc.Status) -ne 'Running' -or ('' + $svc.StartType) -eq 'Disabled') {
            $status = 'attention'
            Add-Attention 'SETUP' 'remote_access' ('The Chrome Remote Desktop host service is ' + $svc.Status + ' (start: ' + $svc.StartType + '): unattended access must work before the unattended setup session.')
        }
    } elseif ($crd.Count -gt 0) {
        $summary = 'Chrome Remote Desktop is installed, but its host service was not found'
        $status = 'attention'
        Add-Attention 'SETUP' 'remote_access' 'Chrome Remote Desktop is installed but its host service is missing: finish the unattended-access setup (with a PIN) before the unattended session.'
    } else {
        $summary = 'Chrome Remote Desktop host is not installed'
        $status = 'attention'
        Add-Attention 'SETUP' 'remote_access' 'Chrome Remote Desktop host is not installed: set it up with unattended access and a PIN before the unattended setup session.'
    }
    Add-Result -Id 'remote_access' -Section 'Power and overnight' -Title 'Remote access' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ crd_service = $(if ($svc) { '' + $svc.Status } else { 'missing' }); crd_start = $(if ($svc) { '' + $svc.StartType } else { '' }); other_tools = @($others | ForEach-Object { '' + $_['name'] }) })
}

# ----- check: long paths

Invoke-Check 'long_paths' 'Windows features NG Runner uses' 'Long paths' {
    $fs = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' 'LongPathsEnabled'
    $gp = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Policies' 'LongPathsEnabled'
    $on = (('' + $fs) -eq '1')
    $summary = 'LongPathsEnabled = ' + $(if ($null -eq $fs) { 'not set (off)' } else { '' + $fs }) + $(if ($on) { ' (on)' } else { '' })
    $details = @()
    if ($null -ne $gp) { $details += ('Group Policy value (CurrentControlSet\Policies) = ' + $gp) }
    $status = 'ok'
    if (-not $on) {
        $status = 'attention'
        if ($script:Facts['isAdmin']) {
            Add-Attention 'SETUP' 'long_paths' 'Long paths are off (real TAP paths reach 285 characters): the unattended session turns LongPathsEnabled on (needs admin).'
        } else {
            Add-Attention 'DECIDE' 'long_paths' 'Long paths are off and this account is not an administrator: either an administrator turns LongPathsEnabled on, or NG Runner uses the \\?\ prefix inside one helper.'
        }
    }
    Add-Result -Id 'long_paths' -Section 'Windows features NG Runner uses' -Title 'Long paths' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ long_paths_enabled = $fs; policy_value = $gp })
}

# ----- check: WebView2 and .NET (the board's window)

Invoke-Check 'webview2' 'Windows features NG Runner uses' 'WebView2' {
    $guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $found = @()
    foreach ($pair in @(@('machine', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\'), @('machine', 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\'), @('this user', 'HKCU:\Software\Microsoft\EdgeUpdate\Clients\'))) {
        $pv = '' + (Get-RegValue ($pair[1] + $guid) 'pv')
        if ($pv -and $pv -ne '0.0.0.0') { $found += ($pv + ' (' + $pair[0] + ')') }
    }
    $release = (Get-RegValue 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' 'Release') -as [int]
    $net = 'not found'
    if ($null -ne $release) {
        if ($release -ge 533320) { $net = '4.8.1 or later' } elseif ($release -ge 528040) { $net = '4.8' } elseif ($release -ge 461808) { $net = '4.7.2' } elseif ($release -ge 461308) { $net = '4.7.1' } elseif ($release -ge 460798) { $net = '4.7' } elseif ($release -ge 394802) { $net = '4.6.2' } else { $net = 'older than 4.6.2' }
    }
    $status = 'ok'
    if ($found.Count -gt 0) { $summary = 'WebView2 runtime ' + ($found -join ', ') } else { $summary = 'WebView2 runtime not found' }
    $summary += '; .NET Framework ' + $net
    if ($found.Count -eq 0) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'webview2' 'The WebView2 runtime was not found: the board opens in the browser instead, or WebView2 is installed for this user.'
    }
    if ($null -eq $release -or $release -lt 394802) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'webview2' ('.NET Framework is ' + $net + ': the board''s window needs 4.6.2 or later, otherwise it opens in the browser.')
    }
    Add-Result -Id 'webview2' -Section 'Windows features NG Runner uses' -Title 'WebView2' -Status $status -Summary $summary -Data ([ordered]@{ webview2 = $found; dotnet_release = $release; dotnet = $net })
}

# ----- check: notifications

Invoke-Check 'notifications' 'Windows features NG Runner uses' 'Notifications' {
    $offs = @()
    $details = @()
    $toast = Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications' 'ToastEnabled'
    if ($null -ne $toast) { $details += ('ToastEnabled = ' + $toast); if (('' + $toast) -eq '0') { $offs += 'app notifications are switched off' } }
    $noc = Get-RegProps 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings'
    if ($noc) {
        foreach ($k in @($noc.Keys)) {
            if ($k -like 'NOC_GLOBAL_SETTING_*') {
                $details += ($k + ' = ' + $noc[$k])
                if ($k -eq 'NOC_GLOBAL_SETTING_TOASTS_ENABLED' -and ('' + $noc[$k]) -eq '0') { $offs += 'notifications are switched off in Settings' }
            }
        }
    }
    foreach ($pk in @('HKCU:\Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications', 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CurrentVersion\PushNotifications')) {
        $v = Get-RegValue $pk 'NoToastApplicationNotification'
        if ($null -ne $v) { $details += ('Policy NoToastApplicationNotification = ' + $v); if (('' + $v) -eq '1') { $offs += 'a policy turns app notifications off' } }
    }
    $nc = Get-RegValue 'HKCU:\Software\Policies\Microsoft\Windows\Explorer' 'DisableNotificationCenter'
    if ($null -ne $nc) { $details += ('Policy DisableNotificationCenter = ' + $nc); if (('' + $nc) -eq '1') { $offs += 'a policy hides the notification center' } }
    $details += 'Do not disturb is stored in a form scripts cannot read: see Settings > System > Notifications.'
    $status = 'ok'
    if ($offs.Count -gt 0) {
        $status = 'attention'
        $summary = 'Off: ' + ($offs -join '; ')
        Add-Attention 'DECIDE' 'notifications' ('Windows notifications are off (' + ($offs -join '; ') + '): NG Runner''s pop-ups at the desk will not show; the phone channel still works.')
    } else { $summary = 'On (no switch-offs or policies found)' }
    Add-Result -Id 'notifications' -Section 'Windows features NG Runner uses' -Title 'Notifications' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ off_reasons = $offs })
}

# ----- check: Mozaik

Invoke-Check 'mozaik' 'Mozaik' 'Mozaik' {
    $progs = @($script:Programs | Where-Object { $_['name'] -match 'Mozaik' })
    $dirs = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs' } else { '' }), 'C:\')) {
        if ($base -and (Test-Path -LiteralPath $base)) {
            foreach ($d in @(Get-ChildItem -LiteralPath $base -Directory -Filter '*Mozaik*' -ErrorAction SilentlyContinue)) { $dirs += $d.FullName }
        }
    }
    foreach ($p in $progs) {
        if ($p['location'] -and (Test-Path -LiteralPath $p['location'] -PathType Container)) { $dirs += $p['location'] }
        $icon = ('' + $p['icon']) -replace ',\s*-?\d+$', ''
        $icon = $icon.Trim('"')
        if ($icon -match '\.exe$' -and (Test-Path -LiteralPath $icon -PathType Leaf)) { $dirs += (Split-Path -Parent $icon) }
    }
    $dirs = @($dirs | Select-Object -Unique)
    $exes = @()
    foreach ($d in $dirs) {
        foreach ($f in @(Get-ChildItem -LiteralPath $d -Recurse -Depth 2 -Filter '*.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 40)) {
            $vi = $null
            try { $vi = $f.VersionInfo } catch { $vi = $null }
            $prod = ''; $pver = ''; $fver = ''
            if ($vi) { $prod = '' + $vi.ProductName; $pver = '' + $vi.ProductVersion; $fver = '' + $vi.FileVersion }
            $exes += , @{ path = $f.FullName; name = $f.Name; product = $prod; version = $pver; fileVersion = $fver }
        }
    }
    $main = @($exes | Where-Object { $_['name'] -match 'mozaik' -or $_['product'] -match 'mozaik' })
    $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'mozaik' -or (('' + $_.Path) -match '\\Mozaik') })
    $lnks = @()
    foreach ($sm in @($(if ($env:ProgramData) { Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs' } else { '' }), $(if ($env:APPDATA) { Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs' } else { '' }))) {
        if ($sm -and (Test-Path -LiteralPath $sm)) { foreach ($l in @(Get-ChildItem -LiteralPath $sm -Recurse -Filter '*Mozaik*.lnk' -ErrorAction SilentlyContinue)) { $lnks += $l.Name } }
    }
    $regLines = @()
    foreach ($hive in @('HKCU:\Software', 'HKLM:\SOFTWARE', 'HKLM:\SOFTWARE\WOW6432Node')) {
        foreach ($k in @(Get-ChildItem -LiteralPath $hive -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like '*Mozaik*' })) {
            $sub = @()
            foreach ($s in @(Get-ChildItem -LiteralPath $k.PSPath -ErrorAction SilentlyContinue | Select-Object -First 15)) { $sub += $s.PSChildName }
            $regLines += ('Registry key ' + $hive + '\' + $k.PSChildName + $(if ($sub.Count -gt 0) { ' (subkeys: ' + ($sub -join ', ') + ')' } else { '' }))
            $vals = Get-RegProps $k.PSPath
            if ($vals) {
                # Only values that look like folder or file paths are shown; others could hold licence data.
                foreach ($vn in @($vals.Keys)) { $vv = '' + $vals[$vn]; if ($vv -match '^[A-Za-z]:\\|^\\\\') { $regLines += ('  ' + $vn + ' = ' + $vv) } }
            }
        }
    }
    $settingsDirs = @()
    foreach ($base in @($env:APPDATA, $env:LOCALAPPDATA, $env:ProgramData)) {
        # Names only: these folders can hold licence files, which are never opened.
        if ($base -and (Test-Path -LiteralPath $base)) { foreach ($d in @(Get-ChildItem -LiteralPath $base -Directory -Filter '*Mozaik*' -ErrorAction SilentlyContinue)) { $settingsDirs += $d.FullName } }
    }
    $details = @()
    foreach ($sd in $settingsDirs) { $details += ('Settings folder: ' + $sd) }
    foreach ($p in $progs) { $details += ('Installed program: ' + $p['name'] + ' ' + $p['version'] + $(if ($p['publisher']) { ' (' + $p['publisher'] + ')' } else { '' }) + $(if ($p['location']) { ', in ' + $p['location'] } else { '' })) }
    # The likeliest main program first: a name that starts with Mozaik, then the shallowest path.
    $main = @($main | Sort-Object -Property @{ Expression = { if ($_['name'] -match '^mozaik') { 0 } else { 1 } } }, @{ Expression = { @($_['path'] -split '\\').Count } })
    foreach ($e in @($main | Select-Object -First 8)) { $details += ('Program file: ' + $e['path'] + $(if ($e['version']) { ', version ' + $e['version'] } else { '' }) + $(if ($e['fileVersion'] -and $e['fileVersion'] -ne $e['version']) { ' (file ' + $e['fileVersion'] + ')' } else { '' })) }
    if ($main.Count -gt 8) { $details += ('... and ' + ($main.Count - 8) + ' more program files') }
    if ($main.Count -eq 0 -and $exes.Count -gt 0) { foreach ($e in @($exes | Select-Object -First 8)) { $details += ('Program file in a Mozaik folder: ' + $e['path'] + $(if ($e['version']) { ', version ' + $e['version'] } else { '' })) } }
    foreach ($l in $lnks) { $details += ('Start menu shortcut: ' + $l) }
    $details += $regLines
    foreach ($p in $procs) { $details += ('Running now: ' + $p.ProcessName + ' (process ' + $p.Id + ')' + $(if ($p.Path) { ', ' + $p.Path } else { '' })) }
    $status = 'ok'
    if ($main.Count -gt 0) {
        $summary = 'Mozaik found: ' + $main[0]['path'] + $(if ($main[0]['version']) { ', version ' + $main[0]['version'] } else { '' })
    } elseif ($progs.Count -gt 0) {
        $summary = 'Mozaik is listed as installed (' + $progs[0]['name'] + ' ' + $progs[0]['version'] + '), but its program file was not found'
        $status = 'attention'
        Add-Attention 'CHECK' 'mozaik' 'Mozaik is listed as installed, but its program file was not found: tell Sulaiman where Mozaik is installed.'
    } else {
        $summary = 'Mozaik was not found in Program Files, the installed-programs list or the Start menu'
        $status = 'attention'
        Add-Attention 'CHECK' 'mozaik' 'Mozaik was not found on this PC: tell Sulaiman where it is installed (NG Runner must know when it is running).'
    }
    if ($procs.Count -gt 0) { $summary += '; running now (' + ((@($procs | ForEach-Object { $_.ProcessName }) | Select-Object -Unique) -join ', ') + ')' } else { $summary += '; not running now' }
    Add-Result -Id 'mozaik' -Section 'Mozaik' -Title 'Mozaik' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ installed_entries = @($progs | ForEach-Object { $_['name'] + ' ' + $_['version'] }); program_files = @($main | ForEach-Object { (Hide-Profile $_['path']) + ' ' + $_['version'] }); running = @($procs | ForEach-Object { '' + $_.ProcessName }) })
}

# ----- collect the background probes

Write-Host 'Waiting for the background checks...'
foreach ($probe in @($PCommands, $PSystem, $PScan)) {
    if ($probe) { try { Complete-Probe $probe } catch { $probe['done'] = $true; $probe['error'] = Get-ShortError $_ } }
}

# ----- check: Windows edition and build

Invoke-Check 'windows' 'This PC' 'Windows' {
    $cv = Get-RegProps 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    if ($null -eq $cv) { throw 'the Windows version key could not be read' }
    $build = ('' + $cv['CurrentBuild']) -as [int]
    $ubr = '' + $cv['UBR']
    $edition = '' + $cv['EditionID']
    $display = '' + $cv['DisplayVersion']
    $osRec = Get-ProbeRecord $PSystem 'os'
    $caption = ''
    $arch = ''
    $lastBoot = $null
    if (Test-RecordOk $osRec) { $caption = ('' + $osRec['data']['caption']) -replace '^Microsoft\s+', ''; $arch = '' + $osRec['data']['arch']; $lastBoot = $osRec['data']['lastBoot'] }
    if (-not $caption) {
        $caption = '' + $cv['ProductName']
        if ($build -ge 22000) { $caption = $caption -replace 'Windows 10', 'Windows 11' }
    }
    $summary = $caption
    if ($display) { $summary += ' ' + $display }
    $summary += ', build ' + $build
    if ($ubr) { $summary += '.' + $ubr }
    if ($arch) { $summary += ', ' + $arch }
    $culture = '' + (Get-UICulture).Name
    $details = @(('Edition ID: ' + $edition), ('Display language: ' + $culture))
    if ($lastBoot) { $details += ('Last restart: ' + (Format-When $lastBoot) + ' (' + [int]((Get-Date) - [datetime]$lastBoot).TotalDays + ' days ago)') }
    if ($culture -notlike 'en-*') { $details += 'Windows is not in English: power settings are read by position, which is less certain.' }
    $status = 'ok'
    if ($edition -like 'Core*') {
        $status = 'attention'
        Add-Attention 'DECIDE' 'windows' ('This is Windows Home (' + $edition + '): the update and automatic sign-in policies the plan relies on exist only on Pro, Enterprise and Education.')
    }
    if ($build -gt 0 -and $build -lt 22000) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'windows' ('This PC runs Windows 10 (build ' + $build + '): NG Runner is planned and tested for Windows 11.')
    }
    if (('' + $cv['InstallationType']) -eq 'Server') {
        $status = 'attention'
        Add-Attention 'DECIDE' 'windows' 'This is Windows Server, not a desktop edition: Smart App Control and automatic sign-in work differently.'
    }
    Add-Result -Id 'windows' -Section 'This PC' -Title 'Windows' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ name = $caption; edition_id = $edition; display_version = $display; build = $build; ubr = $ubr; architecture = $arch; last_restart = (Format-When $lastBoot); display_language = $culture })
}

# ----- check: hardware

Invoke-Check 'hardware' 'This PC' 'Hardware' {
    $cs = Get-ProbeRecord $PSystem 'computer'
    if (-not (Test-RecordOk $cs)) { throw (Get-RecordProblem $PSystem $cs) }
    $en = Get-ProbeRecord $PSystem 'enclosure'
    $bat = Get-ProbeRecord $PSystem 'battery'
    $types = @()
    if (Test-RecordOk $en) { $types = @($en['data']['types']) }
    $kind = 'unknown type'
    $laptopTypes = @(8, 9, 10, 11, 14, 30, 31, 32)
    $desktopTypes = @(3, 4, 5, 6, 7, 13, 15, 16, 24, 34, 35, 36)
    foreach ($t in $types) { if ($laptopTypes -contains [int]$t) { $kind = 'laptop or portable' } elseif ($desktopTypes -contains [int]$t -and $kind -eq 'unknown type') { $kind = 'desktop' } }
    $pcType = $cs['data']['pcType'] -as [int]
    if ($kind -eq 'unknown type' -and $pcType -eq 2) { $kind = 'laptop or portable' } elseif ($kind -eq 'unknown type' -and $pcType -eq 1) { $kind = 'desktop' }
    $batteries = $null
    if (Test-RecordOk $bat) { $batteries = $bat['data']['count'] -as [int] }
    $script:Facts['portable'] = ($kind -eq 'laptop or portable')
    $script:Facts['batteries'] = $batteries
    $batText = 'battery: unknown'
    if ($null -ne $batteries) { if ($batteries -eq 0) { $batText = 'no battery' } else { $batText = 'battery present (a laptop, or a UPS)' } }
    $ram = Format-GB $cs['data']['ram']
    $summary = ('' + $cs['data']['maker'] + ' ' + $cs['data']['model']).Trim() + '; ' + $kind + '; ' + $batText + '; ' + $ram + ' RAM'
    $details = @('Chassis type code(s): ' + ($types -join ', ') + '; PC system type: ' + $pcType)
    Add-Result -Id 'hardware' -Section 'This PC' -Title 'Hardware' -Status 'info' -Summary $summary -Details $details -Data ([ordered]@{ maker = '' + $cs['data']['maker']; model = '' + $cs['data']['model']; kind = $kind; batteries = $batteries; ram_gb = (Get-GB $cs['data']['ram']); chassis_types = $types })
}

# ----- check: sleep states and Modern Standby (powercfg /a)

Invoke-Check 'sleep_states' 'Power and overnight' 'Sleep states' {
    $rec = Get-ProbeRecord $PCommands 'powercfg_a'
    if (-not (Test-RecordOk $rec)) { throw (Get-RecordProblem $PCommands $rec) }
    $avail = @()
    $notAvail = @()
    $section = 0
    foreach ($l in @($rec['lines'])) {
        $raw = ('' + $l).TrimEnd()
        if ($raw.Trim() -eq '') { continue }
        if ($raw -notmatch '^\s') { $section++; continue }
        if ($raw -match '^\t' -or $raw -match '^\s{5,}') { continue }
        if ($section -eq 1) { $avail += $raw.Trim() } elseif ($section -ge 2) { $notAvail += $raw.Trim() }
    }
    if ($section -eq 0) { throw ('powercfg /a gave no usable output (exit ' + $rec['exit'] + ')') }
    $modern = (@($avail | Where-Object { $_ -match 'S0' }).Count -gt 0)
    $s3 = (@($avail | Where-Object { $_ -match 'S3' }).Count -gt 0)
    $script:Facts['modernStandby'] = $modern
    if ($avail.Count -gt 0) { $summary = 'Available: ' + ($avail -join ', ') } else { $summary = 'No sleep states are available' }
    if ($modern) { $summary += ' (Modern Standby)' } else { $summary += ' (no Modern Standby)' }
    $details = @()
    if ($notAvail.Count -gt 0) { $details += ('Not available: ' + ($notAvail -join ', ')) }
    $status = 'ok'
    if ($modern) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'sleep_states' 'This PC uses Modern Standby: desktop apps are paused while it sleeps. It must stay awake on AC power during the run window, or runs happen at first wake and the digest says so (decision 3).'
    }
    Add-Result -Id 'sleep_states' -Section 'Power and overnight' -Title 'Sleep states' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ available = $avail; not_available = $notAvail; modern_standby = $modern; s3 = $s3; raw = @($rec['lines']) })
}

function Read-PowerSetting {
    param($Rec)
    $res = @{ present = $false; ac = $null; dc = $null; names = @{}; byPosition = $false }
    if (-not (Test-RecordOk $Rec)) { return $res }
    $hexLines = @()
    $pending = $null
    foreach ($l in @($Rec['lines'])) {
        $s = ('' + $l).Trim()
        if ($s -match '^Possible Setting Index:\s*([0-9A-Fa-f]+)$') { $pending = [int]('0x' + $Matches[1]) }
        elseif ($s -match '^Possible Setting Friendly Name:\s*(.+)$') { if ($null -ne $pending) { $res['names'][$pending] = $Matches[1].Trim(); $pending = $null } }
        elseif ($s -match '^Current AC Power Setting Index:\s*0x([0-9A-Fa-f]+)') { $res['ac'] = [int64]('0x' + $Matches[1]); $res['present'] = $true }
        elseif ($s -match '^Current DC Power Setting Index:\s*0x([0-9A-Fa-f]+)') { $res['dc'] = [int64]('0x' + $Matches[1]); $res['present'] = $true }
        if ($s -match ':\s*0x([0-9A-Fa-f]{8})$') { $hexLines += $Matches[1] }
    }
    if (-not $res['present'] -and $hexLines.Count -ge 2) {
        # Windows in another language: the current AC and DC values are always the last two.
        $res['ac'] = [int64]('0x' + $hexLines[$hexLines.Count - 2])
        $res['dc'] = [int64]('0x' + $hexLines[$hexLines.Count - 1])
        $res['present'] = $true
        $res['byPosition'] = $true
    }
    foreach ($k in @('ac', 'dc')) { if ($null -ne $res[$k] -and $res[$k] -lt 0) { $res[$k] = $res[$k] + 4294967296 } }
    return $res
}

function Get-PowerChoice {
    param($Setting, $Index, [string[]]$Fallback)
    if ($null -eq $Index) { return 'unknown' }
    $i = [int]$Index
    if ($Setting['names'].ContainsKey($i)) { return ('' + $Setting['names'][$i]) }
    if ($i -ge 0 -and $i -lt $Fallback.Count) { return $Fallback[$i] }
    return ('value ' + $i)
}

# ----- check: power plan (sleep, hibernate, wake timers)

Invoke-Check 'power_plan' 'Power and overnight' 'Sleep and wake timers' {
    $schemeRec = Get-ProbeRecord $PCommands 'powercfg_scheme'
    $plan = 'unknown'
    if (Test-RecordOk $schemeRec) { foreach ($l in @($schemeRec['lines'])) { if (('' + $l) -match '\(([^)]+)\)\s*$') { $plan = $Matches[1] } } }
    $sleepRec = Get-ProbeRecord $PCommands 'standbyidle'
    if (-not (Test-RecordOk $sleepRec)) { throw (Get-RecordProblem $PCommands $sleepRec) }
    $sleep = Read-PowerSetting $sleepRec
    $hib = Read-PowerSetting (Get-ProbeRecord $PCommands 'hibernateidle')
    $wake = Read-PowerSetting (Get-ProbeRecord $PCommands 'rtcwake')
    $unatt = Read-PowerSetting (Get-ProbeRecord $PCommands 'unattendsleep')
    if (-not $sleep['present']) { throw 'powercfg did not show the sleep timeout' }
    # The wake timer values are fixed by Windows: 0 off, 1 on, 2 important wake timers only.
    $wakeNames = @('off', 'on', 'important ones only')
    $summary = 'Plan: ' + $plan + '. On AC power: sleep ' + (Format-Timeout $sleep['ac'])
    if ($hib['present']) { $summary += ', hibernate ' + (Format-Timeout $hib['ac']) }
    if ($wake['present']) { $summary += ', wake timers ' + (Get-PowerChoice @{ names = @{} } $wake['ac'] $wakeNames) }
    $details = @('On battery: sleep ' + (Format-Timeout $sleep['dc']) + $(if ($hib['present']) { ', hibernate ' + (Format-Timeout $hib['dc']) } else { '' }) + $(if ($wake['present']) { ', wake timers ' + (Get-PowerChoice @{ names = @{} } $wake['dc'] $wakeNames) } else { '' }))
    if ($unatt['present']) { $details += ('After an automatic wake, Windows sleeps again after ' + (Format-Seconds $unatt['ac']) + ' unless a program keeps it awake (unattended sleep timeout)') }
    if ($sleep['byPosition']) { $details += 'Values were read by position (Windows is not in English).' }
    $status = 'ok'
    $sleeps = (($sleep['ac'] -as [int64]) -gt 0) -or ($hib['present'] -and ($hib['ac'] -as [int64]) -gt 0)
    if ($sleeps -and $wake['present'] -and ($wake['ac'] -as [int64]) -eq 0) {
        $status = 'attention'
        Add-Attention 'SETUP' 'power_plan' ('On AC power the PC sleeps ' + (Format-Timeout $sleep['ac']) + ' and wake timers are off, so the 1:25 AM wake task could not wake it: the unattended session turns wake timers on.')
    } elseif ($sleeps -and -not $wake['present']) {
        $details += 'The wake timer setting is not shown on this PC.'
    }
    Add-Result -Id 'power_plan' -Section 'Power and overnight' -Title 'Sleep and wake timers' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ plan = $plan; sleep_ac_s = $sleep['ac']; sleep_dc_s = $sleep['dc']; hibernate_ac_s = $hib['ac']; hibernate_dc_s = $hib['dc']; wake_timers_ac = $wake['ac']; wake_timers_dc = $wake['dc']; unattended_sleep_s = $unatt['ac'] })
}

# ----- check: power button and lid

Invoke-Check 'buttons_lid' 'Power and overnight' 'Power button and lid' {
    $pbRec = Get-ProbeRecord $PCommands 'pbuttonaction'
    if (-not (Test-RecordOk $pbRec)) { throw (Get-RecordProblem $PCommands $pbRec) }
    $pb = Read-PowerSetting $pbRec
    $lid = Read-PowerSetting (Get-ProbeRecord $PCommands 'lidaction')
    $sb = Read-PowerSetting (Get-ProbeRecord $PCommands 'sbuttonaction')
    $buttonNames = @('Do nothing', 'Sleep', 'Hibernate', 'Shut down', 'Turn off the display')
    $lidNames = @('Do nothing', 'Sleep', 'Hibernate', 'Shut down')
    $parts = @()
    if ($pb['present']) { $parts += ('power button on AC: ' + (Get-PowerChoice $pb $pb['ac'] $buttonNames)) }
    if ($sb['present']) { $parts += ('sleep button on AC: ' + (Get-PowerChoice $sb $sb['ac'] $buttonNames)) }
    $portable = [bool]$script:Facts['portable'] -or (($script:Facts['batteries'] -as [int]) -gt 0)
    if ($portable -and $lid['present']) { $parts += ('closing the lid on AC: ' + (Get-PowerChoice $lid $lid['ac'] $lidNames)) }
    elseif (-not $portable) { $parts += 'lid: not applicable (desktop)' }
    $summary = ($parts -join '; ')
    if (-not $summary) { $summary = 'not shown on this PC' }
    $summary = $summary.Substring(0, 1).ToUpper() + $summary.Substring(1)
    $details = @()
    if ($pb['present']) { $details += ('On battery: power button ' + (Get-PowerChoice $pb $pb['dc'] $buttonNames) + $(if ($portable -and $lid['present']) { ', lid ' + (Get-PowerChoice $lid $lid['dc'] $lidNames) } else { '' })) }
    $status = 'ok'
    if ($pb['present'] -and ($pb['ac'] -as [int64]) -ne 0) {
        $status = 'attention'
        Add-Attention 'SETUP' 'buttons_lid' ('The power button on AC is set to "' + (Get-PowerChoice $pb $pb['ac'] $buttonNames) + '": the unattended session sets it to "Do nothing" (AC only).')
    }
    if ($portable -and $lid['present'] -and ($lid['ac'] -as [int64]) -ne 0) {
        $status = 'attention'
        Add-Attention 'SETUP' 'buttons_lid' ('Closing the lid on AC is set to "' + (Get-PowerChoice $lid $lid['ac'] $lidNames) + '": the unattended session sets it to "Do nothing" (AC only).')
    }
    Add-Result -Id 'buttons_lid' -Section 'Power and overnight' -Title 'Power button and lid' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ power_button_ac = $pb['ac']; power_button_dc = $pb['dc']; sleep_button_ac = $sb['ac']; lid_ac = $lid['ac']; lid_dc = $lid['dc']; portable = $portable })
}

# ----- check: BitLocker (read without admin from Explorer's property; CIM needs admin)

Invoke-Check 'bitlocker' 'Security and management' 'BitLocker' {
    $val = $null
    $how = ''
    $comError = ''
    try {
        $shell = New-Object -ComObject Shell.Application
        $val = $shell.NameSpace('C:\').Self.ExtendedProperty('System.Volume.BitLockerProtection')
        $how = 'from Explorer''s BitLocker property'
    } catch { $comError = Get-ShortError $_ }
    $state = 'unknown'
    $text = ''
    if ($null -ne $val) {
        switch ([int]$val) {
            1 { $state = 'on'; $text = 'on' }
            2 { $state = 'off'; $text = 'off' }
            3 { $state = 'other'; $text = 'encrypting' }
            4 { $state = 'other'; $text = 'decrypting' }
            5 { $state = 'suspended'; $text = 'suspended' }
            6 { $state = 'on'; $text = 'on (locked)' }
            8 { $state = 'other'; $text = 'waiting for activation (encrypted, no key protector yet)' }
            default { $state = 'unknown'; $text = 'not reported (value ' + $val + ')' }
        }
    } else {
        $cim = Get-ProbeRecord $PSystem 'bitlocker_cim'
        if (Test-RecordOk $cim) {
            $how = 'from the BitLocker WMI class'
            switch ([int]$cim['data']['protection']) { 0 { $state = 'off'; $text = 'protection off' } 1 { $state = 'on'; $text = 'protection on' } default { $state = 'unknown'; $text = 'protection unknown' } }
        } else {
            $why = @()
            if ($comError -match 'language mode') { $why += 'Explorer''s BitLocker property is blocked in restricted PowerShell' }
            elseif ($comError) { $why += ('Explorer''s BitLocker property is not readable (' + $comError + ')') }
            $why += ('the BitLocker WMI class: ' + (Get-RecordProblem $PSystem $cim))
            throw ($why -join '; ')
        }
    }
    $script:Facts['bitlocker'] = $state
    Add-Result -Id 'bitlocker' -Section 'Security and management' -Title 'BitLocker' -Status 'info' -Summary ('C: ' + $text + ' (' + $how + ')') -Data ([ordered]@{ state = $state; text = $text; raw = $val })
}

# ----- check: automatic restart sign-on (ARSO)

Invoke-Check 'arso' 'Power and overnight' 'Automatic sign-in after updates (ARSO)' {
    $sys = Get-RegProps 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    $disable = $null
    $config = $null
    if ($sys) {
        if (@($sys.Keys) -contains 'DisableAutomaticRestartSignOn') { $disable = $sys['DisableAutomaticRestartSignOn'] }
        if (@($sys.Keys) -contains 'AutomaticRestartSignOnConfig') { $config = $sys['AutomaticRestartSignOnConfig'] }
    }
    $optOut = $null
    $sid = '' + $script:Facts['userSid']
    if ($sid) { $optOut = Get-RegValue ('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\UserARSO\' + $sid) 'OptOut' }
    $logRec = Get-ProbeRecord $PSystem 'lsa_log'
    $evRec = Get-ProbeRecord $PSystem 'arso_events'
    $details = @()
    if ($null -eq $disable) { $details += 'Policy "Sign-in and lock last interactive user automatically after a restart": not set (on by default)' }
    else { $details += ('Policy DisableAutomaticRestartSignOn = ' + $disable + $(if (('' + $disable) -eq '1') { ' (turned off)' } else { ' (on)' })) }
    if ($null -eq $config) { $details += 'Policy AutomaticRestartSignOnConfig: not set (default: only when BitLocker is on and not suspended)' }
    else { $details += ('Policy AutomaticRestartSignOnConfig = ' + $config + $(if (('' + $config) -eq '1') { ' (always)' } else { ' (only when BitLocker is on and not suspended)' })) }
    if ($null -eq $optOut) { $details += 'Settings "Use my sign-in info to automatically finish setting up after an update": no opt-out recorded' }
    else { $details += ('Settings "Use my sign-in info to automatically finish setting up after an update": OptOut = ' + $optOut + $(if (('' + $optOut) -eq '1') { ' (turned off)' } else { '' })) }
    $logOn = $null
    if (Test-RecordOk $logRec) {
        $logOn = [bool]$logRec['data']['enabled']
        $count = $logRec['data']['records']
        $details += ('LSA Operational log (where ARSO records events 320/322): ' + $(if ($logOn) { 'on' } else { 'off' }) + $(if ($null -ne $count -and ('' + $count) -ne '') { ', ' + $count + ' records' } else { '' }))
    }
    else { $details += ('LSA Operational log: could not check (' + (Get-RecordProblem $PSystem $logRec) + ')') }
    $last320 = $null
    $last322 = $null
    if (Test-RecordOk $evRec) {
        $ev = $evRec['data']
        $last320 = $ev['last320']
        $last322 = $ev['last322']
        $details += ('ARSO events: 320 (set up) ' + $ev['c320'] + $(if ($last320) { ', last ' + (Format-When $last320) } else { '' }) + '; 322 (failed) ' + $ev['c322'] + $(if ($last322) { ', last ' + (Format-When $last322) } else { '' }) + '; 321 (cleared) ' + $ev['c321'])
    } elseif ($logOn -ne $false) { $details += ('ARSO events: could not check (' + (Get-RecordProblem $PSystem $evRec) + ')') }
    $bl = '' + $script:Facts['bitlocker']
    if (-not $bl) { $bl = 'unknown' }
    $details += ('BitLocker on C: ' + $bl)
    $user = '' + $env:USERNAME
    if (('' + $disable) -eq '1') { $summary = 'Turned off by policy' }
    elseif (('' + $optOut) -eq '1') { $summary = 'Turned off in the sign-in options of ' + $user }
    else { $summary = 'Not turned off by policy or by ' + $user }
    if ($last320 -and (-not $last322 -or $last320 -gt $last322)) { $summary += '; last set up ' + (Format-When $last320) }
    elseif ($last322) { $summary += '; last attempt FAILED ' + (Format-When $last322) }
    elseif ($logOn -eq $false) { $summary += '; no record (the LSA log is off)' }
    else { $summary += '; no ARSO events recorded yet' }
    $status = 'ok'
    if (('' + $disable) -eq '1') {
        $status = 'attention'
        Add-Attention 'DECIDE' 'arso' 'Automatic sign-in after updates is turned off by policy: after an update restart nobody is signed in, so NG Runner, G: and OneDrive stay off until someone signs in.'
    }
    if (('' + $optOut) -eq '1') {
        $status = 'attention'
        Add-Attention 'SETUP' 'arso' ('The Windows user ' + $user + ' has turned off "Use my sign-in info to automatically finish setting up after an update": the unattended session turns it on.')
    }
    if ($last322 -and (-not $last320 -or $last322 -gt $last320)) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'arso' ('The last automatic sign-in setup failed (event 322 on ' + (Format-When $last322) + '): find out why before relying on it.')
    }
    $prove = @()
    if ($logOn -eq $false) { $prove += 'First turn on the LSA Operational log (admin): it is off, and without it ARSO leaves no record.' }
    if ((('' + $config) -ne '1') -and $bl -ne 'on' -and ('' + $disable) -ne '1') { $prove += ('BitLocker is ' + $bl + ', and Microsoft''s documents disagree on whether ARSO works then.') }
    if ($prove.Count -gt 0) {
        $status = 'attention'
        Add-Attention 'SETUP' 'arso' ('Prove automatic sign-in after updates (ARSO) with the restart test in the unattended session. ' + ($prove -join ' '))
    }
    Add-Result -Id 'arso' -Section 'Power and overnight' -Title 'Automatic sign-in after updates (ARSO)' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ policy_disable = $disable; policy_config = $config; user_opt_out = $optOut; lsa_log_on = $logOn; last_320 = (Format-When $last320); last_322 = (Format-When $last322); bitlocker = $bl })
}

# ----- check: scheduled tasks that are not Microsoft's (night jobs that already exist)

Invoke-Check 'scheduled_tasks' 'Power and overnight' 'Scheduled tasks' {
    $rec = Get-ProbeRecord $PSystem 'tasks'
    if (-not (Test-RecordOk $rec)) { throw (Get-RecordProblem $PSystem $rec) }
    $tasks = @($rec['data'] | Where-Object { $_ })
    $details = @()
    $items = @()
    $night = 0
    foreach ($t in @($tasks | Select-Object -First 40)) {
        $name = (('' + $t['path'] + $t['name']) -replace 'S-1-5-21-[\d-]+', '<SID>') -replace '\{[0-9A-Fa-f-]{36}\}', '{GUID}'
        $next = ''
        $nextWhen = $t['next'] -as [datetime]
        if ($nextWhen) { $next = Format-When $nextWhen; if ($nextWhen.Hour -lt 6) { $night++ } }
        $last = ''
        if ($t['last'] -and ([datetime]$t['last']).Year -gt 2000) { $last = Format-When $t['last'] }
        $line = $name + ' | ' + $t['state'] + ' | ' + $(if ($t['triggers']) { $t['triggers'] } else { 'no trigger' }) + ' | next ' + $(if ($next) { $next } else { '-' }) + ' | last ' + $(if ($last) { $last } else { 'never' }) + ' | result ' + (Format-HexCode $t['result']) + ' | ' + $t['logon']
        $details += $line
        $items += , ([ordered]@{ task = $name; state = '' + $t['state']; triggers = '' + $t['triggers']; next_run = $next; last_run = $last; last_result = (Format-HexCode $t['result']); logon = '' + $t['logon'] })
    }
    if ($tasks.Count -gt 40) { $details += ('... and ' + ($tasks.Count - 40) + ' more') }
    $summary = '' + $tasks.Count + ' task(s) not from Microsoft; ' + $night + ' with their next run between midnight and 6 AM'
    Add-Result -Id 'scheduled_tasks' -Section 'Power and overnight' -Title 'Scheduled tasks' -Status 'info' -Summary $summary -Details $details -Data ([ordered]@{ count = $tasks.Count; tasks = $items })
}

# ----- check: Smart App Control (decision 13)

Invoke-Check 'smart_app_control' 'Security and management' 'Smart App Control' {
    $reg = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' 'VerifiedAndReputablePolicyState'
    $def = Get-ProbeRecord $PSystem 'defender_status'
    $defState = ''
    if (Test-RecordOk $def) { $defState = '' + $def['data']['sac'] }
    $state = 'unknown'
    if ($null -ne $reg) {
        switch ([int]$reg) { 0 { $state = 'Off' } 1 { $state = 'On' } 2 { $state = 'Evaluation' } default { $state = 'unknown (value ' + $reg + ')' } }
    } elseif ($defState) {
        if ($defState -match '^Off') { $state = 'Off' } elseif ($defState -match '^Eval') { $state = 'Evaluation' } elseif ($defState -match '^On') { $state = 'On' }
    }
    $script:Facts['sac'] = $state
    $details = @()
    if ($null -ne $reg) { $details += ('Registry VerifiedAndReputablePolicyState = ' + $reg + ' (0 off, 1 on, 2 evaluation)') }
    if ($defState) {
        $details += ('Microsoft Defender reports SmartAppControlState = ' + $defState)
        $defShort = ''
        if ($defState -match '^Off') { $defShort = 'Off' } elseif ($defState -match '^Eval') { $defShort = 'Evaluation' } elseif ($defState -match '^On') { $defShort = 'On' }
        if ($null -ne $reg -and $defShort -and $defShort -ne $state) { $details += 'The registry and Defender disagree; the registry value is used. Confirm in Windows Security > App & browser control.' }
    }
    elseif (-not (Test-RecordOk $def)) { $details += ('Microsoft Defender status: could not check (' + (Get-RecordProblem $PSystem $def) + ')') }
    $status = 'ok'
    if ($state -eq 'On') {
        $status = 'attention'
        $summary = 'On (blocks unknown, unsigned programs)'
        Add-Attention 'BLOCKS' 'smart_app_control' 'Smart App Control is On: it blocks unsigned programs and DLLs, so NG Runner must ship signed, or Jim turns it off with written, informed consent (turning it off may be one-way). Decision 13.'
    } elseif ($state -eq 'Evaluation') {
        $status = 'attention'
        $summary = 'Evaluation (it may switch itself On)'
        Add-Attention 'DECIDE' 'smart_app_control' 'Smart App Control is in Evaluation and may switch itself On: treat it as On. NG Runner ships signed, or Jim turns it off with written, informed consent. Decision 13.'
    } elseif ($state -eq 'Off') {
        $summary = 'Off (signing can wait, decision 13)'
    } else {
        $status = 'could_not_check'
        $summary = 'could not check (the state was not readable)'
        Add-Attention 'CHECK' 'smart_app_control' 'The Smart App Control state could not be read: look in Windows Security > App & browser control > Smart App Control.'
    }
    Add-Result -Id 'smart_app_control' -Section 'Security and management' -Title 'Smart App Control' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ state = $state; registry = $reg; defender = $defState })
}

# ----- check: App Control policies and AppLocker

Invoke-Check 'app_control' 'Security and management' 'App Control and AppLocker' {
    $details = @()
    $dg = Get-ProbeRecord $PSystem 'deviceguard'
    $umci = $null
    if (Test-RecordOk $dg) {
        $umci = $dg['data']['umci'] -as [int]
        $umciText = 'unknown'
        if ($umci -eq 0) { $umciText = 'off' } elseif ($umci -eq 1) { $umciText = 'audit only' } elseif ($umci -eq 2) { $umciText = 'enforced' }
        $details += ('App Control for programs (user-mode code integrity): ' + $umciText)
    } else { $details += ('App Control status: could not check (' + (Get-RecordProblem $PSystem $dg) + ')') }
    $ci = Get-ProbeRecord $PCommands 'citool'
    $policies = @()
    if ((Test-RecordOk $ci) -and $ci['exit'] -eq 0) {
        $name = ''
        foreach ($l in @($ci['lines'])) {
            $s = ('' + $l).Trim()
            if ($s -match '^Friendly Name:\s*(.*)$') { $name = $Matches[1].Trim() }
            elseif ($s -match '^Is Currently Enforced:\s*(\w+)' -and $name) { $policies += ($name + $(if ($Matches[1] -eq 'true') { ' (enforced)' } else { ' (not enforced)' })); $name = '' }
        }
        $details += ('App Control policies (citool -lp): ' + $(if ($policies.Count -gt 0) { $policies -join '; ' } else { 'none listed' }))
    } elseif ($ci -and $ci['ok']) {
        $code = Format-HexCode $ci['exit']
        $why = 'exit ' + $code
        if ($code -eq '0x80070005') { $why = 'needs admin rights' }
        $details += ('App Control policy list (citool -lp): could not check (' + $why + ')')
    } else { $details += ('App Control policy list (citool -lp): could not check (' + (Get-RecordProblem $PCommands $ci) + ')') }
    $locker = @()
    foreach ($c in @('Exe', 'Msi', 'Script', 'Dll', 'Appx')) {
        $k = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\SrpV2\' + $c
        if (Test-Path -LiteralPath $k) {
            $mode = Get-RegValue $k 'EnforcementMode'
            $rules = @(Get-ChildItem -LiteralPath $k -ErrorAction SilentlyContinue).Count
            if ($rules -gt 0) { $locker += ($c + ': ' + $rules + ' rule(s), EnforcementMode ' + $mode) }
        }
    }
    $details += ('AppLocker: ' + $(if ($locker.Count -gt 0) { $locker -join '; ' } else { 'no rules' }))
    $status = 'ok'
    $summary = 'No App Control or AppLocker rules restrict programs'
    $sac = '' + $script:Facts['sac']
    if ($umci -eq 2 -and $sac -ne 'On') {
        $status = 'attention'
        $summary = 'A company App Control policy is enforced on programs'
        Add-Attention 'DECIDE' 'app_control' 'A company App Control policy is enforced on programs (not Smart App Control): NG Runner needs an exception from whoever manages the policy, or signing that the policy trusts.'
    } elseif ($umci -eq 2) {
        $summary = 'App Control is enforced (by Smart App Control)'
    } elseif ($umci -eq 1) {
        $summary = 'App Control runs in audit mode only'
    } elseif ($null -eq $umci) {
        $summary = 'App Control state not readable'
    }
    if ($locker.Count -gt 0) {
        $status = 'attention'
        $summary += '; AppLocker has rules'
        Add-Attention 'DECIDE' 'app_control' ('AppLocker rules limit which programs can run (' + ($locker -join '; ') + '): NG Runner needs an allow rule.')
    }
    Add-Result -Id 'app_control' -Section 'Security and management' -Title 'App Control and AppLocker' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ user_mode_code_integrity = $umci; policies = $policies; applocker = $locker })
}

# ----- check: antivirus

Invoke-Check 'antivirus' 'Security and management' 'Antivirus' {
    $av = Get-ProbeRecord $PSystem 'av_products'
    $def = Get-ProbeRecord $PSystem 'defender_status'
    if (-not (Test-RecordOk $av) -and -not (Test-RecordOk $def)) { throw (Get-RecordProblem $PSystem $av) }
    $products = @()
    $details = @()
    $activeThird = @()
    $anyOn = $false
    if (Test-RecordOk $av) {
        foreach ($p in @($av['data'] | Where-Object { $_ })) {
            $st = [int64]$p['state']
            $on = (($st -band 0x1000) -ne 0)
            $old = (($st -band 0x10) -ne 0)
            $isMs = (('' + $p['name']) -match 'Windows Defender|Microsoft Defender')
            if ($on) { $anyOn = $true; if (-not $isMs) { $activeThird += ('' + $p['name']) } }
            $products += ('' + $p['name'] + ': ' + $(if ($on) { 'on' } else { 'off' }) + ', ' + $(if ($old) { 'out of date' } else { 'up to date' }))
            $details += ('Security Center: ' + $p['name'] + ', productState ' + (Format-HexCode $st) + ' (decoded: ' + $(if ($on) { 'on' } else { 'off' }) + ', definitions ' + $(if ($old) { 'out of date' } else { 'up to date' }) + ')')
        }
    } else { $details += ('Security Center list: could not check (' + (Get-RecordProblem $PSystem $av) + ')') }
    if (Test-RecordOk $def) {
        $d = $def['data']
        $details += ('Microsoft Defender: mode ' + $d['mode'] + ', real-time protection ' + $d['rtp'] + ', tamper protection ' + $d['tamper'] + ', definitions updated ' + (Format-When $d['sigUpdated']) + ', version ' + $d['version'])
    }
    $status = 'ok'
    if ($products.Count -gt 0) { $summary = ($products -join '; ') } else { $summary = 'No antivirus is registered with Windows Security Center' }
    if (-not $anyOn) { $status = 'info'; $summary += ' (no antivirus reports itself as on)' }
    foreach ($n in $activeThird) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'antivirus' ($n + ' is the active antivirus: check how it treats a new, unsigned program; NG Runner may need an exclusion there, or signing.')
    }
    Add-Result -Id 'antivirus' -Section 'Security and management' -Title 'Antivirus' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ products = $products; third_party_active = $activeThird })
}

# ----- check: Defender attack surface reduction rules and Controlled Folder Access

Invoke-Check 'defender_rules' 'Security and management' 'Defender rules (ASR, Controlled Folder Access)' {
    $pref = Get-ProbeRecord $PSystem 'defender_prefs'
    $rules = @{}
    $source = ''
    if (Test-RecordOk $pref) {
        $ids = @($pref['data']['asrIds'] | Where-Object { $_ })
        $acts = @($pref['data']['asrActions'])
        for ($i = 0; $i -lt $ids.Count; $i++) { $a = $null; if ($i -lt $acts.Count) { $a = $acts[$i] }; $rules[('' + $ids[$i]).ToLower()] = $a }
        $source = 'Defender settings'
    }
    $gpo = Get-RegProps 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\Windows Defender Exploit Guard\ASR\Rules'
    if ($gpo) { foreach ($k in @($gpo.Keys)) { if (-not $rules.ContainsKey($k.ToLower())) { $rules[$k.ToLower()] = $gpo[$k] } }; if ($source) { $source += ' and policy' } else { $source = 'policy' } }
    $cfa = $null
    if (Test-RecordOk $pref) { $cfa = $pref['data']['cfa'] }
    $cfaPol = Get-RegValue 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access' 'EnableControlledFolderAccess'
    if ($null -ne $cfaPol) { $cfa = $cfaPol }
    if (-not (Test-RecordOk $pref) -and $null -eq $gpo -and $null -eq $cfaPol) { throw (Get-RecordProblem $PSystem $pref) }
    $actionNames = @{ 0 = 'off'; 1 = 'Block'; 2 = 'Audit'; 5 = 'not configured'; 6 = 'Warn' }
    $details = @()
    $blocking = @()
    foreach ($id in @($rules.Keys)) {
        $a = $rules[$id] -as [int]
        $an = 'value ' + $rules[$id]
        if ($null -ne $a -and $actionNames.ContainsKey($a)) { $an = $actionNames[$a] }
        $label = $id
        if ($ReputationAsrRules.ContainsKey($id)) { $label = $ReputationAsrRules[$id] + ' (' + $id + ')' }
        $details += ('ASR rule ' + $label + ': ' + $an)
        if ($ReputationAsrRules.ContainsKey($id) -and ($a -eq 1 -or $a -eq 6)) { $blocking += ($ReputationAsrRules[$id] + ' (' + $an + ')') }
    }
    $cfaText = 'off'
    $cfaOn = $false
    if ($null -ne $cfa) {
        switch ([int]$cfa) { 0 { $cfaText = 'off' } 1 { $cfaText = 'on (block)'; $cfaOn = $true } 2 { $cfaText = 'audit only' } 3 { $cfaText = 'block disk changes only'; $cfaOn = $true } 4 { $cfaText = 'audit disk changes only' } default { $cfaText = 'value ' + $cfa } }
    } else { $cfaText = 'unknown' }
    $active = @($rules.Keys | Where-Object { $v = $rules[$_] -as [int]; $v -eq 1 -or $v -eq 6 }).Count
    $summary = 'Attack surface reduction: ' + $(if ($rules.Count -eq 0) { 'no rules set' } else { '' + $rules.Count + ' rule(s), ' + $active + ' blocking or warning' }) + '; Controlled Folder Access: ' + $cfaText
    if ($source) { $details += ('Read from: ' + $source) }
    $status = 'ok'
    foreach ($b in $blocking) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'defender_rules' ('Defender rule "' + $b + '" can stop a new, rarely seen program such as NG Runner: an ASR exclusion or signing is needed.')
    }
    if ($cfaOn) {
        $status = 'attention'
        Add-Attention 'DECIDE' 'defender_rules' 'Controlled Folder Access is on: NG Runner''s writes into protected folders can be blocked until it is an allowed app (needs admin).'
    }
    Add-Result -Id 'defender_rules' -Section 'Security and management' -Title 'Defender rules (ASR, Controlled Folder Access)' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ asr_rules = $rules.Count; asr_blocking_or_warning = $active; reputation_rules_blocking = $blocking; controlled_folder_access = $cfaText })
}

# ----- check: managed device (dsregcmd summary, MDM enrollment, policies)

Invoke-Check 'managed_device' 'Security and management' 'Managed device' {
    $ds = Get-ProbeRecord $PDsreg 'dsregcmd'
    $kv = @{}
    $dsOk = ((Test-RecordOk $ds) -and $ds['exit'] -eq 0)
    if ($dsOk) {
        foreach ($l in @($ds['lines'])) {
            if (('' + $l) -match '^\s*([A-Za-z][A-Za-z0-9 \-]*?)\s*:\s*(.*)$') { $k = $Matches[1].Trim(); if (-not $kv.ContainsKey($k)) { $kv[$k] = $Matches[2].Trim() } }
        }
    }
    $details = @()
    $managedBy = @()
    $data = [ordered]@{}
    if ($dsOk) {
        # Only these dsregcmd fields are kept: no device IDs, thumbprints, tenant IDs or accounts.
        foreach ($k in @('AzureAdJoined', 'EnterpriseJoined', 'DomainJoined', 'DomainName', 'TenantName', 'WorkplaceJoined', 'WorkplaceTenantName', 'IsUserAzureAD', 'AzureAdPrt', 'EnterprisePrt')) {
            if ($kv.ContainsKey($k) -and $kv[$k] -ne '') { $data[$k] = $kv[$k]; $details += ($k + ': ' + $kv[$k]) }
        }
        foreach ($k in @('MdmUrl', 'MdmComplianceUrl', 'WorkplaceMdmUrl')) {
            if ($kv.ContainsKey($k)) {
                $v = '' + $kv[$k]
                $shown = 'none'
                if ($v -match '^https?://([^/:]+)') { $shown = $Matches[1] } elseif ($v) { $shown = 'set' }
                $data[$k] = $shown
                $details += ($k + ': ' + $shown)
                if ($shown -ne 'none' -and $k -ne 'MdmComplianceUrl') { $managedBy += ('device management (' + $shown + ')') }
            }
        }
        if (('' + $kv['AzureAdJoined']) -eq 'YES') { $managedBy += ('joined to Microsoft Entra ID' + $(if ($kv['TenantName']) { ' (' + $kv['TenantName'] + ')' } else { '' })) }
        if (('' + $kv['EnterpriseJoined']) -eq 'YES') { $managedBy += 'enterprise-joined' }
        if (('' + $kv['DomainJoined']) -eq 'YES') { $managedBy += ('joined to the domain ' + $kv['DomainName']) }
    } else {
        $why = Get-RecordProblem $PDsreg $ds
        if (Test-RecordOk $ds) { $why = 'exit ' + $ds['exit'] }
        $details += ('dsregcmd /status: could not check (' + $why + ')')
    }
    $providers = @()
    foreach ($e in @(Get-ChildItem -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Enrollments' -ErrorAction SilentlyContinue)) {
        $prov = '' + (Get-RegValue $e.PSPath 'ProviderID')
        if ($prov -and @('Deploy Authority', 'Cloud Authority', 'Local Authority', 'WMI_Bridge_Server') -notcontains $prov) { $providers += $prov }
    }
    $providers = @($providers | Select-Object -Unique)
    $details += ('Management enrollments: ' + $(if ($providers.Count -gt 0) { $providers -join ', ' } else { 'none' }))
    foreach ($p in $providers) { $managedBy += ('enrolled with ' + $p) }
    $areas = @()
    foreach ($a in @(Get-ChildItem -LiteralPath 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device' -ErrorAction SilentlyContinue)) {
        $v = Get-RegProps $a.PSPath
        if ($v) { $n = @(@($v.Keys) | Where-Object { $_ -notlike '*_ProviderSet' -and $_ -notlike '*_WinningProvider' }).Count; if ($n -gt 0) { $areas += ('' + $a.PSChildName) } }
    }
    $details += ('Device policies delivered by management or provisioning: ' + $(if ($areas.Count -gt 0) { $areas -join ', ' } else { 'none' }))
    $odPol = @()
    foreach ($k in @('HKLM:\SOFTWARE\Policies\Microsoft\OneDrive', 'HKCU:\SOFTWARE\Policies\Microsoft\OneDrive')) {
        $v = Get-RegProps $k
        if ($v) { foreach ($n in @($v.Keys)) { $odPol += $n } }
    }
    $details += ('OneDrive policies: ' + $(if ($odPol.Count -gt 0) { ($odPol | Select-Object -Unique) -join ', ' } else { 'none' }))
    $cs = Get-ProbeRecord $PSystem 'computer'
    if ((Test-RecordOk $cs) -and $cs['data']['partOfDomain'] -and -not ($managedBy -match 'domain')) { $managedBy += ('joined to the domain ' + $cs['data']['domain']) }
    $data['enrollments'] = $providers
    $data['policy_areas'] = $areas
    $data['onedrive_policies'] = @($odPol | Select-Object -Unique)
    $managedBy = @($managedBy | Select-Object -Unique)
    $status = 'ok'
    if ($managedBy.Count -gt 0) {
        $status = 'attention'
        $summary = 'Managed: ' + ($managedBy -join '; ')
        Add-Attention 'DECIDE' 'managed_device' ('This PC is managed (' + ($managedBy -join '; ') + '): get written exceptions from its administrator before Phase 1a, because policy can override automatic sign-in, active hours, Smart App Control and program rules.')
    } else {
        $summary = 'Not joined to a domain or Microsoft Entra ID; no device management found'
        if (('' + $kv['WorkplaceJoined']) -eq 'YES') { $summary += '; a work account is registered' + $(if ($kv['WorkplaceTenantName']) { ' (' + $kv['WorkplaceTenantName'] + ')' } else { '' }) + ' without management' }
        if ($areas.Count -gt 0) { $status = 'info' }
        if (-not $dsOk) { $status = 'info'; $summary += ' (dsregcmd could not run, so this is partial)' }
    }
    Add-Result -Id 'managed_device' -Section 'Security and management' -Title 'Managed device' -Status $status -Summary $summary -Details $details -Data $data
}

# ----- check: Mozaik Shared (pinning and online-only files, risk F3)

Invoke-Check 'mozaik_shared' 'OneDrive and Mozaik Shared' 'Mozaik Shared' {
    $root = '' + $script:Facts['mozaikRoot']
    if (-not $root) {
        # With no OneDrive account at all, the OneDrive check has already said so.
        if (($script:Facts['oneDriveAccounts'] -as [int]) -gt 0) {
            Add-Attention 'BLOCKS' 'mozaik_shared' 'The Mozaik Shared folder was not found in any OneDrive folder on this PC: NG Runner and Mozaik both need it here.'
            Add-Result -Id 'mozaik_shared' -Section 'OneDrive and Mozaik Shared' -Title 'Mozaik Shared' -Status 'attention' -Summary 'Not found in any OneDrive folder for this Windows user'
        } else {
            Add-Result -Id 'mozaik_shared' -Section 'OneDrive and Mozaik Shared' -Title 'Mozaik Shared' -Status 'could_not_check' -Summary 'could not check (no OneDrive account is set up)'
        }
        return
    }
    $rec = Get-ProbeRecord $PScan 'scan'
    if (-not (Test-RecordOk $rec)) {
        $why = Get-RecordProblem $PScan $rec
        Add-Attention 'CHECK' 'mozaik_shared' ('Mozaik Shared was found but could not be scanned (' + $why + '): check its files by hand.')
        throw $why
    }
    $s = $rec
    $acct = '' + $script:Facts['mozaikAccount']
    $pinnedRoot = ((($s['rootAttr'] -as [int64]) -band 0x80000) -ne 0)
    $summary = 'In ' + (Split-Path -Leaf (Split-Path -Parent $root)) + ': ' + ('{0:N0}' -f $s['files']) + ' files (' + (Format-GB $s['bytes']) + '); ' + ('{0:N0}' -f $s['cloudOnly']) + ' online-only, ' + ('{0:N0}' -f $s['local']) + ' on this PC, ' + ('{0:N0}' -f $s['pinned']) + ' always kept'
    if ($pinnedRoot) { $summary += '; folder pinned' } else { $summary += '; folder not pinned' }
    $details = @('Folder: ' + $root + ' (OneDrive account "' + $acct + '", attributes ' + (Format-HexCode $s['rootAttr']) + ')')
    $topData = [ordered]@{}
    $tops = $s['tops']
    $topNames = @()
    foreach ($n in $MozaikTopNames) { if ($tops.ContainsKey($n)) { $topNames += $n } }
    foreach ($n in @($tops.Keys | Sort-Object)) { if ($topNames -notcontains $n) { $topNames += $n } }
    foreach ($n in $topNames) {
        $b = $tops[$n]
        $details += ($n + ': ' + ('{0:N0}' -f $b['files']) + ' files (' + (Format-GB $b['bytes']) + '), ' + ('{0:N0}' -f $b['cloudOnly']) + ' online-only (' + (Format-GB $b['cloudBytes']) + '), ' + ('{0:N0}' -f $b['pinned']) + ' always kept')
        $topData[$n] = [ordered]@{ files = $b['files']; online_only = $b['cloudOnly']; on_this_pc = $b['local']; always_kept = $b['pinned']; gb = (Get-GB $b['bytes']); online_only_gb = (Get-GB $b['cloudBytes']) }
    }
    foreach ($pair in @(@('Jobs', 'jobsPresent', 'jobsAttr'), @('_NG_Automation', 'ngAutoPresent', 'ngAutoAttr'))) {
        if ($s[$pair[1]]) {
            $pin = ((($s[$pair[2]] -as [int64]) -band 0x80000) -ne 0)
            $details += ($pair[0] + ' folder: ' + $(if ($pin) { 'pinned ("Always keep on this device")' } else { 'not pinned' }) + ', attributes ' + (Format-HexCode $s[$pair[2]]))
        } else { $details += ($pair[0] + ' folder: missing') }
    }
    $details += ('Job folders in Jobs: ' + $s['jobFolders'] + '; MozaikJob.LCK files: ' + $s['lck'] + '; MozaikJob.UNS files: ' + $s['uns'] + '; OptimizeJob.LCK files: ' + $s['optLck'])
    $ageDays = $null
    if ($s['newestJobs']) {
        $ageDays = [int]((Get-Date) - [datetime]$s['newestJobs']).TotalDays
        $details += ('Newest file in Jobs: ' + (Format-When $s['newestJobs']) + ' (' + $ageDays + ' days ago)')
    }
    $samples = @()
    foreach ($x in @($s['samples'] | Where-Object { $_ })) {
        $details += ('Sample: ' + $x['path'] + ' | ' + $x['state'] + ' | attributes ' + $x['attr'] + ' | ' + $x['kb'] + ' KB | ' + (Format-When $x['modified']))
        $samples += , ([ordered]@{ path = $x['path']; state = $x['state']; attributes = $x['attr']; kb = $x['kb']; modified = (Format-When $x['modified']) })
    }
    $details += ('Scan: ' + ('{0:N0}' -f $s['folders']) + ' folders in ' + ('{0:N1}' -f ($s['ms'] / 1000)) + ' s' + $(if ($s['partial']) { ', STOPPED EARLY (time limit), counts are partial' } else { '' }) + $(if ($s['listErrors'] -gt 0) { ', ' + $s['listErrors'] + ' folder(s) could not be listed' } else { '' }) + '. Names under Jobs are hidden; only attributes were read, nothing was downloaded.')
    $status = 'ok'
    if (-not $s['jobsPresent']) {
        $status = 'attention'
        Add-Attention 'BLOCKS' 'mozaik_shared' 'Mozaik Shared has no Jobs folder on this PC: confirm it is the folder Mozaik uses.'
    }
    if (-not $s['ngAutoPresent']) {
        $status = 'attention'
        Add-Attention 'CHECK' 'mozaik_shared' 'Mozaik Shared has no _NG_Automation folder (Jim''s toolkit): confirm where the toolkit lives on this PC.'
    }
    $needBytes = [int64]0
    $needFiles = 0
    $needParts = @()
    foreach ($n in @('Jobs', '_NG_Automation')) {
        if ($tops.ContainsKey($n) -and [int]$tops[$n]['cloudOnly'] -gt 0) {
            $needBytes += [int64]$tops[$n]['cloudBytes']
            $needFiles += [int]$tops[$n]['cloudOnly']
            $needParts += ($n + ' (' + ('{0:N0}' -f $tops[$n]['cloudOnly']) + ')')
        }
    }
    if ($needFiles -gt 0) {
        $status = 'attention'
        Add-Attention 'SETUP' 'mozaik_shared' ('Files are online-only in ' + ($needParts -join ' and ') + ', ' + (Format-GB $needBytes) + ' in all: the unattended session pins Jobs and _NG_Automation ("Always keep on this device"), so NG Runner never reads a placeholder.')
        $free = $script:Facts['cFree'] -as [int64]
        if ($null -ne $free -and ($needBytes + [int64]10 * 1073741824) -gt $free -and $root -like 'C:*') {
            Add-Attention 'DECIDE' 'mozaik_shared' ('Pinning Jobs and _NG_Automation needs about ' + (Format-GB $needBytes) + ', but only ' + (Format-GB $free) + ' is free on C:: free up space first.')
        }
    }
    if ($null -ne $ageDays -and $ageDays -gt $StaleJobsDays) {
        $status = 'attention'
        Add-Attention 'CHECK' 'mozaik_shared' ('The newest file in Mozaik Shared\Jobs is ' + $ageDays + ' days old: if jobs were saved since then, OneDrive is not syncing on this PC.')
    }
    if ($s['partial']) {
        $status = 'attention'
        Add-Attention 'CHECK' 'mozaik_shared' 'The Mozaik Shared scan stopped at its time limit, so its counts are partial: OneDrive may be slow or still downloading folder lists.'
    }
    $data = [ordered]@{ folder = (Hide-Profile $root); account = $acct; files = $s['files']; folders = $s['folders']; gb = (Get-GB $s['bytes']); online_only = $s['cloudOnly']; online_only_gb = (Get-GB $s['cloudBytes']); on_this_pc = $s['local']; always_kept = $s['pinned']; unpinned_flag = $s['unpinned']; root_pinned = $pinnedRoot; top_folders = $topData; job_folders = $s['jobFolders']; lck_files = $s['lck']; uns_files = $s['uns']; optimize_lck_files = $s['optLck']; newest_jobs_file = (Format-When $s['newestJobs']); partial = [bool]$s['partial']; scan_ms = $s['ms']; samples = $samples }
    Add-Result -Id 'mozaik_shared' -Section 'OneDrive and Mozaik Shared' -Title 'Mozaik Shared' -Status $status -Summary $summary -Details $details -Data $data
}

# ----- check: Google Drive for desktop and G:

Invoke-Check 'google_drive' 'Google Drive' 'Google Drive for desktop' {
    $procs = @(Get-Process -Name GoogleDriveFS -ErrorAction SilentlyContinue)
    $exe = ''
    foreach ($p in $procs) { if ($p.Path) { $exe = '' + $p.Path; break } }
    $ver = ''
    if ($exe) { try { $ver = '' + (Get-Item -LiteralPath $exe).VersionInfo.ProductVersion } catch { $ver = '' } }
    $entry = @($script:Programs | Where-Object { $_['name'] -eq 'Google Drive' }) | Select-Object -First 1
    if (-not $ver -and $entry) { $ver = '' + $entry['version'] }
    $installed = ($procs.Count -gt 0 -or $null -ne $entry -or (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'Google\Drive File Stream')))
    $acctDirs = 0
    if ($env:LOCALAPPDATA) {
        $dfs = Join-Path $env:LOCALAPPDATA 'Google\DriveFS'
        if (Test-Path -LiteralPath $dfs) { $acctDirs = @(Get-ChildItem -LiteralPath $dfs -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d{10,}$' }).Count }
    }
    $volRec = Get-ProbeRecord $PDrive 'gd_volumes'
    $vols = @()
    if (Test-RecordOk $volRec) { $vols = @($volRec['data'] | Where-Object { $_ }) }
    $details = @()
    if ($exe) { $details += ('Program: ' + $exe) }
    $details += ('Account folders kept by Drive for desktop: ' + $acctDirs)
    $volText = @()
    foreach ($v in $vols) {
        $volText += ($v['letter'] + ' (' + $v['fs'] + ', ' + (Format-GB $v['size']) + ', ' + (Format-GB $v['free']) + ' free)')
        $top = Get-ProbeRecord $PDrive ('gd_top:' + $v['letter'] + '\')
        if (Test-RecordOk $top) {
            $names = @($top['data']['names'])
            $knownTop = @('My Drive', 'Shared drives', 'Other computers', '.shortcut-targets-by-id')
            $present = @($knownTop | Where-Object { $names -contains $_ })
            $missing = @($knownTop | Where-Object { $names -notcontains $_ })
            $details += ($v['letter'] + ' top folders present: ' + $(if ($present.Count -gt 0) { $present -join ', ' } else { 'none' }) + '; not present: ' + $(if ($missing.Count -gt 0) { $missing -join ', ' } else { 'none' }))
        } elseif ($top) { $details += ($v['letter'] + ' top folders: could not list (' + (Get-RecordProblem $PDrive $top) + ')') }
    }
    $status = 'ok'
    if ($procs.Count -gt 0) { $summary = 'Google Drive for desktop ' + $ver + ' is running' } elseif ($installed) { $summary = 'Google Drive for desktop ' + $ver + ' is installed but not running' } else { $summary = 'Google Drive for desktop is not installed' }
    if ($vols.Count -gt 0) { $summary += '; drive ' + ($volText -join ', ') }
    elseif (Test-RecordOk $volRec) { $summary += '; no Google Drive drive letter found' }
    else { $summary += '; drive letters: could not check (' + (Get-RecordProblem $PDrive $volRec) + ')' }
    if (-not $installed) {
        $status = 'attention'
        Add-Attention 'BLOCKS' 'google_drive' 'Google Drive for desktop is not installed: NG Runner reads and files IC summaries through G:.'
    } elseif ($procs.Count -eq 0) {
        $status = 'attention'
        Add-Attention 'BLOCKS' 'google_drive' 'Google Drive for desktop is not running, so G: is not available: start it and leave it running.'
    } elseif ((Test-RecordOk $volRec) -and $vols.Count -eq 0) {
        $status = 'attention'
        Add-Attention 'BLOCKS' 'google_drive' 'Google Drive for desktop is running, but no Google Drive drive letter was found: check that the right Google account is signed in to it.'
    } elseif (-not (Test-RecordOk $volRec)) {
        $status = 'attention'
        Add-Attention 'CHECK' 'google_drive' ('The Google Drive drive letter could not be checked (' + (Get-RecordProblem $PDrive $volRec) + '): open G: in Explorer and confirm it answers.')
    }
    foreach ($v in $vols) { if ($v['letter'] -ne 'G:') { $details += ('Note: Google Drive is mounted as ' + $v['letter'] + ', not G:.') } }
    Add-Result -Id 'google_drive' -Section 'Google Drive' -Title 'Google Drive for desktop' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ running = ($procs.Count -gt 0); installed = $installed; version = $ver; volumes = $volText; account_folders = $acctDirs })
}

# ----- check: the CNC operations folder on G: (risk F7)

Invoke-Check 'cnc_folder' 'Google Drive' 'CNC operations folder' {
    $find = Get-ProbeRecord $PDrive 'cnc_find'
    if (-not (Test-RecordOk $find)) {
        $why = Get-RecordProblem $PDrive $find
        Add-Attention 'CHECK' 'cnc_folder' ('The CNC operations folder could not be checked (' + $why + '): G: may be slow or stuck; open it in Explorer and tell Sulaiman how long it takes.')
        throw $why
    }
    $f = $find['data']
    $path = '' + $f['found']
    $details = @('Places looked: ' + $f['tried'] + '; slowest answer: ' + ('{0:N1}' -f ($f['slowestMs'] / 1000)) + ' s')
    $status = 'ok'
    if (-not $path) {
        if ($f['volumes'] -eq 0 -and -not $f['extraGiven']) {
            # The Google Drive check has already raised the missing drive letter.
            $status = 'could_not_check'
            $summary = 'could not check (no Google Drive drive letter)'
        } else {
            $status = 'attention'
            $summary = 'Not found on Google Drive'
            Add-Attention 'BLOCKS' 'cnc_folder' ('The CNC operations folder was not found on Google Drive (looked in ' + $f['tried'] + ' places): tell Sulaiman where it is, or run the check again with -CncPath "<full path>".')
        }
        Add-Result -Id 'cnc_folder' -Section 'Google Drive' -Title 'CNC operations folder' -Status $status -Summary $summary -Details $details -Data ([ordered]@{ found = ''; places_looked = $f['tried'] })
        return
    }
    $summary = 'Reachable: ' + $path
    $det = Get-ProbeRecord $PDrive 'cnc_detail'
    $data = [ordered]@{ found = $path; places_looked = $f['tried'] }
    if (Test-RecordOk $det) {
        $d = $det['data']
        $details += ('Listing it took ' + ('{0:N1}' -f ($d['listMs'] / 1000)) + ' s (' + $d['entries'] + ' items)')
        if ($d['hasNgJobs']) {
            $summary += '; NG Jobs: ' + $d['ngJobFolders'] + ' job folders (named with a job date) and ' + $d['ngOtherFolders'] + ' other folders'
            $details += ('Listing NG Jobs took ' + ('{0:N1}' -f ($d['ngJobsListMs'] / 1000)) + ' s')
        } else {
            $summary += '; no NG Jobs folder'
            $status = 'attention'
            Add-Attention 'CHECK' 'cnc_folder' 'There is no NG Jobs folder inside CNC operations: confirm where the job folders live.'
        }
        $details += ('NG Conversion program Claude folder: ' + $(if ($d['hasNgConversion']) { 'present' } else { 'missing' }))
        if (($d['listMs'] -as [int]) -gt 15000 -or ($d['ngJobsListMs'] -as [int]) -gt 15000) {
            $status = 'attention'
            Add-Attention 'CHECK' 'cnc_folder' 'G: is slow here (a folder listing took over 15 s): check that the CNC operations folders are set to "Available offline".'
        }
        $data['items'] = $d['entries']; $data['list_s'] = ($d['listMs'] / 1000); $data['ng_jobs'] = $d['hasNgJobs']; $data['ng_jobs_job_folders'] = $d['ngJobFolders']; $data['ng_jobs_other_folders'] = $d['ngOtherFolders']; $data['ng_conversion_folder'] = $d['hasNgConversion']
    } else {
        $details += ('Contents: could not check (' + (Get-RecordProblem $PDrive $det) + ')')
        $status = 'attention'
        Add-Attention 'CHECK' 'cnc_folder' ('The CNC operations folder was found but could not be listed (' + (Get-RecordProblem $PDrive $det) + '): open it in Explorer and confirm NG Jobs is there.')
    }
    Add-Result -Id 'cnc_folder' -Section 'Google Drive' -Title 'CNC operations folder' -Status $status -Summary $summary -Details $details -Data $data
}

# ----- check: Python

Invoke-Check 'python' 'Tools' 'Python' {
    $details = @()
    $list = Get-ProbeRecord $PCommands 'py_list'
    $versions = @()
    if (Test-RecordOk $list) {
        foreach ($l in @($list['lines'])) { $t = ('' + $l).Trim(); if ($t) { $details += ('py --list: ' + $t); if ($t -match 'Python\s+([\d.]+[^\s]*)(\s+\(Store\))?') { $versions += ($Matches[1] + $(if ($Matches[2]) { ' (Store)' } else { '' })) } } }
    } elseif ($pyCmd) { $details += ('py --list: could not check (' + (Get-RecordProblem $PCommands $list) + ')') }
    foreach ($c in @(Get-Command -Name python, python3 -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $src = '' + $c.Source
        $details += ($c.Name + ': ' + $src + $(if ($src -like '*\Microsoft\WindowsApps\*') { ' (app alias; not run, because a Store alias can open the Microsoft Store)' } else { '' }))
    }
    foreach ($h in @('HKCU:\Software\Python', 'HKLM:\SOFTWARE\Python', 'HKLM:\SOFTWARE\WOW6432Node\Python')) {
        foreach ($co in @(Get-ChildItem -LiteralPath $h -ErrorAction SilentlyContinue)) {
            foreach ($tag in @(Get-ChildItem -LiteralPath $co.PSPath -ErrorAction SilentlyContinue)) {
                $v = '' + (Get-RegValue $tag.PSPath 'Version')
                $exe = '' + (Get-RegValue ($tag.PSPath + '\InstallPath') 'ExecutablePath')
                $details += ('Registered: ' + $co.PSChildName + ' ' + $tag.PSChildName + $(if ($v) { ' (' + $v + ')' } else { '' }) + $(if ($exe) { ', ' + $exe } else { '' }))
                if ($versions.Count -eq 0 -and $v) { $versions += $v }
            }
        }
    }
    $appx = Get-ProbeRecord $PSystem 'appx'
    if (Test-RecordOk $appx) { foreach ($p in @($appx['data'] | Where-Object { $_ })) { if (('' + $p['name']) -match 'Python') { $details += ('Store package: ' + $p['name'] + ' ' + $p['version']) } } }
    if ($versions.Count -gt 0) { $summary = 'Installed: ' + (($versions | Select-Object -Unique) -join ', ') } else { $summary = 'No Python found (NG Runner brings its own)' }
    Add-Result -Id 'python' -Section 'Tools' -Title 'Python' -Status 'info' -Summary $summary -Details $details -Data ([ordered]@{ versions = @($versions | Select-Object -Unique); py_launcher = [bool]$pyCmd })
}

# ----- check: Claude Code and the Claude app

Invoke-Check 'claude_code' 'Tools' 'Claude Code' {
    $details = @()
    $ver = ''
    $path = ''
    foreach ($c in @(Get-Command -Name claude -All -ErrorAction SilentlyContinue)) {
        $src = '' + $c.Source
        if (-not $path) { $path = $src }
        $details += ('claude: ' + $src + ' (' + $c.CommandType + ')')
        if (-not $ver -and $src -match '\.exe$') { try { $ver = ('' + (Get-Item -LiteralPath $src).VersionInfo.ProductVersion) -replace '\.0$', '' } catch { $ver = '' } }
    }
    if (-not $ver -and $env:APPDATA) {
        $pkg = Join-Path $env:APPDATA 'npm\node_modules\@anthropic-ai\claude-code\package.json'
        if (Test-Path -LiteralPath $pkg) { try { $ver = '' + (Get-Content -LiteralPath $pkg -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { $ver = '' } }
    }
    if ($env:USERPROFILE) {
        $vd = Join-Path $env:USERPROFILE '.local\share\claude\versions'
        if (Test-Path -LiteralPath $vd) { $details += ('Installed versions: ' + ((@(Get-ChildItem -LiteralPath $vd -ErrorAction SilentlyContinue) | ForEach-Object { $_.Name }) -join ', ')) }
        $details += ('Claude Code settings folder (%USERPROFILE%\.claude): ' + $(if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.claude')) { 'present' } else { 'absent' }))
    }
    $desktop = ''
    $appx = Get-ProbeRecord $PSystem 'appx'
    if (Test-RecordOk $appx) { foreach ($p in @($appx['data'] | Where-Object { $_ })) { if (('' + $p['name']) -match 'Claude') { $desktop = '' + $p['version']; $details += ('Claude desktop app (Store package ' + $p['name'] + '): ' + $p['version']) } } }
    if (-not $desktop -and $env:LOCALAPPDATA -and (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'AnthropicClaude'))) { $desktop = 'installed'; $details += 'Claude desktop app: installed (per-user)' }
    if ($path) { $summary = 'Claude Code ' + $(if ($ver) { $ver } else { '(version unknown)' }) + ' at ' + $path } else { $summary = 'Claude Code is not installed' }
    if ($desktop) { $summary += '; Claude desktop app ' + $desktop } else { $summary += '; no Claude desktop app found' }
    Add-Result -Id 'claude_code' -Section 'Tools' -Title 'Claude Code' -Status 'info' -Summary $summary -Details $details -Data ([ordered]@{ claude_code = $ver; path = (Hide-Profile $path); desktop_app = $desktop })
}

# ----- check: git

Invoke-Check 'git' 'Tools' 'git' {
    if (-not $gitCmd) {
        Add-Result -Id 'git' -Section 'Tools' -Title 'git' -Status 'info' -Summary 'git is not installed'
        return
    }
    $rec = Get-ProbeRecord $PCommands 'git_version'
    $ver = ''
    if ((Test-RecordOk $rec) -and @($rec['lines']).Count -gt 0) { $ver = ('' + @($rec['lines'])[0]).Trim() -replace '^git version\s+', '' }
    $summary = 'git ' + $(if ($ver) { $ver } else { '(version unknown)' }) + ' at ' + $gitCmd.Source
    Add-Result -Id 'git' -Section 'Tools' -Title 'git' -Status 'info' -Summary $summary -Data ([ordered]@{ version = $ver; path = (Hide-Profile ('' + $gitCmd.Source)) })
}

# ----- report

function Protect-Text {
    # Last line of defence: no email address or API-key-shaped string leaves this PC.
    param([string]$Text)
    $t = $Text -replace '[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', '[email hidden]'
    $t = $t -replace 'sk-(ant|or|proj)-[A-Za-z0-9_\-]{8,}', '[key hidden]'
    $t = $t -replace '\bsk-[A-Za-z0-9]{20,}', '[key hidden]'
    return $t
}

function ConvertTo-AsciiJson {
    # ConvertTo-Json in PowerShell 5.1 keeps non-ASCII characters as they are; escaping them
    # keeps the file plain ASCII (valid UTF-8, no byte order mark) whatever the system code page.
    param([string]$Json)
    if ($Json -notmatch '[^\x00-\x7F]') { return $Json }
    $seen = @{}
    foreach ($m in [regex]::Matches($Json, '[^\x00-\x7F]')) {
        $ch = '' + $m.Value
        if (-not $seen.ContainsKey($ch)) { $seen[$ch] = ('\u{0:x4}' -f [int][char]$ch) }
    }
    foreach ($k in @($seen.Keys)) { $Json = $Json.Replace($k, $seen[$k]) }
    return $Json
}

function Get-StatusLabel {
    param([string]$Status)
    if ($Status -eq 'ok') { return '[OK]' }
    if ($Status -eq 'info') { return '[NOTE]' }
    if ($Status -eq 'attention') { return '[ATTENTION]' }
    if ($Status -eq 'could_not_check') { return '[COULD NOT CHECK]' }
    return ('[' + $Status + ']')
}

function Resolve-OutputFolder {
    param([string]$Requested)
    $cands = @()
    if ($Requested) {
        $r = $Requested
        if ($r -notmatch '^[A-Za-z]:\\' -and $r -notmatch '^\\\\') { $r = Join-Path (Get-Location).Path $r }
        $cands += $r
    }
    $desk = ''
    try { $desk = [Environment]::GetFolderPath('Desktop') } catch { $desk = '' }
    if (-not $desk) { $desk = '' + (Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' 'Desktop') }
    if (-not $desk -and $env:USERPROFILE) { $desk = Join-Path $env:USERPROFILE 'Desktop' }
    if ($desk) { $cands += $desk }
    if ($env:TEMP) { $cands += $env:TEMP }
    foreach ($c in $cands) {
        try {
            if (-not (Test-Path -LiteralPath $c -PathType Container)) { $null = New-Item -ItemType Directory -Path $c -Force -ErrorAction Stop }
            if (Test-Path -LiteralPath $c -PathType Container) { return $c }
        } catch {
            $script:OutputNotes += ('Could not use the folder ' + $c + ' (' + (Get-ShortError $_) + '), so the report went to the next choice.')
        }
    }
    return ''
}

function Test-StartedFromExplorer {
    try {
        $me = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $PID) -OperationTimeoutSec 10 -ErrorAction Stop
        $parent = Get-Process -Id ([int]$me.ParentProcessId) -ErrorAction Stop
        return (('' + $parent.ProcessName) -eq 'explorer')
    } catch { return $false }
}

function Write-Closing {
    # The window's last lines, ending with one instruction that fits what happened. When the
    # report could not be saved it is shown here instead, the list of what needs attention comes
    # after it, where a photo of the window's last screen still catches it, and the instruction
    # is to take that photo. Returns the exit code: 0 saved; 3 not saved, which tells
    # setup_check.cmd that this window has already said what to do.
    param([bool]$Written, [string]$TxtPath, [string]$JsonPath, [string]$Text, $Attention, $Notes, [double]$Seconds)
    Write-Host ''
    Write-Host ('Done in {0:N0} s.' -f $Seconds)
    foreach ($note in @($Notes)) { Write-Host ('Note: ' + $note) }
    if ($Written) {
        Write-Host 'Report saved:'
        Write-Host ('  ' + $TxtPath)
        Write-Host ('  ' + $JsonPath)
    } else {
        Write-Host 'The report could not be saved. Here it is instead:'
        Write-Host $Text
    }
    Write-Host ''
    if (@($Attention).Count -eq 0) { Write-Host 'Needs attention: nothing.' }
    else {
        Write-Host ('Needs attention: ' + @($Attention).Count + ' item(s), listed at the top of the report.')
        $n = 0
        foreach ($a in @($Attention)) { $n++; Write-Host ('  ' + $n + '. [' + $a['tag'] + '] ' + (Protect-Text $a['text'])) }
    }
    Write-Host ''
    if ($Written) {
        Write-Host 'Next: put both report files in the private Drive folder you share with Sulaiman.'
        return 0
    }
    Write-Host 'Next: take a photo of this window and put it in the private Drive folder you share with Sulaiman.'
    return 3
}

Write-Host 'Writing the report...'
Stop-LeftoverProbes

$Finished = Get-Date
$Seconds = ($Finished - $Started).TotalSeconds
$attention = @($script:Attention | Sort-Object -Property @{ Expression = { $_['rank'] } }, @{ Expression = { $_['seq'] } })
$ordered = @()
foreach ($sec in $SectionOrder) {
    foreach ($id in $ResultOrder) { foreach ($r in $script:Results) { if ($r['section'] -eq $sec -and $r['id'] -eq $id) { $ordered += , $r } } }
    foreach ($r in $script:Results) { if ($r['section'] -eq $sec -and $ResultOrder -notcontains $r['id']) { $ordered += , $r } }
}
foreach ($r in $script:Results) { if ($SectionOrder -notcontains $r['section']) { $ordered += , $r } }

$folder = Resolve-OutputFolder $OutDir
$stamp = $Started.ToString('yyyy-MM-dd-HHmm')
$computer = ('' + $env:COMPUTERNAME) -replace '[^A-Za-z0-9._-]', '_'
if (-not $computer) { $computer = 'PC' }
$base = 'setup-check-' + $computer + '-' + $stamp
$txtPath = ''
$jsonPath = ''
if ($folder) { $txtPath = Join-Path $folder ($base + '.txt'); $jsonPath = Join-Path $folder ($base + '.json') }

$W = 100
$lines = @()
$lines += ('NG Runner Setup Check, version ' + $ScriptVersion)
$lines += ('Computer: ' + $env:COMPUTERNAME + '    Windows user: ' + $env:USERNAME + '    Started: ' + $Started.ToString('yyyy-MM-dd HH:mm') + ' (UTC' + $Started.ToString('zzz') + ')    Took: ' + ('{0:N0}' -f $Seconds) + ' s')
$lines += 'Read-only: this check changed nothing on this PC. It wrote only this report and its JSON copy.'
foreach ($note in @($script:OutputNotes)) { $lines += Format-Wrapped ('Note: ' + $note) $W '' '  ' }
$lines += ''
$lines += ('NEEDS ATTENTION (' + $attention.Count + ')')
if ($attention.Count -eq 0) {
    $lines += '  Nothing needs attention.'
} else {
    $lines += Format-Wrapped 'BLOCKS = stops NG Runner until fixed. DECIDE = needs a decision. SETUP = to change in the unattended setup session. CHECK = could not be checked here; confirm by hand.' $W '  ' '  '
    $lines += ''
    $n = 0
    foreach ($a in $attention) {
        $n++
        $lead = ('{0,3}. ' -f $n)
        $lines += Format-Wrapped ('[' + $a['tag'] + '] ' + $a['text']) $W $lead ('      ')
    }
}
$lines += ''
$lines += 'ALL RESULTS'
$lines += Format-Wrapped '[OK] fine as it is. [NOTE] for information. [ATTENTION] see the list above. [COULD NOT CHECK] with the reason.' $W '  ' '  '
foreach ($sec in $SectionOrder) {
    $inSec = @($ordered | Where-Object { $_['section'] -eq $sec })
    if ($inSec.Count -eq 0) { continue }
    $lines += ''
    $lines += $sec
    foreach ($r in $inSec) {
        $label = (Get-StatusLabel $r['status']).PadRight(18)
        $lines += Format-Wrapped ($r['title'] + ': ' + $r['summary']) $W ('  ' + $label) (' ' * 20)
        foreach ($d in @($r['details'])) { $lines += Format-Wrapped $d $W ((' ' * 22) + '- ') (' ' * 24) }
    }
}
$lines += ''
$lines += 'NOT CHECKED HERE (ask Jim or check by hand)'
foreach ($nc in $NotChecked) { $lines += Format-Wrapped $nc $W '  - ' '    ' }
$lines += ''
$lines += ('More detail (raw values, timings) is in the JSON copy: ' + $base + '.json')
$text = Protect-Text (($lines | ForEach-Object { Hide-Profile ('' + $_) }) -join "`r`n")

$attentionOut = @()
foreach ($a in $attention) { $attentionOut += , ([ordered]@{ tag = $a['tag']; check = $a['check']; text = $a['text'] }) }
$doc = [ordered]@{
    schema           = 'ngrunner.setup-check/1'
    script_version   = $ScriptVersion
    computer         = '' + $env:COMPUTERNAME
    user             = '' + $env:USERNAME
    started          = $Started.ToString('yyyy-MM-ddTHH:mm:sszzz')
    finished         = $Finished.ToString('yyyy-MM-ddTHH:mm:sszzz')
    duration_seconds = ([double]([int64]($Seconds * 10)) / 10)
    read_only        = $true
    language_mode    = '' + $ExecutionContext.SessionState.LanguageMode
    background_jobs  = $(if ($NoJobs) { 'off (-NoJobs)' } elseif ($script:JobsUnavailable) { 'unavailable: ' + $script:JobsUnavailable } else { 'on' })
    output_notes     = @($script:OutputNotes)
    needs_attention  = $attentionOut
    results          = $ordered
    not_checked      = $NotChecked
    timings_ms       = $script:Timings
}
$json = ''
try { $json = Protect-Text (ConvertTo-AsciiJson (Hide-ProfileJson ($doc | ConvertTo-Json -Depth 12))) }
catch { $json = '{ "schema": "ngrunner.setup-check/1", "error": "the JSON copy could not be built: ' + ((Get-ShortError $_) -replace '[\\"]', ' ') + '" }' }

$written = $false
if ($txtPath) {
    try {
        Out-File -LiteralPath $txtPath -InputObject $text -Encoding utf8 -Force -ErrorAction Stop
        Out-File -LiteralPath $jsonPath -InputObject $json -Encoding ascii -Force -ErrorAction Stop
        $written = $true
    } catch {
        Write-Host ('Could not write the report: ' + (Get-ShortError $_))
    }
}

$exitCode = Write-Closing -Written $written -TxtPath $txtPath -JsonPath $jsonPath -Text $text -Attention $attention -Notes @($script:OutputNotes) -Seconds $Seconds

if (-not $NoPause -and $Host.Name -eq 'ConsoleHost' -and (Test-StartedFromExplorer)) {
    $null = Read-Host 'Press Enter to close this window'
}
exit $exitCode
