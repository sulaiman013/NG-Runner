"""
Tests for the P1a interlock (ngrunner.interlock), against the real store and, for the end-to-end
checks, the real commit_write. Paths live under tmp_path; job names are made up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ngrunner.interlock import (
    INSTALL_FOLDERS,
    PAUSED,
    PAUSED_REASON,
    SHADOW,
    is_paused,
    p1a_interlock,
)
from ngrunner.store import Store, StoreError, ensure_job, open_store, set_state
from ngrunner.writes import FileWrite, Roots, commit_write

NOW = datetime(2026, 10, 30, 13, 0, tzinfo=UTC)
JOB = "Testerson_101426"


@dataclass
class _Ctx:
    store: Store
    roots: Roots


@dataclass
class _ShadowCtx(_Ctx):
    mode: str


@pytest.fixture
def ctx(tmp_path: Path) -> _Ctx:
    data = tmp_path / "data"
    for folder in ("NG Jobs", "control", "Mozaik Shared", "data"):
        (tmp_path / folder).mkdir()
    return _Ctx(open_store(data / "ngrunner.db"),
                Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik Shared", tmp_path / "control", data))


def job_file(ctx: _Ctx, folder: str = JOB, name: str = "_summary.pdf") -> Path:
    return ctx.roots.ng_jobs / folder / (folder + name)


def control_file(ctx: _Ctx, *parts: str) -> Path:
    return ctx.roots.control.joinpath(*parts)


# ----- plain cases


def test_a_job_file_and_a_control_file_are_clear(ctx):
    check = p1a_interlock(ctx)
    assert check([job_file(ctx), control_file(ctx, "outbox", "a.json")]) is None


def test_in_shadow_nothing_is_written_in_ng_jobs_whatever_the_job(ctx):
    shadow = _ShadowCtx(ctx.store, ctx.roots, SHADOW)
    check = p1a_interlock(shadow)
    assert "shadow mode" in check([job_file(ctx)])
    assert "shadow mode" in check([ctx.roots.ng_jobs / "Old" / JOB / "x.pdf"])
    assert check([control_file(ctx, SHADOW, JOB, f"{JOB}_summary.pdf")]) is None
    assert check([control_file(ctx, "outbox", "a.json")]) is None
    shadow.mode = "live"
    assert check([job_file(ctx)]) is None                  # it is read each time it is asked


def test_a_pause_is_named_before_shadow_mode(ctx):
    set_state(ctx.store, PAUSED, "yes")
    check = p1a_interlock(_ShadowCtx(ctx.store, ctx.roots, SHADOW))
    assert check([job_file(ctx)]) == PAUSED_REASON


@pytest.mark.parametrize("where", ["mozaik", "data", "elsewhere"])
def test_anything_outside_ng_jobs_and_the_control_folder_is_refused(ctx, tmp_path, where):
    target = {"mozaik": ctx.roots.mozaik_shared / "x.txt", "data": ctx.roots.app_data / "x.txt",
              "elsewhere": tmp_path / "x.txt"}[where]
    assert "outside NG Jobs and the control folder" in p1a_interlock(ctx)([target])


def test_a_target_spelled_in_another_case_is_still_inside(ctx):
    upper = Path(str(job_file(ctx)).upper())
    assert p1a_interlock(ctx)([upper]) is None


def test_a_name_that_only_starts_like_a_root_is_outside(ctx, tmp_path):
    assert p1a_interlock(ctx)([tmp_path / "NG Jobs Old" / "a.txt"]) is not None


def test_one_bad_target_refuses_them_all(ctx):
    assert p1a_interlock(ctx)([job_file(ctx), ctx.roots.mozaik_shared / "x.txt"]) is not None


# ----- paused: filing stops, and nothing else


@pytest.mark.parametrize("value", ["yes", "Sulaiman is checking something"],
                         ids=["the supervisor's yes", "any other value reads as paused"])
def test_paused_refuses_ng_jobs_and_control_shadow(ctx, value):
    set_state(ctx.store, PAUSED, value)
    check = p1a_interlock(ctx)
    assert check([job_file(ctx)]) == PAUSED_REASON
    assert check([control_file(ctx, "shadow", JOB, f"{JOB}_summary.pdf")]) == PAUSED_REASON
    assert check([control_file(ctx, "SHADOW", JOB, f"{JOB}_summary.pdf")]) == PAUSED_REASON


@pytest.mark.parametrize("parts", [("heartbeat", "20261030", "1.json"), ("outbox", "a.json"),
                                   ("logs", "20261030.txt"), ("backups", "20261030.ngrbak"),
                                   ("support", "support-a.zip"), ("shadowy", "a.txt")],
                         ids=["heartbeat", "outbox", "log", "backup", "support bundle",
                              "a folder that only starts like shadow"])
def test_paused_lets_every_other_control_folder_target_pass(ctx, parts):
    set_state(ctx.store, PAUSED, "yes")
    assert p1a_interlock(ctx)([control_file(ctx, *parts)]) is None


def test_not_paused_by_no_or_by_nothing(ctx):
    assert not is_paused(ctx.store)
    set_state(ctx.store, PAUSED, "no")
    assert not is_paused(ctx.store)
    assert p1a_interlock(ctx)([job_file(ctx)]) is None


def test_it_reads_the_pause_each_time_it_is_asked(ctx):
    check = p1a_interlock(ctx)
    assert check([job_file(ctx)]) is None
    set_state(ctx.store, PAUSED, "yes")
    assert check([job_file(ctx)]) == PAUSED_REASON
    set_state(ctx.store, PAUSED, "no")
    assert check([job_file(ctx)]) is None


# ----- OBSERVED: a folder found at install gets no writes


def test_a_folder_there_at_install_gets_no_writes(ctx):
    set_state(ctx.store, INSTALL_FOLDERS, json.dumps([JOB, "Fakeley_102126_Gar"]))
    check = p1a_interlock(ctx)
    assert "only watches it" in check([job_file(ctx)])
    assert "only watches it" in check([job_file(ctx, JOB.upper())])
    assert "only watches it" in check([job_file(ctx, "Fakeley_102126_Gar")])
    assert check([job_file(ctx, "Mockford_102226")]) is None


def test_an_observed_jobs_folder_gets_no_writes_as_closet_or_garage(ctx):
    ensure_job(ctx.store, "4242", JOB, now=NOW, mode="observed")
    check = p1a_interlock(ctx)
    assert "only watches it" in check([job_file(ctx)])
    assert "only watches it" in check([job_file(ctx, f"{JOB}_Gar")])


def test_an_auto_job_is_clear(ctx):
    ensure_job(ctx.store, "4242", JOB, now=NOW, mode="auto")
    assert p1a_interlock(ctx)([job_file(ctx)]) is None


def test_observed_holds_in_shadow_too_and_a_pause_comes_first(ctx):
    set_state(ctx.store, INSTALL_FOLDERS, json.dumps([JOB]))
    target = control_file(ctx, "shadow", JOB, f"{JOB}_summary.pdf")
    assert "only watches it" in p1a_interlock(ctx)([target])
    set_state(ctx.store, PAUSED, "yes")
    assert p1a_interlock(ctx)([target]) == PAUSED_REASON


def test_files_directly_in_ng_jobs_or_shadow_have_no_job_folder(ctx):
    set_state(ctx.store, INSTALL_FOLDERS, json.dumps([JOB]))
    check = p1a_interlock(ctx)
    assert check([ctx.roots.ng_jobs / JOB]) is None         # a file named so has no folder
    assert check([control_file(ctx, "shadow", JOB)]) is None


# ----- with the real chokepoint


def test_commit_write_is_refused_and_writes_nothing_while_paused(ctx):
    set_state(ctx.store, PAUSED, "yes")
    target = job_file(ctx)
    result = commit_write([FileWrite(target, b"x", None)], roots=ctx.roots,
                          interlock=p1a_interlock(ctx))
    assert not result.ok and result.reason == f"Not written: {PAUSED_REASON}"
    assert not target.exists() and not target.parent.exists()


def test_commit_write_passes_targets_from_a_resolved_root_and_the_interlock_knows_them(ctx):
    target = job_file(ctx)
    assert commit_write([FileWrite(target, b"x", None)], roots=ctx.roots,
                        interlock=p1a_interlock(ctx)).ok
    assert target.read_bytes() == b"x"


def test_a_pause_that_comes_while_the_temp_files_are_written_still_refuses(ctx):
    target = job_file(ctx)
    check = p1a_interlock(ctx)
    calls = []

    def pausing(paths: list[Path]) -> str | None:
        calls.append(paths)
        if len(calls) == 2:
            set_state(ctx.store, PAUSED, "yes")
        return check(paths)

    result = commit_write([FileWrite(target, b"x", None)], roots=ctx.roots, interlock=pausing)
    assert not result.ok and len(calls) == 2
    assert not target.exists()


def test_a_store_that_cannot_be_read_refuses_the_commit(ctx, tmp_path, monkeypatch):
    def broken(*args: object, **kwargs: object) -> None:
        raise StoreError("NG Runner's database could not be opened.")

    monkeypatch.setattr("ngrunner.interlock.get_state", broken)
    target = job_file(ctx)
    result = commit_write([FileWrite(target, b"x", None)], roots=ctx.roots,
                          interlock=p1a_interlock(ctx))
    assert not result.ok and "safety check could not run" in result.reason
    assert not target.exists()
