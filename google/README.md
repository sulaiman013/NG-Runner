# NG Runner's Google side (Apps Script)

One Apps Script project, under the Google account that owns the NG Fab Schedule (`docs/decisions.md`, "Accounts"), that is NG Runner's bridge to Google. It:

- exports Sheet1 of the NG Fab Schedule to `fab/export.json` every 5 minutes, and logs Barbara's edits to Job Name, Designer and Install Date in `fab/edits.json`. It only reads the workbook; it never changes it (risk C3);
- sends NG Runner's outbox (email, Slack or Pushover) every minute, writing a receipt for each message and never sending one twice;
- watches NG Runner's heartbeat every 5 minutes: Sulaiman hears of a heartbeat over 45 minutes old, and Jim hears "no list today" when no digest went out by 7:00 AM Pacific (during the shadow run, Sulaiman hears that instead: `SHADOW` below);
- prunes old files from the control folder every day.

It talks to NG Runner only through signed files in the control folder (`ngrunner/signing.py`; `Signing.gs` is the same format). Addresses, tokens and the signing key live only in Script Properties, never in this code.

| File | What |
|---|---|
| `appsscript.json` | V8, time zone America/Los_Angeles, the OAuth scopes the scripts use |
| `Config.gs` | Settings, the control folder, the lock, Pacific time; `installTriggers` and `checkSetup` |
| `Signing.gs` | Sealing and opening envelopes; every control-folder file goes through it |
| `FabExport.gs` | `exportFab` (every 5 min) |
| `FabEdits.gs` | `onFabEdit` (installable edit trigger on the workbook) |
| `Outbox.gs` | `processOutbox` (every minute) and the channel senders |
| `Watchdog.gs` | `runWatchdog` (every 5 min) |
| `Prune.gs` | `pruneControlFolder` (daily, 3 AM Pacific) |
| `test/` | Node tests with fakes of the Google services (below) |

## Setup

1. Under the account that owns the NG Fab Schedule (the script account), create a standalone Apps Script project. In Project Settings, tick "Show appsscript.json", then paste in `appsscript.json` and the seven `.gs` files.
2. Make the control folder under the script account, and share it with Sulaiman and, if Drive on Jim's PC uses another account, with that one. The workbook needs no sharing.
3. Set the Script Properties (Project Settings, Script Properties) from the table below.
4. Run `checkSetup` from the editor. It lists what is missing or wrong, one line each, never a value. Fix those until it lists nothing.
5. Run `installTriggers` and accept Google's authorization. It refuses while `checkSetup` finds a problem, and running it again replaces its own triggers.
6. If Drive for desktop on Jim's PC uses another account, check on real Google that the script account may trash NG Runner's files, since pruning depends on it. Google's reference for `File.setTrashed` says "Only the owner may trash the File", and NG Runner's files reach Drive under the account Drive for desktop uses. If it uses the script account, the owner is the same and this step isn't needed.
   1. As the account Drive for desktop uses on Jim's PC, put a small file named `trash-check.txt` in the control folder, and wait until it shows there on drive.google.com.
   2. As the script account, paste this into the project, run `tryTrash` from the editor, and delete it from the project again:
      ```js
      function tryTrash() { controlFolder_().getFilesByName('trash-check.txt').next().setTrashed(true); }
      ```
      An error that the iterator reached its end only means the file has not reached the control folder yet: wait, and run it again.
   3. No error, and Jim's account no longer sees the file in the control folder: pruning can do its job, and this step is done.
   4. Any other error, or the file still there for Jim's account: Google refused. Delete `trash-check.txt` as Jim's account. Pruning then can't clear what NG Runner wrote, so `shadow/` keeps client PDFs past 30 days and `outbox/` keeps growing, which slows the one-minute run.
      - If ICLA has Google Workspace with shared drives, move the control folder to a shared drive, where a content manager may trash any file, and make the script account a Content manager there. Then set `CONTROL_FOLDER_ID` from the folder's address, in case the move changed it, and NG Runner's `control` path on Jim's PC, and repeat this step.
      - Otherwise, raise it with Sulaiman.

   `checkSetup` also lists what Google refused on the last prune, which never stops `installTriggers`; a prune with no refusal clears that line. A prune that finds nothing old enough tries nothing (heartbeats are the first, at 2 days), so a clean list from `checkSetup` proves nothing yet: this step is the proof.

## Script Properties

Roles are NG Runner's (`jim`, `barbara`, `will`, `george`, `sulaiman`, `backup_contact`), written in capitals in property names: `ROLE_JIM_EMAIL`.

| Property | What |
|---|---|
| `SIGNING_KEY` | The 64-hex-character signing key, the same as `signing_key` in Credential Manager on Jim's PC |
| `CONTROL_FOLDER_ID` | The control folder's Drive ID (from its address) |
| `SHEET_ID` | The NG Fab Schedule's spreadsheet ID (from its address) |
| `ROLE_<ROLE>_EMAIL` | That role's email address |
| `ROLE_<ROLE>_PUSHOVER` | That role's Pushover user key |
| `ROLE_<ROLE>_SLACK` | The Slack user or channel ID the bot posts to for that role |
| `ROLE_<ROLE>_CHANNEL` | The channel for messages this script writes itself (the watchdog's): `email`, `slack` or `pushover`. Every role, Jim's included, defaults to `email`, since P1a has no push channel (`docs/decisions.md`) |
| `PUSHOVER_TOKEN` | The Pushover application token |
| `SLACK_TOKEN` | The Slack bot token (it needs `chat:write`) |
| `EXPECTED_ON_FROM`, `EXPECTED_ON_UNTIL` | The Pacific hours the PC is expected on, `HH:MM` (default `06:00` and `23:00`, as in NG Runner's settings). Equal times mean all day; a range may run past midnight. Keep them equal to NG Runner's own settings |
| `PC_SLEEPS_AT_NIGHT` | `true` for the declared "PC sleeps at night" mode: no 7:00 AM message |
| `ALERT_ROLE`, `ALERT_NAME` | Who the watchdog alerts, and the name Jim's message gives. Default `sulaiman` and `Sulaiman`; while he is away, set the backup contact |
| `SHADOW` | `true` during the shadow run (`docs/testing/jims-pc.md`, window 3), when NG Runner sends everything to Sulaiman. While it is set, a digest to `ALERT_ROLE` is the morning list; no digest by 7:00 AM sends `ALERT_ROLE` "SHADOW No list today", and never tells Jim; and `checkSetup` does not ask for Jim's channel. NG Runner's copies go to the role `sulaiman`, so keep `ALERT_ROLE` at that for the run. Clear it as NG Runner goes live (window 4): a digest to Jim doesn't count while it is set, so a `SHADOW` left on shows itself the next morning |

The script keeps its own state in Script Properties too: `state.*` (sequence numbers, the last heartbeat, the digest day, owed receipts, what Google refused to trash on the last prune), `done.<id>` and `refused.<id>` (one per outbox message, dropped after 31 days). Leave them alone, except as the runbook says.

## What NG Runner must match

- **Outbox:** `outbox/<id>.json`, where `<id>` is the envelope's id. Kind `outbox`; payload exactly `channel`, `to` (a role), `subject`, `lines`, `urgent`, `ref`. The lines are sent as they are, one per line. The digest's `ref` starts with `digest` (`digest` or `digest:<date>`), to `jim`, or to `ALERT_ROLE` while `SHADOW` is set: that is how the watchdog knows the morning list went out.
- **Receipts:** `receipts/<outbox id>.json`, kind `receipt`, written once per accepted message, sent or not. A message refused before sending (bad signature, wrong kind, a sequence number not above the last accepted) gets no receipt, so it shows on Jim's PC as a missing receipt. A receipt Drive refuses is written on a later run (up to 30 are kept for that); `sent_at` is still the time of sending.
- **Order:** outbox files are sent in sequence order. One that arrives ahead of an earlier one waits up to 2 minutes for it; after that the earlier one is refused if it comes.
- **Heartbeats:** `heartbeat/<YYYYMMDD>/<seq>.json`, the seq in the name equal to the body's. The three newest day folders are read. `open_risks` (a list of lines) is shown to Jim as it is.
- **Sequence numbers:** each stream this script writes (`fab_export`, `fab_edits`, `receipt`) starts at the current Unix second and rises by 1, so a re-created project never goes below an old stream.
- **Fab files:** `fab/export.json` and `fab/edits.json` are replaced in place; the export is rewritten every 5 minutes even when nothing changed. A header row it does not recognise, or a sheet that returns no rows with a job name, writes nothing and logs why: the last export stays, its `exported_at` ages, and NG Runner's 30-minute alarm says so. An export therefore always holds at least one row with a job name.
- **Pruned names:** after 30 days `support/support-<YYYYMMDD>-<HHMMSS>.zip` goes, and in `shadow/<folder>/` so do `<folder>_summary.pdf`, `<folder>_NGRunner.json`, `<folder>_NGRunner_log.txt` and a temp file a crash left of any of them. A job folder goes with its last file; a file named any other way is left alone, and keeps its folder.

## Tests

`node --test "google/test/*.test.js"` (from the repo root; node expands the quoted pattern itself) runs them with Node 22 and its built-ins only: no installs, no network, no Google account. The pytest bridge in `tests/test_fab.py` runs the same tests with envelopes sealed by Python, then reads what the scripts wrote with `ngrunner.fab` and `ngrunner.signing`: the two sides are checked against each other byte for byte. The fakes fail a test on any call that would change the workbook.

## Quotas and limits

- On a consumer Google account, triggers may run 90 minutes a day in total (6 hours on Workspace). The one-minute outbox trigger is the biggest user: measure it in the first week (design decision 19), and if needed run `processOutbox` every 5 minutes instead.
- MailApp sends to 100 recipients a day on a consumer account. When that is used up, receipts say so.
- Pushover takes 1024 characters per message: a longer list is cut at a whole line, ending "The rest is on the board."
- Pruning takes no lock, so it never holds up the outbox, and it stops after 4 minutes (Apps Script allows 6); the next day's run carries on.

## Not verified (no live Google run yet)

- Whether Google lets an installable edit trigger run for an account with Viewer access to the workbook. If it refuses, give Editor; the code still never writes (the tests fail on any write).
- The scopes: `SpreadsheetApp` is taken to need the full spreadsheets scope (its reference lists no read-only scope).
- For a date cell, Google may give an edit's old value as a serial number, not as shown. The log keeps what Google gives; it is a hint only.
- That Drive for desktop uploads files in any order (hence the 2-minute wait), and how long a file takes to reach Drive.
- Whether Drive keeps a file's creation date when Drive for desktop replaces it. Pruning goes by that date, so a `_NGRunner.json` rewritten in `shadow/` may count from its rewrite, which only keeps that job's folder a little longer.
- Whether the script account may trash what NG Runner wrote. Google's reference for `File.setTrashed` says "Only the owner may trash the File", and NG Runner's files reach Drive under Jim's account (Drive for desktop), so `heartbeat/`, `outbox/`, `logs/`, `backups/`, `support/` and `shadow/` may all be refused. Setup step 6 tries it; do that in the first test window, before counting on the 30 days for `shadow/`. Pruning already copes: a refused file or folder stays and is counted, the run carries on with the rest, the next run tries it again, and the outbox marks still go (a message whose file stays is still refused by its sequence number, so it is never sent twice). The last run's count is in `state.prune.refused`, the log and `checkSetup`'s list. Google's words for a refusal are not known yet, so any error from `setTrashed` counts as one, and the log gives the first in Google's words.

## Changing the signing key

Set the new key here and on Jim's PC at the same time. Files signed with the old key are refused; an outbox file refused that way stays refused (delete its `refused.<id>` property to have it read again).
