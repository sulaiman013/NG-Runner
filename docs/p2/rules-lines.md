# P2: the RULES lines for you to approve

**Purpose:** your chat no longer types a closet's parts into a per-job script. A new script in your toolkit, `scripts\ng_convert.py`, reads the IC summary and writes the job's IC Conversion, the `.json` and the `.xlsx`, by code. Your RULES have to say so, or a session goes on writing `ic_conversion_<client>.py` as before. These four changes are all your docs need for it. The note that comes with the script (`NOTE-for-Jim.md`) says what it does in plain words.

**From:** Sulaiman, **To:** Jim, **How this is used:** nothing changes until you say yes. Then your chat makes the changes below exactly as written, with `ng_convert.py` added to `scripts\`, and runs your regression check before saving, as with any RULES or script change (RULES §8).

Lines are cited from the copies of your docs in the toolkit zip of 10/31, which are the same as those of 10/29: `RULES.md:745` means RULES.md line 745. Where a quote leaves out a client's name, it shows […] instead.

## How to apply them

1. Read the four changes. To change the wording, tell Sulaiman; otherwise say yes.
2. Put `ng_convert.py` in `_NG_Automation\scripts\`, as it came. Never edit it: it checks its own contents and refuses to run when changed.
3. Paste this page into your chat and ask it to apply it. For each change it finds the Now text, word for word ([…] standing for the name left out), and puts the New text in its place. If the Now text isn't there word for word, the file has changed since 10/31: it changes nothing and tells Sulaiman.
4. Before saving, it runs your regression check, as RULES §8 asks (`RULES.md:738-740`): "run `python scripts\regression.py` before committing it", then "`Regression clean` → commit." Sulaiman ran it on a copy of your toolkit with `ng_convert.py` added, and it was clean: no frozen job moved, as no script of yours imports it. If anything moves on your PC, don't commit; each moved item comes to you.
5. Look over the first 10 conversions it makes before relying on it (the design's P2 exit).

## 1. Making the IC Conversion: RULES §1, Never stop

**Now**, `RULES.md:55-57`, the first sentences of the paragraph:

```text
Input: an IC assembly/summary file. One uninterrupted run: parse → IC Conversion `.json`/`.xlsx`
into `NG Jobs\<job>\` → job folder + walls, every room → verticals, every room (garage rooms get
cabinets). No check-ins, no questions part-way.
```

**New**, in their place:

```text
Input: an IC assembly/summary file. One uninterrupted run: parse → IC Conversion `.json`/`.xlsx`
into `NG Jobs\<job>\` → job folder + walls, every room → verticals, every room (garage rooms get
cabinets). No check-ins, no questions part-way.
- **Never write a per-job script.** For a closet, the IC Conversion is
  `python scripts\ng_convert.py "NG Jobs\<Job>\<Job>_summary.pdf"`: it writes
  `<Job>_IC_Conversion.json` and `.xlsx` beside the summary. Nothing of its parts is typed.
- Every line it lists as could not verify is a `d.act()` item for Jim, never typed in or guessed.
  Exit 3 means it wrote the files with such lines; exit 0, none.
- If it refuses (exit 1), say why in one line and stop that step: never work around it. A garage or
  mixed job is refused by design: convert it as before.
```

Why: today the chat reads each summary itself and types the parts into a new script, 559 typed part lines across 15 of your 24 conversion scripts. `ng_convert.py` reads the same summary by code and checks itself against your own `takeoff.py`, `parse_pulled.py` and `classify.py` on every run. A line your rules don't settle (a part printed 0.6in thick, a hamper front, a drawer face printed at a nominal size) stays an item for you, as RULES §0 asks: "If something could not be verified, the item says so — never a guess" (`RULES.md:27`).

## 2. One home for the takeoff rules: RULES §8

**Now**, `RULES.md:745`:

```text
- **Takeoff rules live in `scripts\takeoff.py` only.** New conversions import it; never re-type them.
```

**New**, in its place:

```text
- **Takeoff rules live in `scripts\takeoff.py` only.** New conversions import it; never re-type them.
  `ng_convert.py` checks its rules against `takeoff.py` on every run, and `takeoff.py`'s own reading
  of its parts must agree; after a change to `takeoff.py` it refuses until Sulaiman sends a new one.
  Never edit `ng_convert.py`: a new one comes from Sulaiman, and goes in like any script change, with
  `regression.py` clean.
```

Why: it keeps one set of takeoff rules, yours. `ng_convert.py` carries NG Runner's converter, which mirrors `takeoff.py`; rather than a second set that could drift, it stops the moment the two disagree.

## 3. The scripts list: INDEX, The checkers

**Now**, `INDEX.md:55`:

```text
| `takeoff.py` | the one home of the takeoff rules — import it |
```

**New**, that line and one after it:

```text
| `takeoff.py` | the one home of the takeoff rules — import it |
| `ng_convert.py` | a closet's IC summary → its IC Conversion `.json` + `.xlsx`, by code, never a per-job script; checks itself against `takeoff.py`, `parse_pulled.py` and `classify.py` every run; lists, never fills in, a line it could not verify (`RULES` §1) |
```

## 4. The Rev line: INDEX

Your revision discipline asks for this (`INDEX.md:87`): "Bump this index's Rev line when RULES.md changes".

**Now**, `INDEX.md:3`, whatever Rev line is there when this is applied (7.0 on 10/31, or 7.1 once P1a's lines are in).

**New**, in its place, with the next Rev number for `<rev>` and the day it is saved for `<date>`, written like 2026-10-21:

```text
_Rev <rev> — <date>. A closet's IC Conversion comes from `scripts\ng_convert.py`, never a per-job script (RULES §1, §8). History lives in git and in `RULES_full_20261006.md`._
```

## Not needed yet

- **Garage and mixed jobs.** `ng_convert.py` refuses them until BUILD's garage buckets (`BUILD.md:127-135`) are ported, so they are converted as before. Their RULES line comes with that.
- **Costing.** It writes the takeoff, not the cost: `rates`, `costing` and `pricing` stay with `budget.py` and `emit.py` (BUDGET). Its `.json` carries every PULLED line bucketed by your `classify.py`, so they can read it.
- **The tracker.** It writes no tracker row. That comes with lock (phase 4).
- **Walls' angles, verticals and runs.** They still come from the drawings; its `.json` leaves them empty and says so.

## Lines that stay as they are

Your revision discipline says (`INDEX.md:86-87`): "If you learn something in a session that contradicts a file here, fix the file before the session ends". These lines agree with the new ones, so they stay:

- `BUILD.md:70`: "Step 2 — Takeoff (`<Job>_IC_Conversion.json` + `.xlsx` → NG Jobs folder)". Still the step; for a closet it is now change 1's command. Its rules (what counts, the snap tolerances) are the ones `ng_convert.py` follows: its snap is `SCHEMA.md`'s "Snap tolerances (Takeoff)", held to that text by a test.
- `BUDGET.md:131-133`: "**The workbook renders the JSON; it is not the truth.**" `ng_convert.py` writes the `.json` first and lays the workbook out from it, in your shared writer's four sheets, and checks the workbook against the `.json` before either is saved.
- `RULES.md:105-146`, sheet size: "READ THE MATERIAL LIBRARY, never a table". `ng_convert.py` follows RULES' own table until you answer which is right for 3/4 Classic White (the library says 61x109, RULES and all 7 real nests 49x97); a colour the table has no size for reads "could not verify", never a guess.
- `RULES.md:148-166`, colours named by their Tafisa name. Its workbook names a Tafisa colour "Tafisa (IC SU)", by IC code, as NG Runner has no Tafisa names of its own; the chat's tracker rows keep the Tafisa names.
