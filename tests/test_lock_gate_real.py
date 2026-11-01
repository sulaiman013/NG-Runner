"""
Check-before-lock on a real job: Jim's crosscheck run from a copy of his toolkit on a staged copy of
Case F (one of the two real cases whose nests reach Nest checked), its result taken as P3's check,
and the gate asked with Case F's rooms as they are on disk.

Measured 2026-10-31: no real case's check passes whole on this PC (wm_variant_check and parm_check
could not verify, tests/test_checks_real.py), so the gate refuses Lock design, Finish lock and Lock on
Case F and says why, and only Jim's "lock without check" would let Lock design through. With a check
that passed, the same hashes let it through, and one room changed since stops it. Marked fixtures:
they skip when the toolkit, the tools' Python or NG Jobs (for its IC Conversion, read only) is absent.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from helpers_lock import job_check
from helpers_toolkit import conversion, job_folder, stage_tree, tools_python
from ngrunner.checks import run_crosscheck
from ngrunner.clock import FakeClock
from ngrunner.guards.design import room_identity
from ngrunner.lock.gate import lock_gate
from ngrunner.toolkit import take_snapshot

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Nest:
    material: str
    state: str
    room_hashes: dict


@pytest.fixture(scope="module")
def case_f(tmp_path_factory):
    tools_python()
    tree = stage_tree(tmp_path_factory.mktemp("jims"))
    copy = take_snapshot(tree, tmp_path_factory.mktemp("data") / "toolkit" / "copy", clock=FakeClock(NOW))
    job = job_folder("F")
    ran = run_crosscheck(copy, job, conversion("F"), tmp_path_factory.mktemp("staging"), python=tools_python())
    rooms = {p.name: p.read_bytes() for p in sorted(job.glob("Room*.des"))}
    sha = {name: hashlib.sha256(data).hexdigest() for name, data in rooms.items()}
    identity = {name: room_identity(name, data) for name, data in rooms.items()}
    check = job_check(rooms=dict(ran.rooms), identities=identity, code=ran.code,
                      conversion=ran.conversion_sha256)
    nests = tuple(Nest(p.stem, "nest_checked", identity) for p in job.glob("*.opt"))
    return check, nests, sha, identity, ran.conversion_sha256


def test_a_real_check_that_could_not_verify_refuses_every_lock_click(case_f) -> None:
    check, nests, sha, identity, conv = case_f
    assert check.state == "could_not_verify"
    assert check.run.rooms == sha                  # it ran on the very bytes on disk

    for click in ("lock_design", "lock"):
        result = lock_gate(click, nests=nests, check=check, room_sha256=sha, room_identity=identity,
                           conversion_sha256=conv)
        assert not result.allowed and [m.code for m in result.missing] == ["check_unsure"]
    waived = lock_gate("lock_design", nests=nests, check=check, room_sha256=sha, room_identity=identity,
                       conversion_sha256=conv, without_check=True)
    assert waived.allowed and waived.without_check


def test_the_same_hashes_let_a_passed_check_through_and_a_changed_room_stops_it(case_f) -> None:
    check, nests, sha, identity, conv = case_f
    passed = replace(check, state="passed")

    assert lock_gate("lock", nests=nests, check=passed, room_sha256=sha, room_identity=identity,
                     conversion_sha256=conv).allowed
    saved = dict(sha, **{"Room1.des": "0" * 64})
    stopped = lock_gate("lock", nests=nests, check=passed, room_sha256=saved, room_identity=identity,
                        conversion_sha256=conv)
    assert [m.code for m in stopped.missing] == ["room_changed"]
