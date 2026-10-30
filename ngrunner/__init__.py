"""
NG Runner: watches for new NG jobs and runs Jim's IC summary -> Mozaik pipeline on his PC.

Code decides; an LLM only ever reads drawings (Phase 5) and every value it returns is
confirmed by a non-LLM source. All writes outside the app's own data folder go through
ngrunner.writes.commit_write(). See AGENTS.md.
"""
