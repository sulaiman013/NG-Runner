# NG Runner

Automates Jim's NG pipeline: an Inspired Closets design PDF becomes a verified Mozaik CNC job, with Jim deciding every step that needs judgment.

## Language

### Jobs and documents

**Job**:
One customer project going through the NG program, identified by its Studio project ID and named by its Salesforce Opportunity Name (`Name_MMDDYY`), frozen at intake.
_Avoid_: project, order, design

**Studio project ID**:
The number Studio and Salesforce share for a job; the job's identity, which survives renames.
_Avoid_: job number, SF id

**IC summary**:
The Studio Install Report PDF for a job; the input to everything. "The bible" — its dimensions are never questioned.
_Avoid_: assembly report (ambiguous), summary file, design PDF

**Assembly print**:
Mozaik's own printed assembly sheet for a built job. Not the IC summary.

**Render**:
One download of a job's IC summary from Studio; a job can have several, and one governs. Not Render the hosting service, which runs the online Assembly Reports (the code and messages call that "the online Assembly Reports").
_Avoid_: version, copy

**Fab row**:
A row Barbara adds to the NG Fab Schedule's Sheet1; the trigger for a new job.

**Room word**:
A word such as closet, garage or pantry that a Fab row or an Opportunity Name may carry beside the surname; it never names the job. A row with any other extra word (a first name, a second surname, a note) is asked about, never auto-matched.

**Branch**:
Whether a job is built as a closet or as a garage. The pipeline marks a garage job by adding `_Gar` to its NG Jobs folder and file names; it is the same job, never a run of it or another job.
_Avoid_: garage variant, garage job (as if it were a separate job)

**Run (of a job)**:
A later pass at a job that already exists, named by adding remake, revN, redoN, -N, pickup, top or a GB (go-back) date after its date. It belongs to the parent job, unless it has a Studio project ID of its own (a separate "-2" sale, for example): then it is a job of its own.
_Avoid_: sub-job, version; "run" alone (see Pipeline run)

**Pipeline run**:
One pass of the pipeline over one job, by Jim's chat or by NG Runner; Jim's docs call it "the run". The night run is NG Runner's pass, 1:30–4:30 AM, over every job that needs work.
_Avoid_: "run" alone (see Run (of a job))

**Remake / revision**:
A remake re-cuts parts of a job; a revision (revN) is a new design version. Both are runs of the parent job.

**Variant**:
The suffix after the date in a job's or a file's name: a branch (`_Gar`), a run's suffix, or both. It says which branch or run a name is, never which job: only the Studio project ID says that.
_Avoid_: sub-job, suffix job; Jim's "WM variant", which is a panel product (Thru, Left or Right)

**Alias**:
A later name of a job, after its name was frozen at intake (a rename in Studio or Salesforce); the job's identity stays its Studio project ID.
_Avoid_: new name, rename

### Inside an IC summary

**Design**:
One Studio design inside an IC summary, listed on page 1 by name and category; one IC summary can hold several. A design is not a job.
_Avoid_: room (a Mozaik word), project

**Elevation page**:
A page of an IC summary that shows a design's walls as face-on drawings (elevations).
_Avoid_: drawing page

**WALL/AREA table**:
The table headed WALL/AREA NAME (W X H) that lists each wall or area of a design with its width and height in inches.

**PULLED list**:
The IC summary's consolidated parts list for the whole job, headed PULLED. Its columns follow the report layout: two lists side by side, quantity first, in landscape; one list, item number first, in portrait.
_Avoid_: BOM, parts list (ambiguous)

**Report layout**:
Landscape or portrait: the two page layouts Studio renders an IC summary in, which Jim's BUILD.md calls "two document generations". All renders of one job use the same layout, and a summary already in the job's folder decides which.
_Avoid_: orientation (Assembly Reports' setting), generation, version

### Mozaik work

**Room**:
One `Room#.des` file in a Mozaik job; Room0 holds loose parts.

**Vertical**:
A vertical panel: FM (floor-mounted) or WM (wall-mounted, hung from the top line).
_Avoid_: upright, side panel

**Section**:
A cabinet or shelf unit between two verticals; always inserted by Jim by hand.

**Bridge (BR)**:
A single top piece spanning between verticals; never counts as a section.

**Top line (T)**:
The one height every WM vertical in a room hangs from.

**Nest**:
Mozaik's layout of parts on sheets for one material (`<Material>.opt`); its programs are the TAP files that get cut.

### NG Runner terms

**Crosscheck**:
Running every checker over every room of a job; the result is a numbered fix list or `Nothing to fix — matches IC summary.`

**Lock design / Finish lock / Lock**:
Jim's lock clicks. Lock design records the job with calculated quantities before nesting; Finish lock completes a material once its nest passes; Lock does both when the nest already passed.

**DO NOT CUT / NOT CHECKED / Nest checked**:
The three states a nested material can show; never silence, never "OK to cut".

**Clearance**:
Jim's click saying a program past its sheet size is on a sheet he added on purpose. It is bound to that program's hash, so a re-post asks again, and it never touches doubled holes.

**Card**:
A question for Jim with fixed choices and a recorded verbatim answer. A choice that names a job holds its Studio project ID, so an answer means that one job whenever it comes.

**Digest**:
The single 6:30 AM message summarising every job touched overnight.

**Settle**:
Jim marking a finding as "not a problem", scoped to this job or to all jobs under a stated condition.

**Lease**:
NG Runner's marker that it is handling a job, so Jim's chat sessions leave it alone; it expires.

**Chokepoint**:
`commit_write()`, the only code allowed to write shared files.
_Avoid_: writer, saver

**Owned file**:
A file whose content is still exactly what NG Runner last wrote there (its hash matches); the only kind of existing file NG Runner overwrites.
_Avoid_: our file, managed file

**Control folder**:
The private Drive folder, outside the CNC operations tree, through which NG Runner and its Apps Script exchange signed files.
_Avoid_: shared folder, inbox

**Fab export**:
The Apps Script's copy of Sheet1 in the control folder, rewritten every 5 minutes; the only way NG Runner sees the NG Fab Schedule.
_Avoid_: sheet dump, snapshot

**Shadow mode**:
NG Runner running for real on Jim's PC with nothing reaching anyone else: what it would file goes to the control folder instead of NG Jobs, and its messages go to Sulaiman, marked SHADOW. The shadow run is the test window spent in it.
_Avoid_: dry run, test mode

**Observed job**:
A job whose NG Jobs folder was there when NG Runner was installed; NG Runner watches and checks it, and writes nothing into it.
_Avoid_: existing job, old job (`NG Jobs\Old` is a folder)

**DEV item**:
A problem for Sulaiman, in a lane Jim doesn't see. One that holds up a job reaches Jim after 48 hours, with the way back to chat; an urgent one turns the tray red.
_Avoid_: ticket, bug

**Could not verify / Could not check**:
"Could not verify" is a value NG Runner could not confirm, reported as such and never guessed. "Could not check" is a Setup Check item that could not run, always shown with its reason.
_Avoid_: unknown, N/A; NOT CHECKED (that is a nest state)

**Q1–Q19**:
The 19 places where Jim's docs disagree, each asked as a question only Jim settles. A letter (Q6a, Q6b) splits one Q into single-idea questions that together settle it.
_Avoid_: D1–D4 (those are risks in the design's register)
