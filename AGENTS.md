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
| `ngrunner/` | The app package. One module per concern (`writes.py`, `intake/`, `match.py`, …) |
| `tests/` | pytest. Synthetic data in git; real-file tests are marked `fixtures` and skip when their local material is absent (below) |
| `tools/` | Standalone scripts: `setup_check.ps1` (read-only check of Jim's PC) and `setup_check.cmd` (what Jim double-clicks to run it) |
| `docs/p0/` | Phase 0 material for Jim (questionnaire, access list, Setup Check guide), blank. His answers name jobs, so they stay outside the repo, in his private Drive folder: read them there, and never save them over `questionnaire-jim.md` (git would take that file). `.gitignore` keeps every other file in `docs/p0/`, and any copy of the questionnaire, out of git |
| `.local-fixtures/` | Gitignored. `summaries/ngjobs/` = 27 archived `<Job>_summary.pdf`; `summaries/downloads/` = raw Assembly Reports downloads (incl. incomplete early renders and Studio-native names) |

## Commands
```powershell
.\.venv\Scripts\python.exe -X warn_default_encoding -m pytest -q                     # all tests
.\.venv\Scripts\python.exe -X warn_default_encoding -m pytest -q -m "not fixtures"   # synthetic only
```
Keep `-X warn_default_encoding`: without it Python raises no `EncodingWarning`, so the `error::EncodingWarning` filter in `pyproject.toml` can't catch a missing `encoding="utf-8"` (rule 7). `tests/conftest.py` refuses to start a run without it, whichever tests are picked. `.gitattributes` fixes line endings (CRLF for `.cmd`/`.bat`, LF for the rest), whatever a PC's `core.autocrlf` says.

## Fixtures (local-only test material)
Tests marked `fixtures` read material that never enters git, and skip when it is absent. They name real files by index or count, never by name, and show Jim's spec text nowhere.

| Variable | Default | What |
|---|---|---|
| `NGR_FIXTURES` | `.local-fixtures\` | The fixtures folder (real IC summaries, see Layout) |
| `NGR_NG_JOBS` | discovered | The NG Jobs folder, for the real-file matcher tests. Unset, it is discovered under `G:\.shortcut-targets-by-id\*\...`, `G:\My Drive\...` or `G:\Shared drives\...`; the test skips when none is found |
| `NGR_SPEC_DIR` | `C:\Sulaiman Automation - Assembly Reports\NG IC MOZAIK` | Jim's spec snapshot, for the quote check in `tests\test_docs_p0.py` |

## Before filing goes live
P1a files nothing on Jim's PC until all of these hold:
1. **Two fetches agree.** Two fetches of a job 10 minutes apart have the same `content_fingerprint()`: the summary check alone can't catch a partial render that is consistent with itself.
2. **One report layout per job.** Every fetch of one job uses the same layout, landscape or portrait, so its fingerprints can be compared.
3. **The file system is proven on Jim's PC.** A canary on G: (Google Drive) and on OneDrive confirms `fsync`, `os.rename` and `os.replace`, and the file identity every commit relies on: `os.fstat` of a new temp file and `os.lstat` of its path, taken while NG Runner still holds it open with no sharing, give the same `(st_dev, st_ino)`, and a rename keeps it. It also confirms the no-follow create (`CreateFile` with `CREATE_NEW` and `FILE_FLAG_OPEN_REPARSE_POINT`) works there. Where any of this differs, every commit refuses ("could not be prepared") and leaves an empty temp file behind, since the clean-up needs the same match.
4. **Writes are recorded in SQLite.** The owned-files JSON registry is replaced by the SQLite write record (design §3.3, "Write"), with a crash-recovery path.
5. **Backups are pruned** after 60 days.
6. **The auto-match rate is measured on the real Sheet1** (design section 5), with the sold stages adopted from questionnaire items 3a and 3b (`docs/decisions.md`): Ready to Order and Ready to Install. Complete never counts: with it, every job installed before NG Runner would be a candidate, so a remake row of an installed job is asked about as a card, that job first.
7. **Leftover temp files are reported.** `commit_write()` never deletes a `*.ngr-tmp` it did not create in the same commit, so a crash leaves them in place; the board lists any it finds for Sulaiman.

## Status (2026-10-29)
Slice 1 exists, uncommitted: the Phase 0 material (`docs/p0/`, `tools/setup_check.ps1` and `setup_check.cmd`, both run on Sulaiman's PC) and the Phase 1a foundations, each with tests: `commit_write()` with the P1a write rules (`writes.py`), matching of files, folders and Fab rows to jobs (`match.py`), and the strict summary check with `content_fingerprint()` (`intake/summary_check.py`). Not built yet: fetching, the Fab trigger, SQLite, tray, board, digest and installer. Nothing is installed on Jim's PC. The repo is public on GitHub (github.com/sulaiman013/NG-Runner, branch `master`), so rule 5 matters twice over: no client data, and no secrets, ever.
