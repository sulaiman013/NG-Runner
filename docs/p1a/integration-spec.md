# P1a integration spec — wave 2

Wires the wave-1 slices into the running app. Read `build-spec.md` first; this file adds the flows, the job model, the supervisor and the acceptance scenarios. The design (local only) is the source of truth behind both. `docs/decisions.md` gives the reason for each ruling these flows follow; where a ruling and the design differ for P1a, the ruling holds.

## 1. The filing pipeline (`ngrunner/intake/pipeline.py`)

The supervisor calls `tick(ctx)` every 60 seconds; `ctx` holds the store, settings, secrets, clock, roots, the Render and Studio clients and the messenger. Every step is idempotent and keyed by store rows, so a crash or restart mid-way resumes without duplicates. Real fetches take up to 95 s, so a tick never waits on them: they run off the scheduler thread, a couple at a time, and stop between steps on a stop request.

1. **Fab export.** Read `control\fab\export.json` (and `edits.json`). Accept it only per the envelope rules (build-spec section 3): the same file read again (same seq and id) is unchanged, not a replay, and the 30-minute alarm goes by `exported_at`. Diff it against the last accepted export, whose rows are kept in the store as it came, not rebuilt from `fab_rows`: Add job rows have no sheet row, and would read as gone.
   - **The first export** after install goes through `diff(None, rows)`, and only it does: every row is marked `seen_at_install`, and nothing starts (design section 5). Every later one goes through `diff(previous, rows, edits, unchanged=True)`, which keeps sheet rows current, with only the edits made after the previous export's `exported_at`.
   - **A sheet that suddenly changes a lot.** An export where more than max(5 rows, 10% of the rows) are gone or new is held until the next export says the same, and logged to DEV. Nothing in it acts meanwhile.
   - `new`: a row identical in job, designer and install to a recently gone row is that row coming back, not a new job, so it returns to the state it had. Otherwise insert a `fab_rows` row with `first_seen` = now (not `exported_at`, which is earlier). It acts only once a later export still shows it unchanged, so a half-typed row never asks.
   - `edited`: update the row and, when its text changed, set `text_changed_at`.
     - A row in `new`, `waiting_list`, `asked` or `none` goes back to `new` (Barbara corrected the name), and its open which-job card is withdrawn.
     - A `seen_at_install` row stays seen, and the edit is logged.
     - A `matched` or `filed` row whose job text changed is re-matched in memory, with the job's own ID and folder left out of `ng_jobs`. Any result other than matched to this job raises the card "Row changed: which job?" and files nothing new.
     - A `filed` row where only the install date or designer changed, and whose old install date has passed, may be a remake, so it is not absorbed as an edit: it raises the card "Is this a remake of <Job>?", and nothing is filed until it is answered. That card is a new template in wave 2.
   - `gone`: mark `gone` and log it (rows move to Archive). Its open which-job card is withdrawn.
2. **Job list.** A `new` row that has waited its one export needs a list with `synced_at` at or after its last text change. That is `text_changed_at`, or `first_seen` until the text changes (`to_match()` passes it as `FabRow.first_seen`), so a corrected row needs a list read after the correction. If the stored list is older, ask Render for a push (at most once per 5 minutes) and set the row to `waiting_list`. Poll the export each tick until it is fresh. The alarm "job list is older than the oldest waiting row and over an hour old" goes to the board, the digest and DEV.
   - Match from `get_job_list`, so the list and its time come from one moment. Pass `synced_at`, never `fetched_at`.
   - Never replace a stored list with an empty one or one with `synced_at` None. `replace_opportunities` gets `clients=job_list.clients`.
   - Transient errors retry quietly; others are DEV items carrying `str(error)`. A missing export key is a board item, not a crash.
3. **Match.** `match_fab_row(row.to_match(), opportunities, ng_jobs, list_synced_at, sold_stages=settings.sold_stages)`. `ng_jobs` holds every Studio ID in `jobs` plus every NG Jobs folder name found by a read-only scan, `NG Jobs\Old` included.
   - `matched`: create or link the `jobs` row (name not yet frozen; mode `auto`, or `observed` when its folder was found at install), set the row to `matched`, queue fetch 1 now.
   - `ambiguous`: card "Which job is this row?" for Jim, with the candidates and "None of these"; its `choice_ids` hold each candidate's Studio ID. One outbox email to Barbara listing the candidates (design: "When a name is unclear she gets one email listing the candidates"). The row goes to `asked`. Barbara's correction re-runs the match (step 1).
   - `none`: the same card and email, with no candidates ("type the full job name in the row"). A row too long to read (`row_too_long`) takes this path.
   - `stale_list`: back to `waiting_list`.
   - **Jim's answer** links a job only through the card's `choice_ids`, after checking that the ID is still in the list. If it is gone, the board says so and the row stays `asked`. "None of these" sets the row to `none`, logged, with no refiling.
   - The card, the outbox row and the row's state change in one `store.transaction()`, after checking for an open card of that kind: `add_card` and `add_outbox` don't de-duplicate. `commit_write` never runs inside a transaction.
4. **Fetch 1.** `fetch_render(FetchTarget(...), base_url=settings.studio_url)`, stored in `renders`, with the bytes in `AppPaths.renders\<studio_id>\<sha256>.pdf`. A job's fetches all use one layout: its first render's, else `settings.report_layout`. Keep `Fetched.url` as the job's `known_url`.

   | `Fetched.code` | What happens |
   |---|---|
   | `ok`, and `check.ok` | Queue fetch 2 at +10 min |
   | `ok`, and not `check.ok` | An early render: retry every 30 min |
   | `not_found`, `timeout`, `network`, `http_error`, `not_pdf`, `truncated` | Retry with backoff: 5, 15, 30, 60 min, then hourly |
   | `too_big` | A DEV item |
   | `no_project_name` | Retry with the same backoff, then the 24-hour card |
   | `wrong_job`, and `found_name` equals the list's current name for that Studio ID | The same job renamed: add the alias, re-fetch under the new name, and log it. The Studio ID is the identity, and the check against the list keeps this from being a guess. The folder is never renamed |
   | `wrong_job`, any other name | A DEV item and the 24-hour card at once, never silent retries |

   Past 24 h from the first attempt: card "Could not get a complete IC summary for <Job>" (design 3.4: Fetch, 24 h, then a card), carrying `Fetched.reason`.
5. **Fetch 2.** `agree(first, second)`, over `Render(cache_path, fetched_at, layout)`.

   | `Agreement.code` | What happens |
   |---|---|
   | `agree` | File, using `Agreement.fingerprint` |
   | `differ` | The second becomes the new first; fetch again at +10 min, carrying the same 24-hour deadline |
   | `same_bytes` | One DEV item; fetch again at +10 min. The 24-hour card bounds it |
   | `first_incomplete` | Drop the first; the second then counts as fetch 1 (step 4) |
   | `second_incomplete` | Keep the first and fetch again, every 30 min as for an early render (step 4) |
   | `too_soon` | Fetch again at the first fetch's time + 10 min |
   | `layouts_differ` | A DEV item; do not loop |
   | `agree()` raises `OSError` (a cached file is gone) | Treat it as no first fetch |
6. **File.**
   - The render's PROJECT NAME already equals the Opportunity Name (fetch enforces it). Freeze the job name now; later names become aliases.
   - `branch_of(designs, row_text)`, asked about the row's text and the Opportunity Name alike (build-spec 6.2). A garage-only summary gives `<Name>_Gar`, whatever closet-side room word the row carries. Closets only, or closets and garage mixed, give `<Name>` with no card: a mixed summary is one job with no `_Gar` (Jim's BUILD.md Step 0). `unclear` (the row or the Opportunity Name says garage or `_Gar` and no design carries a garage word, or a design has a blank category and no garage word in its name) raises the card "Closet or garage?"; only that job waits.
   - Look in `NG Jobs\<folder>\` and `NG Jobs\Old\<folder>\` (read-only).
     - A summary there, NG Runner's or hand-named (`match_file_to_job` with `folder=` and `opportunity_names=`), is compared like with like. Read its layout from `check_summary(...).details['orientation']`. If it differs from the job's, compare against a fetched pair in that layout, and the job keeps it.
       - The same fingerprint: mark `filed` with no write, unless a crash cut NG Runner's own commit short (below).
       - A different fingerprint: card "IC changed: which render governs?". Never overwrite.
     - An OBSERVED job (its folder was found at install) gets no writes, and the board shows why; a same-fingerprint summary there still marks it filed.
     - A folder in `NG Jobs\Old` follows the same rules with no write, and the job is never given a second folder at the top.
   - Otherwise one commit of three new files through `ctx.commit` (section 3): `<folder>_summary.pdf`, `<folder>_NGRunner.json` (section 4) and `<folder>_NGRunner_log.txt`, with the P1a interlock (section 2).
   - After a crash mid-commit, when the start-up settle adopted part of a commit into `owned` (section 3), write whichever of the three files are missing, and only those.
   - A refusal leaves the job BLOCKED with the reason, and is retried when that reason clears: paused, unreachable or "folder changed".
7. **Tell.**
   - Outbox email to Barbara: "Filed as <Job>".
   - An event for the digest and the board.
   - From filing on, a nightly re-fetch for 30 days, one at a time, as a pending operation with no deadline. Content that changes, and is confirmed by two agreeing fetches, raises "IC changed: which render governs?". Nothing is ever re-filed by itself. A stored fingerprint read by another pypdf version is recomputed from `cache_path` first, so an upgrade raises no false card.

**Add job** (board button or `add job <name or Studio ID>`) enters the pipeline at step 3, with the typed text as an exact name, or at step 4 for a Studio ID found in the list. The typed text goes through `parse_command`, so digits mean a Studio ID. A job added by hand has no Fab row, so no "Filed as" email.

**What leaves the PC.** Jim's phone gets job names and dates only (decision 10), so cards and messages never quote IC design names. `Agreement.reason` can quote them, so it goes to the event log and DEV only, and card text uses fixed wording. `Fetched.reason` is safe.

## 2. The P1a interlock (`ngrunner/interlock.py`)

`p1a_interlock(ctx) -> Callable[[list[Path]], str | None]`. It returns a plain reason when:
- NG Runner is paused and a target is in NG Jobs or `control\shadow\`. Every other control-folder target passes, so a pause stops filing while heartbeats, the digest, messages and backups go on;
- a target is in the folder of an OBSERVED job, which gets no writes (the design says watch and check only);
- a target is outside NG Jobs and the control folder.

Otherwise it returns None. `commit_write` calls it twice. HELD is not in P1a: no button or command can set it, so it is not in the interlock and stays an unused overlay. The Mozaik conditions of design section 5 join here in P1b and later, as P1a writes nothing a Mozaik job reads.

## 3. The write record and crash recovery (`writes.py`, owned by its fixer)

Replace `owned_files.json` with the store's `owned` table:
- `commit_write` gains a keyword `registry=`, an object with four methods. The default stays the JSON registry until the supervisor passes the store's. When a registry is given, `_data_folder_problem` and `_record` skip the JSON, and `_record` catches `StoreError`, for plain words.
  - `owned(path) -> sha | None`;
  - `begin(planned) -> journal id`: **before the first rename,** the store writes a `pending_writes` journal row: paths, new hashes, state `pending`;
  - `record(journal id, written)`: **after the read-back,** moves those hashes into `owned` and the row to `done`, in one transaction;
  - `abort(journal id)`: a commit that ended with nothing changed closes its row.
- `owned` holds NG Jobs and `control\shadow\` paths only: heartbeats and outbox files are write-once, and about 300 commits a day would otherwise add rows forever.
- One `ctx.commit(writes)` wrapper does `commit_write` plus `record_commit`, so no caller forgets.
- **At start-up,** every `pending` row is settled:
  - each path that holds exactly its intended hash is adopted into `owned`;
  - every other path is left alone (never deleted);
  - the row is closed, and the case is logged to DEV;
  - a path unreachable at start-up keeps its row pending and retries, because closing it would strand a file NG Runner can't overwrite later.

  So a crash between the rename and the record never strands a file NG Runner can't overwrite later, and never adopts a file someone else changed.

`pending_writes` is the next store migration, appended. Wave 2 also closes the chokepoint's other findings, listed with their reasons in `docs/decisions.md` ("The write chokepoint").

## 4. Files in the job folder

`<folder>_NGRunner.json`, schema 1:

```json
{"schema": 1, "job": "<Name>", "branch": "closet",
 "summary": {"file": "<folder>_summary.pdf", "sha256": "...", "layout": "landscape",
             "pages": 19, "fetched": ["<ISO>", "<ISO>"]},
 "filed_at": "<ISO>", "items": [],
 "note": "NG Runner filed this IC summary. Chat sessions read this file before working the job."}
```

This is for machines (Jim's chat) and holds no address.
- **Kept current.** Jim's chat reads it first (`docs/p1a/rules-lines.md`), so NG Runner rewrites it whenever a card about the job opens or closes, and lists open cards in `items`. "IC changed: which render governs?" is one of them. It stays an owned file, rewritten through `commit_write`.
- **A summary replaced by hand.** The RULES lines allow this on Jim's word. When the summary's sha256 no longer matches what `_NGRunner.json` records, NG Runner logs it and never overwrites the summary. It rewrites `_NGRunner.json` so the notes describe the file that is there, and it marks the job's summary "replaced by hand". `<folder>_NGRunner_log.txt` is for people. Each line has a Pacific time and one plain sentence, for example `10/30 3:05 PM  Filed the IC summary (landscape, 19 pages), checked on two fetches 10 minutes apart.`

## 5. The job model (`ngrunner/jobmodel.py`)

Pure functions over store facts (design 3.4: facts, clicks and pending work; no single state).

- **Board rows:** one per job, and one per Fab row with no job yet (`JobRow.studio_id` None), so a row waiting on its which-job card still shows.
- **Ticks (P1a), by key:**
  - `row`, "on the Fab Schedule";
  - `job`, "matched to Salesforce";
  - `summary`, "fetched and checked twice";
  - `filed`;
  - `branch`, "closet" or "garage".

  Later phases add theirs; anything not built yet is `na`.
- **Install date:** a date; the words "could not verify" when `fab.install_date` gives None; None only when the row has no cells.
- **Overlays:** WAITING (resumes by itself: list, second fetch), BLOCKED (a reason and a one-line fix), NOT_RUN (a reason and a retry), OBSERVED (a folder found at install: no writes, and the row says why), PAUSED. HELD stays an unused value: nothing sets it in P1a.
- **Next step:** one plain line per job. Examples: "Waiting for a fresh job list", "Card TES-1: which job is this row?", "Second fetch at 3:15 PM", "Filed 10/30 3:05 PM", "Card FAK-1: closet or garage?".
- **Your list:** open cards, pinned items and DEV items older than 48 h, each naming its job through `dev.studio_id`, with the manual fallback "work <Job> in chat as before". A DEV item about no job leaves nothing to work in chat, so it stays with Sulaiman.
- **Tray colour:**
  - grey when paused;
  - red when NG Runner is unwell: no receipt for 5 min, the store unreadable, or an urgent DEV item;
  - amber with the count of Your list;
  - green otherwise.

  An unreadable store is decided by the supervisor, not by a view read from the store, since no view can be read then. An urgent DEV item is one opened with `urgent=True` (`dev.urgent`): NG Runner can't do its job until someone acts, as with an urgent message that has no receipt and no other way out (section 6). In shadow mode the tooltip starts SHADOW.
- **Digest view:**
  - owner items first: jobs installing within 5 days with no summary filed, alarms;
  - then one line per job touched since the last digest;
  - then open cards with their age, coming back at 1, 3 and 7 days;
  - then "Your list: N open, oldest D days";
  - a quiet night gets one line.

## 6. The supervisor (`ngrunner/supervisor.py`, `python -m ngrunner run`)

- **Start-up, in order:**
  - `single.acquire()`; on `None`, `open_running_board` if `--open-board`, then exit 0, which the launcher reads as already running;
  - `StopSignal`;
  - load settings and secrets. A missing one is a plain board item, never a crash loop: `run` must come up and call `record_up` even with neither, because `install.ps1` starts it before they exist, and two failed starts retire the version;
  - read `Store(path).schema_version()`; if it is below `LATEST`, `backup_to` first, because `open_store` migrates at once and an older version then refuses the file;
  - open and migrate the store; one it can't read shows red, and is no crash loop either;
  - start the board server and the tray, `publish_board`, then `record_up`, within 120 s of the start. Nothing before it waits on a shared folder;
  - `pending_notes` into `open_dev`, then `ack_notes`;
  - settle pending writes;
  - start the scheduler thread;
  - catch up missed windows and name them ("Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM").
- **A second start** opens the running board (build-spec 6.7). Test both: that file never enters the support bundle or a log, and the board's address never goes to stdout.
- **Logging.** One rotating log file in `AppPaths.logs` for the `ngrunner.*` and `waitress` loggers. It never holds a secret, the board's token URL or a message body.
- **Schedule:**

  | Task | When |
  |---|---|
  | Pipeline tick | every 60 s |
  | Heartbeat | every 5 min |
  | Receipts check | every minute: a message with no receipt after 5 min turns the tray red. An urgent one goes through `messages.pushover.send` if the Pushover keys are in Credential Manager; without them (all of P1a unless Sulaiman adds them) it is logged and a DEV item opens. The tray turns red either way |
  | Digest | 6:30 AM Pacific, once per Pacific date |
  | Nightly work: backup (committed to `control\<folder>\<name>`; `Pruned.left` and any `BackupError` go to DEV), re-fetches, yesterday's log to the control folder (composed from events, job names only), the toast-setting check, pruning NG Runner's own old backups, pruning old renders (keeping the governing one and the latest that differs), `prune_seen_ids`, pruning `writes` rows | from 1:30 AM, once per night key |
  | The daily "ran" and DEV lines to Sulaiman; telling Jim of DEV items over 48 h | with the nightly work |
  | Pause files | every minute |

- **Timing.** Phone quiet hours are 10 PM to 6 AM except DO NOT CUT, `cant_send` included. Barbara's emails use the same quiet hours, with no new setting. The morning-list toast shows with the digest, or at the first start after it. No unlock detection in P1a.
- **Sending** (the messenger), in order:
  - `vetted(msg, now=now)`; if `replaced`, open a DEV item with its problems;
  - in one transaction: `next_seq` with `start=` the current Unix second, `outbox_file`, `add_outbox`. The seq is assigned when the row is created, and a retry rewrites the same id and seq, so there are no gaps;
  - `commit_write`, then `mark_outbox_written`.

  At restart, a queued row whose file exists is marked written; otherwise it is rewritten. In P1a the only outbox message to Jim is the digest: a card reaches him by board, toast and digest (`docs/decisions.md`).
- **Receipts:** `read_receipt`, `seen_id`, `record_receipt` (build-spec section 3). A receipt saying the send failed goes to DEV with its error, and nothing is resent.
- **Quit means quit, until the next logon.** Tray Quit calls `launcher.record_quit` and `request_stop`. The 10-minute "ensure running" task then does nothing; the logon task and a start by hand clear it. If Jim leaves it off, the Apps Script watchdog's missing heartbeats tell Sulaiman, as they would for a PC that is off.
- **The tray's Send support bundle** commits to `control\support\<bundle_name>`, through `commit_write`.
- **Commands filled with it** (build-spec 6.7): `install-shortcut` makes `NG Runner.lnk` with AUMID `ICLA.NGRunner`; `release-all` exits 0 saying there are no leases; `setup`; `canary` (section 7). `tools/sign_pause.py` gives Sulaiman the signed pause files of build-spec section 3, with a Unix-second seq. Every new module joins `selftest.P1A_MODULES`, with `pythoncom`, `win32com` and `win32cred`.
- **Shutdown:** stop the scheduler between steps, then the board, then the tray, then `withdraw_board`. Nothing is left half-written: commit_write is all-or-nothing, and the store is transactional.

## 7. Shadow mode, the canary and the release build

Development happens on Sulaiman's PC; Jim's PC gets short test windows (`docs/testing/jims-pc.md`). Three things make that work.

- **Shadow mode** (`settings.mode`: `shadow` or `live`, default `shadow`).
  - Everything runs as live, except filing: `commit_write` targets `control\shadow\<folder>\<files>` instead of `NG Jobs\<folder>\`. The P1a write rules allow the control folder, so the chokepoint is exercised for real. These files hold client PDFs, so `Prune.gs` removes them after 30 days.
  - Outbox messages to anyone but Sulaiman are redirected to Sulaiman, marked SHADOW. The redirection happens before the lint, so he sees what would have gone out.
  - No toasts on Jim's PC: the immediate card kinds would pop up during an unattended window.
  - The board and tray say SHADOW.
  - The Apps Script's `SHADOW` Script Property is set for as long (build-spec 6.4), so a digest to Sulaiman counts and Jim gets no "no list today".
  - Switching to `live` is a deliberate settings change on the board (`BoardActions.set_mode`, on Health), logged as an event. Window 4 clears the Script Property with it.
  - A shadow decision is stored like a live one, so the daily comparison reads the store.
- **The canary:** `NGRunner.exe canary --folder <absolute path>` on Jim's PC, since the build has no Python (`python -m ngrunner canary --folder <absolute path>` from the repo). It runs `commit_write` with roots whose control root is a new test folder inside the given folder, and reports one plain line per check:
  - the no-follow create;
  - `fstat` and `lstat` give the same identity while the file is open;
  - a rename keeps the identity;
  - `fsync`;
  - rename and replace;
  - read-back;
  - a forced rollback (`commit_write` has no seam for one, so the canary wraps `os.rename` in process, as the tests do).

  At the end it removes only the files and folders it made, each checked by identity (a dev tool, not the chokepoint). It refuses a folder inside NG Jobs or Mozaik Shared, and any folder named like them, since it runs before settings exist. Exit code 0 only when every check passes.
- **The release build:**
  - `ngrunner.spec` builds the folder build;
  - `NGRunner.exe --self-test` passes inside it;
  - `install.ps1 -Root <scratch> -NoRegister` installs it into a scratch root on Sulaiman's PC;
  - a test kit zip holds the build, the Setup Check, `installer\` and `docs\testing\jims-pc.md`.

## 8. Acceptance: the day simulation (`tests/test_e2e_day.py`)

The setup is all fake, with a fake clock:
- a fake Studio server serving synthetic summaries (`tests/helpers_studio.py`, beside `helpers_pdf.summary_pdf`);
- a fake Render (export and sync);
- the control folder, NG Jobs and the data folder under `tmp_path`;
- the Apps Script's role played by the test, which writes signed exports and receipts, except where a scenario names the real scripts, run under node.

Scenarios, each with its assertions:

1. Install. Existing rows are `seen_at_install` and existing folders OBSERVED. Nothing is filed or sent. An exact-name row for an observed job writes nothing into its folder, and the board says why.
2. A bare surname row:
   - it waits one export;
   - the stale list asks Render for a push;
   - the fresh list matches the row;
   - two fetches 10 minutes apart agree;
   - the summary is filed as `<Name>\<Name>_summary.pdf` within 45 simulated minutes;
   - "Filed as" goes to Barbara;
   - the next 6:30 AM digest lists the job;
   - a heartbeat is written every 5 minutes.
3. An unclear row: a card and one email to Barbara. Her corrected name is matched and filed, and the card is withdrawn. Jim's card answer gives the same result on another run.
4. An early incomplete render followed by a complete one: filed after the complete pair agrees.
5. Garage-only designs give `<Name>_Gar\<Name>_Gar_summary.pdf`. Mixed closet and garage designs give `<Name>\<Name>_summary.pdf` with no card. A design that can't be classified raises the closet/garage card, and the answer files the job.
6. The folder already holds the same summary: marked filed with no write. A different summary raises the "IC changed" card, and nothing is overwritten. A job whose folder is in `NG Jobs\Old` follows the same rules, and no second folder appears at the top.
7. Paused: nothing is filed; heartbeats, the digest and messages go on. Resumed: the pipeline continues where it stopped.
8. Studio down for 24 h raises the card.
9. Receipts stop: the tray turns red. Tested both ways: with the Pushover keys, urgent messages go through `messages.pushover.send`; without them, they are logged and a DEV item opens.
10. A nightly re-fetch with changed content, confirmed twice, raises the card. No re-filing.
11. The night of the DST change: the night work runs once.
12. A restart mid-pipeline (a new supervisor on the same store) finishes without duplicate files, messages or cards.
13. Wrong-job property: across many generated rows and Opportunity lists, every filed summary belongs to the Opportunity the row was generated for, and every other row asks. "0 summaries filed to the wrong job" is the P1a exit criterion.
14. Shadow mode: scenario 2 again with `mode=shadow`.
    - The files land in `control\shadow\<Name>\`, and NG Jobs is untouched.
    - "Filed as" goes to Sulaiman only, marked SHADOW.
    - The board and tray say SHADOW, and no toast shows.
    - Switching to live files the next row for real.
15. A quiet night with the watchdog: `compose()` of a quiet night, through `outbox_file` and the real `Outbox.gs` and `Watchdog.gs`, counts as the morning's digest. No "No list today" and no "No list reached Jim".
16. A shadow morning: with `SHADOW` set and the digest sent to the alert role, the real scripts count it. Jim gets nothing, and the alert role gets no false "No list reached Jim".
17. A row typed with a date or a five-digit typo: every message about it passes the lint and quotes the row as typed, for Barbara and for the digest that brings its card back.
18. An empty export followed by a whole one: the first is held, and rows seen at install stay seen. No card, email or fetch starts.
19. A late lower-seq receipt: it still counts, so its message is not unreceipted and the tray stays as it was.
20. A new database on a used control folder: its messages and heartbeats start at the current Unix second, and the real scripts accept them.
21. A hand-filed portrait summary on a job fetching landscape: compared against a fetched portrait pair. The same content marks the job filed with no write and no "IC changed" card, and the job keeps portrait.
22. An answer to a which-job card after a restart and a new job list: it links the job the choice's ID names. An ID no longer in the list links nothing, the board says so, and the row stays `asked`.
