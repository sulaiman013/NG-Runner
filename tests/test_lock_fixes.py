"""
Fixers as diffs (design 3.2 "Undo", 3.3 "Write", 5 "Approvals and survival"; decisions 7a and 7b): a fix
is planned as a diff against a staged copy and never touches the room it was read from; applied only
after Jim approves that very finding, with a backup, through commit_write and an inverse patch for
Undo; and judged after Jim's next save by the fix's own check.

These run on synthetic rooms with fakes standing in for Jim's fixer and its check: they replay fixed
bytes and fixed lines, so none of his rules is written here. tests/test_lock_fixes_real.py runs his
own scripts on the real rooms. Job names and materials are made up.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ngrunner.clock import FakeClock
from ngrunner.guards.facts import ToolRun
from ngrunner.interlock import p4_interlock
from ngrunner.lock.fixes import (
    FixRefused, apply_fix, plan_fix, replan, survival, undo_fix,
)
from ngrunner.lock.notch import NOTCH_FACE
from ngrunner.lock.patch import PatchRefused, apply_patch, diff, inverse
from ngrunner.store import open_store
from ngrunner.store.locks import get_fix, list_fixes
from ngrunner.toolkit import Snapshot
from ngrunner.writes import Roots

JOB = "Testerson_101426"
T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
MATERIAL = "3-4 Egger Classic White"


def room(active: str = "False", hidden: str = "True", tail: str = "") -> bytes:
    """A made-up room with one WM panel, C7: line bores on the flip face, its notch drawn twice."""
    lines = [
        "15",
        '<?xml version="1.0" encoding="utf-8"?>',
        '<Room Name="Made up">',
        '  <Product ProdName="WM Panel 48 Thru" CabNo="7" Width="19.304">',
        '    <CabProdPart Name="Panel">',
        '      <OperationLineBore ID="1" Depth="15" FlipSideOp="True" />',
        f'      <OperationGroove Width="6.35" ID="2" Hide="{"False" if active == "False" else "True"}" FlipSideOp="False">',
        "      </OperationGroove>",
        f'      <OperationGroove Width="6.35" ID="3" Hide="{"True" if active == "False" else "False"}" FlipSideOp="True">',
        "      </OperationGroove>",
        "    </CabProdPart>",
        "  </Product>",
        f'  <Annotations Count="{tail or "1"}" />',
        "</Room>",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


WRONG = room()                       # the active notch is on the front face: the wrong one
FIXED = room(active="True")          # what Jim's fixer makes of it: the hidden one is active
WRONG_LINE = "   !! C7   WM Panel 48 Thru         notch on face False, line bores on True - NOTCH ON THE WRONG FACE"
CLEAN_LINE = "   every notch is on its line-bore face; WM banding taped and locked"


class FakeJim:
    """Jim's notch_side.py and notch_check.py, replayed: his fixer writes FIXED for WRONG, and his check
    flags C7 on any room whose active groove is ID 2, as these two rooms are written."""

    def __init__(self, *, fixed: bytes = FIXED, still_flags: bool = False) -> None:
        self.still_flags, self.calls = still_flags, []
        self.replies = {room(tail=t): room(active="True", tail=t) for t in "2345"}
        self.replies[WRONG] = fixed

    def __call__(self, snapshot, script: str, args, *, timeout_s: int = 120, python=None) -> ToolRun:
        self.calls.append((script, list(args)))
        data = Path(args[0]).read_bytes()
        if script.endswith("notch_side.py"):
            Path(args[1]).write_bytes(self.replies.get(data, data))
            return ToolRun(0, "  ('7', 'WM Panel 48 Thru', ...)\n  fixed ...\n", "", False)
        wrong = b'ID="2" Hide="False"' in data or self.still_flags
        lines = [Path(args[0]).name, WRONG_LINE if wrong else CLEAN_LINE]
        return ToolRun(1 if wrong else 0, "\n".join(lines) + "\n", "", False)


def nest(flipped: bool) -> bytes:
    """A made-up nest of C7's material, its part placed on a sheet, flipped by hand or not."""
    return ("8\r\n<?xml version=\"1.0\" encoding=\"utf-8\"?>\r\n"
            "<OptimizeMaterial DisplayName=\"x\" Width=\"1244.6\" Length=\"2463.8\" MaterialId=\"1\">"
            "<RoomInfo Number=\"1\" />"
            "<OptimizePart PartID=\"4\" Quan=\"1\" Name=\"Panel\" AssyNo=\"R1C7\" Width=\"300\" Length=\"1200\" />"
            "<OptimizeSheet Quan=\"1\" Flipped=\"False\" Width=\"1244.6\" Length=\"2463.8\">"
            f"<OptimizePartLocation PartID=\"4\" X=\"1\" Y=\"1\" Flipped=\"{'True' if flipped else 'False'}\" />"
            "</OptimizeSheet></OptimizeMaterial>").encode("utf-8")


@pytest.fixture
def env(tmp_path: Path):
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik copy", tmp_path / "control", tmp_path / "data")
    job = roots.mozaik_shared / "Jobs" / JOB
    for folder in (roots.ng_jobs, job, roots.control, roots.app_data):
        folder.mkdir(parents=True)
    (job / "Room1.des").write_bytes(WRONG)
    (job / "MozaikJob.UNS").write_bytes(b"JDELA\r\n")
    clock = FakeClock(T0)
    return dict(roots=roots, job=job, room=job / "Room1.des", store=open_store(roots.app_data / "n.db"),
                clock=clock, work=tmp_path / "work", jim=FakeJim(),
                snapshot=Snapshot(tmp_path / "toolkit", "0" * 64, T0, 1))


def plan(env, **kw):
    return plan_fix(NOTCH_FACE, env["snapshot"], env["room"], folder=JOB, work=env["work"],
                    run=kw.pop("run", env["jim"]), nests=kw.pop("nests", {}), **kw)


def write_args(env, interlock=lambda paths: None) -> dict:
    return dict(store=env["store"], roots=env["roots"], interlock=interlock, clock=env["clock"])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----- the byte patch


def test_a_patch_applies_only_to_the_bytes_it_was_made_from_and_its_inverse_gives_them_back() -> None:
    patch = diff(WRONG, FIXED)

    assert apply_patch(patch, WRONG) == FIXED
    assert apply_patch(inverse(patch), FIXED) == WRONG
    with pytest.raises(PatchRefused):
        apply_patch(patch, WRONG + b" ")
    assert diff(WRONG, WRONG).edits == ()


def test_a_patch_names_only_the_bytes_that_changed() -> None:
    patch = diff(WRONG, FIXED)
    assert sum(len(e.old) for e in patch.edits) < 20 and len(patch.edits) == 2


# ----- planning: a diff against a staged copy


def test_a_plan_is_the_fixers_own_output_as_a_diff_with_what_it_touched(env) -> None:
    made = plan(env)

    assert apply_patch(made.patch, WRONG) == FIXED
    assert [(e.cab, e.id, e.attribute, e.before, e.after) for e in made.elements] == [
        ("7", "2", "Hide", "False", "True"), ("7", "3", "Hide", "True", "False")]
    assert made.tags == ("R1C7",) and made.room == "Room1.des" and made.folder == JOB
    assert len(made.fingerprint) == 64 and plan(env).fingerprint == made.fingerprint


def test_planning_never_touches_the_room_it_reads(env) -> None:
    before = os.stat(env["room"])

    plan(env)

    after = os.stat(env["room"])
    assert env["room"].read_bytes() == WRONG
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    assert all(Path(arg).parent != env["job"] for _, args in env["jim"].calls for arg in args)
    assert not any(env["work"].rglob("*.des"))          # the staged copies went with their run


def test_nothing_to_fix_is_no_plan(env) -> None:
    env["room"].write_bytes(FIXED)
    assert plan(env) is None


def test_a_fixer_that_changes_more_than_it_says_is_refused(env) -> None:
    sneaky = FIXED.replace(b'Width="19.304"', b'Width="19.305"')
    with pytest.raises(FixRefused, match="more than"):
        plan(env, run=FakeJim(fixed=sneaky))


@pytest.mark.parametrize("broken", [
    FIXED.replace(b"\r\n", b"\n", 1),                       # a bare LF
    FIXED.replace(b"15\r\n", b"16\r\n", 1),                 # another version line
    FIXED.replace(b"</Room>", b"</Rom>"),                   # no longer XML
])
def test_a_fixer_output_that_breaks_the_file_is_refused(env, broken: bytes) -> None:
    with pytest.raises(FixRefused):
        plan(env, run=FakeJim(fixed=broken))


def test_a_fix_its_own_check_doesnt_clear_is_refused(env) -> None:
    with pytest.raises(FixRefused, match="own check"):
        plan(env, run=FakeJim(still_flags=True))


def test_the_nest_is_read_for_hand_flips_on_the_parts_the_fix_touches(env) -> None:
    flipped = plan(env, nests={MATERIAL: nest(flipped=True)})
    plain = plan(env, nests={MATERIAL: nest(flipped=False)})

    assert flipped.hand_flipped == ("R1C7",) and flipped.renest == (MATERIAL,)
    assert plain.hand_flipped == () and plain.renest == (MATERIAL,)
    assert flipped.fingerprint != plain.fingerprint                # Jim approves what he was shown
    assert any("Fix and re-nest" in line for line in plain.words)
    assert any("flipped by hand" in line for line in flipped.words)


# ----- applying, after approval


def test_a_fix_applies_only_with_jims_approval_of_that_very_finding(env) -> None:
    made = plan(env)

    refused = apply_fix(made, approved="f" * 64, programs={}, **write_args(env))

    assert not refused.ok and "approved" in refused.reason
    assert env["room"].read_bytes() == WRONG and list_fixes(env["store"]) == []


def test_an_approved_fix_is_backed_up_written_and_kept_with_its_inverse(env) -> None:
    made = plan(env)

    result = apply_fix(made, approved=made.fingerprint, programs={MATERIAL: ["c" * 64]}, **write_args(env))

    assert result.ok, result.reason
    assert env["room"].read_bytes() == FIXED
    record = get_fix(env["store"], result.fix.id)
    assert record.base_sha256 == sha(WRONG) and record.result_sha256 == sha(FIXED)
    assert [Path(b).read_bytes() for b in record.backups] == [WRONG]
    assert record.programs == {MATERIAL: ["c" * 64]}


def test_a_room_saved_since_the_plan_is_planned_again_not_patched(env) -> None:
    made = plan(env)
    env["room"].write_bytes(room(tail="2"))

    result = apply_fix(made, approved=made.fingerprint, programs={}, **write_args(env))

    assert not result.ok and "planned again" in result.reason
    assert env["room"].read_bytes() == room(tail="2")


def test_approvals_bind_to_the_finding_not_the_file(env) -> None:
    made = plan(env)
    env["room"].write_bytes(room(tail="2"))                 # saved again, same finding
    again = plan(env)

    assert replan(made, again) == "same"
    assert replan(made, None) == "gone"
    env["room"].write_bytes(room(tail="2"))
    assert replan(made, plan(env, nests={MATERIAL: nest(flipped=True)})) == "changed"


def test_no_fix_is_written_while_mozaik_runs(env) -> None:
    made = plan(env)

    @dataclass
    class Ctx:
        store: object
        roots: Roots
        clock: FakeClock
        tracker: None = None

        def lease_held(self, job: str) -> bool:
            return True

    other = Roots(env["work"] / "real NG Jobs", env["work"] / "real Mozaik", env["work"] / "real control",
                  env["roots"].app_data)
    check = p4_interlock(Ctx(env["store"], other, env["clock"]), mozaik_running=lambda: True)

    result = apply_fix(made, approved=made.fingerprint, programs={}, **write_args(env, check))

    assert not result.ok and "Mozaik is running" in result.reason
    assert env["room"].read_bytes() == WRONG


# ----- Undo


def applied(env, programs=None):
    made = plan(env)
    result = apply_fix(made, approved=made.fingerprint, programs=programs or {MATERIAL: ["c" * 64]},
                       **write_args(env))
    assert result.ok, result.reason
    return get_fix(env["store"], result.fix.id)


def test_undo_gives_back_the_exact_original_bytes(env) -> None:
    record = applied(env)

    result = undo_fix(record, NOTCH_FACE, programs={MATERIAL: ["c" * 64]}, **write_args(env))

    assert result.ok, result.reason
    assert env["room"].read_bytes() == WRONG
    assert get_fix(env["store"], record.id).state == "undone"


def test_undo_is_refused_once_a_program_was_made_from_the_fixed_design(env) -> None:
    record = applied(env)

    result = undo_fix(record, NOTCH_FACE, programs={MATERIAL: ["c" * 64, "d" * 64]}, **write_args(env))

    assert not result.ok and "program" in result.reason
    assert env["room"].read_bytes() == FIXED


def test_undo_after_a_save_puts_back_only_the_values_it_changed(env) -> None:
    record = applied(env)
    env["room"].write_bytes(room(active="True", tail="5"))      # Jim saved: the fix held, a count moved

    result = undo_fix(record, NOTCH_FACE, programs={MATERIAL: ["c" * 64]}, **write_args(env))

    assert result.ok, result.reason
    assert env["room"].read_bytes() == room(tail="5")


def test_undo_is_refused_once_the_values_are_no_longer_ng_runners(env) -> None:
    record = applied(env)
    env["room"].write_bytes(room(tail="5"))                      # Mozaik put the notch back

    result = undo_fix(record, NOTCH_FACE, programs={MATERIAL: ["c" * 64]}, **write_args(env))

    assert not result.ok and "no longer" in result.reason
    assert env["room"].read_bytes() == room(tail="5")


# ----- survival, after Jim's next save


def judged(env, record, data: bytes, sessions=()):
    return survival(record, NOTCH_FACE, env["snapshot"], room_bytes=data, room_name="Room1.des",
                    work=env["work"], sessions=sessions, store=env["store"], at=env["clock"].now(),
                    run=env["jim"])


def test_a_fix_not_saved_over_yet_is_waiting(env) -> None:
    record = applied(env)
    assert judged(env, record, FIXED).code == "waiting"


def test_a_fix_still_there_after_the_next_save_held(env) -> None:
    record = applied(env)
    result = judged(env, record, room(active="True", tail="3"))
    assert result.code == "held"
    assert get_fix(env["store"], record.id).survival == "held"


def test_a_value_put_back_by_a_session_open_across_the_write_is_mozaik_undid(env) -> None:
    record = applied(env)
    open_across = [(record.applied_at - timedelta(minutes=5), record.applied_at + timedelta(minutes=30))]

    result = judged(env, record, room(tail="3"), sessions=open_across)

    assert result.code == "mozaik_undid" and result.urgent and result.redo
    assert "Mozaik undid" in result.words


def test_a_value_changed_in_a_session_after_the_write_is_jims_own_change(env) -> None:
    record = applied(env)
    later = [(record.applied_at + timedelta(minutes=5), record.applied_at + timedelta(minutes=30))]

    result = judged(env, record, room(tail="3"), sessions=later)

    assert result.code == "you_changed" and not result.urgent
    assert "You changed it in Mozaik" in result.words


def test_a_panel_gone_after_the_save_could_not_be_verified(env) -> None:
    record = applied(env)
    gone = room(active="True", tail="3").replace(b'CabNo="7"', b'CabNo="8"')
    assert judged(env, record, gone).code == "could_not_verify"
