"""
Leases (design 5, F8 and C1; questionnaire 15): NG Runner's marker in a job's NG Jobs folder that it is
handling the job, so Jim's chat leaves it alone. It lapses by itself, and Pause, uninstall and "Release
all jobs to chat" release every one. On fakes: the folders are under tmp_path, the clock is a fake and
job names are made up.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ngrunner.clock import FakeClock
from ngrunner.lock.leases import (
    LEASE_NAME, LEASE_TERM, RENEW_AFTER, Lease, is_active, lease_bytes, lease_held, read_lease,
    release_all, release_lease, renew_lease, take_lease,
)
from ngrunner.store import open_store
from ngrunner.store.locks import get_lease
from ngrunner.writes import Roots

JOB, OTHER = "Testerson_101426", "Sampleton_101526"
HOLDER = "NG Runner on PC1"
T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)


def clear(paths: list[Path]) -> str | None:
    return None


@pytest.fixture
def env(tmp_path: Path):
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik copy", tmp_path / "control",
                  tmp_path / "data")
    for folder in (roots.ng_jobs / JOB, roots.ng_jobs / OTHER, roots.mozaik_shared, roots.control,
                   roots.app_data):
        folder.mkdir(parents=True)
    store = open_store(roots.app_data / "ngrunner.db")
    return dict(store=store, roots=roots, interlock=clear, clock=FakeClock(T0), holder=HOLDER)


def lease_file(env, folder: str = JOB) -> Path:
    return env["roots"].ng_jobs / folder / LEASE_NAME


def test_the_lease_file_is_the_one_the_chokepoint_allows_in_a_job_folder() -> None:
    assert LEASE_NAME == "_NG Runner is handling this job.txt"


def test_taking_a_lease_writes_it_active_for_its_term(env) -> None:
    result = take_lease(JOB, **env)

    assert result.ok, result.reason
    lease = read_lease(lease_file(env).read_bytes())
    assert lease == Lease(HOLDER, "active", T0, T0 + LEASE_TERM, None)
    assert lease_held(env["store"], JOB, T0)
    assert get_lease(env["store"], JOB).sha256 == hashlib.sha256(lease_file(env).read_bytes()).hexdigest()


def test_the_file_tells_the_chat_plainly_and_holds_no_client_data(env) -> None:
    take_lease(JOB, **env)
    text = lease_file(env).read_text(encoding="utf-8")

    assert text.startswith("NG Runner is handling this job")
    assert "State: active" in text and "Expires: 2026-10-31T" in text
    assert "@" not in text and JOB not in text


def test_a_lease_lapses_by_itself_when_ng_runner_stops_renewing_it(env) -> None:
    take_lease(JOB, **env)

    assert lease_held(env["store"], JOB, T0 + LEASE_TERM - timedelta(seconds=1))
    assert not lease_held(env["store"], JOB, T0 + LEASE_TERM)
    assert not is_active(read_lease(lease_file(env).read_bytes()), T0 + LEASE_TERM)


def test_a_lease_is_renewed_every_two_hours_while_ng_runner_works(env) -> None:
    take_lease(JOB, **env)
    before = lease_file(env).read_bytes()

    env["clock"].advance(RENEW_AFTER - timedelta(minutes=1))
    early = renew_lease(JOB, **env)
    assert early.ok and lease_file(env).read_bytes() == before        # not due: nothing written

    env["clock"].advance(timedelta(minutes=1))
    due = renew_lease(JOB, **env)
    assert due.ok, due.reason
    renewed = read_lease(lease_file(env).read_bytes())
    assert renewed.expires_at == T0 + RENEW_AFTER + LEASE_TERM and renewed.taken_at == T0
    assert RENEW_AFTER == timedelta(hours=2) < LEASE_TERM


def test_a_lapsed_lease_is_never_renewed_back_to_life(env) -> None:
    take_lease(JOB, **env)
    env["clock"].advance(LEASE_TERM)

    result = renew_lease(JOB, **env)

    assert not result.ok and "lapsed" in result.reason
    assert not lease_held(env["store"], JOB, env["clock"].now())


def test_releasing_a_lease_hands_the_job_back_to_the_chat(env) -> None:
    take_lease(JOB, **env)
    env["clock"].advance(timedelta(minutes=5))

    result = release_lease(JOB, **env)

    assert result.ok, result.reason
    lease = read_lease(lease_file(env).read_bytes())
    assert lease.state == "released" and lease.released_at == T0 + timedelta(minutes=5)
    assert not lease_held(env["store"], JOB, env["clock"].now())


def test_release_all_releases_every_job_and_one_problem_doesnt_stop_the_rest(env) -> None:
    take_lease(JOB, **env)
    take_lease(OTHER, **env)
    lease_file(env, JOB).write_bytes(b"the chat wrote here")          # not NG Runner's any more

    results = release_all(**env)

    assert [(r.folder, r.ok) for r in results] == [(OTHER, True), (JOB, False)] or \
        sorted((r.folder, r.ok) for r in results) == [(JOB, False), (OTHER, True)]
    assert read_lease(lease_file(env, OTHER).read_bytes()).state == "released"
    assert lease_file(env, JOB).read_bytes() == b"the chat wrote here"


def test_a_lease_file_someone_else_changed_is_never_written_over(env) -> None:
    take_lease(JOB, **env)
    lease_file(env).write_bytes(b"edited by hand")
    env["clock"].advance(RENEW_AFTER)

    for act in (take_lease, renew_lease, release_lease):
        result = act(JOB, **env)
        assert not result.ok
    assert lease_file(env).read_bytes() == b"edited by hand"


def test_text_that_isnt_a_lease_reads_as_none() -> None:
    assert read_lease(b"anything at all") is None
    assert read_lease(lease_bytes(Lease(HOLDER, "active", T0, T0 + LEASE_TERM, None))) is not None
    assert read_lease(lease_bytes(Lease(HOLDER, "active", T0, T0 + LEASE_TERM, None))
                      .replace(b"active", b"maybe")) is None
