# What Sulaiman needs from your PC

**Not sent (Sulaiman, 2026-10-29).** Sulaiman does these items himself, with Jim's OK, in the test windows on Jim's PC (`docs/testing/jims-pc.md`). The questionnaire and the weekly check-in are dropped (`docs/decisions.md`). Kept as the record of what each item is for.

**For:** Jim. **From:** Sulaiman. Needed for Phase 0 (getting ready) and Phase 1 (filing summaries). Most of it happens on one 20-minute call.

**Where files go.** Make one Google Drive folder that you own, for example "NG Runner for Sulaiman", and share it with Sulaiman only. Everything below goes there. Never email these files or post them in Slack, and don't put them in the shared CNC operations folders: Mozaik jobs and NG Jobs folders carry client names and addresses. Keep the folder afterwards. It becomes the store of test files, and nothing in it ever goes into NG Runner's code.

**Before you zip anything:** close Mozaik, and send the folders exactly as they are. Don't tidy or rename anything first.

## Before the call

- [ ] **1. Your `_NG_Automation` folder, zipped**
  - Why: it is your toolkit (scripts, templates, the 3 frozen test jobs and their baseline). NG Runner runs your own tools, not retyped copies, and none of it is on Sulaiman's PC yet.
  - How: in File Explorer open Mozaik Shared, right-click `_NG_Automation` and choose **Compress to ZIP file** (on some PCs: **Send to > Compressed (zipped) folder**). If OneDrive asks to download files first, let it. Upload the zip to the folder.
- [ ] **2. About 20 closed Mozaik job folders, zipped**
  - Which: locked and cut jobs, newest first. Between them, try to cover a garage, a Tafisa colour with its 1/4 backs, a bridge between bays of different depths, REA panels, radius panels, a stacked tower and a remake.
  - Why: Sulaiman has never been able to read a real Mozaik design or nest file (the copy on his PC is cloud-only). The checks, the nest guard and the `.bak` rule are tested on these before they go near a live job.
  - How: select the job folders in Mozaik Shared\Jobs, zip them (one zip or a few) and upload.
- [ ] **3. The NG Jobs folders for those jobs**
  - Why: they hold the IC summary, the conversion, the crosscheck files and the programs, so each Mozaik job can be checked against what was cut.
  - How: nothing to send. Sulaiman already reads NG Jobs (including Old) on the shared drive, read-only. Tell him only if a job's NG Jobs folder is somewhere else.
- [ ] **4. The 3 frozen regression jobs**
  - Why: they are your yardstick (RULES §8). Every shared tool has to give the same answers on them, on Windows, before it goes live.
  - How: they come inside the toolkit zip, in `reference\regression` with `baseline.json`; check that folder is in the zip. If their original job folders are still in Mozaik Shared\Jobs, zip those too.

## On the 20-minute call

- [ ] **5. The call itself**
  - Why: items 6 to 8 need you at your PC, signed in as yourself.
  - How: pick a time that suits you. Have sections 1–4 of the questionnaire answered if you can.
- [ ] **6. Chrome Remote Desktop, unattended, with a PIN**
  - Why: so Sulaiman can install, update and fix NG Runner during your night without you at the PC. It is free; AnyDesk's free version isn't allowed for business use.
  - How: in Chrome go to remotedesktop.google.com/access, choose **Set up remote access**, install it, name the PC and pick a PIN of at least 6 digits. Use the account you agree on the call. Give Sulaiman the PIN by phone, never in writing. You can turn access off any time from the same page.
- [ ] **7. The Setup Check report**
  - Why: it reads, and never changes, the settings that decide whether NG Runner can run at night and how it must be installed: Windows edition and admin rights, sleep and wake, Smart App Control, antivirus, whether ICWC manages the PC, and OneDrive and Drive.
  - How: over the remote session, Sulaiman puts `setup_check.cmd` and `setup_check.ps1` on your Desktop and you double-click `setup_check.cmd`. In a minute or so it saves two files on the Desktop: the report (`.txt`) and a copy of it for Sulaiman's tools (`.json`). Drag both into your private Drive folder (in File Explorer: Google Drive (G:) > My Drive).
- [ ] **8. Mozaik's program name, install folder and version**
  - Why: NG Runner never writes while Mozaik is running, so it has to recognise Mozaik exactly. A Mozaik upgrade can change the file format, so the version is recorded and watched.
  - How: open Mozaik and its About screen. Sulaiman notes the version and reads the program's location over the remote session. Close Mozaik afterwards.

**Also in Phase 0:** the questionnaire (about 50–65 minutes; sections 1–4, needed before the call, about 30–40) and two weeks of the weekly two-question check-in.
