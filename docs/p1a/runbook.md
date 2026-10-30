# NG Runner runbook (P1a)

For Sulaiman, or a backup developer, keeping NG Runner running on Jim's PC. It covers starting, stopping and pausing; falling back to chat; every P1a alarm with its one-line fix; updating and rolling back; restoring onto a new PC; rotating keys; and deleting fixtures (build spec 6.7, design section 6 "Monitoring, support, ownership"). It grows with each phase.

Written in wave 1 of P1a. Items marked † rely on parts that wave 2 wires (the supervisor, the board's Health page, the tray menu, the `canary` and `setup` commands); check them against the build before go-live.

**The rule above all others: when NG Runner is down, Jim works in chat as before.** NG Runner only adds; it never takes over a job so that chat can't work it. What it missed it catches up on when it is back, and says so. Never tell Jim "nothing is lost": the design bans those words (section 4, "Silence and failure"), because a risk may be open.

## Where things are

| What | Where |
|---|---|
| The install | `%LOCALAPPDATA%\Programs\NGRunner\`: `launcher\` (NGRunnerLauncher.exe, what everything starts), `app-<version>\` (one folder per version, side by side), `installer\` (uninstall.ps1 and its files), `tasks\` (the task XML as registered), `ngrunner-install.json` (the install record) |
| NG Runner's data | `%LOCALAPPDATA%\NGRunner\`: `ngrunner.db` (the database), `settings.json`, `launcher.json` (which versions passed their self-test, failed starts, notes for DEV), `logs\` (`launcher.log`, `app-output.log`, NG Runner's own rotating log †), `backup\` (copies of files commit_write replaced, pruned after 60 days), `renders\` (fetched IC summaries: client documents, this PC only), `staging\`, `support\` (support bundles) |
| Secrets | Windows Credential Manager, generic credentials `NGRunner/signing_key` and `NGRunner/export_key`, plus `NGRunner/pushover_token` and `NGRunner/pushover_user` once Pushover is added (P1b, `docs/decisions.md`). Never in files, logs or bundles |
| The control folder | A Drive folder shared only by the account that runs the Apps Script (the one that owns the NG Fab Schedule), Sulaiman and, if Drive on Jim's PC uses another account, that one: `fab/`, `heartbeat/`, `outbox/`, `receipts/`, `pause/`, `logs/`, `backups/`, `support/` and, in shadow mode, `shadow/` (spec section 3) |
| Auto-start | Scheduled tasks `NGRunner-logon` (at sign-in) and `NGRunner-ensure` (every 10 minutes); Run-key value `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\NGRunner`; Start Menu item `NG Runner` (it carries the AUMID `ICLA.NGRunner`, without which notifications don't show) |

Commands, in a PowerShell window on Jim's PC (`$app` is the newest `app-<version>` folder):

```powershell
$app = "$env:LOCALAPPDATA\Programs\NGRunner\app-0.1.0"
& "$app\NGRunner.exe" --version                 # the version
& "$app\NGRunner.exe" --self-test               # the post-install checks; 0 = passed
& "$app\NGRunner.exe" --self-test --record      # the same, and lets the launcher start this version
& "$app\NGRunner.exe" support-bundle            # writes a bundle into the data folder's support\
& "$app\NGRunner.exe" restore <file>.ngrbak     # the restore drill (asks for the private key)
& "$app\NGRunner.exe" canary --folder <folder>  # proves a drive's file system; 0 = every check passed †
& "$app\NGRunner.exe" setup                     # writes settings.json and puts the keys in Credential Manager †
```

Exit codes: 0 done, 1 a problem (the printed lines say what), 2 a wrong command line, 3 a command not built yet.

## Start, stop, pause

- **Start.** The Start Menu item `NG Runner`; if it runs already, its board opens instead. It also starts at sign-in, and within 10 minutes of stopping by itself. The launcher writes one line per start in `logs\launcher.log` (`started 0.1.0 (process 1234)`).
- **Stop.** Tray, then Quit (it asks first) †. NG Runner stays off until Jim signs in again or starts it from the Start Menu: the 10-minute task respects a Quit. The heartbeat stops, so expect the "not checking in" alarm after 45 minutes; to stop for longer during expected-on hours, pause instead.
- **Pause.** Tray, then Pause, or `pause` in the board's command box †. From afar, a signed file in the control folder's `pause/`, made by `tools/sign_pause.py` † (pausing is always safe, so a file is enough). Paused, NG Runner files nothing, and keeps its heartbeat, the digest, messages and backups; the tray turns grey. **Resume only at the PC**: tray, then Resume, or `resume`.
- **Shadow or live.** It starts in shadow mode, which files into the control folder's `shadow/` and sends every message to Sulaiman, marked SHADOW, with no toasts on Jim's PC. The switch to live is on the board's Health page †, and the `SHADOW` Script Property is cleared with it (`docs/testing/jims-pc.md`, window 4).

## Falling back to chat

Whenever NG Runner is down, paused, or a job has waited on it for more than 2 days:

1. Jim gets the IC summary from Assembly Reports and uploads it to his chat, as before NG Runner.
2. Anything NG Runner already filed in `NG Jobs\<Job>\` stays and is correct: the summary passed the strict check and two fetches agreed. Chat reads `<Job>_NGRunner.json` before working a job (the RULES line).
3. Nothing NG Runner wrote needs undoing, and nothing needs to be told to it later: it re-reads the folders.

## Alarms, and their one-line fixes

| Alarm (as it reads) | Who sees it, where | One-line fix |
|---|---|---|
| "NG Runner is not checking in" (no heartbeat for 45 minutes in expected-on hours) | Sulaiman, from the Apps Script watchdog | Remote in: wake or sign in the PC; if NG Runner isn't running, start it from the Start Menu, then read the last lines of `logs\launcher.log` and `logs\app-output.log`; if it is, check that Google Drive for desktop is signed in and syncing, since heartbeats reach the control folder through it. |
| "No list reached Jim" (to Sulaiman) and "No list today" (to Jim, on his `ROLE_JIM_CHANNEL`, `email` until Pushover is added): no digest receipt by 7:00 AM. With `SHADOW` set, "SHADOW No list today", to the alert role only | Jim and Sulaiman, from the watchdog | The same fix as the line above; the digest follows the next run. If his PC is simply off at night, declare it: `pc_sleeps_at_night` in NG Runner's settings and `PC_SLEEPS_AT_NIGHT` in the Script Properties. If `SHADOW` was left set after go-live, clear it: a digest to Jim doesn't count while it is set. |
| No daily "ran" line came (its absence is the alarm, design section 2) | Sulaiman | Check the heartbeat: if NG Runner is not checking in, use the first line's fix; if it is, read `logs\app-output.log` in a support bundle and the Outbox log in the Apps Script project. |
| "The Fab Schedule export is over 30 minutes old, so new rows are not being seen" | Jim's digest (For you), the board | In the Apps Script project (under the account that owns the NG Fab Schedule), check FabExport's 5-minute trigger and its last error, then run FabExport once by hand. It writes nothing, and logs why, when Sheet1 returns no rows or its header row isn't recognised, so look at the sheet too. |
| "The job list is over an hour old and older than the oldest waiting row, so that row can't be matched yet" | Jim's digest, the board, DEV | On the office PC, check the `AssemblyReportsPublishJobs` task (it runs only while `autob` is signed in) and `logs\publish_jobs.log` there, then press Sync in Assembly Reports. |
| "The online Assembly Reports refused NG Runner's export key: it is not set there, or it is a different one." | Sulaiman (DEV, the board's Health page †) | Set the same value in both places (see "Key rotation"). "Has no job-list export yet" instead means its update is not deployed: Sulaiman deploys it. |
| "Windows notifications are off for NG Runner, so its pop-ups don't show" | Jim's digest, the board, DEV | Settings, System, Notifications: turn notifications on, and on for NG Runner. If NG Runner is missing from that list, run install.ps1 again (it remakes the Start Menu item). |
| "NG Runner is paused, so nothing is filed until you resume it at the PC" | Jim's digest, grey tray | Clear the reason for the pause, then Resume at the PC. |
| "NG Runner can't send messages" (no receipt for 5 minutes; red tray), and on the For you line "Messages are not being confirmed sent, so some may not reach you; the board still shows everything" | Jim: the tray, the board, the digest, and his phone once Pushover is added (P1b) | In the Apps Script project, check the Outbox trigger (every minute), its quota and that account's sign-in; receipts resume by themselves. |
| "<file> was not written: NG Jobs is not reachable right now." (or the control folder) | Sulaiman (DEV), the job's row on the board † | Check that Google Drive for desktop is running, signed in and online on Jim's PC, and that the folder in the settings still exists; the write is tried again when that clears. |
| "Could not get a complete IC summary for <Job>" (fetching failed for 24 hours) | Jim's card, the digest | Open the job's Install Report in Assembly Reports; once Studio renders it fully, the next fetch files it (or answer the card). |
| A fetch found another job's name in the report (`wrong_job`: a DEV item and that card at once †) | Sulaiman (DEV), Jim's card | Compare Studio's project name with the Opportunity Name. A rename the job list already shows is handled by itself, so this is a real mismatch to fix there. |
| Two fetches came back byte for byte the same (`same_bytes`: one DEV item †) | Sulaiman (DEV) | Something between NG Runner and Studio served one render twice, so look for a cache or proxy on Jim's PC or network. It keeps fetching every 10 minutes, and the 24-hour card bounds it. |
| A report too big to fetch, or two fetches in different layouts (`too_big`, `layouts_differ`: a DEV item, no loop †) | Sulaiman (DEV) | Open the job's Install Report in Assembly Reports and look at the job itself; meanwhile Jim works it in chat as before. |
| A Fab export held: more than max(5 rows, 10%) of its rows gone or new at once (DEV †) | Sulaiman (DEV) | Look at Sheet1. A real change goes through when the next export says the same; a sheet read cut clears with the next whole export. |
| A job with no filing because its folder was there at install (OBSERVED) or is in `NG Jobs\Old` (no write, and the job's row says why †) | Jim and Sulaiman, the board | Nothing to fix: NG Runner only watches such a folder and never makes a second one, so Jim works that job in chat as before. |
| An urgent message with no receipt and no Pushover keys (logged, a DEV item, the red tray †) | Sulaiman (DEV) | The same fix as the "can't send messages" line; until receipts resume, only the board and the tray reach Jim. |
| "<Job> has waited on NG Runner for over 2 days; work it in chat as before" (a DEV item over 48 hours old) | Jim's digest | Jim works the job in chat; Sulaiman fixes the cause from a support bundle and closes the DEV item. |
| "NG Runner 0.1.1 failed to start twice (...), so 0.1.0 was started instead." | Sulaiman's DEV lane † | Read `logs\app-output.log` and `launcher.log` in a support bundle; fix, give the build a new version and install it (the failed version stays out until its own self-test passes again). |
| "No version of NG Runner could start: ..." (and, at a Start Menu click, a box saying NG Runner could not start) | Sulaiman (DEV, once a version starts; the heartbeat alarm meanwhile), Jim | Remote in, run `--self-test` on the newest version and read `app-output.log`; install the last good release again. |
| "NG Runner's start-up record is damaged" (`launcher.log`; nothing starts) | Sulaiman, through the heartbeat alarm | Run `NGRunner.exe --self-test --record` for the version to keep: it sets the damaged record aside (`launcher.json.damaged-...`) and writes a fresh one. |
| The self-test failed during install | Sulaiman, in the install window | Fix what the numbered lines name, rebuild under a new version, install again (install.ps1 already removed the failed version). |
| "NG Runner's database was last used by a newer version of NG Runner" (after a rollback) | Sulaiman (DEV, `app-output.log`) | Start the newer version again, or restore the last backup made by this version (see "Restore"). |
| "NG Runner's database is damaged (SQLite's check found problems), so it can't be used; restore it from a backup." | Sulaiman (DEV, `app-output.log`, the red tray †) | Quit NG Runner, move `ngrunner.db` and its `-wal` and `-shm` files aside, then `NGRunner.exe restore "<file>" --install` (see "Restore"). |
| A message failed the message check, so its fixed template went out instead (DEV names the rule and the line, never the text) | Sulaiman (DEV) | Nothing waits on it: fix the template, or the outside text that fed it, in the next build. |
| A secret is missing from Credential Manager (a plain board item, never a crash loop †) | Sulaiman, the board | Put it back from ICLA's password manager as `NGRunner/<name>`, or through `NGRunner.exe setup` †. |
| The nightly backup has no key: `backup_public_key` is blank (a board item, not a crash †) | Sulaiman, the board | Make a pair with `NGRunner.exe keygen` and put the public key in the settings (see "Key rotation"). |
| The settings file has settings this version does not know, kept unused (after a fallback to an older version; a DEV note †) | Sulaiman (DEV) | Nothing to do while the older version runs: it writes them back for the version that knows them. |
| A commit cut short by a crash, settled at start-up (DEV †) | Sulaiman | Nothing to do for a file that holds what was meant; check by hand any path the note says was left alone. |
| Leftover temp files (`*.ngr-tmp`) listed on the board | Sulaiman, the board's Health page † | NG Runner never deletes them: with NG Runner quit, check each against `logs\` and delete the listed files by hand. |
| A write refused by commit_write (its reason in DEV) | Sulaiman | The chokepoint did its job: find the code that asked for that write (events, support bundle) and fix that, never the chokepoint's rules. |
| A control-folder file refused (bad signature, old sequence number, an id seen before; never a Fab file read again unchanged, or a late receipt) | Sulaiman (DEV; the file is ignored) | Check that the signing key in Credential Manager and in the Apps Script's Script Properties match (see "Key rotation"). |
| No `backups/<YYYYMMDD>.ngrbak` for a day | Sulaiman (weekly check of the control folder) | Check that the PC was on that day (nightly work runs at the first chance after 1:30 AM, or at the next logon) and look in DEV for the backup's reason; the next night writes the next one. |
| Pruning left a copy in `backup\` (it holds a link, or the folder can't be read) | Sulaiman (DEV) | Look inside `%LOCALAPPDATA%\NGRunner\backup\<time>\`; remove a link itself by hand, never through it. |
| "NG Runner has no settings yet; run its setup first" | Sulaiman, through the heartbeat alarm | Run `NGRunner.exe setup` †, or put `settings.json` back from the last support bundle's values. |

While Sulaiman is away, set `ALERT_ROLE` and `ALERT_NAME` in the Script Properties, so the watchdog's alarms reach and name the backup contact (design section 4; `google/README.md`). During the shadow run, leave `ALERT_ROLE` at `sulaiman`, since NG Runner's copies go there.

## Update and rollback

**Build** (Sulaiman's PC):

1. All tests green: `.venv\Scripts\python.exe -X warn_default_encoding -m pytest -q`.
2. Raise `version` in `pyproject.toml` (digits and dots only, like `0.1.1`: the launcher reads no other kind). Every build that leaves the PC gets a new version; install.ps1 refuses a second, different build under a version already installed.
3. Build into a new, empty folder outside the repo:
   `.venv\Scripts\python.exe -m PyInstaller ngrunner.spec --noconfirm --distpath <out>\dist --workpath <out>\build`
4. Note the last line: `manifest.json sha256 <hash>`.

**Install** (Jim's PC, over the remote session):

1. Copy `<out>\dist` to Jim's PC (zipped), as `NGRunner-<version>`. Nothing ever installs itself from Drive. Install when Mozaik is closed (design section 6, "Updates"): install.ps1 does not check for it, because Mozaik's exe name is only confirmed on Jim's PC in test window 1, and P1a writes nothing a Mozaik job reads.
2. In PowerShell: `powershell -NoProfile -ExecutionPolicy Bypass -File "<folder>\installer\install.ps1" -ManifestSha256 <hash>`
3. It checks every file against the manifest, copies the new version beside the old one, runs its self-test (a failing version is removed again), registers the tasks, the Run key and the Start Menu item, asks the running NG Runner to stop (it finishes what it is doing; after 2 minutes it is ended), and starts the launcher.
4. Check: `launcher.log` ends with `started <version>`, and the board and the next heartbeat show it.

If install.ps1 says PowerShell runs in Constrained Language Mode, Smart App Control or an App Control policy is on: the scripts and the exes must be signed first (decision 13).

**Rollback** happens by itself: two failed starts of a version retire it, the previous good version is started, and a DEV note says so. By hand: `uninstall.ps1 -Version <bad version>` removes that version only; the launcher then starts the newest remaining version that passed its self-test. If the newer version had already upgraded the database, the older one refuses it: start the newer one again, or restore the last backup the older one made. Keep the last two versions; remove older ones with `uninstall.ps1 -Version <old version>`.

## Restore onto a new PC, and the restore drill

**The drill** (part of P1a's exit criteria, then monthly; it writes nothing):

1. Take the newest `backups/<YYYYMMDD>.ngrbak` from the control folder.
2. On any PC that has the release: `NGRunner.exe restore "<file>"`, and paste the private backup key from ICLA's password manager when asked (it is not shown).
3. Pass: `Restore drill passed: schema version N, T tables, R rows.` A wrong key, a changed byte or a cut-short file is refused with its reason.

**A new PC** (or a rebuilt one):

1. Install with `install.ps1 -NoStart`, and go straight on to step 2: the 10-minute task starts NG Runner otherwise, and a database it made stops step 2 (then quit it and move that new database aside).
2. `NGRunner.exe restore "<file>" --install`. It puts the database in place only where NG Runner has none, and never while NG Runner runs. It also moves the outgoing sequence numbers far ahead, since the backup is older than the last files the old PC sent, and the Apps Script ignores any number it has seen. So the first message waits up to 2 minutes for the gap, and that is normal. It moves the card numbers ahead too, so no card ID is issued twice.
3. Put the settings back (`settings.json`, or `NGRunner.exe setup` †) and the secrets into Credential Manager from ICLA's password manager.
4. Start NG Runner from the Start Menu; check the board and the first heartbeat.

What happened after the backup was made (rows seen, summaries filed) is not in the restored database. commit_write never files over a summary that is already there, so nothing is filed twice; NG Runner must then treat such a summary as already filed †.

## Key rotation

Never paste a key into chat, email, a ticket or a log.

- **Backup key pair.** `NGRunner.exe keygen` on Sulaiman's PC prints a pair and its key id. The private key goes into ICLA's password manager, named with its key id; only the public key goes into NG Runner's settings on Jim's PC, as `backup_public_key` †. Keep the old private key until the last backup made with the old key has left the control folder (30 days): every backup names the key id that opens it, and restore says which key it needs.
- **Signing key** (control-folder envelopes). Pause NG Runner. Make a new key (64 hex characters, `ngrunner.signing.new_key()`), put it in Credential Manager as `NGRunner/signing_key` and in the Apps Script's Script Properties as `SIGNING_KEY`, then resume at the PC. An outbox file refused meanwhile stays refused until its `refused.<id>` property is deleted (`google/README.md`, "Changing the signing key").
- **Export key** (the job list from the online Assembly Reports). Set a new value for `ASSEMBLY_EXPORT_KEY` in that service's environment on Render (Sulaiman deploys it himself) and in Credential Manager as `NGRunner/export_key`.
- **Pushover** (once it is added). Make a new application token in Pushover, then update `NGRunner/pushover_token` (and `NGRunner/pushover_user` if the user key changed).

## Deleting fixtures

Fixtures are real client documents used for testing; they never enter git (AGENTS.md rule 5).

- **A developer's PC:** delete `.local-fixtures\` in the repo, any folder `NGR_FIXTURES` points to, `out\`, build output folders, and pytest's temp folders (`%TEMP%\pytest-of-<user>`).
- **Jim's PC:** `%LOCALAPPDATA%\NGRunner\renders\` holds fetched IC summaries; delete it only with NG Runner quit, and only once the jobs no longer need them.
- **Drive:** fixture files referenced by the sha256 manifest stay in ICLA's Drive, and ICLA decides about them. When someone leaves the project, ICLA removes their Drive access and they delete their local copies.
- Never "clean up" anything in NG Jobs, Mozaik Shared or the control folder by hand: those are Jim's records.

## Support bundle

Tray, then Send support bundle †, which commits it to the control folder's `support/` (pruned after 30 days); or `NGRunner.exe support-bundle`, which writes it into the data folder and prints where. It holds the versions, the settings, the database's schema version and row counts (never a row), the launcher's record and the end of each recent log. It never holds a secret, an address or the board's token.

## Uninstall

`powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\NGRunner\installer\uninstall.ps1"`

It unregisters both tasks, removes the Run-key value and the Start Menu item, asks NG Runner to stop, releases every job to chat (`release-all` †; P1a writes no leases, `docs/decisions.md`, so chat is not held up either way), and removes the install folder's own parts. It keeps the data folder and prints where it is: remove that by hand, and only after the last backup has passed a drill. It keeps the Credential Manager entries too: remove those by hand if they should go. It removes nothing if anything in the install holds a link or junction, and nothing from a folder without `ngrunner-install.json`.
