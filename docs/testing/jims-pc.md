# Testing on Jim's PC

All development happens on Sulaiman's PC. Jim's PC is for short, scripted test windows that stay out of the way of his other work.

Every window states how long it takes, what it changes and how to undo it. Each one runs over Chrome Remote Desktop once that is set up, at a time Jim isn't at the PC. No window restarts the PC or changes Windows power, update or restart settings (`docs/decisions.md`, "The night"). Whatever can be proven on Sulaiman's PC is proven there first.

## Before the first window (on Sulaiman's PC)

1. Build the release: the PyInstaller folder build from `ngrunner.spec`, then its self-test.
2. Run `install.ps1 -Root <scratch> -NoRegister` and the self-test from the installed copy.
3. Run the canary from the installed copy (`NGRunner.exe canary --folder <folder>`) on this PC's Google Drive, in a test folder of Sulaiman's own.
4. Zip the test kit:
   - the build;
   - `tools\setup_check.cmd` and `setup_check.ps1`;
   - `installer\`;
   - this file.

Ready outside Jim's PC before window 3:
- **Google** (step by step: [apps-script-setup.md](apps-script-setup.md)):
  - the Apps Script's account: the one that owns the NG Fab Schedule (`docs/decisions.md`, "Accounts");
  - the control folder, shared only by that account, Sulaiman and, if Drive on Jim's PC uses another account, that one;
  - the Apps Script project (`google\`) deployed, with its Script Properties set: signing key, addresses, Pushover when added, and, from the start, `SHADOW` set to `true` with `ALERT_ROLE` left at `sulaiman` (window 3 says why).
- **Render:** the job-list export, shipped by Sulaiman's push to Assembly Reports, with `ASSEMBLY_EXPORT_KEY` set.
- **Studio:** the login NG Runner will use, in ICLA's password manager, for the setup in window 2. It never goes in the repo.

## Window 1 — first look (about 15 minutes, Jim present)

1. **Remote access:** Jim installs Chrome Remote Desktop and turns on unattended access with a PIN only he and Sulaiman know (about 5 min).
2. **Setup Check:** double-click `setup_check.cmd`. It is read-only, takes up to 3 minutes and saves two report files.

3. **Mozaik:** open Mozaik's About screen and note the program's name, install folder and version, then close it. NG Runner must recognise Mozaik exactly to never write while it runs, and a new version can change the file format.
4. **The night:** ask Jim whether this PC stays on overnight. The answer sets one setting in window 2.

| Changes | Undo | Passes when |
|---|---|---|
| Chrome Remote Desktop is installed; nothing else | Settings, Apps, uninstall it | The report is read and decides signing (Smart App Control). It also gives the real paths of NG Jobs, Mozaik Shared and the control folder on his PC, shows Drive and OneDrive healthy, and records the Mozaik version |

## Window 2 — install and prove the drives (about 20 minutes, over remote access)

1. Copy the test kit to the PC. Run `install.ps1` for Jim's user; it needs no admin rights. It creates:
   - the app folder under `%LOCALAPPDATA%\Programs\NGRunner`;
   - a Start Menu shortcut;
   - the logon and "ensure running" scheduled tasks;
   - a Run-key fallback.
2. Run `NGRunner.exe --self-test`: it must say it passed, with exit code 0.
3. Run the canary on Jim's Google Drive in the control folder: `NGRunner.exe canary --folder <control folder>` (the build has no Python). It proves:
   - the no-follow create;
   - `fstat` and `lstat` give the same identity while the file is open, and a rename keeps it;
   - `fsync`, rename and replace;
   - read-back and rollback.

   It removes only the files it created, checked by identity. Mozaik Shared is not touched: P1a never writes there.
4. **Settings, keys and the Studio login.** Run `NGRunner.exe setup`. It asks for each setting in plain words, writes `settings.json`, puts the two keys and the Studio login in Credential Manager, shows no secret and checks the folders. NG Runner signs in to Studio through the community site, in Microsoft Edge with no window showing, the first time it needs to and then every 14 days or so, so Edge must stay on this PC. Quit NG Runner from the tray and start it from the Start Menu, so it reads them. It starts in **shadow** mode, the default. Then, before window 3, set these in the Apps Script project's Script Properties:
   - `ROLE_JIM_CHANNEL`: `email`, the default, since P1a has no push channel (`docs/decisions.md`), with his address in `ROLE_JIM_EMAIL`.
   - If his PC is off at night (window 1), which P1a allows: `pc_sleeps_at_night` on in the setup and `PC_SLEEPS_AT_NIGHT` set to `true` here. Otherwise the 7:00 AM alarm fires every morning.
   - `EXPECTED_ON_FROM` and `EXPECTED_ON_UNTIL`, the same hours as the setup's.
5. **Copy the material for later phases.** Do this with Mozaik closed, without tidying or renaming anything, and only into Sulaiman's private Drive folder, never into git:
   - the `_NG_Automation` toolkit, zipped, including its 3 frozen regression jobs and `baseline.json`;
   - about 20 closed Mozaik job folders, newest first, covering if possible a garage, a Tafisa colour with 1/4 backs, a bridge between bays of different depths, REA and radius panels, a stacked tower and a remake.

   P2 and P3 are built on these on Sulaiman's PC.

| Changes | Undo | Passes when |
|---|---|---|
| The app, its shortcut, two scheduled tasks, a Run key, its data folder, Credential Manager entries for the two keys, the Studio login and, after the first sign-in, its session | `uninstall.ps1`, which keeps the data folder and the Credential Manager entries: remove those by hand if they should go too | The self-test is clean, the canary passes on G:, the tray shows SHADOW, the heartbeat reaches the control folder, and the board shows no Studio login missing |

## Window 3 — shadow run (1 to 2 weeks, unattended)

NG Runner does everything except write into NG Jobs:
- it reads the Fab Schedule export, matches rows, fetches and checks each summary twice, compares its designs with Studio's proposal, and decides the branch;
- it writes the files it would have filed into `control\shadow\` instead, where they are pruned after 30 days, since they hold client PDFs;
- Barbara gets nothing (copies go to Sulaiman);
- Jim's digest goes to Sulaiman only. With the `SHADOW` Script Property set, the Apps Script counts that as the morning's list, and its "no list today" reaches Sulaiman only, never Jim;
- no toasts pop up on Jim's PC: the two immediate card kinds would otherwise appear during an unattended window;
- the board and tray are labelled SHADOW.

Sulaiman checks once a day, remotely or from the heartbeat and digest copies. Each check compares every shadow decision with what really happened: the folder Jim's chat created and the summary it holds.

Two new alarms come from the design check. Each is one DEV item a day, and jobs keep filing, marked "designs not cross-checked":
- `studio_unavailable` (Studio can't be reached, or its login is missing or refused). Fix: run `NGRunner.exe setup` with the right login, or wait if Studio is only out of reach.
- `page_changed` (Studio's project page no longer reads as measured). Fix: correct the parser in `ngrunner/intake/studio_designs.py` on Sulaiman's PC and ship a new build.

| Changes | Undo | Passes when |
|---|---|---|
| Nothing outside NG Runner's own data folder and the control folder | Pause from the tray, or uninstall | 0 rows matched to the wrong job; every other row asked, with a reason that makes sense; no crash; the heartbeat unbroken while the PC is on; the auto-match rate on real rows measured and written down; no design missing from a filed summary, compared with what Jim actually builds (AGENTS.md: Before filing goes live, item 8) |

## Window 4 — go live (5 minutes, over remote access)

Switch shadow off, in both places:

1. On the board's Health page, switch to live. The switch is logged as an event.
2. In the Apps Script project, clear the `SHADOW` Script Property and run `checkSetup`, which now asks for Jim's channel. Left set, it would count no digest to Jim, so the next morning would show it.

**Jobs filed during the shadow run:** the next pass takes each one whose row is still on the sheet, or that has no row, through a fresh fetch and files it in NG Jobs, and Barbara gets her "Filed as" then. A folder Jim made himself is compared and never overwritten. A job whose rows have left the sheet stays aside, reading "Trial run only, not in NG Jobs", until `add job` or a new row brings it back.

Barbara's emails and Jim's digest go to them, and his toasts show. The design's P1a exit criteria then run for 10 working days:
- every new row filed within 45 minutes or carded;
- 0 summaries filed to the wrong job;
- the board right on 10 jobs Jim picks;
- 10 of 10 digests;
- a sleep test that alerts Sulaiman within 50 minutes;
- 20 of 20 forbidden writes refused;
- the restore drill passed.

Undo: switch back to shadow and set `SHADOW` to `true` again, or pause. Nothing NG Runner filed is ever removed by it.
