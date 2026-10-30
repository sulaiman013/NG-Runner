# Decisions

How decisions are made (Sulaiman, 2026-10-29): Jim's questionnaire is not sent. Every decision it asked takes the default recommended there and in the design (section 8), chosen by one test: does it ease Jim's work? Jim's judgment comes in only where it is easy for him, as a one-tap card about a concrete job at the moment it matters. Relief is measured from files and NG Runner's own log, not by asking him. Development happens on Sulaiman's PC; Jim's PC is used only for short, scripted test windows (`docs/testing/jims-pc.md`).

Sulaiman can veto any line below; the change then goes here first, then into the code.

## Decided for Phase 1a

| Decision | Default | Why |
|---|---|---|
| Trigger (design 1) | A new Sheet1 row, plus an Add job button on the board | Barbara already types the rows; nothing new to learn |
| Matching a surname row (questionnaire 3a) | Strict rule (b): only Ready to Order and Ready to Install count, every word of the row must be explained, and a newer sale of the same family and designer makes it ask | A question costs Jim one tap; a wrong job costs a wrong cut |
| Installed jobs (3b) | Complete never counts for a surname match; a remake row of an installed job becomes a card with that job first | Otherwise every job installed before NG Runner would be a candidate |
| Report layout (4) | Landscape (Assembly Reports' default); a job keeps the layout it started with | The two-fetch check compares two downloads of one layout |
| Where Jim hears from NG Runner in P1a (design 2, questionnaire 8) | The board and tray on his PC, plus the 6:30 AM digest by email | No new app or account for Jim. A push channel (Pushover or a Slack DM) comes in P1b, when DO NOT CUT needs one |
| Quiet hours (9) | No phone messages 10 PM–6 AM, except DO NOT CUT (from P1b) | As recommended |
| Barbara's questions | One email listing the candidates when a row is unclear; "Filed as <Job>" when it is filed | Design section 2; she corrects the name in her own cell |
| The night | P1a does not need the PC on overnight and changes no Windows power, update or restart settings. Nightly work runs at the first chance after 1:30 AM, or at the next logon, and is named when late | Leaves Jim's PC as it is; the night setup (questionnaire 5, 6) waits for Phase 5 |
| Mozaik (Q18b) | NG Runner never opens or drives Mozaik, and never writes while it runs | RULES 79 |
| Questions during work (Q14) | Work never stops to ask; questions come as cards. Only "which job is this row?", "closet or garage?" and a report that differs from Studio's proposal (the design check, below) make a job wait, and only that job | RULES 3-4, 57 |
| Messages (Q18a) | Jim's format: numbered, one line each, no intro or sign-off; a clean result is exactly "Nothing to fix — matches IC summary." | RULES 11, 19-20 |
| Accounts (11, 12) | The Apps Script runs under the Google account that owns the NG Fab Schedule, with no separate automation account (Sulaiman, 2026-10-30). ICLA owns the other accounts (Pushover when added, GitHub, signing); Sulaiman is a member | No new account or sharing, and pruning should work if Drive on Jim's PC uses that account, since only a file's owner may trash it. Accepted: emails come from that address, the script's permissions cover the whole account, and its daily limits are shared |
| Remote access (7) | Chrome Remote Desktop, unattended with a PIN, set up in the first test window | Every later test and update runs over it, so Jim is not interrupted |
| Signing (10, design 13) | Decided from the first Setup Check: Smart App Control off means signing waits; on or evaluating means P1a ships signed | Measured, not asked |
| Chat and NG Runner on one job (15) | NG Runner writes only its own new files in P1a, so no lease is needed yet; the two RULES lines and the skill change ship with P1a for Jim's chat to read NG Runner's notes and the filed summary | Sulaiman applies them on Jim's PC with Jim's OK in a test window |
| Baseline (16) | Not asked of Jim. Measured from NG Jobs files (when summaries arrive, crosscheck rounds per job) and from NG Runner's event log once it runs | Objective, and costs Jim nothing |

## Taken when the phase that needs them is built

Each uses the questionnaire's recommended answer. Where Jim's docs disagree (Q1–Q19), RULES.md outranks the rest, then the newest dated rule. What no rule settles is shown as "could not verify", never guessed.

- **P1b (guards):** order flags (any colour but Classic White, substitutions, totals above a set amount), George's emails, the push channel, the team tab (only if Ron, Will or Mary ask).
- **P2 (one toolkit):** the drawing reader's consent and provider; Q6 and Q17.
- **P3 (checks):** Q1 (tested live in Mozaik first), Q19 (the `.bak` rule).
- **P4 (lock):** decisions 4, 7a–7f, 8 and 9; leases.
- **P5 (overnight build):** the night setup (questionnaire 5, 6), garage builds (Q8).

## Sulaiman's own calls

- Hours per week for NG Runner, and the upkeep on top (questionnaire 1).
- A backup contact when he is away (13): George is the suggestion.
- A backup developer (14): the runbook is written so one could take over.

## Settled while fitting Phase 1a's parts together (2026-10-30)

An independent fit check read the eight parts of Phase 1a together and found 28 places where they would not work together: H1-H7 high, M1-M15 medium, L1-L6 low. A review of the write chokepoint found W17-W26. The rulings below settle them, and they all lean the same way: when something can't be verified, ask or wait; never guess, and never file. `docs/p1a/build-spec.md` and `docs/p1a/integration-spec.md` stand as these rulings settle it.

### The parts (the reconcile step, before wave 2)

| Decision | Ruling | Why |
|---|---|---|
| A digest's ref (H1) | `outbox_file`'s default ref is `digest:<Pacific YYYY-MM-DD of now>` for kinds `digest` and `quiet_digest` | The Apps Script knows a digest only by a ref starting `digest`. With the kind as its ref, a quiet night's digest didn't count, and Jim got "No list today" beside it |
| A card's choices (H3) | `CardSpec`, `Card` and `cards.choice_ids` hold, parallel to `choices`, a Studio ID, or None for "None of these" and "Keep <job>". The which-job and row-changed templates fill it | Labels alone lose which job each one meant: the candidates exist only in memory, and by the time Jim answers the list may have been replaced. That is a wrong-job risk on the path that exists to prevent one |
| Barbara's text in messages (H4) | The lint masks the quoted text of `Fab row "..."` and `now reads "..."` before the date, number, attribute, verify and leak rules, and keeps the email, phone, path and hash rules on the raw text. `JOB_NAME` takes three more shapes that real folders have. Fixed templates for Barbara don't point at Jim's board | Real row forms failed the lint, so Barbara got a fixed line naming no row and pointing at a board she doesn't have, and one such card turned the whole digest into a fixed line |
| Fetch timeout (H5) | `fetch_render`'s default `timeout_s` is 240 | Real fetches took 7 to 95 s, and a timeout ends the address search at once, so a render over 60 s could only ever reach the 24-hour card |
| The shadow run and the Apps Script (H6) | A Script Property `SHADOW`. While set, `Outbox.gs` counts a digest to the alert role, `noListCheck_` sends to the alert role and never to Jim, and `checkSetup` does not require Jim's channel | In shadow the digest goes to Sulaiman, so Jim would get "No list today" every morning of a 1 to 2 week run, and the alert role a false alarm |
| A sheet that comes back empty (H7) | `exportFab_` skips and logs when a sheet returns no rows | An empty read would mark every row gone and the next whole one every row new, so rows seen at install would start as new jobs. Skipping lets the 30-minute alarm say so |
| New outgoing streams (M1) | `next_seq(db, stream, *, start=1)`: a new outgoing stream begins at `start`. The messenger and the heartbeat pass the current Unix second | The Apps Script ignores a seq at or below its last, so a new database on a used control folder would lose its messages and set off the heartbeat alarm while NG Runner runs |
| Writing a queued message again (M3) | `outbox_file(..., id=None)`: rebuild the row as `Message('outbox', rec.subject, rec.lines, rec.urgent)` with `id=rec.id, seq=rec.seq, ref=rec.ref` | A new id after a refused commit or a crash leaves a seq gap that the Apps Script holds for 2 minutes, and a row stuck in queued |
| The client in the store (M4) | `replace_opportunities(..., clients=None)` reads `clients.get(studio_id)`. Use keywords in any conversion: `Opportunity` is `(name, studio_id, owner, stage)` and `OpportunityRecord` is `(studio_id, name, stage, owner, client)` | `Opportunity` has no client, so the store silently kept none, and fetch lost the address guesses real jobs need |
| No address in the client (M5) | `JobList.clients` cuts the client at the first word holding a digit and strips a trailing hyphen. `_client_words` stays, so the address rules still work on an uncut name | An account name can carry a street address, which would reach the store and the backups, against the export's promise |
| A corrected row's list (M7) | `fab_rows.text_changed_at`, set when a row's text changes. `to_match()` passes it as `FabRow.first_seen`; `first_seen` and `original_text` stay as the record | Freshness measured from first sight matches a corrected name against a list older than the correction, so the surname rule could pick the only sold sale while the sale Barbara meant isn't listed yet |
| DEV items (M8) | `dev.studio_id` and `dev.urgent`. `open_dev` takes both and de-duplicates on code, text and job | Your list needs the job for "work <Job> in chat as before", and the tray needs the urgency; DEV rows held neither |
| `Fetched.found_name` (M12) | A trailing field, set on `wrong_job` | Studio's name was only inside a sentence, so the pipeline couldn't tell a rename from a real mismatch |
| Cards and rows with no job yet (M13) | `templates.card_subject(card)`, the logic now inline in `card_message`. `CardView` refuses a job that isn't text. `JobRow.studio_id` may be None | The word None showed on Jim's Cards page, and a row with no job yet needs a board row |
| Shadow in the settings and on the board (M14) | `Settings.mode` (`shadow` by default, validated). `BoardView.shadow` with a banner in `base.html`; `TrayState.tooltip` starts SHADOW. `BoardActions.set_mode` with a guarded POST route, shown on Health | Nothing could hold or show the mode, or make the switch to live |
| No phone in P1a (M15) | `ALARMS["no_receipts"]` and `cant_send` are worded without a phone. Jim's channel defaults to `email` in both `.gs` files and the README. `Settings.phone` defaults to `none` | P1a has no push channel (above). The old default made `checkSetup` refuse, and the digest promised a phone that gets nothing |
| Fingerprints and pypdf (L1) | `renders.pypdf`, the version that read the fingerprint. Recompute from `cache_path` when it differs | Fingerprints hash pypdf's text, so after an upgrade nightly re-fetches would raise false "IC changed" cards |
| DEV lines (L2) | Built from `lint.redact(text)`, each line vetted, with only a failing line replaced by its code. `FIXED['dev']` no longer says "it is on the board" | One path or field name turned the whole DEV message into a fixed line, pointing at a board with no DEV view |
| A restore (L3) | `jump_after_restore(db) -> None` jumps every outgoing stream by 1,000,000 and `card_numbers` by 1000, and `backup.for_a_new_pc` calls it | A card ID issued after the backup would be issued again, against "IDs never reused", and `for_a_new_pc` knew the store's private `out:` prefix |
| What the Apps Script prunes (L4) | `PRUNE_RULES` entries: `support-<date>-<time>.zip` after 30 days, and `shadow/` files after 30 days | Bundles would pile up, and shadow files hold client PDFs |
| Two online-backup paths (L5) | Keep `backup.snapshot`, which gives bytes with no temp file. The supervisor's pre-migration copy is `backup_to`'s one caller. Both docstrings say so | Two paths did one job, and nothing called `backup_to` |
| Addresses in settings (L6) | Remove `Settings.emails` | The spec says no address is ever written into a file, and nothing read them |
| Identical bytes | A new `Agreement` code `same_bytes`, after the two incomplete codes in the order and before `layouts_differ`. The pipeline fetches again at +10 min and opens one DEV item; the 24-hour card bounds it | All 6 real pairs measured differ in bytes, because Studio renders afresh, so identical bytes mean something served one render twice, which proves nothing about a stable render |
| `seen_ids` | `prune_seen_ids(db, *, keep_last: int = 5000) -> int` | It grows by about 300 rows a day, and nothing removed them |
| Settings across versions | `Settings.backup_public_key`: blank means the nightly backup is a board item, not a crash. `load_settings` sets unknown keys aside with a note the supervisor can pass to DEV | A fallback to an older version must never refuse a newer `settings.json` |
| Two new message forms | A template for the catch-up line ("Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM"), and a "SHADOW" title form for redirected messages | The design names missed windows, and a redirected message must read as one |
| The fake Studio | `FakeStudio` moves from `tests/test_fetch.py` to `tests/helpers_studio.py`, beside `helpers_pdf.summary_pdf` | The day simulation needs it too |

### The pipeline (wave 2)

| Decision | Ruling | Why |
|---|---|---|
| A row's first-seen time | It stays "now", not `exported_at`. A corrected row needs a list synced after `text_changed_at` (M7) | "Now" is later than `exported_at`, so stricter, and the job list's clock check bounds it |
| The first export | `diff(None, rows)` only before the first accepted export. Keep the last export's rows, use `unchanged=True` to keep sheet rows current, and pass only the edits made after the previous export's `exported_at` | `diff(None, ...)` marks rows as seen, so a later use would hide new rows; older edits would be read again |
| A sheet that suddenly changes a lot (H7) | Hold any export where more than max(5 rows, 10%) are gone or new until the next export says the same, and log it to DEV. A new row identical in job, designer and install to a recently gone row is that row coming back, not a new job | A sheet read cut would otherwise mark rows gone and then new, with cards, emails to Barbara and fetches for rows seen at install |
| A half-typed row | A new row acts only once a later export still shows it unchanged | So a half-typed row never asks |
| Corrections and gone rows | A correction or a gone row withdraws its open which-job card. An edit of a `seen_at_install` row stays seen, and is logged | A card about text that is no longer there only misleads, and a row seen at install must never start work |
| Remakes | If only the install date or designer changed on a filed row whose old install date has passed, raise a card ("Is this a remake of <Job>?") instead of treating it as an edit. Nothing is filed until it is answered | A remake must not be absorbed as an edit |
| Re-matching a filed row (M10) | Only when the job text changed, with the job's own ID and folder left out of `ng_jobs`. "Row changed" for any result other than matched to this job | With its own ID in `ng_jobs`, a filed row reads as ambiguous, not as its own job |
| Card answers (H3) | Link a job only through the card's `choice_ids`, first checking that the ID is still in the list. If it is gone, say so on the board and keep the row asked. "None of these" sets the row to `none`, logged, with no refiling | A match run again days later can give other candidates, and a wrong job here is a wrong cut |
| After a fetch | `ok` and `check.ok`: the second fetch at +10 min. `ok` and not `check.ok`: an early render, retry every 30 min. `not_found`, `timeout`, `network`, `http_error`, `not_pdf`, `truncated`: back off as the spec says. `too_big`: a DEV item. `no_project_name`: retry, then the 24-hour card. `wrong_job`: the rename rule below, otherwise a DEV item and a card at once, never silent retries. Keep `Fetched.url` as the job's `known_url`. Pass `base_url=settings.studio_url` | Each code means something different, and silent retries on a wrong job would hide a real mismatch |
| Fetches and the tick | A tick never waits on a fetch: run them off the scheduler thread, a couple at a time, and stop between steps on a stop request | Real fetches take up to 95 s, and `install.ps1` ends NG Runner after 2 minutes |
| After `agree()` | `agree`: file, using `Agreement.fingerprint`. `differ`: the second becomes the new first, and fetch again at +10 min, carrying the same 24-hour deadline. `same_bytes`: one DEV item, and fetch again at +10 min. `first_incomplete`: drop the first. `second_incomplete`: keep the first and fetch again. `too_soon`: fetch again at the first fetch's time + 10 min. `layouts_differ`: a DEV item, no loop. `OSError`, when a cached file is gone: treat that as no first fetch | Only an agreeing pair files. Everything else is bounded by the 24-hour card or needs a person |
| What leaves the PC (decision 10) | Jim's phone gets job names and dates only. Cards and messages never quote IC design names. `Agreement.reason` can quote them, so it goes to the event log and DEV only; card text uses fixed wording. `Fetched.reason` is safe | The consent covers job names and dates going to Pushover, nothing more |
| A rename (M12) | A `wrong_job` whose `found_name` equals the list's current name for that Studio ID is the same job renamed: add the alias, re-fetch under the new name, and log it. The folder is never renamed. Otherwise a DEV item and a card | The Studio ID is the identity, and the check against the list keeps this from being a guess. Each nightly re-fetch of a renamed job would otherwise read as `wrong_job` |
| The job list | Never replace a stored list with an empty one or one with `synced_at` None. Match from `get_job_list`. Pass `synced_at`, never `fetched_at`. Transient errors retry quietly; others are DEV items carrying `str(error)`. Ask for a push at most once per 5 minutes. A missing export key is a board item, not a crash. `replace_opportunities` gets `clients=job_list.clients` | `get_job_list` gives the list and its time from one moment. `fetched_at` is always newer than the row, so passing it would defeat the freshness rule with no error. An empty list has no rivals in it |
| An unchanged export | The same file read again (same seq and id) is "unchanged": no DEV item each minute. Judge the 30-minute "export is old" alarm by `exported_at` | The export is replaced in place and read every minute, so seeing it again is normal, not a replay |
| The interlock's scope (H2) | Paused applies only to targets in NG Jobs and `control\shadow\`. Every other control-folder target passes. Scenario 7 is reworded to say so | A pause stops filing. It must not stop heartbeats, the digest, messages and backups, or the 45-minute alarm fires on a paused PC |
| HELD | Not in P1a: dropped from the interlock and left as an unused overlay | No button or command can set it |
| OBSERVED | It blocks filing: a folder found at install gets no writes, and the board shows why. A same-fingerprint summary there still marks the job filed | The design says watch and check only, and an exact-name row would otherwise write into that folder |
| `NG Jobs\Old` | `ng_jobs` and the look-in-the-folder step read it too. A job there is never given a second folder at the top: same rules, no write | 37 folders at the top, 102 in Old: without it a job moved to Old looks new, and would get a second folder |
| Compare like with like (M9) | When a summary is in the folder, read its layout from `check_summary(...).details['orientation']`. If it differs from the job's, compare against a fetched pair in that layout, and the job keeps it | 12 of the 27 archived summaries are portrait, and fingerprints differ between layouts, so a hand-filed portrait summary would raise a false "IC changed" card |
| Branch (M11) | Unclear only when the row or Opportunity Name says garage or `_Gar` and no design carries a garage word, or when a design has a blank category and no garage word in its name. Mixed is closet, with no card; all-garage is garage, whatever closet-side room word the row carries. Use `match.room_words()` and the row's variant | Jim's BUILD.md makes a mixed summary one job. A closet-side room word on an all-garage job is no reason to ask. Without the blank-category rule no design could ever be unclassifiable |

### The messenger and the supervisor (wave 2)

| Decision | Ruling | Why |
|---|---|---|
| Urgent messages with no receipt | If the Pushover keys are in Credential Manager, send directly through `messages.pushover.send`. Without keys (all of P1a unless Sulaiman adds them), log it and raise a DEV item. The tray turns red either way. Scenario 9 tests both | P1a has no push channel, so the direct path can't be assumed |
| Receipts (M2) | Exempt from the seq rule: `seen_id` only. Look up `receipts/<id>.json` per outstanding row rather than listing the folder. `record_receipt` raises `LookupError` for a message the store doesn't hold (after a restore): ignore it | Receipts reach the PC through Drive in any order, and `record_receipt` is already once-only. The strict rule would give a false red tray |
| Outgoing sequence numbers | Assign the seq when the outbox row is created, with `start=` the current Unix second (M1), and rewrite the same id and seq on retry. At restart, a queued row whose file exists is marked written; otherwise it is rewritten | No gaps for the Apps Script to wait on, and no message written twice |
| Sending, in order | `vetted(msg, now=now)` (if `replaced`, open a DEV item with its problems); then in one transaction `next_seq`, `outbox_file`, `add_outbox`; then `commit_write` and `mark_outbox_written`. In shadow mode the redirection to Sulaiman happens before the lint | `commit_write` never runs inside a transaction, and in shadow Sulaiman sees what would have gone out |
| Quit means quit, until the next logon | Tray Quit calls `launcher.record_quit` and `request_stop`. The 10-minute "ensure running" task then does nothing (`stopped_by_user`); the logon task and a start by hand clear it | Otherwise the task would undo Jim's Quit. If he leaves it off, the Apps Script watchdog's missing heartbeats tell Sulaiman, as they would for a PC that is off |
| Logging | The supervisor sets up one rotating log file in `AppPaths.logs` for the `ngrunner.*` and `waitress` loggers. It never logs a secret, the board's token URL or a message body | The board's, tray's and toasts' loggers had nowhere to go, and AGENTS.md rule 6 holds |
| A second start | Opens the running board through `single.open_running_board`, which reads what `publish_board` left in the data folder. That file never enters the support bundle or a log, and the board's address never goes to stdout. Check both, and test them | The address carries the board's token, and `app-output.log` does enter the bundle |
| `run` | In this order: `single.acquire`; on `None`, `open_running_board` if `--open-board` and exit 0; `StopSignal`, `publish_board`, `record_up` within 120 s; `withdraw_board` on exit; `pending_notes` into `open_dev`, then `ack_notes`. It must come up and call `record_up` even with no settings or secrets yet | `install.ps1` starts it before they exist, and two failed starts retire the version |
| Commands and the self-test | Every new module joins `selftest.P1A_MODULES`, plus `pythoncom`, `win32com` and `win32cred`. `install-shortcut` makes `NG Runner.lnk` with AUMID `ICLA.NGRunner`. `release-all` exits 0 saying there are no leases. `canary` as the integration spec says, also refusing folders named like NG Jobs or Mozaik Shared | A module missing from the build must fail the self-test; without the AUMID Windows drops toasts; uninstall calls `release-all`; and the canary runs before settings exist |
| A setup command | `NGRunner.exe setup` writes `settings.json` and puts the secrets in Credential Manager. It asks plainly, shows no secret, and checks the folders | Test window 2's step 4 had no tool |
| A pause signing script | `tools/sign_pause.py` gives Sulaiman the signed pause files build-spec section 3 promises, with a Unix-second seq | The spec promised one, and none existed. A seq from the clock never falls below an earlier one |
| Before `open_store` | Read `Store(path).schema_version()`; if it is below `LATEST`, `backup_to` first | `open_store` migrates at once, and an older version then refuses the file |
| Shadow mode and toasts | No toasts on Jim's PC in shadow mode. The board and tray still say SHADOW | The immediate card kinds would pop up during an unattended window |
| The support bundle's destination | `control\support\<bundle_name>`, through `commit_write` | The tray's Send support bundle had nowhere to send it |
| Nightly work | Commit the `Backup` to `control\<folder>\<name>` once per night key, and send `Pruned.left` and any `BackupError` to DEV. Also the yesterday's-log file, composed from events with job names only; pruning old renders, keeping the governing one and the latest that differs; `prune_seen_ids`; and pruning `writes` rows. Also the daily "ran" and DEV lines to Sulaiman, and telling Jim of DEV items over 48 h | Every fetch has new bytes, so renders would pile up on Jim's PC. The design's daily lines and 48-hour rule had no place in the schedule |
| Timing | Phone quiet hours are 10 PM to 6 AM except DO NOT CUT, `cant_send` included. Barbara's emails use the same quiet hours, with no new setting. The morning-list toast shows with the digest, or at the first start after it. No unlock detection in P1a | One rule, no new setting, and nothing new to detect, while the design's morning list still comes |

### The write chokepoint (wave 2)

| Decision | Ruling | Why |
|---|---|---|
| The registry interface | It has `owned`, `begin` (the journal row), `record` and `abort`. A path unreachable at start-up keeps its row pending and retries. The journal is the next store migration, appended | The spec's `owned` and `record` left nothing to call before the first rename. Closing an unreachable path's row would strand a file NG Runner can't overwrite later |
| With a registry | Skip the JSON in `_data_folder_problem` and `_record`. Catch `StoreError` in `_record`, for plain words. Record `owned` only for NG Jobs and shadow paths. One `ctx.commit(writes)` wrapper does `commit_write` plus `record_commit` | Heartbeats and outbox files are write-once, and about 300 commits a day would otherwise add rows forever. The wrapper means no caller forgets |
| The clock in `writes.py` | `writes.py:1116`, the only `datetime.now()` outside `clock.py`, takes a clock | Otherwise the day simulation's prune reads real time |
| Leftovers from a stop (W17) | Move `os.fstat` and the `_Temp` construction inside `_write_new`'s protected block, and in `_make_folder` record the pin in a try/finally right after `mkdir`. Accept the window just after `CreateFile` returns (microseconds; the leftover is reported on the board, AGENTS.md: Before filing goes live, item 7) and say so in the docstring | A stop in those windows left an empty temp file or an empty new folder behind |
| A stop after the record (W18) | With `registry=`, the journal and the start-up settle close it. For the JSON registry, before rolling back after any failure once `_record` has started, re-read the record: if it already holds every new hash, the commit is complete, so skip the rollback and re-raise | Files went back while the record kept the new hashes, so later overwrites of the restored file were refused |
| A temp file changed in place (W19) | Just before each rename, also require the temp file's content to hash to the intended hash, and refuse before any rename if it doesn't. Word the rollback note "left in place" when NG Runner's own rename put the file there. Test: rewrite the temp in place during interlock call 2; the target stays untouched | It was still renamed in, and the target was left holding someone else's bytes |
| A parent folder swapped (W20) | Check `_moved_folder(t.pins)` right after `_create_new`, before writing. Leave backup-folder pinning out | A temp file and its content could sit in another folder until the later check. Backups live in the app's own data folder |
| A folder at a temp name (W21) | On `PermissionError`, if `os.path.lexists(path)`, try the next name | It read as a refusal, and in a rollback it failed the restore |
| `commit_write`'s docstring (W22) | "Ctrl+C, an exit or any other BaseException", and it names the one remaining window | It named two exceptions, and promised more than W17 and W18 kept |
| Long file names (W23) | Refuse file names longer than 238 characters, with a plain reason | A longer name's temp name is not valid, so it could never be written, and the refusal was Windows' raw text |
| A root reached through another (W24) | `_load_roots` refuses a root whose configured spelling passes through another root, with a message about the setting | It loaded, and then every target spelled from it was refused |
| A record nested too deep (W25) | Catch `RecursionError` in `_Registry._load` and report a damaged record | The reason read "Unexpected problem (RecursionError)" |
| Tests for the unreached branches (W26) | Add tests for `_temp_changed` (identity and content), a record-write failure leading to a rollback, a target that becomes a link after planning, and a failed restore during a rollback | No test reached them, and W19 sat in one of them |

### As wave 2 built it

Where wave 2's owners went beyond or differed from the specs. The pipeline's other two deviations, the remake card and a rename after filing, are settled in "The final rulings" below.

| Decision | Ruling | Why |
|---|---|---|
| A card and its email (pipeline) | Not all in one transaction. The card, the row's state and a queued `tell_*` row commit together, and the send follows under a unique ref. A restart looks for that ref in the outbox and in `messenger.held()`, so nothing goes twice | `Messenger.send` opens its own transaction and writes a file, so it can't run inside another |
| A message held through quiet hours (messenger) | It has no outbox row until it goes. It waits in the state key `messenger.held` with the id `send()` returned, and takes its seq when it goes, so `get_outbox(id)` is None until 6 AM | If a held message kept a seq, later messages would pass it, and the Apps Script would refuse it, so it would never get a receipt |
| A message that never gets a receipt (messenger) | A written message with no receipt after 24 hours counts as failed: one DEV item, `receipt_lost`, and nothing is resent | `Outbox.gs` can lose a message or refuse a late one, and nothing else could clear that row, so one lost message would keep the tray red for good. A real outage still stays red through the next day's messages |
| The urgent DEV item (messenger) | It closes itself when the receipt arrives, the direct send succeeds, or the message is given up on | No screen closes DEV items in P1a |
| Pushover in shadow mode (messenger) | The direct path counts as "no keys", so scenario 9's with-keys branch runs with `mode=live` | It would ring Jim's phone during the shadow run |
| A commit settled at start-up (the write record) | When the settle closes a journal row, it opens one DEV item, code `write_settled`, with file names only. A row closes as `done` if it adopted anything, otherwise `aborted` | A crash cut a commit short, so Sulaiman hears of it, and can check by hand any path that was left alone |

### The final rulings (wave 3)

Measured facts behind them are in AGENTS.md: Before filing goes live, item 8. They all lean the same way: when something can't be verified, ask or say so; never guess.

**The Studio design check (`ngrunner/intake/studio_designs.py`)**

| Decision | Ruling | Why |
|---|---|---|
| The check | After fetch 1 passes the summary check, compare the report's designs with Studio's Proposal tab. `match`: carry on as today | On 59 of 59 real sold jobs the two are equal by name, 124 designs on each side, while those projects hold 202 in all. It is a list the report's own checks can't give |
| The two differ | `report_missing_designs` or `proposal_missing_designs`: never file. One card, kind `design_mismatch`: "<Job>: the report lists 3 designs and Studio's proposal lists 4." Choices: "Fetch it again" and "File it as it is". Counts only, never a design name. Only that job waits. Re-check every 30 minutes; when the fetch and the proposal agree, withdraw the card and carry on. "Fetch it again" queues a fresh fetch 1. "File it as it is" files it and logs an event saying Jim chose to | A report that lost a room is the mistake this check exists for, so a person decides. Design names never leave the PC ("What leaves the PC", above) |
| Studio can't be read | `StudioUnavailable`, when Studio can't be reached or the login is missing or refused: still file. Mark it plainly: `designs_checked: false` in `<folder>_NGRunner.json`, "designs not cross-checked" on the job's board line and in its log line, and one DEV item per day with code `studio_unavailable`. `page_changed` is treated the same way, with its own DEV code | Today's hand download has no such check, and a login problem must not stop all filing. `page_changed` means the parser needs attention |
| Studio's report address | When every derived address gives `not_found`, try `ProjectPage.report_url` once, as the job's `known_url` | That should fetch the 1 sold job in 60 the derived addresses miss |
| Where the check runs | Where fetches run, never on the scheduler's thread | The sign-in can take a minute |
| `Ctx.studio` | `Ctx` gains `studio`: a `StudioSite`, or None when no login is set up. None means "not cross-checked", as above. The supervisor builds it from the secrets `studio_username` and `studio_password`; `NGRunner.exe setup` asks for them and shows neither. A missing login is a board item, not a crash | A login problem must not stop all filing, and no secret is ever shown (AGENTS.md rule 6) |
| Counts a person can check | "Filed as" to Barbara, Jim's digest line and `<folder>_NGRunner_log.txt` all show the job's design and page counts, for example "Filed as Testerson_101426: 3 designs, 19 pages". `<folder>_NGRunner.json` gains `designs` (the count) and `designs_checked`. Counts only, no design names | A person who knows the job can spot a missing room at no cost, which also covers what the check can't see |

**The day simulation owner's findings**

| Decision | Ruling | Why |
|---|---|---|
| A fetch that raises | It must not go silent. The error becomes a refused `Fetched`, so backoff and the 24-hour card apply. Tested with a cache write that fails | It was only logged and retried every minute with `attempts` still 0, so no card or DEV item ever appeared, against "filed within 45 minutes or carded" |
| "File the new one" | It must not end in nothing. Fall back to the newest cached render with the same fingerprint. If none is left, keep the card open and fetch again; never end failed with no message | Jim can answer an "IC changed" card after pruning has removed the render it points to |
| Digest reminders | Record them only when `vetted()` did not replace the digest | Otherwise those cards don't come back until their next reminder day |
| Lines to Sulaiman after a crash | `_night_ran` and `_night_dev_lines` check their ref, as the digest does | A crash between the send and the step's note would send the line twice |
| The remake card | It gets its own kind: `remake` in `cards.CARD_KINDS`, with `QUESTIONS` and `_CARD_BACK` entries, and `_act` dispatches on the kind. Its digest line asks whether it is a remake. Wave 2 had it as `row_changed`, told apart by `REMAKE_CHOICES` | Under `row_changed` the digest asked "which job is it?" over the choices "A remake" and "The same job" |
| `selftest.P1A_MODULES` | Gains `win32com.shell` and `win32com.propsys` | `install-shortcut` imports them, and a module missing from the build must fail the self-test |
| `supervisor._is_paused` | Uses `interlock.is_paused` | So the two can never disagree. It tested `== "yes"`, while the interlock treats anything but `"no"` as paused |
| `Ctx.commit` | Calls `commit_recorded` instead of copying it | The copy lacked `commit_recorded`'s guard around the row save |

**Others**

| Decision | Ruling | Why |
|---|---|---|
| A BLOCKED job | It counts on Your list once it has been blocked for over an hour, so the tray goes amber. A pause doesn't count, since the tray is already grey | Silence is never success. The tray stayed green while a job was BLOCKED |
| `match._typed_variant` | It gets a public name, `typed_variant` | So `branch.py` stops importing a private one |
| A rename after filing | Raising one "IC changed" card stands. The folder is never renamed | PROJECT NAME is part of the fingerprint, so the content really does differ. The pipeline owner was right not to suppress it, since that could hide a real change, and Jim settles it with one tap |

## Matching (settled)

| Decision | Ruling | Why |
|---|---|---|
| `match_by_folder` | It requires `opportunity_names`, as `match_file_to_job` does | A folder named for a sale with no job, or for a run of it, is no job's, never the parent's |
| A row too long to read | `none` includes the reason `row_too_long`, which takes the `none` path (the same card) | A cell over 200 characters is never read, so a person must retype it |
| Room words in `branch.py` | `room_words(text)` gives one spelling each, so `branch.py`'s message says "cabinet" and `tests/test_branch.py` must expect it. The `_Gar` flag isn't in it, so keep that check. `branch.py`'s `("Master", ...)` case no longer tests a surname that is a room word; use "Closet" | One reading of a row, in `match.py`, instead of a copy in `branch.py` |
| `guest` and `master` | No longer room words | Both are plausible surnames, so a double-barrelled family could read as a surname plus a room word, and lose to a newer sold job |
| What blocks a surname match | A newer sale named with a first name or a second surname, or opened the same day, blocks a match, and so do shared initials or a sale with no owner | Salesforce can lag behind the sale. Initials that fit two owners, or a sale with no owner, rule nobody out |
| `match_fab_row`'s parameter (M6) | `list_synced_at`, renamed from `list_fetched_at` | It must get `synced_at`, but both records have a `fetched_at`, which is always newer than the row, so passing that would let a stale list auto-match with no error |

## Measured on real data, 2026-10-30

- **Fetching:** of the 60 most recently changed sold jobs, 59 fetch, in 7 to 95 s, and all 59 pass the summary check. Two fetches 10 minutes apart: 6 of 6 jobs agree on text and all 105 drawings, and no pair is byte-identical.
- **Auto-match, typed the way Barbara usually types them (bare surname, designer's initials), against the local list of 2026-09-15 (10,643 Opportunities):** 32 of 53 jobs (60%) dated in the list's last 30 days, 64 of 116 (55%) in its last 90 days, and 307 of all 628 sold (49%). None matched the wrong job; the rest ask with a card. The real Sheet1's rate is measured in the shadow run (AGENTS.md: Before filing goes live, item 6).
