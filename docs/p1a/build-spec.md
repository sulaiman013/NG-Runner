# P1a build spec — contracts for the builders

Status: working spec for building Phase 1a ("File it", design section 7: 10 dev-days). The design (`01-final-design.md`, local only) is the source of truth; this file fixes the interfaces so slices can be built in parallel and wired together without guessing. Where this file and the design disagree, the design wins and this file is corrected.

## 1. What P1a ships, and what already exists

Ships (design section 7): installer, launcher, tray, board, digest, heartbeat and receipts; Fab trigger, matcher, strict double fetch, completeness check, `_Gar` check, filing; `commit_write` for new files; test harness, runbook, backup; RULES lines and the skill update for Jim.

Exit criteria, over 10 working days on Jim's PC: every new row filed within 45 min or carded; 0 summaries filed to the wrong job; board right on 10 jobs Jim picks; 10/10 digests; a sleep test alerts Sulaiman within 50 min; 20/20 forbidden writes refused; restore drill passed.

Already built — call these, never re-implement them:

| Module | Use |
|---|---|
| `ngrunner.writes.commit_write(writes, *, roots, interlock, phase="P1a", owned=None)` | The only writer outside `%LOCALAPPDATA%\NGRunner` (rule 1). `interlock` is required |
| `ngrunner.match` | `match_fab_row`, `match_file_to_job(..., opportunity_names=...)` (required), `match_by_folder`, `normalize_job_file`, `slugify`, `Opportunity`, `FabRow`, `MatchResult` |
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
- Job Name forms seen: a bare surname (most rows); `Name_MMDDYY`; a surname plus an extra token (initials, a first name, a project tag run on like `Prj1`); a surname plus a room word; a date typo with five digits. Designer: two-letter initials, once a first name.
- NG Runner never edits the workbook (risk C3). Its Apps Script only reads it.

**NG Jobs naming.** Job folder `<Name_MMDDYY>`, or `<Name_MMDDYY>_Gar` for a garage job; the summary inside is `<folder name>_summary.pdf` (25 of 25 plain folders and both `_Gar` folders follow it). `commit_write`'s P1a rules allow exactly `<folder>_summary.pdf`, `<folder>_NGRunner.json`, `<folder>_NGRunner_log.txt` and the lease file inside a job folder.

**The job list.** Assembly Reports keeps the Salesforce Opportunity mirror; Render's copy is refreshed by the office PC's publisher (about every 11 min, or within a minute of a Sync request). Render can't reach Salesforce itself.

## 3. The control folder

A Drive folder outside the CNC operations tree, shared only by Jim's account, the ICLA automation account (Apps Script) and Sulaiman. Every file is a signed envelope (`ngrunner.signing`). NG Runner writes only new files here (through `commit_write`), never overwrites or deletes; the Apps Script replaces its own two `fab/` files in place and prunes old files elsewhere.

| Path | Written by | Kind | Notes |
|---|---|---|---|
| `fab/export.json` | Apps Script, every 5 min, replaced in place | `fab_export` | payload `{"sheet": "Sheet1", "exported_at": ISO, "rows": [{"sheet_row": int, "job": str, "designer": str, "install": [str, str], "fab_date": str}]}`; display values as shown (the reader normalises); a half-synced or unchanged file is simply read again next minute |
| `fab/edits.json` | Apps Script onEdit trigger, replaced in place (the last 500 edits) | `fab_edits` | payload `{"edits": [{"at": ISO, "sheet_row": int, "column": "job"\|"designer"\|"install", "old": str\|null, "new": str\|null}]}`; a hint for linking a corrected name to its row, never the only evidence |
| `heartbeat/<YYYYMMDD>/<seq>.json` | NG Runner, every 5 min | `heartbeat` | payload in section 6.5 |
| `outbox/<id>.json` | NG Runner | `outbox` | payload `{"channel": "email"|"slack"|"pushover", "to": role, "subject": str, "lines": [str], "urgent": bool, "ref": str}`; `to` is a role (`jim`, `barbara`, `sulaiman`, ...), resolved to an address by the Apps Script's own settings, so no address is ever written into a file |
| `receipts/<id>.json` | Apps Script | `receipt` | payload `{"outbox_id": str, "sent_at": ISO, "channel": str, "ok": bool, "error": str|null}` |
| `pause/<id>.json` | Sulaiman (a small signing script) | `pause` | payload `{"reason": str}`; pausing is always safe, so a file is enough; resume only at the PC |
| `logs/<YYYYMMDD>.txt` | NG Runner, once a day | (plain text, not an envelope) | yesterday's log, job names only, never addresses |
| `backups/<YYYYMMDD>.ngrbak` | NG Runner, nightly | (encrypted, not an envelope) | section 6.8 |

**Sequences and replay.** Each sender keeps one sequence per kind, increasing by 1. A receiver accepts an envelope only if its signature verifies, its kind is the expected one, its seq is above the last it accepted for that stream, and its id is new; anything else is logged to DEV and ignored. A Fab export older than 30 minutes, or none at all, raises the "Fab export is over 30 minutes old" alarm.

## 4. The store (SQLite, `%LOCALAPPDATA%\NGRunner\ngrunner.db`)

Short-lived connection per operation (WAL, `busy_timeout` 5 s, foreign keys on), parameterised SQL only, all times stored as `to_iso()` text. Schema version in `PRAGMA user_version`, with ordered, idempotent migrations (an old database is upgraded; a newer one than the code knows is refused). Tables (P1a):

| Table | Columns (types as SQLite affinity) | Notes |
|---|---|---|
| `jobs` | `studio_id` PK, `name` NOT NULL, `name_frozen` INT, `branch` (`closet`/`garage`/NULL), `designer`, `install_text`, `folder`, `mode` NOT NULL (`auto`/`observed`/`held`), `created_at`, `updated_at` | identity is `studio_id` |
| `aliases` | `studio_id` FK, `name`, `seen_at`; PK (studio_id, name) | later names of a job |
| `fab_rows` | `id` PK, `first_seen` NOT NULL, `text`, `designer`, `install_text`, `fab_date`, `original_text`, `sheet_row`, `state` NOT NULL, `studio_id` FK NULL, `reason`, `updated_at` | states: `seen_at_install`, `new`, `waiting_list`, `asked`, `matched`, `filed`, `none`, `gone` |
| `opportunities` | `studio_id` PK, `name`, `stage`, `owner`, `client` | replaced wholesale on each list fetch |
| `list_state` | single row: `synced_at`, `fetched_at`, `job_count` | `synced_at` is Render's, the time the list was read |
| `renders` | `id` PK, `studio_id` FK, `fetched_at`, `url`, `sha256`, `page_count`, `creation_date`, `fingerprint`, `layout`, `check_ok` INT, `missing` (JSON), `project_name`, `cache_path`, `governing` INT | one row per fetch |
| `pending` | `id` PK, `kind` NOT NULL, `studio_id`, `fab_row_id`, `due_at` NOT NULL, `deadline_at`, `attempts` INT, `state` (`open`/`done`/`failed`/`expired`), `note` | the pending operations of design 3.4 |
| `runs` | `id` PK, `studio_id`, `stage`, `trigger`, `started_at`, `ended_at`, `outcome`, `reason`, `app_version`, `read_hashes` (JSON), `written_hashes` (JSON) | |
| `cards` | `id` PK (`ROD-2`), `studio_id`, `fab_row_id`, `kind`, `text`, `choices` (JSON), `default_choice`, `created_at`, `state` (`open`/`answered`/`withdrawn`), `answer`, `answer_words`, `answered_by`, `answered_at`, `channel`, `reminded_at` (JSON list) | IDs never reused |
| `card_numbers` | `prefix` PK, `last` INT | |
| `events` | `id` PK, `at`, `studio_id`, `kind`, `text`, `lane` (`ACT`/`LOG`/`DEV`) | NG Runner's event log |
| `writes` | `id` PK, `at`, `ok` INT, `reason`, `paths` (JSON), `before` (JSON), `after` (JSON), `backups` (JSON) | one row per `commit_write` call |
| `owned` | `path_key` PK, `sha256`, `at` | the SQLite write record that replaces `owned_files.json` (wired in wave 2) |
| `outbox` | `id` PK, `seq` UNIQUE, `channel`, `recipient`, `subject`, `lines` (JSON), `urgent` INT, `ref`, `created_at`, `written_at`, `receipt_at`, `fallback_at`, `state` | |
| `seqs` | `stream` PK, `last` INT | outgoing sequences and the last accepted incoming ones |
| `seen_ids` | `stream`, `id`; PK (stream, id) | replay guard |
| `dev` | `id` PK, `at`, `code`, `text`, `state`, `told_jim_at` | the DEV lane |
| `state` | `key` PK, `value` | paused, install time, last digest, toast setting... |

The store module exposes plain functions grouped by table (`ngrunner.store`), each opening its own connection, plus `Store.backup_to(path)` (SQLite online backup) and `Store.transaction()` for the few multi-table steps. Its docstrings are the contract for wave 2.

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
`get(name) -> str | None`, `put(name, value)`, `remove(name)` over Windows Credential Manager (generic credentials, target `NGRunner/<name>`), and `MemorySecrets` for tests (same interface). Names in P1a: `signing_key`, `export_key`, `pushover_token`, `pushover_user`. Values are never logged, printed or put in exceptions.

### 6.2 fetch
- Port Assembly Reports' resolver (`app/studio.py` there: `slugify`, `candidate_slugs`, the `install_summary[_landscape]/<client>/<project>/<studio_id>/` forms, streaming download with timeouts and a size cap), made strict: the bytes start with `%PDF` and end with `%%EOF` (whitespace aside), and page 1's PROJECT NAME must be present and slug-equal to the job name. A missing label is refused (Assembly Reports accepts it; NG Runner must not).
- Assembly Reports' order first, then the forms it misses, which 4 of 60 real sold jobs needed (measured 2026-10-30): Studio keeps a hyphen in both slugs, and an account name can carry a street address, so every client word before the first one holding a digit is tried. At most 24 addresses; wrong ones 404 in a fraction of a second.
- `FetchTarget(studio_id, name, client, layout, known_url=None)`; `Fetched(ok, url, data, code, reason, status)` with `code` one of `ok`, `not_found`, `timeout`, `network`, `http_error`, `not_pdf`, `truncated`, `too_big`, `no_project_name`, `wrong_job`; `reason` is plain words.
- `fetch_render(target, *, http, timeout_s=60, max_bytes=50_000_000) -> Fetched`; `http` is injected (a `requests.Session`-like object); the default makes one.
- `agree(first, second, *, min_gap=timedelta(minutes=10)) -> Agreement(ok, code, reason)`: both pass `check_summary`, both are the same layout, their `content_fingerprint`s are equal, and the second was fetched at least `min_gap` after the first. Codes: `agree`, `differ`, `first_incomplete`, `second_incomplete`, `layouts_differ`, `too_soon`.
- `branch.branch_of(designs, row_text=None) -> Branch(kind, reason)`, following Jim's BUILD.md Step 0 (lines 22-30): "A mixed summary is ONE job — never split it, never add `_Gar`".
  - `garage` (folder `<Name>_Gar`) only when every page-1 design is a garage design by its name or category.
  - `closet` (no suffix) when none is, and also when the summary mixes closet and garage designs.
  - `unclear` only when a design can't be classified at all, or when the row says garage and no design does. That raises the closet/garage card (How-we-work 37-38), and only that job waits.
  - One closed word list, no fuzzy matching.

### 6.3 joblist
- Render, in the Assembly Reports repo: `GET /api/jobs/export`, guarded by header `X-Export-Key` against a new `ASSEMBLY_EXPORT_KEY` (constant-time compare; 403 when unset or wrong), returning `{"synced_at": ISO with offset or null, "job_count": n, "jobs": [{"studio_id", "name", "stage", "designer", "client", "created", "modified"}]}`. Never `email`, `opp_id` or `account_name`. Documented in that repo's AGENTS.md and README; tested with the Flask test client. **Not committed or pushed** (pushing that repo deploys production; Sulaiman pushes it himself, decision 18).
- NG Runner: `RenderClient(base_url, export_key, *, http)`, `.export() -> JobList(synced_at, fetched_at, opportunities: tuple[match.Opportunity, ...])` (owner is the `designer` field), raising `JobListError` with plain words; `.request_push()` posts to `/api/sync` and never raises for a busy or odd reply. `fresh_enough(job_list, first_seen) -> bool` is `synced_at >= first_seen`.

### 6.4 fab
- `read_export(data, *, key) -> FabExport(seq, exported_at, rows)`; rows are `ExportRow(sheet_row, job, designer, install: tuple[str, str], fab_date)`; normalises whitespace inside the job text only for comparison, keeps the original for display.
- `diff(previous_rows, current_rows, edits=()) -> list[RowChange]`, `RowChange(kind: "new" | "edited" | "gone", row, previous)`. Exact (job, designer, install) matches are the same row, as a multiset; an unmatched current row with the same designer and install as an unmatched previous row, at the nearest position, is an edit (an edit log entry naming old and new text settles it); leftovers are new or gone. Blank rows are ignored. "gone" is logged, never acted on (rows move to Archive).
- `install_date(install, *, near) -> date | None`: reads the two Install Date cells ("Wednesday" + " 7 /1", "7/15/26"), picking the year nearest `near`; None when unreadable ("could not verify", never guessed).
- `google/`: `appsscript.json` (V8, time zone America/Los_Angeles), `Config.gs`, `Signing.gs` (HMAC-SHA256 over the body string, hex), `FabExport.gs` (time trigger, 5 min: Sheet1 display values for B, C, D, E, I with their row numbers), `FabEdits.gs` (installable onEdit: B, C, D, E edits), `Outbox.gs` (time trigger, 1 min: verify, send by channel, write receipts, never resend an id), `Watchdog.gs` (time trigger, 5 min: heartbeat older than 45 min during expected-on hours alerts Sulaiman once per gap; no digest receipt by 7:00 AM Pacific sends Jim the "no list today" message of design section 4), `Prune.gs` (daily: files older than 30 days in `heartbeat/`, `outbox/`, `receipts/`, `logs/` and `backups/`; heartbeats older than 2 days). Addresses and tokens live only in Script Properties. A node test harness (`google/test/`) loads the `.gs` files with mocked Google services and must reproduce `tests/vectors/envelope_v1.json`.

### 6.5 comms
- `messages.lint.lint(message) -> list[str]` for every message a person sees (design section 4 "Format rules"): items numbered `1.`, one line each; every item names its job (or is a known job-free line kind); times as `9:42 PM` (no `Z`, no ISO, no 24-hour); dates as `10/30` or `Fri 10/30`; no paths, hashes (hex runs of 12+), Studio IDs, attribute or table names, emails or phone numbers; sizes in inches with fractions, mm only in parentheses after inches; no intro or sign-off (`Hi`, `Hello`, `Thanks`, `Regards`, `Best`, `Cheers`, `Dear`); a clean check reads exactly `Nothing to fix — matches IC summary.` Templates are linted at test time; at runtime a failing message is replaced by its fixed per-code template and opens a DEV item.
- `messages.templates`: the P1a messages, each a function returning `Message(kind, title, lines, urgent)`: digest; quiet-night digest; "Filed as <Job>" (Barbara); "Which job is this row?" with candidates (Barbara's email and Jim's card); "Closet or garage?"; "Could not get a complete IC summary for <Job>" (fetch past 24 h); "IC changed: which render governs?"; DEV and "ran" lines for Sulaiman; urgent "NG Runner can't send messages" (no receipt in 5 min).
- `cards`: `card_prefix(job_name)` (first three letters, uppercase: `ROD`); `Card` model; `parse_command(text, open_cards) -> Command | Rejection`, strict: `<CARD-ID> <choice number or label>`, `pause`, `resume`, `add job <name or Studio ID>`; anything else is a `Rejection` whose text is exactly `I didn't understand. Try: <example>` with a real open card when one exists. Answers are stored verbatim.
- `digest.compose(view) -> Message` from a `DigestView` (owner items first, then one line per job touched, then open cards with their age at 1, 3 and 7 days; a quiet night gets one line).
- `heartbeat.payload(...)` -> `{"app_version", "paused", "open_risks": [str], "last_fab_seq", "list_synced_at", "pending": int, "dev_open": int}`.
- `messages.outbox`: `outbox_file(message, *, to, channel, key, seq, now) -> (path_parts, bytes)` and `read_receipt(data, *, key) -> Receipt`; the actual write is `commit_write`, injected (wave 2 wires it).
- `messages.pushover.send(token, user, title, text, *, urgent, http) -> bool` (direct path for urgent items when receipts stop).

### 6.6 ui
- Board: `create_board(views, actions, *, token) -> Flask`, served by waitress on `127.0.0.1` at an OS-chosen port in a daemon thread (`BoardServer.start() -> url`). Security: a per-launch token (`secrets.token_urlsafe(32)`); the first GET with `?t=<token>` sets an HttpOnly, SameSite=Strict cookie; every POST needs the token (header or form) and exact `Origin` and `Host` (`127.0.0.1:<port>`); nothing changes on a GET; CSP `default-src 'self'` with per-request nonces, no external resources, no inline handlers. "Open file" opens only paths inside the configured roots.
- Pages: Board (one row per job: ticks, next step, install date, overlay), Your list (items for Jim), Cards (buttons plus the command box), History (events), Health (paths, last export, list age, heartbeat, toasts on or off). Plain words, Assembly Reports' DESIGN.md tokens and status style (icon + label + colour).
- View models (wave 2 fills them from the job model): `BoardView(jobs, your_list, cards, health, tray, paused)`, `JobRow(studio_id, name, install, ticks: tuple[Tick, ...], next_step, overlay)`, `Tick(key, label, state: "done" | "todo" | "blocked" | "na")`, `CardView(id, job, text, choices, age_days)`, `TrayState(colour: "green" | "amber" | "red" | "grey", count, tooltip)`.
- Tray (pystray, own thread): colour per `TrayState`, count badge, menu: Open board, Pause or Resume, Check for new rows now, Send support bundle, Quit (asks first). Toasts (windows-toasts, AUMID `ICLA.NGRunner`): immediate kinds at once, others at most one per 10 min; `toasts_enabled() -> bool | None` reads the Windows notification setting (None when it can't be read: "could not verify").

### 6.7 ops
- `single.py`: a named mutex `Local\NGRunner.Supervisor`; a second start exits quietly after opening the running board.
- `launcher.py`: versions in `%LOCALAPPDATA%\Programs\NGRunner\app-<ver>\`; state in `AppPaths.launcher_state`; starts the newest version whose self-test passed; two failed starts fall back to the previous good version and leave a DEV note for it to send.
- `selftest.py`: `run_self_test(paths) -> list[str]` (imports, a migration on a copy of the database, the forbidden-writes table, template lint); exit code 0 only when empty.
- `installer/`: `install.ps1` and `uninstall.ps1` (per user, no admin: copy the build, Start Menu shortcut with the AUMID, both scheduled tasks from XML, a Run-key fallback, the self-test), `tasks/NGRunner-logon.xml` and `tasks/NGRunner-ensure.xml` (every 10 min; priority 4, no time limit, IgnoreNew, battery conditions off). Inno Setup waits for its licence question; this route needs no installer licence.
- `ngrunner.spec`: PyInstaller 6.22, folder build, no UPX, UTF-8 mode.
- `backup.py`: nightly SQLite online backup, encrypted to an ICLA-held X25519 public key (ephemeral X25519, HKDF-SHA256, AES-256-GCM, a versioned header), returned as bytes for `commit_write` into `backups/`; `restore(data, private_key) -> bytes` for the drill; pruning of NG Runner's own `AppPaths.backup` copies after 60 days.
- `support.py`: a support bundle (versions, settings without secrets, schema version, counts, recent logs) as bytes.
- `docs/p1a/runbook.md`: start, stop, pause; falling back to chat; every P1a alarm with its one-line fix; restore onto a new PC; update and rollback; key rotation; deleting fixtures.

### 6.8 jimdocs
Drafts for Jim to approve through his RULES section 8 process: the RULES.md lines P1a needs (NG Runner files the summary into `NG Jobs\<Job>\`; chat reads `<Job>_NGRunner.json` before working a job; if NG Runner is down, work in chat as before) and the skill-text change (take the summary from the NG Jobs folder instead of an upload). Written in his style, each change quoting the exact line it replaces with `file:line`, no client names.

## 7. Wave plan

1. **Wave 1 (parallel):** the eight slices above, each test-first, with fakes for its neighbours.
2. **Wave 2 (integration):** the intake pipeline (Fab change → match → fetch twice → check → branch → file → messages), the P1a interlock, the job model (facts, clicks, pending work → board rows, tray colour, digest lines), the supervisor (single instance, scheduler, tray and board wiring, pause, alarms), the SQLite write record inside `writes.py`, and an end-to-end day simulation on a fake clock.
3. **Wave 3 (verification):** adversarial lenses on wrong-job filing, writes in integration, board and control-folder security, robustness (restarts, DST, offline, sleep), Jim-facing wording, and packaging (PyInstaller build, install script, self-test).
