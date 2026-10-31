"""
Check-before-lock (design 5: "Lock is refused ... without a current check"; decision 8; questionnaire 29
and 32): a lock click is refused unless the nest guard says Nest checked for its materials and the P3
check is current for those very rooms, and the refusal says exactly what is missing. Jim's "lock without
check" waives only the check, and is kept; nothing waives a nest.

The facts are plain values in the shapes the store keeps (store.NestRecord, store.CheckRunRecord). Job
names and materials are made up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from ngrunner.lock.gate import GateResult, lock_gate
from ngrunner.messages.lint import check_line

T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
WHITE, ORE = "3-4 Egger Classic White", "3-4 Tafisa Spring Blossom_Ore"
SHA = {"Room0.des": "a" * 64, "Room1.des": "b" * 64}
IDENTITY = {"Room0.des": "c" * 64, "Room1.des": "d" * 64}


@dataclass(frozen=True)
class Nest:
    material: str
    state: str = "nest_checked"
    room_hashes: dict = field(default_factory=lambda: dict(IDENTITY))


@dataclass(frozen=True)
class Check:
    state: str = "done"
    code: str | None = "passed"
    rooms: dict = field(default_factory=lambda: dict(SHA))
    identities: dict = field(default_factory=lambda: dict(IDENTITY))
    stale_at: datetime | None = None
    words: str | None = "Jim's crosscheck passed: every checker it runs read clean."


def gate(click: str = "lock", *, nests=(Nest(WHITE), Nest(ORE)), check=Check(), unsettled: int = 0,
         material: str | None = None, without_check: bool = False, rooms=SHA,
         identities=IDENTITY) -> GateResult:
    return lock_gate(click, nests=nests, check=check, room_sha256=rooms, room_identity=identities,
                     unsettled=unsettled, material=material, without_check=without_check)


def codes(result: GateResult) -> list[str]:
    return [m.code for m in result.missing]


# ----- clear


def test_lock_is_allowed_with_every_nest_checked_and_a_current_check() -> None:
    result = gate()
    assert result.allowed and result.missing == () and not result.without_check


def test_lock_design_needs_the_check_but_no_nest_yet() -> None:
    assert gate("lock_design", nests=()).allowed
    assert codes(gate("lock_design", nests=(), check=None)) == ["no_check"]


def test_finish_lock_needs_only_its_own_materials_nest() -> None:
    nests = (Nest(WHITE), Nest(ORE, state="not_checked"))
    assert gate("finish_lock", nests=nests, material=WHITE).allowed
    assert codes(gate("finish_lock", nests=nests, material=ORE)) == ["nest_not_checked"]


# ----- the nest side


@pytest.mark.parametrize("state", ["not_checked", "do_not_cut", "save_nest", "not_posted"])
def test_lock_is_refused_unless_every_nest_reads_nest_checked(state: str) -> None:
    result = gate(nests=(Nest(WHITE), Nest(ORE, state=state)))

    assert not result.allowed and codes(result) == ["nest_not_checked"]
    assert "Tafisa Spring Blossom Ore" in result.missing[0].words


def test_no_nest_at_all_is_no_lock() -> None:
    assert codes(gate(nests=())) == ["no_nest"]
    assert codes(gate("finish_lock", material=WHITE, nests=(Nest(ORE),))) == ["no_nest"]


def test_a_pass_on_a_design_that_has_changed_since_is_no_pass() -> None:
    changed = dict(IDENTITY, **{"Room1.des": "e" * 64})
    result = gate(identities=changed, check=Check(identities=changed))

    assert codes(result) == ["nest_stale", "nest_stale"]


# ----- the check side


@pytest.mark.parametrize("check, code", [
    (None, "no_check"),
    (Check(state="running", code=None), "check_not_done"),
    (Check(state="not_run", code=None), "check_not_done"),
    (Check(stale_at=T0), "check_stale"),
    (Check(code="could_not_verify", words="NG Runner could not verify parm_check."), "check_unsure"),
])
def test_lock_is_refused_without_a_current_finished_check(check, code: str) -> None:
    result = gate(check=check)
    assert not result.allowed and codes(result) == [code]


def test_a_room_saved_since_the_check_ran_makes_it_no_check_of_this_design() -> None:
    saved = dict(SHA, **{"Room1.des": "f" * 64})
    result = gate(rooms=saved)

    assert codes(result) == ["room_changed"]
    assert "Room 1" in result.missing[0].words


def test_a_room_added_since_the_check_ran_makes_it_no_check_of_this_design() -> None:
    result = gate(rooms=dict(SHA, **{"Room2.des": "9" * 64}),
                  identities=dict(IDENTITY, **{"Room2.des": "8" * 64}))
    assert "room_changed" in codes(result)


def test_findings_left_open_refuse_a_lock_and_settled_ones_dont() -> None:
    assert codes(gate(check=Check(code="failed"), unsettled=2)) == ["items_open"]
    assert gate(check=Check(code="failed"), unsettled=0).allowed


def test_the_check_and_the_nest_pass_must_be_on_the_same_design() -> None:
    other = dict(IDENTITY, **{"Room1.des": "7" * 64})
    result = gate(check=Check(identities=other))
    assert codes(result) == ["check_other_design"]


# ----- lock without check


def test_lock_without_check_waives_only_the_check_and_says_so() -> None:
    result = gate(check=None, without_check=True)
    assert result.allowed and result.without_check
    assert codes(result) == ["no_check"]           # what was waived is still named


def test_lock_without_check_never_waives_a_nest() -> None:
    result = gate(check=None, nests=(Nest(WHITE, state="do_not_cut"),), without_check=True)
    assert not result.allowed
    assert codes(result) == ["no_check", "nest_not_checked"]


def test_a_click_that_isnt_one_of_jims_three_is_refused() -> None:
    with pytest.raises(ValueError):
        gate("unlock")


# ----- the words


def test_every_refusal_reads_plainly_for_the_board() -> None:
    results = [gate(check=None), gate(nests=()), gate(check=Check(code="failed"), unsettled=1),
               gate(nests=(Nest(WHITE, state="do_not_cut"),)), gate(check=Check(stale_at=T0)),
               gate(rooms=dict(SHA, **{"Room1.des": "f" * 64})),
               gate(check=Check(code="could_not_verify", words="NG Runner could not verify parm_check."))]
    for result in results:
        for missing in result.missing:
            assert check_line(missing.words) == [], missing.words
