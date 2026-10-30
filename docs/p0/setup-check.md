# Setup Check

A one-time check of Jim's PC. It tells Sulaiman whether NG Runner can run there overnight, and what has to be set up or decided first. It is step 2 of the install plan (the 20-minute call). It usually takes under a minute, and three minutes at most.

## It changes nothing

- It only reads settings. It installs nothing, changes no setting, and never opens Mozaik or any job file.
- It reads OneDrive file *attributes*, never their contents, so nothing is downloaded.
- It writes exactly two files: the report and a copy of it for Sulaiman's tools.
- It never records passwords or keys. For API keys it notes only "set" or "not set".
- It hides email addresses and the names of job folders.

## What it checks

| Area | What it looks at |
|---|---|
| This PC | Windows edition and version, hardware, PowerShell restrictions, time zone, free space on C: |
| Account | Admin rights, User Account Control, the API key variables (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_BASE_URL`) |
| Overnight | Sleep and Modern Standby, sleep and wake timers, power button and lid, Windows Update active hours and pending restarts, automatic sign-in after updates (ARSO), Chrome Remote Desktop, scheduled tasks already on the PC |
| Security | Smart App Control, antivirus, Defender rules (attack surface reduction, Controlled Folder Access), App Control and AppLocker, whether a company manages the PC, BitLocker |
| Windows features | Long paths, WebView2, notifications |
| OneDrive | Running or not, accounts and their sign-in state, the Mozaik Shared folder, how many of its files are online-only |
| Google Drive | Running or not, the G: drive, the CNC operations folder and NG Jobs |
| Mozaik | Where it is installed, its version, whether it is running now |
| Tools | Python, Claude Code, git |

Some things cannot be seen from the PC, such as the Google account type, whether George's PC opens the same jobs, and Do not disturb. The report lists these at the end so they can be asked on the call.

## How to run it

1. Sulaiman puts the two files, `setup_check.cmd` and `setup_check.ps1`, side by side on the Desktop, over Chrome Remote Desktop or through your private Drive folder.
2. Double-click `setup_check.cmd`. Run it as yourself, never with *Run as administrator*: the check is meant for Jim's normal account, because an elevated window sees G: and OneDrive differently. If Windows asks whether to run it, choose **Run** (on a blue "Windows protected your PC" screen: **More info**, then **Run anyway**).
3. A window shows the check's progress. At the end it says where the report is, lists what needs attention, and waits for a key before it closes.

`setup_check.cmd` starts `setup_check.ps1` from the same folder for this one run only. It does not change the PC's script setting.

**If double-clicking doesn't work**, start it from PowerShell instead:

1. Open the Start menu, type `PowerShell`, and open **Windows PowerShell** (again, not *Run as administrator*).
2. Paste this line and press Enter:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders').Desktop)\setup_check.ps1"
   ```

   It finds the Desktop wherever Windows keeps it, OneDrive included. Another way is to type everything up to `-File` and a space, drag the file into the window, and press Enter.

   `-ExecutionPolicy Bypass` allows this one run only. It does not change the PC's script setting.

For Sulaiman, `setup_check.cmd` and the PowerShell line take the same options, for example `setup_check.cmd -OutDir "<folder>" -NoPause` in a Command Prompt:

| Option | Use |
|---|---|
| `-OutDir "<folder>"` | Save the report somewhere other than the Desktop |
| `-CncPath "<full path>"` | Look for the CNC operations folder at this path too |
| `-NoJobs` | Run every check in the same window, without time limits (troubleshooting) |
| `-NoPause` | Close at the end without waiting for a key |

## Where the report goes

Two files appear on the Desktop, or in the `-OutDir` folder:

- `setup-check-<PC name>-<date>-<time>.txt` is the report to read.
- `setup-check-<PC name>-<date>-<time>.json` holds the same results for tools.

The report starts with **Needs attention**. Every item carries a tag:

| Tag | Meaning |
|---|---|
| BLOCKS | NG Runner cannot run until this is fixed |
| DECIDE | Needs a decision from Jim or Sulaiman |
| SETUP | A change planned for the unattended setup session (step 3) |
| CHECK | Could not be checked here; confirm by hand |

All the results follow, then the list of things the check cannot see. A check that fails or needs admin rights says *could not check (reason)*, and the rest still run.

## What to send back

Put both files in your private Drive folder for Sulaiman, the one from the access list (*What Sulaiman needs from your PC*). On the call, Sulaiman does this with you over Chrome Remote Desktop: drag the two files from the Desktop into that folder (in File Explorer it is under Google Drive (G:) > My Drive), or upload them at drive.google.com.

The report contains no passwords, keys, email addresses, job names or client names. It does show:

- the PC name and the Windows user name;
- the OneDrive account names (for example "ICWC");
- the installed programs it looked for;
- the names of the PC's scheduled tasks;
- where the CNC operations folder is on G:, which can include a Google Drive folder ID.

If no report appears, take a photo of the window and put that in the folder instead.
