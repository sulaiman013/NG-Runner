"""
What a check run found, as items Jim can act on (P3: "Repair items with a ready chat line", "Audit of
chat-built jobs"): ngrunner/checks/findings.py turns the check runner's verdicts into items, each naming
parts by tag (R2C3) and never quoting what a checker printed, and templates.py words them.

The checker output here is synthetic, in the shapes Jim's checkers print (read from their source and
seen on the real Cases A to J), with made-up names: a room called "Testerson - Primary" and a product
called "Testerson Special" stand in for the client details real output carries, and none may reach an
item, its words, its chat line or its fingerprint's neighbours.
"""
from __future__ import annotations

import json
import re

import pytest

from test_checks import CLEAN, PLAN, ROOM1, read
from ngrunner.checks import findings as f
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import lint

JOB = "Testerson_101426"
CLIENT = "Testerson"
ADDRESS = "1234 Fake Street"
ROOM_HEADER = f"{ROOM1}  (Testerson - Primary at {ADDRESS})"


def found(sections=CLEAN, *, flagged=(), kind="check", rooms=None, garage=False, q1_settled=True, **kw):
    """Q1 settled unless a test is about it: until it is, no run can pass (test_until_q1...)."""
    result = read(sections, flagged=flagged, **kw)
    return f.findings_of(result, kind=kind, room_files=rooms, garage=garage, q1_settled=q1_settled)


def items(result: f.Findings, kind: str | None = None) -> list[f.Item]:
    return [item for item in result.items if kind in (None, item.kind)]


def shown(item: f.Item) -> str:
    return " ".join(filter(None, (templates.check_item_words(item), templates.check_chat_line(JOB, item))))


NOTCH = ("Room2.des\n"
         "   !! C1   WM Panel 48 Thru Radius  notch on face True, line bores on False - NOTCH ON THE WRONG FACE\n"
         "   !! C3   Testerson Special        notch on face True, line bores on False - NOTCH ON THE WRONG FACE\n"
         "   !! WM Panel 48 Thru Radius  WM banding not taped + locked on top/front/bottom")
DOUBLED = ("Room1.des\n   ok BR C1   84 BR        NoShelfHoles=True   vertical C4   cams at Y 319,96\n"
           f"   !! C8   48 S Hang 1S         F.Shelf   TWO 20mm HOLES ON TOP OF EACH OTHER (Testerson - Primary):"
           f" X=9.525 Y=260 from '_PAC Titus (Testerson) V2' and X=9.5 Y=260 from '_PAC Titus (Testerson)' - 0.025mm apart\n"
           f"   !! C8   48 S Hang 1S         Top       TWO 20mm HOLES ON TOP OF EACH OTHER (Testerson - Primary):"
           f" X=9.525 Y=37 from '_PAC Titus (Testerson) V2' and X=9.5 Y=37 from '_PAC Titus (Testerson)' - 0.025mm apart\n"
           "        -> two joint fastener templates are both matching these joints. Check the template set.")


# ----- a clean run


def test_a_clean_run_has_no_items_and_reads_passed() -> None:
    result = found()

    assert result.code == PASSED and result.items == ()
    assert result.left_out == ("hinge_check", "sheet_size_check", "br_cam_check --nest")


# ----- repair items: a failed checker becomes items naming parts by tag, with a ready chat line


def test_a_failed_checker_becomes_one_item_per_fault_and_room_naming_its_parts_by_tag() -> None:
    result = found({**CLEAN, "notch_check": NOTCH}, flagged=("notch_check",))

    fixes = items(result, "fix")
    assert result.code == FAILED
    assert [(i.what, i.tags) for i in fixes] == [("notch_face", ("R2C1", "R2C3")), ("wm_banding", ("R2",))]
    notch = fixes[0]
    assert templates.check_item_words(notch) == "R2C1 and R2C3: the notch is on the wrong face"
    assert templates.check_chat_line(JOB, notch) == (
        f"{JOB}: put the notch on its line-bore face on R2C1 and R2C3, then run crosscheck")
    assert notch.settles and notch.n == 1 and fixes[1].n == 2


def test_lines_that_explain_a_fault_belong_to_it_and_make_no_item_of_their_own() -> None:
    result = found({**CLEAN, "br_cam_check": DOUBLED}, flagged=("br_cam_check",))

    item, = items(result, "fix")
    assert (item.what, item.tags) == ("doubled_holes", ("R1C8",))
    assert templates.check_item_words(item) == "R1C8: two holes sit on top of each other"


def test_a_fault_line_no_table_names_is_still_an_item_never_dropped() -> None:
    odd = "Room1.des\n   !! C4   48 DH                something new and strange happened"
    result = found({**CLEAN, "notch_check": odd}, flagged=("notch_check",))

    item, = items(result, "fix")
    assert (item.what, item.tags) == ("other", ("R1C4",)) and result.code == FAILED
    assert templates.check_item_words(item) == "R1C4: the notch face and WM banding check found something to fix"


def test_a_line_bore_off_the_panel_is_fixed_in_mozaik_and_can_never_be_settled() -> None:
    off = (f"{ROOM1}\n   !! C1   FM 84                    line bore OFF THE PANEL at local X=2140 on a"
           " 84\" (2133.6mm) panel - elev 84 3/16\"")
    result = found({**CLEAN, "drill_depth_check": off}, flagged=("drill_depth_check",))

    item, = items(result)
    assert (item.kind, item.what, item.settles) == ("mozaik", "bore_off_panel", False)
    assert templates.check_chat_line(JOB, item) is None
    assert templates.check_item_words(item) == "R1C1: a line bore runs off the panel. Fix it in Mozaik"


def test_many_parts_are_named_eight_at_most_then_how_many_more() -> None:
    lines = "\n".join(f"   !! C{n}   48 DH                notch on face True, line bores on False - NOTCH ON THE"
                      " WRONG FACE" for n in range(1, 12))
    item, = items(found({**CLEAN, "notch_check": f"Room1.des\n{lines}"}, flagged=("notch_check",)))

    assert len(item.tags) == 11
    assert templates.check_item_words(item).startswith(
        "R1C1, R1C2, R1C3, R1C4, R1C5, R1C6, R1C7, R1C8 and 3 more: ")


def test_parts_missing_or_extra_in_the_panel_set_are_named_by_size_in_inches() -> None:
    xcheck = ("IC board parts 22   Mozaik cutlist 22\n\n-- DR: IC 3  Mozaik 2\n"
              "   MISSING (157.5, 447.0) x1   DR Deco 17.6x6.2x3/4 FL LG\n"
              "-- DW: IC 1  Mozaik 2\n   EXTRA   (158.5, 447.8) x1")
    result = found({**CLEAN, "panel_xcheck": xcheck}, flagged=("panel_xcheck",))

    missing, extra = items(result, "fix")
    assert templates.check_item_words(missing) == (
        "1 part on the IC summary is not in Mozaik's cutlist: 6 3/16\" (157.5 mm) by 17 5/8\" (447 mm)")
    assert templates.check_item_words(extra) == (
        "1 part in Mozaik's cutlist is not on the IC summary: 6 1/4\" (158.5 mm) by 17 5/8\" (447.8 mm)")


def test_an_advisory_finding_is_shown_but_never_to_fix_and_never_fails_the_job() -> None:
    advice = (f"Room1.des   (Testerson - Primary)\n   !! wall 3    H=60\"       IC wants 1xLeft"
              "                       built 1xThru")
    result = found({**CLEAN, "ic_code_check": advice}, advisory=("ic_code_check",))

    item, = items(result)
    assert item.kind == "advisory" and not item.settles and result.code == PASSED


# ----- could not verify: what wasn't checked, and why


@pytest.mark.parametrize("checker, text, says", [
    ("panel_xcheck", "Traceback (most recent call last):\n  File \"x.py\", line 9\nKeyError: 'build_mm'",
     "Could not verify the panel set: the check stopped without saying what it found"),
    ("parm_check", "  -- Closets-JobParms.dat                         no counterpart in the reference job\n\n"
                   "RESULT: CLEAN - parameters match the reference job",
     "Could not verify the parameters: the check had no reference job to compare with, so it compared"
     " nothing"),
    ("section_span", CLEAN["section_span"] + "\n   every bay is filled, mostly",
     "Could not verify the section span: the check printed a line NG Runner doesn't know"),
], ids=["a crash", "no reference job", "a line not known"])
def test_a_checker_that_could_not_verify_is_one_item_that_says_what_and_why(checker, text, says) -> None:
    flagged = ("panel_xcheck",) if checker == "panel_xcheck" else ()
    result = found({**CLEAN, checker: text}, flagged=flagged)

    cnv = [i for i in items(result, "could_not_verify") if i.checker == checker]
    assert len(cnv) == 1 and not cnv[0].settles
    assert templates.check_item_words(cnv[0]) == says
    assert result.code == COULD_NOT_VERIFY


def test_a_library_on_a_linux_path_says_so() -> None:
    result = found(library=False)

    item, = [i for i in items(result, "could_not_verify") if i.checker == "wm_variant_check"]
    assert "a path this PC doesn't have" in templates.check_item_words(item)


@pytest.mark.parametrize("checker, text, tags, words, chat", [
    ("top_line", "   Room2.des    WM Panel 48 Thru Radius    H=756     Elev  1371.1 ->  1376.0  (top 2127 -> 2132)\n\n"
                 "would change: 1 WM panels",
     ("R2",), "R2: WM panels are off the room's top line",
     f"{JOB}: put the WM panels in R2 on the room's top line, then run crosscheck"),
    ("outset_check", "  FLAG Room1.des     C7    wall 2_1    Testerson Special          Outset=12.7\n\n"
                     "1 product(s) NOT at Outset 0 - review",
     ("R1C7",), "R1C7: the product isn't at Outset 0, so it sits off the wall",
     f"{JOB}: set R1C7 to Outset 0, then run crosscheck"),
    ("eb_tops", "   Room3.des    COUNTERTOP 24.000in x 96.000in  four edges, slot 1\n\nwould change: 1 part(s)/slot(s)",
     ("R3",), "R3: the countertop or second-colour banding isn't as Jim's banding rules say",
     f"{JOB}: band each countertop in R3 on all four edges, and each second-colour part in its own colour, then"
     " run crosscheck"),
], ids=["top line", "outset", "banding"])
def test_a_finding_jims_crosscheck_doesnt_flag_is_one_to_fix_naming_where(checker, text, tags, words, chat) -> None:
    """Question 26: NG Runner flags these itself, as items to fix with a chat line, which Jim may settle
    for a job (an Outset on purpose, say)."""
    result = found({**CLEAN, checker: text})

    item, = [i for i in items(result) if i.checker == checker]
    assert (item.kind, item.tags, item.settles) == ("fix", tags, True)
    assert templates.check_item_words(item) == words
    assert templates.check_chat_line(JOB, item) == chat
    assert lint(Message("check_result", f"{JOB} checked 9:42 PM", (f"1. {templates.check_item_line(JOB, item)}",))) == []
    assert result.code == FAILED


def test_a_crosscheck_that_couldnt_be_read_is_one_item_for_the_whole_run() -> None:
    result = found(exit_code=2)

    item, = items(result)
    assert item.kind == "could_not_verify" and item.checker == ""
    assert templates.check_item_words(item) == (
        "Could not verify any check: Jim's crosscheck's exit code doesn't fit what it wrote")


def test_a_garage_job_says_its_hinge_count_was_not_checked() -> None:
    result = found(garage=True)

    item, = items(result)
    assert item.kind == "could_not_verify" and item.checker == "hinge_check"
    assert result.code == COULD_NOT_VERIFY
    assert templates.check_item_words(item) == (
        "Could not verify the hinge count: Jim's crosscheck doesn't run his hinge check")


# ----- Q1: the parameters can't pass or fail on the top until Jim settles how it is numbered


def test_until_q1_is_settled_the_parameters_never_pass() -> None:
    template = ("  OK NG Closets-JobParms.dat                      identical\n\n"
                "RESULT: CLEAN - parameters match the reference job")
    result = found({**CLEAN, "parm_check": template}, q1_settled=False)

    item, = items(result)
    assert item.checker == "parm_check" and item.kind == "could_not_verify" and not item.settles
    assert templates.check_item_words(item) == (
        "Could not verify the parameters: how the top is numbered waits for Jim's answer to Q1")
    assert result.code == COULD_NOT_VERIFY
    assert found({**CLEAN, "parm_check": template}, q1_settled=True).code == PASSED


def test_a_parameter_that_differs_on_the_top_is_could_not_verify_and_another_is_to_fix() -> None:
    drift = ("  !! NG Closets-JobParms.dat                     2 parameter(s) differ\n"
             "        BTop            Top                 this job = 4 reference = 1 (want 1)\n"
             "        SysHolesW       Holes               this job = 4 reference = 1 (want 1)\n\n"
             "RESULT: 1 parameter file(s) drifted - fix before building")
    result = found({**CLEAN, "parm_check": drift}, flagged=("parm_check",), q1_settled=False)

    top, = [i for i in items(result, "could_not_verify") if i.checker == "parm_check"]
    other, = [i for i in items(result, "fix") if i.checker == "parm_check"]
    assert not top.settles and other.settles and result.code == FAILED
    assert templates.check_item_words(other) == "1 parameter differs from the template job's"


# ----- the audit of a chat-built job: the panel set, the top line, the operations, the parameters


def test_an_audit_reads_only_the_panel_set_top_line_operations_and_parameters() -> None:
    sections = {**CLEAN, "notch_check": NOTCH,
                "op_schema_check": "  !! OperationHole in Room1.des\n       INVENTED attribute(s): Zed\n"
                                   "       Mozaik writes: X, Y\n       we wrote:      <OperationHole Zed=\"1\" />\n"
                                   "  checked 12 user-written operation element(s) across 2 file(s): 1 REJECTED"}
    result = found(sections, flagged=("notch_check", "op_schema_check"), kind="audit",
                   rooms={"Room0.des": room(), "Room1.des": room()})

    assert {v.checker for v in result.verdicts} == set(f.AUDIT_CHECKERS) | {f.ROOM_FILES}
    assert [i.checker for i in items(result, "fix")] == ["op_schema_check"]      # never the sections' checks
    assert result.code == FAILED
    assert f.AUDIT_CHECKERS == ("panel_xcheck", "top_line", "op_schema_check", "parm_check")


# ----- NG Runner's own check: each room written as Mozaik writes it


def room(text: str = "<Room Quan=\"1\" />", *, line_end: bytes = b"\r\n", version: bytes = b"15") -> bytes:
    return version + line_end + b'<?xml version="1.0" encoding="utf-8"?>' + line_end + text.encode()


def test_a_room_with_line_ends_mozaik_doesnt_write_is_to_fix() -> None:
    result = found(rooms={"Room0.des": room(), "Room1.des": room(line_end=b"\n")})

    item, = items(result)
    assert (item.checker, item.kind, item.tags) == (f.ROOM_FILES, "fix", ("R1",))
    assert templates.check_item_words(item) == "R1: the room file isn't written the way Mozaik writes it"
    assert result.code == FAILED


def test_a_room_in_a_version_never_seen_is_could_not_verify_not_a_fault() -> None:
    result = found(rooms={"Room0.des": room(), "Room1.des": room(version=b"16")})

    item, = items(result)
    assert (item.checker, item.kind) == (f.ROOM_FILES, "could_not_verify")


def test_rooms_written_as_mozaik_writes_them_pass_quietly() -> None:
    assert found(rooms={"Room0.des": room(), "Room1.des": room()}).code == PASSED


# ----- fingerprints: what a settle is bound to


def test_a_fingerprint_is_the_same_for_the_same_finding_and_changes_with_its_values() -> None:
    once = items(found({**CLEAN, "notch_check": NOTCH}, flagged=("notch_check",)), "fix")[0]
    again = items(found({**CLEAN, "notch_check": NOTCH}, flagged=("notch_check",)), "fix")[0]
    moved = items(found({**CLEAN, "notch_check": NOTCH.replace("C3 ", "C5 ")}, flagged=("notch_check",)),
                  "fix")[0]

    assert once.fingerprint == again.fingerprint
    assert moved.fingerprint != once.fingerprint
    assert re.fullmatch(r"[0-9a-f]{64}", once.fingerprint)


# ----- no client detail, anywhere an item goes


def test_no_client_detail_reaches_an_item_its_words_its_chat_line_or_its_stored_form() -> None:
    sections = {**CLEAN, "notch_check": NOTCH, "br_cam_check": DOUBLED,
                "section_span": f"{ROOM1}\n   !! C3   Testerson Special        starts at 12.000, 6.350mm"
                                " GAP after C2",
                "section_align": f"{ROOM_HEADER}\n\n  WALL 2_1   panel top line(s) = 96\" (2438.4mm)\n"
                                 "    !! C7   Testerson Special        Elev=0.0000     H=2132.0    -> top 2132.0 is"
                                 " +0.94mm vs the panels' 2131.1\n        suggested Elev 0.0 (grid) / -0.9 (top"
                                 " flush)\n\n  --- bore rows on the verticals ---\n    all bore rows clean"}
    result = found(sections, flagged=("notch_check", "br_cam_check", "section_span", "section_align"))

    stored = json.dumps(f.to_json(result))
    words = " ".join(shown(item) for item in result.items).replace(JOB, "<the job>")
    assert {item.what for item in result.items} >= {"span", "top_off", "doubled_holes", "notch_face"}
    for detail in (CLIENT, ADDRESS, "Special", "Titus", "PAC"):
        assert detail not in stored and detail not in words and detail not in repr(result)
    assert f.from_json(f.to_json(result)) == result


def test_every_item_word_and_chat_line_passes_jims_format() -> None:
    sections = {**CLEAN, "notch_check": NOTCH, "br_cam_check": DOUBLED,
                "panel_xcheck": "IC board parts 22   Mozaik cutlist 22\n\n-- DR: IC 3  Mozaik 2\n"
                                "   MISSING (157.5, 447.0) x2   DR Deco 17.6x6.2x3/4 FL LG"}
    result = found(sections, flagged=("notch_check", "br_cam_check", "panel_xcheck"), library=False, garage=True)

    lines = tuple(f"{item.n}. {templates.check_item_line(JOB, item)}" for item in result.items)
    message = Message("check_result", f"{JOB} checked 9:42 PM (trial)", lines)
    assert lint(message) == []


def test_every_checker_in_the_plan_has_plain_words() -> None:
    assert {p.name for p in PLAN} | {"hinge_check", f.ROOM_FILES} <= set(templates.CHECKER_WORDS)
    assert all("_" not in words for words in templates.CHECKER_WORDS.values())


# ----- the command box's "check <job>", and a close prompt's toast button


def test_check_job_in_the_command_box_names_one_job_whole(tmp_path) -> None:
    from datetime import datetime, timezone

    from ngrunner.checks import jobchecks
    from ngrunner.store import ensure_job, open_store

    store = open_store(tmp_path / "ngrunner.db")
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    ensure_job(store, "4242", JOB, now=now)
    ensure_job(store, "4343", "Fakeley_101426", now=now)

    assert jobchecks.check_command(store, f"check {JOB}") == "4242"
    assert jobchecks.check_command(store, f"  CHECK   {JOB.lower()} ") == "4242"
    assert jobchecks.check_command(store, "check Testerson") == ""          # a part of a name is no job
    assert jobchecks.check_command(store, "check Nobody_101426") == ""
    assert jobchecks.check_command(store, "TES-1 Closet") is None
    assert jobchecks.check_command(store, "pause") is None
    assert jobchecks.prompt_choice(f"{jobchecks.PROMPT_ARG} 7 2") == (7, 2)
    assert jobchecks.prompt_choice("TES-1 Closet") is None
