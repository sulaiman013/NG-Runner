# P1a: the RULES lines for you to approve

**Purpose:** NG Runner is about to file each new job's IC summary in its NG Jobs folder by itself. Your RULES have to say so; otherwise a chat session asks you to upload a summary that is already there, or saves its own copy over NG Runner's. These three changes are all your docs need for filing, apart from the skill (skill-update.md).

**From:** Sulaiman, **To:** Jim, **How this is used:** nothing changes until you say yes. Then your chat makes the changes below exactly as written and runs your regression check before saving, as with any RULES edit (RULES §8).

Lines are cited from the copies of your docs Sulaiman received on 10/29: `RULES.md:87-88` means RULES.md lines 87–88. Where a quote leaves out a client's name, it shows […] instead.

## How to apply them

1. Read the three changes. To change the wording, tell Sulaiman; otherwise say yes.
2. Paste this page into your chat and ask it to apply it. For each change it finds the Now text, word for word ([…] standing for the name left out), and puts the New text in its place. If the Now text isn't there word for word, the file has changed since 10/29: it changes nothing and tells Sulaiman.
3. Before saving, it runs your regression check, as RULES §8 asks (`RULES.md:738-741`): "run `python scripts\regression.py` before committing it", then "`Regression clean` → commit." These changes touch no script, checker or takeoff, so expect `Regression clean`; if anything moves, it doesn't commit, and each moved item comes to you to approve or reject, as with any RULES edit.
4. Do it before NG Runner starts filing on your PC. It is safe earlier: until NG Runner files a job, nothing here changes how your chat works on it.

## 1. Where the summary comes from: RULES §1, The IC summary is the bible

**Now**, `RULES.md:87-88`:

```text
Archive the PDF to `NG Jobs\<Job>\<Job>_summary.pdf` before anything else — every later correction
is a re-derivation of it.
```

**New**, in place of those two lines:

```text
The PDF is archived at `NG Jobs\<Job>\<Job>_summary.pdf` before anything else — every later
correction is a re-derivation of it.
- **NG Runner** (the app on Jim's PC) files it there for each new job on the NG Fab Schedule, once
  two downloads 10 minutes apart agree and both pass its check (right job, complete). Work from
  that copy.
- Never replace a summary NG Runner filed except on Jim's word: any other summary for the same job
  (an upload, a newer download) is an item, never a swap.
- No summary there yet (NG Runner down, or a job it has not filed) → work as before: archive the
  PDF Jim uploads.
```

Why: NG Runner now does the archiving, so a session works from its copy instead of asking for an upload or saving over it. A different summary is yours to settle, never the session's. The last line keeps today's way for any job NG Runner hasn't filed, including while it is down.

## 2. NG Runner's record of the job: RULES §1, Safety

**Now**, `RULES.md:83`, the last Safety line:

```text
- A denied permission popup means STOP and ask in words — never re-fire.
```

**New**, added after it:

```text
- **Read `NG Jobs\<Job>\<Job>_NGRunner.json` before working a job or answering about it**, when
  it is there: NG Runner's record of what it did on the job and what it still has open. Never
  write or delete a `<Job>_NGRunner*` file.
- Its `nests` say what NG Runner last read of each material, and its `preflight` what to look at
  before a lock. Neither says a job may be cut or locked: only you and Jim decide that.
- Its `takeoff` is NG Runner's own takeoff of the filed summary (an estimate: the nest decides).
  When its `complete` is false (lines it could not verify, or a job it leaves to chat), the order
  heads-up Barbara and Will get is your IC Conversion JSON's list, sent whole and marked
  "From the chat's takeoff". So keep writing that JSON as before.
```

Why: a session can't see NG Runner, just as it can't see another session's chat (`INDEX.md:81-82`: "a session that wasn't told something in its own chat still needs to get it right"), so NG Runner tells it through this file. NG Runner only ever overwrites a file it wrote and nobody has changed since, so a session that edited one would stop NG Runner keeping it up to date.

The `takeoff` block came with schema 3 of that file, and is null until NG Runner has made the takeoff. It covers closets only: a garage or mixed job's takeoff stays in your chat. A heads-up marked "From the chat's takeoff" carries your chat's list alone: NG Runner never mixes the two, and never sends part of a list.

## 3. The Rev line: INDEX

Your revision discipline asks for this (`INDEX.md:87`): "Bump this index's Rev line when RULES.md changes".

**Now**, `INDEX.md:3`, with a client's name left out:

```text
_Rev 7.0 — 2026-10-21. Recompacted: the changelog that had accumulated on this line is gone, as are the incident narratives in RULES §BR and §Construction parameters and the […] post-mortem in FAILURES. No rule changed — only the log came out. History lives in git and in `RULES_full_20261006.md`._
```

**New**, in its place, with the day it is saved for `<date>`, written like 2026-10-21:

```text
_Rev 7.1 — <date>. NG Runner (the app on Jim's PC) files each new job's IC summary; chat reads `<Job>_NGRunner.json` before working a job (RULES §1). History lives in git and in `RULES_full_20261006.md`._
```

## Not needed yet

Item 15 of the questionnaire asked about two RULES lines. Filing needs only the first, which is change 2 above without the settled list.

- **The settled list** starts with checks (phase 3), when you can mark a finding as not a problem for all jobs. Its line comes then.
- **The lease line** (chat never writes a job NG Runner is working on) comes with lock and fixes (phase 4). Filing doesn't need it: NG Runner only adds new files of its own to a job folder (the summary and its two `<Job>_NGRunner` files) and never writes over a file your chat made, and with changes 1 and 2 your chat never writes over NG Runner's.

## Lines that stay as they are

Your revision discipline says (`INDEX.md:86-87`): "If you learn something in a session that contradicts a file here, fix the file before the session ends". These lines agree with the new ones, so they stay:

- `RULES.md:55`: "Input: an IC assembly/summary file." Still true, wherever the PDF comes from.
- `BUILD.md:35`: "**First action: archive the IC summary PDF** into `NG Jobs\<Job>\` per RULES". That now means change 1; BUILD says of itself (`BUILD.md:3-4`): "it does not restate the settled rules from RULES.md".
- `BUDGET.md:144-145`: "Save the IC summary PDF (and any Product Summary PDFs) into `NG Jobs\<Job>\` at Step 1, before anything else." A job NG Runner filed already has it there, and BUDGET keeps RULES in force (`BUDGET.md:4`: "`RULES.md` still applies in full").
- `Where-Claude-works-paths.md:50`: "The IC summary, archived here first". Still true: NG Runner now archives it first.
