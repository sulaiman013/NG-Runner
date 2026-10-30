"""The 6:30 AM digest: owner items first, one line per job touched, open cards back at 1, 3 and
7 days with their age, and Your list; a quiet night gets one line (design section 4)."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from ngrunner.cards import CardSpec, new_card
from ngrunner.digest import DigestView, JobLine, compose, reminders
from ngrunner.messages import templates
from ngrunner.messages.lint import lint

NOW = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)     # Fri 10/30 6:30 AM Pacific


def open_card(kind, *, job=None, row=None, age=timedelta(0), number=1, reminded=()):
    choices = {"which_job": ("Dummitt_101426", "None of these"),
               "closet_or_garage": ("Closet", "Garage")}.get(kind, ("Keep trying", "Stop trying"))
    card = new_card(CardSpec(kind, job=job, row=row, text="Waiting on Jim.", choices=choices),
                    created_at=NOW - age, allocate=lambda prefix: number)
    return dataclasses.replace(card, reminded_at=tuple(reminded))


def test_the_designs_digest_example_comes_out_word_for_word():
    view = DigestView(
        now=NOW,
        owner=("Dummitt_101426 installs 11/3, not locked", "Open DO NOT CUT: Testova 3/4 Walnut"),
        jobs=(
            JobLine("Testerson_101426", "built except Closet 3 wall 2 (radius position not"
                                        " confirmed). Card TES-2"),
            JobLine("Fakeley_102726", "garage or closet? Card FAK-1. Nothing built yet"),
            JobLine("Mockford_061626", "not run. Mozaik was open at 1:30 AM. Runs tonight, or"
                                       " tap Build now"),
        ),
        cards=(open_card("closet_or_garage", job="Testerson_101426", age=timedelta(hours=7),
                         number=2),
               open_card("closet_or_garage", job="Fakeley_102726", age=timedelta(hours=3))),
        open_items=3, oldest_item=NOW - timedelta(days=2, hours=1),
    )
    message = compose(view)
    assert message.text == (
        "NG Runner · Fri 10/30\n"
        "For you: Dummitt_101426 installs 11/3, not locked. Open DO NOT CUT: Testova 3/4"
        " Walnut.\n"
        "1. Testerson_101426: built except Closet 3 wall 2 (radius position not confirmed)."
        " Card TES-2.\n"
        "2. Fakeley_102726: garage or closet? Card FAK-1. Nothing built yet.\n"
        "3. Mockford_061626: not run. Mozaik was open at 1:30 AM. Runs tonight, or tap Build"
        " now.\n"
        "Your list: 3 open, oldest 2 days."
    )
    assert message.kind == "digest" and not message.urgent
    assert lint(message) == []


def test_a_quiet_night_gets_one_line():
    message = compose(DigestView(now=NOW))
    assert message.text == ("NG Runner · Fri 10/30\n"
                            "Nothing new since the last list, and nothing is waiting on you.")
    assert message.kind == "quiet_digest" and lint(message) == []


def test_a_quiet_night_with_open_items_still_gets_one_line():
    message = compose(DigestView(now=NOW, open_items=2, oldest_item=NOW - timedelta(days=4)))
    assert message.lines == ("Nothing new since the last list. Your list: 2 open, oldest"
                             " 4 days.",)
    assert lint(message) == []


def test_open_cards_come_back_with_their_age_after_the_jobs():
    row_card = open_card("which_job", row="Dummitt", age=timedelta(days=3, hours=1),
                         reminded=(NOW - timedelta(days=2),))          # back at 1 day already
    garage = open_card("closet_or_garage", job="Mockford_101426", age=timedelta(days=1))
    young = open_card("fetch_failed", job="Fakeley_102826", age=timedelta(hours=20))
    message = compose(DigestView(
        now=NOW, jobs=(JobLine("Testerson_101426", "filed 10/29 3:05 PM"),),
        cards=(row_card, garage, young), open_items=3, oldest_item=row_card.created_at))
    assert message.lines == (
        "1. Testerson_101426: filed 10/29 3:05 PM.",
        '2. Card DUM-1, open 3 days: which job is Fab row "Dummitt"?',
        "3. Card MOC-1, open 1 day: is Mockford_101426 a closet or a garage?",
        "Your list: 3 open, oldest 3 days.",
    )
    assert [c.id for c in reminders(DigestView(now=NOW, cards=(row_card, garage, young)))] == [
        "DUM-1", "MOC-1"]
    assert lint(message) == []


def test_a_card_its_job_line_already_names_is_not_repeated_but_counts_as_back():
    garage = open_card("closet_or_garage", job="Mockford_101426", age=timedelta(days=1))
    view = DigestView(now=NOW, jobs=(JobLine("Mockford_101426", "closet or garage? Card"
                                                                " MOC-1"),),
                      cards=(garage,), open_items=1, oldest_item=garage.created_at)
    assert compose(view).lines == ("1. Mockford_101426: closet or garage? Card MOC-1.",
                                   "Your list: 1 open, oldest 1 day.")
    assert [c.id for c in reminders(view)] == ["MOC-1"]


def test_every_kind_of_card_reads_well_when_it_comes_back():
    cards = (
        open_card("which_job", row="Dummitt", age=timedelta(days=7)),
        open_card("closet_or_garage", job="Mockford_101426", age=timedelta(days=7)),
        open_card("fetch_failed", job="Fakeley_102826", age=timedelta(days=7)),
        open_card("ic_changed", job="Testerson_101426", age=timedelta(days=7)),
        open_card("row_changed", job="Testova_102226", row="Testova", age=timedelta(days=7)),
    )
    message = compose(DigestView(now=NOW, cards=cards, open_items=5,
                                 oldest_item=NOW - timedelta(days=7)))
    assert message.lines == (
        '1. Card DUM-1, open 7 days: which job is Fab row "Dummitt"?',
        "2. Card MOC-1, open 7 days: is Mockford_101426 a closet or a garage?",
        "3. Card FAK-1, open 7 days: still no complete IC summary for Fakeley_102826.",
        "4. Card TES-1, open 7 days: the IC summary of Testerson_101426 changed; which render"
        " governs?",
        '5. Card TES-1, open 7 days: Fab row "Testova" changed after Testova_102226 was filed;'
        " which job is it?",
        "Your list: 5 open, oldest 7 days.",
    )
    assert lint(message) == []


def test_ages_read_naturally_and_an_unknown_age_is_left_out():
    def your_list(**kw) -> str:
        return compose(DigestView(now=NOW, owner=("Paused",), **kw)).lines[-1]

    assert your_list(open_items=1, oldest_item=NOW - timedelta(hours=5)) == (
        "Your list: 1 open, oldest under a day.")
    assert your_list(open_items=1, oldest_item=NOW - timedelta(days=1, hours=2)) == (
        "Your list: 1 open, oldest 1 day.")
    assert your_list(open_items=2) == "Your list: 2 open."
    assert your_list() == "Your list: nothing open."


def test_the_header_is_the_pacific_day():
    late = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)   # 11 PM Fri 10/30 in Pacific
    assert compose(DigestView(now=late)).title == "NG Runner · Fri 10/30"


def test_owner_items_come_first_on_one_line():
    message = compose(DigestView(
        now=NOW, owner=("Testerson_101426 installs Tue 11/3 and has no IC summary filed yet",
                        "The Fab Schedule export is over 30 minutes old."),
        jobs=(JobLine("Fakeley_102826", "filed 10/29 3:05 PM."),)))
    assert message.lines == (
        "For you: Testerson_101426 installs Tue 11/3 and has no IC summary filed yet. The Fab"
        " Schedule export is over 30 minutes old.",
        "1. Fakeley_102826: filed 10/29 3:05 PM.",
        "Your list: nothing open.",
    )
    assert lint(message) == []


def test_a_late_digest_says_so_first_even_after_a_quiet_night():
    late = datetime(2026, 10, 30, 15, 12, tzinfo=timezone.utc)      # 8:12 AM Pacific
    message = compose(DigestView(now=late, owner=(templates.missed_digest(due=NOW, sent=late),)))
    assert message.kind == "digest"
    assert message.lines == ("For you: Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM.",
                             "Your list: nothing open.")
    assert lint(message) == []


def test_a_job_line_naming_a_longer_card_id_does_not_hide_this_card():
    garage = open_card("closet_or_garage", job="Mockford_101426", age=timedelta(days=1))
    view = DigestView(now=NOW, jobs=(JobLine("Mockford_101426", "card MOC-12 answered"),),
                      cards=(garage,), open_items=1, oldest_item=garage.created_at)
    assert "2. Card MOC-1, open 1 day: is Mockford_101426 a closet or a garage?" in (
        compose(view).lines)


# ----- a row that can't be quoted costs its own line, never Jim's whole list

WITHHELD = "could not be shown in this list; it is on the board."


@pytest.mark.parametrize("row", [
    "Testerson barbara@example.com", "Testerson 310-555-0123", r"Testerson G:\NG Jobs\x",
    "Testerson " + "3f" * 32, "Testerson k3Yv9QzR7mT2wX8pL4nB6cD1fG5hJ0",
], ids=["address", "phone", "path", "hash", "key"])
def test_a_row_the_lint_would_stop_costs_only_its_own_card_line(row):
    cards = (open_card("which_job", row=row, age=timedelta(days=3), number=2),
             open_card("row_changed", job="Fakeley_102226", row=row, age=timedelta(days=1)),
             open_card("closet_or_garage", job="Mockford_101426", age=timedelta(days=7)))
    view = DigestView(now=NOW, owner=("Paused",),
                      jobs=(JobLine("Dummitt_102826", "filed 10/29 3:05 PM"),), cards=cards,
                      open_items=3)
    message = compose(view)
    assert message.lines == (
        "For you: Paused.",
        "1. Dummitt_102826: filed 10/29 3:05 PM.",
        f"2. Card TES-2, open 3 days: {WITHHELD}",
        f"3. Card FAK-1, open 1 day: {WITHHELD}",
        "4. Card MOC-1, open 7 days: is Mockford_101426 a closet or a garage?",
        "Your list: 3 open.",
    )
    assert templates.vetted(message, now=NOW) == templates.Vetted(message, ())
    assert row not in message.text
    assert [card.id for card in reminders(view)] == ["TES-2", "FAK-1", "MOC-1"]   # all came back


def test_a_job_name_the_lint_would_stop_costs_only_its_own_card_line_too():
    cards = (open_card("fetch_failed", job="Testerson_Prj1", age=timedelta(days=1)),
             open_card("fetch_failed", job="Fakeley_102826", age=timedelta(days=1)))
    message = compose(DigestView(now=NOW, cards=cards, open_items=2))
    assert message.lines[:2] == (
        f"1. Card TES-1, open 1 day: {WITHHELD}",
        "2. Card FAK-1, open 1 day: still no complete IC summary for Fakeley_102826.")
    assert templates.vetted(message, now=NOW) == templates.Vetted(message, ())
