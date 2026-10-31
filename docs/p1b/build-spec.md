# P1b build spec — contracts for the builders

Status: working spec for building Phase 1b ("Guards", design section 7: 11 dev-days). Everything marked measured was read on Sulaiman's PC on 2026-10-30, read-only. The design (`01-final-design.md`, local only) is the source of truth. Where this file departs from it, it says so, and section 9 lists each departure as a ruling for `docs/decisions.md`. Section 9's defaults are adopted (`docs/decisions.md`, 2026-10-30), as Sulaiman asked for decisions, and he can overrule any of them. Real jobs are called Case A, Case B and so on; the key is outside the repo. Made-up names (`Testerson_101426`, abbreviation `TES`) are the only job names here.

## 1. What changes the plan

1. **No nest or design file can be read on this PC.** Two copies sit behind one stopped OneDrive account, `Mozaik Shared\Jobs` and `Documents\JD\Mozaik Move\Mozaik\Jobs`: 233 `.opt` and 431 `.des` between them, every read failing with Windows error 362, "The cloud file provider is not running". No toolkit script is in any place searched (section 10). So nothing about `.opt`, `.des`, `.UNS`, `.LCK` or the toolkit's output below comes from a real file. It all comes from Jim's docs.
2. **The programs on G: are readable, and they show both nest defects.** 1,663 of 1,663 `.TAP` files parse. Cutting moves give the sheet (48.75 × 96.75 on a 49×97, 60.75 × 108.75 on a 61×109). Doubled cam housings show as two holes 0.001in apart: 286 of 1,397 programs posted before Jim's 10-20 fix, 0 of 67 after it (1,045 holes). So the guard's two checks can start now as a **program check**, without the toolkit. This is a departure: the design calls `sheet_size_check --taps` and `br_cam_check --nest`. A program check can raise DO NOT CUT but can never say "Nest checked".
3. **The design's exit test can't run as written.** It names two known bad nests. Case B's was overwritten: the tracker note says Jim re-optimized it on 09-28 after correcting the material. Its programs now read six sheets on 49×97 and one on 61×109 that Jim added by hand and priced. Case A's four per-sheet programs read 49×97, though its own readback text says 61×109. Neither bad state is shown to exist on disk. Only OneDrive version history might hold them (UNVERIFIED). Section 11 proposes a test that can run.
4. **A program past the library size is not always a fault.** Of 186 material folders with a size rule, 7 hold a program past it, and at least 3 of those are sheets Jim added on purpose. DO NOT CUT needs a way for Jim to clear one program on one job, which the design's "never silenceable" doesn't allow (Q3a, section 9).
5. **"The chat's v2.0 JSON" is not a stable format.** 14 of the 34 files that say 2.0 lack the blocks a heads-up needs. The newest job's file has no `schema_version`, and its `buckets` is keyed by thickness with no material. So the first live job may well read "could not verify the takeoff". The heads-up reads named blocks, ignores the version, and refuses any other shape. One RULES line asking the chat for one shape fixes it (section 9).
6. **What Jim holds arrives only in test window 2** (`docs/testing/jims-pc.md`, step 5), and nothing is installed on his PC yet. Group 3 waits for Jim's zip on Google Drive, or for window 2.

## 2. Where each piece stands

| Piece | Group | Evidence |
|---|---|---|
| Program reader (`guards/tap.py`) | **1. Now, real data** | 1,663 of 1,663 parsed in 27 s, 0 errors. 1,661 say `G20` and end `M30` then `%`; the other 2 are old test files |
| Doubled holes in programs | **1** | 1,464 non-merged programs. A positive control: one program of Case D has 8 pairs 0.001in apart, the job RULES 538 names. 0 of 67 after the fix |
| Sheet size from programs (`guards/sheets.py`) | **1** | 1,607 programs in 186 folders have a rule; 7 folders flag (section 3.1). Case C's 1/4 on 61×109 passes; Case B's hand-added 5×9 flags; Case A does not flag |
| Per-material state (`guards/nest_state.py`), board, digest, heartbeat | **1** | Pure logic over real program facts. `Nest checked` is reachable only with synthetic `.opt` facts until group 3 |
| Watching for new programs (`guards/watch.py`) | **1** | Walk of all of NG Jobs (2,627 files): 2.2 s. Both `.TAP` (1,435) and `.tap` (228) occur |
| Order heads-up (`guards/headsup.py`) | **1** | 58 of 58 conversion JSONs parsed. Since 09-20, 20 of 27 have the blocks in the usable shape; the newest job's doesn't. Tie to the tracker: 16 of 20 on tape, 16 of 20 on sheets (section 3.4) |
| Lock pre-flight (`guards/tracker.py`, `guards/preflight.py`) | **1** for tracker, check and job-sheet items | 4 snapshots and the meta file parsed: 101 sheet rows, 88 tape rows, 60 job rows. 63 of 63 job sheets parsed. 5 of 5 newest-format crosscheck files parsed. Nest items come from the state above |
| Labels-to-design match (`guards/labels.py`) | **1**, as a fallback only | Labels ⊂ catalog on 5 of 5 jobs examined, apart from a numbering difference (section 3.2). Not the design's match: it can't see a missing part |
| Shared reader (`guards/reads.py`) | **1** | Error 362 is real here: open succeeds, read fails. The share flags test on NTFS |
| Messages, cards, lint | **1** | 7 proposed lines run through the real `check_line`: pass. It rejects the JSON's tape text as written (`1mm`), so the line is rebuilt |
| `.opt` reader (`guards/opt.py`) | **2. Now, proof needs Jim** | 0 real files read. 233 names and sizes only (488 B to 1.15 MB). Format from RULES and BUILD only |
| TAP reconcile, both directions | **2** | Needs `<OptimizeGCodeRun Filename>`. 65 of 74 `.opt` names match a program folder of the same name in the 49 jobs on both sides, by metadata. The other 9 have no folder of that name |
| Observer and owner log (`guards/observer.py`) | **2** | 50 `MozaikJob.UNS` and 1 `OptimizeJob.LCK`: all 7 bytes, none readable. Listing shows `Mozaik.exe` and `Optimize.exe`; the live process is unseen |
| Stored pass (`.opt` hash + TAP manifest + room hashes) | **2** | Needs real `.opt` and `.des` |
| George's and Will's emails | **2** | `ROLES` already has both, and the outbox handles them. Real delivery needs their addresses, and goes to Sulaiman in shadow |
| Cloud NOT CHECKED (`Watchdog.gs`) | **2** | Node tests now. Whether the script's account can read NG Jobs is UNVERIFIED |
| Push channel | **2** | `messages/pushover.py` is built and tested. Needs Jim's account and a wider consent (section 9) |
| Snapshot harness mechanism (`ngrunner/toolkit.py`) | **2** | Copy, hash, run and kill work on a synthetic toolkit. A real run needs the toolkit |
| `sheet_size_check --taps` adapter | **3. Blocked: toolkit** | Script absent. Only prose reports of its results exist, never its output or exit codes |
| `br_cam_check --nest` adapter | **3** | Same. Inside a crosscheck its `.des` mode prints only room names when clean. No `--nest` output and no failure text seen |
| Snapshot adoption (regression, baseline) | **3** | `regression.py` and `baseline.json` absent |
| Nest-to-design match (`.opt` against `.des` cutlist) | **3** | `des_cutlist` absent; no `.des` read |
| `material_lookup` in place of the size table | **3** | Script and `Materials.dat` absent |
| Mozaik lab | **3** (needs Jim and Mozaik) | Mozaik is not installed here. Script in section 8 |
| Team tab | **Not built** | `docs/decisions.md`: only if Ron, Will or Mary ask |

## 3. Measured formats

### 3.1 Programs (`.TAP`, 1,663 in NG Jobs and Old)
- **Encoding:** ASCII, CRLF (1,662 of 1,663), inches (`G20`).
- **Layout:** `(Multi Drill Section)` with `G81 B<bank> ... X Y W R F` lines, then `(Main Spindle)`, then `M06 T<n>` tool blocks of `G0`, `G1`, `G2`, `G3`.
- **Never use for the sheet's size:**
  - `G81` X and Y: they are drill-bank references, and reach X 53 on a 49in sheet.
  - The final `G00 X0.0000 Y108.0000`: a park move on 1,659 programs, whatever the sheet.
  - `G0`, `G28`, `G53`.
- **The sheet:** the largest X and Y over `G1`, `G2`, `G3` after a tool change. On 1,099 per-sheet programs: 1,039 fit 49×97, 58 fit 61×109, 2 fit 49×121. X clusters at 48.75 (688) and 60.75 (43), Y at 96.75 (767) and 108.75 (47). Flip passes reach 48.94 × 97.0, inside the sheet. A fit is a lower bound: a small program fits any sheet, so a fit never proves the right sheet. It can only prove a wrong one.
- **A hole:** `G1 Z<depth>` with no X or Y, then `G0` retracting at the same X and Y. This excludes router lead-ins, which ramp while moving and otherwise give thousands of false pairs. Tool T6 at Z 0.13 on 3/4 is 16 mm deep, RULES' "20mm × 16mm BLIND".
- **A near pair:** same tool, same depth, more than 0 and at most 0.0197in (0.5 mm, RULES 536) apart. Measured on 1,464 non-merged programs: 3,034 on T6 (3,030 at 0.001in, the 0.025 mm RULES gives), 4 on T8, 2 on T5. Different tools at one spot are a counterbore, which RULES allows, and aren't compared.
- **Merged programs** (`<a>-<b>.tap`, `comb`) overlay several sheets, so exact repeats are normal. Sheet check yes, near pairs no.
- **Names, the 551 programs in current jobs:** 369 `<Abbr>S##.TAP`, 67 merged, 46 RM, RD, fix, pile or combo, 4 long form or flip (`<Job>_<Material>_S##R##[-Flip].TAP`), **65 hand-named** and fitting none of the design's families. Case C's 1/4 folder now holds only three hand-named programs, though its 10-20 check said 1 run.
- **Folders:** 42 distinct names. 6 don't start with a thickness (`New folder`, `GCode`, a job name, and a few others), so they have no rule.

The 7 folders with a program past the size:

| Kind | Folders | Reading |
|---|---|---|
| Hand-added on purpose, by file name or a priced tracker row | 3 | 5×9 or 4×10 sheets. One then re-posted under a plain `S##` name, so the name can't be the test |
| Tafisa, reaching up to 59.75 × 107.75 | 2 | In Old. Unexplained; RULES says Tafisa is 49×97 |
| A re-cut in another colour's folder | 1 | 2 programs, 60.75 × 108.75 |
| A shop file | 1 | Not a job |

Other Egger colours: 2 shop projects and 1 hand-built job sit on 49×97, against BUILD's "any other Egger colour 61×109", and so does one job's 1/4 Classic White from before the 10-20 rule. So only the lines of the RULES table carry a rule, and any other name is "could not verify".

### 3.2 Labels and the parts catalog
- **`<Name> Labels.pdf`** (135 files, text extracts with pypdf at the pinned version), one label per page: job name, design, `R1C3`, `FEnd (L) 19 1/16 x 83 15/16`, `3-4 Egger Classic White Custom*`, sheet number, part number. Pages reading `Remnant ...` are offcuts. Room 0 labels read `R0N1` or `R0N3(1\14)`. Some files carry no `R#C#` (on 13 jobs every page fails, one of them from October).
- **`NG Conversion program Claude\parts_catalog.csv`** (`utf-8-sig`, 3,513 rows, 159 jobs, 30 of 31 dated top-level NG Jobs folders by exact name, rewritten today at 07:51): `Job, Room, RoomName, Part, Product, W, H, D, Wall, Pieces, JobModified`. `Pieces` is `name~W~L~qty | ...` in mm. `JobModified` is a date with no time. No material column. It is made on Jim's PC, so a stale run looks like a stale design.
- **The match:** key `(R#C#, sorted W and L, to 1/16in, ±1/8in)`.
  - Labels against catalog on the 5 jobs examined: 15 of 15 less 1, 16 of 16 less 1, 43 of 43, 81 of 81 less 1, 270 of 270 less 3. Every miss is a Room 0 `R0N` (the catalog says `R0C`) or a counter top `R#N0`.
  - The other direction fails by design: the catalog lists drawer box parts that are never nested.
  - On older jobs with several overlapping label files the counts are not clean (for example 200 labels against 174 pieces), so a job's newest file per material is used, and any mismatch reads "could not verify".

### 3.3 Job sheets, crosscheck files
- **`<Name> CNC Job Sheet.docx`** (63 of 63 read with `zipfile`; no new dependency): `Job Name:`, `Job Abbr:`, `Install Date:`, `¾”: <n>`, `Color: <text>`, `¼”:`, `1”:`, `Special Notes:`. Typed by hand: one reads `Color: Classic 5x9`. Abbreviations can differ from the program prefix.
- **`<Job>_crosscheck.txt`** in the newest form: 5 files. Line 1 `<Job> - crosscheck <UTC>`; line 4 `CROSSCHECK CLEAN` or `CROSSCHECK FLAGGED: <checkers>`. No room hashes inside. Against the catalog's date: 2 checks are older than the job's last design date, and 3 fall on the same date, which reads "could not verify". Older forms (`_CROSSCHECK*.md`, `_panelcheck.txt`) count as no check.

### 3.4 IC Conversion JSON (58 files, all parse)
- **Versions:** 34 say 2.0, 23 have none, 1 says 1.3. BUDGET's v2 keys (`source`, `rates`, `third_party`, `costing`, `pricing`) appear in 1 of the 34.
- **The usable shape:** `buckets` a list of objects, and `edgeband.rows` a list. Of the 34 that say 2.0, 20 have it and 14 don't. Since 09-20: 20 of 27, and 15 of those buckets have the exact keys `material, thk_in, parts, sqft, sqm, calc_sheets_raw, sheets_with_waste, sheet_math, edgeband_lf`.
- **Three bucket shapes in real files:** a list (23 since 09-20); a dict keyed by material (the 1.3 file); a dict keyed by thickness, with no material (the newest job). 2 recent files have no buckets at all.
- **`estimate.total`:** a number or a string like `$7,999.05`.
- **Never send:** `address`, `client`, `contact_notes`, `site`, `email`. `address` is in nearly every recent file.
- **Tie to the tracker,** on the 20 files that say 2.0, have list buckets and have tracker rows: tape from `buckets[].edgeband_lf` 16 of 20. Sheets: `calc_sheets_raw` equals Calc Sheets on 12, `sheets_with_waste` on 4 more, 4 neither (a removed design, a later correction). So the tracker's own column uses two bases, and the heads-up states which one it shows.
- **Flags on the 20 usable since 09-20:** 12 are not all Classic White; 1 is at or above $25,000. IC "Arctic White" built as Egger Classic White is on 13 of the 20, so flagging that swap would flag most jobs (section 9). `parts[]` has `ic_in`, `ic_mm` and `thick_in` on all 20, but no IC thickness, so a size swap can't be read from a field. Only 2 `flags` lines mention one, in free text.

### 3.5 Tracker snapshots (`NG Conversion program Claude\`, every 15 min)
- `tracker_snapshot_{SheetGoods,Edgeband,JobSummary,Pricing,meta}.csv`: UTF-8, no BOM, title rows first, header cells with embedded newlines (`Calc\nSheets`), so `newline=""` and find the row whose first cell is `Job`.
- Display values: `$1,071.33`, `58.0%`. A `TOTALS` or `GRAND TOTAL` row closes each tab. `meta` has `snapshot_taken` and `sheet_lastupdate`.
- `VOID ...` rows are not real materials (2 sheet, 3 tape).
- **Pricing:** blocks start at `SHEET GOODS` and `EDGEBAND`, with a `Material...` header. Lines starting `▲` or `Name =` aren't prices. Today: 25 real sheet keys and 16 tape keys. One key exists twice, with and without a trailing space, at $60 and $65.
- **What a pre-flight would say today:** 0 duplicate rows. 6 nested sheet rows with no $/Sheet. 1 tape row of 634 LF with no $/LF. 1 priced key with no price. 7 status spellings. 6 dated folders with programs and no Job Summary row. `sheet_lastupdate` is 10-21, so no new rows have landed in 9 days.
- **Inbox archives:** 21 `ingested_tracker_inbox_<ts>.csv`, 0 `SKIPPED_`; header `TAB,JOB,FIELD1,...` when present.

### 3.6 From Jim's docs only — UNVERIFIED until real files are read
- **`.opt`** (RULES 705-726, BUILD 565-568, 774-787): UTF-16LE with a BOM on some, UTF-8 on others; sniff it. `<OptimizeSheet>` with `Quan` and `Flipped`. Physical sheets are the sum of `Quan` over `Flipped="False"`. `<OptimizeGCodeRun Filename>`, `<OptimizePart>`, `<OptimizePartLocation>`, `<OperationHole>` X, Y and face, `Width` and `Length`. Parts with no `<OptimizeSheet>` means not nested. `RunId` means nothing.
- **`MozaikJob.UNS`** is `JDELA\r\n`, and the same 7 bytes on all 50 seen. **`MozaikJob.LCK`** replaces it on open; **`OptimizeJob.LCK`** while the Optimizer runs.
- **Commands** (BUILD 551, INDEX, RULES 725): `sheet_size_check.py <job folder>\*.opt`, `--taps <NG Jobs job dir>`, `br_cam_check.py --nest <Mozaik job dir>`. Output and exit codes undocumented.

## 4. What the pieces share

**The store.** Migration 4, appended (`LATEST` is 3 today):

| Table | Columns | Notes |
|---|---|---|
| `programs` | `folder`, `material`, `name` (PK of the three), `sha256`, `size`, `mtime`, `family`, `reach_x`, `reach_y`, `holes`, `near_pairs`, `seen_at`, `state` (`seen_at_install`/`new`/`read`) | `folder` is the NG Jobs folder, so a folder with no Fab row still counts |
| `nests` | `folder`, `material` (PK), `studio_id` NULL, `state`, `basis` (`programs`/`nest`), `reasons` (JSON), `opt_sha256`, `manifest` (JSON), `room_hashes` (JSON), `posted_at`, `checked_at`, `trial` INT, `george_told_at`, `updated_at` | one row per material |
| `clearances` | `id` PK, `folder`, `material`, `program_sha256`, `what`, `card_id`, `at` | Jim's click, bound to that program's hash |
| `owner_log` | `id` PK, `at`, `folder`, `file`, `owner` | every UNS and LCK user string seen |
| `heads_ups` | `folder` PK, `json_sha256`, `sent_at`, `outbox_id` | one per JSON content |
| `snapshots` | `id` PK, `taken_at`, `sha256`, `adopted` INT, `result` | toolkit snapshots |

**Paths and settings.** `Settings.ng_jobs` and `mozaik_shared` exist. `AppPaths` gains `.toolkit`. Settings gain `tracker_folder` (no default) and `order_flag_total` (default 25000). `order_flags_on` stays off until Sulaiman turns it on, so nothing reaches Barbara or Will by surprise.

**Schedule.** `Supervisor` calls `guards.tick(ctx)` beside `pipeline.tick`, every 60 s. As in P1a, a tick never waits: reads of G: run in one worker, and stop between files on a stop request. Nightly: the snapshot harness (when a toolkit is set) and a re-read of the tracker's drift.

**The job model.** `_TICKS` gains `("nest", "Nest")`: `done` only when every material is Nest checked, `blocked` on any DO NOT CUT, `todo` otherwise. `JobRow` gains `materials: tuple[MaterialRow, ...] = ()`, with `MaterialRow(material, state, note, at, file)`. Tray per design 4: red for DO NOT CUT or a posted NOT CHECKED. `digest_view.owner` gets open DO NOT CUTs and NOT CHECKED, so the heartbeat's `open_risks` and the watchdog's "no list today" carry them with no change.

**`<folder>_NGRunner.json`, schema 2.** Adds `"nests": [{material, state, basis, programs, at}]` and `"preflight": {at, items: [{code, text}]}`. Still no address. Allowed by the existing P1a write rules, so `_PHASE_RULES` doesn't change. An OBSERVED job gets no writes, so its nest state and pre-flight are board-only: Jim's chat won't see them. `docs/p1a/rules-lines.md` says what chat reads and gets one more line.

**The interlock.** Unchanged in P1b (departure from integration spec section 2, which said the Mozaik conditions might join here). P1b writes nothing a Mozaik job reads; it only reads. The conditions join when writes into Mozaik Shared do, in P3.

**The build.** Every new module joins `selftest.P1A_MODULES`, and `ngrunner.spec` carries them.

**Trial labels.** Every verdict line says "(trial)" until 5 real nests have had one, and after that for a material whose feature is not yet exercised (design 5, Trust per feature). From folder names alone only `_Gar` and Tafisa 1/4 can be told. REA, bridges and stacked towers need `.des`, so until then all stay trial (UNVERIFIED how).

## 5. Contracts per piece

### 5.1 Programs: `ngrunner/guards/tap.py`
```python
Family = Literal["sheet", "long", "merged", "remake", "hand"]

@dataclass(frozen=True)
class NearPair: tool: str; depth: float; apart_in: float; x: float; y: float

@dataclass(frozen=True)
class Tap:
    name: str; sha256: str; size: int; inches: bool; complete: bool; family: Family
    reach: tuple[float, float] | None; holes: int; near_pairs: tuple[NearPair, ...]

def read_tap(name: str, data: bytes) -> Tap: ...      # never raises on content; a bad file is complete=False
def family_of(name: str) -> Family: ...
```
Section 3.1's rules, exactly. `complete` is `M30` then `%`. `near_pairs` is empty for `merged`. No client data leaves it: a `Tap`'s repr holds names and numbers only.

### 5.2 Sheet size: `ngrunner/guards/sheets.py`
```python
def expected_size(material_folder: str) -> tuple[float, float] | None: ...
def size_finding(material_folder: str, taps: Sequence[Tap]) -> SizeFinding: ...
# SizeFinding(code: "nothing_past" | "past_size" | "no_rule", programs: tuple[str, ...])
```
- The table holds the RULES section 2 lines only (Q3's default), keyed on folder-name prefix, and allows 0.01in over. `tests/test_docs_p1b.py` ties each line to RULES' words through `NGR_SPEC_DIR`, as `test_docs_p0.py` does, so a RULES change fails a test. Rule 4 still holds: this is a stand-in, and `material_lookup` replaces it on adoption.
- `nothing_past` never means "on the right sheet". It reads "nothing past 49x97".
- Mixed sizes are legal (BUILD 576). A material may hold several sizes; the question is only whether the size is expected or cleared.

### 5.3 State: `ngrunner/guards/nest_state.py`
```python
class NestState(StrEnum): NOT_POSTED; NOT_CHECKED; SAVE_NEST; DO_NOT_CUT; NEST_CHECKED

@dataclass(frozen=True)
class MaterialEvidence:
    material: str; taps: tuple[Tap, ...]; seen_at_install: bool
    opt: OptFacts | None = None; tools: tuple[ToolVerdict, ...] = ()
    design: DesignMatch | None = None; cleared: frozenset[str] = frozenset()

@dataclass(frozen=True)
class Verdict: state: NestState; reasons: tuple[str, ...]; basis: str; trial: bool

def decide(ev: MaterialEvidence) -> Verdict: ...      # pure; no store, no clock
```

| Evidence, first match wins | State | Board note |
|---|---|---|
| Seen at install and unchanged | NOT_CHECKED, no alarm | "Posted before NG Runner; not checked" |
| No program and no `.opt` | NOT_POSTED | no line |
| A new program has near pairs | DO_NOT_CUT | "N cam holes are doubled in programs ..." |
| A new program is past its size, not cleared | DO_NOT_CUT | "A program is on 61x109 and this colour is 49x97" |
| A tool says it failed | DO_NOT_CUT | the tool's fixed line |
| `.opt` has parts and no sheets | SAVE_NEST | "Save the nest in the Optimizer so I can check it" |
| `.opt`, both tools pass, design matches, all hashes current | NEST_CHECKED | "Nest checked 9:42 PM, programs TESS01-06" |
| Anything else | NOT_CHECKED | "Programs read 9:42 PM on 49x97, 0 doubled holes; the nest itself is not checked" |

The design's "A change to any of them puts that material back to NOT CHECKED" holds: a pass is bound to `opt_sha256`, `manifest` and `room_hashes`. Unknown evidence is NOT_CHECKED, never a pass. The first scan after install marks every program `seen_at_install`: without that, 65 old jobs go red at once.

### 5.4 Watching: `ngrunner/guards/watch.py`
`tick(ctx) -> None`. Each minute, list the top level of NG Jobs (not `Old`: a job moved there has stopped being watched), each folder's subfolders, and their `.tap` files by name, size and mtime. Skip `desktop.ini`, `~$*`, `.part`, `.ngr-tmp`. A file acts once two polls agree and it is `complete`; an incomplete one is re-read and goes to DEV after 10 minutes. Then `decide`, store, and on a state change tell (5.6). NG Jobs unreachable uses the existing `ng_jobs_unreachable` alarm. Nothing here writes outside the store, except `_NGRunner.json` through `ctx.commit`.

### 5.5 Pre-flight: `ngrunner/guards/tracker.py`, `guards/preflight.py`
```python
def read_tracker(folder: Path) -> TrackerSnapshot: ...  # raises TrackerUnreadable, plain words

def preflight(job: str, snap: TrackerSnapshot, nests: Sequence[NestRecord],
              check: CheckFile | None, catalog_date: date | None, *, now: datetime) -> Preflight: ...
# Preflight(items: tuple[PreflightItem(code, text), ...], snapshot_taken: datetime)
```
`code` is one of: `snapshot_old` (over 20 min: the whole result reads "could not verify"), `no_rows`, `duplicate_row`, `not_priced`, `zero_cost`, `key_spelled_twice`, `status_unknown`, `nest_state`, `no_check`, `check_flagged`, `check_older_than_design`, `check_same_day`. Material and status compare exactly, as the tracker's `INDEX/MATCH` does. Status words are reported, never judged, until decision 9 (P2). Shown on the board and in `_NGRunner.json`; no message of its own, and it blocks nothing in P1b, since Lock stays in chat. `read_check_file(path) -> CheckFile(at, verdict, checkers)` reads the newest form only. `read_job_sheet(path) -> JobSheet(name, abbr, install, counts)`, so a sheet count in the job sheet can be compared with the programs. "Never fill Optimizer Sheets from a nest that failed" becomes an item, not a block.

### 5.6 Telling people: `messages/templates.py`, `cards.py`
New templates, each with a `selftest.message_samples` example. These exact shapes pass `check_line` today:
```
1. Testerson_101426: DO NOT CUT 3/4 Classic White (trial). A program is on 61x109 and this colour is 49x97.
1. Testerson_101426: DO NOT CUT 3/4 Classic White. 8 cam holes are doubled in programs TESS02 and TESS03.
1. Testerson_101426: NOT CHECKED 3/4 Classic White, posted 11:40 PM. NG Runner is offline.
1. Testerson_101426: Nest checked 9:42 PM for 3/4 Classic White, programs TESS01-06.
1. Testerson_101426: 12 sheets 3/4 Tafisa Apres Skis, 4 sheets 1/4 Tafisa Apres Skis (takeoff estimate; the nest decides).
2. Testerson_101426: 240 feet of 15/16" tape, Tafisa Apres Skis (takeoff estimate; the nest decides).
1. Testerson_101426: before you lock: 2 sheets of 3/4 Spring Blossom have no price in the tracker.
```
The lint refuses a bare `1mm`, which the JSON's tape text carries, so tape lines drop the gauge. Program names like `TESS02` pass. No message ever says OK to cut.

| Event | Jim | George | Others |
|---|---|---|---|
| DO NOT CUT | Tray red, toast, phone, digest, Your list | Email at once, any hour | |
| Program past its size | Also a card, kind `sheet_size`: "[I added this sheet on purpose] [Re-nest it]" | | |
| Jim clears it | | "Cleared by Jim" line | |
| Nest checked | Board, digest | Email, business hours, plus "Jim still has N open items" when true | |
| NOT CHECKED, PC offline | Digest when it returns | From the watchdog | Sulaiman's alert |
| Heads-up | | | Barbara and Will, business hours |

- `cards.CARD_KINDS` gains `sheet_size`, with `QUESTIONS` and `_CARD_BACK` entries, and `_act` dispatches on it. Its `choice_ids` are `None`. An answer writes a `clearances` row bound to that program's sha256, so a re-post asks again.
- `_URGENT_KINDS` gains `do_not_cut`. Quiet hours already except DO NOT CUT.
- In shadow mode all of this goes to Sulaiman marked SHADOW, by the existing redirect. No toast.
- `google/Config.gs`: `channelProblems_` also checks `george` and `will` when a new Script Property `GUARDS_ON` is set.

### 5.7 NOT CHECKED from the cloud: `google/Watchdog.gs`
When the heartbeat is stale, email the role `george` the last heartbeat's risks that start "Not checked", once per gap (`openRisks_` already holds them). The design's "posted 11:40 PM" while offline needs more: a new `postedSince_(lastBeat)` walks NG Jobs by `DriveApp`, using a Script Property `NG_JOBS_FOLDER_ID`, for material folders updated after the last beat. It reports "posted, not checked" with the folder's time. Skip this part and say so if that account can't read NG Jobs (UNVERIFIED). Node tests in `google/test/`, mocked. It's a change to the script running the live shadow, so it goes in only when Sulaiman deploys it.

### 5.8 Heads-up: `ngrunner/guards/headsup.py`
```python
def read_takeoff(data: bytes) -> Takeoff | None: ...   # None: blocks missing or not as measured
def order_flags(t: Takeoff, *, total_line: float) -> tuple[str, ...]: ...
def heads_up(job: str, t: Takeoff, flags: Sequence[str], *, revised: bool) -> Message: ...
```
- `Takeoff(sheets: tuple[(material, thk_in, sheets_with_waste, calc_sheets_raw), ...], tape: tuple[(colour, width, feet), ...], total: float | None, ic_color: str)`. The reader picks named fields, so an address can't travel. A test puts a fake address in every field and asserts it never appears in any message.
- Trigger: a new or changed `<Job>_IC_Conversion.json` in a current NG Jobs folder. One message per content hash; a change says "revised". `None` means no message, one DEV item, and a board note "could not verify the takeoff". Never a partial list. Only the list form of `buckets` with `edgeband.rows` reads today, so a thickness-keyed file like the newest job's gets `None`.
- First line: "takeoff estimate; the nest decides", which the design requires, with the basis named: "sheets with 10% waste" from `sheets_with_waste`.
- Never carries client, address, contact or site. Colours as the JSON spells them until `material_lookup` is adopted (UNVERIFIED that the JSON always uses the Tafisa name; RULES 148 wants it).

### 5.9 Labels-to-design: `ngrunner/guards/labels.py`
```python
def read_labels(data: bytes) -> tuple[LabelPart, ...]: ...         # LabelPart(part, name, w_in, l_in, material, sheet)
def catalog_pieces(rows: Iterable[Mapping[str, str]], job: str) -> tuple[Piece, ...]: ...
def labels_in_design(labels, pieces) -> DesignMatch: ...           # code: "all_found" | "some_missing" | "could_not_read"
```
- A fallback that needs no Mozaik file and can be proven now, and it is **not the design's match**: one direction only (a part nested but not in the saved design), date-only, no material, no per-part operations, and only once labels exist.
- `some_missing` reads "could not verify" on the board and adds a pre-flight item. It raises no DO NOT CUT and never sets NEST_CHECKED. Room 0 is compared by name and size, as `R0N` and `R0C` differ.

### 5.10 Shared reads: `ngrunner/guards/reads.py`
`read_shared(path: Path) -> bytes`, with `CreateFile` share read, write and delete (design 5, Reads: Python's default blocks Mozaik's rename-on-save). Raises `CloudOnly` on Windows error 362 or 395 and `Unreadable` otherwise, both plain words. Every `.opt`, `.des`, `.UNS` and `.LCK` read goes through it. Tested live against this PC's placeholders, and on a local file renamed mid-read.

### 5.11 `.opt`: `ngrunner/guards/opt.py` (group 2)
`read_opt(data: bytes) -> OptFacts(encoding, sheets: tuple[OptSheet(width_mm, length_mm, quan, flipped), ...], parts, placed, runs: tuple[str, ...], holes)`, or raises `OptUnreadable`. Sniff the BOM. An `.opt` may be read while Mozaik runs once unchanged for 120 s, hashing the same twice, and parsing (design 5). `reconcile(opt, taps) -> Reconcile(missing, extra)`: a program the nest names and isn't on disk is a real finding. An extra is only a LOG line, since 65 of 551 are hand-named. Built test-first on synthetic files written from the docs, so it is expected to change on first contact with a real one.

### 5.12 Observer: `ngrunner/guards/observer.py` (group 2)
`mozaik_running(*, names=("Mozaik.exe", "Optimize.exe"), psutil=psutil) -> bool | None`, None when processes can't be read. `read_locks(job_dir) -> Locks(uns_owner, lck_owner, opt_lck_owner, lck_age)`. Each minute it logs new owner strings to `owner_log`. Any owner string other than the first seen goes to DEV at once (decision 4). The board shows lock age and owner. It never deletes a lock. Nothing gates on it in P1b.

### 5.13 Toolkit: `ngrunner/toolkit.py` (group 2, adapters group 3)
```python
def take_snapshot(source: Path, dest: Path, *, clock: Clock) -> Snapshot: ...   # copy, then sha256 of sorted (relpath, sha)
def run_tool(snapshot: Snapshot, script: str, args: Sequence[str], *, timeout_s: int = 120) -> ToolRun: ...
# ToolRun(exit_code, stdout, stderr, timed_out)
```
- The build has no Python. `run_tool` calls `NGRunner.exe run-tool <script> <args>`, a new command using `runpy` in UTF-8 mode, inside a Job Object that kills on timeout. From the repo it uses `sys.executable -X utf8`. So any module a script imports must be in the build (UNVERIFIED which). Checks run on staged local copies, never in place, and `*.opt` is expanded first, since Windows doesn't expand it.
- `guards/tool_checks.py`, blocked: `sheet_size(run) -> ToolVerdict` and `br_cam_nest(run) -> ToolVerdict`, code `passed | failed | could_not_verify`. Fail closed on any line not seen in real output. Written only once real output exists. These scripts ran on Linux in the chat's container until now (risk E4), so a first Windows run is itself a test.

## 6. Build order and test plan for group 1

Rules as in P1a: test-first, `-X warn_default_encoding`, synthetic names in git, real files only through tests marked `fixtures`, named by index or count, skipping when absent. **First, collect the fixtures** with a read-only `tools/collect_p1b_fixtures.py`, into `.local-fixtures/p1b/`, with an `index.json` holding each file's role.

| Folder | What to copy | Used by |
|---|---|---|
| `taps/doubled/` | Case D's 3 per-sheet programs and its flip pass | 1, 2 |
| `taps/fixed/` | Case C's 9 per-sheet programs and 1/4 programs; Case F's 3, Case G's 6 | 1, 2 |
| `taps/hand_added/` | Case B's whole 3/4 folder and second material; Case H's 4×10 programs | 2, 3 |
| `taps/sized_49/` | Case A's 4 | 2 |
| `taps/families/` | one merged, RM, RD, fix, pile, flip, long form and hand-named each | 1 |
| `labels/` | Labels PDF of Cases C, D, F, G and K, and `parts_catalog.csv` | 7 |
| `conv/` | all 27 JSONs since 09-20 | 6 |
| `tracker/` | the 5 snapshot files, one `ingested` with a header and one without | 5 |
| `jobsheets/`, `checks/` | all 63 job sheets, the 5 newest crosscheck files, Case A's `_nest_readback.txt` | 5 |

1. **`tap.py`.** Synthetic: a program for each parsing rule (park move ignored, `G81` X ignored, lead-in not a hole, retract required, 0.001in pair found, different tools not paired, merged skips pairs). Real: all fixture programs parse complete. Case D has 8 near pairs on one program; Cases C, F and G have 0; every reach is within 61 × 121. `NGR_NG_JOBS` run, optional and slow: 1,663 parsed, 286 of 1,397 and 0 of 67.
2. **`sheets.py`.** Synthetic: each RULES line, and an unknown name gives `no_rule`. Real: Case C's 1/4 and 3/4 give `nothing_past`; Case B's 3/4 gives `past_size` naming one program; Case H the same; Case A `nothing_past`. The test against RULES' words uses `NGR_SPEC_DIR`.
3. **Store migration 4, `nest_state.py`.** An old database upgrades; every row of the table in 5.3, on synthetic evidence. Property: no evidence set without `opt`, both tools and a design match can give NEST_CHECKED, and a clearance never survives a changed hash.
4. **`watch.py`, job model, board.** The day simulation gains scenarios on a fake clock:
   - a first scan alarms nothing;
   - a doubled program raises DO NOT CUT and a red tray within 2 minutes, with one email to George;
   - a half-written file waits;
   - a re-post with the fault gone returns to NOT CHECKED, and George is told;
   - Jim's clearance closes the card, and a re-post re-opens it;
   - a pause stops nothing here, since these are reads;
   - shadow mode sends only to Sulaiman, marked.
   A board test renders `MaterialRow` per state with icon, label and colour.
5. **Tracker and pre-flight.** Synthetic rows for each code. Real, on today's copied snapshot: 0 duplicates, the 6 and 1 unpriced rows, 1 duplicate key, 7 status spellings, and the 2 and 3 check results of section 3.3. A snapshot over 20 minutes old gives only "could not verify". Job sheets: 63 parse, and Case B's has one line reading 5x9.
6. **Heads-up.** Synthetic: the 3 bucket shapes found, a total as a number and as a string. Real: 20 of 27 give a message, 7 give `None` with one DEV item, the newest job's among them, none holds any `address`, `client` or `site` value from its own file (checked against that file's values, never printed). The tie of 16 of 20 and 16 of 20 is a test. Every message passes `lint`.
7. **Labels match.** Real: the 5 examined jobs give `all_found` once Room 0 is compared by name; a label removed from a copy gives `some_missing`; a file with no `R#C#` gives `could_not_read`.
8. **`reads.py`.** A live read of a placeholder raises `CloudOnly` (skips when the folder is absent); a rename while open succeeds.

Then group 2, against synthetic files: `opt.py`, `observer.py`, the harness on a made-up toolkit folder holding two scripts that print and exit, `Watchdog.gs` under node. Group 1 is enough to run in shadow on this PC against real G: for a few days before anything reaches Jim's PC: real posts raise real verdicts, only to Sulaiman.

## 7. What is needed from Jim

Over Chrome Remote Desktop, with Mozaik closed, into Sulaiman's private Drive folder, never git (`docs/testing/jims-pc.md`, window 2 step 5). The real job names are in the key.

**The toolkit, `Mozaik Shared\_NG_Automation\`, zipped whole** (safest, as imports are unknown). At the very least:
- `scripts\sheet_size_check.py`, `scripts\br_cam_check.py`, `scripts\material_lookup.py`, `scripts\des_cutlist.py`, `scripts\regression.py`, `scripts\crosscheck.py`, `scripts\deliver.py`, `scripts\units.py`, `scripts\elements.py`, and whatever they import;
- `reference\regression\` with `baseline.json`.

**From `Mozaik Shared\`:**
- `Data\Materials.dat`
- `Product Libraries\NG Closets\parameters.dat`

**From `Mozaik Shared\Jobs\`, whole folders as they are** (each `<thk Material>.opt`, `Room#.des/.bak/.sbk`, `JobDat.dat`, `OptStatus.dat`, `MozaikJob.UNS`):

| Case | Why |
|---|---|
| A | `3-4 Egger Classic White.opt`: does its header say 61×109, as its readback does, while its programs read 49×97? |
| B | `3-4 Egger Classic White.opt` as it is now: 6 sheets on 49×97 plus Jim's 61×109; and the second material |
| C | `1-4 Egger Classic White.opt` on 61×109, which must pass; `3-4` with its flip pass; a `.bak` newer than `.des` |
| D | The real positive for `--nest`: its programs show doubled holes |
| E | `--nest` went from 32 doubled housings to clean; a material changed after lock |
| F, G | Nest from an unsaved state; `.bak` newer and smaller; hand flips in G's `.opt` |
| H | 3/4 and 1/4 `.opt` with parts and no `<OptimizeSheet>`; a 4×10 sheet |
| I | `3-4 Tafisa Spring Blossom_L543.opt`: 178 KB, 19 parts, no sheets (BUILD) |
| J | Four genuinely nested `.opt` files with `RunId="-1"` |

**From OneDrive version history, if it still holds them:** Case B's `3-4 Egger Classic White.opt` from before 09-28 2:34 PM, Case A's from before 09-14 3:32 PM, and Case D's from 10-18.

**One line each from Jim, by card or in window 1:**
- Were the 2 Tafisa folders at 59.75 × 107.75 mistakes?
- Does George open shared Mozaik jobs (decision 4)?

**Sulaiman, not Jim:** George's and Will's addresses for Script Properties; Jim's Pushover account.

## 8. The Mozaik lab (30 minutes, with Jim, recorded as traces)

A test job, nothing live. Before and after each step, note `Room#.des/.bak/.sbk`, `.opt`, `MozaikJob.UNS/.LCK`, `OptimizeJob.LCK` (name, size, hash, mtime), and the process list.
1. Open a job: does `.UNS` give way to `.LCK`? What is in it?
2. Change a section, save, close.
3. Change a section, recalculate, close **without saving**.
4. Nest without saving: settles BUILD 546 ("Optimize reads the SAVED job") against the 10-20 evidence. Does the `.opt` change? Do the programs carry the unsaved change?
5. Open the Optimizer and close it without running: parts and no sheets?
6. Save the nest and post: which files, in what order, how fast? Does the 2-poll rule in 5.4 fit?
7. Add a sheet by hand in the Optimizer: how does the `.opt` show it? Does it say hand-added anywhere? (Q3a)
8. If George can: open a test job from his PC. How long until the `.LCK` shows here?

Sets: the `.bak` rule's wording, decision 7e, and the "Not verified" cause.

## 9. Rulings to take

In `docs/decisions.md`'s form, each with the default adopted, chosen by its test: does it ease Jim's work? Sulaiman can overrule any of them.

| Decision | Default | Why |
|---|---|---|
| **Q3** Which sheet size must a nest be on? | RULES: the material library, thickness first, then colour. Until the library is here, only the RULES table's lines carry a rule, and any other name is "could not verify" | 1,607 real programs agree with those lines but for 7 folders. Other Egger colours really do sit on 49×97, so BUILD's "any other Egger colour 61×109" can't be used |
| **Q3a** (new) A sheet Jim added by hand | DO NOT CUT as designed, plus one card, "[I added this sheet on purpose] [Re-nest it]", clearing that one program on that one job. George gets a "Cleared by Jim" line. A re-post asks again | BUILD 576 says mixed sizes are legal, and it happened on purpose on at least 3 real jobs. Rough rate: one card per 40 nests. A file name can't decide it: one such program was re-posted with a plain name |
| **Q4** IC "Charcoal" | RULES: built, nested and tracked as Egger Black U999 ST19 Woodgrain, on 61×109. An old `3-4 Charcoal` folder has no rule, so "could not verify" | Pricing's own note says it replaces Charcoal; real Egger Black programs are all on 61×109. One older job used a `3-4 Charcoal` material on 49×97, so that name mustn't be mapped silently |
| **Q12** Where order call-outs go | RULES: not in Jim's work list; they stay in the workbook, and Barbara and Will get them by email | As the questionnaire recommended |
| **Decision 12a** Colour flag | Any bucket not Classic White | Jim, on the call: "most of the stuff that we do is white". 12 of 20 real jobs flag: it's the common case, so it's a line on the message, not an alarm |
| **Decision 12b** Substitution flag | Name a colour swap once, as a plain line ("IC Arctic White, built as Egger Classic White"), read from `ic_color` against the bucket's material, with no flag. A size swap (a 1-1/8in vertical built at 3/4) is **not** flagged in P1b: it waits for P2's shared converter to write a field for it | That colour swap is on 13 of 20 real jobs, so flagging it flags most jobs. The JSON has no field for a size swap, and the chat writes its free text differently each time, so reading it would be a guess |
| **The takeoff's shape** (new) | One RULES line and a skill line for Jim's chat, through his RULES section 8 process: `buckets` is always a list of objects with `material` and `thk_in`, and `edgeband.rows` is always present | The newest job's file can't give sheets by colour. Without this the heads-up says "could not verify" on some live jobs. Costs Jim one approval |
| **Decision 12c** Total flag | $25,000, on `estimate.total`, which is after discount | The figure from the call (01:24:11). 1 of 20 real jobs flags. `order_flag_total` makes it a setting |
| **The program check** (departure) | Build it first, in NG Runner's code, as an extra check that never replaces Jim's scripts. A fault raises DO NOT CUT; a clean read stays NOT CHECKED with a note. When the toolkit is adopted, both run, and a disagreement goes to DEV | Rule 4 says Jim's rules come from his toolkit. This reads what the machine will cut, a check his chat has already run by hand, and it's the only one provable here. It adds no new rule beyond the 0.5 mm in RULES 536 |
| **DO NOT CUT is "never silenceable"** (departure) | Holds for a settle and for doubled holes. The one exception is Q3a's click on a size | Otherwise Jim's own 5×9 stays red for good |
| **The exit test** (departure) | Section 11 | The named bad nests are not shown to exist |
| **What reaches the phone** (departure from decision 10) | Widen it to job names, dates, material and sheet size | DO NOT CUT is useless without the material. The consent says job names and dates only |
| **Push channel** | Pushover, as built. Slack only if Jim says Slack reaches his phone | The client and its tests exist. UNVERIFIED that Slack reaches his phone |
| **Team tab** | Not built | Only if asked |
| **Interlock** (departure) | Unchanged in P1b | P1b writes nothing Mozaik reads |
| **Bringing this PC's OneDrive back** | No, by Sulaiman's call. The team works in Google Drive, so this PC's OneDrive stays as it is | What lives only on Jim's PC arrives as a zip on Google Drive |

## 10. UNVERIFIED

**Searched for toolkit, `.opt` and `.des`, by name, with nothing readable:** all of `G:` (CNC operations, My Drive, Other computers, every shortcut target), all three OneDrives, and on C: `Downloads`, `Documents`, `Desktop` and the two project folders. Not searched: the rest of C:.

**Formats and tools:**
- Everything in section 3.6: no `.opt`, `.des`, `.UNS`, `.LCK` or `Materials.dat` byte has been read.
- The output, exit codes and imports of every toolkit script. Whether they run on Windows at all.
- Whether `sheet_size_check` passes a hand-added sheet.

**The named cases:**
- Why Case A's readback says 61×109 when its programs read 49×97, and what its `.opt` holds.
- Whether version history holds any pre-fix `.opt`.
- Whether the 2 Tafisa folders were mistakes.

**On Jim's PC:**
- Mozaik's process names there, and whether a post reaches G: in one step or file by file.
- How fast G: shows a new program. The 2.2 s walk and 27 s read were measured on this PC only.
- `JDELA` as the only owner; the 7-byte sizes are from July and are weak evidence. Whether George opens shared jobs.

**Programs:**
- What a doubled housing does in a merged program; those aren't checked.
- That T6 is the 20 mm bit. The tool list doesn't map numbers, and the 16 mm depth and 0.001in apart fit RULES.
- That 0 of 67 stays 0: the sample after the fix is 67 programs, nearly all on 4 jobs.

**Other data:**
- Why `parts_catalog.csv` refreshes when it does, and its `JobModified` time zone.
- Whether the JSON always uses the Tafisa colour name.
- Whether the Apps Script's account can read NG Jobs. Whether George's email reads on his phone.
- How REA, bridges and stacked towers can be told apart for trial labels without `.des`.
- Whether Slack reaches Jim's phone.

## 11. Exit criteria

| Design (P1b row) | Status | Proposed instead |
|---|---|---|
| The known bad nests flagged | Not shown to exist | Case D's real program raises DO NOT CUT for doubled holes. Case B's real 5×9 raises DO NOT CUT and its card, cleared by a click. When `.opt` files arrive: version-history copies must flag, or, if none, a copy of a real `.opt` with its size edited, in the test folder only |
| Case C's 1/4 on 61×109 passes | Testable now on programs | Same, and again on its real `.opt` |
| First 5 real nests labelled trial | Testable in shadow | Same |
| 0 new $0 rows after go-live | Baseline measured today | Same, from today's 6 and 1 |
| 5 heads-ups match the tracker on their basis | 16 of 20 and 16 of 20 on past jobs | Same, on 5 live jobs |
| Added here | | 0 false DO NOT CUT on real posts across the shadow run; every one a person checks. 67 programs is too few to promise it |
| Added here | | Nest checked reached on one real job on Jim's PC, with both of his scripts run from the snapshot |
