# NG Runner: questions for Jim

**Purpose:** NG Runner is about to start running parts of your NG pipeline by itself. Your answers decide what it may do on its own, which rule it follows where your docs disagree, and what has to be settled before each phase goes live. Phase 1 (filing summaries) can't start until sections 1–4 are answered.

**From:** Sulaiman, **To:** Jim, **How your answers will be used:** each answer is kept word for word with its date and becomes a setting or rule NG Runner follows. Where an answer settles a disagreement in your docs, Sulaiman drafts the RULES.md line and you accept it through your regression check (RULES §8). Nothing in your toolkit changes without your word.

## Context

NG Runner is a small Windows app for your PC. When Barbara adds a row to the NG Fab Schedule, it fetches that job's IC summary (the Studio PDF you call the assembly report), checks it is complete and is the right job, and files it in the job's NG Jobs folder, so downloading and uploading stop. Later phases add, one at a time and each with your OK: the nest guard (DO NOT CUT or NOT CHECKED, which George gets too), a list of what to look at before each lock, an order heads-up for Barbara and Will, your toolkit's tools shared with your chat so it stops writing a script for every job, a crosscheck when you say sections are done, and lock from a button. Sections, trim, nesting, posting and every lock stay yours. Code makes every call, nothing is guessed, and anything it can't confirm says "could not verify". The plan Sulaiman commits to goes as far as checks; lock and fixes come too if time allows. Building walls and verticals overnight is a separate go/no-go after a test on your past jobs, and it may not fit the time available. Your docs disagree with each other in 19 places, numbered Q1–Q19 below. RULES.md outranks the rest, so the recommended answer usually follows RULES where it speaks (Q1 explains where it doesn't) and your newest dated rule where it is silent.

## How to answer

About 50–65 minutes in all, of which sections 1–4 take about 30–40. Write on the line that starts with ">": "yes" accepts the recommendation, and the one question with no recommendation (3b) takes a plain yes or no; anything else, in your own words. Partial answers are fine, and "don't know" is useful: flag anything you're unsure of rather than skipping it. Sections 1–4 are needed now, ideally before our 20-minute call. Sections 5–10 say when they are needed, so skip them today if you're short on time. Your answers may name jobs, so send the file back through the private Drive folder from the access list, not by email.

Quotes cite your docs by name and line, from the copies Sulaiman received on 10/29. "RULES 580" means RULES.md line 580. "Skill" is SKILL - ng-ic-to-mozaik.md; "closet runcard", "garage runcard" and "garage spec" are the SKILL-ref files; "10-06 archive" is ARCHIVE - RULES_full_20261006.md and "pre-10-11 archive" is ARCHIVE - RULES_before_20261011.md; "the call" is our 10/19 kickoff call.

## 1. Getting started (needed now)

### 1. How many hours a week should go to NG Runner?

_Why this matters: it sets every date below. Firefighting (the maintenance side) stays separate and comes first._

The dates below assume every one of these hours goes to building. Looking after each phase once it is live takes another 2–4 hours a week, so the weekly total grows as phases go live.

**Recommended:** half time: 20 hours a week of building, for filing in about 6 weeks, the nest guard by about week 10 and checks by about week 24 (at quarter time, 10 hours, each date roughly doubles). Upkeep comes on top of the 20: about 22–24 hours a week in all once filing is live, and about 28–36 once checks are live (four phases by then: filing, the nest guard, the shared tools and checks). If upkeep has to come out of the 20 instead, the total stays at 20 and every date after filing slips; reply "yes" to accept, or give your number and say which.

> 

### 2. Should a new row on the NG Fab Schedule start a job?

NG Runner only reads Sheet1; it never edits it. You can also start a job yourself with an **Add job** button.

**Recommended:** yes: a new Sheet1 row, plus the Add job button; reply "yes" to accept.

> 

### 3a. Which Salesforce stages count when Barbara's row names a customer by surname?

When a row gives a surname rather than the full job name, NG Runner files the job only if every word of the row is the surname, a room word such as closet or garage, or the job date; exactly one Opportunity has that surname, is owned by the row's designer, has no NG job yet and is at a stage that counts; and that designer has no newer Opportunity of the same surname (lost ones aside). Otherwise it asks you which job the row is. The stages Assembly Reports sees are Propose, Design, Incomplete, Ready to Order, Ready to Install, Complete, Lost and Dead Lead.

_Why this matters: a stage that counts by mistake can file a summary to the wrong job; a stage left out only means one more question for you._

**Recommended:** Ready to Order and Ready to Install (sold, not yet installed); reply "yes" to accept, or name the stages.

> 

### 3b. Do remakes of installed jobs come through the Fab Schedule as new rows?

An installed job's Opportunity is at Complete. NG Runner never files a Complete job from a surname alone: every job installed before NG Runner would then count, and a new sale could be filed to an older job of the same family. So a row for a remake of an installed job comes to you as a one-tap question, with that job offered first.

**No recommendation:** only you know this; reply "yes" or "no". It tells Sulaiman how often to expect those questions.

> 

### 4. Which report layout should NG Runner download for new jobs: landscape or portrait?

Studio offers every IC summary in two layouts, landscape and portrait. Assembly Reports downloads landscape unless someone picks portrait. Of the 27 summaries in your current NG Jobs folders, 15 are landscape and 12 portrait.

- BUILD 49: "TWO document generations exist — detect, never assume". The two generations are these two layouts: all 15 landscape summaries have the two-column PULLED list, and all 12 portrait ones the one-column list.

_Why this matters: NG Runner downloads every job at least twice and compares the downloads, so all downloads of one job must use the same layout._

**Recommended:** landscape, with each job kept on the layout it started with; reply "yes" to accept, or say "portrait".

> 

### 5. Can your PC stay on and signed in overnight, with Mozaik closed?

Locked is fine. The night run is 1:30–4:30 AM, and NG Runner never writes while Mozaik is running. If the PC has to sleep, NG Runner runs at first wake instead and the morning message says so.

**Recommended:** yes: on, signed in and locked, with Mozaik closed at night; reply "yes" to accept, or "it sleeps".

> 

### 6. May Sulaiman set Windows up for the night run?

In one remote session during your night: the PC wakes itself at 1:25 AM; Windows Update restarts it only between 5 AM and 11 AM; the power button (and the lid, on a laptop) does nothing while plugged in; the folders NG Runner watches stay downloaded on the PC; and one test restart proves NG Runner comes back on its own.

Between 5 and 11 AM, Windows may restart for an update without asking, so a Mozaik job left open and unsaved then could lose its changes. Windows keeps at least 6 hours a day open for these restarts, and they must stay clear of the 1:30–4:30 AM run. If you are often at the PC before 11 AM, give the time you usually start, and Sulaiman will look for a window that misses your mornings too.

**Recommended:** yes, all five; reply "yes" to accept, or name any you don't want, and give your start time if it is before 11 AM.

> 

### 7. May Sulaiman reach your PC with Chrome Remote Desktop?

Unattended, with a PIN, so he can install, update and fix NG Runner while you sleep. It is free and you can remove it any time. It is set up on the 20-minute call (see the access list).

**Recommended:** yes; reply "yes" to accept.

> 

### 8. Which one phone channel should NG Runner use?

It carries the digest at 6:30 AM (one message covering every job touched overnight) and the urgent items: DO NOT CUT, a fix Mozaik undid, a tracker failure, a night with no run.

**Recommended:** a Slack direct message if Slack already alerts on your phone, otherwise Pushover (a small alerts app); reply "yes" to accept, or say which.

> 

### 9. Should the phone stay quiet from 10 PM to 6 AM, except DO NOT CUT?

**Recommended:** yes; reply "yes" to accept.

> 

### 10. If Smart App Control is on, should NG Runner carry ICLA's digital signature?

Smart App Control is a Windows setting that blocks programs without a digital signature (a publisher stamp Windows checks before it runs a program); the Setup Check shows whether it is on. If it is, either NG Runner carries ICLA's digital signature (1–20 business days to get, and it needs an email address on ICLA's own website domain, otherwise a paid certificate), or you turn Smart App Control off, which may be permanent. If it is off, the digital signature can wait.

**Recommended:** give NG Runner ICLA's digital signature; reply "yes" to accept.

> 

### 11. Should ICLA own every account NG Runner uses?

A Google account just for NG Runner (alarms, emails, reading the schedule), Pushover, the drawing reader's account (section 7), GitHub (where NG Runner's code is kept) and the digital signature (item 10). Sulaiman is added as a member. Keys go in ICLA's password manager, plus a sealed copy of the logins that only you hold, so ICLA can always get into its own accounts. No client file ever goes into GitHub.

**Recommended:** yes; reply "yes" to accept.

> 

### 12. Is it OK for these services to receive the following?

- Pushover (if it is chosen in item 8): job names and dates.
- Google and Slack: ICLA's own accounts, for alarms, emails and reading the schedule.
- Chrome Remote Desktop (Google): what is on your screen during a remote session.
- GitHub: code only, never client files.

The drawing reader is asked separately (item 25).

**Recommended:** yes; reply "yes" to accept.

> 

### 13. Who should alarms name when Sulaiman is away?

**Recommended:** George, who runs Mozaik and already gets the shop emails (DO NOT CUT, NOT CHECKED); reply "yes" to accept, or give a name.

> 

### 14. Should we line up a backup developer?

**Recommended:** yes, in time, with no name needed now: the runbook that ships with the first release is written so another developer could take over; reply "yes" to accept.

> 

### 15. May two RULES lines stop your chat and NG Runner working the same job at once?

1. Before answering about a job, chat reads NG Runner's notes for that job and the settled list.
2. Chat never writes a job that has an active lease (NG Runner's marker that it is working that job). A lease lapses by itself if NG Runner stops (it renews every 2 hours while it works), and ends at once when you pause NG Runner or press **Release all jobs to chat**.

Sulaiman drafts both lines and the matching skill update; you accept them through your regression check and paste the skill into claude.ai.

**Recommended:** yes; reply "yes" to accept.

> 

### 16. Will you answer two questions once a week?

Hours you spent on NG that week, and the worst time-waster. Two weeks of answers before anything ships give the baseline the plan is measured against.

**Recommended:** yes; reply "yes" to accept.

> 

## 2. Where your docs disagree: how a run behaves (needed now)

### Q14. Does a run ever stop to ask?

- RULES 3-4, 57: "the rule wins — flag it in the delivery, never stop to ask"; "No check-ins, no questions part-way."
- Skill 13, 26-27, 95: "STOP that step"; if closet or garage "is at all ambiguous, ask Jim before doing anything else"; the shelf cross-check is a "hard STOP".

**Recommended (RULES):** the night's work never stops. Questions come afterwards as cards (a question with buttons). Only two questions make a job wait, and only that one job: "closet or garage?" when it is unclear (How-we-work 37-38), and NG Runner's own "which job is this row?" when a Fab Schedule row fits no job or several; reply "yes" to accept.

> 

### Q18b. Who opens Mozaik?

- The call, 01:27:36: "it automatically opens up the job, it opens up the job and puts all this information in here."
- RULES 79: "Never write a job folder while Mozaik is running at all".

**Recommended:** NG Runner never opens or drives Mozaik and never writes while it is running. It writes a new job's folder while Mozaik is closed, the job then shows in Mozaik's job list, and you open it, as with chat today (chat writes the files; it doesn't run Mozaik); reply "yes" to accept.

> 

### Q18a. What goes in the message you get?

- RULES 11, 19-20: "Jim's work list and nothing else"; "No intro, no summary, no closing line, no file list"; a clean job is the one line "Nothing to fix — matches IC summary."
- Skill 189-199: an eight-part delivery (summary line, open questions, material estimate, files placed, verification report, saw list, anomalies, next actions).

**Recommended (RULES):** your work list only; the eight-part delivery comes out of the skill in the same clean-up as Q7; reply "yes" to accept.

> 

### Q7. May the dead rules come out of the skill and the closet runcard?

- RULES 5, 221, 238-239: "This file outranks the skill and the runcards."; "Worksurfaces are always included."; "There is no saw list ... Any 'RIP category / emit a SAW LIST' instruction elsewhere is dead."
- Skill 73-74, 101: "WS counters are EXCLUDED"; "Counters/countertops: disregarded." Skill 104-105 and 197 still ask for a saw list, and closet runcard 12 and 36-37 say the same.

_Why this matters: the skill loads itself on every summary (skill 3), so these lines keep bringing back things you settled, like the saw list._

**Recommended:** yes. Sulaiman drafts a corrected skill and closet runcard without those lines, and you paste the skill into claude.ai; reply "yes" to accept.

> 

### Q11. How is "out of date" decided for nests, programs and labels?

- RULES 699, 709-711: "Read the outputs, not the clock"; compare the .sbk with the .des first, because "Mozaik re-saves annotation positions on close."
- Skill 175-176, closet runcard 44 and FAILURES 138-140: every output must be newer than every room file, by timestamp.

**Recommended (RULES):** by content, never by the clock; reply "yes" to accept.

> 

## 3. Where your docs disagree: sheets, colours and the tracker (needed now)

### Q3. Which sheet size must a nest be on?

- RULES 105-106, 714-715: "Every colour has its own sheet size, and the library is the only source"; the check "branches on GAUGE first, then colour", and 1/4 Egger is 61x109 "in EVERY colour, Classic White included".
- SCHEMA 184: 49×97 "for closets AND garages, no exceptions" (garage spec 56 and skill 130 agree).
- BUILD 554-558 and FAILURES 183-184: by colour only ("Branch on the COLOUR, not the brand"), which fails a good 1/4 Classic White nest on 61×109.
- The 1/4 rule also flipped. Pre-10-11 archive 112 (10/07): "ALL 1/4 IS WHITE VINYL AT 49×97"; RULES 128-129 (10/20): the back is in the job's own colour.

**Recommended (RULES):** each material's size comes from Mozaik's material library, checked by thickness first, then colour: 3/4 Classic White 49×97, 3/4 Egger Black 61×109, 1/4 Egger 61×109 in every colour, and 1/4 backs in the job's own colour; reply "yes" to accept.

> 

### Q4. What is IC "Charcoal"?

- RULES 115, 123, 160-161: IC "Charcoal" is "Egger Black U999 ST19 Woodgrain" (3/4 on 61×109).
- SCHEMA 72-74: Charcoal is listed among the Tafisa colours.
- BUILD 603-604: "Charcoal and Egger Black U999 ST19 Woodgrain are separate SKUs at separate prices"; BUDGET 319 prices Charcoal at $59 a sheet.

_Why this matters: it decides the brand, the template, the sheet size and the tracker row._

**Recommended (RULES):** IC "Charcoal" is built, nested and tracked as Egger Black U999 ST19 Woodgrain; reply "yes" to accept.

> 

### Q10. How thick is a closet worksurface?

- RULES 221: "Closet WS = 3/4″ in the room's own colour."
- BUILD 77-78 and How-we-work 228: "always 1″"; FAILURES 161: "always built at 1″".

**Recommended (RULES):** 3/4 in the room's own colour; reply "yes" to accept.

> 

### Q9. Are rail covers ever checked?

- RULES 245-247 (your 10/19 rule): "RAIL COVERS ARE DISREGARDED, PERIOD ... Not by size, not by count, not as a missing part".
- RULES 662: "never a SIZE finding, but the COUNT is checked."
- BUILD 80 (9/01) and RULES 224-225: BUILD says to "bucket it on its own thickness"; RULES says it goes "in the room's 3/4 bucket" and "Never open a 0.4in bucket."

**Recommended (RULES, newest 10/19):** never in a check or a work list, by size or by count; the takeoff still buys them as 3/4 in the room's 3/4 bucket, and the count line (RULES 662-665) comes out; reply "yes" to accept.

> 

### Q12. Where do order call-outs go?

- Skill 76-77 and How-we-work 47-48: "Deliver immediately in chat ... (ordering must run ahead of availability)."
- BUILD 102-105: "Record, don't chat": sheets, tape and the paste block go in the IC Conversion workbook. RULES 11 and 22-24: your message carries only what you must act on.

**Recommended (RULES):** not in your work list; they are recorded in the IC Conversion workbook, and Barbara and Will get them by email from the guards phase (item 17); reply "yes" to accept.

> 

### Q13. When is the tracker written?

- RULES 59, 727: "Tracker is not part of the run."; "Only once Jim says the job is locked is the Materials Tracker entry finalised."
- BUILD 311, 613: "Touch 1 — Takeoff ... APPEND the row structure right after the takeoff"; BUILD 309 and INDEX 73 point at Lux_Materials_Tracker.xlsx, which BUILD 613 calls "a FROZEN copy - never edit it."

**Recommended (RULES):** no tracker rows during a build run; rows go into the live Google Sheet tracker only from your lock (how Lock design fits in is item 32); the .xlsx is never written; reply "yes" to accept.

> 

## 4. Where your docs disagree: building and checking (needed now)

### Q2. Where is a room's WM top line?

- RULES 330-339 (your 10/18 rule): one top line per room; "A floor-mount panel reaching up beside the WM run → T = that FM panel's top"; 83.9″ (2132 mm) "is the default, NOT an override".
- BUILD 244, 294: "top line is 2132"; "T = 2132, always."
- SCHEMA 90: "Elev = wall height − panel height".

**Recommended (RULES):** one top line per room: the top of the FM panel beside the WM run; with no FM panel there, the WM run's own top; a top line within 1″ of 83.9″ (2132 mm) means 83.9″; reply "yes" to accept.

> 

### Q5. Which WM Panel 48 products are legal?

- RULES 377-387: six (Thru, Left and Right, each square or radius), and "All six live in templates" (your templates folder).
- SCHEMA 98, 120, 156-157: "The four legal WM product names, exactly" (no plain Left or Right); "An unknown name is a hard fail"; and Left, Right and Right Radius are listed as "Missing".

_Why this matters: a check written from SCHEMA would fail legal panels; a recent job uses WM Panel 48 Right Radius._

**Recommended (RULES):** all six are legal and all six templates are in your templates folder; "WM Panel 48 Thru Rad" is not a product; reply "yes" to accept.

> 

### Q15a. Are wall corners always 90°?

- Skill 86 and How-we-work 56: "All corners 90°".
- BUILD 214-216: "Non-orthogonal walls exist"; recover the angle "from a geometric constraint ... never from pixels".

**Recommended (BUILD; RULES is silent and neither line is dated):** 90° unless the plan shows an angled wall; its angle then comes from the table's wall lengths and how the other walls line up, never from the picture, and it is flagged every time; reply "yes" to accept.

> 

### Q15b. Does the panel check still need the Mozaik MM report?

- BUILD 12, 357: "Intake the Mozaik summary", then check.
- RULES 651-653: "No MM report needed, and nothing to re-upload"; the cutlist is read from the room files with the job closed.

**Recommended (RULES):** no MM report needed; reply "yes" to accept.

> 

### Q15c. What tolerance does the panel check use?

- RULES 666: "Tolerance 1.5mm, doors 3mm."
- BUILD 376-382 and RULES 473: BUILD's table is verticals 1.5 mm, doors 3 mm, shelves 12 mm, back panels by size only, and RULES 473 itself mentions "the 12mm shelf tolerance".

**Recommended (RULES 666 read with RULES 473):** verticals 1/16″ (1.5 mm), doors 1/8″ (3 mm), shelves 15/32″ (12 mm), back panels by size only; reply "yes" to accept.

> 

### Q16a. Which joint fastener settings do closet jobs carry?

- RULES 527: "JointFast1 = 9 (_PAC Titus (Base-Tall) V2, DI 9.525) and JointFast2 = 1 (None)".
- Garage spec 19: "JointFast1/2 = 37/37 (closets 9/14)".

**Recommended (RULES):** Joint Fastener 1 = 9 (Titus Base-Tall) and Joint Fastener 2 = 1 (None) on closet jobs, with upper sections keeping your 10/21 custom fastener (RULES 604-611); the garage spec's "closets 9/14" note gets corrected; reply "yes" to accept.

> 

### Q16b. Is the garage cabinet builder kept?

- BUILD 110-120: "Garage cabinets — the builder EXISTS, use it"; skipping it skipped "the step he expects on every garage" (your 9/28 correction).
- Handoff brief 84: the scripts whose names start with run_ (the builder is one of them) are "One-offs. Archive, do not port".

**Recommended (BUILD, your 9/28 rule; the brief isn't one of your rules):** keep the garage cabinet builder as the shared garage builder, and archive only the true one-offs; reply "yes" to accept.

> 

### Q16c. Where do a new job folder's settings files come from?

- RULES 630-632: the closet parameters come from Lux Closet Template Claude "and every other settings file from" one August job; "That pointer is the whole mechanism".
- BUILD 144: "Build from the TEMPLATE jobs, enumerating their files".

**Recommended (RULES):** keep it as RULES describes, and never repoint the parameters to that August job ("repointing it drags nailers back into every new job", RULES 632); reply "yes" to accept.

> 

## 5. Needed before the nest guard goes live (skip today if short on time)

### 17. Which jobs should the order heads-up flag?

From the guards phase, Barbara and Will get one email per new job: sheets by colour and thickness, tape by width and colour, labelled "takeoff estimate; the nest decides", and never with the address.

**Recommended:** any colour other than Classic White, every substitution (for example a 1-1/8″ Tafisa vertical built at 3/4, RULES 219-220), and any job whose IC summary total is $25,000 or more (your example on the call, 01:24:11); reply "yes" to accept, or give your own dollar line.

> 

### 18. May Sulaiman offer Ron, Will and Mary a team tab?

A view of where each job stands. Nobody can change anything from it, and it is built only if they want it.

**Recommended:** yes; reply "yes" to accept.

> 

### 19. Can you give 30 minutes for a recorded Mozaik test?

On a test job, Sulaiman watches the files while you open, save, close, recalculate without saving, and nest without saving (and George opens it from his PC, if he can). It settles when NG Runner may safely write, and whether a nest is taken from the saved job (BUILD 546: "Optimize reads the SAVED job") or from what is open on screen.

**Recommended:** yes; reply "yes" to accept.

> 

### 20. What should NG Runner do with a job folder that has neither a lock file nor a closed marker?

- RULES 78: "MozaikJob.UNS = closed = safe."
- 10-06 archive 345: "a missing .UNS means the job is open."
- FAILURES 18-22: a job with neither file opened and closed fine. In a July copy, 29 of 79 job folders had neither.

**Recommended:** decide after the Mozaik test (item 19); until then, fixes for those jobs come to you as steps to do in Mozaik, and NG Runner writes nothing into them; reply "yes" to accept.

> 

## 6. Needed before the shared tools go into your chat

### 21. Which status words should the tracker use?

- BUILD 572-574: "The moment the .opt files exist ... flip the row Status to Complete."
- RULES 727: "Only once Jim says the job is locked is the Materials Tracker entry finalised." Today the tracker shows 7 different status words.

**Recommended:** only the two it already uses: "In progress" (with its box symbol) until you lock, and "LOCKED" (with its padlock) from your lock; never "Complete"; reply "yes" to accept.

> 

### 22. Should the tracker stop marking rows Complete by itself?

Its inbox script marks rows Complete on its own when nest numbers arrive (Tracker_Ingest lines 201-202 and 277). One job showed Complete while a material row was missing.

**Recommended:** yes, turn it off; reply "yes" to accept.

> 

### 23. May the tracker inbox change so a batch can never be applied twice?

- BUILD 632-640: a batch that ran twice left a job "permanently un-lockable until the duplicates are removed by hand".

Today every batch reuses one file name, and once a file came back and ran again. The change gives each batch its own ID and file name and sets a repeat aside without applying it; old-style files keep working.

**Recommended:** yes; reply "yes" to accept.

> 

### 24. Which jobs should join the 3 frozen test jobs?

All three are closets today (handoff brief 189-190).

**Recommended:** the garage job where you confirmed the 1″ shelves on 9/28, one Tafisa job you trust, and one job each with REA panels and with a stacked tower (you pick them); reply "yes" to accept, or name the jobs.

> 

### Q6a. Do third-party parts belong inside initial cost?

- SCHEMA 193: "Initial Cost = panel material + edge banding + drawer hardware + shoe rails + labor" (no third party).
- BUILD 93-94 and BUDGET 88: third-party parts "sit INSIDE initial cost, before the ×1.25".

**Recommended (BUILD and BUDGET agree; RULES is silent; SCHEMA is the odd one out):** yes, inside, before the ×1.25; reply "yes" to accept.

> 

### Q6b. Is the 10.8% "labor" in the margin math a different cost from shop labor?

- BUILD 92: shop labor is $80 an hour at 6 sheets an hour, inside initial cost.
- BUDGET 91-92: "Labor = 10.8% of Net sale", and margin = 1 − (cost + labor) ÷ net sale.

**Recommended (BUDGET as written):** yes, they are different and both stay; reply "yes" to accept, and say what the 10.8% covers if you can.

> 

### Q17a. Should the crosscheck run every checker?

- RULES 39 and INDEX 36-37: it "runs EVERY checker on every room".
- Your crosscheck files run 14: no garage hinge check, no sheet-size check, no nest cam check.

**Recommended (RULES):** every checker runs: the hinge check on garage jobs, and the sheet-size and nest cam checks whenever a nest exists; reply "yes" to accept.

> 

### Q17b. What should a shared conversion file hold?

- BUDGET 117-129: version 2 adds the source PDF, rates, every PULLED line, third-party parts, roll-ups, costing, pricing and open questions.
- Your October version 2.0 files hold the vertical schedule, runs and flags, but none of those.

**Recommended (RULES is silent):** both: everything BUDGET lists, plus the vertical schedule the October files carry; reply "yes" to accept.

> 

## 7. The drawing reader (needed before the test on your past jobs)

The reader only matters if overnight walls and verticals go ahead, and a test on your past jobs decides that first. It is OpenAI's GPT-6 Luna, unless the test shows it isn't good enough. It isn't trained on your files (OpenAI no longer offers that for this model); it is guided by instructions in your words and 3–5 worked examples from your past jobs, and code checks every value it reads against a second source. OpenAI can update the model without notice, so NG Runner re-reads a few frozen walls every night and stops using it if the answers change.

### 25. May cropped wall drawings be sent to OpenAI to be read, starting with the test?

What goes: one wall's drawing at a time, cut out of the elevation page, plus that wall's parts-list lines as text, plus the worked examples. Code blocks any send that contains the client's name, email, phone or address, and never sends a whole page. During the test only, the same crops also go to one stronger model for comparison (likely Anthropic's Claude). If the reader ever has to go through a middleman service (OpenRouter), you are asked again first. Before the first send, Sulaiman gives you each provider's current data terms in writing: whether it trains on what it receives, and how long it keeps it.

**Recommended:** yes; reply "yes" to accept.

> 

### 26. May ICLA open an OpenAI account for the reader, capped at $20 a month?

The expected cost is about $1–2 a month at your volume (to be measured). The key carries its own spend limit, on top of NG Runner's nightly cap.

**Recommended:** yes; reply "yes" to accept.

> 

### 27. When the reader isn't sure, may it suggest an answer on a one-tap card?

For example: "I think the radius is 3rd from left: [Yes] [No, 2nd]". A value nobody has confirmed is never written into a job, and each of your answers can become a new worked example.

**Recommended:** yes; reply "yes" to accept.

> 

## 8. Needed before checks go live

### Q1. What top should a closet section get?

- RULES 580, 637-638: "0 Full Top · … · 4 None"; a job must carry the template's values.
- BUILD 176-179: "1 = Full Top · … · 5 = None. So 4 builds stretchers where a solid top belongs", from your 9/29 note: "It now has stretchers on the tops instead of a solid top."
- 10-06 archive 1010, 1019-1021: your 9/24 "NG CLOSETS HAS NO TOP OR BOTTOM NAILERS" changed Base Cab Top and Top on Wall Cab from 1 to 4, read as "None".

**Recommended:** a solid full top and no top or bottom nailers on every closet section, as your 9/24 and 9/29 notes say. RULES and BUILD number these settings differently, so Sulaiman confirms which number Mozaik stores for that during the Mozaik test (item 19), and until then NG Runner reports these settings as "could not verify"; reply "yes" to accept.

> 

### Q19. What should happen when a .bak doesn't match its .des?

- RULES 81-82: "A .bak larger or newer than its .des means the .des is stale — read both before writing, or Jim's work is lost."
- A newer .bak is normal after a close: 48 of the 76 job folders with .bak files in a July copy. And your 10/20 cam-fix crosscheck notes (lines 203-211) say: "The .bak is NEWER and SMALLER than its .des on both ... The clean recalculated state exists only in the .bak."

**Recommended:** when RULES 81 fires, NG Runner compares the design inside both files (products, parts, positions, sizes and settings). A .bak that matches a state it saw you save, or a copy it kept before its own write, is history. Anything else stops checks and writes on that job and gives you one item (exact wording settled after the Mozaik test): "[job name] may have unsaved work. Open it and check your latest changes are there before saving; if not, don't save and tell Sulaiman." It never picks the .bak, and until checks go live it only records when it would have fired; reply "yes" to accept.

> 

### 28. Should a "not a problem on all jobs" settle take effect at once?

- RULES 740-746 and INDEX 85-86: any rule change runs regression and is accepted only on your word, and changes "must land in the FILE, not just get said in chat".
- NG Runner proposes: when you settle something for all jobs you name the condition (for example "when the bays on each side are different depths"). It works at once through a shared settled list that your chat reads too, and Sulaiman folds it into RULES at your next chat session, with your accept and the regression run.

**Recommended:** yes; reply "yes" to accept.

> 

## 9. Needed before NG Runner locks jobs or fixes files

### 29. Should Lock need a check of the current design?

Only your click locks a job. Without a check of the design as it stands now, NG Runner asks you to confirm and logs "lock without check".

**Recommended:** yes; reply "yes" to accept.

> 

### 30. While each automatic fix is on trial, should it be one click instead of silent?

- RULES 34-37, 462 and BUILD 389-391: section depth, span and alignment, the bridge sequence, the hang-notch face, WM banding and the WM variant are "fix, don't flag": fixed in the file, and "Something you silently fixed should never appear as a flagged item".
- NG Runner proposes: during each fix's trial it shows as a one-click item. Once you OK that fix, it fixes automatically and logs it, as RULES §0 says.

**Recommended:** yes; reply "yes" to accept.

> 

### 31. How should a fix work once programs exist?

- RULES 407 and BUILD 393-394: "The edit invalidates any nest — say so."; "re-nest and say so".
- NG Runner proposes: it offers only "[Fix and re-nest (material)]", after first reading the nest for parts you flipped by hand in the Optimizer (on 10/20 a fix landed on parts you had already flipped by hand). That material shows NOT CHECKED until it is re-nested.

**Recommended:** yes; reply "yes" to accept.

> 

### 32. May Lock design come before the nest?

- RULES 539-540: the nest cam check "is a HARD GATE BEFORE A LOCK".
- BUILD 334-335: with no nest yet, "Lock the design side anyway (status, Notes)".
- NG Runner proposes three clicks: **Lock design** records the job's calculated rows as "In progress" before nesting; **Finish lock** completes each material only once its nest passes; **Lock** does both when the nest has already passed.

**Recommended:** yes; reply "yes" to accept.

> 

### 33. May NG Runner first watch who opens jobs, before anything goes on George's PC?

Mozaik's lock file and closed marker carry the user name of whoever has or had the job open. NG Runner records them for 4 weeks. If only yours shows up and you confirm it in writing, nothing goes on George's PC. If George does open shared jobs, a small status app goes on his PC so NG Runner never writes while he has a job open (about 5 more days of work).

**Recommended:** yes; reply "yes" to accept, and say if you already know George opens shared jobs.

> 

## 10. Needed before garage builds

Two garage rules are missing from RULES, although INDEX 3 says "No rule changed", and INDEX 27 and BUILD 538-539 still point to "the hinge section in RULES.md".

### Q8a. How many hinges does a garage door get?

- BUILD 532 and 10-06 archive 207-209 (your 9/28 rule): "A door 30″ (762mm) or less on the hinge axis gets exactly 2 hinge holes".
- RULES has no hinge section, though INDEX 27 and BUILD 538-539 point to one.

**Recommended (newest dated rule, 9/28):** doors of 30″ or less get exactly 2 hinges, and the rule goes back into RULES; reply "yes" to accept.

> 

### Q8b. How thick are garage decks (tops and bottoms)?

- 10-06 archive 162, 173-175 (your 10/06 rule): "I have changed the garage spec to tops and bottoms are now 1″." On a colour garage that makes the decks AMWH white, because "1″ stock only exists in white".
- BUILD 132-134, skill 125-126, garage spec 51 and garage runcard 13 still say decks are 3/4.

**Recommended (newest dated rule, 10/06):** decks are 1″; on a colour garage they are white AMWH and flagged every time; the rule goes back into RULES; reply "yes" to accept.

> 

## Anything else?

Anything we didn't ask that we should know? For example a rule that lives only in a chat, a job you want NG Runner to look at first, or something you never want it to do.

> 
