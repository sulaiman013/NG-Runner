# P1a: the skill change for you to approve

**Purpose:** once NG Runner files each new job's IC summary in its NG Jobs folder, your ng-ic-to-mozaik skill should take the summary from there, so you stop downloading and uploading it. This is the only skill change filing needs; the RULES lines that go with it are in rules-lines.md.

**From:** Sulaiman, **To:** Jim, **How this is used:** nothing changes until you say yes. Your chat can read the skill but not change it (`Where-Claude-works-paths.md:87`: "The skills — `ng-ic-to-mozaik`, `ic-view`. Read-only"), so you put the new text into claude.ai yourself.

Lines are cited from the copy of the skill Sulaiman received on 10/29: `SKILL - ng-ic-to-mozaik.md:10-13` means lines 10–13 of that file.

## How to apply it

1. Read the change. To change the wording, tell Sulaiman; otherwise say yes.
2. Make the change in your copy of the skill's SKILL.md: find the Now text, word for word, and put the New text in its place. If the Now text isn't there word for word, the skill has changed since 10/29: change nothing and tell Sulaiman.
3. No copy to hand? Ask your chat for the ng-ic-to-mozaik SKILL.md with this change made. It can read the skill and save the edited file for you (`Where-Claude-works-paths.md:85`: "Writing here delivers the file to you").
4. In claude.ai, replace the skill with the edited SKILL.md, where you added it. Its reference files (the two runcards and the garage spec) stay as they are.
5. Check it: in a new chat, ask to start a job whose summary NG Runner has filed. The chat should take the PDF from that job's NG Jobs folder without asking you for it.

Do it together with the RULES lines, before NG Runner starts filing. It is safe earlier: a job with no summary in NG Jobs still needs your upload, as today.

## 1. The work order: a job's name, with its PDF taken from NG Jobs

**Now**, `SKILL - ng-ic-to-mozaik.md:10-13`, the first paragraph under Mission and posture:

```text
An IC summary PDF landing in chat IS the work order. Do not wait for further instruction: identify the
job, run the pipeline below, and deliver. "Utmost verification" means every step has a **gate** — a check
that must pass before the next step runs. Never deliver a number you have not cross-checked. If a gate
fails, STOP that step, report exactly what failed, and continue any independent steps.
```

**New**, in place of those four lines:

```text
An IC summary PDF landing in chat IS the work order — and so is Jim asking to start a job by name.
**Take the PDF from NG Jobs when it is there:** NG Runner (the app on Jim's PC) files every new job's
summary as `NG Jobs\<Job>[_Gar]\<Job>[_Gar]_summary.pdf`, so never ask Jim to upload it. No file there
→ ask for the PDF, as before. A name that fits more than one NG Jobs folder → ask which, first. Do not
wait for further instruction: identify the job, run the pipeline below, and deliver. "Utmost
verification" means every step has a **gate** — a check that must pass before the next step runs.
Never deliver a number you have not cross-checked. If a gate fails, STOP that step, report exactly
what failed, and continue any independent steps.
```

Why: NG Jobs is one of the three folders your chat can open (`Where-Claude-works-paths.md:16-20`), so once NG Runner files the summary there, the upload has nothing left to do. When a name is the work order, the name alone says which job, so a name that fits two folders is asked about, never guessed. Everything from "Do not wait" on (`SKILL - ng-ic-to-mozaik.md:10-13`) is unchanged, only re-wrapped.

## Lines that stay as they are

- `SKILL - ng-ic-to-mozaik.md:3`: "or Jim asks to convert, curate, start, or check a closet or garage job". The skill already starts when you ask by name; only what it does next changes.
- `SKILL - ng-ic-to-mozaik.md:46-47`: "Read the SOP **fresh at the start of every job**". So each session reads the new RULES lines, the one on `<Job>_NGRunner.json` included, before it starts; the skill need not repeat them.
- `SKILL - ng-ic-to-mozaik.md:65`: "Parse the PDF". The PDF the work order brought, filed or uploaded.
- `SKILL-ref - runcard closet.md:5` and `SKILL-ref - garage-runcard.md:5`: "PDF parsed". The same PDF.
