# NG Runner

NG Runner is a Windows tray app for the CNC office of Inspired Closets LA. When a job is added to the fab schedule, it finds the job in Salesforce, downloads its install report (the IC summary) from Studio twice, checks that the report is complete and belongs to that job, and files it in the job's folder. Every step that needs judgment goes to the owner as a one-tap question; nothing is guessed. Later phases add read-only guards, shared checking tools, and a button for locking a job.

## Status

- **Phase 0 (prepare):** ready. It includes a read-only PC check (`tools/setup_check.cmd`), the owner's questionnaire and the access list (`docs/p0/`).
- **Phase 1a (file it):** in progress. Built and tested so far:
  - the write chokepoint (`ngrunner/writes.py`);
  - job matching (`ngrunner/match.py`);
  - the summary check (`ngrunner/intake/summary_check.py`).

  Fetching, the fab-schedule trigger, the board, the tray, the morning digest and the installer are being built against `docs/p1a/build-spec.md`. Nothing is installed on a production PC yet.

## Working on it

Windows 11 and Python 3.14:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -X warn_default_encoding -m pytest -q
```

- **Tests:** they need `-X warn_default_encoding`, and refuse to start without it. Tests that need local-only material (real reports, the shared drive) skip when it is absent.
- **Rules:** `AGENTS.md` holds the rules for anyone changing the code. `GLOSSARY.md` defines the domain words.
- **Client documents** never enter this repository: tests use made-up jobs, and real files stay on the PC.
