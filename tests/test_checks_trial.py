"""
The replay trial's comparison (ngrunner/checks/trial.py): NG Runner's check against what the chat reported,
finding by finding, on the checkers the chat's output speaks for. Synthetic, in the shapes Jim's crosscheck
prints (tests/test_checks.py); tests/test_trial_real.py runs it on the real jobs.
"""
from __future__ import annotations

from test_checks import CLEAN, PLAN, crosscheck_file, read
from ngrunner.checks import findings as f
from ngrunner.checks import trial

NOTCH = ("Room2.des\n"
         "   !! C1   WM Panel 48 Thru Radius  notch on face True, line bores on False - NOTCH ON THE WRONG FACE\n"
         "   !! C3   WM Panel 48 Thru         notch on face True, line bores on False - NOTCH ON THE WRONG FACE")
PANELS = ("IC board parts 18   Mozaik cutlist 17\n\n-- SH: IC 10  Mozaik 9\n"
          "   MISSING (356, 370.8) x2   SH 14x14.6x3/4 FIX WH\n-- VT: IC 5  Mozaik 5")


def ours(sections=CLEAN, *, flagged=(), library=True) -> f.Findings:
    return f.findings_of(read(sections, flagged=flagged, library=library), q1_settled=True)


def chat(covers, *found) -> trial.ChatReport:
    return trial.ChatReport(tuple(covers), frozenset(trial.Finding(*one) for one in found))


def test_each_part_an_item_names_is_one_finding_and_no_could_not_verify_is_one() -> None:
    found = ours({**CLEAN, "notch_check": NOTCH, "panel_xcheck": PANELS}, flagged=("notch_check", "panel_xcheck"),
                 library=False)

    assert trial.findings_in(found) == {trial.Finding("notch_check", "notch_face", "R2C1"),
                                        trial.Finding("notch_check", "notch_face", "R2C3"),
                                        trial.Finding("panel_xcheck", "missing_part", "356.0x370.8")}
    assert "wm_variant_check" in trial.unverified_checkers(found)        # its library isn't here: no finding


def test_both_ours_only_and_chat_only_on_the_checkers_the_chat_speaks_for() -> None:
    found = ours({**CLEAN, "notch_check": NOTCH, "panel_xcheck": PANELS}, flagged=("notch_check", "panel_xcheck"))
    said = chat(("notch_check",), ("notch_check", "notch_face", "R2C1"), ("notch_check", "wm_banding", "R2"),
                ("panel_xcheck", "missing_part", "356.0x370.8"), (trial.OUTSIDE, "item", "1"))

    compared = trial.compare(found, said)

    assert compared.both == {trial.Finding("notch_check", "notch_face", "R2C1")}
    assert compared.ours_only == {trial.Finding("notch_check", "notch_face", "R2C3")}   # the panel set isn't covered
    assert compared.chat_only == {trial.Finding("notch_check", "wm_banding", "R2")}
    assert compared.outside == {trial.Finding(trial.OUTSIDE, "item", "1")} and not compared.agrees


def test_what_the_chat_found_where_ng_runner_could_not_verify_is_neither_a_miss_nor_a_clean() -> None:
    found = ours(library=False)
    said = chat(("wm_variant_check",), ("wm_variant_check", "wm_variant", "R1C2"))

    compared = trial.compare(found, said)

    assert compared.unverified == {trial.Finding("wm_variant_check", "wm_variant", "R1C2")}
    assert not compared.chat_only and not compared.ours_only


def test_jims_crosscheck_file_as_the_chat_saved_it_reads_without_the_chats_notes_below_it() -> None:
    text = crosscheck_file({**CLEAN, "notch_check": NOTCH}, flagged=("notch_check",))
    text += "\n===== RECHECK AFTER THE FIX - CLEAN\n   !! C9   something the chat wrote by hand\n"

    said, found = trial.chat_crosscheck(text, plan=PLAN, q1_settled=True)

    assert said.covers == tuple(p.name for p in PLAN if not p.advisory)
    assert said.findings == {trial.Finding("notch_check", "notch_face", "R2C1"),
                             trial.Finding("notch_check", "notch_face", "R2C3")}
    assert trial.compare(ours({**CLEAN, "notch_check": NOTCH}, flagged=("notch_check",)), said).agrees


def test_counts_add_up_over_jobs() -> None:
    one = trial.compare(ours({**CLEAN, "notch_check": NOTCH}, flagged=("notch_check",)), chat(("notch_check",)))
    two = trial.compare(ours(), chat(("notch_check",), ("notch_check", "notch_face", "R1C1")))

    assert trial.counts([one, two]) == {"both": 0, "ours_only": 2, "chat_only": 1, "unverified": 0, "outside": 0}


def test_a_size_is_one_part_whichever_way_round_it_is_given() -> None:
    assert trial.size_key(2438.4, 104.75) == trial.size_key(104.8, 2438.4) == "104.8x2438.4"
