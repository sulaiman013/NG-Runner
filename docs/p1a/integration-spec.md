# P1a integration spec — wave 2

Wires the wave-1 slices into the running app. Read `build-spec.md` first; this file adds the flows, the job model, the supervisor and the acceptance scenarios. The design (local only) is the source of truth behind both.

## 1. The filing pipeline (`ngrunner/intake/pipeline.py`)

The supervisor calls `tick(ctx)` every 60 seconds; `ctx` holds the store, settings, secrets, clock, roots, the Render and Studio clients and the messenger. Every step is idempotent and keyed by store rows, so a crash or restart mid-way resumes without duplicates.

1. **Fab export.** Read `control\fab\export.json` (and `edits.json`). Accept it only per the envelope rules (build-spec section 3). Diff it against the last accepted export (kept in the store).
   - The first export after install marks every row `seen_at_install`; nothing starts (design section 5).
   - `new`: insert a `fab_rows` row with `first_seen` = now.
   - `edited`: update the text. A row in `new`, `waiting_list`, `asked` or `none` goes back to `new` (Barbara corrected the name). A row already `matched` or `filed` is re-matched in memory; if the result is another job, raise the card "Row changed: which job?" and file nothing new.
   - `gone`: mark `gone` and log it (rows move to Archive).
2. **Job list.** A `new` row needs a list with `synced_at >= first_seen`. If the stored list is older, ask Render for a push (at most once per 5 minutes) and set the row to `waiting_list`. Poll the export each tick until it is fresh. The alarm "job list is older than the oldest waiting row and over an hour old" goes to the board, the digest and DEV.
3. **Match.** `match_fab_row(FabRow(text, designer, first_seen), opportunities, ng_jobs, synced_at, sold_stages=settings.sold_stages)`. `ng_jobs` holds every Studio ID in `jobs` plus every NG Jobs folder name found by a read-only scan.
   - `matched`: create or link the `jobs` row (name not yet frozen, mode `auto`), set the row to `matched`, queue fetch 1 now.
   - `ambiguous`: card "Which job is this row?" for Jim, with the candidates and "None of these". One outbox email to Barbara listing the candidates (design: "When a name is unclear she gets one email listing the candidates"). The row goes to `asked`. Jim's answer links the job; Barbara's correction re-runs the match.
   - `none`: the same card and email, with no candidates ("type the full job name in the row").
   - `stale_list`: back to `waiting_list`.
4. **Fetch 1.** `fetch_render(FetchTarget(...))`, stored in `renders`, with the bytes in `AppPaths.renders\<studio_id>\<sha256>.pdf`.
   - Checked complete: queue fetch 2 at +10 min.
   - Incomplete (an early render): retry every 30 min.
   - Not found, network or timeout: retry with backoff (5, 15, 30, 60 min, then hourly).
   - Past 24 h from the first attempt: card "Could not get a complete IC summary for <Job>" (design 3.4: Fetch, 24 h, then a card).
5. **Fetch 2.** `agree(first, second)`. On `agree`, file. On `differ`, the second becomes the new first and fetch 2 is queued again. On incomplete, continue as step 4.
6. **File.**
   - The render's PROJECT NAME already equals the Opportunity Name (fetch enforces it). Freeze the job name now; later names become aliases.
   - `branch_of(designs, row_text)`: `closet` gives folder `<Name>`, `garage` gives `<Name>_Gar`. `unclear` raises the card "Closet or garage?"; only that job waits.
   - Look in `NG Jobs\<folder>\` (read-only).
     - A summary there, NG Runner's or hand-named (`match_file_to_job` with `folder=` and `opportunity_names=`), with the same fingerprint: mark `filed` with no write.
     - One with a different fingerprint: card "IC changed: which render governs?". Never overwrite.
   - Otherwise one `commit_write` of three new files: `<folder>_summary.pdf`, `<folder>_NGRunner.json` (section 4) and `<folder>_NGRunner_log.txt`, with the P1a interlock (section 2).
   - Record the commit in `writes`.
   - A refusal leaves the job BLOCKED with the reason, and is retried when that reason clears: paused, unreachable or "folder changed".
7. **Tell.**
   - Outbox email to Barbara: "Filed as <Job>".
   - An event for the digest and the board.
   - From filing on, a nightly re-fetch for 30 days. Content that changes, and is confirmed by two agreeing fetches, raises "IC changed: which render governs?". Nothing is ever re-filed by itself.

**Add job** (board button or `add job <name or Studio ID>`) enters the pipeline at step 3, with the typed text as an exact name, or at step 4 for a Studio ID found in the list.

## 2. The P1a interlock (`ngrunner/interlock.py`)

`p1a_interlock(ctx) -> Callable[[list[Path]], str | None]`. It returns a plain reason when:
- NG Runner is paused;
- the job is HELD;
- a target is outside NG Jobs and the control folder.

Otherwise it returns None. `commit_write` calls it twice. The Mozaik conditions of design section 5 join here in P1b and later, as P1a writes nothing a Mozaik job reads.

## 3. The write record and crash recovery (`writes.py`, owned by its fixer)

Replace `owned_files.json` with the store's `owned` table:
- `commit_write` gains a keyword `registry=`, an object with `owned(path) -> sha | None` and `record(mapping)`. The default stays the JSON registry until the supervisor passes the store's.
- **Before the first rename,** the store writes a `pending_writes` journal row: paths, new hashes, state `pending`.
- **After the read-back,** `record()` moves those hashes into `owned` and the row to `done`, in one transaction.
- **At start-up,** every `pending` row is settled:
  - each path that holds exactly its intended hash is adopted into `owned`;
  - every other path is left alone (never deleted);
  - the row is closed, and the case is logged to DEV.

  So a crash between the rename and the record never strands a file NG Runner can't overwrite later, and never adopts a file someone else changed.

## 4. Files in the job folder

`<folder>_NGRunner.json`, schema 1:

```json
{"schema": 1, "job": "<Name>", "branch": "closet",
 "summary": {"file": "<folder>_summary.pdf", "sha256": "...", "layout": "landscape",
             "pages": 19, "fetched": ["<ISO>", "<ISO>"]},
 "filed_at": "<ISO>", "items": [],
 "note": "NG Runner filed this IC summary. Chat sessions read this file before working the job."}
```

This is for machines (Jim's chat) and holds no address. `<folder>_NGRunner_log.txt` is for people. Each line has a Pacific time and one plain sentence, for example `10/30 3:05 PM  Filed the IC summary (landscape, 19 pages), checked on two fetches 10 minutes apart.`

## 5. The job model (`ngrunner/jobmodel.py`)

Pure functions over store facts (design 3.4: facts, clicks and pending work; no single state).

- **Ticks (P1a):**
  - Row ("on the Fab Schedule");
  - Job ("matched to Salesforce");
  - Summary ("fetched and checked twice");
  - Filed;
  - Branch ("closet" or "garage").

  Later phases add theirs; anything not built yet is `na`.
- **Overlays:** WAITING (resumes by itself: list, second fetch), BLOCKED (a reason and a one-line fix), NOT_RUN (a reason and a retry), HELD, OBSERVED (folders found at install), PAUSED.
- **Next step:** one plain line per job. Examples: "Waiting for a fresh job list", "Card ROD-1: which job is this row?", "Second fetch at 3:15 PM", "Filed 10/30 3:05 PM", "Card DIA-1: closet or garage?".
- **Your list:** open cards, pinned items and DEV items older than 48 h (with the manual fallback "work <Job> in chat as before").
- **Tray colour:**
  - grey when paused;
  - red when NG Runner is unwell: no receipt for 5 min, the store unreadable, or an urgent DEV item;
  - amber with the count of Your list;
  - green otherwise.
- **Digest view:**
  - owner items first: open cards with their age, jobs installing within 5 days with no summary filed, alarms;
  - then one line per job touched since the last digest;
  - then "Your list: N open, oldest D days";
  - a quiet night gets one line.

## 6. The supervisor (`ngrunner/supervisor.py`, `python -m ngrunner run`)

- **Start-up:**
  - single instance first;
  - load settings and secrets (a missing one is a plain board item, never a crash loop);
  - open and migrate the store;
  - settle pending writes;
  - start the board server, the tray and the scheduler thread;
  - catch up missed windows and name them ("Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM").
- **Schedule:**

  | Task | When |
  |---|---|
  | Pipeline tick | every 60 s |
  | Heartbeat | every 5 min |
  | Receipts check | every minute: a message with no receipt after 5 min turns the tray red, and urgent ones go to Pushover directly |
  | Digest | 6:30 AM Pacific, once per Pacific date |
  | Nightly work: backup, re-fetches, yesterday's log to the control folder, the toast-setting check, pruning NG Runner's own old backups | from 1:30 AM, once per night key |
  | Pause files | every minute |

- **Shutdown:** stop the scheduler between steps, then the board, then the tray. Nothing is left half-written: commit_write is all-or-nothing, and the store is transactional.

## 7. Acceptance: the day simulation (`tests/test_e2e_day.py`)

The setup is all fake, with a fake clock:
- a fake Studio server serving synthetic summaries;
- a fake Render (export and sync);
- the control folder, NG Jobs and the data folder under `tmp_path`;
- the Apps Script's role played by the test, which writes signed exports and receipts.

Scenarios, each with its assertions:

1. Install. Existing rows are `seen_at_install` and existing folders OBSERVED. Nothing is filed or sent.
2. A bare surname row:
   - the stale list asks Render for a push;
   - the fresh list matches the row;
   - two fetches 10 minutes apart agree;
   - the summary is filed as `<Name>\<Name>_summary.pdf` within 45 simulated minutes;
   - "Filed as" goes to Barbara;
   - the next 6:30 AM digest lists the job;
   - a heartbeat is written every 5 minutes.
3. An unclear row: a card and one email to Barbara. Her corrected name is matched and filed. Jim's card answer gives the same result on another run.
4. An early incomplete render followed by a complete one: filed after the complete pair agrees.
5. Garage designs give `<Name>_Gar\<Name>_Gar_summary.pdf`. Mixed designs raise the closet/garage card; the answer files the job.
6. The folder already holds the same summary: marked filed with no write. A different summary raises the "IC changed" card, and nothing is overwritten.
7. Paused: nothing is written. Resumed: the pipeline continues where it stopped.
8. Studio down for 24 h raises the card.
9. Receipts stop: the tray turns red, and urgent messages go to Pushover.
10. A nightly re-fetch with changed content, confirmed twice, raises the card. No re-filing.
11. The night of the DST change: the night work runs once.
12. A restart mid-pipeline (a new supervisor on the same store) finishes without duplicate files, messages or cards.
13. Wrong-job property: across many generated rows and Opportunity lists, every filed summary belongs to the Opportunity the row was generated for, and every other row asks. "0 summaries filed to the wrong job" is the P1a exit criterion.
