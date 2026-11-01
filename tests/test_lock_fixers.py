"""
P4's fixers beyond the notch (lock/topline.py, lock/banding.py, lock/fixers.py): Jim's top_line.py, wm_eb's
enforce_room and eb_tops.py, each planned on a staged copy as a diff, refused unless RULES section 6's byte
gate passes, every byte it changed is one of its elements' values and its own check named exactly the
touched parts before and none after; then applied, undone and judged by the same fixes.py life as the notch.

Synthetic rooms and fakes that replay what his scripts print (tests/helpers_fixers.py): none of his rules
is written here. tests/test_lock_fixers_real.py runs his own scripts on the real rooms. Names are made up.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import helpers_fixers as h
from ngrunner.checks import findings
from ngrunner.clock import FakeClock
from ngrunner.guards.facts import ToolRun
from ngrunner.lock import des
from ngrunner.lock.banding import COUNTERTOP_BANDING, WM_BANDING, CountertopBanding, WmBanding
from ngrunner.lock.fixers import FIXERS, fixer_for, fixer_named
from ngrunner.lock.fixes import Element, FixRefused, Said, apply_fix, plan_fix, said_cleared, survival, undo_fix
from ngrunner.lock.notch import NOTCH_FACE
from ngrunner.lock.patch import apply_patch
from ngrunner.lock.topline import TOP_LINE
from ngrunner.store import open_store
from ngrunner.store.locks import get_fix
from ngrunner.toolkit import Snapshot
from ngrunner.writes import Roots

JOB = "Testerson_101426"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
MATERIAL = "3-4 Egger Classic White"


@pytest.fixture
def env(tmp_path: Path):
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik copy", tmp_path / "control", tmp_path / "data")
    job = roots.mozaik_shared / "Jobs" / JOB
    for folder in (roots.ng_jobs, job, roots.control, roots.app_data):
        folder.mkdir(parents=True)
    toolkit = tmp_path / "toolkit"
    (toolkit / "scripts").mkdir(parents=True)
    (toolkit / "scripts" / "crosscheck.py").write_text(h.CROSSCHECK, encoding="utf-8")
    return dict(roots=roots, job=job, room=job / "Room1.des", store=open_store(roots.app_data / "n.db"),
                clock=FakeClock(T0), work=tmp_path / "work", snapshot=Snapshot(toolkit, "0" * 64, T0, 1))


def plan(env, fixer, data: bytes, run, **kw):
    env["room"].write_bytes(data)
    return plan_fix(fixer, env["snapshot"], env["room"], folder=JOB, work=env["work"], run=run,
                    nests=kw.pop("nests", {}), **kw)


def write_args(env) -> dict:
    return dict(store=env["store"], roots=env["roots"], interlock=lambda paths: None, clock=env["clock"])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def only_its_elements_changed(fixer, base: bytes, made) -> bool:
    """The fixed room is the room with only its elements' values changed, each read where the fixer reads it
    on either side: the property each plan must hold (fixes.plan_fix refuses any other)."""
    result = apply_patch(made.patch, base)

    def masked(data: bytes, value: str) -> bytes | None:
        spans = [(fixer.locate(data, e), getattr(e, value)) for e in made.elements]
        if any(span is None or data[span[0]:span[1]] != want.encode() for span, want in spans):
            return None
        out, at = [], 0
        for (start, end), _ in sorted(spans):
            out += [data[at:start], b"|"]
            at = end
        return b"".join([*out, data[at:]])

    before = masked(base, "before")
    return before is not None and before == masked(result, "after")


# ----- the list of fixers


def test_each_fixer_has_its_own_name_and_fixes_a_finding_p3_raises() -> None:
    assert [f.name for f in FIXERS] == ["notch_face", "top_line", "wm_banding", "countertop_banding"]
    raised = {(checker, what) for checker, table in findings._FAULTS.items() for _, what, kind in table
              if kind == findings.FIX}
    raised |= {(checker, what) for checker, table in findings._UNFLAGGED.items() for _, what in table}
    for fixer in FIXERS:
        assert fixer.finding in raised, fixer.name
        assert fixer_named(fixer.name) is fixer and fixer_for(fixer.finding) is fixer
    assert fixer_named("swap_rea") is None and fixer_for(("br_cam_check", "bridge_cams")) is None


def test_every_fixers_words_pass_the_lint_on_jims_messages(env) -> None:
    from ngrunner.lock.fixes import _in_cab_order
    from ngrunner.messages import templates
    from ngrunner.messages.lint import check_line

    wm = h.FakeWm()
    moved = h.countertop_room(back=("3", "True"))
    made = [plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine()), plan(env, WmBanding(call=wm.call), h.WM_WRONG, wm.run),
            plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, h.FakeEbTops(fixes={h.TOPS_WRONG: moved.replace(
                b'EBand="3"', b'EBand="1"')}))]
    for each in made:
        assert [check_line(line) for line in each.words] == [[] for _ in each.words], each.words
        assert check_line(templates.fix_card(JOB, words=each.words, renest=each.renest).text) == []
        fixer = fixer_named(each.fixer)
        short = ", ".join(dict.fromkeys(fixer.short(e) for e in _in_cab_order(each.elements)))
        for said in (f"The fix on {short} held after the save", f"Mozaik undid the fix on {short}: the save put"
                     " the old values back", f"You changed it in Mozaik: {short} holds other values now"):
            assert check_line(said) == [], said


def test_the_notch_fixer_is_the_one_it_always_was() -> None:
    assert (NOTCH_FACE.name, NOTCH_FACE.script, NOTCH_FACE.check) == (
        "notch_face", "scripts/notch_side.py", "scripts/notch_check.py")
    assert NOTCH_FACE.finding == ("notch_check", "notch_face")


# ----- what a check said, by identity


def test_a_check_must_name_exactly_the_touched_parts_before_and_none_after() -> None:
    keys = {"4": "panel", "6": "panel", "8": "other"}
    before, clean = Said({"panel": 2}, keys), Said({}, keys)

    assert said_cleared(before, clean, {"4", "6"})
    assert not said_cleared(before, clean, {"4"})                       # a named part the fix didn't touch
    assert not said_cleared(Said({"panel": 2, "other": 1}, keys), clean, {"4", "6"})
    assert not said_cleared(before, Said({"panel": 1}, keys), {"4", "6"})  # still named after
    moved = {"4": "moved", "6": "moved", "8": "panel"}
    assert said_cleared(before, Said({}, moved), {"4", "6"})
    assert not said_cleared(Said({"panel": 2}, keys), Said({"panel": 1}, moved), {"4", "6"})   # a line still there
    assert not said_cleared(before, clean, {"4", "6", "9"})            # a part that isn't there
    assert not said_cleared(before, clean, set())


# ----- the top line


def test_a_top_line_plan_is_each_panels_elev_by_its_cabno(env) -> None:
    made = plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine())

    assert [(e.cab, e.id, e.attribute, e.before, e.after) for e in made.elements] == [
        ("4", "", "Elev", "1127.1", "928"), ("6", "", "Elev", "1127.125", "928")]
    assert made.tags == ("R1C4", "R1C6") and made.fixer == "top_line"
    assert apply_patch(made.patch, h.TOP_WRONG) == h.TOP_FIXED
    assert only_its_elements_changed(TOP_LINE, h.TOP_WRONG, made)
    assert made.words[0] == ("Room 1: 2 wall-hung panels (R1C4, R1C6) don't hang on the room's top line; the fix moves"
                             ' each one up or down onto it, the line at 83 15/16" (2132 mm), the furthest by'
                             ' 7 13/16" (199.1 mm)')


def test_the_top_line_fixer_runs_only_on_a_staged_copy_and_its_check_with_check(env) -> None:
    jim = h.FakeTopLine()
    plan(env, TOP_LINE, h.TOP_WRONG, jim)

    assert env["room"].read_bytes() == h.TOP_WRONG
    assert [args[0] == "--check" for _, args in jim.calls] == [False, True, True]
    assert all(Path(args[-1]).parent != env["job"] for _, args in jim.calls)
    assert not any(env["work"].rglob("*.des"))


def test_a_room_on_its_top_line_is_no_plan(env) -> None:
    assert plan(env, TOP_LINE, h.TOP_FIXED, h.FakeTopLine()) is None


def test_a_top_line_fixer_that_changes_more_than_elev_is_refused(env) -> None:
    for sneaky in (h.topline_room("928", "928", h4="1205"), h.topline_room(h4="1205")):
        with pytest.raises(FixRefused, match="more than the panels' Elev"):
            plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine(fixed=sneaky))


def test_a_check_line_naming_nothing_in_the_room_cant_be_read(env) -> None:
    def said(text: str):
        return lambda *a, **k: ToolRun(0, text, "", False)

    assert TOP_LINE.flags(env["snapshot"], h.TOP_FIXED, room_name="Room1.des", work=env["work"],
                          run=said(h.top_check(2))) is None                # no panel there at 1127.1
    other = "   !! Wall Mount 72 Left        WM banding not taped + locked on top/front/bottom"
    assert WM_BANDING.flags(env["snapshot"], h.WM_WRONG, room_name="Room1.des", work=env["work"],
                            run=h.FakeWm(lines=[other]).run) is None
    assert COUNTERTOP_BANDING.flags(env["snapshot"], h.TOPS_WRONG, room_name="Room1.des", work=env["work"],
                                    run=h.FakeEbTops(lines=[h.TOPS_LINE_33])) is None


def test_a_top_line_check_that_named_a_panel_the_fix_didnt_move_refuses_the_plan(env) -> None:
    one = h.topline_room("928", "1127.125")             # the fixer moved only C4; the check named two
    with pytest.raises(FixRefused, match="own check"):
        plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine(fixed=one, check={one: h.top_check(0)}))


def test_a_top_line_check_that_still_names_a_moved_panel_refuses_the_plan(env) -> None:
    for after in (h.top_check(1, h.TOP_HIT_AT_928), h.top_check(2)):      # named where it is, or nowhere
        with pytest.raises(FixRefused, match="own check"):
            plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine(check={h.TOP_FIXED: after}))


@pytest.mark.parametrize("said", [
    "   Room1.des    something else\n\nwould change: 0 WM panels\n",           # a line it never prints
    h.top_check(2).replace("would change: 2", "would change: 3"),               # a count that doesn't fit
    h.top_check(2).replace("Room1.des", "Room2.des"),                           # another room
    h.top_check(0) + h.TOP_ALREADY + "\n",                                      # said twice
], ids=["unknown line", "count", "another room", "twice"])
def test_a_top_line_check_that_cant_be_read_is_none(env, said: str) -> None:
    def run(*_a, **_k):
        return ToolRun(0, said, "", False)

    assert TOP_LINE.flags(env["snapshot"], h.TOP_WRONG, room_name="Room1.des", work=env["work"], run=run) is None


@pytest.mark.parametrize("run", [ToolRun(1, h.top_check(0), "", False), ToolRun(0, h.top_check(0), "oops", False),
                                 ToolRun(None, "", "", True)], ids=["exit 1", "stderr", "timed out"])
def test_a_top_line_check_that_didnt_finish_cleanly_is_none(env, run: ToolRun) -> None:
    assert TOP_LINE.flags(env["snapshot"], h.TOP_WRONG, room_name="Room1.des", work=env["work"],
                          run=lambda *a, **k: run) is None


def test_two_panels_named_alike_are_told_apart_by_what_the_check_says_after(env) -> None:
    wide = h.topline_room(w6="30")                      # C6 named like C4, but not a panel top_line moves
    right, wrong = h.topline_room("928", w6="30"), h.topline_room(e6="928", w6="30")
    checks = {wide: h.top_check(1), right: h.top_check(0), wrong: h.top_check(1)}

    def jim(fixed: bytes) -> h.FakeTopLine:
        fake = h.FakeTopLine(check=checks)
        fake.fixes[wide] = fixed
        return fake

    made = plan(env, TOP_LINE, wide, jim(right))
    assert [e.cab for e in made.elements] == ["4"]
    with pytest.raises(FixRefused, match="own check"):                      # it moved the one not named
        plan(env, TOP_LINE, wide, jim(wrong))


def test_a_top_line_fix_is_written_undone_to_the_exact_bytes_and_judged_after_a_save(env) -> None:
    made = plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine())
    applied = apply_fix(made, approved=made.fingerprint, programs={}, **write_args(env))
    assert applied.ok, applied.reason
    assert env["room"].read_bytes() == h.TOP_FIXED
    record = get_fix(env["store"], applied.fix.id)

    def judged(data, sessions=()):
        return survival(record, TOP_LINE, env["snapshot"], room_bytes=data, room_name="Room1.des", work=env["work"],
                        sessions=sessions, store=env["store"], at=T0, run=h.FakeTopLine(), keep=False)

    assert judged(h.TOP_FIXED).code == "waiting"
    assert judged(h.topline_room("928", "928", count="3")).code == "held"
    assert judged(h.topline_room(count="3"), [(T0 - timedelta(minutes=5), None)]).code == "mozaik_undid"
    assert judged(h.topline_room("900", "928", count="3")).code == "you_changed"
    assert "C4, C6" in judged(h.topline_room("928", "928", count="3")).words

    undone = undo_fix(record, TOP_LINE, programs={}, **write_args(env))
    assert undone.ok, undone.reason
    assert env["room"].read_bytes() == h.TOP_WRONG


def test_undo_of_a_top_line_fix_after_a_save_puts_back_only_the_elevs(env) -> None:
    made = plan(env, TOP_LINE, h.TOP_WRONG, h.FakeTopLine())
    record = get_fix(env["store"], apply_fix(made, approved=made.fingerprint, programs={}, **write_args(env)).fix.id)
    env["room"].write_bytes(h.topline_room("928", "928", count="5"))           # Jim saved; the fix held

    assert undo_fix(record, TOP_LINE, programs={}, **write_args(env)).ok
    assert env["room"].read_bytes() == h.topline_room(count="5")


# ----- WM banding


def test_a_wm_banding_plan_is_each_edges_band_and_lock_by_its_part_and_place(env) -> None:
    jim = h.FakeWm()
    made = plan(env, WmBanding(call=jim.call), h.WM_WRONG, jim.run)

    assert [(e.cab, e.id, e.attribute, e.before, e.after) for e in made.elements] == [
        ("7", "0.2", "EBandLock", "False", "True"), ("7", "0.3", "EBand", "0", "1"),
        ("7", "0.3", "EBandLock", "False", "True")]
    assert made.tags == ("R1C7",) and made.fixer == "wm_banding"
    assert apply_patch(made.patch, h.WM_WRONG) == h.WM_FIXED
    assert only_its_elements_changed(WM_BANDING, h.WM_WRONG, made)
    assert made.words[0] == ("Room 1: 1 wall-hung panel (R1C7) isn't taped and locked on top, front and bottom; the fix"
                             " tapes and locks those edges")


def test_wm_banding_is_called_as_build_calls_it_on_a_staged_copy(env) -> None:
    jim = h.FakeWm()
    plan(env, WmBanding(call=jim.call), h.WM_WRONG, jim.run)

    module, function, room, out = jim.calls[0]
    assert (module, function) == ("scripts/wm_eb.py", "enforce_room")
    assert Path(room).parent != env["job"] and Path(out).parent != env["job"]
    assert [c[0] for c in jim.calls[1:]] == ["scripts/notch_check.py", "scripts/notch_check.py"]
    assert env["room"].read_bytes() == h.WM_WRONG


def test_wm_banding_that_touches_a_point_off_a_parts_outline_is_refused(env) -> None:
    jim = h.FakeWm(fixed=h.wm_room(front=("1", "True"), bottom=("1", "True"), outline="1"))
    with pytest.raises(FixRefused, match="can't name"):
        plan(env, WmBanding(call=jim.call), h.WM_WRONG, jim.run)


def test_wm_banding_that_moves_a_point_is_refused(env) -> None:
    moved = h.WM_FIXED.replace(b'X="756" Y="356"', b'X="757" Y="356"')
    jim = h.FakeWm(fixed=moved)
    with pytest.raises(FixRefused, match="more than"):
        plan(env, WmBanding(call=jim.call), h.WM_WRONG, jim.run)


def test_wm_banding_whose_check_named_another_panel_or_still_names_it_is_refused(env) -> None:
    other = "   !! Wall Mount 72 Left        WM banding not taped + locked on top/front/bottom"
    for jim in (h.FakeWm(lines=[h.WM_LINE, other]), h.FakeWm(still_flags=True)):
        with pytest.raises(FixRefused, match="own check"):
            plan(env, WmBanding(call=jim.call), h.WM_WRONG, jim.run)


def test_wm_banding_whose_call_breaks_is_refused(env) -> None:
    jim = h.FakeWm(call_fails=True)
    with pytest.raises(FixRefused, match="didn't finish cleanly"):
        plan(env, WmBanding(call=jim.call), h.WM_WRONG, jim.run)


def test_a_wm_banding_line_not_in_its_printed_shape_cant_be_read(env) -> None:
    jim = h.FakeWm(lines=["   !! WM Panel 48 Thru WM banding not taped + locked on top/front/bottom"])
    assert WM_BANDING.flags(env["snapshot"], h.WM_WRONG, room_name="Room1.des", work=env["work"], run=jim.run) is None


def test_a_wm_banding_fix_is_written_undone_to_the_exact_bytes_and_judged(env) -> None:
    jim = h.FakeWm()
    fixer = WmBanding(call=jim.call)
    made = plan(env, fixer, h.WM_WRONG, jim.run)
    record = get_fix(env["store"], apply_fix(made, approved=made.fingerprint, programs={}, **write_args(env)).fix.id)
    assert env["room"].read_bytes() == h.WM_FIXED

    held = survival(record, fixer, env["snapshot"], room_bytes=h.wm_room(front=("1", "True"), bottom=("1", "True"),
                                                                          count="3"),
                    room_name="Room1.des", work=env["work"], sessions=(), store=env["store"], at=T0, run=jim.run,
                    keep=False)
    assert held.code == "held" and held.words == "The fix on C7 held after the save"

    assert undo_fix(record, fixer, programs={}, **write_args(env)).ok
    assert env["room"].read_bytes() == h.WM_WRONG


# ----- countertops


def test_a_countertop_plan_is_each_edges_band_and_lock_by_its_idtag_and_place(env) -> None:
    jim = h.FakeEbTops()
    made = plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, jim, nests={MATERIAL: h.nest([("R1N0", False)])})

    assert [(e.cab, e.id, e.attribute, e.before, e.after) for e in made.elements] == [
        ("", "T32.2", "EBand", "0", "1"), ("", "T32.2", "EBandLock", "False", "True")]
    assert made.tags == ("R1N0",) and made.renest == (MATERIAL,) and made.fixer == "countertop_banding"
    assert apply_patch(made.patch, h.TOPS_WRONG) == h.TOPS_FIXED
    assert only_its_elements_changed(COUNTERTOP_BANDING, h.TOPS_WRONG, made)
    assert made.words[0] == ('Room 1: 1 countertop (37" (939.8 mm) by 17" (431.8 mm)) isn\'t taped and locked on all four'
                             " edges; the fix tapes and locks every edge in banding slot 1")


def test_the_second_colour_is_the_one_jims_crosscheck_checks_with(env) -> None:
    jim = h.FakeEbTops()
    plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, jim)

    assert [args[:-1] for args in jim.calls] == [["3763"], ["--check", "3763"], ["--check", "3763"]]


@pytest.mark.parametrize("crosscheck", [
    "CHECKS = []\n",
    'CHECKS = [("eb_tops", lambda j, c: product_rooms(j), False)]\n',
    'CHECKS = [("eb_tops", lambda j, c: ["--check", "3763", "--all"] + product_rooms(j), False)]\n',
    'CHECKS = [("eb_tops", lambda j, c: ["--check", COLOUR] + product_rooms(j), False)]\n',
    "CHECKS = [\n",
], ids=["none", "no list", "another flag", "a name", "broken"])
def test_a_second_colour_that_cant_be_read_from_crosscheck_plans_nothing(env, crosscheck: str) -> None:
    (env["snapshot"].path / "scripts" / "crosscheck.py").write_text(crosscheck, encoding="utf-8")
    with pytest.raises(FixRefused, match="second colour"):
        plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, h.FakeEbTops())


def test_two_countertops_are_each_named_and_a_moved_slot_is_said(env) -> None:
    wrong = h.countertop_room(back=("3", "True"), second=("0", "False"))
    fixed = h.countertop_room(back=("1", "True"), second=("1", "True"))
    lines = {wrong: [h.TOPS_LINE, h.TOPS_LINE_33], fixed: []}

    class Jim(h.FakeEbTops):
        def __call__(self, snapshot, script, args, **kw):
            if args[0] == "--check":
                self.lines = lines[Path(args[-1]).read_bytes()]
            return super().__call__(snapshot, script, args, **kw)

    made = plan(env, COUNTERTOP_BANDING, wrong, Jim(fixes={wrong: fixed}))
    assert {e.id.split(".")[0] for e in made.elements} == {"T32", "T33"} and made.tags == ("R1N0",)
    assert made.words[0].startswith("Room 1: 2 countertops (")
    assert made.words[0].endswith("; the fix tapes and locks every edge in banding slot 1, moving tape that was in slot 3")


def test_eb_tops_changing_a_parts_banding_or_a_slots_colour_is_refused(env) -> None:
    part = h.countertop_room(back=("1", "True"), part_band="6")
    with pytest.raises(FixRefused, match="part's banding"):
        plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, h.FakeEbTops(fixes={h.TOPS_WRONG: part}))
    slot = h.countertop_room(back=("1", "True"), slot6="3763")
    with pytest.raises(FixRefused, match="more than"):
        plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, h.FakeEbTops(fixes={h.TOPS_WRONG: slot}))


def test_an_eb_tops_line_about_a_part_is_one_ng_runner_doesnt_read_yet(env) -> None:
    part_line = "   Room1.des    WM Panel     FEnd           ORE slot 6  Top:1->6"
    jim = h.FakeEbTops(lines=[part_line])
    assert COUNTERTOP_BANDING.flags(env["snapshot"], h.TOPS_WRONG, room_name="Room1.des", work=env["work"],
                                    run=jim) is None


def test_a_countertop_flipped_by_hand_in_a_nest_is_named(env) -> None:
    made = plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, h.FakeEbTops(),
                nests={MATERIAL: h.nest([("R1N0", True), ("R1C7", False)]), "1 in Am Lam Wh": h.nest([("R2N0", False)])})
    assert made.hand_flipped == ("R1N0",) and made.renest == (MATERIAL,)


def test_a_countertop_fix_is_written_undone_to_the_exact_bytes_and_judged(env) -> None:
    jim = h.FakeEbTops()
    made = plan(env, COUNTERTOP_BANDING, h.TOPS_WRONG, jim)
    record = get_fix(env["store"], apply_fix(made, approved=made.fingerprint, programs={}, **write_args(env)).fix.id)
    saved = h.countertop_room(back=("1", "True"), count="3")

    held = survival(record, COUNTERTOP_BANDING, env["snapshot"], room_bytes=saved, room_name="Room1.des",
                    work=env["work"], sessions=(), store=env["store"], at=T0, run=jim, keep=False)
    assert held.code == "held" and held.words == "The fix on the countertops held after the save"
    gone = saved.replace(b'IDTag="32"', b'IDTag="34"')
    assert survival(record, COUNTERTOP_BANDING, env["snapshot"], room_bytes=gone, room_name="Room1.des",
                    work=env["work"], sessions=(), store=env["store"], at=T0, run=jim,
                    keep=False).code == "could_not_verify"

    assert undo_fix(record, COUNTERTOP_BANDING, programs={}, **write_args(env)).ok
    assert env["room"].read_bytes() == h.TOPS_WRONG


# ----- reading a room's points


def test_a_point_is_found_by_its_place_wherever_the_bytes_around_it_moved() -> None:
    longer = h.WM_WRONG.replace(b'Name="Made up"', b'Name="Made up, and longer"')
    element = Element("7", "0.3", "EBand", "0", "1")

    for data in (h.WM_WRONG, longer):
        start, end = des.point_span(data, element.cab, element.id, element.attribute)
        assert data[start:end] == b"0" and data[:start].endswith(b'EBand="')
    assert des.point_span(h.WM_WRONG, "7", "0.9", "EBand") is None
    assert des.point_span(h.WM_WRONG, "8", "0.3", "EBand") is None
    assert des.point_span(h.TOPS_WRONG, "", "T32.2", "EBand") is not None
    assert des.point_span(h.TOPS_WRONG, "", "T31.2", "EBand") is None


def test_two_products_with_one_cabno_are_never_located() -> None:
    twice = h.TOP_WRONG.replace(b'CabNo="6"', b'CabNo="4"')
    assert TOP_LINE.locate(twice, Element("4", "", "Elev", "1127.1", "928")) is None
    assert WM_BANDING.locate(h.WM_WRONG.replace(b"</Products>", b'<Product CabNo="7"></Product></Products>'),
                             Element("7", "0.3", "EBand", "0", "1")) is None


def test_the_fixers_dont_compare_their_hooks() -> None:
    assert WmBanding(call=lambda *a, **k: None) == WM_BANDING and CountertopBanding() == COUNTERTOP_BANDING


def test_a_changed_point_whose_name_another_point_has_is_refused_and_one_elsewhere_isnt(env) -> None:
    untaped = h.point(0, "939.7999", "431.8", "0", "False").strip().encode()
    taped = h.point(0, "939.7999", "431.8", "1", "True").strip().encode()
    spare = (b'    <Product ProdName="Spare" CabNo="9"><CabProdPart Name="P"><PartShapeXml Name="">'
             b'<ShapePoint ID="0" EBand="0" /></PartShapeXml></CabProdPart></Product>' + h.CRLF.encode())
    other = h.TOPS_WRONG.replace(b"  </Products>", spare * 2 + b"  </Products>")     # C9 twice, its point too
    made = plan(env, COUNTERTOP_BANDING, other, h.FakeEbTops(fixes={other: other.replace(untaped, taped)}))
    assert {e.id for e in made.elements} == {"T32.2"}

    top = h.TOPS_WRONG.split(b"  <Tops>" + h.CRLF.encode())[1].split(b"  </Tops>")[0]
    dup = h.TOPS_WRONG.replace(b"  </Tops>", top + b"  </Tops>")                       # T32 twice
    with pytest.raises(FixRefused, match="another point has too"):
        plan(env, COUNTERTOP_BANDING, dup, h.FakeEbTops(fixes={dup: dup.replace(untaped, taped, 1)}))
