# P1a build spec — contracts for the builders

Status: working spec for building Phase 1a ("File it", design section 7: 10 dev-days). The design (`01-final-design.md`, local only) is the source of truth; this file fixes the interfaces so slices can be built in parallel and wired together without guessing. Where this file and the design disagree, the design wins and this file is corrected, unless `docs/decisions.md` records a ruling for P1a.

## 1. What P1a ships, and what already exists

Ships (design section 7): installer, launcher, tray, board, digest, heartbeat and receipts; Fab trigger, matcher, strict double fetch, completeness check, `_Gar` check, filing; `commit_write` for new files; test harness, runbook, backup; RULES lines and the skill update for Jim.

Exit criteria, over 10 working days on Jim's PC: every new row filed within 45 min or carded; 0 summaries filed to the wrong job; board right on 10 jobs Jim picks; 10/10 digests; a sleep test alerts Sulaiman within 50 min; 20/20 forbidden writes refused; restore drill passed.

Already built — call these, never re-implement them:

| Module | Use |
|---|---|
| `ngrunner.writes.commit_write(writes, *, roots, interlock, phase="P1a", owned=None)` | The only writer outside `%LOCALAPPDATA%\NGRunner` (rule 1). `interlock` is required |
| `ngrunner.match` | `match_fab_row(row, opportunities, ng_jobs, list_synced_at, *, sold_stages=...)`, `match_file_to_job(..., opportunity_names=...)` and `match_by_folder(..., opportunity_names=...)` (required in both), `room_words`, `typed_variant`, `normalize_job_file`, `slugify`, `Opportunity`, `FabRow`, `MatchResult` |
| `ngrunner.intake.summary_check` | `check_summary(source) -> SummaryCheck`, `content_fingerprint(source)` |
| `ngrunner.clock` | `Clock`, `SystemClock`, `FakeClock`, `to_iso`/`from_iso` (UTC), `fmt_clock`, `fmt_date`, `fmt_day`, `at_pacific`, `next_at`, `parse_wall`, `PACIFIC` |
| `ngrunner.signing` | `seal(kind, payload, *, key, seq, issued_at, id=None) -> bytes`, `open_envelope(data, *, key, kind=None) -> Envelope`, `EnvelopeError`, `new_key`, `key_from_text`. Test vector: `tests/vectors/envelope_v1.json` |
| `ngrunner.config` | `AppPaths` (data folder), `Settings`, `load_settings`, `save_settings`, `settings_from`, `roots`, `SettingsError` |

## 2. Facts the build relies on

**The NG Fab Schedule (read through the Drive connector, 2026-10-29; structure only).**
- Tabs: Dashboard (a summary view), **Sheet1** (the live list, about 110 rows), Requests (the Installer App's part requests), Archive (older rows, with an extra Start Date column first).
- Sheet1 header row: A blank; B `Job Name`; C `Designer`; D and E one merged `Install Date` header over two cells; F `Designed`, G `Opt`, H `Paperwork` (x marks); I `Fab Date`; J `Last touch`; K `Product`; L `Sale Cost`; M `Cost`; N `Delta`; O `Product Ordered`; P `Notes`.
- Install Date is split: D holds a weekday ("Wednesday") or a full date ("7/15/26"); E holds a short date with odd spacing (" 7 /1") or a full date.
- Rows have no IDs, are kept roughly in install-date order (new rows can be inserted anywhere), and finished rows are moved to Archive.
- Job Name forms seen: a bare surname (most rows); `Name_MMDDYY`; a surname plus an extra token (initials, a first name, a project tag run on like `Prj1`); a surname plus a room word; a date typo with five digits (`Name_MMDDY`). The matcher also reads a date typed another way (`Name 10/14/26`, `Name 10-14-2026`, `Name 2026-10-14`, `Name 101426`). Every message that quotes a row carries all of these as typed (section 6.5). Designer: two-letter initials, once a first name.
- NG Runner never edits the workbook (risk C3). Its Apps Script only reads it.

**NG Jobs naming.** Job folder `<Name_MMDDYY>`, or `<Name_MMDDYY>_Gar` for a garage job; the summary inside is `<folder name>_summary.pdf` (25 of 25 plain folders and both `_Gar` folders follow it). `commit_write`'s P1a rules allow exactly `<folder>_summary.pdf`, `<folder>_NGRunner.json`, `<folder>_NGRunner_log.txt` and the lease file inside a job folder.

**The job list.** Assembly Reports keeps the Salesforce Opportunity mirror; Render's copy is refreshed by the office PC's publisher (about every 11 min, or within a minute of a Sync request). Render can't reach Salesforce itself.

**Studio's project page (read with a Studio login, 2026-10-30).** Its Proposal tab holds the designs meant to be on the report, its Design tab every design on the project, and it links the job's exact report address. On 59 of 59 real sold jobs, the Proposal tab's designs equal the report's page 1, by name: 124 designs on each side, while those projects hold 202 in all and 27 jobs have designs outside the proposal. So the proposal, not the whole project, is the list to check. It doesn't catch a design the designer left off the proposal, or a missing drawing inside a design (AGENTS.md: Before filing goes live, item 8).

## 3. The control folder

A Drive folder outside the CNC operations tree, shared only by the account that runs the Apps Script (the one that owns the NG Fab Schedule), Sulaiman and, if Drive on Jim's PC uses another account, that one. Every file is a signed envelope (`ngrunner.signing`) unless the table says otherwise. NG Runner writes only new files here (through `commit_write`), and never overwrites or deletes, except in `shadow/`, where the rules of a job folder apply; the Apps Script replaces its own two `fab/` files in place and prunes old files elsewhere.

| Path | Written by | Kind | Notes |
|---|---|---|---|
| `fab/export.json` | Apps Script, every 5 min, replaced in place | `fab_export` | payload `{"sheet": "Sheet1", "exported_at": ISO, "rows": [{"sheet_row": int, "job": str, "designer": str, "install": [str, str], "fab_date": str}]}`; display values as shown (the reader normalises); read again next minute (see "Sequences and replay" for an unchanged or half-synced file) |
| `fab/edits.json` | Apps Script onEdit trigger, replaced in place (the last 500 edits) | `fab_edits` | payload `{"edits": [{"at": ISO, "sheet_row": int, "column": "job"\|"designer"\|"install", "old": str\|null, "new": str\|null}]}`; a hint for linking a corrected name to its row, never the only evidence |
| `heartbeat/<YYYYMMDD>/<seq>.json` | NG Runner, every 5 min | `heartbeat` | payload in section 6.5 |
| `outbox/<id>.json` | NG Runner | `outbox` | payload `{"channel": "email"|"slack"|"pushover", "to": role, "subject": str, "lines": [str], "urgent": bool, "ref": str}`; `to` is a role (`jim`, `barbara`, `sulaiman`, ...), resolved to an address by the Apps Script's own settings, so no address is ever written into a file |
| `receipts/<id>.json` | Apps Script | `receipt` | payload `{"outbox_id": str, "sent_at": ISO, "channel": str, "ok": bool, "error": str|null}` |
| `pause/<id>.json` | Sulaiman (`tools/sign_pause.py`, with a Unix-second seq) | `pause` | payload `{"reason": str}`; pausing is always safe, so a file is enough; resume only at the PC |
| `logs/<YYYYMMDD>.txt` | NG Runner, once a day | (plain text, not an envelope) | yesterday's log, job names only, never addresses |
| `backups/<YYYYMMDD>.ngrbak` | NG Runner, nightly | (encrypted, not an envelope) | section 6.7, `backup.py` |
| `shadow/<folder>/<files>` | NG Runner, in shadow mode | (the job folder's own files, not envelopes) | what it would have filed (integration spec section 7); they hold client PDFs, so `Prune.gs` removes them after 30 days |
| `support/<bundle_name>` | NG Runner, from the tray's Send support bundle | (a zip, not an envelope) | named `support-<date>-<time>.zip`; `Prune.gs` removes it after 30 days |

**Sequences and replay.** Each sender keeps one sequence per kind, increasing by 1. Every stream starts at the current Unix second, NG Runner's as well as the Apps Script's (`next_seq(db, stream, start=int(clock.now().timestamp()))`), so a new database on a used control folder is never ignored; a restore still jumps NG Runner's far ahead (`store.jump_after_restore`). A receiver accepts an envelope only if its signature verifies, its kind is the expected one, its seq is above the last it accepted for that stream, and its id is new; anything else is logged to DEV and ignored, except:
- **Receipts are exempt from the seq rule.** They reach the PC through Drive in any order, so a late one with a lower seq still counts: `seen_id` alone guards them, since `record_receipt` is already once-only. NG Runner looks up `receipts/<id>.json` for each outstanding row rather than listing the folder. `record_receipt` raises `LookupError` for a message the store doesn't hold (after a restore), and that receipt is ignored.
- **A Fab file read again with the same seq and id** is unchanged, not a replay, and is not logged.
- **A half-synced file** (its bytes don't parse yet) is read again next minute, and goes to DEV only if it is still unreadable 10 minutes later, two export intervals.

A Fab export whose `exported_at` is over 30 minutes old, or none at all, raises the "Fab export is over 30 minutes old" alarm.

## 4. The store (SQLite, `%LOCALAPPDATA%\NGRunner\ngrunner.db`)

Short-lived connection per operation (WAL, `busy_timeout` 5 s, foreign keys on), parameterised SQL only, all times stored as `to_iso()` text. Schema version in `PRAGMA user_version`, with ordered, idempotent migrations (an old database is upgraded; a newer one than the code knows is refused). A shipped version is never edited: a change is a new migration, appended. Version 2 adds `cards.choice_ids`, `fab_rows.text_changed_at`, `dev.studio_id`, `dev.urgent` and `renders.pypdf`; the migration after it adds `pending_writes` (wave 2). Tables (P1a, all versions):

| Table | Columns (types as SQLite affinity) | Notes |
|---|---|---|
| `jobs` | `studio_id` PK, `name` NOT NULL, `name_frozen` INT, `branch` (`closet`/`garage`/NULL), `designer`, `install_text`, `folder`, `mode` NOT NULL (`auto`/`observed`/`held`), `created_at`, `updated_at` | identity is `studio_id`; nothing sets `held` in P1a |
| `aliases` | `studio_id` FK, `name`, `seen_at`; PK (studio_id, name) | later names of a job |
| `fab_rows` | `id` PK, `first_seen` NOT NULL, `text_changed_at`, `text`, `designer`, `install_text`, `fab_date`, `original_text`, `sheet_row`, `state` NOT NULL, `studio_id` FK NULL, `reason`, `updated_at` | states: `seen_at_install`, `new`, `waiting_list`, `asked`, `matched`, `filed`, `none`, `gone`. `text_changed_at` is set when the row's text changes, and `to_match()` passes it as `FabRow.first_seen` (`first_seen` until then); `first_seen` and `original_text` stay as the record |
| `opportunities` | `studio_id` PK, `name`, `stage`, `owner`, `client` | replaced wholesale on each list fetch, never by an empty list or one with no `synced_at`; `client` comes from `replace_opportunities(..., clients=)`, and never holds an address |
| `list_state` | single row: `synced_at`, `fetched_at`, `job_count` | `synced_at` is Render's, the time the list was read |
| `renders` | `id` PK, `studio_id` FK, `fetched_at`, `url`, `sha256`, `page_count`, `creation_date`, `fingerprint`, `pypdf`, `layout`, `check_ok` INT, `missing` (JSON), `project_name`, `cache_path`, `governing` INT | one row per fetch. `pypdf` is the version that read the fingerprint: when it differs from the running one, the fingerprint is recomputed from `cache_path` |
| `pending` | `id` PK, `kind` NOT NULL, `studio_id`, `fab_row_id`, `due_at` NOT NULL, `deadline_at`, `attempts` INT, `state` (`open`/`done`/`failed`/`expired`), `note` | the pending operations of design 3.4 |
| `runs` | `id` PK, `studio_id`, `stage`, `trigger`, `started_at`, `ended_at`, `outcome`, `reason`, `app_version`, `read_hashes` (JSON), `written_hashes` (JSON) | |
| `cards` | `id` PK (`TES-2`), `studio_id`, `fab_row_id`, `kind`, `text`, `choices` (JSON), `choice_ids` (JSON), `default_choice`, `created_at`, `state` (`open`/`answered`/`withdrawn`), `answer`, `answer_words`, `answered_by`, `answered_at`, `channel`, `reminded_at` (JSON list) | IDs never reused. `choice_ids` is parallel to `choices`: a Studio ID, or null for "None of these" and "Keep <job>" |
| `card_numbers` | `prefix` PK, `last` INT | a restore jumps it by 1000, so no ID is issued twice |
| `events` | `id` PK, `at`, `studio_id`, `kind`, `text`, `lane` (`ACT`/`LOG`/`DEV`) | NG Runner's event log |
| `writes` | `id` PK, `at`, `ok` INT, `reason`, `paths` (JSON), `before` (JSON), `after` (JSON), `backups` (JSON) | one row per `commit_write` call; pruned nightly |
| `owned` | `path_key` PK, `sha256`, `at` | the SQLite write record that replaces `owned_files.json` (wired in wave 2); NG Jobs and `control\shadow\` paths only |
| `pending_writes` | `id` PK, `at`, `paths` (JSON), `hashes` (JSON), `state` (`pending`/`done`/`aborted`) | the write journal (integration spec section 3) |
| `outbox` | `id` PK, `seq` UNIQUE, `channel`, `recipient`, `subject`, `lines` (JSON), `urgent` INT, `ref`, `created_at`, `written_at`, `receipt_at`, `fallback_at`, `state` | |
| `seqs` | `stream` PK, `last` INT | outgoing sequences and the last accepted incoming ones |
| `seen_ids` | `stream`, `id`; PK (stream, id) | replay guard; pruned nightly |
| `dev` | `id` PK, `at`, `code`, `text`, `studio_id`, `urgent` INT, `state`, `told_jim_at` | the DEV lane. `studio_id` is the job it holds up, or NULL. `urgent` turns the tray red (integration spec section 5). One open item per code, text and job |
| `state` | `key` PK, `value` | paused, install time, last digest, toast setting... |

The store module exposes plain functions grouped by table (`ngrunner.store`), each opening its own connection, plus `Store.backup_to(path)` (SQLite online backup) and `Store.transaction()` for the few multi-table steps. Its docstrings are the contract for wave 2. `backup_to` has one caller, the supervisor's copy before a migration; `backup.snapshot`, which gives bytes with no temp file, serves everything else. Beyond the tables:
- `next_seq(db, stream, *, start=1)`: a new outgoing stream begins at `start`. The messenger and the heartbeat pass the current Unix second.
- `replace_opportunities(..., clients=None)` reads `clients.get(studio_id)`. Use keywords in any conversion: `Opportunity` is `(name, studio_id, owner, stage)` and `OpportunityRecord` is `(studio_id, name, stage, owner, client)`.
- `open_dev(..., studio_id=None, urgent=False)` de-duplicates on code, text and job.
- `jump_after_restore(db) -> None` jumps every outgoing stream by 1,000,000 and `card_numbers` by 1000, so `backup.for_a_new_pc` need not know the store's private names.
- `prune_seen_ids(db, *, keep_last: int = 5000) -> int`.

## 5. Slices and their files (wave 1)

Each builder edits only its own files. Tests go in `tests/test_<module>.py`. No network in tests (fake HTTP servers on 127.0.0.1 or injected callables), no writes outside `tmp_path`, synthetic names only (Testerson, Fakeley, Mockford, Dummitt...).

| Slice | Files |
|---|---|
| store | `ngrunner/store/` (`__init__.py`, `db.py`, `schema.py`, one module per table group), `ngrunner/secrets.py` |
| fetch | `ngrunner/intake/fetch.py`, `ngrunner/intake/branch.py` |
| joblist | `ngrunner/joblist.py`; in the Assembly Reports repo: the export endpoint, its config key and docs |
| fab | `ngrunner/fab.py`; `google/` (Apps Script + its node tests) |
| comms | `ngrunner/messages/` (`lint.py`, `templates.py`, `outbox.py`, `pushover.py`), `ngrunner/cards.py`, `ngrunner/digest.py`, `ngrunner/heartbeat.py` |
| ui | `ngrunner/board/` (Flask app, templates, static), `ngrunner/tray.py`, `ngrunner/toasts.py` |
| ops | `ngrunner/launcher.py`, `ngrunner/single.py`, `ngrunner/backup.py`, `ngrunner/support.py`, `ngrunner/selftest.py`, `installer/`, `ngrunner.spec`, `docs/p1a/runbook.md` |
| jimdocs | `docs/p1a/rules-lines.md`, `docs/p1a/skill-update.md` |

## 6. Contracts per slice

### 6.1 secrets (store slice)
`get(name) -> str | None`, `put(name, value)`, `remove(name)` over Windows Credential Manager (generic credentials, target `NGRunner/<name>`), and `MemorySecrets` for tests (same interface). Names in P1a: `signing_key`, `export_key`, `pushover_token`, `pushover_user`, `studio_username`, `studio_password` and `studio_session` (the JSON of Studio's two cookies, which `StudioSite` saves after a sign-in). Values are never logged, printed or put in exceptions.

### 6.2 fetch
- Port Assembly Reports' resolver (`app/studio.py` there: `slugify`, `candidate_slugs`, the `install_summary[_landscape]/<client>/<project>/<studio_id>/` forms, streaming download with timeouts and a size cap), made strict: the bytes start with `%PDF` and end with `%%EOF` (whitespace aside), and page 1's PROJECT NAME must be present and slug-equal to the job name. A missing label is refused (Assembly Reports accepts it; NG Runner must not).
- Assembly Reports' order first, then the forms it misses, which 4 of 60 real sold jobs needed (measured 2026-10-30): Studio keeps a hyphen in both slugs, and an account name can carry a street address, so every client word before the first one holding a digit is tried. At most 24 addresses; wrong ones 404 in a fraction of a second.
- `FetchTarget(studio_id, name, client, layout, known_url=None)`; `Fetched(ok, url, data, code, reason, status, check=None, found_name=None)` with `code` one of `ok`, `not_found`, `timeout`, `network`, `http_error`, `not_pdf`, `truncated`, `too_big`, `no_project_name`, `wrong_job`. `reason` is plain words and quotes no design name, so it may reach a card. `check` is set on `ok`, which doesn't mean complete: `check.ok` False is an early render. `found_name` is set on `wrong_job`: the name Studio's report carries, so the pipeline can tell a rename from a real mismatch (integration spec section 1).
- `fetch_render(target, *, http=None, timeout_s=240, max_bytes=50_000_000, base_url=STUDIO_URL) -> Fetched`; `http` is injected (a `requests.Session`-like object); the default makes one. Real fetches took 7 to 95 s, Assembly Reports gives Studio 240 s, and a timeout ends the address search at once, so a shorter limit would leave a slow job only the 24-hour card.
- `agree(first, second, *, min_gap=timedelta(minutes=10)) -> Agreement(ok, code, reason, fingerprint=None)`, over `Render(source, fetched_at, layout)`: both pass `check_summary`, their bytes differ, both are the same layout, their `content_fingerprint`s are equal, and the second was fetched at least `min_gap` after the first. The first rule that fails gives the code, in this order: `first_incomplete`, `second_incomplete`, `same_bytes`, `layouts_differ`, `differ`, `too_soon`; otherwise `agree`.
  - `same_bytes` means something served one render twice, a cache for example. All 6 real pairs measured differ in bytes, because Studio renders afresh, so identical bytes prove nothing about a stable render. The pipeline fetches again, and the 24-hour card bounds it.
  - `reason` can quote IC design names, so it goes to the event log and DEV only.
  - It raises `OSError` when a cached file can't be read.
- `branch.branch_of(designs, row_text=None) -> Branch(kind, reason)`, following Jim's BUILD.md Step 0 (lines 22-30): "A mixed summary is ONE job — never split it, never add `_Gar`".
  - `garage` (folder `<Name>_Gar`) when every page-1 design is a garage design by its name or category, whatever closet-side room word the row carries.
  - `closet` (no suffix) when none is, and also when the summary mixes closet and garage designs, with no card.
  - `unclear` only when the row or the Opportunity Name says garage or `_Gar` and no design carries a garage word, or when a design can't be classified: a blank category and no garage word in its name. That raises the closet/garage card (How-we-work 37-38), and only that job waits.
  - The row is read with `match.room_words()` and `match.typed_variant()`, never a copy of match's reading. The pipeline asks about the row's text and about the Opportunity Name alike.
  - `reason` reaches that card, so it never quotes a design's name (decision 10).
  - One closed word list, no fuzzy matching.
- **The design check,** `ngrunner/intake/studio_designs.py` (the flow is in integration spec section 1, step 4; the rulings are in `docs/decisions.md`):
  - `StudioSite(secrets, clock, *, http=None, sign_in=browser_sign_in, min_sign_in_gap=timedelta(hours=1))`, one per process, with `.project_page(studio_id) -> ProjectPage(proposal, all_designs, report_url)`. It only ever GETs `/project/details/`.
  - `check_designs(report_names, page) -> DesignCheck(code, missing_from_report, not_in_proposal)` takes `SummaryCheck.designs`, with `code` one of `match`, `report_missing_designs` (which wins when both sides lack one) or `proposal_missing_designs`. Names are normalised, each design and each card pair off once, and what is left over is missing. Designs outside the proposal don't count.
  - `project_page` raises `StudioUnavailable(code, reason)`, with `code` one of `no_login`, `sign_in_failed`, `network` or `page_changed`. `reason` is plain words: never a password, a cookie, a design name or an address.
  - `browser_sign_in(username, password) -> dict[str, str]`: Studio's own form doesn't work for this login, so sign-in goes through the community site in headless Microsoft Edge, and keeps Studio's two cookies. They let plain requests read project pages for 14 days, so the browser is rarely needed. A wrong password could lock the account, so `StudioSite` never signs in twice within `min_sign_in_gap`.
  - A `DesignCheck` holds counts only, and no `ProjectPage` repr shows a design, so no name reaches a card, a message or a DEV item.
  - When every derived address gives `not_found`, the pipeline tries `ProjectPage.report_url` once, as the job's `known_url`.

### 6.3 joblist
- Render, in the Assembly Reports repo: `GET /api/jobs/export`, guarded by header `X-Export-Key` against a new `ASSEMBLY_EXPORT_KEY` (constant-time compare; 403 when unset or wrong), returning `{"synced_at": ISO with offset or null, "job_count": n, "jobs": [{"studio_id", "name", "stage", "designer", "client", "created", "modified"}]}`. Never `email`, `opp_id` or `account_name`. Documented in that repo's AGENTS.md and README; tested with the Flask test client. Sulaiman pushes it himself, since pushing that repo deploys production (decision 18); it is pushed.
- NG Runner: `RenderClient(base_url, export_key, *, http)`, `.export() -> JobList(synced_at, fetched_at, opportunities: tuple[match.Opportunity, ...], clients, clock_offset)` (owner is the `designer` field), raising `JobListError` with plain words; `.request_push()` posts to `/api/sync` and never raises for a busy or odd reply. `fresh_enough(job_list, first_seen) -> bool` is `synced_at >= first_seen`.
- `JobList.clients` maps a Studio ID to its client, cut at the first word holding a digit, with a trailing hyphen stripped: client never holds an address, though an account name can carry one, so no address reaches the store or a backup. `intake.fetch` keeps `_client_words`, so its address rules still work on an uncut name. Assembly Reports' `_client_display_name` gets the same cut when Sulaiman ships the export.

### 6.4 fab
- `read_export(data, *, key) -> FabExport(seq, exported_at, rows, id)`; rows are `ExportRow(sheet_row, job, designer, install: tuple[str, str], fab_date)`; normalises whitespace inside the job text only for comparison, keeps the original for display. `read_edits(data, *, key) -> FabEdits(seq, edits, id)`.
- `diff(previous_rows, current_rows, edits=(), *, unchanged=False) -> list[RowChange]`, `RowChange(kind: "new" | "edited" | "gone" | "seen" | "same", row, previous)`. `diff(None, rows)` gives every row as `seen`, and is for the first export only; `unchanged=True` adds `same` pairs, so a caller can follow rows that moved. Exact (job, designer, install) matches are the same row, as a multiset; an unmatched current row with the same job text and the same designer or install as an unmatched previous row, or with the same designer and install at the nearest position, is an edit (an edit log entry naming old and new text settles it); leftovers are new or gone. Blank rows are ignored. A "gone" row starts no work (rows move to Archive): it is logged, and its open which-job card is withdrawn (integration spec section 1).
- `install_date(install, *, near) -> date | None`: reads the two Install Date cells ("Wednesday" + " 7 /1", "7/15/26"), picking the year nearest `near`; None when unreadable ("could not verify", never guessed).
- `google/`: `appsscript.json` (V8, time zone America/Los_Angeles), `Config.gs`, `Signing.gs` (HMAC-SHA256 over the body string, hex), `FabExport.gs` (time trigger, 5 min: Sheet1 display values for B, C, D, E, I with their row numbers; a sheet that returns no rows is skipped and logged, so the 30-minute alarm says so), `FabEdits.gs` (installable onEdit: B, C, D, E edits), `Outbox.gs` (time trigger, 1 min: verify, send by channel, write receipts, never resend an id), `Watchdog.gs` (time trigger, 5 min: heartbeat older than 45 min during expected-on hours alerts Sulaiman once per gap; no digest receipt by 7:00 AM Pacific sends Jim the "no list today" message of design section 4), `Prune.gs` (daily: files older than 30 days in `outbox/`, `receipts/`, `logs/`, `backups/`, `support/` and `shadow/`; heartbeats older than 2 days). Addresses and tokens live only in Script Properties. A node test harness (`google/test/`) loads the `.gs` files with mocked Google services and must reproduce `tests/vectors/envelope_v1.json`.
- Script Properties that change what it does (the rest are in `google/README.md`):
  - `ROLE_JIM_CHANNEL` defaults to `email`, since P1a has no push channel (`docs/decisions.md`).
  - `SHADOW`: while set, `Outbox.gs` counts a digest to the alert role as the morning's digest, `noListCheck_` sends to the alert role and never to Jim, and `checkSetup` does not require Jim's channel. Set for the shadow run, cleared at go-live (`docs/testing/jims-pc.md`).
  - `PC_SLEEPS_AT_NIGHT`: the declared "PC sleeps at night" mode, with no 7:00 AM message.

### 6.5 comms
- `messages.lint.lint(message) -> list[str]` for every message a person sees (design section 4 "Format rules"): items numbered `1.`, one line each; every item names its job (or is a known job-free line kind); times as `9:42 PM` (no `Z`, no ISO, no 24-hour); dates as `10/30` or `Fri 10/30`; no paths, hashes (hex runs of 12+), Studio IDs, attribute or table names, emails or phone numbers; sizes in inches with fractions, mm only in parentheses after inches; no intro or sign-off (`Hi`, `Hello`, `Thanks`, `Regards`, `Best`, `Cheers`, `Dear`); a clean check reads exactly `Nothing to fix — matches IC summary.` Templates are linted at test time; at runtime a failing message is replaced by its fixed per-code template and opens a DEV item.
  - Barbara's text is quoted as she typed it, in any row form of section 2. `check_line` masks the quoted text of `Fab row "..."` and `now reads "..."` before the date, number, attribute, verify and leak rules, and keeps the email, phone, path and hash rules on the raw text.
  - `JOB_NAME` also takes the shapes real folders have: `Aa_999999a`, `Aa_999999a9` and `Aa 999999`.
  - Fixed templates for Barbara name no board, since she has none.
- `messages.templates`: the P1a messages, each a function returning `Message(kind, title, lines, urgent)`: digest; quiet-night digest; "Filed as <Job>" (Barbara), with the job's design and page counts, for example "Filed as Testerson_101426: 3 designs, 19 pages"; "Which job is this row?" with candidates (Barbara's email and Jim's card); "Closet or garage?"; "Could not get a complete IC summary for <Job>" (fetch past 24 h); "IC changed: which render governs?"; "Is this a remake of <Job>?"; "<Job>: the report lists 3 designs and Studio's proposal lists 4.", counts only, never a design name, with the choices "Fetch it again" and "File it as it is"; DEV and "ran" lines for Sulaiman; urgent "NG Runner can't send messages" (no receipt in 5 min); the catch-up line ("Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM"). Every template has an example in `selftest.message_samples`.
  - `ALARMS["no_receipts"]` and `cant_send` are worded without a phone, since P1a has no push channel.
  - DEV lines are built from `lint.redact(text)`, and each is vetted: only a failing line is replaced, by its code. `FIXED['dev']` points at no board view, since the board has none.
  - A message redirected in shadow mode has a "SHADOW" title form, for example `SHADOW Filed as Testerson_101426`.
  - `card_subject(card)` gives what a card is about: its job, or its Fab row when it has no job yet.
- `cards`: `card_prefix(job_name)` (first three letters, uppercase: `TES`); `Card` model; `CardSpec` and `Card` carry `choice_ids: tuple[str | None, ...]`, parallel to `choices`: a Studio ID, or None for "None of these" and "Keep <job>". The which-job and row-changed templates fill it from their candidates. `CARD_KINDS` is `which_job`, `closet_or_garage`, `fetch_failed`, `ic_changed`, `row_changed`, `remake` and `design_mismatch`, each with its `QUESTIONS` and `_CARD_BACK` entries; a remake's digest line asks whether it is a remake. `parse_command(text, open_cards) -> Command | Rejection`, strict: `<CARD-ID> <choice number or label>`, `pause`, `resume`, `add job <name or Studio ID>`; anything else is a `Rejection` whose text is exactly `I didn't understand. Try: <example>` with a real open card when one exists. Answers are stored verbatim.
- `digest.compose(view) -> Message` from a `DigestView` (owner items first, then one line per job touched, a filed job's line with its design and page counts, then open cards with their age at 1, 3 and 7 days; a quiet night gets one line).
- `heartbeat.payload(...)` -> `{"app_version", "paused", "open_risks": [str], "last_fab_seq", "list_synced_at", "pending": int, "dev_open": int}`.
- `messages.outbox`: `outbox_file(message, *, to, channel, key, seq, now, ref=None, id=None) -> OutboxFile(path_parts, data)` and `read_receipt(data, *, key) -> Receipt`; the actual write is `commit_write`, injected (wave 2 wires it).
  - `ref` defaults to the message's kind, except that kinds `digest` and `quiet_digest` default to `digest:<Pacific YYYY-MM-DD of now>`: the Apps Script knows a digest only by a ref starting `digest`.
  - `id=` lets a queued row be written again with its own id and seq: rebuild it as `Message('outbox', rec.subject, rec.lines, rec.urgent)` with `id=rec.id, seq=rec.seq, ref=rec.ref`.
- `messages.pushover.send(token, user, title, text, *, urgent, http) -> bool` (direct path for urgent items when receipts stop, only once both Pushover secrets exist).

### 6.6 ui
- Board: `create_board(views, actions, *, token) -> Flask`, served by waitress on `127.0.0.1` at an OS-chosen port in a daemon thread (`BoardServer.start() -> url`). Security: a per-launch token (`secrets.token_urlsafe(32)`); the first GET with `?t=<token>` sets an HttpOnly, SameSite=Strict cookie; every POST needs the token (header or form) and exact `Origin` and `Host` (`127.0.0.1:<port>`); nothing changes on a GET; CSP `default-src 'self'` with per-request nonces, no external resources, no inline handlers. "Open file" opens only paths inside the configured roots.
- Pages: Board (one row per job: ticks, next step, install date, overlay), Your list (items for Jim), Cards (buttons plus the command box), History (events), Health (paths, last export, list age, heartbeat, toasts on or off, shadow or live). Plain words, Assembly Reports' DESIGN.md tokens and status style (icon + label + colour). In shadow mode `base.html` shows a banner on every page.
- View models (wave 2 fills them from the job model): `BoardView(jobs, your_list, cards, health, tray, paused, shadow)`, `JobRow(studio_id: str | None, name, install, ticks: tuple[Tick, ...], next_step, overlay)` (a row with no job yet is a board row), `Tick(key, label, state: "done" | "todo" | "blocked" | "na")`, `CardView(id, job: str, text, choices, age_days)` (it refuses a job that isn't text, so `None` never shows: wave 2 passes `templates.card_subject(card)`), `TrayState(colour: "green" | "amber" | "red" | "grey", count, tooltip)` (the tooltip starts SHADOW in shadow mode).
- `BoardActions.set_mode(mode) -> str`, behind a POST route with the guards above, shown on Health: the deliberate switch between shadow and live.
- `ngrunner.config`, `Settings`:
  - `mode`: `shadow` by default, or `live`, validated.
  - `phone` defaults to `none`, since P1a has no push channel (`docs/decisions.md`).
  - `backup_public_key`: blank means the nightly backup is a board item, not a crash.
  - No `emails` field: no address is ever written into a file (section 3), and nothing read them.
  - `load_settings` sets unknown keys aside with a note the supervisor can pass to DEV (`Settings.set_aside_note()`), so a fallback to an older version never refuses a newer `settings.json`.
- Tray (pystray, own thread): colour per `TrayState`, count badge, menu: Open board, Pause or Resume, Check for new rows now, Send support bundle, Quit (asks first). Toasts (windows-toasts, AUMID `ICLA.NGRunner`): immediate kinds at once, others at most one per 10 min; `toasts_enabled() -> bool | None` reads the Windows notification setting (None when it can't be read: "could not verify").

### 6.7 ops
- The command line, `ngrunner/__main__.py` (`NGRunner.exe` in the build, which has no Python; `python -m ngrunner` from the repo):
  - From wave 1: `--version`, `--self-test [--record]`, `restore BACKUP [--install]`, `support-bundle`, `keygen`.
  - Filled in wave 2: `run [--attempt ID] [--open-board]`, the default; `install-shortcut --target EXE`; `release-all`.
  - Added in wave 2: `canary --folder <absolute path>` (integration spec section 7) and `setup`, which writes `settings.json` and puts the secrets in Credential Manager, the Studio login (`studio_username`, `studio_password`) among them, asking plainly, showing no secret and checking the folders.
  - Exit codes: 0 done, 1 a problem (the printed lines say what), 2 a wrong command line or no data folder, and for `canary` a folder it refuses.
  - `NGRunnerLauncher.exe [--logon | --ensure]`: 0 running or left off on purpose, 1 nothing could start, 2 not set up.
- `single.py`: a named mutex `Local\NGRunner.Supervisor`; a second start opens the running board through `open_running_board`, which reads what `publish_board` left in the data folder, then exits 0. That file never enters the support bundle or a log, and the board's address never goes to stdout, since `app-output.log` does enter the bundle.
- `launcher.py`: versions in `%LOCALAPPDATA%\Programs\NGRunner\app-<ver>\`; state in `AppPaths.launcher_state`; starts the newest version whose self-test passed; two failed starts fall back to the previous good version and leave a DEV note for it to send. After `record_quit` the 10-minute task does nothing (`stopped_by_user`); the logon task and a start by hand clear it.
- `selftest.py`: `run_self_test(paths) -> list[str]` (imports of `P1A_MODULES`, a migration on a copy of the database, the forbidden-writes table, template lint over `message_samples()`); exit code 0 only when empty.
- `installer/`: `install.ps1 [-Root] [-Build] [-ManifestSha256] [-NoRegister] [-NoStart]` and `uninstall.ps1 [-Root] [-Version] [-NoRegister]` (per user, no admin: copy the build, Start Menu shortcut with the AUMID, both scheduled tasks from XML, a Run-key fallback, the self-test; uninstall keeps the data folder and has no switch to remove it), `tasks/NGRunner-logon.xml` and `tasks/NGRunner-ensure.xml` (every 10 min; priority 4, no time limit, IgnoreNew, battery conditions off). Inno Setup waits for its licence question; this route needs no installer licence.
- `ngrunner.spec`: PyInstaller 6.22, folder build, no UPX, UTF-8 mode. It must carry Playwright, which drives Edge for Studio's sign-in: without it every sign-in fails, with "The browser tool for signing in is not installed." Not yet checked in a build.
- `backup.py`: `nightly_backup(paths, public_key, *, clock) -> Backup(folder, name, data)`, a SQLite online backup through `snapshot(db) -> bytes`, encrypted to an ICLA-held X25519 public key (ephemeral X25519, HKDF-SHA256, AES-256-GCM, a versioned header), for `commit_write` into `backups/`; `restore(data, private_key) -> bytes` for the drill; `for_a_new_pc(db) -> bytes`, which calls `store.jump_after_restore`; `prune(...)` removes NG Runner's own `AppPaths.backup` copies after 60 days.
- `support.py`: `make_bundle(paths, *, clock, ...) -> bytes`, a support bundle (versions, settings without secrets, schema version, counts, recent logs), named by `bundle_name(now)`. The tray's Send support bundle commits it to `control\support\<bundle_name>`.
- `docs/p1a/runbook.md`: start, stop, pause; falling back to chat; every P1a alarm with its one-line fix; restore onto a new PC; update and rollback; key rotation; deleting fixtures.

### 6.8 jimdocs
Drafts for Jim to approve through his RULES section 8 process: the RULES.md lines P1a needs (NG Runner files the summary into `NG Jobs\<Job>\`; chat reads `<Job>_NGRunner.json` before working a job; if NG Runner is down, work in chat as before) and the skill-text change (take the summary from the NG Jobs folder instead of an upload). Written in his style, each change quoting the exact line it replaces with `file:line`, no client names.

## 7. Wave plan

1. **Wave 1 (parallel):** the eight slices above, each test-first, with fakes for its neighbours.
   - **The reconcile step, before wave 2:** each slice's owner closes what a fit check of the parts found. The rulings are in `docs/decisions.md`, and this spec and the integration spec stand as they settle it.
2. **Wave 2 (integration):** the intake pipeline (Fab change → match → fetch twice → check → branch → file → messages), the P1a interlock, the job model (facts, clicks, pending work → board rows, tray colour, digest lines), the supervisor (single instance, scheduler, tray and board wiring, pause, alarms), the messenger, the canary, the `setup` command, `tools/sign_pause.py`, the SQLite write record inside `writes.py`, and an end-to-end day simulation on a fake clock.
3. **Wave 3 (verification):** first the final rulings (`docs/decisions.md`): the Studio design check, the counts a person can check and the new card kinds. Then adversarial lenses on wrong-job filing, writes in integration, board and control-folder security, robustness (restarts, DST, offline, sleep), Jim-facing wording, and packaging (PyInstaller build, install script, self-test).
