# Baseline

Measured from the NG Jobs files, read-only, with `tools/baseline.py` (docs/decisions.md, "Baseline"). Nothing is asked of Jim. The per-job report names jobs, so it stays on the PC that took it. These are the counts. Retake them the same way once NG Runner runs, and compare.

## 2026-10-29 (before NG Runner)

| Measure | Value |
|---|---|
| Job folders (with a `Name_MMDDYY` name) | 88, of which 60 are in `Old` and 4 are garage folders |
| With the IC summary filed as `<folder>_summary.pdf` | 42 (48%) |
| With an IC Conversion | 56 |
| With a crosscheck | 14 |
| Crosscheck rounds per checked job (each "(rev N)" header is a round with chat) | median 1, at most 5 |
| Checked jobs whose rounds ever came back `CROSSCHECK CLEAN` | 21% |
| Days from the summary to the first clean round (file times) | median 2.2 |

What P1a should move: every new job's summary filed as `<folder>_summary.pdf` within 45 minutes of its Fab row, with no download or upload by hand. The crosscheck figures are the baseline for P3.
