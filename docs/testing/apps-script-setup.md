# Setting up NG Runner's Google side

For Sulaiman, once, before test window 3 ([jims-pc.md](jims-pc.md)). Allow about an hour. It installs nothing on Jim's PC and changes nothing in the NG Fab Schedule, which the script only reads.

When you finish, one Apps Script project, under the Google account that owns the Fab Schedule, exports the Fab Schedule, sends NG Runner's messages, watches its heartbeat and prunes old files, all through one private Drive folder. [google/README.md](../../google/README.md) explains how. This guide is only the clicks.

These steps have not been run on a live Google account yet, and Google moves its buttons now and then. If a screen differs, follow the words on it, then correct this guide.

**The placeholders:** `<Jim's address>`, `<Barbara's address>`, `<your address>`, `<control folder ID>`, `<spreadsheet ID>`, `<signing key>`. None of these real values ever goes in the repo.

**Before you start:**
- Open a private browser window (Chrome: Ctrl+Shift+N). Use it, signed in only as the script account (step 1), unless a step says otherwise. Triggers run as the account that made them, so a project made under your own account would send its mail as you.
- Have the password manager open, for the signing key.
- Have the repo checked out at the commit the test kit was built from, since you paste from its `google\` folder.

## 1. Check the script account

The project runs under the Google account that owns the NG Fab Schedule, called the script account below (`docs/decisions.md`, "Accounts"). There is no new account to make, and the script reads the sheet directly. What that costs: NG Runner's emails come from that address, shown as "NG Runner", and replies land in its inbox; the script's permissions cover that whole account, so only `google\`'s code keeps it read-only on the workbook; and Google's daily limits are shared with anything else that account runs.

In the private window, signed in as the script account:

1. Open the NG Fab Schedule and click **Share**. The account must be listed as **Owner**. If it isn't, stop: the rest of this guide assumes it.
2. Click the round picture at the top right, then **Manage your Google Account**, **Security**. Check that **2-Step Verification** is on, since this account now also runs NG Runner's Google side.
3. Go to `script.google.com` and click the alarm clock icon on the left (**Triggers**). If other projects already run under this account, note them: they share the 90 minutes of trigger time a free account gets each day.
4. Find out which account Google Drive is signed in with on Jim's PC (the Drive icon in his tray, in window 1). This decides step 2's sharing and whether pruning works; see the end of this guide.

## 2. Create the control folder

Signed in as the script account:

1. Go to `drive.google.com`. Click **+ New**, **New folder**, type `NG Runner control`, then **Create**. It stays in this account's My Drive, outside the shared CNC operations tree. Leave it empty: the script and NG Runner make their own folders in it.
2. Right-click the folder, then **Share**, **Share**. Type `<your address>`, and `<Jim's address>` only if Drive on Jim's PC uses another account. Leave the role at **Editor** and click **Send**. Jim's PC writes here through Drive for desktop; you read logs here and put pause files here.
3. Open **Share** again and check it. It must list only the script account (Owner), you and, if you added it, Jim's other account. **General access** must say **Restricted**. Click **Done**.
4. Double-click the folder. The address now reads `drive.google.com/drive/folders/<control folder ID>`. Copy the ID, stopping before any `?`, and keep it for step 6.

**Later, at the start of window 2, on Jim's PC.** If Drive there uses the script account, the folder is already in his My Drive, usually at `G:\My Drive\NG Runner control`. If it uses another account, a shared folder reaches his PC only once it is in his Drive: in his browser, signed in as that account, go to `drive.google.com`, then **Shared with me**, right-click `NG Runner control`, then **Organize**, **Add shortcut**, **My Drive**, **Add**. It usually sits at `G:\.shortcut-targets-by-id\<folder id>\NG Runner control`, as the CNC folder does on your PC. Either way, find it in File Explorer and note its full path, for the canary and `NGRunner.exe setup`. The Setup Check does not look for this folder.

## 3. Copy the NG Fab Schedule's ID

Open the NG Fab Schedule. The address reads `docs.google.com/spreadsheets/d/<spreadsheet ID>/edit`. Copy the ID, and keep it for step 6. It needs no sharing, since the script account owns it. Change nothing in the workbook.

## 4. Create the Apps Script project

In the private window, as the script account:

1. Go to `script.google.com` and click **New project**.
2. Click **Untitled project** at the top, type `NG Runner`, then **Rename**.
3. Click the gear icon on the left (**Project Settings**) and tick **Show "appsscript.json" manifest file in editor**.
4. Click the `< >` icon on the left (**Editor**).

## 5. Paste in the files

For each file below, open it from the repo's `google\` folder in Notepad and press Ctrl+A, Ctrl+C. In the editor, do what the table says, then click into the code and press Ctrl+A, Ctrl+V, Ctrl+S.

| File in `google\` | In the editor, first |
|---|---|
| `appsscript.json` | Click it in the Files list |
| `Config.gs` | The project starts with `Code.gs`. Click the three dots beside it, then **Rename**, type `Config`, press Enter |
| `Signing.gs`, `FabExport.gs`, `FabEdits.gs`, `Outbox.gs`, `Watchdog.gs`, `Prune.gs` | One at a time: click **+** beside Files, then **Script**, type the name without `.gs`, press Enter |

The Files list must now show eight files: `appsscript.json` and seven `.gs` files. `README.md` and `test\` stay out.

When `google\` changes later, paste the changed files the same way.

## 6. Set the Script Properties

**First, the signing key.** It seals every file in the control folder, so each side only trusts what the other signed. In PowerShell on your own PC, from the repo root:

```powershell
.\.venv\Scripts\python.exe -c "from ngrunner.signing import new_key; print(new_key())"
```

It prints `<signing key>`, 64 hex characters. Save it in the password manager straight away. It goes in `SIGNING_KEY` below, and `NGRunner.exe setup` puts the same key in Credential Manager on Jim's PC in window 2. Never paste it into a chat, an email, a ticket or a log.

`NGRunner.exe keygen` does not make this key. It prints the backup key pair, for NG Runner's settings and the password manager, and nothing from it goes in this project.

**Then the properties.** Click the gear icon, scroll to **Script Properties**, and click **Add script property** (**Edit script properties** once some exist). Type one name and value per row, click **Add script property** for the next row, and finish with **Save script properties**. Type names exactly as written, and values without quotes.

Roles are NG Runner's: `jim`, `barbara`, `will`, `george`, `sulaiman`, `backup_contact`, written in capitals in a property's name, so the backup contact's address is `ROLE_BACKUP_CONTACT_EMAIL`.

**Set all of these now:**

| Property | Value | What it means |
|---|---|---|
| `SIGNING_KEY` | `<signing key>` | The key above, the same as `signing_key` in Credential Manager on Jim's PC |
| `CONTROL_FOLDER_ID` | `<control folder ID>` | Which folder the script reads and writes |
| `SHEET_ID` | `<spreadsheet ID>` | Which workbook it exports. It reads Sheet1 |
| `SHADOW` | `true` | The shadow run: NG Runner sends everything to you. While it is set, a digest to `ALERT_ROLE` counts as the morning list, and "SHADOW No list today" goes to `ALERT_ROLE` and never to Jim. **Set it before step 7:** without it, Jim gets "No list today" by email on the first morning |
| `ALERT_ROLE` | `sulaiman` | Who the watchdog alerts. Keep it at `sulaiman` for the shadow run, since NG Runner's copies go there |
| `ALERT_NAME` | `Sulaiman` | The name Jim's message gives when it says who was alerted |
| `ROLE_SULAIMAN_EMAIL` | `<your address>` | Where your alerts and copies go |
| `ROLE_SULAIMAN_CHANNEL` | `email` | How the watchdog reaches you |
| `ROLE_JIM_EMAIL` | `<Jim's address>` | Where Jim's digest and the watchdog's messages to him go, once `SHADOW` is cleared |
| `ROLE_JIM_CHANNEL` | `email` | How the watchdog reaches Jim. Email, since P1a has no push channel |
| `ROLE_BARBARA_EMAIL` | `<Barbara's address>` | Where her emails go at go-live; none go during the shadow run. `checkSetup` does not ask for it, so don't skip it |
| `EXPECTED_ON_FROM` | `06:00` | When the PC is expected on, Pacific, as `HH:MM`. A silent heartbeat alerts you only inside these hours |
| `EXPECTED_ON_UNTIL` | `23:00` | The end of those hours. Equal times mean all day, and a range may run past midnight. Keep both the same as NG Runner's own settings |

**Set this only if it applies:**

| Property | Value | What it means |
|---|---|---|
| `PC_SLEEPS_AT_NIGHT` | `true` | Jim's PC is off at night (his answer in window 1): no 7:00 AM message, and his digest comes at first wake. Turn on `pc_sleeps_at_night` in `NGRunner.exe setup` too. Otherwise leave it unset, and if it isn't known yet, set it after window 1 |

**Leave these unset for now:**

| Property | When it is needed |
|---|---|
| `ROLE_<ROLE>_EMAIL` for `will`, `george` and `backup_contact` | When that role gets messages. Set `ROLE_BACKUP_CONTACT_EMAIL` before you are away, then point `ALERT_ROLE` and `ALERT_NAME` at the backup contact |
| `ROLE_<ROLE>_CHANNEL` for the other roles | Only to use something other than email, the default: `email`, `slack` or `pushover` |
| `ROLE_<ROLE>_PUSHOVER`, `PUSHOVER_TOKEN` | When Pushover is added (P1b): that role's Pushover user key, and the Pushover application token |
| `ROLE_<ROLE>_SLACK`, `SLACK_TOKEN` | If Slack is used: the Slack user or channel ID the bot posts to for that role, and the bot token, which needs `chat:write` |

Once the triggers run, the script adds properties of its own: `state.*`, `done.<id>` and `refused.<id>`. Leave them alone, except as the runbook says.

## 7. Run checkSetup, then installTriggers

1. Click the `< >` icon, then `Config.gs` in the Files list.
2. In the toolbar, open the function menu beside **Run** and **Debug**, pick `checkSetup`, and click **Run**.
3. The first time only, Google asks for permission. Click **Review permissions** and pick the script account. On "Google hasn't verified this app" click **Advanced**, then **Go to NG Runner (unsafe)**. If it shows tick boxes, click **Select all**. Then click **Allow** or **Continue**. Google warns because this is a private script, not a published app. The permissions are the ones in `appsscript.json`: Sheets, Drive, sending mail, reaching outside services, and running on triggers.
4. Read the **Execution log** that opens at the bottom (the table below). Fix each line and click **Run** again, until it says `setup: everything NG Runner needs is set`.
5. Pick `installTriggers` in the same menu and click **Run**. The log should say `triggers installed: exportFab, processOutbox, runWatchdog, pruneControlFolder, onFabEdit`. It refuses with `fix the setup first` while `checkSetup` finds a problem. It is safe to run again: it replaces its own triggers.
6. Click the alarm clock icon on the left (**Triggers**). It must list those five, none of them twice.

### Reading checkSetup's answers

`checkSetup` writes one **Warning** line per problem, each starting `NG Runner: setup:`, and never shows a value. One **Info** line, `NG Runner: setup: everything NG Runner needs is set`, means it found nothing wrong. A red **Error** line means the run itself stopped: a function "is not defined" means a `.gs` file is missing or was only half pasted.

| It says | What it means | What to do |
|---|---|---|
| `the project time zone is ..., not America/Los_Angeles` | `appsscript.json` was not pasted or saved | Paste it again and press Ctrl+S |
| `the Script Property SIGNING_KEY is not set to the 64-hex-character signing key` | It is missing, or not exactly 64 hex characters | Paste it again from the password manager |
| `the control folder could not be opened: ...` | `CONTROL_FOLDER_ID` is missing or wrong, or this account can't see the folder | Copy the ID again (step 2). Check you are signed in as the script account |
| `the NG Fab Schedule could not be opened: ...` | `SHEET_ID` is missing or wrong, this isn't the account that owns the workbook, or it is an Excel file in Drive and not a Google Sheet | Copy the ID again (step 3), and check which account you are signed in as. If its title shows a `.XLSX` tag, stop and tell Jim |
| `the workbook has no Sheet1` | The tab has another name | Ask Jim. Never rename it yourself |
| `Sheet1 is empty`, or `no Job Name header in the first 10 rows of Sheet1` | Wrong workbook, or the sheet has changed | Check `SHEET_ID`, then look at the sheet |
| `no "designer" header in row ...` or `more than one "fab date" header in row ...` (any of the four headers) | The export needs exactly one each of Job Name, Designer, Install Date and Fab Date | Look at the sheet. If its layout really changed, the script must change, never Jim's sheet |
| `the header beside Install Date in row ... is not blank, so the date is not two cells` | Install Date should be one header merged over two cells | The same |
| `sulaiman has no email set up (Script Property ROLE_SULAIMAN_EMAIL)` | The alert role can't be reached | Add that property |
| `jim has no email set up (Script Property ROLE_JIM_EMAIL)` | It asks for Jim's channel only when `SHADOW` is off | `SHADOW` must be `true` now, so check it. Add `ROLE_JIM_EMAIL` too |
| `... has no pushover set up ...` or `... has no slack set up ...` | That role's channel names a service with no keys | Set its `ROLE_<ROLE>_CHANNEL` back to `email` |
| `...'s channel ... is not email, slack or pushover` | A typo in `ROLE_<ROLE>_CHANNEL` | Correct it |
| `EXPECTED_ON_FROM is not a time like 06:00` (or `EXPECTED_ON_UNTIL`) | The time isn't `HH:MM` | Correct it |

A clean answer doesn't prove everything. `checkSetup` can't tell whether an address is the right person's, whether the signing key matches Jim's PC, or whether `ROLE_BARBARA_EMAIL` is set. It only checks the sheet's headers, so it can't tell the right workbook from another with the same layout.

## 8. See it work

Within about 10 minutes:

1. **The export.** The control folder gets a `fab` folder holding `export.json`. If it doesn't, click **Executions** on the left and open the newest `exportFab`: it logs why it wrote nothing.
2. **The triggers.** **Executions** shows `processOutbox` every minute and `exportFab` and `runWatchdog` every 5 minutes, none **Failed**.
3. **Your alert.** An email from "NG Runner" reaches `<your address>`, subject "NG Runner is not checking in", saying "NG Runner has not checked in yet." That is right, since NG Runner isn't on Jim's PC yet, and it proves the alert reaches you. It comes once, and outside the expected hours it waits until `EXPECTED_ON_FROM`. Check Spam the first time.
4. **Each morning** after 7:00 AM Pacific, until NG Runner runs on Jim's PC, "SHADOW No list today" reaches you (not with `PC_SLEEPS_AT_NIGHT` set). That is right too. Jim gets nothing: if he ever gets "No list today", `SHADOW` is not `true`.

## Not proven until it runs live

The full list is in [google/README.md](../../google/README.md), "Not verified". The three to watch here:

- **The workbook stays read-only by code alone.** The script account could edit it, so only `google\`'s code keeps it read-only, and its tests fail on any write.
- **Pruning NG Runner's files.** They reach Drive under the account Drive for desktop uses on Jim's PC, and Google says only the owner may trash a file. If that is the script account, pruning should work. If it is another account, it may be refused: run [google/README.md](../../google/README.md)'s setup step 6 in the first test window, before counting on the 30 days for `shadow/`.
- **The daily quota.** On a free account, triggers may run 90 minutes a day in total, and the one-minute outbox uses the most. Measure it in the first week under **Executions**. If needed, run `processOutbox` every 5 minutes instead.

## Later

- **Go live (window 4):** clear `SHADOW` and run `checkSetup` again, which now asks for Jim's channel ([jims-pc.md](jims-pc.md)).
- **A new signing key:** [the runbook](../p1a/runbook.md), "Key rotation".

## Checklist

- [ ] The script account owns the NG Fab Schedule, has 2-Step Verification on, and its other triggers are noted
- [ ] Control folder made, shared with you (and Jim's other account, if Drive on his PC uses one), **General access** Restricted, its ID copied
- [ ] NG Fab Schedule's ID copied
- [ ] Project holds `appsscript.json` and seven `.gs` files, all saved
- [ ] Signing key made with the command in step 6, saved in the password manager, set as `SIGNING_KEY`
- [ ] Script Properties set, with `SHADOW` at `true` and `ALERT_ROLE` at `sulaiman`
- [ ] `checkSetup` says `everything NG Runner needs is set`
- [ ] `installTriggers` ran, and **Triggers** lists five
- [ ] `fab/export.json` is in the control folder
- [ ] The "not checking in" email reached you, and Jim got nothing
- [ ] After window 1: `PC_SLEEPS_AT_NIGHT` is set if his PC is off at night
- [ ] Window 2: the folder is in Jim's Drive and its path is noted; `NGRunner.exe setup` used the same signing key and the same expected hours
