# P2 to P5 build spec: what can start now, and on what evidence

Status: working spec for Phases 2 to 5 (design section 7: P2 18, P3 18, P4 16, P5 28 dev-days). Measured on Sulaiman's PC on 2026-10-31, read-only. Jim's toolkit arrived while this was being written and is unpacked, gitignored, under `.local-fixtures/toolkit/20261031/`: 183 scripts, the templates, the docs, `reference\` (the three frozen jobs and `baseline.json`), `Materials.dat`, `NG Closets - parameters.dat`, and ten real Mozaik job folders, Jobs A to J. When this was written none of Jim's scripts had been run, and everything said about them came from reading their source. Section 9's rulings are adopted since (`docs/decisions.md`, "Phase 2's rulings and Jim's approvals"): under R1 his scripts have run here on staged copies, section 2 says what is built, and section 10 what that answered. The design (`01-final-design.md`, local only) is the source of truth. Where this file departs from it, it says so, and section 9 lists each departure as a ruling for `docs/decisions.md`. Real jobs are named by code only: pairs `PAA` to `PBZ` (an IC summary with the chat's conversion), Jobs A to J (Mozaik folders), and frozen jobs X, Y and Z. The key is outside the repo. Made-up names (`Testerson_101426`, abbreviation `TES`) are the only job names here. No toolkit code may enter this repo, which is public.

## 1. What changes the plan

1. **The converter's core can be built and scored here, and a first honest extraction already matches the chat on most real jobs.** 52 NG Jobs folders hold an IC summary, the chat's `<Job>_IC_Conversion.json` and its workbook together (27 at the top level, 25 in `Old`). A first reading of the summary's text, with only `takeoff.py`'s rules re-typed and nothing taken from the JSON, gives (section 3.2):
   - On 2 of the 3 frozen jobs, exactly the takeoff `baseline.json` records: board parts, area, sheets and tape. On the third, the parts are equal and the area and tape differ.
   - On the 25 current jobs that record a board total, the same count on 22.
   - The same area on 20 of 24, and tape within 0.2 ft on 21 of 24.
   - The estimate total on 46 of the 46 files that hold one.

   Every miss has a named cause, and most are judgment the rules don't write down (section 3.3).
2. **Jim's toolkit never takes a summary to a takeoff.** The chat reads the PDF itself and types the parts into each `ic_conversion_<client>.py` as a Python literal (559 typed part lines in 15 of 24 scripts). None of the 24 imports `takeoff.py`, `parse_pulled.py` or `classify.py`, though RULES section 8 says "New conversions import it". `takeoff.py` turns a conversion JSON's `parts` into a takeoff. `parse_pulled.py` reads every PULLED line from the PDF (with pdfplumber) but gives no thickness, built material or colour. The step between them, PDF lines to the board parts as built, exists only as each job's hand-typed list. That missing step is the shared converter.
3. **A real `.opt` doesn't read the way Jim's docs say, and P1b's `guards/opt.py` refuses all 20 of them.** Every `.opt` in Jobs A to J is UTF-8 with no BOM, and starts with a version line (`8`, CRLF) before `<?xml`. RULES 705 says UTF-16LE with a BOM. `opt.py` parses the text with `ET.fromstring`, which rejects anything before the XML declaration. So every real nest would read NOT CHECKED with a DEV item. This is a P1b fix, for whoever owns group 2. Jim's own scripts read `.opt` with regular expressions and would not see the version line.
4. **The real nests settle several of P1b's `ASSUMPTION`s.** A hand-added sheet shows as its own `<OptimizeSheet>` size: Job B's 3/4 Classic White has 6 blocks on 49×97 and 3 on 61×109. Flip passes are blocks with `Flipped="True"`: Jobs C and D have 1 each. Job I's Spring Blossom nest has 21 parts and no sheets. `RunId="-1"` is on all 20. `Flipped` also appears on part elements, so it must be read on the sheet block only.
5. **The material library disagrees with RULES.** In this `Materials.dat`, `3-4 Egger` (id 118) is 61×109. RULES 122 and `sheet_size_check.expected()` say 3/4 Classic White is 49×97, and all 7 Classic White 3/4 nests in Jobs A to J carry 49×97. So `material_lookup.sheet("3-4 Egger")` would answer the sheet `sheet_size_check` calls a fault. It is a question for Jim, never something code picks (section 9).
6. **The per-job script families can now be diffed.** All 126 per-job scripts are here: 24 `ic_conversion`, 23 `tracker`, 18 `build`, 17 `ic_workbook`, 10 `verify`, 9 `wb`, 8 `run`, 7 `panelcheck`, 6 `fix`, 2 `harvest` and 2 `swap`. The other 57 are shared. Their helpers drifted: `snap_depth` in 14 conversion scripts has 7 different bodies, `imp` 9 in 14, `snap_h` 6 in 12.
7. **No script writes the tracker inbox.** The 23 `tracker_*` scripts edit `Lux_Materials_Tracker.xlsx` with openpyxl, a file BUILD 613 says is frozen. 0 of 183 scripts mention `tracker_inbox`. So the "shared tracker tool" collapses nothing: it is new, built from BUILD 817-837, the 21 ingested inbox files and the snapshots.
8. **The toolkit ran in the chat's Linux container.** All 18 `build_*`, 13 of 23 `tracker_*` and 8 shared scripts carry `/mnt/user-data` or `/tmp/claude` paths. `material_lookup.py` defaults to Jim's OneDrive path, which `MOZAIK_DATA` overrides. `crosscheck.py` runs each checker as `sys.executable <checker>.py`. In NG Runner's frozen build `sys.executable` is `NGRunner.exe`, so `run-tool` (P1b 5.13) must also accept a bare script path, or crosscheck can't run there.
9. **The docs in the zip are the snapshot.** RULES, BUILD, INDEX, SCHEMA, FAILURES and BUDGET are byte-for-byte identical to `NG IC MOZAIK\` (0 differing lines; INDEX Rev 7.0, 2026-10-21). The zip adds `SOP.md`, `IC_VIEW.md`, `RULES_full_20261006.md` and the parts catalog's refresh scripts.

## 2. Where each piece stands

**Group 1** can start now and be proved here on real data. **Group 2** can start now, but its proof needs Jim, his PC or Mozaik. **Group 3** is blocked, and the row names what unblocks it. "Needs the run ruling" means proof needs Jim's scripts run here (section 9, decision R1), which is Sulaiman's call and not Jim's; R1 is adopted, so those can be proved here now. "Built" names what exists as of 2026-10-31.

| Phase | Piece | Group | Evidence |
|---|---|---|---|
| P2 | **Shared converter core**: summary to board parts to takeoff (`convert/`) | **1. Built** | Frozen jobs X and Z equal `baseline.json`'s takeoff; Y differs by its two named rows. On the 23 top-level closet pairs, board parts equal the chat's on 17, and each of the rest differs by exactly its Unplaced rows (`tests/test_convert_takeoff.py`). It enters Jim's toolkit only through his RULES section 8 approval |
| P2 | Takeoff tie to the tracker | **1. Built** in the inbox writer: Calc Sheets is area ÷ 32 (R6) | 47 jobs with tracker rows. Tape equals the tracker's Calc Lin Ft on 21. Calc Sheets is area ÷ 32 on 25, and every one of the 37 list buckets' `calc_sheets_raw` is area ÷ 32 |
| P2 | Diff of the per-job families | **1. Built** (`tools/diff_families.py`) | All 126 scripts are here, and 21 of 24 conversions name a job that has a pair on Drive. Read statically with `ast` (section 3.4). Which differences are unwritten rules is still a person's call |
| P2 | Shared workbook (renders the JSON) | **1**, partly | 52 real `.xlsx` beside their JSONs, plus `wb_generic.py` and 17 `ic_workbook_*`. Needs openpyxl, which NG Runner doesn't carry. No `.xlsx` opened yet (UNVERIFIED) |
| P2 | Shared tracker tool: inbox writer, "did it land" | **1. Built** (`ngrunner/tracker_inbox.py`) | BUILD 817-837 gives the format, 21 `ingested_tracker_inbox_*.csv` the real files, the snapshots the landing check. Nothing writes a batch to Drive before P4's Lock. Where it departs from 5.6: `docs/decisions.md`, "The tracker inbox" |
| P2 | Idempotent ingest (Jim's `Tracker_Ingest.gs`) | **2** | A text copy is on Drive (`Tracker_Ingest.gs.txt`), and a change can be written and tested under node here. Deploying it changes Jim's tracker: decision 9, Jim |
| P2 | Shared check runner: reading checker output | **1. Built** (`ngrunner/checks/readers.py`) | Written from the checkers' source, then checked against what they printed on Jobs A to J and Jim's own crosscheck files of Jobs C to G (`tests/test_checks_real.py`). How each is read: `docs/decisions.md`, "The check runner's readers" |
| P2 | Shared check runner: running the checkers | **1. Built** (`ngrunner/checks/crosscheck.py`) | crosscheck ran from a copy of his toolkit on staged copies of Jobs A to J. `parm_check`'s `Lux Closet Template Claude` job came in Jim's answers of 2026-10-31: staged beside each job, it compares on all 45 real jobs with an IC Conversion (`docs/decisions.md`, "P3: the replay trial and the audit") |
| P2 | Regression reproduced (`regression.py`) | **Done** | Clean on a staged copy laid out as Jim's tree, in the tools' Python: his 3 frozen jobs reproduce (`tests/test_toolkit_real.py`) |
| P2 | RULES line "never write a per-job script" | **2** | A line for Jim's approval, through RULES section 8 |
| P2 | "Jim reviews the first 10 conversions" | **2** | Jim |
| P2 | Reader evaluation: preparation (wall crops, parts-list text, truth from the as-cut `.des`) | **1. Built** (`tools/reader_eval/`, dev only) | 41 elevation pages give 41 crops, none with text over it, and at least 26 test walls pair with their as-cut truth (`tests/test_reader_real.py`). Nothing is sent anywhere |
| P2 | Reader evaluation: any read by a model | **2**: Jim consents (2026-10-31). Built up to the call; it waits for a provider key in Credential Manager (`NGRunner/openai_key` or `NGRunner/anthropic_key`), then decisions 16 and 17 come from the evaluation | Only crops with no text over them are sent |
| P3 | Snapshot with nightly adoption | **1. Built** (`toolkit.try_adoption`, the supervisor's night step); **2** for "3/3 byte-identical on the packaged build" on Jim's PC | Adoption is `regression.py` clean on that very copy, proven on a staged copy of his real toolkit. R7 fixed item 8. How it runs: `docs/decisions.md`, "Adopting a copy of Jim's toolkit". Nothing in the build makes a tools' Python yet, so in a build his scripts run in `NGRunner.exe` itself, which no real run has tried |
| P3 | Symmetric `.bak` guard | **1. Built** for the reader and the diff (`guards/bak.py`, findings only); Q19 for the rule | 30 `.des` with `.bak` and `.sbk`, plus 18 `.bak2` to `.bak4`, all read and compared. All 30 `.des` start `15` then CRLF and are CRLF throughout. By size and contents, section 10 |
| P3 | Close prompt and Check now | **1. Built** on the day simulation's fakes (scenarios 75 to 79 and 96); **2** for the trigger on Jim's PC | A later close asks only when the job holds a change no check has looked at, and never while a check waits for it; the first close always asks. The trigger is Mozaik closing on Jim's PC (no real `.LCK` read yet) |
| P3 | Audit of chat-built jobs (panel set, top line, `op_schema`) | **1. Built and measured** (`findings.AUDIT_CHECKERS`, NG Runner's own top line in `checks/topline.py`) | 45 real jobs audited (Jobs A to J, 32 P5 jobs, the 3 frozen jobs; 8 P5 jobs have no IC Conversion), `parm_check` with the template job; counts per kind and job set in `docs/decisions.md`, "P3: the replay trial and the audit". NG Runner's own top line agrees with Jim's `top_line` on all 99 rooms both read. No audit passes whole while Q1 is open |
| P3 | Trial checks: 15 compared with chat | **1. Replayed offline** (`checks/trial.py`); **2** for the live 15 | 25 real jobs replayed finding by finding against the chat's saved output: 0 missed act items, 0 false cleans, noise over 1 a job on 3 jobs, all from Jim's checkers as they print. 4 replays (Cases C, E, F, G: same rooms, every checker) stand for 4 of the 15 |
| P3 | Repair items with a ready chat line | **1** | Text from findings. Needs the check runner |
| P3 | Takeoff on intake, and the order email from it | **1. Built** (store migration 5, notes schema 3) | On the 52 pairs: 36 complete closets, 10 closets with 1 to 7 lines it could not verify (34 and 12 before the answers read from Jim's files on 2026-10-31), and 6 garage or mixed jobs that stay in chat; none failed (`tests/test_takeoff_real.py`). An incomplete takeoff sends the chat's whole list instead. As built: `docs/decisions.md`, "The takeoff on intake" |
| P3 | "Takeoff equals the chat's on 10 jobs" | **1** | Section 3.2: 20 of 24 by area. With the converter as built, 17 of 23 top-level closets by board parts (above). The exit itself is on 10 live jobs |
| P3 | Settle, and "I already told chat" | **1. Built**, with settles for all jobs and the one settled list (P4's second round); **2** for the RULES line | Store and board work. A settle for all jobs names its condition and stands for every finding of its kind; the list is NG Runner's own copy of `NG Jobs\_NG Runner\` until P4 goes live. Jim's chat reads it through a RULES line he approves, not drafted yet |
| P4 | Leases | **1. Built** on fakes; **2** for the chat side | The lease file's RULES line needs Jim's approval. The command box's `lock <Job>` is the board's Lock button, and the heartbeat's open risks name each fix approved and not yet verified |
| P4 | Lock design, Finish lock, Lock: nest side | **1** | 20 real `.opt`: sheets are the sum of `Quan` over `Flipped="False"` blocks, programs are `<OptimizeGCodeRun Filename>`. P1b's `opt.py` reads all 20 since R8 |
| P4 | Lock: tracker side | **1. Built** in shadow (P4's second round) | Inbox writer and landing check, above. Names from NG Runner's own takeoff by RULES' names, each held to the Pricing block (31 of 52 real pairs named, from 11); the chat's IC Conversion only when NG Runner's own takeoff isn't complete. A shadow batch blocks only its own job's next drop and closes against the chat's own rows on the real tracker: `docs/decisions.md`, "P4's second round" |
| P4 | "5 locks match the chat" | **2**: counted as shadow batches close | Health counts the jobs whose shadow locks, made from NG Runner's own takeoff, matched the chat's own rows. The shadow run on real jobs makes the count |
| P4 | Check-before-lock | **1**, needs the run ruling | Needs the check runner |
| P4 | Job sheet (`cnc_job_sheet.py`) | **1. Built** (`lock/job_sheet.py`) | His script runs from an adopted copy, in the tools' Python, on staged copies of Jobs A to J: every count matches the nests, and four have a flip sheet that isn't one (question 18). The day simulation runs it on Case A at lock (scenario 95) |
| P4 | Fixers as diffs, backup, inverse-patch Undo | **1** on copies of Jobs A to J's `.des` | Byte-level work on CRLF files with a version line, checked by their own checker. Writing into real Mozaik jobs waits for P4's go-live |
| P4 | Fixer survival after Jim's next save; approving each fixer; "0 writes while Mozaik ran" | **2** | Mozaik, on Jim's PC |
| P5 | Builder (P5a), "3 archived builds reproduced" | **1. Started.** The reader is decided (Claude, `docs/decisions.md`, "P5's reader"), the placement rules are built (`ngrunner/build/placement.py`, decisions "P5a placement"), and so is the room model in memory (`room.py`, `walls.py`, `schedule.py`, decisions "P5a builder, slice 1"): walls from the WALL/AREA table, and per wall a schedule of every panel's x, Elev, height, depth, mount, Thru/Left/Right, radius, REA, stacked and product, each with its source or "could not verify". Room#.des is written from it (`writer.py`, `products.py`, decisions "P5a builder, slice 2"): seeded from the template job's Room1, each panel cloned from Jim's own products, 20 gates on the written bytes (`gates.py`), and committed only when all pass, through `commit_write` into NG Runner's own shadow copy (`commit.py`, writes.py's "P5 shadow"). The plan view is read by code (`plan.py`, decisions "P5a plan reader"): the chain's order, each wall's angle, the free ends and the joints, or "could not verify" with the reason | Compared as Jim's regression compare does, with each frozen room's own panels standing in for the reading and its own chain for the plan: X and Z reproduce on every wall and every panel (4 + 20 walls, 14 + 16 panels); Y could not verify (its top line, its run start) and has one side Jim built before the 2026-09-21 rule. On the 26 DEV walls from Claude's cached readings: 89 panels, 50 right, 39 could not verify, 0 wrong. The plan reader on every real room Jim built as designed (96: the frozen jobs, Jobs A to J, the P5 jobs): 84 right, 3 built turned a half turn from the plan, 9 could not verify, 0 wrong; with their plans as read, X and Z still reproduce on every wall and every panel, and 14 of the 18 DEV rooms read their chain. Written, with each frozen room's own colour standing in: Jim's `regression.py compare` from an adopted copy says `Regression clean — nothing moved.` on X and on Z, and `1. Room1.des: missing from the rebuild` on Y, which isn't written (17 values could not verify). On X and Z 19 of 20 gates pass; gate 6 could not verify (his `elev_panels.py` needs poppler's `pdftoppm`, not on this PC, and finds no wall box on X's pages), so nothing is committed. The 18 DEV rooms all wait for their plan and their colour, 12 for a panel value too. Still to build: where a room's colour comes from, and a renderer for `elev_panels.py` |
| P5 | Reading at filing, overnight staging, promotion (P5b) | **3**: the night setup (questionnaire 5 and 6). The evaluation and Jim's consent are done | |

## 3. Measured formats

### 3.1 The IC summary's text (pypdf, as pinned)

- **Pages:** 52 summaries, read in 15 s in all. Page 1 is the contact block; never read it beyond PROJECT NAME (P1a does that). Each design has a `STYLES, HARDWARE, AND FINISHES` page with its `WALL/AREA NAME (W X H)` table, elevation pages, and a design `Parts List`. Then an `Estimate` page and the `Project Parts List` pages holding PULLED.
- **Two PULLED layouts, as BUILD 48-55 says:**
  - 24 landscape: `PULLED QTY ITEM # DESCRIPTION DIMENSIONS`, two columns, quantity first.
  - 28 portrait: `PULLED ITEM # QUANTITY DESCRIPTION DIMENSIONS`, one column, item first.

  pypdf gives one token per line in reading order. In the landscape layout it often puts the right column's next row inside a description: `SH 14x23.5x3/4 ADJ WH 1 300444237 SH 16x19x3/4 ADJ ML`. A quantity of 1 to 4 digits followed by an IC item code (`\d{8,10}` or `[A-Z]{2,5}(-[A-Z0-9]{1,4})?`) starts a new row. Before this split, 1 job read 66 parts against 131.
- **Find PULLED pages by the word PULLED in the first 300 characters**, not by the title `Project Parts List`: some newer renders put the title elsewhere. Getting this wrong lost 46 parts on one job.
- **Sections:** `CTO`, `Components`, `Fronts`, `Hardware`, `Accessories`, `Trim`, `Aluminum Doors & Drawers`, `Studio Activated Custom Parts`. Footer lines carry the store's address and phone and are dropped.
- **Board lines:** `<KIND> [words] <a>x<b>x<thick> <codes...> <colour code>`, the kinds being `VT SH DK DR DW CK FF WS BP HP TR Base TR Rail Cover TR`. `Custom Vert` is a VT. Thickness is `3/4`, `3/8`, `1`, `1-1/8`, `0.4` or `0.6`.
- **A rail cover can be a hardware line** (`White Rail Cover`, item `400000570`) with no dimensions. RULES 224 builds it at 3.5×96 from 3/4.
- **A third description form** (pair PBA): `Shelf 14x18 Fix WH` and nominal drawer faces `DW 08x24`. The chat read these as `SH 14x17.9` and `DW 7.4x24.5`, which nothing in the text gives. The converter refuses them ("could not verify"), never guesses.
- **The estimate total** is the figure after the line `Total` on the Estimate page. It equals the JSON's `estimate.total` on 46 of 46 files that hold one.
- **Never read** page 1 beyond PROJECT NAME, or the Estimate page beyond `Total`: both hold the client's name, address, email and phone.

### 3.2 The extraction against the chat (the hypothesis, tested)

Rules applied, all from `takeoff.py` and RULES section 2, re-typed in a scratch script outside the repo:
- BP under 1/2in is built 1/4.
- TR Rail Cover is 3/4 at its face size.
- A closet WS at 1-1/8 is built 3/4.
- Tape: one long edge (IC's second dimension) on FM VT, SH, DK and TR Base; height plus twice the depth on a WM VT (`FN`, `RN`, `LN`); twice the length on TR Rail Cover; all four edges on DW, DR and WS; none on CK, FF and BP.
- Sheets are `ceil(sqft × 1.10 / sheet)` per bucket.
- Scribe and Quarter Round are dropped, and so are glass doors (`DR ... SH-G`).

Scored on named fields only. Client fields are never compared or printed.

| Measure | Result |
|---|---|
| Frozen jobs, against `baseline.json`'s takeoff | X and Z equal on parts, sqft, sheets and tape. Y: parts 36 = 36, sqft 88.39 against 88.64, tape 128.5 against 119.0 |
| Board parts, top-level pairs with a total | 22 of 25 equal |
| Board parts, `Old` pairs with a total | 13 of 23. Older conversions predate the rail-cover rule (2026-09-01) and the 1/4 rules (10-15, 10-20), so `Old` measures rule drift as much as the reader |
| Area per thickness, top level | 18 of 24 equal within 0.05 sqft. Total area 20 of 24 |
| Tape, top level | 21 of 24 within 0.2 ft |
| Estimate total | 46 of 46 |
| Design count | 23 of 24 top-level files that list designs |
| BUILD 65 gate, PULLED against the design lists | 44 of 52 equal. Most of the 8 are a rail cover printed only on PULLED |
| Per-colour bucket (23 files with list buckets, 37 buckets) | All buckets equal on 14 of 23 files. Sheets with waste equal on 30 of 37 buckets, with RULES' table for sheet sizes |
| `calc_sheets_raw` | Area ÷ 32 on 37 of 37 buckets: a 4×8 basis, no waste. RULES' formula is the other column, `sheets_with_waste` |

So the hypothesis holds for the takeoff's core: board parts, area by thickness, sheets and tape from the PULLED list. It does not yet hold for:
- splitting by colour on a job with two colours;
- garages (pair PAW, BUILD's three-bucket rule);
- the judgment in 3.3.

### 3.3 Why the rest differ, by pair

| Pair | Difference | Cause | What the converter does |
|---|---|---|---|
| PAE | 3 back panels in a bucket the chat named `White 1/4 Vinyl` with `thk_in` 0.75; a countertop's colour | The chat's own label slip; Jim said in chat the summary was wrong for one room | Counts as RULES says. Jim's override is a settled-list entry, which the summary can't show |
| PAN | 45 sqft: 1in colour fronts the chat built at 3/4 | RULES 217-220 cover 1-1/8 Tafisa verticals and tops, not fronts | A Q for Jim; until then "could not verify" |
| PAO | 4 `CK DBack ...x0.6` and a 1in line | Rules silent: BUILD excludes only "under 0.3in" | A Q; "could not verify" |
| PAV | 1 `TR 5x96x0.6 COVE` (crown) | Same | Same |
| PAW | All buckets | A garage: shelves 1in AMWH, backs 3/4 AMWH, an unpriced Pewter WS taken as 1in AMWH | Garage branch: BUILD 127-135. Pewter is Q8 territory |
| PBA | 10 parts | The third description form (3.1) | "Could not verify", naming the line |
| PAL | Tape 341.8 against 350.7 | A rail cover printed as hardware, banded by the chat at 8.9 ft more | Unexplained (UNVERIFIED) |
| PAP, PAQ | No chat total | The JSON has no `total_board_parts` or `parts` | Not scored |

### 3.4 Jim's toolkit, read from source

**Families** (183 `.py`):

| Family | Files | Lines | Notes |
|---|---|---|---|
| Shared | 57 | 6,666 | `takeoff` 107, `parse_pulled` 129, `classify` 150, `emit` 116, `budget` 72, `material_lookup` 184, `units` 23, `elements` 374, `crosscheck` 85, `deliver` 121, `regression` 240, `sheet_size_check` 112, `br_cam_check` 221, `des_cutlist` 44, `parm_check` 84, `panel_xcheck` 150, `cnc_job_sheet` 47, `elev_panels` 78 |
| `ic_conversion_*` | 24 | 2,935 | Hand-typed part lists. No PDF read and no shared import, except 1 that imports `budget` and 1 that imports `ic_conversion_0922.build` |
| `ic_workbook_*`, `wb_*` | 17 + 9 | 3,210 | openpyxl. `wb_generic.write(meta, C, PUL, pdf)` is a shared writer, imported by 5 |
| `tracker_*` | 23 | 1,978 | Edit the frozen `Lux_Materials_Tracker.xlsx`. 13 carry Linux paths |
| `build_*` | 18 | 2,673 | All carry Linux paths. 10 scripts import `build_jobs_0914` |
| `verify_*`, `panelcheck_*` | 17 | 1,858 | Superseded by `crosscheck` (handoff brief) |
| `fix_*`, `run_*`, `swap_*`, `harvest_*` | 18 | 1,245 | One-offs, except one `run_*` script that is the garage cabinet builder (BUILD 112) |

- **Third-party imports:** openpyxl (49 files), numpy (6), PIL (5), scipy (3), pdfplumber (3: `parse_pulled`, `parse_product_summary`, `parse_mozaik_cutlist`), fitz (1). NG Runner's venv has none of them, only pypdf.
- **The core checkers** import only the standard library and `units`.
- **`crosscheck.py`:**
  - It runs 14 checkers on product rooms, Room0 apart. INDEX says "every checker", but `hinge_check`, `sheet_size_check` and `br_cam_check --nest` are not in its list (stage map D17).
  - A checker counts as flagged when it exits non-zero, or when its output matches `^\s*(!!|MISSING|EXTRA)|\bdrifted\b|\bdiffer\b`. So `??` lines never flag.
  - It writes `<Job>_crosscheck.txt`, with line 1 `<Job> - crosscheck <UTC>` and line 4 the verdict, as P1b measured. It exits 1 when anything is flagged.
- **`sheet_size_check.py`:**
  - Reads the size from the first `<OptimizeMaterial>`'s `Width`, `Length` and `DisplayName`.
  - Counts sheets as `Quan` over `<OptimizeSheet` blocks not `Flipped="True"`.
  - Takes programs from `<OptimizeGCodeRun Filename>` (its base name), and looks for them in `<NG Jobs job>\<opt stem>\`.
  - **A `.opt` with no `<OptimizeMaterial>` prints `??` and still exits 0**, which its own docstring warns "looks like a pass". The adapter must read `??` as could not verify.
  - On the 20 real files, every `<OptimizeMaterial>` is present.
- **`regression.py`:** three layers (rule gates, checker output, takeoff), the three frozen jobs, `--accept` only on Jim's word. It exits 1 with a numbered list when anything moved.
- **`baseline.json`:** per job, `gates` (0 on each), `checkers` (text per checker and room), `takeoff` and `schedule`.

### 3.5 Jim's job files (Jobs A to J, read by bytes)

- **Folders:** 10 folders, 287 files: 30 `.des`, 30 `.bak`, 30 `.sbk`, 18 `.bak2` to `.bak4`, 20 `.opt`, 150 `.dat`, 6 `MozaikJob.UNS`, 2 `.txt` and 1 `.xml`. No `.LCK`.
- **`.opt`:** UTF-8, no BOM, a version line `8`, then the XML (item 3), from 7,246 B to 660,967 B. Sheet sizes in inches:

  | Material | Sizes |
  |---|---|
  | 3/4 Classic White | 49×97 on 7 jobs; Job B adds 3 blocks at 61×109 |
  | 3/4 and 1/4 Tafisa Apris Ski (Job E) | 61×109 on both. RULES 124 and 134 say 49×97 |
  | 1/4 Egger | 61×109 |
  | 3/4 Egger Black | 61×109 |
  | 1in AMWH | 49×120 on Job H, 49×97 on Job J |
  | 3/4 AMWH (Job H) | 11 at 49×97 and 6 at 49×120 |
  | 1/4 Tafisa Classic White (Job J) | 49×97 |

- **`.des`:** all 30 start `15` then CRLF and are CRLF throughout. Each has a `.bak` and a `.sbk`.
- **`Materials.dat`:** UTF-8, 109 materials. Sizes in inches:

  | Material (id) | Size |
  |---|---|
  | `3-4 Egger` (118) | 61×109 (item 5) |
  | `1-4 Egger` (119) | 61×109 |
  | `Egger 3-4` (124) | 61×109 |
  | `Egger 1 in` (125) | 49×97 |
  | `3-4 Tafisa` (101), `1-4 Tafisa` (106), `3-4 Arauco` (129), `3-4 Am Lam Wh` (102), `1 in Am Lam Wh` (107) | 49×97 |

  `White 1/4 Vinyl` and `1-4 Tafisa Classic White` are not material names in it.
- **`NG Closets - parameters.dat`:** UTF-8, 4,984 lines.

## 4. What the pieces share

- **One rule table, ported, with a check that it stays Jim's.** AGENTS.md rule 4 says port or call Jim's scripts, never re-type RULES.
  - The converter ports `takeoff.py`'s constants and its `eb_lf` rules into one module, `ngrunner/convert/rules.py`, and adds only the step `takeoff.py` lacks.
  - A `fixtures` test reads `takeoff.py` from `NGR_TOOLKIT` by `ast`, never by import, and fails if its `WASTE`, its tape rules or its type list move. Under the run ruling, a second test runs `takeoff.takeoff(normalise(...))` on the converter's output for the 52 pairs and asserts the same numbers.
- **Where it runs.** NG Runner first, on the filed summary, writing nothing in NG Jobs in P2: the result goes to the board, and to Sulaiman in shadow. P3 allows the IC Conversion `.json` and `.xlsx` as new files (design 3.2). Jim's chat gets the same code as a toolkit script only through RULES section 8: regression clean, Jim's approval, then "Jim reviews the first 10 conversions".
- **The PDF reader.** pypdf, as pinned (`pypdf==6.21.4`), so the build gains nothing. Jim's `parse_pulled` uses pdfplumber. A dev-only differential test compares the two readers' PULLED rows on the 52 summaries when pdfplumber is in a separate venv. Whether pypdf is in the chat's container is UNVERIFIED.
- **Client data.** As in `headsup.py`: named fields only. The converter never reads page 1 beyond PROJECT NAME or the Estimate page beyond `Total`. Its results' repr holds codes and numbers only. A test puts a fake address and email into a synthetic summary's contact block and asserts that neither reaches any output.
- **The store.** P2 adds a `takeoffs` table, appended as the next migration after P1b's: `studio_id`, `render_sha256`, `rules_sha256` (the hash of `takeoff.py` the rules came from), `result` JSON, `could_not_verify` JSON, `at`. One row per render and rules version.
- **Fixtures.** A read-only `tools/collect_p2_fixtures.py` copies into `.local-fixtures/p2/pairs/<index>/` the summary, JSON and workbook of every pair, with `index.json` holding the folder's top or `Old` place and nothing else. The toolkit stays where it is. A new variable, `NGR_TOOLKIT`, defaults to `.local-fixtures\toolkit\20261031`. Tests name pairs by index, never by name.

## 5. Contracts, in build order (group 1)

### 5.1 The summary's lines: `ngrunner/convert/summary_lines.py`
```python
@dataclass(frozen=True)
class SummaryLine: section: str; item: str; qty: int; desc: str; page: int; column: int

def read_pages(data: bytes) -> tuple[str, ...]: ...                 # pypdf text per page
def pulled_lines(pages: Sequence[str]) -> tuple[SummaryLine, ...]: ...   # raises SummaryUnreadable
def design_lines(pages: Sequence[str]) -> Mapping[str, tuple[SummaryLine, ...]]: ...
def estimate_total(pages: Sequence[str]) -> float | None: ...
def layout(page: str) -> Literal["qty_first", "item_first"]: ...
```
Section 3.1's rules exactly: the layout comes from the order of `QTY` or `QUANTITY` after `PULLED`. A landscape row inside a description splits on a quantity followed by an item code. A continuation line joins the row above it. A dimension line (`28.858"W x 5.906"H`) is kept apart. Never read page 1 or the Estimate page beyond the two facts named. `SummaryUnreadable` (no PULLED page, or an unknown header) is plain words.

### 5.2 Board parts: `ngrunner/convert/parts.py`
```python
@dataclass(frozen=True)
class BoardPart:
    kind: str; qty: int; a_in: float; b_in: float; printed_thk: float; built_thk: float
    codes: tuple[str, ...]; colour: str; wall_mount: bool; source: str   # "pulled" | "pulled_hardware"

@dataclass(frozen=True)
class Unplaced: line: SummaryLine; reason: str          # the "could not verify" list

def board_part(line: SummaryLine) -> BoardPart | Unplaced | None: ...   # None: not a board line
```
- Rules from `rules.py` only: BP under 1/2 is built 1/4; TR Rail Cover is 3/4; a rail cover hardware line is built 3.5×96 at 3/4; a closet WS at 1-1/8 is built 3/4, in the colour IC prints on it; a white shelf at 1-1/8 is built 1in; a white part built 1in or thicker makes the whole job's white AMWH, its 1/4 backs White 1/4 Vinyl; glass doors, glass shelves (`SHG`), Scribe and Quarter Round are not board. The last four were answered from Jim's own files on 2026-10-31 (`docs/decisions.md`), the whole-job AMWH awaiting his yes.
- `Unplaced`, never a guess:
  - a board kind with no `a×b×t`;
  - a thickness of 0.6;
  - a 1in part in a colour other than white, and a 1-1/8 shelf in one;
  - a colour no material in Jim's library carries, named (Navy, IC MS), never swapped for another;
  - the `Shelf` and nominal `DW` forms;
  - any kind not in the list.

### 5.3 The takeoff: `ngrunner/convert/takeoff.py`
```python
@dataclass(frozen=True)
class Bucket: colour: str; thk_in: float; parts: int; sqft: float; sheet_in: tuple[float, float] | None
              sheets_with_waste: int | None; calc_sheets_32: float; tape_lf: float

@dataclass(frozen=True)
class Takeoff: buckets: tuple[Bucket, ...]; board_parts: int; tape_lf: float; estimate_total: float | None
               gate_pulled_equals_designs: bool; unplaced: tuple[Unplaced, ...]; branch: str

def takeoff(parts: Sequence[BoardPart], *, sheet_size: Callable[[str, float], tuple[float, float] | None]) -> Takeoff: ...
```
- **Buckets** are colour code and built thickness, project-wide, with a ceiling once per bucket (RULES 141). A rail cover printed with no colour joins the job's largest 3/4 bucket.
- **`sheet_size`** comes from RULES section 2's table, as P1b's `guards/sheets.py` does. Until Jim answers item 5, `Materials.dat` is never consulted for it. `None` gives `sheets_with_waste` None and a "could not verify" line, never 49×97 by default (a departure from `takeoff.py`, which defaults to 49×97).
- **Both sheet numbers** are carried, named: `sheets_with_waste` (RULES) and `calc_sheets_32` (what `calc_sheets_raw` and the tracker's Calc Sheets hold).
- **A garage or mixed job** (branch from P1a's `intake/branch.py`) gets `unplaced` for every line until BUILD 127-135's garage rule is ported, since pair PAW shows it differs.

### 5.4 Scoring: `tools/score_takeoff.py` (dev only)
`score(takeoff, conversion_json) -> Score(parts, sqft_by_thk, tape, estimate, designs, buckets)` on named fields, with tolerances 0.05 sqft and 0.2 ft. It reads JSONs through the same shape rules as `headsup.read_takeoff`, plus the dict shapes. It prints pair index and codes only. It is the P3 exit "takeoff equals the chat's on 10 jobs".

### 5.5 The family diff: `tools/diff_families.py` (dev only, read-only)
`ast` only, never an import. Per family: each function's name and body hash, literal data lines against logic lines, imports, and hard-coded paths. Output is a local report outside the repo, since it names clients. The design's 3 dev-days become one: section 3.4 already holds most of it. What remains is to list, for each drifted helper (`snap_depth` 7 bodies, `imp` 9, `snap_h` 6, `eb` 3), what differs. Each difference is either a rule `takeoff.py` already holds, or a question for Jim (handoff brief risk 3: "some of those differences are rules nobody wrote down").

### 5.6 The tracker inbox: `ngrunner/tracker_inbox.py`
```python
def inbox_rows(job: str, takeoff: Takeoff, *, verb: Literal["append", "set"]) -> list[list[str]]: ...  # header row always first
def landed(job: str, rows: Sequence[Sequence[str]], snap: TrackerSnapshot) -> Landing: ...           # per row: landed | missing | differs
```
BUILD 817-837's verbs and columns, with the header always first. `customer` is left blank: the design forbids client data, and the tracker's Customer column is never read (`guards/tracker.py`). Whether a blank customer is accepted is UNVERIFIED. P2 builds and tests; nothing is written to `NG Conversion program Claude\` until P4's Lock. The 21 archived inbox files are the format's test fixtures (5 without a header, which BUILD says lost their first line).

### 5.7 Preparing the reader evaluation: `tools/reader_prep.py` (dev only)
For every summary: each elevation page's embedded raster (pypdf `page.images`), its pixel size, and the wall's parts-list lines as text. Code asserts that no text span overlaps a crop (design 3.6). The truth is the as-cut `.des` of Jobs A to J and the frozen rooms: panel order, X, top and bottom per wall, read by bytes. **Nothing is sent to any model or service.** The step that sends waits for decision 10 and decisions 16 and 17.

## 6. Test plan

As in P1a and P1b: test-first, `-X warn_default_encoding`, synthetic names in git, real files only through tests marked `fixtures`, named by index, skipping when absent.

1. **`summary_lines.py`.** Synthetic: a portrait page, a landscape page with a row inside a description, a continuation line, a dimension line, a footer, a page titled differently, and an unknown header (raises). Real: all 52 summaries give a layout and at least one PULLED line. The 46 estimates equal their JSON's `estimate.total`.
2. **`parts.py`.** Synthetic: one line for each rule in 5.2 and each `Unplaced` reason. Real: pairs PAN, PAO, PAV and PBA give exactly the `Unplaced` lines in 3.3, by kind and count.
3. **`takeoff.py`.** Synthetic: one bucket for each RULES table line, a rail cover with no colour, and a missing sheet size giving None. Real:
   - frozen X and Z equal `baseline.json`'s takeoff; Y equals on parts and is named as a known difference;
   - the counts of 3.2 are pinned as floors (22 of 25, 20 of 24, 21 of 24), so a regression fails a test. They are never fitted to.
4. **Client data.** A synthetic summary whose contact block, estimate page and every description carry a fake address, email and phone: nothing reaches `Takeoff`, its repr, the store row or a message.
5. **`ast` tie to `takeoff.py`.** Under `NGR_TOOLKIT`: `WASTE`, the type list and the tape rules read from Jim's file equal `rules.py`'s. Skips when absent.
6. **Under the run ruling only:** a staged copy laid out as Jim's tree runs `regression.py` (clean against the baseline), `takeoff.takeoff(normalise(...))` on the converter's output, and each core checker on the frozen rooms, comparing its text with `baseline.json`. Never in `.local-fixtures`, never in place.
7. **P1b's `opt.py` against the 20 real `.opt`** (owner of group 2, not this phase): each reads, its sheet count equals the sum of `Quan` on `Flipped="False"` blocks, and Job I's reads "parts and no sheets".

## 7. What is still needed from Jim

Nothing for group 1. For the rest:
- **His approval, through RULES section 8:** the converter as a toolkit script; the RULES line "never write a per-job script"; the idempotent ingest change in `Tracker_Ingest.gs` (decision 9); each fixer (P4).
- **His consent** (decision 10) before any drawing goes to a model, including the evaluation.
- ~~**The `Lux Closet Template Claude` job folder**, for `parm_check`.~~ Came in his answers of 2026-10-31.
- **One line each, by card:**
  - Is `3-4 Egger` at 61×109 in the library right, or is it RULES' 49×97? (item 5)
  - Job E's 3/4 and 1/4 Tafisa on 61×109: on purpose?
  - Are 1in colour fronts built at 3/4? (PAN)
  - Are `0.6` crown and `DBack` lines board? (PAO, PAV)

## 8. Exit criteria

| Design | Status | Proposed |
|---|---|---|
| P2: 3 frozen jobs and 3 archived conversions reproduced | Takeoff: 2 of 3 frozen exactly; archived: 22 of 25 by count | The takeoff layer as 3.2 defines it, on named fields. "Reproduced" never includes free text or client fields |
| P2: regression clean | Needs the run ruling | Run on a staged copy here, then on Jim's PC |
| P2: Jim reviews the first 10 conversions | Jim | Same |
| P2: no per-job scripts after go-live | Measured today: 126 | Counted from `_NG_Automation\scripts\` names in each zip or snapshot |
| P2: evaluation report | Consent given 2026-10-31; needs a provider key | Same, prepared offline first (5.7) |
| P3: takeoff equals the chat's on 10 jobs | 20 of 24 past jobs by area | On 10 live jobs, by the 5.4 score |

## 9. Rulings to take

**Jim's approvals (2026-10-31, as Sulaiman reported them):** Jim approves both:
1. **Tools into his toolkit (RULES section 8):** NG Runner's shared tools may go into `_NG_Automation`. His regression still has to pass first, as RULES section 8 says, and each tool goes in as a reviewed delivery, never by NG Runner writing into his toolkit on its own.
2. **Drawings to a model (decision 10):** the P2 reader evaluation may send elevation crops to a model. Only crops with no text span over them go (design 3.6), never a page, a name or an address. Provider and model are decisions 16 and 17, chosen by the evaluation within its USD 20 a month.


In `docs/decisions.md`'s form, each with the default adopted (2026-10-31, as Sulaiman asked for decisions and no questions), chosen by its test: does it ease Jim's work? Sulaiman can overrule any of them. Recorded there in "Phase 2's rulings and Jim's approvals".

| Decision | Default | Why |
|---|---|---|
| **R1 (new)** Run Jim's scripts on this PC | Yes, on a staged copy laid out as his tree, in a separate venv with openpyxl, pdfplumber, numpy, PIL and scipy, run with `-I` inside a Job Object, in a temp folder, never in `.local-fixtures` and never on G: | It is the only way to prove the check runner, regression and job sheet here, and it costs Jim nothing. Nothing of his runs until Sulaiman says so |
| **R2 (new)** Where the converter lives | NG Runner's code, porting `takeoff.py`'s rules with an `ast` tie to his file (4). It is offered to Jim's toolkit through RULES section 8 only after 10 of his conversions agree | Rule 4 and design 1 ("one toolkit"): his rules stay his, and a shared tool enters his kit only with his approval |
| **R3 (new)** PDF reader | pypdf, as pinned | Already in the build, and fingerprints already pin it (decision L1). pdfplumber would add a dependency to the frozen app |
| **R4 (new)** What the converter can't place | "Could not verify", naming the line, as a card when it matters; never a guess. Garage and mixed jobs stay in chat until BUILD's garage rule is ported | RULES 0 "never a guess". Pairs PAN, PAO, PAV, PAW and PBA show where the rules are silent |
| **R5 (new)** The library against RULES on 3/4 Classic White | RULES' 49×97 until Jim answers; `material_lookup` isn't used for sizes meanwhile | RULES outranks the rest, `sheet_size_check` agrees with it, and all 7 real Classic White nests carry 49×97 |
| **R6 (new)** Two sheet numbers | Carry both, named: `sheets_with_waste` (RULES 141) and area ÷ 32 (the tracker's Calc Sheets) | Measured: 37 of 37 `calc_sheets_raw` are area ÷ 32. The heads-up already names its basis |
| **R7 (new)** `run-tool` and `crosscheck` | `NGRunner.exe` treats a first argument ending `.py` as `run-tool <script>` | crosscheck starts each checker as `sys.executable <checker>.py`, and in the build that is NGRunner.exe |
| **R8 (P1b fix)** A `.opt`'s version line | `opt.py` drops one leading line of digits before the XML declaration, and reads UTF-8 with no BOM as measured. Its `ASSUMPTION`s are checked against the 20 real files | Item 3: all 20 real nests fail today |
| **Decision 9** Tracker | His two status strings stay; auto-Complete off; the ingest change approved by Jim (design 8) | Unchanged |
| **Decision 10** Consent | Before any drawing leaves the PC, including the evaluation | Unchanged |
| **Q6a, Q6b** Cost math | RULES and BUDGET as written; the converter computes no cost in P2 | Only the takeoff is in scope; cost stays in `budget.py` |
| **Q17a** Should crosscheck run every checker? | Yes, adding `sheet_size_check` and `br_cam_check --nest` where a `.opt` exists, through Jim's approval | INDEX promises it; the source runs 14 |
| **Q17b** What should a shared conversion file hold? | The named blocks the heads-up reads (P1b's "takeoff's shape"), plus `source.sha256_16` and `unplaced` | One shape for every reader |
| **Q1**, **Q19** | Waiting for P3, as decided. Q19's evidence is now here (3.5) | |
| **Decisions 4, 7a-7f, 8**; leases | Waiting for P4 | |
| **Night setup, Q8** | Waiting for P5 | |

## 10. UNVERIFIED

- **The converter:**
  - Whether pypdf is in the chat's container.
  - Why pair PAL's tape differs by 8.9 ft.
  - Whether the third description form (PBA) is a generation or a one-off.
  - How a two-colour job splits by colour beyond thickness (matched here by nearest area, which is weak).
- **The workbook:** no `.xlsx` was opened.
- **The tracker:** whether the tracker accepts a row with a blank customer; why 22 of 47 jobs' Calc Sheets aren't area ÷ 32 (later corrections, or another basis).
- **Jim's scripts inside the built `NGRunner.exe`:** run once, in a private build on Sulaiman's PC (2026-10-31): `NGRunner.exe run-tool regression.py` was clean in 2.7 s, and crosscheck on Cases C, D and F, each checker started as `NGRunner.exe <checker>.py`, gave the same verdicts as the tools' Python. Not yet run in the shipped kit or on Jim's PC. Scripts that need the tools' packages (the workbook, `parse_pulled`, the builders) can't run in the build until it carries `toolspython\`, which nothing ships yet.
- **Jim's files:**
  - Whether Job E's Tafisa on 61×109 was meant.
  - Which `Materials.dat` Optimize used for Jobs A to J.
- **The family diff:** which differences between drifted helpers are unwritten rules.
- **The reader:** whether the embedded rasters are at native pixels on every render; the design assumes it.

**Answered since (2026-10-31):**
- **Jim's scripts run on Windows,** in the tools' Python (R1), on staged copies. His regression is clean and reproduces his 3 frozen jobs (`tests/test_toolkit_real.py`); `des_cutlist` runs on all 10 jobs (`tests/test_real_designs.py`); crosscheck and its checkers run on Jobs A to J, and what each printed is pinned in `tests/test_checks_real.py`.
- **The zip didn't keep Mozaik's file times:** every file carries the time it was unpacked. So "newer" can't be measured on Jobs A to J, and the `.bak` count goes by size and contents instead.
- **The `.bak` finding, by size:** 7 of the 30 `.bak` are larger than their `.des`, and 0 of the 7 differ in a part or a product: only in annotations and numbering. The one room whose parts differ, Job G's Room 1 (groove Hide flags on 5 panels), has a smaller `.bak`, and its nest sides with the `.des` (`tests/test_guards_bak.py`). This is Q19's evidence: by size alone, RULES 81 would call 7 designs stale where no part differs, and miss the one that does.
