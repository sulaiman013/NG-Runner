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
| Shadow mode and NG Jobs | Shadow mode writes nothing in NG Jobs, including for a job filed there while live: "File the new one" files aside in `control\shadow\`, its notes wait until live, and `p1a_interlock` refuses any NG Jobs target in shadow, so no path can forget it. Live files in NG Jobs even for a job filed aside: a missing file there is a new filing, and one that exists is compared, never overwritten blindly | `set_mode` and the integration spec both say shadow files only into the control folder. A job filed live kept `where: ng_jobs`, so "File the new one" replaced the real summary during a shadow run, and a job filed aside and answered live changed only the copy pruned after 30 days |

### After the three independent reviews (wave 3)

Three reviews of the running app (safety, the running app itself, and breaking the code on purpose) found 35 problems between them. These are the rulings their fixes made.

| Question | Ruling |
|---|---|
| Can shadow mode write in NG Jobs? | Never. The interlock refuses it, and notes wait until live |
| What happens to jobs filed in shadow at go-live? | Each one whose row is on the sheet, or that has no row, goes through a fresh fetch and files in NG Jobs, and Barbara gets her "Filed as" then. A folder Jim made is compared and never overwritten. One whose rows have left the sheet stays aside, reads "Trial run only, not in NG Jobs", and `add job` or a new row brings it back |
| A Studio sign-in or a fetch that hangs? | The sign-in has 7 minutes. A fetch over 15 minutes frees its slot, opens DEV `fetch_stuck` and turns the tray amber at once, and whatever it brings back later is dropped |
| A wrong password in a crash loop? | The last sign-in's time is kept in the store, so a restart can't try again within the hour. A browser that hangs stays parked until the process exits |
| A job past its deadline that was never tried? | It gets the 24-hour card an hour after it is first seen past the deadline, which lets a PC that slept make its attempt first |
| Does the night's re-fetch check the proposal? | Yes. The card gives the counts, or says the designs could not be cross-checked |
| A design card whose content or counts moved? | It is replaced, and a late answer to the old one is logged and ignored |
| Two sales with one name? | The second is BLOCKED with DEV `same_folder`, since one folder can't hold both |
| NG Jobs unreadable? | `ng_jobs_unreachable` is an alarm. After an hour the row is on Your list and the tray is amber, and its next step says why |
| A board or tray that can't start? | `board_failed` and `tray_failed` are alarms, retried every minute and closed on success |
| DEV items that stay open for good? | Each closes when the thing works again: a job's fetch items on filing, a task's on its next clean pass, the heartbeat, backup, log and night-step items on their next success. `message_replaced` stays until `NGRunner.exe dev-close` closes it, because it names wording to fix |
| Old jobs on the board? | Filed jobs with nothing open and no row on the sheet leave the board after 30 days. There is no "show all" view yet |
| What does an "IC changed" card say? | What differs (`intake/report_diff.py`), since Studio's report changes with its catalog: part numbers or names only, text, or drawings. It describes and never decides |
| A multi-day install (`10/29-30`)? | It counts as its first day, the day the job must be ready |

## Phase 1b's rulings (2026-10-30)

The recommended defaults, adopted as Sulaiman asked for decisions, and he can overrule any of them. The reasons and the measurements are in `docs/p1b/build-spec.md`, section 9.

| Question | Ruling |
|---|---|
| Sheet size a nest must be on (Q3) | The RULES table's lines only, until the material library is here. Any other name reads "could not verify" |
| A sheet Jim added by hand (Q3a) | DO NOT CUT, plus one card that clears that one program on that one job. A re-post asks again |
| IC "Charcoal" (Q4) | Egger Black U999 ST19 Woodgrain on 61x109, as RULES says. An old `3-4 Charcoal` folder has no rule |
| Order call-outs (Q12) | By email to Barbara and Will, never in Jim's work list |
| Colour flag (12a) | Any material but Classic White gets a line, not an alarm |
| Substitutions (12b) | A colour swap is named once, unflagged. A size swap isn't flagged in P1b, because the takeoff has no field for it |
| Total flag (12c) | $25,000 on the estimate's total, and it's a setting |
| The takeoff's shape | One RULES line and a skill line for Jim's chat, through his approval process. Until then an unreadable takeoff says "could not verify", never a partial list |
| Checking the programs without the toolkit (departure) | Built first, as an extra check that never replaces Jim's scripts. A fault raises DO NOT CUT, and a clean read stays NOT CHECKED. Once the toolkit is adopted both run, and a disagreement goes to DEV |
| "DO NOT CUT is never silenceable" (departure) | It holds, except for the Q3a click on a size |
| P1b's exit test (departure) | The design's named bad nests aren't shown to exist on disk, so section 11 of the spec replaces that line |
| What reaches a phone (departure) | Job names, dates, material and sheet size, since DO NOT CUT is useless without the material. **Jim's consent is still needed before any of it goes to his phone** |
| The interlock | Unchanged in P1b, which writes nothing a Mozaik job reads |
| Bringing this PC's stopped OneDrive back | No, by Sulaiman's call. The team works in Google Drive, so this PC's OneDrive stays as it is, and what lives only on Jim's PC arrives as a zip on Google Drive |

## Wiring Phase 1b's guards into the app (2026-10-30)

Sulaiman's defaults, which override `docs/p1b/build-spec.md` where they differ, and what the wiring settled as it met real files. He can overrule any of them.

| Question | Ruling | Why |
|---|---|---|
| The tray and a posted NOT CHECKED | Red for DO NOT CUT, even while paused, since a pause stops filing and no guard. A posted NOT CHECKED changes no colour until a toolkit snapshot is adopted, and goes red only after that. It still shows on the board, in the digest and in the heartbeat | Today every new post reads NOT CHECKED, and a red that never clears teaches people to ignore red |
| Both faults at once | `decide` keeps its state order, and a verdict lists every fault: doubled holes, sizes, a failed check. Clearing a size takes only that fault off the list | Jim re-nests once, not once per fault. Doubled holes can never be cleared |
| The sheet size card | One per material, naming how many and which. Answering writes one clearance per program, each bound to its hash. A new or changed program past its size withdraws the open card and asks again about all that stand. "Re-nest it" stays DO NOT CUT, writes nothing and isn't asked again until a re-post | One real folder has 7 programs past its size, and 7 cards for one click would be noise. An answer stands only for what its card said |
| A lower-case `.tap` holding one sheet | Near pairs are read. `family_of` still goes by name. One sheet takes at most one M00 stop, no tool loaded twice, line numbers that never start again and one program end; a file that can't be told counts as several | Merged programs overlay sheets, which is why pairs are skipped there. 26 real ones hold one sheet, and 1 of them holds a real pair |
| "No OK to cut" | A runtime rule in `messages/lint.py` over the whole text, quoted words included | A verdict's reasons can come from a tool's own output, so a test of today's words isn't enough |
| Counts on real data | They stay as measured, and code isn't fitted to the spec's numbers | Drive's contents move: the whole drive now reads 278 of 1,368 with pairs before the fix, where the spec has 286 of 1,397 |
| Labels and the real match | Two types: `facts.DesignMatch` is the nest against the design, and `labels.LabelsMatch` the fallback. `MaterialEvidence` has a field for each, and the labels' one wording is `LabelsMatch.note` | Two classes of one name, one of which can pass a nest, is how they get swapped. Either way a labels reading can't reach NEST_CHECKED |
| A verdict after a click | Jim's answer writes its clearances at once, and the material is read and judged again at the guards' next pass | The store keeps a count of near pairs, and no read of G: may run on the tick's thread. While G: is away it stays DO NOT CUT, which is the safe way to be wrong |
| A card about a folder with no job | Named from what it asked, kept in the state table. One card that can't be named costs only itself: one DEV item, and the command box goes on working for the rest | `card_from_record` raised on it, which broke the box for every card |
| A program gone | Gone after two polls, and its row stays, so one that comes back unchanged is what it was and never a new post | A cloud drive can hide a file for a while, and an old doubled program must not go red for that |
| A listing that loses many folders at once | More than 5, or a quarter of them: not believed for 2 hours, with a DEV item after 10 minutes. After that it stands | A drive starting up shows an empty folder, and that must not clear a DO NOT CUT. Same thinking as an export held (H7) |
| The first scan | It waits for a pass that listed everything, with a DEV item after 10 minutes. It only lists: a hash is read the first time a verdict needs it | A folder it couldn't read would look new later, and go red |
| The digest | Every DO NOT CUT, then the 10 newest posted NOT CHECKED and a count of the rest. The heartbeat carries the same lines | Until the toolkit is here every post reads NOT CHECKED while its job is in NG Jobs, which would bury the rest of the For you line |
| George's and Will's email | Waits out quiet hours like Barbara's. DO NOT CUT never does, and for George neither does what closes it ("Cleared by Jim", "No longer DO NOT CUT") | He heard of the alarm at any hour |
| A fault gone by a re-post | George gets one line that says so and that it reads NOT CHECKED. It stops short of saying it can be cut | The build spec's scenario, in words the lint allows |
| The heads-up while `order_flags_on` is off | Live: nothing sent, its hash noted, so turning it on doesn't send every job at once. Shadow: Sulaiman gets one copy, marked. Takeoffs there at install are never sent | Nothing reaches Barbara or Will by surprise |
| A material holding a person's name | The leak limit stays open: the Pricing check was not added | Measured on the 20 readable real files against the tracker's keys: an exact key refuses 10, a prefix either way 5, and every word being in some key 3 on sheets and 5 on tape. The rule was to add it only if it refused none |
| Trial labels | After 5 real nests a verdict loses "(trial)", except a material whose feature (garage folder, Tafisa 1/4) no earlier nest has exercised. REA, bridges and stacked towers can't hold one on trial until a `.des` can be read | Only real posts count, not what was there at install |
| The tracker's folder | No default in code. `setup` takes `NG Conversion program Claude` beside NG Jobs when it is there, and says so. With none, Health says the pre-flight is off | It is only ever read, and a settings file from before P1b must still load |

## Phase 1b, group 2: the nest files, the observer and the toolkit (2026-10-30)

What building them settled, each on the build spec's contract as a default, and he can overrule any of them. No real `.opt`, `.UNS` or `.LCK` byte has been read, so everything here is ready for real files and proven only on files written from Jim's lines and, since 2026-10-31, from how his own scripts read them (the next table). Each assumption a real file could break is marked `ASSUMPTION` in the code.

| Question | Ruling | Why |
|---|---|---|
| A program the nest names that isn't in the folder | A real finding: NOT CHECKED with the program named, and no pass. Not a DO NOT CUT, in P1b | What `Filename` really holds is unseen, and a post can land after its nest is saved. A wrong reading here would be a false alarm on every job. Jim's own `sheet_size_check --taps` says it is a fault once it is adopted, as a failed check. With no programs at all, it only reads "No programs read yet": that is a nest not posted |
| A program in the folder the nest doesn't name | One LOG line at each verdict, and nothing on the board | 65 of 551 real ones are hand-named. Jim's "nothing stale" (RULES 725) is a question for him: his `--taps` exits 1 for it, and here it fails nothing |
| A `.opt` not as documented | `OptUnreadable`: NOT CHECKED, a board note, and one DEV item at once, closed by a good read. Never a pass, a crash or a DO NOT CUT. One that may read by itself (only in the cloud, changing while read) tries again each pass, with the DEV item after 10 minutes | The first real file to meet this reader is what Sulaiman needs to hear about |
| What counts as not documented | No parts; only flip passes; a `Quan` there that isn't a whole number from 1; `Flipped` there and not `True` or `False`; sheets with no readable size, under 200 mm or with no header; UTF-16 with no BOM | A zero is never read as a fact (BUILD 567). The XML parser would read UTF-16 with no BOM by guessing, so it is refused first. Where his scripts settle a point, this reads as they do (the next table) |
| When a `.opt` may be read | Once listings show it unchanged for 120 s, hashing the same twice, and parsing: whether Mozaik is running or not | Design 5 asks this only while Mozaik runs, but nothing gates on the observer in P1b, and two minutes cost nothing |
| Sheet sizes in a `.opt` | From `<OptimizeMaterial>`, or a size on the block itself. They are read and nothing judges them | The size is `sheet_size_check`'s (rule 4). `holes` is a count, as nothing here reads a hole's place |
| Nests there at the first listing | From before install, and raise nothing until they change. That listing is its own first scan, whole or not at all | Group 1's first scan is long done on an upgrade, and the old nests with parts and no sheets must not all ask to be saved |
| A listing of Mozaik Shared that loses things | Gone after two polls, and what is known is kept for good, so one back unchanged is what it was. Only a material whose verdict counts is judged again | A drive starting up shows an empty folder (scenario 65d), and with nothing kept every old nest would read as new when it filled |
| The stored pass | The `nests` row: `opt_sha256`, `manifest`, `room_hashes`, `checked_at`. To judge one again, it is made back into both checks passing and the design matching, on those very files, and goes through `decide`, so it can't pass by any way a run of the checks couldn't | One rule in one place. A change of hash can't get past it |
| A pass while its files move | Back to NOT CHECKED at once, on the listing alone, before they can be read, and tells nobody. Once settled: the same bytes stand as they were, with their old time, and no check runs. Anything else loses the pass, and George hears | A pass must never stand on files that are changing, and Mozaik re-saves a `.opt` on close (How We Work 94) |
| A pass whose nest can't be read for now | It reads NOT CHECKED and stays on record for 10 minutes, telling nobody, as a file only settling or away in the cloud says nothing about what it holds. After that the pass is lost, and George hears | Nothing is concluded from what can't be seen, and he must not go on trusting it for long |
| George and a pass lost | One line, NOT CHECKED, at any hour. It isn't in 5.6's table | He was told it was checked, and may be cutting. Design 4 lets NOT CHECKED through quiet hours. DO NOT CUT says so itself |
| Parts and no sheets | SAVE_NEST: on the board, with a row even for a folder with no Fab row, and on Your list. No message, no toast, no colour but amber | It asks Jim for one click and warns of nothing |
| Nest checked in the digest | The job gets its line, by an event in the ACT lane; the digest doesn't say which material. To finish when a real pass can happen (group 3) | Nothing can reach it yet, and no digest line can be proven against real use |
| The owner log | One row each time a lock file's listing changes and it is read, including an empty one. Only a string that isn't empty and isn't the first ever seen goes to DEV, one item for each folder, file and string. Rows are never updated | Not every minute, as 50 files can't have changed. An empty lock names nobody, and logging it says so |
| A lock's words on the board | "Open in Mozaik by JDELA, lock 2 days old", and "In the Optimizer ..." for `OptimizeJob.LCK`. A row's note; no alarm and no card | The stale-lock line of design F1, F2 waits for the writes it guards (P3). Its age goes on with nothing new read |
| The observer on Health | "Mozaik at this PC": Running, Not running or Could not tell | Nothing else uses it in P1b. It is how Sulaiman sees whether the process names are the real ones |
| The Job Object | `win32job` from pywin32, the process started suspended, joined, then resumed with psutil. `KILL_ON_JOB_CLOSE`, so a crash takes them too | pywin32 and psutil are already pinned and in the app build. A script that can't be joined never runs |
| A snapshot's hash | Checked again before every run, and a copy that has changed runs nothing | A run must be of what was hashed. Runs write no `.pyc` into it, and start in a folder of their own |
| No toolkit | `watch.nest_checks` is None, and there is no reader of `des_cutlist`, so nothing can reach NEST_CHECKED. No snapshot is taken on a schedule yet | Adopting a copy needs his regression to run (group 3), and his scripts have been read and not run |
| The self-test and the toolkit | It copies a made-up toolkit and runs two scripts for real, one to its exit and one to a kill. A failure fails the build | Only a real `.exe` shows `run-tool`, `runpy`, UTF-8 mode and the job working, as win32timezone showed on 2026-10-30 |
| The cloud NOT CHECKED: when | Only in the hours the PC is expected on, as for Sulaiman's alert. It comes when they begin | A PC off overnight is no gap, or George would hear every night. It departs from 5.7, which says only "when the heartbeat is stale" |
| The cloud NOT CHECKED: what needs what | No `ROLE_GEORGE_EMAIL`: nothing at all. With it: the heartbeat's "Not checked" lines. With `NG_JOBS_FOLDER_ID` too: the walk | The live shadow has neither, so deploying it changes nothing |
| The cloud NOT CHECKED: the walk | Each 30 minutes at most, in the expected hours, for 3 minutes at most. One that stops early says what it found and looks again from the same time, so a line may be said twice and is never lost. Over 20 lines reads "N more" | Quota is daily, and a walk costs a Drive call per job |

## What Jim's toolkit settled, read from its source (2026-10-31)

His zip is at `.local-fixtures/toolkit/20261031/` (gitignored), read and never run. This table was written from his source before any real file came; the next section corrects it from real bytes, and wins where they differ. His scripts are his, and the repo is public, so none of their code is copied here: only what they print, and what they show a file holds.

| Assumption | What the source says | So |
|---|---|---|
| The encoding | Confirmed. Three scripts sniff the BOM the same way. `sheet_size_check` decodes the rest with `errors="replace"` | A stray byte in a UTF-8 one no longer makes it unreadable |
| Where a sheet's size is | **Broken.** Not on the block or the root: `<OptimizeMaterial>` holds `Width`, `Length`, `WidthTrim` and `DisplayName`. It is RULES 143's "header" | Read from there |
| `Quan` and `Flipped` on every block | **Broken.** `sheet_size_check` reads a block with no `Quan` as 1 and any block not `Flipped="True"` as a board | Read the same, and a value that is there and wrong stays unreadable, as he would read `"true"` as a board and count a flip as stock |
| What `Filename` holds | Confirmed that it may hold a path: he takes the base name, either slash. **Broken** for a name with no extension: he compares it whole with the `.tap` files there | No extension is no match |
| A run with no name | **Broken.** He takes only runs with a `Filename` | It names nothing, and is no fault of the file. A nest naming nothing can't pass |
| Sheets with no part placed, parts placed with no sheets | **Broken, as rules of mine.** No script reads `<OptimizePartLocation>`, and BUILD 784 says sheets are the only test | The rules are gone. It is a count |
| `<OptimizeGCodeRun>` and `<OperationHole>` | Confirmed, and more: a run sits inside its sheet (`cnc_job_sheet`, which calls a sheet with two runs a flip sheet). A hole sits inside its `<OptimizePart>`, with `Diameter` and `FlipSideOp`, which is the "face" of RULES 708 | Nothing here rested on where they nest |
| A `.opt` beside its material folder by name | Confirmed: `--taps` looks in `<job>\<.opt's stem>`. Rooms are `Room\d+\.des`, Room0 included | Unchanged |
| A `.opt` is well-formed XML | **Not settled.** Every script reads one by regular expression. His builders parse the `.des` the same app writes with an XML parser | Unchanged: it is what catches a half-written file, and fails closed |
| What is in a `.UNS` or `.LCK`, and the process names | **Not settled.** No script reads a lock's contents or names a process. His builders copy a `.UNS` from a template job, so on a chat-built job it names whoever the template named | Unchanged, and only a `.LCK` is a sighting of someone |
| How a hand-added sheet shows (Q3a) | **Not settled, and it matters.** `sheet_size_check` reads only the first `<OptimizeMaterial>`, so a later size isn't checked by it | The program check stays the one that sees a 5x9 |
| What a tool prints, and its exit codes | Pinned by format strings, for `sheet_size_check` and `br_cam_check --nest`. `des_cutlist` prints a table, with no verdict | `guards/tool_checks.py` reads the two, marked read from source and not seen running. Any other line is could_not_verify. A test ties each to the source |
| Which modules a script needs | `sheet_size_check`, `br_cam_check` and `units` need only `sys`, `re`, `os` and `glob`; `des_cutlist` `re`, `sys`, `os` and `collections`. All in the build. Across all 183, `openpyxl`, `numpy`, `scipy`, `fitz` and `pdfplumber` are not in the build | The two checks can run in the build as it is, read by `ast` and not run. The builders can't, and P1b runs none of them |

**Why a tool's exit code can't be believed by itself, both ways:**

- `sheet_size_check` exits 0 having read nothing: named no file, or printing `??` of one with no header. `br_cam_check --nest` exits 0 with no `.opt` to read, and prints its clean line for a file decoded wrongly or holding no parts. `guards/opt.py` refuses those files before any check runs.
- `sheet_size_check --taps` exits 1 for a program the nest doesn't name, and its match is exact, so `.TAP` against `.tap` fails. Read by its exit code, every hand-named program is a false DO NOT CUT. The reader takes only a wrong size as a failure, a program not on disk as could_not_verify, and an extra as nothing.

**To raise with Jim, as they are his to settle (rule 4):**

- `cnc_job_sheet.py` calls any sheet with two runs a flip sheet. On the real nests a sheet holds a second run for a flip pass, and also for a remake, redo or combined program: 8 sheets on 7 of the 20 nests, so the operator's job sheet lists those as flip sheets. Answered from his RULES on 2026-10-31 (question 18, below): NG Runner never files a job sheet that names one. (Written from source first, this said it counts a flip pass as a sheet: on real files the flip pass sits inside its run, its pattern match swallows it, and the count comes out right.)
- `sheet_size_check` passes a file it can't read (`??`, exit 0), so a wrong codec reads as clean to anyone going by its exit code. And it reads one size per nest, so a sheet added by hand on another size passes it (Cases B and H).
- RULES 725 says nothing else may sit in the folder: on all 10 real jobs something does, 35 programs in all, 25 of them merged, so `--taps` exits 1 on every one.
- RULES 705 says a `.opt` is UTF-16LE with a BOM: 0 of 20 real ones are.

## Real nests, and the checks' real output (2026-10-31)

Ruling R8, and what followed. The 10 real job folders (`.local-fixtures/toolkit/20261031/jobs/`, by case letter), their 20 `.opt`, 30 `.des` and 7 `MozaikJob.UNS`, the programs on G:, and what Jim's two checks printed when Sulaiman ran them on copies (`.local-fixtures/toolkit/runs_20261031/results.json`). NG Runner ran none of his scripts. Tests: `tests/test_real_nests.py` and the `fixtures` half of `tests/test_guards_tool_checks.py`.

| Question | What real bytes showed | So |
|---|---|---|
| The encoding (R8) | **Broken.** All 20 are UTF-8 with no BOM, and each starts with a line `8` before `<?xml`. So `opt.py` failed on all 20, and each would have read NOT CHECKED | One leading line of digits is set aside, and a version not seen (only `8` is known) is unreadable. The BOM is still sniffed. All 20 now read, with the sheets and runs Jim's own check printed |
| A sheet's size | Every block carries its own `Width` and `Length`. A sheet added by hand is a block of its own size: Case B 6 on 49x97 and 3 on 61x109, Case H 11 on 49x97 and 6 on 49x120. The root is the header, `<OptimizeMaterial>` | Read from the block, and the header only failing that |
| A flip pass | It is a second run of its sheet, holding a copy of the header with the flipped block inside it (Cases C and D). `Flipped` is also on each part's location | Read on sheet blocks only. Counted as no board: Case C reads 11, Case D 4, as Jim's check printed |
| `Quan`, `Flipped`, `RunId` | `Quan="1"` and `Flipped` on all 141 blocks. `RunId="-1"` on all 20, nested or not (Case J's four, Case I's none) | Unchanged |
| Parts and no sheets | Case I's Spring Blossom: 21 parts, no sheet, no run. Case H's two of BUILD 696 have been saved since | SAVE_NEST, on real bytes |
| Case A | Its `.opt` and every sheet are 49x97, as its programs read. The readback's "61x109" is fixed words in Jim's `opt_readback.py`, printed whatever the file says | Build spec 1.3's puzzle is closed: the file never said 61x109 |
| What the nest names against the programs on G: | 19 materials with a folder: missing programs on 6 (in Cases C, E and H, 16 in all), and 35 programs it doesn't name, 25 of them merged | Missing stays NOT CHECKED and never DO NOT CUT, as one would be a false alarm on half the jobs. Extra stays a log line |
| `MozaikJob.UNS` | 7 of 7 are the 7 bytes `JDELA\r\n`. No `.LCK` came: Case E was open when zipped, and its lock wasn't in the zip | Unchanged. A lock's contents are still unseen |
| `sheet_size_check`'s output | Exits 1 on all 10, almost all for its reconcile lines. Prints "ok" for Case I's nest with no sheets, from the header alone | Its reader takes each material's size line, and an ok only beside at least one physical sheet and all its programs on disk. Reconcile lines are a log line, a program not on disk is could not verify, and anything not seen is could not verify, `??` included |
| `br_cam_check --nest`'s output | Its exit code fits what it printed on all 10. Its counts of doubled holes equal NG Runner's program reader's on all 20 materials: 16 (A), 36 (B), 8 (D), 40 (J), 0 on the other 16 | Its reader takes each nest's lines. The agreement is a test |
| The two sheet size checks | They disagree on 4 materials. Jim's sees a header on the wrong sheet whose parts fit (Case E's 1/4 on 61x109, Case H's 1in on 49x120), and NG Runner's sees sheets added by hand (Case B's 3/4, Case H's 3/4), which Jim's can't, as it reads one size per nest: the first `<OptimizeMaterial>`'s | Both run. Either's fault is DO NOT CUT, and a disagreement opens one DEV item (`checks_disagree`) |
| Nest checked on real cases | With no design match, none reaches it. With one assumed, 6 of 20 do (Cases F, G, I's Egger Black, and three of Case J's), and none of the 9 known bad. Cases F and G are the nests from an unsaved state: only the design match can catch that | The property test stays exact. A pass still needs a design match, which has no reader yet, so Nest checked stays out of reach in the app |
| Running them in the app | They import only `sys`, `re`, `os` and `units`, and write nothing | `watch.nest_checks` runs both from the adopted toolkit copy on staged copies of the nest and its programs, through `run_tool`, and refuses a copy not the bytes it read. Nothing adopts a copy yet (group 3), so today it is None |

## Phase 2's rulings and Jim's approvals (2026-10-31)

Adopted as Sulaiman asked for decisions and no questions, and he can overrule any of them. The reasons and measurements behind R1 to R8 are in `docs/p2/build-spec.md`, section 9.

**Jim's approvals, as Sulaiman reported them:**
- **His tools into his toolkit (RULES section 8).** NG Runner's shared tools may go into `_NG_Automation`, each through his regression and as a reviewed delivery. NG Runner never writes into his toolkit by itself.
- **Drawings to a model (decision 10).** The reader evaluation may send elevation crops to a model: crops only, with no text on them, never a page, a name or an address. The provider and model are decisions 16 and 17, which the evaluation decides.

| Question | Ruling |
|---|---|
| R1: run Jim's scripts on this PC | Yes: on a staged copy laid out as his tree, in the tools' Python, isolated, inside a Job Object, on a local disk, and never in `.local-fixtures`, in place or on G:. As built: "Adopting a copy of Jim's toolkit", below |
| R2: where the converter lives | NG Runner's code (`ngrunner/convert/`), his rules mirrored in `rules.py` and tied to his `takeoff.py` by `ast`. It is offered to his toolkit through RULES section 8 only after 10 of his conversions agree |
| R3: the PDF reader | pypdf, as pinned |
| R4: what the converter can't place | Unplaced, reading "could not verify" and naming the line; never a guess. Garage and mixed jobs stay in chat until BUILD's garage rule is ported, and so does a job whose designs don't say closet or garage |
| R5: the library against RULES on 3/4 Classic White | RULES' 49x97 until Jim answers. `material_lookup` is not used for sizes meanwhile |
| R6: two sheet numbers | Both carried, named: `sheets_with_waste` (RULES 141) and area ÷ 32, the tracker's Calc Sheets |
| R7: `run-tool` and crosscheck | `NGRunner.exe` takes a first argument ending `.py` as `run-tool <script>` (`ngrunner/__main__.py`) |
| R8: a `.opt`'s version line | `opt.py` sets aside one leading line of digits and reads UTF-8 with no BOM. What real files showed: "Real nests", above |
| The heads-up when NG Runner's takeoff isn't complete (Sulaiman's ruling) | The chat's IC Conversion JSON, sent whole and marked "From the chat's takeoff", as that is the list Barbara and Will order from today. Only when neither gives a whole list does it say "could not verify the takeoff". Never a partial list, and never a mix of the two |
| The shadow run on Sulaiman's PC (Sulaiman's ruling) | It reads the real NG Jobs, in shadow mode, on his yes. It writes nothing there |

## Built on those rulings (2026-10-31)

Where the builders went beyond or differed from `docs/p1b/build-spec.md` and `docs/p2/build-spec.md`, read from the code and its tests. Each is a default, and Sulaiman can overrule any of them. They settle what three rows above left open: "No toolkit" (group 2), and "Nest checked on real cases" and "Running them in the app" (Real nests).

### The nest-to-design match (`ngrunner/guards/design.py`)

Measured on the 20 real nests of the 10 real jobs (`tests/test_real_designs.py`): 10 match, 9 are "Not verified", and 1, with parts and no sheets, is not compared. With Jim's two checks as he ran them, only Cases F and G reach Nest checked, where 6 of 20 did with a match assumed, and no known bad nest does.

| Question | Ruling | Why |
|---|---|---|
| A nest that isn't the saved design | "Not verified": DO NOT CUT (row 5 of `decide`), red, to George and in the way of Finish lock. A match it couldn't make is could not verify: NOT CHECKED, with its note. Only "matches" is ever part of a pass | The design names this state. A nest from an unsaved state, or a design changed after nesting, is what Jim's two checks can't see (10-20) |
| What it reads | Jim's `des_cutlist`, run from the adopted copy on staged copies of the rooms, and a stand-in that reads the rooms by bytes for what `des_cutlist` doesn't print: each part's place, operations and hand-set material. The two must count the same parts, or nothing is concluded. `des_cutlist`'s output is read whole: a line it doesn't print is could not verify | Rule 4: the cutlist is his. The stand-in goes once his toolkit prints these itself |
| Material | Compared only where the design carries one Jim set on the part by hand (`MatORId`, `TextureORId`). Material from the room's templates isn't read | `des_cutlist`'s colour is no stand-in for it: on Case I it names 15 of 22 parts by a texture none of them is nested on |
| Operations | Every operation the design gives a part must be on the nest's part, value for value, equations left out. The nest's part may hold more | Mozaik applies joint fastener templates at optimize, and doubled ones are `br_cam_check`'s to find |
| Sizes | Within 0.05 mm is the same, for sizes and for numbers in operations | `des_cutlist` rounds to 0.1 mm, and `panel_xcheck` reports nothing of 0.05 or less |
| Counts | Pooled across every nest of the job | One size can be split between two materials (Case B) |
| A room missing from the nest | "Not verified", naming the room. So is a nest room the saved design lacks | The design changed after nesting (Case J's Room 4) |
| Counter tops | Size and count only, from `des_cutlist`. One in no nest is "Not verified" | The design holds no parts or operations for them |
| A nest with no sheets | It takes only what nested ones leave, and is never itself judged a match. Its verdict is SAVE_NEST | Its parts aren't placed |
| What a pass rests on for a room | Its design identity (`design.room_identity`), in place of the file's hash: the room as it compares, less what a re-save of the same design changes (two counters, Room 0's product numbers, annotations). The stored `room_hashes` hold these | On the 30 real `.des` and `.bak` pairs, 20 differ in bytes, `des_cutlist` prints the same from all 30, and the identity is the same on 29. The 30th (Case G's Room 1) differs in groove Hide flags on 5 panels, a real change, and its nest sides with the `.des` |
| Its words | Parts named by tag only (R1C3), 8 at most and then "N more" | The lint refuses Mozaik's part names |

### Adopting a copy of Jim's toolkit (`ngrunner/toolkit.py`, the supervisor's night step)

| Question | Ruling | Why |
|---|---|---|
| When a copy is adopted | Each night, on a worker of its own, `_NG_Automation` in Mozaik Shared is copied whole and hashed. A copy that isn't the adopted one's bytes runs his `regression.py` from its `scripts` folder with no argument, so never `--accept`, for up to 15 minutes. It is adopted only on exactly his clean line, exit 0 and nothing on stderr, and only if the copy's hash still holds after. A copy with no baseline is never run | His RULES section 8 adopts a change this way. With no baseline, his regression writes one and says nothing moved |
| What a try comes to | Adopted: an event, and the DEV items below close. Unchanged (the adopted copy's bytes): they close too. Refused: DEV `toolkit_refused`, saying how many things moved and never which. Failed, when no copy could be made: DEV `toolkit_failed`. A try over an hour is given up: DEV `toolkit_stuck`. One open item per code, replaced when its words change, saying which copy the checks go on with | Sulaiman hears, and the last adopted copy goes on. His regression's items name his frozen jobs |
| Copies kept | The two newest adopted copies, each at `<toolkit folder>\<its sha256>`. A refused copy is deleted, and so is any other folder NG Runner named that way, and a try a stop cut short | One to fall back on. Only names NG Runner gives are ever removed |
| No `_NG_Automation` in Mozaik Shared | The night step does nothing, and says nothing | Every PC is in this state until Jim's toolkit is in its Mozaik Shared. Open: neither Health nor DEV says so, so a toolkit that goes missing later shows only as no Nest checked |
| Which Python runs his scripts | In this order: `NGR_TOOLS_PYTHON`, where a path that isn't there runs nothing; `toolspython\python.exe` beside a build's `NGRunner.exe`; in a build, `NGRunner.exe run-tool`; from the repo, `python -X utf8 -m ngrunner run-tool`. The tools' Python runs `-I -B -X utf8`, the script's folder first on its path, and a script it starts inherits only `PYTHONUTF8`, `PYTHONDONTWRITEBYTECODE` and `PYTHONNOUSERSITE` of the `PYTHON*` variables. Nothing in the build makes `toolspython` yet | His scripts import openpyxl, pdfplumber, numpy, Pillow, scipy and PyMuPDF, pinned in `tools/tools-venv.txt` and never in NG Runner's build. The checks and regression import only the standard library and each other (read by `ast`), so NG Runner itself can run those |
| Where | The copy, each run's folder and everything staged on a fixed NTFS or ReFS drive of this PC: never a network path, and never a cloud drive, which is how Drive for desktop's G: shows itself (FAT32) | A run must be of what was hashed, and a check never runs in place |
| The copy's hash | Checked before and after every run. A copy that changed runs nothing, or what it said is not used | A verdict must say which scripts stood behind it |

### The check runner's readers (`ngrunner/checks/`)

Measured on Cases A to J (`tests/test_checks_real.py`): no case passes whole on this PC, as `wm_variant_check` and `parm_check` read could not verify on every case.

| Question | Ruling | Why |
|---|---|---|
| What runs | crosscheck's own `CHECKS` list, read from its source by `ast` and never imported: 14 checkers on the copy of 2026-10-31, `ic_code_check` advisory and never deciding. Every result names what crosscheck leaves out: `hinge_check`, `sheet_size_check` and `br_cam_check --nest` (Q17a). The two nest checks run in the guards; `hinge_check` runs nowhere yet | INDEX says crosscheck runs every checker. Its source runs 14 |
| On what | A staged copy of the job's rooms, its `-JobParms.dat` files and its IC Conversion, with the template job's parameter files beside it when there is one. crosscheck writes its `_crosscheck.txt` into the staged copy, never into NG Jobs | Run on the real folders, it would write on G: and read rooms Mozaik may be saving |
| crosscheck's own output | Read only when all of it fits: its exit code with its verdict line, its rooms line with the rooms staged, its sections with its list, its flags with its sections, and nothing on stderr but Python 3.14's warning about `datetime.utcnow()`. Anything else is could not verify for every checker | Fail closed |
| A finding crosscheck doesn't flag | Failed, flagged by NG Runner itself, an item to fix with its chat line, which Jim may settle for a job: `outset_check`'s products not at Outset 0, and `eb_tops --check`'s and `top_line --check`'s "would change" over 0, each with exit 0. Until 2026-10-31 it was could not verify, as a question for Jim; answered from his files (question 26, below) | crosscheck flags only a non-zero exit or a line with `!!`, MISSING, EXTRA, "drifted" or "differ", so these read as clean there. Outset is one Jim always wants flagged |
| `parm_check` with no reference job | Could not verify | It prints "no counterpart" for every file and still says clean. The template job isn't in the zip |
| `wm_variant_check`'s library | A fault it finds in the geometry fails; a clean read is could not verify | Its library is a Linux path, so on Windows the library check is skipped while it still prints that the equation matches |
| A checker that crashes | Could not verify | crosscheck flags a traceback as a fault, but it found nothing either way (`panel_xcheck` on three real conversions) |
| `??`, a NOTE asking for the job to be opened, a deferred check, a line not seen before | Could not verify | A person must look |
| What a person sees | NG Runner's fixed words only. What a checker printed is kept for a repair item, never shown | The checkers print room and client names |

### The tracker inbox (`ngrunner/tracker_inbox.py`)

| Question | Ruling | Why |
|---|---|---|
| Its shape (departs from contract 5.6) | `inbox_rows(job, takeoff, *, verb, names, status, optimizer=None)`. `names(colour, thickness)` gives each bucket's tracker names; `status` is required to add rows and optional to set them; `optimizer` holds nest numbers by tracker name, and without it that column stays blank. `landed()` also takes `dropped`, when the batch was dropped | What a bucket is called on the tracker is Jim's rule (RULES: Tafisa names, and every material matching the Pricing block character for character), so the caller passes it. Rows are added at Lock design with the job's status, and set at Finish lock with the nest's numbers |
| What it refuses, whole, in plain words | A takeoff with any line it could not place; a bucket with no tracker name, or tape with no tape name; two tape colours under one tape name; a nest number for a material the takeoff lacks; a number that isn't a size; a cell that isn't one line; new rows with no status; a batch with no header first, no rows, or a row the ingest doesn't read; and a file inside `NG Conversion program Claude`, one already there, or a path that isn't full | Never a batch that is partly right. Jim's ingest reads from line 2, so a batch with no header loses its first row and is still archived as ingested |
| Two buckets under one tracker name | One row, with their sums | The ingest's `_SET` finds a row by job and material |
| Calc Sheets | Area ÷ 32 (R6), never the sheets with waste | What the tracker's Calc Sheets holds |
| Customer, and the Job Summary note | Left blank on every row | No client data. Whether the tracker takes a blank customer well is UNVERIFIED |
| Did it land | Each row as the snapshot shows it: landed, missing or differs, numbers equal within half their last shown place. A snapshot taken before the drop judges nothing | Calling its rows missing would invite a second drop |

### The takeoff on intake (P3)

| Question | Ruling | Why |
|---|---|---|
| When | Each summary filed is queued for its takeoff in the transaction that files it, and made off the tick's thread, one at a time; a newer filing takes the place of one not made yet. A job found at install, or in `NG Jobs\Old`, gets none. A summary it can't read, 6 tries with no filed bytes to read, or a run over 15 minutes is a failed takeoff: DEV `takeoff_failed`, and the chat's takeoff is used | A crash can't lose it, and a takeoff never holds up a filing |
| Which jobs | Closets only. Garage, mixed and unclear jobs stay in chat (R4) | |
| The store (migration 5, `takeoffs`) | One row per summary and rules version, with `state` (`made`, `in_chat` or `failed`), `branch`, `complete`, and `could_not_verify` as a count, where contract 4 had a JSON list. `result` holds codes and numbers only: an unplaced line is its page and quantity, never its text or reason | A line's text can quote anything, a client's details included |
| Complete | Every row placed, every bucket with a sheet size, and the job a closet | Anything less is a partial list |
| Which rules made it | `rules_sha256` holds `rules.fingerprint()`, a hash of `rules.py`'s tables, where contract 4 had the hash of Jim's `takeoff.py` | The app never reads his file: it runs from those tables, and `tests/test_convert_ties.py` holds them to it |
| The order heads-up | From NG Runner's own complete takeoff. Otherwise the chat's whole JSON, marked "From the chat's takeoff, since NG Runner's own takeoff could not verify N lines". Otherwise "could not verify the takeoff", with how many lines. A heads-up from the chat's JSON always says so on its first line. With no takeoff of NG Runner's (a garage or mixed job, a summary it couldn't read, one replaced by hand, a job filed before this), the chat's JSON, as in P1b | Sulaiman's fallback ruling, above |
| Where it is sent from | The guards' pass (`guards/takeoffs.py`), for either source, through one `heads_ups` row per folder: one message per content, and "revised" whichever source sent the one before | One place picks the source, so the two never both go. So a heads-up from NG Runner's takeoff waits for the guards' next pass |
| Colour names | RULES' brand for the IC colour code, as `material_lookup` maps it: "Egger Classic White"; a brand of many colours with its code, "Tafisa (IC AP)"; a code with no brand, "IC colour ZZ". On a job with a white part built 1in or thicker, all its white is AMWH and reads "AMWH (IC WH)", its 1/4 white backs on White 1/4 Vinyl at 49x97 (question 10, below; until 2026-10-31 it read "AMWH or Egger Classic White (IC WH)" with no size for the backs) | NG Runner has no name of its own for a Tafisa colour. RULES 214 makes white AMWH beside anything 1in or thicker, and the RULES of 2026-10-06 says of the whole job |
| Decision 12b on NG Runner's takeoff | No colour-swap line | It names colours by IC code, so there is no IC colour text to read a swap from |
| Jim's chat (notes schema 3) | `<folder>_NGRunner.json` carries a `takeoff` block, null until made: its state, branch, whether complete, the could-not-verify count, the rules fingerprint and the summary's hash, and once made its buckets, tape, board parts, estimate total and unplaced lines by page. `docs/p1a/rules-lines.md` gives the chat its line | It is what the chat reads first |

### Others

| Question | Ruling | Why |
|---|---|---|
| The `.bak` reader (`guards/bak.py`) | Findings only: whether a room's `.bak` is newer or larger than its `.des`, and a symmetric diff, part by part. Never a verdict, and nothing gates on it, until Jim answers Q19 | The design's `.bak` rule waits for P3 and Q19. On the real rooms, size and contents are measured, never file times (`docs/p2/build-spec.md`, section 10) |

## P5's reader, and Phases 3 and 4 wired into the app (2026-10-31)

| Question | Ruling | Why |
|---|---|---|
| P5's drawing reader | **Claude Opus 5.5, direct.** GPT-6 Luna is rejected and OpenRouter is closed (Sulaiman's call, on these numbers) | On the 121 new walls, read once: Claude accepted 118 with a reading error on 4 walls, all under an inch, none gross. Luna, tuned in six rounds, accepted 57 with a reading error on 21 walls and a gross misplacement on 6. It costs $0.032 a wall against Luna's $0.015, about $2–3 a month |
| Which walls go through without Jim | None, yet. Every wall is shown to Jim | Every category with 20 or more walls had errors. Most of Claude's silent misses (76 of 84) are Jim's build choices, not reading: where a run starts, the top line, and the gap under stacked uppers. P5 builds those three as code rules first. REA and hutches have no walls in the test yet |
| The check that keeps client details out of a request | Stays as strict as it is | It blocked 1 wall of 122, on catalogue words. Loosening it waits for P5's build |
| A shadow batch and the in-flight rule | A shadow batch never blocks another job's drop. It blocks only a second drop for its own job. It closes when the chat's own rows for that job appear in the real tracker's snapshot (read only), as matched or differing, and that comparison is P4's "5 locks match the chat" test. With no chat rows after 7 days it closes as not compared. **Built** (P4's second round, below): the 30-minute DEV item is now for a live batch only | In shadow nothing ingests NG Runner's test folder, so the first batch would otherwise stay in flight forever and block every later drop on every job |
| The tracker's names for a batch's rows | **Built** (P4's second round, below): NG Runner's own takeoff is the first source, each bucket named by RULES' names (`convert/tracker_names.py`, which the workbook's JSON carries too), and every name held to the snapshot's Pricing block; the chat's IC Conversion only when NG Runner's own takeoff isn't complete, whole. Until 2026-10-31 the strict stopgap took every name from the chat's IC Conversion | Only 23 of 52 real conversions name their buckets. A name not in the Pricing block costs the job $0 silently (RULES' hard precondition) |
| A fix's survival in shadow | Proven only in the day simulation until go-live | A shadow fix is written into NG Runner's own copy of the room, which Mozaik never saves |
| P4's messages | To Sulaiman, marked SHADOW, in live mode too while P4 is in shadow | Nothing of P4's reaches Jim before he approves it |
| A failed check with open could-not-verify items | The lock gate refuses it | A check that couldn't look at everything hasn't cleared the job |
| Left for P4's next round | **Built** (P4's second round, below): `lock <Job>` in the command box; "fixes not yet verified" in the heartbeat's risks; one settled list for all jobs; a day-simulation scenario running a real `cnc_job_sheet` | Not needed for the shadow trial |

## P4's second round (2026-10-31)

Built on the rulings above, test-first. Each row is a default where the rulings left a choice, and Sulaiman can overrule any of them. Store migration 8 adds, with defaults, whether a tracker batch is shadow (every batch before it was), which takeoff its rows came from, when a shadow batch closed, and a settle's scope with its checker and what.

### A shadow batch (`lock/shadow.py`, `drop.in_flight`)

| Question | Ruling | Why |
|---|---|---|
| What blocks a drop | Shadow: only an open batch of the same job. Live: every open live batch of any job, and any `tracker_inbox` file waiting in the real folder, as before. A shadow batch never blocks a live one | The ruling above. Jim's ingest takes one batch at a time, so live stays global |
| When the chat's rows count as there | Any row of the job's on the real tracker's snapshot taken after the drop. A batch that carries nest numbers or Jim's LOCKED (Finish lock, Lock) waits until the chat's own Job Summary reads LOCKED, as the chat's nest numbers come with its lock | Comparing NG Runner's nest numbers with the chat's Touch 1 rows would call every Lock "differing" |
| Matched | Every row of the batch has one chat row of the same material name, with the same numbers to within half their last shown place (as the landing check holds them), and a whole-job batch (Lock design, Lock) has no chat row it lacks. A Lock design's In progress is never compared with the chat's status | The chat may have locked the job by the time its rows are compared, which says nothing about the rows |
| Differing | Each difference in plain words, both sides' numbers: "Egger Classic White: Calc Sheets 8.3 on the chat's row, 8.2 on NG Runner's", "...: the chat has no row by that name", "...: the chat has a row NG Runner's batch has not". The board and the message show the first three and a count | Which rows differ, in plain words (the ruling) |
| Who hears | Sulaiman, marked SHADOW, once per batch; a line in the LOG lane, so nothing of it reaches Jim's digest. No DEV item, and the 30-minute one is for a live batch only | P4's messages go to Sulaiman while P4 is in shadow |
| "5 locks match the chat" | Counted by job on Health ("2 of 5 jobs' shadow locks match the chat; 1 differs"): a job counts when its shadow batches made from NG Runner's own takeoff matched and none differed. A batch from the chat's IC Conversion, one from before the source was kept, a live one and a re-send are never counted | A batch made from the chat's own list under its own names would match the chat by construction |
| A simulated ingest | When an archive of a shadow batch does appear in the test folder (the day simulation's ingest), the batch follows the after-drop flow as a live one would | So the confirm, doubt and re-send flow stays proven in shadow, as the fix's survival is |

### Tracker names from NG Runner's own takeoff (`convert/tracker_names.py`, `lock/names.py`)

Measured on the 52 real pairs against the real snapshot's Pricing block (`tests/test_lock_names_real.py`). **Before**, with the stopgap: 11 locks could be named, 8 of them with every name in Pricing. **After**: 31 named with every name in Pricing exactly, 30 from NG Runner's own takeoff and 1 from the chat's. The 21 refused each say which name or why: 7 have no whole takeoff on either side; 2 have `Egger Classic White (1/4) 5x9` in Pricing twice; 1 each is Arauco, Houghton and River Rock in RULES' form, which Pricing spells otherwise; 1 is Blue Stone; and 8 are the chat's own names or tape keys, which Pricing lacks.

| Question | Ruling | Why |
|---|---|---|
| The names | RULES section 2's map, mirrored and tied to his text (`tests/test_convert_tracker_names.py`): an IC colour word becomes its Tafisa name, Charcoal and Eggshell Ivory keep their own names, and the IC code's colour word is `material_lookup`'s ALIASES. The forms are the tracker's own, as its Pricing block spells them: a Tafisa sheet `Tafisa <Tafisa name>_<IC word> (3/4)`; Egger Black and Arauco `<name> (3/4)`; AMWH `Am Lam Wh (3/4)`, `Am Lam Wh (1 in)` and `White 1/4 Vinyl`; Egger Classic White `Egger Classic White` at 3/4 (the spelling the chat's rows use, priced the same as `(3/4) 4x8`) and `Egger Classic White (1/4) 5x9` on its 61x109 backs | No doc of Jim's writes the tracker's forms down. Six of the Pricing block's nine Tafisa sheet keys have the first form |
| Tape | `<material> 1mm — <width>` (the Pricing block's own hint: 3/4 takes 15/16" / 1", 1 in takes 1-1/4" / 1-5/16"). Every Tafisa colour takes the one `Tafisa` tape, its EB Colour `<Tafisa name> (<IC word>)`: BUILD 587-596's form without the product code, which NG Runner can't verify. Egger, Arauco and AMWH carry their own names | BUILD: "one price covers every Tafisa colour"; the EB Colour says which |
| Never a guess | A colour RULES gives no Tafisa name (Mocha, Blue Stone), a code with no brand, or a thickness with no form is refused, saying why. RULES' spelling is kept exactly, so Houghton's "Winter Fun!" refuses against the tracker's "Winter Fun", Arauco's "Prism SF210 Antique White" against "Prism Antique White SF210", and River Rock's Tafisa form against "River Rock (Tafisa)" | Character for character (RULES' hard precondition). Each is a question for Jim: his RULES and his Pricing disagree |
| The Pricing check | At the click, before any row is made: each name in its block exactly, once (a key that is there again with a trailing space counts as twice, as the drop counts it), with a price. Refused with which name: "Egger Classic White tape 15/16" isn't in the tracker's Pricing exactly as NG Runner names it, so the rows can't be made" | The real Pricing block has `Egger Classic White (1/4) 5x9` twice, at $65 and, with a trailing space, $60: 2 real locks refuse on it until Jim says which |
| The chat's IC Conversion | Only when NG Runner's own takeoff isn't complete (lines it could not place, a garage or mixed job, none made): its buckets' numbers under its own names, and its one tape key per board, whole, held to Pricing the same. Never a mix: a complete takeoff of NG Runner's that can't be named is refused, never handed to the chat's. The lock panel says when the rows would come from the chat, and a button is no longer blocked for want of NG Runner's takeoff | As the order heads-up's fallback. The stopgap put NG Runner's numbers under the chat's names |
| The workbook | Each bucket of the workbook's JSON carries its tracker names (or why it has none), saying each must be checked against Pricing, which the workbook can't read. The delivered converter changes with it, so it reaches Jim only as a reviewed delivery (RULES section 8) | The chat reads the names NG Runner's lock would use |

### The rest

| Question | Ruling | Why |
|---|---|---|
| `lock <Job>` | The command box's grammar, as `check <Job>`: the one job the name names whole, then exactly the board's Lock button (lock design and every Finish lock at once), held to the same gate. Never "lock without check", which stays a confirmation of its own on the board. A name that is no job: "NG Runner doesn't know that job, so nothing was locked." | One gate for a lock however it is asked |
| Fixes not yet verified | The heartbeat's open risks add each fix approved and not written, each written and not yet judged by the room's next save, and each whose survival could not be verified, marked "(shadow)". Jim's digest doesn't carry them while P4 is in shadow. A fix that held, that Jim changed, that Mozaik undid (a card of its own) or that was undone is no open risk | Design 6.5 names them. The watchdog repeats the heartbeat's risks only when no digest went |
| A settle for all jobs | A third choice on a check item, "Not a problem for any job", which needs the condition in Jim's words (the board's box is required, and the store's side refuses a blank one). It stands for every finding of its kind, the checker and what it found, on any job, through new runs and design changes; a never-silenceable item can't be settled so. The first given for a kind stands | Design 6: "All jobs" must name a condition and takes effect at once. A finding's fingerprint names its own job's parts, so a kind is all that holds across jobs |
| The settled list | `NG Jobs\_NG Runner\NG Runner settled.json`, written through commit_write as P4's shadow writes are, into NG Runner's own copy of that folder until P4 goes live, and again whenever the copy is missing or isn't what the store says. Each entry: the checker and what, NG Runner's words for that kind, Jim's condition, how and when, active, and whether RULES absorbed it (false until Jim's accept in his chat). Each job's notes carry the settles that stand for its items, with their scope, and name the list. A list that can't be written is one DEV item, `settled_list_failed` | Design 3.3's "Settled entry". The RULES line that has Jim's chat read the list is still to be drafted for his approval (`docs/p1a/rules-lines.md`, "Not needed yet") |
| The job sheet in the day simulation | Scenario 95 runs Jim's real `cnc_job_sheet.py` from an adopted copy of his real toolkit, in the tools' Python, on Case A's real JobDat.dat and nests, asked as a Lock that sets LOCKED leaves it: the sheet matches and is filed in NG Runner's own copy only. It skips without his toolkit or the tools' Python | Scenarios 81 to 83 drive the click; a real Lock on a real job also needs a passing check, which no real case gives on this PC |

## P5a placement: Jim's build choices as code rules (2026-10-31)

`ngrunner/build/placement.py`: one pure function per rule, each citing the line of Jim's docs it ports (his toolkit of 2026-10-31), and "could not verify" with the reason wherever his rules don't settle a case. `python -m tools.reader_eval.run place --provider anthropic` puts Claude's cached readings through the rules, room by room, and re-scores them against the as-cut `.des` with the same scorer and tolerances (1/8 in x, 1/4 in top and bottom). No model is called. The rules were decided from Jobs A to J's 26 walls and Jim's docs; the 121 walls of the P5 jobs are the test. Their diagnoses and three of their drawings were seen while the docs were read, but no rule or number comes from them.

| Rule | What the builder does | Source |
|---|---|---|
| Top line | One T per room; every WM panel hangs on it, Elev = T − height. T is the top of the highest FM panel reaching above the run's lowest bottom; with none, a run drawn within 1 in of 83.94 in hangs on 2132; a T within 1 in of 2132 is 2132. With no FM panel beside it and a drawn top elsewhere: could not verify, since a builder has no "as built" top, only the drawing's | RULES 329-339 (`top_line.py`'s `room_top` is Jim's check), as questionnaire Q2 recommends. BUILD 244 and 294 ("T = 2132, always") predate it; RULES outranks them |
| Mount and height | WM when the panel's own IC line has FN, LN or RN, else FM; the height snapped to 32k+20, more than 1.3 mm off: could not verify | RULES 290-292, 318, 325-326 |
| Stacked gap | A stacked upper with a hanging notch is WM and hangs on T. One without is FM: it stands on the closet counter, Elev = its base's height + 19.304. No WS line on the wall, no base, or bases of two heights under it: could not verify | RULES 73, 218, 221, 310, 326; Jim's own build script of Job E ("uppers stand on the counter"), tied by `ast` in `tests/test_build_placement_real.py` |
| Run start | Never moves a run. Drawn tight to the wall's start (within 1/8 in), or 3 in or more along it (a Wall Filler or an opening): as drawn. Any gap between: could not verify. Jim's builds of the same drawn gap start the run tight to the wall (Jobs C, H), centre it (Job C) or keep it (Jobs A, B, E, I); BUILD 274 says how to read the WIDTH row, not where to build | RULES 264, BUILD 274, 276 |

Measured on Claude's cached readings (silent values: build choice and reading as `diagnose.py` splits them):

| | DEV, 26 walls | TEST, 121 walls |
|---|---|---|
| Silent build-choice errors, before → after | 33 → 15 | 76 → 70 |
| Silent reading errors, before → after | 2 → 2 | 8 → 8 |
| Walls with any wrong value, before → after | 9 → 6 | 20 → 18 |
| Walls, and values, newly wrong | 0, 0 | 0, 0 |
| Walls with a could-not-verify | 10: run start 8, top line 2 | 28: run start 24, top line 4, stacked gap 1 |
| Silent errors left on walls no rule stopped | 0 | 35: 30 on cut WM panels whose Elev is off the 32 mm grid, 5 reading |

- **Per category, build-choice errors before → after.** DEV: stacked tower 18 → 0 (Job E's uppers, all three walls now right), radius 15 → 15 and flush end 15 → 15 (all on walls that could not verify), bridge 4 → 4, plain 0 → 0. TEST: radius 34 → 28, flush end 68 → 62, plain 40 → 40, bridge 10 → 10, stacked tower 0 → 0 (its 3 errors are reading). The top line placed the WM panels of 8 DEV and 62 TEST walls, and none got worse.
- **The 30 left unseen in TEST are the as-cut rooms', not the rules'.** Each is a WM panel the archived room hangs at an Elev of 32k+20 (top 2120, 2152 or 1960), the rule Jim's RULES of 2026-10-06 calls an error ("32k+20 is the HEIGHT grid, not the Elev grid", the toolkit's `RULES_full_20261006.md` 638-648, which names one of these jobs) and today's gate 5 refuses (RULES 319). Claude read 26 of them at 83.94 in, as today's rule hangs them, so they were silent before too. The other 4 are one wall whose room cuts one run at 2132 and another at 1960, two tops in one room (a defect, RULES 330-331): Claude read it within an inch of 1960, and the rule hangs it on the room's 2132, still wrong against that cut, now by 6.8 in.
- **Jim's frozen jobs**, their own panels standing in for the drawing (no reader has read them), so "as drawn" holds there by construction and the top line and hung panels are the real test. **X:** T 2132 from its FM panels; all 9 panels off the floor, its stacked tower's 3 WM uppers included, where frozen; its runs start 24 in or more along their walls, as drawn. **Z:** T 2132 in all 4 rooms (one with no FM panel, by RULES 338); 10 of 10 hung panels where frozen; runs drawn tight, as drawn (Z centres a 1/8 in slack, inside the tolerance). **Y:** could not verify. Its T, 2408, puts both WM Elevs on 32k+20 (its build script says so), the rule RULES now calls an error; and its runs start 0.78 and 1.5 in from the wall. So today's rules reproduce 2 of the 3; Y would need Jim's T.
- **Left for Jim** (the questions he would answer by building it): where a run starts when the drawing leaves under 3 in at the wall's start; and T for a room with no FM panel beside its WM run whose drawn top is not near 83.94 in (Job F's room is cut at 96 in exactly, Elev 36 in, on neither grid).
- **Limits of the measurement.** FM panels on a room's walls that weren't read count as standing on the floor. A read vertical takes its IC line by the height it named; when that height's lines disagree on FM or WM it could not verify. A rule never changes x, so the run-start rows only move errors from silent to flagged.

## P5a builder, slice 1: the room model (2026-11-01)

`ngrunner/build/`: `room.py` is the model's types, `walls.py` a room's walls from its design's WALL/AREA table, and `schedule.py` each wall's panel schedule and the room. In memory only: nothing reads or writes a file. Every value is a `Fact`: the value, its source (the table, the plan, the reading, the IC line, or a rule) and its status (set; review, where Jim's rule keeps a value but flags it; could not verify, with the reason; not needed). `python -m tools.reader_eval.run build --provider anthropic` builds it from Jim's frozen jobs and from Claude's cached readings and compares it with the as-cut rooms value by value (`tools/reader_eval/built.py`). No model is called. Each row below is a default where Jim's docs left a choice, citing his lines (toolkit of 2026-10-31), and Sulaiman can overrule any of them.

| Question | Ruling | Why |
|---|---|---|
| A wall's length and height | The table's inches × 25.4, rounded to 4 places; its WallNumber and IDTag are its number, and a wall not named by digits has no IDTag (could not verify) | BUILD 201-202 ("exact, never from pixels"); gen_rooms.py 78, 87-88; SCHEMA 14-15. NG Runner's own summary check reads the table |
| The chain's order and each wall's angle | From the plan view only. Nothing in NG Runner reads the plan yet, so without one they could not verify, and so do each wall's position, its start and the joints. Given a plan, its walls must be the table's, each once, and turn clockwise | BUILD 201-202 ("Only TURN DIRECTIONS come from the plan"), 209-221; RULES 454-457; regression.py 78-82. The chain is not always the table's order: gen_rooms.py 41-73 traces it between the plan's two free ends, and two of frozen job Z's four rooms run 5, 1, 2, 3, 4 and 4, 1, 2, 3, 5 |
| Where the chain starts | The first wall at (0, 0), never shifted. Joints name IDTags; H_Walls is the tallest wall | Frozen X and Z start there, and 13 of the 14 per-job build scripts that write walls never shift the chain. gen_rooms.py 82-85 and frozen Y's script shift it so no coordinate is negative; Jim's regression compare doesn't compare PosX or PosY (regression.py 201-204). Joints: gen_rooms.py 94-96, SCHEMA 20-21, and frozen Z's name IDTags 5 and 1; H_Walls: gen_rooms.py 111-113 |
| A vertical's own IC lines | Its wall's VT, VT RAD and Custom Vert lines of the height the reader named: VT RAD when it saw a radius, the others when it saw none | RULES 296-301 and BUILD 237-239: the parts list is a quantity list, the drawing gives the order |
| Height and mount | Placement's (RULES 291, 318; 290, 324-326). New: a line with no notch whose vertical is drawn above the floor with no base under it could not verify its mount | The line says floor, the drawing says hung, and neither is guessed. On one P5 job, 4 such panels are built WM Panel 48; the rule alone would have built them FM 84, silently |
| Depth | Its lines' one depth on SCHEMA's grid within 1.3 mm, else kept IC-exact with status review. Lines of two depths at that height: could not verify | SCHEMA 161-166, BUILD 286 (the grid and tolerance are build_verticals.py's, tied by `ast`). RULES 304-306: the DEPTH row says which, and the reading doesn't give it |
| REA and radius | REA from its lines (RULES 411-413). Radius is the reader's yes or no, held to the wall's VT RAD count; a count that differs, or a reading unsure of a radius, leaves every radius on the wall could not verify | RULES 294 ("a VT RAD line, never the drawing alone"), 317 |
| Gates 1 and 4 | A reading whose count or heights are not the wall's VT lines' (or one unsure of the count) leaves every value from the lines could not verify. Gate 2 then holds by construction, gate 5 is placement's top line | RULES 313-320: no vertical's own line can be told |
| x and Elev | x is the drawn left face to 3 places, could not verify where placement's run start is. Elev is placement's: FM on the floor 0, WM T − height, an FM upper on the counter | BUILD 283-284; RULES 292, 329-339; "P5a placement" above |
| Thru, Left or Right | Geometry: a face is covered by a wall within 6.5 in or a section within 7 in. Jim inserts the sections by hand after the verticals, so at build time a section is a bay: the gap from the face to the nearest vertical overlapping it by more than 1 mm, when that gap is an IC shelf width of the wall within 1.3 mm. A section seen past another panel covers no face. Both faces open, or a side that changes with where the run starts (as drawn, tight, centred): could not verify. A plain FM panel has no notch | RULES 361-372, 397-398, 459-460; BUILD 245-248, 274-277, 296; swap_rea.py 32-43 and 54 (his geometry for REA panels; its 6.5 in is tied by `ast`). The "past another panel" limit is what frozen X's stacked tower needs: Jim built the radius panel against its base Right and the upper beside it Left, though drawers lie 3/4 in past the base |
| Geometry and the drill codes | Held together, height by height: the sides geometry gives must be the drill codes' (FD Thru, LH Left, RH Right) as a set. Where they differ, could not verify | RULES 372 says the plan wins and RULES 62 ranks IC codes above geometry inference, so Jim's docs disagree (AGENTS.md rule 4). With the as-cut panels standing in for the drawing on every paired wall (measured 2026-11-01), the two differed on 7 groups of panels and the codes were what Jim built on 6 |
| The product | FM 84; FM 84 REA and its side; WM Panel 48 and its side, then Radius; WM 48 REA the same. A floor panel over 84 in: could not verify | BUILD 243; RULES 377-387, 415-417. Jim's docs name only FM 84, and the real rooms here hold 54 such panels on FM 84 (Jobs A, B, H, J and six P5 jobs) and 32 on FM 90 (four P5 jobs), at the same heights |
| What "reproduced" means | Jim's regression compare (his lines 188-220): walls by file order, length and height within 0.5 mm, angle within 0.01°; each panel found on its wall by X within 0.5 mm, then product, height, depth and Elev. The tool also compares mount, side, radius, REA and the top, and each wall's start relative to the first | His own check of a rebuild, tied by `ast` |

Measured 2026-11-01 (`tests/test_build_room_real.py` pins it):

- **Frozen X and Z reproduce, wall by wall and panel by panel**, with their own panels standing in for the reading and their own chains for the plan (no reader or plan reader has read them), so x and the chain hold by construction and the test is everything the builder adds. X: 4 walls, every length, height, angle, number and start right; 14 panels on 3 walls, every value right. Z: 4 rooms, 20 walls all right (2 rooms' chains are not the table's order); 16 panels, every value right. Top line 2132 in all five rooms.
- **Frozen Y could not verify**, as placement found: walls right (its chain is shifted 12.7 mm, gen_rooms' normalising); top line could not verify; of its 7 panels, 6 could not verify (x by the run start, 0.78 in out; Elev and top by the top line; 2 sides where geometry and the codes differ) and 1 is wrong: a panel today's geometry and its own RH line make Right, which Jim built Thru before the rule of 2026-09-21 (BUILD 260).
- **The 26 DEV walls (Jobs A to J), from Claude's cached readings, no plan**, within the evaluation's tolerances (x 1/8 in, Elev and top 1/4 in, height 0.65 in; depth 0.5 mm):

| Wall | Categories | Panels | Right | Could not verify | Wrong | Why could not verify |
|---|---|---|---|---|---|---|
| A/3 | bridge | 3 | 0 | 3 | 0 | run start; FM 84 or FM 90 |
| A/5 | radius, flush end | 3 | 3 | 0 | 0 | |
| B/5 | bridge | 3 | 0 | 3 | 0 | FM 84 or FM 90 |
| B/12 | no verticals | 0 | 0 | 0 | 0 | |
| B/14 | plain, flush end | 3 | 0 | 3 | 0 | run start; FM 84 or FM 90 |
| B/16 | radius, bridge, flush end | 4 | 4 | 0 | 0 | |
| C/3 | plain, flush end | 3 | 3 | 0 | 0 | |
| C/7 | radius, flush end | 5 | 5 | 0 | 0 | |
| C/12 | plain | 3 | 0 | 3 | 0 | run start; the reading's own doubt of x |
| C/16 | radius, flush end | 5 | 0 | 5 | 0 | run start |
| D/3 | plain, flush end | 3 | 3 | 0 | 0 | |
| E/3 | stacked tower | 4 | 4 | 0 | 0 | |
| E/5 | stacked tower | 6 | 6 | 0 | 0 | |
| E/9 | stacked tower | 8 | 8 | 0 | 0 | |
| E/13 | plain | 3 | 0 | 3 | 0 | run start |
| F/3 | no verticals | 0 | 0 | 0 | 0 | |
| F/4 | radius, bridge, flush end | 2 | 0 | 2 | 0 | top line |
| F/5 | radius, flush end | 3 | 0 | 3 | 0 | top line |
| G/3 | radius, flush end | 4 | 4 | 0 | 0 | |
| G/4 | bridge | 2 | 2 | 0 | 0 | |
| G/5 | radius, flush end | 5 | 5 | 0 | 0 | |
| H/3 | plain, flush end | 6 | 0 | 6 | 0 | the reading's own doubt of x; FM 84 or FM 90 |
| H/8 | plain, flush end | 3 | 0 | 3 | 0 | the reading's own doubt of x; FM 84 or FM 90 |
| I/3 | plain, flush end | 5 | 0 | 5 | 0 | run start |
| J/8 | bridge | 3 | 3 | 0 | 0 | |
| J/19 | no verticals | 0 | 0 | 0 | 0 | |
| **All** | | **89** | **50** | **39** | **0** | |

  Values that could not verify, by reason: x by the run start 21, x the reader doubted 10, Elev and top by the top line 5 each (Job F's room), product FM 84 or FM 90 13. Every height, depth, mount, side, radius and REA on these walls is right.
- **The 121 TEST walls (P5 jobs), for context:** 413 panels, 255 right, 134 could not verify, 24 wrong: 18 on rooms cut on the old 32k+20 Elev rule (as placement found), 6 of the reader's own x errors, and 1 side where geometry and the IC line agree on Left and Jim built Thru. The two new rules above (a floor line drawn hanging, a floor panel over 84 in) came from these walls, so TEST is no blind test of them.
- **Left for Jim**, besides questions 31 and 32: FM 84 or FM 90 for a floor panel over 84 in; which wins when geometry and the drill codes disagree (RULES 62 or 372); and, for a floor line drawn hanging, the line or the drawing.
- **Not in this slice, and what each needs.** Writing Room#.des: the plan (a plan reader, or a person's), then each room seeded from the template job's Room1 with Products, Walls and WallJoints stripped by a balanced scanner, `UniqueID` 50001 up, Name, `IdTagCount` as SCHEMA 32-37, the cloned `<Wall>`/`<WallJoint>` attribute set, coordinates as `f"{v:.3f}"` stripped (BUILD 145-150, 283-291), CRLF and the version line. Cloning products: the model's product names map to his 19 templates (read from the adopted toolkit copy at run time, never committed), resized (the groove's cached X = height − 191.9, RULES 400-404; an REA's slant, RULES 419-421), line bores stripped except on REA (RULES 423-427, BUILD 288), WM banding (RULES 431-450), `SourceLib` from the template (BUILD 289). The gates on the written room: RULES 313-322 (gate 6 by `elev_panels.py`), legal names (RULES 388), the notch face, WM banding, REA drilling untouched, then `regression.py compare` on the 3 frozen jobs, the exit test. And `commit_write` with the interlock, into NG Runner's own copy until P5 goes live (AGENTS.md rule 1).

## P3: the replay trial and the audit (2026-11-01)

What P3 could do without Jim: replay NG Runner's check against what Jim's chat reported on the same real jobs, audit every real chat-built job, and make the close prompt ask only when there is something new to check. NG Runner's check ran as the app runs it: Jim's crosscheck from a staged copy of his toolkit, in the tools' Python, on a staged copy of each job (R1), its IC Conversion read from NG Jobs, and his template job, `Lux Closet Template Claude`, staged beside it for `parm_check` (it came in his answers of 2026-10-31, `.local-fixtures\answers20261031\`). The jobs: Cases A to J, the 40 P5 jobs (P-tags; Case K is P39) and his 3 frozen jobs. Tests: `tests/test_trial_real.py`, `tests/test_checks_topline_real.py` (both `fixtures`), `tests/test_checks_trial.py`, `tests/test_checks_topline.py`, `tests/test_check_runs.py` and day-simulation scenario 96. Each row is a default, and Sulaiman can overrule any of them.

### The replay trial (`checks/trial.py`)

| Question | Ruling | Why |
|---|---|---|
| What the chat reported | Jim's crosscheck file as the chat saved it (Cases C to G), read by NG Runner's own readers, so nothing is transcribed. The chat's own reports (panel checks and crosschecks it wrote itself, 20 jobs) read by a person into NG Runner's vocabulary, as each report's last word to Jim stood; that reading is in the test, by job code. An item about what no checker looks at (materials, the tracker, prices, the nest) is counted as outside the check | Its delivery's act items are what Jim acted on (RULES 0) |
| The unit | One finding: a checker, the kind of fault (`findings.py`'s codes) and one part (R2C3, R2, or a size for the panel set). Only the checkers the chat's output speaks for are compared | Finding by finding, as the brief asks; a panel check says nothing of notches |
| NG Runner could not verify the checker the chat found something on | Neither a miss nor a clean: "unverified" | Could not verify is no verdict (rule 8) |
| The rooms | The fixtures hold each job as Jim last saved it, days or weeks after most reports. A chat finding NG Runner doesn't raise on rooms saved since, where NG Runner reads them fixed, is "since", never a miss | 23 of the 25 chat-only findings are so, each with its evidence in the test |
| Who is right | A person's reading of Jim's docs, line cited, or "can't tell"; the test fails on any disagreement without one | Code compares; it never decides who is right |
| How many of the 15 it stands for | **4** (Cases C, E, F, G): their crosscheck files print what NG Runner's run prints on 13 of 14 checkers (`tests/test_checks_real.py`), so the rooms are the same, every checker is compared, and the two agree on every finding. The other 21 replays compare one or a few checkers on rooms saved since, and stand for none | Design 7's exit is "15 checks compared with chat"; only a same-rooms, all-checker comparison is one |

**Measured.** 25 jobs replayed: the 5 crosscheck files and 20 of the chat's own reports (P34's has no IC Conversion to check against; Case I has no report).

| | Both | NG Runner only | Chat only | Unverified | Outside |
|---|---|---|---|---|---|
| Crosscheck files, C to G | 1 | 1 | 1 | 0 | 0 |
| The chat's own reports | 4 | 108 | 25 | 17 | 17 |

- **Crosscheck files.** C, F and G agree, clean. E: both raise `parm_check`, its `SysHolesW` of 4, which RULES 604-611 makes Jim's own setting for sparse uppers and the design names known noise; the chat's delivery left it out, NG Runner shows it as an item to fix. D (its rooms changed after the chat's run): NG Runner alone raises a doubled housing on R1C5, right by RULES 536-539, whose line 538 names this very job; the chat's crosscheck alone flagged a bore depth on R1C1, which its own delivery logged as no defect.
- **NG Runner only, 108.** 76 real by a rule made after the chat's check: 69 doubled housings on three jobs (RULES 524-541), 5 notch faces (RULES 404-409, from 2026-09-22), 1 WM banding (BUILD 516, from 2026-09-21) and 1 `JointFast2` (RULES 527-535). 5 real by rules in force (two stale bore rows, BUILD 416-422; a worksurface 2.7 mm off, RULES 666; a vertical at the wrong depth). 1 Jim's word on that job (an extra toe kick he said is fine). 9 can't tell. **17 noise, on 6 jobs:** P20 8 (two bridges not named BR, so Jim's checkers can't see them, BUILD 8b, 4 bore depths and 2 panel lines; a hamper front built as a door, 2, RULES 659-661), P28 4 (a cleat counted under another family and a hook board built up a size, RULES 258-264 and 659-661, BUILD 373-374), P35 2 (TR Base 4.1, RULES 266-268, on an older IC Conversion that `panel_xcheck`'s 4x96 rule can't read), P26 1 (LED strips, RULES 103 and 667), P30 and P39 1 each (Hang Rail, RULES 245-247). Every one is printed by Jim's `panel_xcheck` or `drill_depth_check` as they are, and NG Runner reads them as printed.
- **Chat only, 25.** 23 fixed in the rooms since, 1 superseded (a rail cover, RULES 245-247, Jim 2026-10-19), 1 Jim's word on that job (a section he set aside for the count).
- **Unverified, 17.** All on the panel set, where Jim's `panel_xcheck` stops on an older IC Conversion (`KeyError`): it does on 17 of the 32 audited P5 jobs, Cases A, H and J, and one frozen job. 16 are Case J's, a closet and garage job, which stays in chat (R4).

**Against design 7's exit:** 0 missed act items; 0 false cleans (NG Runner passed no job the chat found a fault on); noise at most 1 a job fails on P20 (8), P28 (4) and P35 (2), plus E's `SysHolesW` on both sides. The live trial on Jim's PC still makes the 15.

**Not built, for a ruling (rule 4: his rules come from his toolkit).** The design's known-noise list (rail covers, TR Base 4x96, `SysHolesW`) is not in NG Runner's readers, so those reach Jim as items, which he settles once per job or for all jobs. RULES disagrees with itself on the Hang Rail: lines 245-247 "never ... in a panel check", lines 662-665 "the COUNT is checked", and his `panel_xcheck` prints Mozaik's Hang Rails as extra parts. Bridges not named BR are invisible to every checker of his. Each is a question for Jim, or a reader change for Sulaiman.

### The audit of chat-built jobs (`findings.AUDIT_CHECKERS`, `checks/topline.py`)

| Question | Ruling | Why |
|---|---|---|
| What it reads | crosscheck's `panel_xcheck`, `top_line`, `op_schema_check` and `parm_check`, with NG Runner's own room files and its own top line. Unchanged otherwise | Design 7, P3 |
| `parm_check`'s template | `Mozaik Shared\Jobs\Lux Closet Template Claude` on Jim's PC, staged beside the job, as before. Measured with his copy: it compares on all 45 jobs with an IC Conversion. It passes on C, D, F and G, the four the chat fixed in its cam fix; on 41 a parameter differs: `JointFast2` on 40 (RULES 527-535: since the doubled cams it is 1; an existing job keeps its own, and on a job built from today's template a difference is a real fault, RULES 637-640) and E's `SysHolesW`. `BTop`, `WTop` and `BotNail` differ on 33 and stay could not verify for Q1: the template's own `BTop` is 0 where `parm_check` says it wants 1 | Answered from Jim's files, 24 |
| NG Runner's own top line | One T per room by P5's rule, read as it is (`placement.top_line`, RULES 333-339); every WM panel on it (RULES 330-331, and no FM panel beside a run with two tops is the defect); gate 5's Elev on 32k (RULES 319). A top as built off the height grid is could not verify, as P5a placement left it for Jim. Which products are panels is his `top_line.py`'s, tied by `ast`. Where his `top_line` item names a room, NG Runner's own "off the top line" item for it is left out: one item per fault | His check picks the commonest top where no FM panel sets it, and never reads the Elev grid |
| A job with no IC Conversion | Not audited: crosscheck refuses to run, as before | A chat-built job always has one (RULES 1) |

**Measured** (items, and in brackets the parts or rooms they name):

| | A to J | P5 | Frozen |
|---|---|---|---|
| Audited / no IC Conversion | 10 / 0 | 32 / 8 | 3 / 0 |
| Panel set: missing, extra | 1 (1), 2 (2) | 9 (42), 7 (22) | 1 (1), 1 (1) |
| Panel set could not verify (`panel_xcheck` stopped) | 3 | 17 | 1 |
| Top line to fix (his `top_line`) | 1 (1 room) | 4 (7 rooms) | 0 |
| Top as built off the grid, could not verify (NG Runner's) | 1 | 3 | 1 |
| Operations to fix (`op_schema_check`) | 0 | 0 | 0 |
| Operations by hand, could not verify | 2 | 7 | 1 |
| Parameters to fix | 6 | 32 | 3 |
| Parameters for Q1, could not verify | 9 | 25 | 3 |
| Room files | all pass | all pass | all pass |

No audit passes whole while Q1 is open. On the 99 product rooms of the 45 audited jobs, NG Runner's own top line and Jim's `top_line` agree on every one: 8 off the top line on both, 47 on it, 39 with nothing hung, and 5 whose top as built is off the grid, which his passes and NG Runner leaves to Jim. On all 112 product rooms, none hangs on two tops or has its Elev off the grid.

### The close prompt (`jobchecks._prompt`)

| Question | Ruling | Why |
|---|---|---|
| When a later close asks | Only when the job holds a change no check has looked at: no finished run, a stale one, rooms the guards' listing saw change at or after the newest run began, or any of it not known. Never while a check is already asked for it, which runs once the job is closed | A close with nothing saved has nothing new for "sections done?" (P3, "Close prompt and Check now") |
| The first close | Always asks, with its three answers | Its answers are also Jim's word that the job opened fine (design 3.4, "Jim's recorded clicks") |
| Its words | Unchanged: "<Job> closed 9:40 PM", "Sections done?", [Yes, check it] [Not yet]; pinned to Your list, the tray amber and the digest while unanswered; Check now on every row | Design 4, "The close prompt" |

## P5a builder, slice 2: writing the room (2026-11-01)

`ngrunner/build/`: `writer.py` turns a room model with nothing left to verify into Room#.des in memory, `products.py` clones each panel from Jim's own products, `desxml.py` is the balanced scanner both use, `gates.py` reads the written bytes back against the model, the wall's IC lines and his rules, and `commit.py` writes a room only when every gate passed, through `commit_write` with an interlock, into NG Runner's own shadow copy. Jim's template room and products are read at run time (the template job's folder, the adopted toolkit copy) and never committed; `tests/helpers_build.py` makes up stand-ins in their shape. Each row is a default where his docs left a choice, citing his lines (toolkit of 2026-10-31); Sulaiman can overrule any of them.

| Question | Ruling | Why |
|---|---|---|
| The seed | The template job's Room1 (`Lux Closet Template Claude`), its `Walls`, `WallJoints` and `Products` replaced whole, found by a scanner over tags that reads past quoted values; every other byte kept. `UniqueID` 50000 + the room's number, `Name` the IC design's (escaped), `IdTagCount` the highest IDTag of a wall or a product, `H_Walls` the tallest wall | BUILD 144-150; SCHEMA 32-37; gen_rooms.py 109-113; build_jobs_0914.py 14-29, 81-123 |
| What the model doesn't hold | The room's colour: its `<BaseMatTempSel>` run, lifted from a known-good room built in that colour, and its drawer front. Given as `RoomSettings`; without them the room is not written. A run on the blank template's cabinet id 88, with a per-part `MatORId`, or of other categories than the template's is refused | SCHEMA 46-85 (59, 75-78, 82-84); BUILD 152-160; build_jobs_0914.py 39-50, 87-90. Nothing NG Runner reads gives the colour yet |
| `<Wall>` and `<WallJoint>` | Each wall a clone of the template room's one wall, each joint a clone of the first joint in Jim's reference rooms (the adopted copy's frozen rooms), since the template room has none: only the numbers change. No reference joint: a room of two walls or more is not written | SCHEMA 9-30 ("clone this template and mutate only the numeric values"); tied: every wall and joint in his frozen rooms has exactly these attribute sets, and SCHEMA 20-21's names |
| A panel's `Wall` | `<its wall's place in the file>_1`, not its number | Frozen Z's two rooms whose chain isn't the table's order put their panels on `3_1` for wall 2; `regression.py compare` matches on it |
| A panel's identity | IDTags after the highest wall IDTag, one each in file order; `UniqueID` the room's × 1000 + k, `CabNo` k | build_jobs_0914.py 100; build_verticals.py 156; frozen X runs 50001001 up. Mozaik renumbers cabinets itself (`RoomNosDirty`) |
| Which product | FM 84 is the template room's own (`SourceLib` NG Closets, `MaxH` 2463.8), as his builder clones it; the other 15 legal products are `templates\<name with _>.xml` of the adopted copy. The June REA names are never used | build_jobs_0914.py 33-37; wm_panel.py 5-7; rea_panel.py 23-28; RULES 416-417. `templates\FM_84.xml` is the older one (`1 PAC Closet Library V2`, `MaxH` 3048) |
| Resizing | By value: every `X`, `Y`, `L`, `W`, `Height`, `Depth` on the Product, its part and its outline points within 0.05 mm of the template's height or depth becomes the panel's; on an REA the slant's two derived values too; width 19 on the Product and its top outline | RULES 419-421; rea_panel.py 95-108; BUILD 284. 0.05 mm, not rea_panel's 0.01: the template room's FM 84 holds 2132 and 2132.012 |
| Notch, bores, banding, REA | Every hang notch's cached X = height − 191.9; line bores stripped by the scanner; WM banding by geometry (an edge reaching the front half taped and locked, a slot Jim picked kept, the rest bare); an REA's every operation left byte for byte, its notch X reported when off, never corrected; FM panels keep the template's banding | RULES 400-409, 423-450; BUILD 253, 288; wm_panel.py 66-72; wm_eb.py 35-60; rea_panel.py 109-120 |
| `SourceLib` | Read from the template room: its products' one `SourceLib`, which must be its `AutofillLib` | BUILD 289 ("read it from the template, never hard-code"); RULES 417 |
| Numbers and the file | `f"{v:.3f}"`, trailing zeros stripped, never -0; UTF-8, CRLF throughout, the template's version line. A product's own lines are kept as they are, so an REA's drilling stays its template's byte for byte | BUILD 290-291; build_jobs_0914.py 9-11 |
| What is written | Room<n>.des, n of 1 or more, only. Room0 and the job folder's other files are the job folder step (BUILD Step 3), not this slice; .bak and .sbk are Mozaik's own | BUILD 140-197 |
| The gates | 20, each on the bytes read back: file (CRLF, version, an XML parser reads it), settled (no could-not-verify in the model), attributes, walls (every value within 0.0005 mm of the model's, clockwise, `IdTagCount`), panels (the same for each product), gates 1 to 5, gate 6, legal names, drill codes, notch, groove, WM banding, REA (drilling, slant), line bores, part (W = Depth, L = Height), SourceLib (and no `MatORId`). Each passed, failed or could not verify; a room commits only when all 20 passed, and a gate that didn't run counts as not passed | RULES 313-322, 351-450; BUILD 199-296; SCHEMA 7-37, 96-147; panel_drill.py, notch_side.py, wm_variant_check.py, wm_eb.py, rea_panel.py, part_geom_check.py, top_line.py |
| Gate 5's grid | Every WM Elev on 32k, whatever T is | RULES 319 says so for every room; regression.py 100 checks it only when T is 2132. RULES outranks |
| Gate 6 | Jim's `elev_panels.py` from the adopted copy, per wall. Its table's wall-box lines (the wall's full height, at either end) set aside, each panel must have lines drawn within 1 in of its X overlapping its own band, the longest read to the nearest VT height on the wall being its own, and every line drawn must belong to a panel. A wall it can't measure, or a line that reads as two heights, could not verify. Radius is not checked here, depth isn't drawn | RULES 320-322 and the script's own notes (its lines 7-14). On frozen Z's walls (measured, below) a radius panel's lines read as long as a square one's, so gate 3 and the reading hold the radius. NG Runner's reading of RULES 320-322, so it fails closed |
| The commit | writes.py's "P5 shadow": in a Mozaik Shared root only `Jobs\<Job>\Room<n>.des`, n of 1 or more, new or NG Runner's own, never one it didn't write; that root on this PC's own disk and not named Mozaik Shared; nothing in NG Jobs, the control folder or the tracker. "P5", the real write, has no rules. The interlock refuses anything below the folders NG Runner is configured with, a pause, any Mozaik process here (or one that can't be told), and a job folder holding a lock file. Every room of a job, all or none | AGENTS.md rules 1, 2 and 8; P4's shadow writes the same way (lock/__init__.py). `commit.p5_shadow_roots` is `<data>\P5 shadow\` |

Measured 2026-11-01 (`tests/test_build_written_real.py` pins it). Each frozen room's own panels stand in for the reading and its own chain for the plan, as in slice 1, and its own material run and drawer front for its colour:

- **Jim's `regression.py compare`, from an adopted copy of his toolkit (his regression clean on that copy), in the tools' Python, on NG Runner's written rooms:** X `Regression clean — nothing moved.` (exit 0); Z `Regression clean — nothing moved.` (exit 0); Y `1. Room1.des: missing from the rebuild` (exit 1), as it isn't written.
- **Y is not written**, blocked by 17 values: x on 7 panels and the wall's run start (the run is drawn 0.78 in from the wall's start), Elev on 4 (its top line, 2408, is on the Elev rule RULES now calls an error), side and product on 2 (geometry and the drill codes differ), and its own material run, on the blank template's cabinet id 88, which predates SCHEMA 82-84.
- **The gates on X (1 room) and Z (4 rooms):** 19 of 20 pass on every room; gate 6 could not verify on every wall, as `elev_panels.py` renders the page with poppler's `pdftoppm` (`subprocess`, its line 23), which this PC doesn't have. PyMuPDF is already pinned in `tools/tools-venv.txt` (1.28.2) and installed in the tools' Python, but this script doesn't use it, so nothing was added. With PyMuPDF rendering the page in `pdftoppm`'s place at the same 150 dpi (a probe outside the repo, not committed), his script measured Z's 4 walls and gate 6 passed on each; on X's 3 pages it stops with "no wall box found on that page" (its wall box must fill 38% of the page's height), so X's gate 6 could not verify even with `pdftoppm`.
- **Jim's own checkers on the written rooms** (staged copies, the adopted copy, the tools' Python): `part_geom_check.py` finds every panel's part right in all 5, `top_line.py --check` would change no WM panel, `notch_side.py` finds no notch on the wrong face.
- **Nothing was committed:** X and Z are refused at the commit by gate 6. No room reached even the shadow copy.
- **The first 26 walls (Jobs A to J), Claude's cached readings, no plan, no colour:** 18 rooms, none written. Every one waits for its plan (each wall's position, angle and start, and the joints) and its colour (the material run and the drawer front). Six of them (in Jobs C, D, E and G) wait for nothing else; the other 12 also for a panel value: the run start (8 rooms), FM 84 or FM 90 (5), a wall of the room the reader didn't read (3), the reading's own doubt of x (3), the top line (1, Job F).
- **Left for Jim or Sulaiman:** how `elev_panels.py` gets a renderer (a pinned poppler `pdftoppm.exe` beside the tools' Python, or a stand-in built on PyMuPDF, which changes what his measurement sees), and its wall-box finder on portrait pages like X's; where a room's colour comes from (SCHEMA 75-76's known-good job per colour, and the GV code for the drawer front); FM 84 from the template room or from `templates\FM_84.xml` (their `SourceLib` and `MaxH` differ); and whether a WM Elev must be on 32k when T isn't 2132 (RULES 319 or regression.py 100).

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
- **Auto-match, typed the way Barbara usually types them (bare surname, designer's initials), against the local list of 2026-09-15 (10,643 Opportunities):** 32 of 53 jobs (60%) dated in the list's last 30 days, 64 of 116 (55%) in its last 90 days, and 307 of all 628 sold (49%). None matched the wrong job; the rest ask with a card.
- **Auto-match on the real Sheet1, read once, each row treated as new, against the live list of 10,781:** 67 of 91 rows (74%), 62 by exact name and 5 by surname and designer, and 4 of the 4 with an upcoming install date. 55 of the 67 have an NG Jobs folder under that name. No person has checked the 67, so this gives no count of wrong matches; the shadow run measures both (AGENTS.md: Before filing goes live, item 6).
- **The first real run, on Sulaiman's PC:** 20 real rows arriving as new, the live job list, real Studio and real Drive, with a stand-in NG Jobs. 16 matched and 4 asked. All 11 auto-matched jobs filed by themselves; a separate audit found the right job on page 1 of all 11, no duplicates, and 0 of 36 fetches failed the check.
- **Jim's own summaries against a fresh fetch:** 4 of 4 differ. 3 (from 10-18) in rod part numbers and descriptions only, with identical drawings: Studio's catalog changed. 1 (from 09-22) is a real redesign. So a difference doesn't mean a redesign, and a card must say what changed.
- **Install Date cells:** a multi-day install (`10/29-30`) now counts as its first day, the day the job must be ready. 84 of 91 real rows read, up from 72. The other 7 stay "could not verify": 2 blank, 1 a note, and 4 whose weekday doesn't match the date.

## Answered from Jim's own files (2026-10-31)

The 30 questions of "NG Runner questions for Jim" (his Drive, `CNC operations\Sulaiman\Sulaiman Testing (Do not delete)\`), numbered as there, which is not the questionnaire's Q1–Q19. Each answer below was read from his own files, his RULES (`.local-fixtures\toolkit\20261031\NG_Automation_docs\RULES.md`, identical to the spec snapshot), his library, his scripts and the 40 real nests of `.local-fixtures\reader\p5jobs`, and checked by his own Claude against his live files. Only these changed or pinned code; every other question stays "could not verify" (Unplaced) as before, and Jim can overrule any line here.

| # | Answer | Source | In NG Runner |
|---|---|---|---|
| 2 | Settled. A worksurface printed in a second colour is built in its own printed colour, its tape in that colour's bucket. The room's colour is for one IC prints no second colour on | RULES 190 ("A part in a second colour bands in ITS OWN colour ... point those parts at it"), RULES 221; a real job's second-colour counter tops are nested on their own colour's Tafisa sheet, not the room's Arauco | `parts.settle_colours`: a worksurface keeps its printed colour and never decides its room's. One real pair now places its 2 tops (1 sheet, 21.9 ft of tape, as the chat had them) |
| 3 | Behaviour settled, what Navy is built in still Jim's. Navy (IC MS) is never placed and never swapped for another colour: the line says so by name, and nothing else on the job waits for it. Navy has textures in his library, but no material carries one and it has no price | RULES 146 (never substitute); his library; his own notes on the job that printed it, where the chat built Ore | `rules.NO_MATERIAL`, `parts._read`: "Navy can't be placed: no material in the library carries it and it has no price; never swapped for another colour". The takeoff stays incomplete, so the order heads-up stays the chat's |
| 8 | Settled. A glass shelf (`SHG ... CLR`) is bought in, not cut | RULES 243 (glass is a genuine buyout) | `rules.NOT_BOARD_CODES`. One real pair loses 2 could-not-verify lines |
| 9 | Settled for white only. A white shelf printed 1-1/8in is built 1in AMWH; one in a colour is still Jim's | RULES 214 ("White with something 1in+ -> AMWH"), RULES 217 (1in+ only in white) | `rules.WHITE_SHELF_*`, `parts._built`. One real pair is now complete, 81 board parts as the chat counted |
| 10 | Built as the whole job, awaiting Jim's yes: one white part built 1in or thicker makes all the job's white AMWH, so its 1/4 white backs go on White 1/4 Vinyl at 49x97, not 1-4 Egger | RULES 214, and the RULES of 2026-10-06 (`ARCHIVE - RULES_full_20261006.md` line 541, which states the whole-job rule as a premise); RULES 135 for the backs. Today's RULES says only "White with something 1in+ -> AMWH" | `rules.thick_white`, `rules.job_brand`, `rules.job_sheet_size`; named "AMWH (IC WH)". On the real pair above: 12 3/4, 1 1in and 2 vinyl sheets, as the chat had them |
| 11 | Already so: Classic White backs on `1-4 Egger` at 61x109. Jim's yes or no remains on one older nest at 49x97 | RULES 128-135 | Pinned (`tests/test_convert_takeoff.py`) |
| 12 | **Not settled.** NG Runner follows RULES 123/161 today: IC Charcoal is Egger Black U999 at 61x109. But his library also has `AMLM Charcoal` (id 121) and `3-4 Charcoal` (id 93), and the job BUILD names as the Charcoal-board one was cut on Tafisa Black Brava | RULES 123, 161; his library; that job's nest | Unchanged; a test pins only that it follows RULES 123 today |
| 14 | Already so: `TR BASE` in any case is a TR Base | RULES ("TR Base is ALWAYS 4x96") | Pinned (`tests/test_convert_parts.py`) |
| 18 | Settled. A flip sheet is one with a `Flipped="True"` block, the same board turned over; a remake run is a program, never counted against sheets. His `cnc_job_sheet` calls every sheet with more than one G-code run a flip sheet: on the 83 real nests here, 28 sheets, 9 of them flips, and 4 of the 10 real jobs A to J would get a wrong one on the sheet | RULES 716-722 | `lock/job_sheet.py`: every sheet his note calls a flip sheet must have a flip pass in its nest, or the sheet is an item for Jim and isn't filed. A note it can't read is could not verify |
| 19 | Settled. George runs Mozaik too, so a second name on a job's lock is normal | INDEX line 79 | No card or message to Jim treats it as odd. `guards/observer.py` still opens one DEV item for Sulaiman (`lock_owner_other`) the first time a lock names someone new: kept, as it shows what string George's lock holds, which no real `.LCK` has yet |
| 23 | Already so: `wm_variant_check`'s library is a Linux path (its line 26), so on Windows its "notch equation matches library" is no evidence. Its library comparison counts as not run | the script | Pinned (`tests/test_checks.py`, `tests/test_check_findings.py`) |
| 24 | Already so: `parm_check`'s CLEAN when it compared nothing (lines 63, 82) is could not verify. Its reference job is now in his backup | the script; crosscheck | Pinned |
| 25 | Already so: `panel_xcheck`'s crash on older conversions (lines 87-89) is could not verify, never "no findings" | the script | Pinned |
| 26 | Settled. "NOT at Outset 0" and "would change: N" over 0 never make crosscheck flag (its FLAG pattern, line 54): NG Runner flags them itself. Outset is one Jim always wants flagged | crosscheck's FLAG pattern; `outset_check` ("FLAG, never auto-fix"), `eb_tops`, `top_line` | `checks/readers.py` fails them (`NG_RUNNER_FLAGS`), `checks/findings.py` makes each an item to fix with its chat line, which Jim may settle. On the real Jobs A, H and J, `eb_tops` and `top_line` now fail |
| 27 | Settled. Eight checkers are outside his regression: section_span, part_geom_check, eb_tops, top_line, parm_check, panel_xcheck, sheet_size_check and br_cam_check --nest. (notch_check's WM-banding half is outside it too, and has its own fault test already) | his `regression.py`, `checkers()` | `tests/test_check_must_flag.py`: a must-flag example for each, tied to his source by the lines he prints |
| 28 | Already so: a toolkit copy with no `baseline.json` never runs its regression, and a regression that writes one is no pass | his `regression.py` | Pinned (`tests/test_toolkit_adopt.py`) |
| 29 | No code change: the misfiled summary is named for another job, which the summary check's PROJECT NAME already catches | the summary's own PROJECT NAME | None |

The rules' fingerprint (`rules.fingerprint()`) changed with these, so a takeoff made before today names the older rules. The converter on the 52 real pairs, before and after: the 14 complete closets scored in Jim's format are unchanged, cell for cell; 4 other pairs changed, each by one of the answers above (Navy named, 2 counter tops placed, 2 glass shelves dropped, 4 white shelves built 1in with the job's white AMWH), and two of them are now complete.
