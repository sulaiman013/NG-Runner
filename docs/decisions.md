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
| Questions during work (Q14) | Work never stops to ask; questions come as cards. Only "which job is this row?" and "closet or garage?" make a job wait, and only that job | RULES 3-4, 57 |
| Messages (Q18a) | Jim's format: numbered, one line each, no intro or sign-off; a clean result is exactly "Nothing to fix — matches IC summary." | RULES 11, 19-20 |
| Accounts (11, 12) | ICLA owns every account (Google automation account, Pushover when added, GitHub, signing); Sulaiman is a member | As recommended |
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
