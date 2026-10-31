"""
The words of NG Runner's own takeoff (messages/templates.py): the order heads-up made from it, the
"could not verify" heads-up that replaces a partial list, and the lines the board, the digest and
the history show. Every message goes through the lint, and none carries anything but codes,
numbers and fixed words. Job names are made up.
"""
from __future__ import annotations

from datetime import date

import pytest

from ngrunner.messages import Message
from ngrunner.messages import templates as t
from ngrunner.messages.lint import check_line, lint

JOB = "Testerson_101426"
WH_3_4 = {"colour": "WH", "thk_in": 0.75, "parts": 8, "sqft": 30.33, "sheet_in": [49.0, 97.0],
          "sheets_with_waste": 2, "calc_sheets_32": 0.9478, "tape_lf": 26.0}
WH_1_4 = {"colour": "WH", "thk_in": 0.25, "parts": 3, "sqft": 24.0, "sheet_in": [61.0, 109.0],
          "sheets_with_waste": 1, "calc_sheets_32": 0.75, "tape_lf": 0.0}
AP_3_4 = {"colour": "AP", "thk_in": 0.75, "parts": 4, "sqft": 9.33, "sheet_in": [49.0, 97.0],
          "sheets_with_waste": 1, "calc_sheets_32": 0.2916, "tape_lf": 12.4}


def clean(message: Message) -> Message:
    assert lint(message) == [], message.text
    return message


# ----- a bucket in words


@pytest.mark.parametrize("colour, words", [
    ("WH", "Egger Classic White"),          # one IC colour to its brand: the brand names it
    ("CC", "Egger Black U999"),
    ("IV", "Arauco"),
    ("AP", "Tafisa (IC AP)"),               # a brand of many colours: the IC code says which
    ("SU", "Tafisa (IC SU)"),
    ("ZZ", "IC colour ZZ"),                 # never a guess at a brand
])
def test_a_colour_reads_as_its_brand_and_its_ic_code_where_the_brand_has_many(colour, words):
    assert t.takeoff_material(colour) == words


@pytest.mark.parametrize("inches, words", [(0.25, "1/4"), (0.75, "3/4"), (1.0, '1"'),
                                           (1.125, '1-1/8"'), (0.375, "3/8")])
def test_a_thickness_reads_as_jims_lines_read_it(inches, words):
    assert t._thickness_words(inches) == words      # private: the board and messages use it


def test_the_board_lines_are_sheets_by_colour_and_thickness_then_tape():
    assert t.takeoff_lines([WH_3_4, WH_1_4, AP_3_4]) == (
        "2 sheets 3/4 Egger Classic White",
        "1 sheet 1/4 Egger Classic White",
        "1 sheet 3/4 Tafisa (IC AP)",
        "26 feet of tape for 3/4 Egger Classic White",
        "12 feet of tape for 3/4 Tafisa (IC AP)",
    )
    assert all(not check_line(line) for line in t.takeoff_lines([WH_3_4, WH_1_4, AP_3_4]))


def test_lines_that_could_not_be_verified_are_counted_never_quoted():
    assert t.could_not_verify_lines(1) == "could not verify 1 line"
    assert t.could_not_verify_lines(3) == "could not verify 3 lines"


# ----- the heads-up made from NG Runner's own takeoff


def heads_up(*, revised: bool = False, flags: tuple[str, ...] = ()) -> Message:
    return t.takeoff_heads_up(JOB, buckets=[WH_3_4, WH_1_4, AP_3_4], flags=flags,
                              waste_percent=10, revised=revised)


def test_the_heads_up_lists_sheets_and_tape_labelled_as_an_estimate():
    message = clean(heads_up())

    assert message.kind == "heads_up"
    assert message.title == f"{JOB}: order heads-up (takeoff estimate; the nest decides)"
    assert message.lines[0].startswith("Takeoff estimate; the nest decides.")
    assert "10% waste" in message.lines[0] and "IC summary" in message.lines[0]
    assert message.lines[1:] == (
        f"1. {JOB}: 2 sheets 3/4 Egger Classic White, 1 sheet 1/4 Egger Classic White, 1 sheet"
        " 3/4 Tafisa (IC AP) (takeoff estimate; the nest decides).",
        f"2. {JOB}: 26 feet of tape for 3/4 Egger Classic White (takeoff estimate; the nest"
        " decides).",
        f"3. {JOB}: 12 feet of tape for 3/4 Tafisa (IC AP) (takeoff estimate; the nest decides).",
    )


def test_a_revised_heads_up_says_so_and_carries_its_flags():
    message = clean(heads_up(revised=True, flags=("Not all Classic White: Tafisa (IC AP)",)))

    assert message.title == f"{JOB}: revised order heads-up (takeoff estimate; the nest decides)"
    assert message.lines[0].startswith("Revised takeoff estimate; the nest decides.")
    assert message.lines[-1] == f"4. {JOB}: Not all Classic White: Tafisa (IC AP)."


def test_a_bucket_with_no_tape_gets_no_tape_line():
    message = clean(t.takeoff_heads_up(JOB, buckets=[WH_1_4], flags=(), waste_percent=10,
                                       revised=False))
    assert [line for line in message.lines if "tape" in line] == []


def test_a_bucket_with_no_sheet_count_is_never_listed_as_a_guess():
    with pytest.raises(ValueError):
        t.takeoff_heads_up(JOB, buckets=[{**WH_3_4, "sheets_with_waste": None}], flags=(),
                           waste_percent=10, revised=False)


# ----- a takeoff that isn't complete: no list at all


@pytest.mark.parametrize("revised", [False, True])
def test_a_takeoff_that_is_not_complete_says_could_not_verify_and_how_many_lines(revised):
    message = clean(t.takeoff_unverified(JOB, lines=3, revised=revised))

    assert message.kind == "heads_up"
    assert "could not verify the takeoff" in message.title
    assert ("revised" in message.title) is revised
    assert "could not verify 3 lines" in message.text
    assert "sheet" not in message.text and "tape" not in message.text   # never a partial list
    assert all(line.startswith(("1. ",)) or not line[:1].isdigit() for line in message.lines)


def test_one_line_reads_as_one():
    assert "could not verify 1 line of its takeoff," in clean(t.takeoff_unverified(JOB, lines=1,
                                                                    revised=False)).text


def test_both_have_a_fixed_template_that_carries_no_outside_data():
    fixed = t.fixed("heads_up", day=date(2026, 10, 31))
    assert lint(fixed) == [] and JOB not in fixed.text


# ----- the history's line and the digest's clause


def test_the_history_line_says_what_the_takeoff_is_in_plain_words():
    assert t.takeoff_made_words(state="made", complete=True, could_not_verify=0,
                                board_parts=8, buckets=1, tape_lf=26.0) == (
        "Made its takeoff from the IC summary: 8 board parts in 1 bucket and 26 feet of tape"
        " (takeoff estimate; the nest decides).")
    assert t.takeoff_made_words(state="made", complete=False, could_not_verify=2,
                                board_parts=8, buckets=1, tape_lf=26.0) == (
        "Made its takeoff from the IC summary, but could not verify 2 lines, so no order list"
        " goes out from it.")
    assert t.takeoff_made_words(state="in_chat", branch="garage") == (
        "Its takeoff stays in chat: a garage job, until BUILD's garage rule is ported.")
    assert t.takeoff_made_words(state="in_chat", branch="mixed") == (
        "Its takeoff stays in chat: a closet and garage job, until BUILD's garage rule is"
        " ported.")
    assert t.takeoff_made_words(state="failed") == (
        "NG Runner could not read the IC summary for its takeoff, so the chat's takeoff is used.")
    for words in (t.takeoff_made_words(state="made", complete=True, could_not_verify=0,
                                       board_parts=8, buckets=1, tape_lf=26.0),
                  t.takeoff_made_words(state="failed")):
        assert not check_line(words)


def test_the_digest_clause_for_a_takeoff():
    assert t.takeoff_digest([WH_3_4], complete=True, could_not_verify=0) == (
        "takeoff: 2 sheets 3/4 Egger Classic White and 26 feet of tape (takeoff estimate; the"
        " nest decides)")
    assert t.takeoff_digest([WH_3_4], complete=False, could_not_verify=2) == (
        "takeoff: could not verify 2 lines")


# ----- white with something 1in or thicker: RULES makes its room AMWH, and rooms aren't known


def test_white_on_a_takeoff_with_a_white_part_1in_or_thicker_names_both_brands_never_one():
    one_inch = {**WH_3_4, "thk_in": 1.0, "sheets_with_waste": 1, "tape_lf": 4.0}
    lines = t.takeoff_lines([WH_3_4, one_inch, AP_3_4])

    assert lines[:3] == ("2 sheets 3/4 AMWH or Egger Classic White (IC WH)",
                         '1 sheet 1" AMWH or Egger Classic White (IC WH)',
                         "1 sheet 3/4 Tafisa (IC AP)")
    assert t.takeoff_material("WH", thick_white=True) == "AMWH or Egger Classic White (IC WH)"
    assert t.takeoff_material("AP", thick_white=True) == "Tafisa (IC AP)"
    message = clean(t.takeoff_heads_up(JOB, buckets=[WH_3_4, one_inch], flags=(),
                                       waste_percent=10, revised=False))
    assert "AMWH or Egger Classic White (IC WH)" in message.text


# ----- a heads-up from the chat's takeoff says so (Sulaiman's ruling, 2026-10-31)

P1B = Message("heads_up", f"{JOB}: order heads-up (takeoff estimate; the nest decides)", (
    "Takeoff estimate; the nest decides. Counted as sheets with 10% waste.",
    f"1. {JOB}: 12 sheets 3/4 Egger Classic White (takeoff estimate; the nest decides)."))


def test_a_heads_up_from_the_chats_takeoff_says_so_on_its_first_line():
    marked = clean(t.from_the_chats_takeoff(P1B))

    assert marked.lines[0] == ("Takeoff estimate; the nest decides. Counted as sheets with 10%"
                               " waste. From the chat's takeoff.")
    assert (marked.title, marked.kind, marked.lines[1:]) == (P1B.title, P1B.kind, P1B.lines[1:])


def test_one_sent_because_ng_runners_could_not_verify_says_how_many_lines():
    marked = clean(t.from_the_chats_takeoff(P1B, could_not_verify=3))

    assert marked.lines[0].endswith("From the chat's takeoff, since NG Runner's own takeoff could"
                                    " not verify 3 lines.")


def test_the_digest_says_when_the_chats_list_is_the_heads_up():
    assert t.takeoff_digest([WH_3_4], complete=False, could_not_verify=2, chat=True) == (
        "takeoff: could not verify 2 lines, so the order heads-up is the chat's takeoff")
    assert not check_line(t.CHATS_TAKEOFF)
