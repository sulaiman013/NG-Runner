# AGENTS.md — NG Runner

Canonical guide for any coding agent in this repo. Domain words are defined in [GLOSSARY.md](GLOSSARY.md) — use them exactly.

## What it is
A Windows tray app for Jim Delany (owner, Inspired Closets LA), installed for his own user on his PC. When Barbara adds a row to the NG Fab Schedule, NG Runner files the job's verified IC summary, then (phase by phase) checks, guards and locks the job, with Jim making every decision by click. Built and supported remotely by Sulaiman (solo, part-time).

**The design** (proposed, the source of truth for scope and rules) is local only, because it names clients: `C:\Sulaiman Automation - Assembly Reports\NG Runner design\` — start at its `README.md`; the design is `01-final-design.md`. Jim's own pipeline spec snapshot: `C:\Sulaiman Automation - Assembly Reports\NG IC MOZAIK\` (`RULES.md` outranks his other docs).

## Rules (do not break)
1. **All writes outside the app's own data folder go through `ngrunner.writes.commit_write()`.** Nothing else opens a file for writing in Mozaik Shared, NG Jobs or any shared folder. It never deletes anything the same commit did not create: it removes only its own temp files, the empty folders it made and, in a rollback, a file it renamed in moments before (each checked by identity first). Its `interlock` is a required keyword-only argument with no default (it used to default to "always clear"), so no caller can skip the Mozaik check by leaving it out.
2. **Never write while Mozaik could be using the files** (any Mozaik process, `MozaikJob.LCK`, `OptimizeJob.LCK`). Wait, never force.
3. **Code decides.** No LLM in any verdict, gate, message or parser. The only LLM use is the Phase 5 drawing reader, behind the two-source rule.
4. **Jim's rules come from his toolkit, not from here.** Don't re-type RULES.md logic; call or port his scripts, and where his docs disagree, raise it as a question (Q1–Q19), never pick silently.
5. **Client data never enters git.** No IC summaries, Mozaik files, real job names, addresses or emails in code, tests or docs that are committed. Tests use synthetic names; real files live in `.local-fixtures/` (gitignored).
6. **No secrets in the repo or logs.** Keys live in Windows Credential Manager.
7. **Windows-safe I/O:** always pass `encoding="utf-8"`; read and write `.des` files in binary; open CSVs with `newline=""`; no `os.chdir`; absolute paths only.
8. **Never guess.** A value that can't be verified is reported as "could not verify". Messages to Jim follow his format (numbered, one line each, imperial, no intro or sign-off; a clean result is exactly `Nothing to fix — matches IC summary.`).
9. **Commit and push only when Sulaiman asks.**

## Layout
| Path | What |
|---|---|
| `ngrunner/` | The app package. One module per concern (`writes.py`, `intake/`, `match.py`, …). The running app: `supervisor.py` (start-up, schedule, board and tray wiring), `intake/pipeline.py` (a Fab row through to a filed job), `interlock.py` (the P1a write check), `jobmodel.py` (store facts to board rows, tray and digest), `messenger.py` (sending and receipts), `canary.py` (proves a drive). `intake/studio_designs.py` reads Studio's Proposal tab for the design check |
| `ngrunner/guards/` | Phase 1b's read-only guards. `watch.py` runs beside `pipeline.tick`: `scan.py` reads G: on a worker and `watch.py` takes what it found, with `takeoffs.py` (the order heads-up) and `lockcheck.py` (the lock pre-flight). `nest_state.py` is the verdict, one pure function; `tap.py`, `sheets.py`, `labels.py`, `headsup.py`, `tracker.py` and `preflight.py` read and judge one kind of file each; `kept.py` is what they keep in the state table. `docs/p1b/build-spec.md` is the contract, as `docs/decisions.md` settles it |
| `tests/` | pytest. Synthetic data in git; real-file tests are marked `fixtures` and skip when their local material is absent (below). `fakes.py` and `test_e2e_day.py` are the day simulation: its scenarios run on a fake clock, with a fake Studio, Render and Apps Script, and from scenario 30 on they are the guards |
| `google/` | The Apps Script bridge to Google: Fab Schedule export and edits, outbox, watchdog, prune (`README.md` there). Its tests in `google/test/` run under node, through `tests/test_fab.py` |
| `installer/` | `install.ps1`, `uninstall.ps1`, the scheduled-task XML and the frozen build's entry scripts. `ngrunner.spec` at the root is the PyInstaller spec |
| `tools/` | Standalone scripts: `setup_check.ps1` (read-only check of Jim's PC) and `setup_check.cmd` (what Jim double-clicks to run it), `sign_pause.py` (a signed pause file, for Sulaiman), `build_kit.py` (the test kit), `baseline.py` (the baseline measurement) |
| `docs/p0/` | Phase 0 material for Jim (questionnaire, access list, Setup Check guide), blank. His answers name jobs, so they stay outside the repo, in his private Drive folder: read them there, and never save them over `questionnaire-jim.md` (git would take that file). `.gitignore` keeps every other file in `docs/p0/`, and any copy of the questionnaire, out of git |
| `docs/p1a/` | Phase 1a: `build-spec.md` and `integration-spec.md` (the contracts), `runbook.md` (running it on Jim's PC), `rules-lines.md` and `skill-update.md` (drafts for Jim's chat) |
| `docs/decisions.md` | Every decision with its reason: the defaults adopted instead of Jim's questionnaire, and the rulings that settled how Phase 1a's parts fit. Before changing how a part behaves, check it here; a change goes here first |
| `docs/testing/jims-pc.md` | The short, scripted test windows on Jim's PC |
| `.local-fixtures/` | Gitignored. `summaries/ngjobs/` = 27 archived `<Job>_summary.pdf`; `summaries/downloads/` = raw Assembly Reports downloads (incl. incomplete early renders and Studio-native names); `summaries/sold/` = one fetch each of recent sold jobs; `summaries/pairs/` = two fetches each, 10 minutes apart. The last two are named by index, with `index.json` beside them |

## Commands
```powershell
.\.venv\Scripts\python.exe -X warn_default_encoding -m pytest -q                     # all tests
.\.venv\Scripts\python.exe -X warn_default_encoding -m pytest -q -m "not fixtures"   # synthetic only
.\.venv\Scripts\python.exe -m ngrunner canary --folder <absolute path>               # the canary, from the repo
& "$app\NGRunner.exe" canary --folder <absolute path>                                # the canary on Jim's PC
```
On Jim's PC the canary runs from the installed build, which has no Python (`$app` is the newest `app-<version>` folder, `docs/p1a/runbook.md`).

Keep `-X warn_default_encoding`: without it Python raises no `EncodingWarning`, so the `error::EncodingWarning` filter in `pyproject.toml` can't catch a missing `encoding="utf-8"` (rule 7). `tests/conftest.py` refuses to start a run without it, whichever tests are picked. `.gitattributes` fixes line endings (CRLF for `.cmd`/`.bat`, LF for the rest), whatever a PC's `core.autocrlf` says.

## Fixtures (local-only test material)
Tests marked `fixtures` read material that never enters git, and skip when it is absent. They name real files by index or count, never by name, and show Jim's spec text nowhere.

| Variable | Default | What |
|---|---|---|
| `NGR_FIXTURES` | `.local-fixtures\` | The fixtures folder (real IC summaries, see Layout) |
| `NGR_NG_JOBS` | discovered | The NG Jobs folder, for the real-file matcher tests. Unset, it is discovered under `G:\.shortcut-targets-by-id\*\...`, `G:\My Drive\...` or `G:\Shared drives\...`; the test skips when none is found |
| `NGR_SPEC_DIR` | `C:\Sulaiman Automation - Assembly Reports\NG IC MOZAIK` | Jim's spec snapshot, for the quote check in `tests\test_docs_p0.py` |
| `NGR_DESIGN_DIR` | `C:\Sulaiman Automation - Assembly Reports\NG Runner design` | The design, which names real jobs. `tests\test_docs_p1a.py` reads it and Jim's snapshot, and fails if any file git would commit names a client from them |

## Before filing goes live
P1a files nothing on Jim's PC until all of these hold:
1. **Two fetches agree.** Two fetches of a job 10 minutes apart have the same `content_fingerprint()`: the summary check alone can't catch a partial render that is consistent with itself.
   - **Measured on real jobs from Sulaiman's PC (2026-10-30).** 6 of 6 jobs agree on their text and all 105 drawings, and no pair is byte-identical, since Studio renders afresh, so identical bytes count as no agreement (`same_bytes`). Of the 60 most recently changed sold jobs, 59 fetched (7 to 95 s each) and all 59 pass the summary check. `tests/test_real_renders.py` keeps both true on those files.
   - Jim's PC repeats it during the shadow run, on the real jobs of those days.
2. **One report layout per job.** Every fetch of one job uses the same layout, landscape or portrait, so its fingerprints can be compared. A summary already in the job's folder in the other layout decides it, since 12 of the 27 archived summaries are portrait.
3. **The file system is proven on Jim's PC.** A canary on G: (Google Drive) and on OneDrive confirms `fsync`, `os.rename` and `os.replace`, and the file identity every commit relies on: `os.fstat` of a new temp file and `os.lstat` of its path, taken while NG Runner still holds it open with no sharing, give the same `(st_dev, st_ino)`, and a rename keeps it. It also confirms the no-follow create (`CreateFile` with `CREATE_NEW` and `FILE_FLAG_OPEN_REPARSE_POINT`) works there. Where any of this differs, every commit refuses ("could not be prepared") and leaves an empty temp file behind, since the clean-up needs the same match.
   - **Proven on Sulaiman's PC on Google Drive for desktop (2026-10-29, 18 of 18 checks)**, in `CNC operations\Sulaiman\Sulaiman Testing (Do not delete)\`, the only place on G: tests may write. Everything above held. File and folder IDs stayed stable through Drive's sync, Drive refused to make junctions, and `commit_write` worked end to end: new files, an overwrite of its own file, and a rollback.
   - Jim's PC repeats it with `NGRunner.exe canary --folder <folder>` (Commands) in test window 2. OneDrive waits for P3, since P1a never writes there.
4. **Writes are recorded in SQLite.** The owned-files JSON registry is replaced by the SQLite write record (design §3.3, "Write"), with a crash-recovery path.
5. **Backups are pruned** after 60 days.
6. **The auto-match rate is measured on the real Sheet1** (design section 5), with the sold stages adopted from questionnaire items 3a and 3b (`docs/decisions.md`): Ready to Order and Ready to Install. Complete never counts: with it, every job installed before NG Runner would be a candidate, so a remake row of an installed job is asked about as a card, that job first.
   - **Measured so far on the real job list (2026-10-30).** Each sold job typed the way Barbara usually types it (bare surname, designer's initials), against the local list of 2026-09-15 (10,643 Opportunities): 32 of 53 jobs (60%) dated in the list's last 30 days, 64 of 116 (55%) in its last 90 days, and 307 of all 628 sold (49%). None matched the wrong job; the rest ask with a card.
   - **Read once on the real Sheet1 (2026-10-30).** The Apps Script's signed export, opened by the real code through Drive for desktop, against the live list of 10,781 Opportunities, each row treated as new: 67 of 91 rows auto-match (74%), 62 by exact name and 5 by surname and designer, and 4 of the 4 rows with an upcoming install date. 55 of the 67 have an NG Jobs folder under that name. The other 12 have none, which doesn't show them right or wrong, and no person has checked the 67.
   - The gate is still the shadow run (`docs/testing/jims-pc.md`, window 3): rows as they arrive, checked by a person.
7. **Leftover temp files are reported.** `commit_write()` never deletes a `*.ngr-tmp` it did not create in the same commit, so a crash leaves them in place; the board lists any it finds for Sulaiman.
8. **A report that leaves something out is measured.** The summary check and the two fetches show that a render is whole, stable and true to its own page 1, not that it holds everything the job needs. A design missing from page 1 can't be seen from the file, so the report is checked against a second list: the Proposal tab of Studio's project page (`intake/studio_designs.py`, `docs/decisions.md`). That check is being wired in.
   - **Measured on real jobs (2026-10-30).** On 59 of 59 real sold jobs, the Proposal tab's designs equal the report's page 1, by name: 124 designs on each side, while those projects hold 202 in all, so the proposal and not the whole project is the list. A report that differs from the proposal doesn't file until Jim answers a card. When Studio can't be read, the job still files, marked "designs not cross-checked".
   - **What it doesn't catch.** A design the designer left off the proposal, or a missing drawing inside a design (measured 2026-10-30 on 86 real renders, 189 designs, 969 walls: elevation pages often show several walls but carry one wall number, so 172 designs have walls with no page labelled for them, and a rule on it would refuse real jobs). So "Filed as", the digest and the job's log show its design and page counts for a person to catch a missing room, and the shadow run compares every filed summary's designs with what Jim actually builds: any missing design blocks go-live. The BI model's parts list is still an unverified candidate for these.
   - Never call a filed summary "complete" or "verified complete": it is "checked".
9. **Studio's report changes by itself.** It is rendered live from the current catalog, so the same design can read differently later. Measured 2026-10-30: 3 of 4 of Jim's copies from 10-18 differ from a fresh fetch in rod part numbers and descriptions only, with identical drawings (a 96in rod became 92in). The nightly re-fetch will raise an "IC summary differs" card on every filed job with a changed part, so the card says what differs (`intake/report_diff.py`), and the shadow run must show how many such cards a catalog change makes.

## Status (2026-10-30)
Phase 1a is about 97% built, and Phase 1b's guards that can run from Drive's programs and tracker are wired and proven on the built app. The Apps Script is live on real Google in shadow mode. The built `.exe` has run the whole path on Sulaiman's PC: a shadow run that wrote nothing to NG Jobs, the go-live switch, and 13 of 13 jobs filed for real into a stand-in NG Jobs. Three independent reviews' problems are fixed. Nothing is installed on Jim's PC.

- **The parts, each with tests:** the Phase 0 material (`docs/p0/`, `tools/setup_check.ps1` and `setup_check.cmd`), and in Phase 1a `commit_write()` with the P1a write rules and the SQLite write record (`writes.py`), matching (`match.py`), the strict summary check (`intake/summary_check.py`), fetching and the two-fetch agreement (`intake/fetch.py`), the store (`store/`), secrets, the job list (`joblist.py`), the Fab export and the Apps Script (`fab.py`, `google/`), messages, cards, digest and heartbeat, the board, tray and toasts, and the launcher, self-test, backup, support bundle, installer and runbook.
- **The wiring (wave 2, `docs/p1a/integration-spec.md`):** the pipeline, job model, supervisor, messenger, interlock and canary, the `setup` command and `tools/sign_pause.py`. `intake/branch.py` is corrected: a mixed closet-and-garage summary is one job, with no card. A fit check of the parts found 28 places where they would not work together; its rulings are in `docs/decisions.md`, and the specs stand as the rulings settle it.
- **Proven on fakes only:** all 22 scenarios of the day simulation pass, and 3,945 tests in all. None of that touched real Studio, Render, Drive, a packaged build or Jim's PC, so the gates above still stand. The scenarios passed on their first full run with no mutation checks, so nothing yet shows that each assertion can catch a fault.
- **Going in now (wave 3):** the Studio design check with its design and page counts (item 8 above), and the final rulings (`docs/decisions.md`). Then an independent review, the release build and the test kit.

**Phase 1b, group 1, is wired into the running app and runs on fakes and real files read-only; nothing of it has been committed or run in the built app on real services.** A first scan of the real NG Jobs alarms nothing; as if every program had just been posted, the real guards read 60 materials in about 10 s and raise 20 DO NOT CUT (16 doubled holes, 4 sizes) and 4 sheet size cards. Group 2 and 3 (`.opt`, the observer, the toolkit, `Watchdog.gs`, the tool adapters) wait, so nothing can yet reach "Nest checked".

The repo is public on GitHub (github.com/sulaiman013/NG-Runner, branch `master`), so rule 5 matters twice over: no client data, and no secrets, ever.
