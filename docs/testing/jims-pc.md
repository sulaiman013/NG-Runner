# Testing on Jim's PC

All development happens on Sulaiman's PC. Jim's PC is for short, scripted test windows that stay out of the way of his other work.

Every window states how long it takes, what it changes and how to undo it. Each one runs over Chrome Remote Desktop once that is set up, at a time Jim isn't at the PC. No window restarts the PC or changes Windows power, update or restart settings (`docs/decisions.md`, "The night"). Whatever can be proven on Sulaiman's PC is proven there first.

## Before the first window (on Sulaiman's PC)

1. Build the release: the PyInstaller folder build from `ngrunner.spec`, then its self-test.
2. Run `install.ps1 -Root <scratch> -NoRegister` and the self-test from the installed copy.
3. Run the canary (`python -m ngrunner canary`) on this PC's Google Drive, in a test folder of Sulaiman's own.
4. Zip the test kit:
   - the build;
   - `tools\setup_check.cmd` and `setup_check.ps1`;
   - `installer\`;
   - this file.

Ready outside Jim's PC before window 3:
- **Google:**
  - the ICLA Google automation account;
  - the control folder, shared only by Jim's account, the automation account and Sulaiman;
  - the Apps Script project (`google\`) deployed, with its Script Properties set: signing key, addresses, Pushover when added.
- **Render:** the job-list export, shipped by Sulaiman's push to Assembly Reports, with `ASSEMBLY_EXPORT_KEY` set.

## Window 1 — first look (about 15 minutes, Jim present)

1. **Remote access:** Jim installs Chrome Remote Desktop and turns on unattended access with a PIN only he and Sulaiman know (about 5 min).
2. **Setup Check:** double-click `setup_check.cmd`. It is read-only, takes up to 3 minutes and saves two report files.

3. **Mozaik:** open Mozaik's About screen and note the program's name, install folder and version, then close it. NG Runner must recognise Mozaik exactly to never write while it runs, and a new version can change the file format.

| Changes | Undo | Passes when |
|---|---|---|
| Chrome Remote Desktop is installed; nothing else | Settings, Apps, uninstall it | The report is read and decides signing (Smart App Control). It also gives the real paths of NG Jobs, Mozaik Shared and the control folder on his PC, shows Drive and OneDrive healthy, and records the Mozaik version |

## Window 2 — install and prove the drives (about 20 minutes, over remote access)

1. Copy the test kit to the PC. Run `install.ps1` for Jim's user; it needs no admin rights. It creates:
   - the app folder under `%LOCALAPPDATA%\Programs\NGRunner`;
   - a Start Menu shortcut;
   - the logon and "ensure running" scheduled tasks;
   - a Run-key fallback.
2. Run `ngrunner --self-test`: it must report nothing.
3. Run the canary on Jim's Google Drive in the control folder. It proves:
   - the no-follow create;
   - `fstat` and `lstat` give the same identity while the file is open, and a rename keeps it;
   - `fsync`, rename and replace;
   - read-back and rollback.

   It removes only the files it created, checked by identity. Mozaik Shared is not touched: P1a never writes there.
4. Write the settings and the keys to Credential Manager, and start NG Runner in **shadow** mode.
5. **Copy the material for later phases.** Do this with Mozaik closed, without tidying or renaming anything, and only into Sulaiman's private Drive folder, never into git:
   - the `_NG_Automation` toolkit, zipped, including its 3 frozen regression jobs and `baseline.json`;
   - about 20 closed Mozaik job folders, newest first, covering if possible a garage, a Tafisa colour with 1/4 backs, a bridge between bays of different depths, REA and radius panels, a stacked tower and a remake.

   P2 and P3 are built on these on Sulaiman's PC.

| Changes | Undo | Passes when |
|---|---|---|
| The app, its shortcut, two scheduled tasks, a Run key, its data folder, two Credential Manager entries | `uninstall.ps1`, which keeps the data folder unless asked | The self-test is clean, the canary passes on G:, the tray shows SHADOW, and the heartbeat reaches the control folder |

## Window 3 — shadow run (1 to 2 weeks, unattended)

NG Runner does everything except write into NG Jobs:
- it reads the Fab Schedule export, matches rows, fetches and checks each summary twice, and decides the branch;
- it writes the files it would have filed into `control\shadow\` instead;
- Barbara gets nothing (copies go to Sulaiman);
- Jim's digest goes to Sulaiman only;
- the board is labelled SHADOW.

Sulaiman checks once a day, remotely or from the heartbeat and digest copies. Each check compares every shadow decision with what really happened: the folder Jim's chat created and the summary it holds.

| Changes | Undo | Passes when |
|---|---|---|
| Nothing outside NG Runner's own data folder and the control folder | Pause from the tray, or uninstall | 0 rows matched to the wrong job; every other row asked, with a reason that makes sense; no crash; the heartbeat unbroken while the PC is on; the auto-match rate on real rows measured and written down |

## Window 4 — go live (5 minutes, over remote access)

Switch shadow off. Barbara's emails and Jim's digest go to them. The design's P1a exit criteria then run for 10 working days:
- every new row filed within 45 minutes or carded;
- 0 summaries filed to the wrong job;
- the board right on 10 jobs Jim picks;
- 10 of 10 digests;
- a sleep test that alerts Sulaiman within 50 minutes;
- 20 of 20 forbidden writes refused;
- the restore drill passed.

Undo: switch back to shadow, or pause. Nothing NG Runner filed is ever removed by it.
