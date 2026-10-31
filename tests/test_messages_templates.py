"""The P1a templates: every message NG Runner sends, each lint-clean with realistic data."""
from __future__ import annotations

import dataclasses
import random
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner import selftest
from ngrunner.cards import CARD_KINDS, CardSpec, new_card, parse_command
from ngrunner.clock import PACIFIC, at_pacific, fmt_clock, fmt_day
from ngrunner.config import Settings
from ngrunner.digest import DigestView, compose
from ngrunner.match import Opportunity
from ngrunner.messages import Message
from ngrunner.messages import templates as t
from ngrunner.messages.lint import SUBJECT_LIMIT, check_line, lint
from ngrunner.messenger import waits_for_morning

NOW = datetime(2026, 10, 30, 22, 5, tzinfo=timezone.utc)        # Fri 10/30 3:05 PM Pacific
INSTALL = date(2026, 11, 6)                                      # Fri 11/6
CANDIDATES = (
    Opportunity("Dummitt_101426", "201", "Pat Example", "Ready to Install"),
    Opportunity("Dummitt_102226", "202", "Pat Example", "Ready to Order"),
)


def assert_clean(message: Message) -> None:
    assert isinstance(message, Message)
    assert lint(message) == [], message.text


# ----- Barbara's emails


def test_filed_as_confirms_the_job_to_barbara():
    message = t.filed("Testerson_101426", row="Testerson", designer="PE", install=INSTALL)
    assert_clean(message)
    assert message.title == "Filed as Testerson_101426"
    assert message.lines == (
        'Fab row "Testerson" (designer PE, installs Fri 11/6) is filed as Testerson_101426.',)
    assert not message.urgent


def test_filed_as_says_garage_and_leaves_out_what_it_does_not_know():
    message = t.filed("Testerson_101426", row="  Testerson   garage ", designer="", garage=True)
    assert_clean(message)
    assert message.lines == (
        'Fab row "Testerson garage" is filed as Testerson_101426 (garage).',)


def test_filed_as_shows_the_design_and_page_counts_for_a_person_to_check():
    message = t.filed("Testerson_101426", row="Testerson", designs=3, pages=19)
    assert_clean(message)
    assert message.title == "Filed as Testerson_101426: 3 designs, 19 pages"
    assert message.lines == (
        'Fab row "Testerson" is filed as Testerson_101426: 3 designs, 19 pages.',)
    garage = t.filed("Testerson_101426_Gar", row="Testerson", designs=1, pages=1, garage=True)
    assert_clean(garage)
    assert garage.title == "Filed as Testerson_101426_Gar: 1 design, 1 page"
    assert garage.lines == (
        'Fab row "Testerson" is filed as Testerson_101426_Gar (garage): 1 design, 1 page.',)


@pytest.mark.parametrize("designs, pages", [(None, 19), (3, None), (3, 0), (None, None)])
def test_a_count_that_cant_be_read_is_left_out_never_guessed(designs, pages):
    assert t.filed_counts(designs, pages) == ""
    message = t.filed("Testerson_101426", row="Testerson", designs=designs, pages=pages)
    assert_clean(message)
    assert message.title == "Filed as Testerson_101426"
    assert message.lines == ('Fab row "Testerson" is filed as Testerson_101426.',)


def test_which_job_email_lists_the_candidates_for_barbara():
    message = t.which_job_email("Dummitt", designer="PE", install=INSTALL, candidates=CANDIDATES)
    assert_clean(message)
    assert message.title == 'Which job is Fab row "Dummitt"?'   # one thread per row
    assert message.lines == (
        'Fab row "Dummitt" (designer PE, installs Fri 11/6) could be more than one job, so'
        " nothing is filed yet. Type the full job name in the row; these jobs match:",
        "1. Dummitt_101426 (Pat Example, Ready to Install)",
        "2. Dummitt_102226 (Pat Example, Ready to Order)",
    )


def test_which_job_email_with_no_candidates_asks_for_the_full_name():
    message = t.which_job_email("Dummitt", designer="PE", candidates=())
    assert_clean(message)
    assert message.lines == (
        'Fab row "Dummitt" (designer PE) matches no job in Salesforce, so nothing is filed yet.'
        " Type the full job name in the row, as Salesforce spells it.",)


# A candidate as Salesforce spells it, with no date in its name, however it is written.
UNDATED = (
    Opportunity("Dummitt", "201", "Pat Example", "Ready to Install"),
    Opportunity("Dummitt closet", "202", "Pat Example", "Ready to Order"),
    Opportunity("Dummitt_Prj1", "203", "", ""),
    Opportunity("Dummitt 10/14/26", "204", "Quinn Sample", "Ready to Order"),
)


def test_a_candidate_with_no_date_in_its_name_still_reaches_barbara():
    message = t.which_job_email("Dummitt", designer="PE", candidates=UNDATED)
    assert t.vetted(message, now=NOW) == t.Vetted(message, ())
    assert message.lines[1:] == (
        "1. Dummitt (Pat Example, Ready to Install)",
        "2. Dummitt closet (Pat Example, Ready to Order)",
        "3. Dummitt_Prj1",
        "4. Dummitt 10/14/26 (Quinn Sample, Ready to Order)",
    )
    # The same when a queued row is written again, with no kind but its title and lines.
    assert lint(Message("outbox", message.title, message.lines)) == []


@pytest.mark.parametrize("name", [
    "Dummitt barbara@example.com", "Dummitt 310-555-0123", r"Dummitt G:\NG Jobs\x",
    "Dummitt " + "3f" * 32, "Dummitt k3Yv9QzR7mT2wX8pL4nB6cD1fG5hJ0",
], ids=["address", "phone", "path", "hash", "key"])
def test_a_candidate_is_still_never_passed_on_with_an_address_or_a_path(name):
    message = t.which_job_email("Dummitt", candidates=(Opportunity(name, "201", "", ""),))
    vetted = t.vetted(message, now=NOW)
    assert vetted.replaced and name not in vetted.message.text


@pytest.mark.parametrize("cell", ["N/A", "n/a", "TBD", "tbd", "None", "  N/A  ", "Unknown", "??"])
def test_a_designer_cell_that_says_nothing_is_left_out_so_the_email_goes_as_written(cell):
    listing = t.which_job_email("Dummitt", designer=cell, install=INSTALL, candidates=CANDIDATES)
    assert t.vetted(listing, now=NOW) == t.Vetted(listing, ())
    assert listing.lines[0].startswith(
        'Fab row "Dummitt" (installs Fri 11/6) could be more than one job')
    no_match = t.which_job_email("Dummitt", designer=cell)
    assert t.vetted(no_match, now=NOW) == t.Vetted(no_match, ())
    assert no_match.lines[0].startswith('Fab row "Dummitt" matches no job in Salesforce')
    filed = t.filed("Testerson_101426", row="Testerson", designer=cell)
    assert filed.lines == ('Fab row "Testerson" is filed as Testerson_101426.',)
    assert t.vetted(filed, now=NOW) == t.Vetted(filed, ())


def test_a_long_row_makes_a_subject_cut_at_a_word_and_the_body_keeps_it_all():
    row = "Testerson " + "closet and pantry " * 16
    assert len(row) > 290
    message = t.which_job_email(row, candidates=CANDIDATES)
    assert t.vetted(message, now=NOW) == t.Vetted(message, ())
    assert len(message.title) <= SUBJECT_LIMIT
    assert message.title.startswith('Which job is Fab row "Testerson closet and pantry')
    assert message.title.endswith('…"?')
    assert message.title.removesuffix('…"?').rsplit(" ", 1)[-1] in ("closet", "and", "pantry")
    assert f'"{" ".join(row.split())}"' in message.lines[0]


# ----- Jim's cards


def card(made: CardSpec):
    return new_card(made, created_at=NOW, allocate=lambda prefix: 2)


def test_closet_or_garage_holds_only_that_job():
    message = t.card_message(card(t.closet_or_garage_card(
        "Mockford_101426", reason="1 of its 3 designs is a garage")))
    assert_clean(message)
    assert message.title == "MOC-2 · Mockford_101426 · Closet or garage?"
    assert message.lines == (
        "1 of its 3 designs is a garage. Nothing is filed for Mockford_101426 until you choose;"
        " other jobs carry on.", "1. Closet", "2. Garage")


def test_which_job_card_offers_the_candidates_and_none_of_these():
    made = card(t.which_job_card("Dummitt", designer="PE", install=INSTALL,
                                 candidates=CANDIDATES))
    message = t.card_message(made)
    assert_clean(message)
    assert made.id == "DUM-2" and made.row == "Dummitt" and made.job is None
    assert message.title == 'DUM-2 · Fab row "Dummitt" · Which job is this row?'
    assert message.lines == (
        'Fab row "Dummitt" (designer PE, installs Fri 11/6) could be more than one job; nothing'
        " is filed until you pick one, or Barbara types the full job name in the row.",
        "1. Dummitt_101426", "2. Dummitt_102226", "3. None of these")


def test_which_job_card_with_no_candidates():
    message = t.card_message(card(t.which_job_card("Dummitt", designer="PE")))
    assert_clean(message)
    assert message.lines == (
        'Fab row "Dummitt" (designer PE) matches no job in Salesforce; nothing is filed until'
        ' Barbara types the full job name in the row, or you add it with "add job" and its'
        " name.", "1. None of these")


def test_fetch_failed_after_24_hours_keeps_trying_by_default():
    made = card(t.fetch_failed_card("Testerson_101426", since=NOW - timedelta(days=1),
                                    reason="the PDF Studio sent ends early"))
    message = t.card_message(made)
    assert_clean(message)
    assert message.title == "TES-2 · Testerson_101426 · Could not get a complete IC summary"
    assert message.lines == (
        "Could not get a complete IC summary for Testerson_101426 since Thu 10/29 3:05 PM: the"
        " PDF Studio sent ends early. NG Runner keeps trying until you choose.",
        "1. Keep trying", "2. Stop trying")
    assert made.default_choice == "Keep trying"


def test_ic_changed_asks_which_render_governs_and_refiles_nothing():
    made = card(t.ic_changed_card("Testerson_101426", filed_at=NOW - timedelta(days=2),
                                  filed_pages=19, new_at=NOW, new_pages=21))
    message = t.card_message(made)
    assert_clean(message)
    assert message.title == "TES-2 · Testerson_101426 · IC changed: which render governs?"
    assert message.lines == (
        "The IC summary fetched Fri 10/30 3:05 PM (21 pages) differs from the one filed"
        " Wed 10/28 (19 pages); nothing is re-filed until you choose.",
        "1. Keep the filed one", "2. File the new one")
    assert made.default_choice == "Keep the filed one"


def test_ic_changed_says_what_differs_after_its_question():
    made = card(t.ic_changed_card("Testerson_101426", filed_at=NOW - timedelta(days=2),
                                  filed_pages=19, new_at=NOW, new_pages=19,
                                  changed="The drawings changed. The text is the same."))
    message = t.card_message(made)
    assert_clean(message)
    assert message.lines[0].endswith(
        "nothing is re-filed until you choose. The drawings changed. The text is the same.")


def test_row_changed_asks_which_job_the_edited_row_is():
    message = t.card_message(card(t.row_changed_card(
        "Testerson_101426", was="Testerson", now_reads="Fakeley",
        candidates=(Opportunity("Fakeley_102826", "203", "Quinn Sample", "Ready to Order"),))))
    assert_clean(message)
    assert message.title == "TES-2 · Testerson_101426 · Row changed: which job?"
    assert message.lines == (
        'Fab row "Testerson" now reads "Fakeley", after it was filed as Testerson_101426;'
        " nothing new is filed until you choose.",
        "1. Keep Testerson_101426", "2. Fakeley_102826", "3. None of these")


def test_which_job_card_keeps_the_studio_id_each_choice_means():
    made = card(t.which_job_card("Dummitt", candidates=CANDIDATES))
    assert made.choices == ("Dummitt_101426", "Dummitt_102226", "None of these")
    assert made.choice_ids == ("201", "202", None)
    assert card(t.which_job_card("Dummitt")).choice_ids == (None,)


def test_row_changed_keeps_the_studio_id_each_choice_means():
    made = card(t.row_changed_card(
        "Testerson_101426", was="Testerson", now_reads="Fakeley",
        candidates=(Opportunity("Testerson_101426", "101", "Pat Example", "Ready to Order"),
                    Opportunity("Fakeley_102826", "203", "Quinn Sample", "Ready to Order"))))
    assert made.choices == ("Keep Testerson_101426", "Fakeley_102826", "None of these")
    assert made.choice_ids == (None, "203", None)        # the job itself is the Keep, with no ID


def test_two_sales_with_one_name_keep_their_own_ids():
    twins = (Opportunity("Dummitt_101426", "201", "Pat Example", "Ready to Install"),
             Opportunity("Dummitt_101426", "202", "Quinn Sample", "Ready to Install"))
    assert card(t.which_job_card("Dummitt", candidates=twins)).choice_ids == ("201", "202", None)


def test_cards_about_a_known_job_point_no_choice_at_a_job():
    for made in (
        t.closet_or_garage_card("Testerson_101426", reason="its designs disagree"),
        t.fetch_failed_card("Testerson_101426", since=NOW, reason="Studio did not answer"),
        t.ic_changed_card("Testerson_101426", filed_at=NOW, filed_pages=1, new_at=NOW, new_pages=2),
    ):
        assert card(made).choice_ids == (None, None)


def test_no_studio_id_ever_reaches_the_words_of_a_card():
    made = card(t.which_job_card("Dummitt", candidates=CANDIDATES))
    assert not {"201", "202"} & set(t.card_message(made).text.split())


def test_a_cards_subject_is_its_job_or_the_fab_row_it_asks_about():
    # What the title shows, and what the board shows beside a card: never the word None.
    about_a_job = card(t.closet_or_garage_card("Mockford_101426", reason="its designs disagree"))
    assert t.card_subject(about_a_job) == "Mockford_101426"
    about_a_row = card(t.which_job_card("  Dummitt   closet ", candidates=CANDIDATES))
    assert t.card_subject(about_a_row) == 'Fab row "Dummitt closet"'
    assert t.card_message(about_a_row).title == (
        'DUM-2 · Fab row "Dummitt closet" · Which job is this row?')
    changed = card(t.row_changed_card("Testerson_101426", was="Testerson", now_reads="Fakeley"))
    assert t.card_subject(changed) == "Testerson_101426"


def test_every_kind_of_card_has_its_words():
    assert set(t.QUESTIONS) == set(CARD_KINDS)
    assert set(t._CARD_BACK) == set(CARD_KINDS)


def test_the_remake_card_is_a_kind_of_its_own():
    made = card(t.remake_card("Testerson_101426", row="Testerson"))
    assert made.kind == "remake" and made.choices == t.REMAKE_CHOICES
    message = t.card_message(made)
    assert_clean(message)
    assert message.title.endswith("· Testerson_101426 · Is this a remake?")


@pytest.mark.parametrize("report, proposal, words", [
    (3, 4, "the report lists 3 designs and Studio's proposal lists 4."),
    (1, 2, "the report lists 1 design and Studio's proposal lists 2."),
    (2, 0, "the report lists 2 designs and Studio's proposal lists 0."),
    (3, 3, "the report lists 3 designs and Studio's proposal lists 3, with different names."),
])
def test_the_design_mismatch_card_says_it_in_counts(report, proposal, words):
    made = card(t.design_mismatch_card("Testerson_101426", report=report, proposal=proposal))
    assert made.kind == "design_mismatch" and made.job == "Testerson_101426"
    assert made.text == f"Testerson_101426: {words}"
    assert made.choices == t.DESIGN_MISMATCH_CHOICES == ("Fetch it again", "File it as it is")
    assert made.default_choice is None
    message = t.card_message(made)
    assert_clean(message)
    assert message.title.endswith("· Testerson_101426 · The report and Studio's proposal differ")
    assert message.lines == (made.text, "1. Fetch it again", "2. File it as it is")
    assert t.vetted(message, now=NOW) == t.Vetted(message, ())


def test_two_sales_with_one_name_get_choices_that_tell_them_apart():
    twins = (Opportunity("Dummitt_101426", "201", "Pat Example", "Ready to Install"),
             Opportunity("Dummitt_101426", "202", "Quinn Sample", "Ready to Install"),
             Opportunity("Dummitt_101426", "203", "Quinn Sample", "Ready to Install"))
    made = card(t.which_job_card("Dummitt", candidates=twins))
    assert made.choices == ("Dummitt_101426 (Pat Example, Ready to Install)",
                            "Dummitt_101426 (Quinn Sample, Ready to Install) #2",
                            "Dummitt_101426 (Quinn Sample, Ready to Install) #3",
                            "None of these")
    assert_clean(t.card_message(made))


# ----- the digest's own words


def test_digest_and_quiet_digest_templates_are_clean():
    day = date(2026, 10, 30)
    made = card(t.which_job_card("Dummitt", candidates=CANDIDATES))
    busy = t.digest(day, for_you=(t.install_soon("Testerson_101426", date(2026, 11, 3)),
                                  t.ALARMS["fab_export_stale"], t.stuck("Mockford_101426")),
                    jobs=(("Fakeley_102826", "filed 10/29 3:05 PM"),), cards=((made, 3),),
                    open_items=4, oldest_days=3)
    assert_clean(busy)
    assert busy.lines[0] == (
        "For you: Testerson_101426 installs Tue 11/3 and has no IC summary filed yet. The Fab"
        " Schedule export is over 30 minutes old, so new rows are not being seen."
        " Mockford_101426 has waited on NG Runner for over 2 days; work it in chat as before.")
    assert_clean(t.quiet_digest(day))
    assert_clean(t.quiet_digest(day, open_items=1, oldest_days=0))


def test_the_catch_up_line_names_the_missed_digest_and_when_it_went():
    due, sent = (at_pacific(date(2026, 10, 30), wall) for wall in (time(6, 30), time(8, 12)))
    line = t.missed_digest(due=due, sent=sent)
    assert line == "Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM"
    assert check_line(line) == []
    message = t.digest(date(2026, 10, 30), for_you=(line,))
    assert_clean(message)
    assert message.lines[0] == (
        "For you: Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM.")


@pytest.mark.parametrize("code", sorted(t.ALARMS))
def test_every_alarm_reads_cleanly_in_the_for_you_line(code):
    assert_clean(t.digest(date(2026, 10, 30), for_you=(t.ALARMS[code],)))


# ----- Sulaiman's lines


def test_dev_lines_name_their_job_or_ng_runner_with_the_time():
    message = t.dev((t.DevLine(NOW, "Studio did not answer on the 3rd try", "Testerson_101426"),
                     t.DevLine(NOW - timedelta(days=1), "the Fab export is 42 minutes old")),
                    day=date(2026, 10, 30))
    assert_clean(message)
    assert message.title == "NG Runner DEV · Fri 10/30"
    assert message.lines == (
        "1. Testerson_101426 at 3:05 PM: Studio did not answer on the 3rd try.",
        "2. NG Runner at Thu 10/29 3:05 PM: the Fab export is 42 minutes old.")
    assert t.dev((), day=date(2026, 10, 30)).lines == ("No DEV items are open.",)


def test_a_dev_line_has_outside_text_made_safe_first():
    reason = (r"Testerson_101426_summary.pdf could not be replaced (Access is denied), so the"
              r" commit was rolled back. Its earlier content is in"
              r" C:\Users\example\AppData\Local\NGRunner\backup\20261030T000000000000Z\x.pdf.")
    message = t.dev((t.DevLine(NOW, reason, "Testerson_101426"),), day=date(2026, 10, 30))
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426 at 3:05 PM: Testerson_101426_summary.pdf could not be replaced"
        " (Access is denied), so the commit was rolled back. Its earlier content is in [path].",)


def test_a_dev_line_that_still_fails_is_replaced_by_its_code_and_the_others_stay():
    message = t.dev((
        t.DevLine(NOW, "Studio did not answer on the 3rd try", "Testerson_101426", code="fetch"),
        t.DevLine(NOW, 'The job list is missing "synced_at", so it can\'t be used.',
                  code="job_list_bad"),
        t.DevLine(NOW, r"Wrote NG Jobs\Fakeley_102826\x.pdf.", "Fakeley_102826", code="write"),
        t.DevLine(NOW, "the Fab export is 42 minutes old", code="fab_export_stale"),
    ), day=date(2026, 10, 30))
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426 at 3:05 PM: Studio did not answer on the 3rd try.",
        "2. NG Runner at 3:05 PM: a DEV item (job list bad) did not pass the message check; its"
        " text is in NG Runner's database.",
        "3. Fakeley_102826 at 3:05 PM: a DEV item (write) did not pass the message check; its"
        " text is in NG Runner's database.",
        "4. NG Runner at 3:05 PM: the Fab export is 42 minutes old.",
    )
    assert not t.vetted(message, now=NOW).replaced


@pytest.mark.parametrize("item, line", [
    (t.DevLine(NOW, r"Wrote G:\x.", "Testerson", code="write"),   # no job name, so no subject
     "1. NG Runner at 3:05 PM: a DEV item (write) did not pass the message check; its text is in"
     " NG Runner's database."),
    (t.DevLine(NOW, 'Missing "synced_at".', code="unknown"),      # a code that fails the lint too
     "1. NG Runner at 3:05 PM: a DEV item did not pass the message check; its text is in"
     " NG Runner's database."),
    (t.DevLine(NOW, 'Missing "synced_at".'),                      # no code at all
     "1. NG Runner at 3:05 PM: a DEV item did not pass the message check; its text is in"
     " NG Runner's database."),
])
def test_a_dev_line_always_comes_out_clean_whatever_it_carried(item, line):
    message = t.dev((item,), day=date(2026, 10, 30))
    assert message.lines == (line,)
    assert_clean(message)


def test_the_fixed_dev_note_says_where_the_text_really_is():
    message = t.fixed("dev", day=date(2026, 10, 30))
    assert_clean(message)
    assert "board" not in message.text and "database" in message.text


def test_the_daily_ran_line_counts_the_day():
    message = t.ran(date(2026, 10, 30), version="0.1.0", new_rows=4, filed=3, cards_opened=1,
                    cards_answered=0, writes_refused=0, dev_open=2)
    assert_clean(message)
    assert message.title == "NG Runner ran · Fri 10/30"
    assert message.lines == (
        "NG Runner 0.1.0 ran Fri 10/30: 4 new rows, 3 filed, 1 card opened, 0 cards answered,"
        " 0 writes refused, 2 DEV items open.",)


# ----- the urgent one


def test_cant_send_is_urgent_and_says_since_when():
    message = t.cant_send(since=NOW - timedelta(minutes=7), now=NOW, waiting=3)
    assert_clean(message)
    assert message.urgent and message.title == "NG Runner can't send messages"
    assert message.lines == (
        "No message has been confirmed sent since 2:58 PM, and 3 are waiting. The board still"
        " shows everything.",)
    yesterday = t.cant_send(since=NOW - timedelta(days=1), now=NOW, waiting=1)
    assert_clean(yesterday)
    assert yesterday.lines[0].startswith(
        "No message has been confirmed sent since Thu 10/29 3:05 PM, and 1 is waiting.")


def test_nothing_promises_a_phone_that_p1a_does_not_have():
    # docs/decisions.md: no push channel in P1a; an urgent item with no receipt reaches Jim only
    # if the Pushover keys were added, so the words must hold either way.
    assert t.ALARMS["no_receipts"] == (
        "Messages are not being confirmed sent, so some may not reach you; the board still shows"
        " everything")
    for line in (t.ALARMS["no_receipts"], *t.cant_send(since=NOW, now=NOW, waiting=1).lines,
                 *t.fixed("cant_send", day=date(2026, 10, 30)).lines):
        assert "phone" not in line and "directly" not in line and "come here" not in line
        assert "board" in line


# ----- echoes for the command box


def test_echoes_say_what_was_done_in_one_clean_line():
    cards = [card(t.closet_or_garage_card("Mockford_101426", reason="its designs disagree"))]
    for typed, echo in [
        ("MOC-2 2", "Saved: MOC-2, Garage."),
        ("pause", "Paused: NG Runner files nothing until you resume it here."),
        ("resume", "Resumed: NG Runner carries on where it stopped."),
        ("add job Testerson_101426", "Looking for Testerson_101426 in the job list."),
        ("add job 84213", "Looking for that Studio project in the job list."),
    ]:
        line = t.echo(parse_command(typed, cards))
        assert line == echo and check_line(line) == [], typed


# ----- at runtime: a message that fails the lint goes out as its kind's fixed template


def test_a_clean_message_goes_out_as_it_is():
    message = t.filed("Testerson_101426", row="Testerson")
    assert t.vetted(message, now=NOW) == t.Vetted(message, ())


def test_a_failing_message_is_replaced_and_its_problems_are_kept_for_dev():
    bad = t.filed("Testerson_101426", row=r"Testerson G:\NG Jobs\x")   # a path came in
    result = t.vetted(bad, now=NOW)
    assert result.message == t.fixed("filed", day=date(2026, 10, 30))
    assert result.replaced and result.problems == ("line 1: has a path or a web address",)
    assert_clean(result.message)


def test_an_urgent_message_stays_urgent_when_replaced():
    bad = Message("cant_send", "NG Runner can't send messages", ("Since 21:42.",), urgent=True)
    result = t.vetted(bad, now=NOW)
    assert result.replaced and result.message.urgent
    assert_clean(result.message)


@pytest.mark.parametrize("kind", [*t.FIXED, "something_new"])
def test_every_fixed_template_is_clean_and_carries_no_outside_data(kind):
    message = t.fixed(kind, day=date(2026, 10, 30))
    assert_clean(message)
    assert "Testerson" not in message.text


def test_every_template_kind_has_a_fixed_template():
    made = card(t.closet_or_garage_card("Mockford_101426", reason="its designs disagree"))
    kinds = {t.filed("Testerson_101426", row="Testerson").kind,
             t.which_job_email("Dummitt").kind, t.card_message(made).kind,
             t.digest(date(2026, 10, 30)).kind, t.quiet_digest(date(2026, 10, 30)).kind,
             t.dev((), day=date(2026, 10, 30)).kind,
             t.ran(date(2026, 10, 30), version="0.1.0", new_rows=0, filed=0, cards_opened=0,
                   cards_answered=0, writes_refused=0, dev_open=0).kind,
             t.cant_send(since=NOW, now=NOW, waiting=1).kind}
    assert kinds <= set(t.FIXED)


# ----- shadow mode: a message redirected to Sulaiman says so in its title


def test_a_redirected_message_keeps_its_words_and_its_title_says_shadow():
    message = t.filed("Testerson_101426", row="Testerson", designer="PE")
    marked = t.shadow(message)
    assert marked.title == "SHADOW Filed as Testerson_101426"
    assert (marked.kind, marked.lines, marked.urgent) == (
        message.kind, message.lines, message.urgent)
    assert_clean(marked)


def test_a_redirected_message_that_fails_the_lint_still_says_shadow():
    # The redirection comes before the lint, so Sulaiman sees what would have gone out.
    bad = t.shadow(t.filed("Testerson_101426", row=r"Testerson G:\NG Jobs\x"))
    result = t.vetted(bad, now=NOW)
    assert result.replaced
    assert result.message == t.shadow(t.fixed("filed", day=date(2026, 10, 30)))
    assert_clean(result.message)


def test_a_message_that_only_starts_with_the_word_is_not_taken_for_a_redirected_one():
    bad = Message("filed", "SHADOWS Filed as Testerson_101426", (r"Saved in G:\NG Jobs.",))
    assert t.vetted(bad, now=NOW).message == t.fixed("filed", day=date(2026, 10, 30))


# ----- every template, through the lint, with awkward but realistic data

TRICKY_ROW = "O'Testa  \"closet\""                      # an apostrophe, quotes, double spaces


def every_template() -> list[tuple[str, Message]]:
    day = date(2026, 10, 30)
    which = card(t.which_job_card(TRICKY_ROW, designer="pe", install=date(2027, 1, 5),
                                  candidates=CANDIDATES))
    return [
        ("digest", t.digest(day, for_you=(t.install_soon("Testerson_101426-2", INSTALL),),
                            jobs=(("Fakeley_pantry_030926", "filed 10/29 3:05 PM"),),
                            cards=((which, 7),), open_items=1, oldest_days=7)),
        ("quiet digest", t.quiet_digest(day, open_items=3, oldest_days=12)),
        ("filed as", t.filed("O'Testa_101426_Gar", row=TRICKY_ROW, designer="PE",
                             install=INSTALL, garage=True)),
        ("filed as, with counts", t.filed("O'Testa_101426_Gar", row=TRICKY_ROW, designer="PE",
                                          install=INSTALL, garage=True, designs=3, pages=19)),
        ("which job email", t.which_job_email(TRICKY_ROW, designer="PE", candidates=CANDIDATES)),
        ("which job card", t.card_message(which)),
        ("closet or garage", t.card_message(card(t.closet_or_garage_card(
            "O'Testa_101426", reason="the Fab row says garage, but no design is a garage.")))),
        ("could not get a complete IC summary", t.card_message(card(t.fetch_failed_card(
            "Testerson_101426-2", since=NOW, reason="Studio did not answer within 60 seconds")))),
        ("IC changed", t.card_message(card(t.ic_changed_card(
            "Testerson_101426", filed_at=NOW, filed_pages=1, new_at=NOW, new_pages=2)))),
        ("row changed", t.card_message(card(t.row_changed_card(
            "Testerson_101426", was="Testerson", now_reads=TRICKY_ROW, candidates=CANDIDATES)))),
        ("remake", t.card_message(card(t.remake_card("O'Testa_101426", row=TRICKY_ROW)))),
        ("designs differ", t.card_message(card(t.design_mismatch_card(
            "O'Testa_101426_Gar", report=3, proposal=4)))),
        ("DEV", t.dev((t.DevLine(NOW, "the job list is 2 hours old"),), day=day)),
        ("ran", t.ran(day, version="0.1.10", new_rows=1, filed=1, cards_opened=0,
                      cards_answered=2, writes_refused=1, dev_open=0)),
        ("can't send", t.cant_send(since=NOW, now=NOW, waiting=0)),
        *nest_templates(),
    ]


def nest_templates() -> list[tuple[str, Message]]:
    """Every P1b message, with awkward but realistic data: a Mozaik folder name, a hand-named
    program, and a job name with an apostrophe."""
    job, material = "O'Testa_101426_Gar", "3-4 Egger Classic White"
    names = ("TESS01.TAP", "TESS02.TAP", "O'Testa_101426_3-4_S01R01-Flip.tap", "4x10 fix.TAP")
    return [
        ("DO NOT CUT, size", t.do_not_cut(job, material, trial=True, reasons=(
            t.past_its_size(2, "61x109", "49x97"), t.past_its_size(1, None, "49x97")))),
        ("DO NOT CUT, doubled", t.do_not_cut(job, material, trial=False, reasons=(
            t.doubled_holes(1, names[:1]), t.doubled_holes(8, names)))),
        ("NOT CHECKED", t.not_checked(job, material, posted=NOW, now=NOW)),
        ("NOT CHECKED, a day late", t.not_checked(job, material, posted=NOW - timedelta(days=1),
                                                  now=NOW)),
        ("Nest checked", t.nest_checked(job, material, at=NOW, programs=names, trial=True)),
        ("Nest checked, open items", t.nest_checked(job, material, at=NOW, programs=names[:1],
                                                    trial=False, open_items=2)),
        ("cleared", t.sheet_cleared(job, material, programs=((names[0], "61x109"),), at=NOW)),
        ("fault gone", t.fault_gone(job, material, at=NOW)),
        ("pass gone", t.pass_gone(job, material, at=NOW)),
        ("heads-up", t.order_heads_up(job, sheets=((1, "3/4 Tafisa Apres Skis"),),
                                      tape=((1, '15/16"', "Tafisa Apres Skis"),),
                                      flags=("IC Arctic White, built as Egger Classic White",))),
        ("revised heads-up", t.order_heads_up(job, sheets=((12, "3/4 Tafisa Apres Skis"),),
                                              tape=(), revised=True)),
        ("before you lock", t.before_you_lock(job, items=(
            "2 sheets of 3/4 Spring Blossom have no price in the tracker",))),
        ("sheet size card", t.card_message(card(t.sheet_size_card(
            job, material=material, programs=((names[0], "61x109"),), expected="49x97")))),
        ("sheet size card, no known sheet", t.card_message(card(t.sheet_size_card(
            job, material=material, programs=((names[3], None),), expected="49x97")))),
    ]


@pytest.mark.parametrize("name, message", every_template(), ids=[n for n, _ in every_template()])
def test_every_p1a_template_passes_the_lint(name, message):
    assert_clean(message)
    assert_clean(t.shadow(message))


# ----- every form a row takes (build spec section 2), through every template that quotes one

ROW_FORMS = [
    # a bare surname (most rows)
    "Testerson", "Van Testa", "O'Testa", "McTesterson", "Ñúñez",
    # Name_MMDDYY, with its variants
    "Testerson_101426", "Testerson_101426_Gar", "Testerson_101426-2", "Testerson_101426 remake",
    # a surname plus an extra token: initials, a first name, a project tag run on
    "Testerson JD", "Testerson Pat", "TestersonPrj1", "Testerson Prj1", "Testerson_Prj1",
    # a surname plus a room word
    "Testerson closet", "Testerson garage", "Testerson cabinets",
    # a date typo with five digits, or seven
    "Testerson_10142", "Testerson_1014261", "Testerson 10142",
    # a date typed another way, which the matcher reads
    "Testerson 10/14/26", "Testerson 10-14-2026", "Testerson 2026-10-14", "Testerson 101426",
    "Testerson 10/14", "Testerson 10/14/2026", "Testerson Oct 14", "Testerson 11/03",
    # a run, a note, and what people type for nothing
    "Testerson remake", "Testerson -2", "Testerson #2", "Testerson (call first)",
    "Testerson - rush", "Testerson n/a", "Testerson TBD", "Testerson None",
    # her own quotes and spacing
    'Testerson "10/14/26"', 'O\'Testa  "closet"', "  Testerson   closet ",
]


def row_messages(row: str) -> list[tuple[str, Message]]:
    which = card(t.which_job_card(row, designer="PE", install=INSTALL, candidates=CANDIDATES))
    nothing = card(t.which_job_card(row, designer="PE"))
    changed = card(t.row_changed_card("Testerson_101426", was=row, now_reads=row,
                                      candidates=CANDIDATES))
    remake = card(t.remake_card("Testerson_101426", row=row))
    back = [(which, 1), (nothing, 3), (changed, 7), (remake, 3)]
    aged = [dataclasses.replace(made, created_at=NOW - timedelta(days=age)) for made, age in back]
    return [
        ("filed", t.filed("Testerson_101426", row=row, designer="PE", install=INSTALL)),
        ("filed, garage", t.filed("Testerson_101426_Gar", row=row, garage=True)),
        ("filed, with counts", t.filed("Testerson_101426", row=row, designs=3, pages=19)),
        ("which job email", t.which_job_email(row, designer="PE", install=INSTALL,
                                              candidates=CANDIDATES)),
        ("which job email, no candidates", t.which_job_email(row)),
        ("which job card", t.card_message(which)),
        ("which job card, no candidates", t.card_message(nothing)),
        ("row changed card", t.card_message(changed)),
        ("remake card", t.card_message(remake)),
        ("digest with the cards back", t.digest(date(2026, 10, 30), cards=back, open_items=3,
                                                oldest_days=7)),
        ("composed digest", compose(DigestView(now=NOW, cards=tuple(aged), open_items=3))),
    ]


@pytest.mark.parametrize("row", ROW_FORMS)
def test_every_row_form_goes_through_every_template_as_barbara_typed_it(row):
    shown = " ".join(row.split()).replace('"', "”")
    for name, message in row_messages(row):
        assert lint(message) == [], f"{name}: {message.text}"
        assert t.vetted(message, now=NOW) == t.Vetted(message, ()), name
        assert f'"{shown}"' in message.text, f"{name}: {message.text}"


@pytest.mark.parametrize("row", [
    "Testerson barbara@example.com", "Testerson 310-555-0123", r"Testerson G:\NG Jobs\x",
    "Testerson https://example.com/x", "Testerson " + "3f" * 32,
])
def test_a_row_that_carries_an_address_or_a_path_is_never_passed_on(row):
    for name, message in row_messages(row):
        vetted = t.vetted(message, now=NOW)
        # A digest keeps its list: only the card's own line changes (tests/test_digest.py).
        assert vetted.replaced is ("digest" not in name), name
        assert "Testerson " not in vetted.message.text and row not in vetted.message.text, name
        assert lint(vetted.message) == [], name


def test_a_quote_of_hers_becomes_a_curly_one_so_the_lint_sees_where_her_words_end():
    message = t.filed("Testerson_101426", row='Testerson "10/14/26" and 11/03')
    assert message.lines[0].startswith('Fab row "Testerson ”10/14/26” and 11/03" is filed')
    assert lint(message) == []


@pytest.mark.parametrize("job", [
    "Testerson_101426b", "Testerson_101426b2", "Testerson 101426", "Testerson101426",
])
def test_the_job_name_shapes_found_in_ng_jobs_pass_through_the_templates(job):
    day = date(2026, 10, 30)
    made = card(t.closet_or_garage_card(job, reason="its designs disagree"))
    for message in (
        t.filed(job, row="Testerson", designer="PE"),
        t.digest(day, for_you=(t.install_soon(job, INSTALL), t.stuck(job)),
                 jobs=((job, "filed 10/29 3:05 PM"),), cards=((made, 3),), open_items=1),
        t.card_message(made),
        t.dev((t.DevLine(NOW, "Studio did not answer", job),), day=day),
    ):
        assert_clean(message)
        assert job in message.text


@pytest.mark.parametrize("kind, words", [
    ("filed", ("filed one of your Fab rows", "nothing for you to do")),
    ("which_job_email", ("one of your Fab rows", "full job name")),
])
def test_barbaras_fixed_templates_point_at_nothing_she_does_not_have(kind, words):
    message = t.fixed(kind, day=date(2026, 10, 30))
    assert_clean(message)
    assert "board" not in message.text and "Jim" not in message.text
    assert all(word in message.text for word in words)


def test_a_reason_that_says_nothing_is_never_left_as_a_blank():
    fetch = t.fetch_failed_card("Testerson_101426", since=NOW, reason="  ")
    assert "since Fri 10/30 3:05 PM: Studio gave no reason." in fetch.text
    branch = t.closet_or_garage_card("Testerson_101426", reason="")
    assert branch.text.startswith("Its designs don't settle closet or garage.")


# ----- no false alarms: every time, day and job name NG Runner writes passes the lint


def test_every_clock_time_and_every_day_in_jims_form_passes():
    start = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    for minute in range(0, 24 * 60):
        at = start + timedelta(minutes=minute)
        assert check_line(f"Testerson_101426 filed at {fmt_clock(at)}.") == [], fmt_clock(at)
    for offset in range(0, 366):
        day = date(2026, 1, 1) + timedelta(days=offset)
        assert check_line(f"Testerson_101426 installs {fmt_day(day)}.") == [], fmt_day(day)


def test_synthetic_job_names_of_every_shape_pass_through_every_template():
    rng = random.Random(20261030)
    for _ in range(300):
        surname = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(
            rng.randint(2, 14))).capitalize()
        when = date(2026, 1, 1) + timedelta(days=rng.randint(0, 364))
        variant = rng.choice(["", "_Gar", "-2", "_rev2", "_remake", "_pantry"])
        job = f"{surname}_{when:%m%d%y}{variant}"
        opportunity = Opportunity(job, "301", "Pat Example", "Ready to Order")
        at = NOW + timedelta(minutes=rng.randint(0, 10_000))
        made = new_card(t.fetch_failed_card(job, since=at, reason="Studio did not answer"),
                        created_at=at, allocate=lambda prefix: rng.randint(1, 999))
        for message in (
            t.filed(job, row=surname, designer="PE", install=when, garage="Gar" in variant),
            t.which_job_email(surname, candidates=(opportunity,)),
            t.card_message(made),
            t.digest(when, jobs=((job, "filed"),), cards=((made, 3),), open_items=1,
                     oldest_days=3),
            t.dev((t.DevLine(at, "Studio did not answer", job),), day=when),
        ):
            assert lint(message) == [], message.text


# ----- P1b: the nest guard's messages, word for word as the build spec gives them (5.6)


JOB = "Testerson_101426"
CLASSIC = "3/4 Classic White"
SIZE_NOTE = "A program is on 61x109 and this colour is 49x97"
DOUBLED_NOTE = "8 cam holes are doubled in programs TESS02 and TESS03"
NIGHT = datetime(2026, 10, 30, 23, 30, tzinfo=PACIFIC)            # 11:30 PM, in quiet hours
SIX_PROGRAMS = tuple(f"TESS0{n}.TAP" for n in range(1, 7))


def test_do_not_cut_for_a_size_reads_as_the_build_spec_gives_it():
    message = t.do_not_cut(JOB, CLASSIC, reasons=(SIZE_NOTE,), trial=True)
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426: DO NOT CUT 3/4 Classic White (trial). A program is on 61x109 and this"
        " colour is 49x97.",)
    assert message.title == "DO NOT CUT · Testerson_101426 · 3/4 Classic White"
    assert (message.kind, message.urgent) == ("do_not_cut", True)


def test_do_not_cut_for_doubled_holes_reads_as_the_build_spec_gives_it():
    message = t.do_not_cut(JOB, CLASSIC, reasons=(DOUBLED_NOTE,), trial=False)
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426: DO NOT CUT 3/4 Classic White. 8 cam holes are doubled in programs"
        " TESS02 and TESS03.",)


def test_do_not_cut_gives_each_reason_its_own_numbered_line():
    message = t.do_not_cut(JOB, CLASSIC, reasons=(SIZE_NOTE, DOUBLED_NOTE), trial=False)
    assert_clean(message)
    assert message.lines == (
        f"1. {JOB}: DO NOT CUT {CLASSIC}. {SIZE_NOTE}.",
        f"2. {JOB}: DO NOT CUT {CLASSIC}. {DOUBLED_NOTE}.",
    )


def test_do_not_cut_with_no_reason_still_says_do_not_cut():
    message = t.do_not_cut(JOB, CLASSIC, reasons=(), trial=False)
    assert_clean(message)
    assert message.lines == (f"1. {JOB}: DO NOT CUT {CLASSIC}.",)


def test_not_checked_reads_as_the_build_spec_gives_it():
    posted = datetime(2026, 10, 30, 23, 40, tzinfo=PACIFIC)
    message = t.not_checked(JOB, CLASSIC, posted=posted, now=posted + timedelta(minutes=5))
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426: NOT CHECKED 3/4 Classic White, posted 11:40 PM. NG Runner is"
        " offline.",)
    assert message.title == "NOT CHECKED · Testerson_101426 · 3/4 Classic White"
    assert message.kind == "not_checked" and not message.urgent


def test_not_checked_says_the_day_when_it_comes_a_day_late():
    posted = datetime(2026, 10, 29, 23, 40, tzinfo=PACIFIC)
    message = t.not_checked(JOB, CLASSIC, posted=posted, now=NOW)
    assert_clean(message)
    assert "posted Thu 10/29 11:40 PM." in message.lines[0]


def test_nest_checked_reads_as_the_build_spec_gives_it():
    checked = datetime(2026, 10, 30, 21, 42, tzinfo=PACIFIC)
    message = t.nest_checked(JOB, CLASSIC, at=checked, programs=SIX_PROGRAMS, trial=False)
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426: Nest checked 9:42 PM for 3/4 Classic White, programs TESS01-06.",)
    assert message.title == "Nest checked · Testerson_101426 · 3/4 Classic White"
    assert message.kind == "nest_checked" and not message.urgent


def test_nest_checked_says_trial_while_it_is_one():
    checked = datetime(2026, 10, 30, 21, 42, tzinfo=PACIFIC)
    message = t.nest_checked(JOB, CLASSIC, at=checked, programs=SIX_PROGRAMS, trial=True)
    assert_clean(message)
    assert message.lines[0] == (
        f"1. {JOB}: Nest checked 9:42 PM for {CLASSIC} (trial), programs TESS01-06.")


@pytest.mark.parametrize("open_items, line", [
    (0, None),
    (1, f"2. {JOB}: Jim still has 1 open item."),
    (2, f"2. {JOB}: Jim still has 2 open items."),
])
def test_nest_checked_adds_jims_open_items_only_when_there_are_some(open_items, line):
    message = t.nest_checked(JOB, CLASSIC, at=NOW, programs=SIX_PROGRAMS, trial=False,
                             open_items=open_items)
    assert_clean(message)
    assert message.lines[1:] == (() if line is None else (line,))


def test_cleared_by_jim_says_only_that_the_size_was_cleared():
    at = datetime(2026, 10, 30, 21, 42, tzinfo=PACIFIC)
    message = t.sheet_cleared(JOB, CLASSIC, programs=(("TESS07.TAP", "61x109"),), at=at)
    assert_clean(message)
    assert message.title == "Cleared by Jim · Testerson_101426 · 3/4 Classic White"
    assert message.lines == (
        "1. Testerson_101426: Cleared by Jim 9:42 PM, program TESS07 of 3/4 Classic White on"
        " 61x109: he added that sheet on purpose. This clears the sheet size only; the nest itself"
        " is not checked.",)


def test_the_order_heads_up_reads_as_the_build_spec_gives_it():
    message = t.order_heads_up(
        JOB, sheets=((12, "3/4 Tafisa Apres Skis"), (4, "1/4 Tafisa Apres Skis")),
        tape=((240, '15/16"', "Tafisa Apres Skis"),))
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426: 12 sheets 3/4 Tafisa Apres Skis, 4 sheets 1/4 Tafisa Apres Skis"
        " (takeoff estimate; the nest decides).",
        '2. Testerson_101426: 240 feet of 15/16" tape, Tafisa Apres Skis (takeoff estimate; the'
        " nest decides).",
    )
    assert message.title == "Order heads-up · Testerson_101426 · sheets with 10% waste"
    assert message.kind == "heads_up" and not message.urgent


def test_a_revised_heads_up_says_so_and_can_name_its_basis():
    message = t.order_heads_up(JOB, sheets=((1, "3/4 Classic White"),), tape=(), revised=True,
                               basis="sheets as calculated, no waste")
    assert_clean(message)
    assert message.title == (
        "Revised order heads-up · Testerson_101426 · sheets as calculated, no waste")
    assert message.lines == (
        f"1. {JOB}: 1 sheet 3/4 Classic White (takeoff estimate; the nest decides).",)


def test_a_heads_up_gives_each_tape_and_each_flag_a_line_of_its_own():
    message = t.order_heads_up(
        JOB, sheets=((1, "3/4 Classic White"),),
        tape=((1, '15/16"', "Classic White"), (240.4, '1"', "Classic White")),
        flags=("IC Arctic White, built as Egger Classic White", "The total is over $25,000"))
    assert_clean(message)
    assert message.lines[1:] == (
        f'2. {JOB}: 1 foot of 15/16" tape, Classic White (takeoff estimate; the nest decides).',
        f'3. {JOB}: 240 feet of 1" tape, Classic White (takeoff estimate; the nest decides).',
        f"4. {JOB}: IC Arctic White, built as Egger Classic White.",
        f"5. {JOB}: The total is over $25,000.",
    )


def test_a_heads_up_with_no_sheet_rows_says_only_the_tape():
    message = t.order_heads_up(JOB, sheets=(), tape=((240, '15/16"', "Classic White"),))
    assert_clean(message)
    assert message.lines == (
        f'1. {JOB}: 240 feet of 15/16" tape, Classic White (takeoff estimate; the nest decides).',)


def test_before_you_lock_reads_as_the_build_spec_gives_it():
    message = t.before_you_lock(JOB, items=(
        "2 sheets of 3/4 Spring Blossom have no price in the tracker",))
    assert_clean(message)
    assert message.lines == (
        "1. Testerson_101426: before you lock: 2 sheets of 3/4 Spring Blossom have no price in the"
        " tracker.",)
    assert message.title == "Before you lock · Testerson_101426"
    assert message.kind == "before_you_lock"


@pytest.mark.parametrize("shape", [
    "1. Testerson_101426: DO NOT CUT 3/4 Classic White (trial). A program is on 61x109 and this"
    " colour is 49x97.",
    "1. Testerson_101426: DO NOT CUT 3/4 Classic White. 8 cam holes are doubled in programs TESS02"
    " and TESS03.",
    "1. Testerson_101426: NOT CHECKED 3/4 Classic White, posted 11:40 PM. NG Runner is offline.",
    "1. Testerson_101426: Nest checked 9:42 PM for 3/4 Classic White, programs TESS01-06.",
    "1. Testerson_101426: 12 sheets 3/4 Tafisa Apres Skis, 4 sheets 1/4 Tafisa Apres Skis (takeoff"
    " estimate; the nest decides).",
    '2. Testerson_101426: 240 feet of 15/16" tape, Tafisa Apres Skis (takeoff estimate; the nest'
    " decides).",
    "1. Testerson_101426: before you lock: 2 sheets of 3/4 Spring Blossom have no price in the"
    " tracker.",
])
def test_each_shape_in_the_build_spec_comes_out_of_a_template_and_passes_the_lint(shape):
    assert check_line(shape) == []
    assert shape in {line for sample in selftest.message_samples() for line in sample.lines}


# ----- P1b: how names are shown


@pytest.mark.parametrize("names, shown", [
    (("TESS02.TAP",), "TESS02"),
    (("TESS03.tap", "TESS02.TAP"), "TESS02 and TESS03"),    # in order, either extension
    (("TESS01.TAP", "TESS02.TAP", "TESS03.TAP"), "TESS01-03"),
    (SIX_PROGRAMS, "TESS01-06"),
    (("TESS01.TAP", "TESS02.TAP", "TESS03.TAP", "TESS07.TAP"), "TESS01-03 and TESS07"),
    (("TESS01.TAP", "TESS05.TAP", "TESS09.TAP"), "TESS01, TESS05 and TESS09"),
    (("TESS09.TAP", "TESS10.TAP", "TESS11.TAP"), "TESS09-11"),
    (("TESS1.TAP", "TESS2.TAP", "TESS3.TAP"), "TESS1-3"),
    (("TESS01.TAP", "FAKS02.TAP", "TESS02.TAP"), "FAKS02, TESS01 and TESS02"),
    (("pile 3.TAP",), "pile 3"),
    (("Classic_White_S01.TAP",), "Classic White S01"),      # the lint stops an underscore
    (("Classic_White_S01.TAP", "Classic_White_S02.TAP", "Classic_White_S03.TAP"),
     "Classic White S01-03"),
    (("TESS01.TAP", "TESS01.tap"), "TESS01"),               # one program, twice
    ((), ""),
])
def test_program_names_are_shown_short_and_in_order(names, shown):
    assert t.program_names(names) == shown


@pytest.mark.parametrize("name", [
    r"G:\NG Jobs\TESS01.TAP", "TESS01 310-555-0123.TAP", "3f" * 32 + ".TAP",
    "k3Yv9QzR7mT2wX8pL4nB6cD1fG5hJ0.TAP",
], ids=["path", "phone", "hash", "key"])
def test_a_hand_named_program_the_lint_would_stop_is_counted_so_the_alarm_still_goes(name):
    # 65 of 551 real programs are hand-named. The DO NOT CUT must still name its job and material.
    note = t.doubled_holes(4, (name, "TESS02.TAP"))
    message = t.do_not_cut(JOB, CLASSIC, reasons=(note,), trial=False)
    assert note == "4 cam holes are doubled in 2 programs"
    assert t.vetted(message, now=NOW) == t.Vetted(message, ())
    assert name not in message.text


@pytest.mark.parametrize("folder, shown", [
    ("3-4 Egger Classic White", "3/4 Egger Classic White"),
    ("1-4 Tafisa Apres Skis", "1/4 Tafisa Apres Skis"),
    ("3/4 Classic White", "3/4 Classic White"),
    ("New folder", "New folder"),
    ("Testerson 3-4 fix", "Testerson 3-4 fix"),          # only a leading thickness
])
def test_a_material_folders_name_reads_as_a_thickness(folder, shown):
    message = t.do_not_cut(JOB, folder, reasons=(SIZE_NOTE,), trial=False)
    assert_clean(message)
    assert message.title == f"DO NOT CUT · {JOB} · {shown}"
    assert message.lines[0].startswith(f"1. {JOB}: DO NOT CUT {shown}. ")


@pytest.mark.parametrize("folder, shown", [
    ("3-4 Tafisa Spring Blossom_L543", "3/4 Tafisa Spring Blossom L543"),
    ("1-4 Tafisa Apris Ski_M2015", "1/4 Tafisa Apris Ski M2015"),
    ("3-4 Tafisa Casting at First Light_M2007", "3/4 Tafisa Casting at First Light M2007"),
    ("3-4 AraucoPrism-SF210-Antique-White-Suede", "3/4 AraucoPrism SF210 Antique White Suede"),
    ("3-4 Egger Black U999 ST19 Woodgrain", "3/4 Egger Black U999 ST19 Woodgrain"),
    ("1 in Egger Classic White", "1 in Egger Classic White"),
    ("White 1-4 Vinyl", "White 1-4 Vinyl"),
    ("GCode", "GCode"),
])
def test_each_real_kind_of_material_folder_reaches_people_by_name(folder, shown):
    # A DO NOT CUT is no use without its material, so none of these may fall to the fixed template.
    # Measured on the real NG Jobs folder names: the Arauco one would otherwise read as a key.
    for message in (t.do_not_cut(JOB, folder, reasons=(SIZE_NOTE,), trial=True),
                    t.not_checked(JOB, folder, posted=NOW, now=NOW),
                    t.nest_checked(JOB, folder, at=NOW, programs=SIX_PROGRAMS, trial=False),
                    t.sheet_cleared(JOB, folder, programs=(("TESS07.TAP", "61x109"),), at=NOW),
                    t.card_message(card(t.sheet_size_card(
                        JOB, material=folder, programs=(("TESS07.TAP", "61x109"),),
                        expected="49x97")))):
        assert t.vetted(message, now=NOW) == t.Vetted(message, ()), message.text
        assert shown in message.text


@pytest.mark.parametrize("folder", [
    "Testerson 10-14-26", "Testerson 10/14/26", r"G:\NG Jobs\x", "3f" * 32, "Call 310-555-0123",
], ids=["a date", "a slashed date", "a path", "a hash", "a phone number"])
def test_a_folder_the_lint_would_stop_still_lets_the_alarm_name_its_job(folder):
    # A folder in NG Jobs can be named anything. The job is what the alarm can't do without.
    message = t.do_not_cut(JOB, folder, reasons=(SIZE_NOTE,), trial=True)
    assert t.vetted(message, now=NOW) == t.Vetted(message, ())
    assert message.title == f"DO NOT CUT · {JOB} · one material"
    assert folder not in message.text


@pytest.mark.parametrize("count, on, note", [
    (1, "61x109", "A program is on 61x109 and this colour is 49x97"),
    (2, "61x109", "2 programs are on 61x109 and this colour is 49x97"),
    (1, None, "A program runs past every sheet NG Runner knows, and this colour is 49x97"),
    (3, None, "3 programs run past every sheet NG Runner knows, and this colour is 49x97"),
])
def test_past_its_size_counts_programs_and_never_guesses_a_sheet(count, on, note):
    assert t.past_its_size(count, on, "49x97") == note
    assert check_line(note) == []


def test_the_doubled_holes_note_reads_in_the_singular():
    assert t.doubled_holes(1, ("TESS02.TAP",)) == "1 cam hole is doubled in program TESS02"
    assert t.doubled_holes(2, ("TESS02.TAP",)) == "2 cam holes are doubled in program TESS02"


# ----- P1b: the board's notes


AT = datetime(2026, 10, 30, 21, 42, tzinfo=PACIFIC)


@pytest.mark.parametrize("note, words", [
    (t.nest_checked_note(AT, SIX_PROGRAMS), "Nest checked 9:42 PM, programs TESS01-06"),
    (t.nest_checked_note(None, SIX_PROGRAMS), "Nest checked, programs TESS01-06"),
    (t.programs_read_note(at=AT, sheet="49x97"),
     "Programs read 9:42 PM, nothing past 49x97, 0 doubled holes; the nest itself is not checked"),
    (t.programs_read_note(at=None, sheet="49x97"),
     "Programs read, nothing past 49x97, 0 doubled holes; the nest itself is not checked"),
    (t.programs_read_note(at=AT, sheet=None),
     "Programs read 9:42 PM, sheet size could not be verified, 0 doubled holes; the nest itself is"
     " not checked"),
    (t.programs_read_note(at=AT, sheet="49x97", cleared=1),
     "Programs read 9:42 PM, 1 cleared past 49x97, 0 doubled holes; the nest itself is not"
     " checked"),
    (t.programs_read_note(at=AT, sheet="49x97", merged=2),
     "Programs read 9:42 PM, nothing past 49x97, 0 doubled holes outside 2 merged programs; the"
     " nest itself is not checked"),
    (t.programs_read_note(at=AT, sheet="49x97", merged=1),
     "Programs read 9:42 PM, nothing past 49x97, 0 doubled holes outside 1 merged program; the"
     " nest itself is not checked"),
    (t.programs_unread_note(("TESS02.TAP",)),
     "Could not read program TESS02 yet; the nest itself is not checked"),
    (t.programs_unread_note(("TESS02.TAP", "TESS03.TAP")),
     "Could not read programs TESS02 and TESS03 yet; the nest itself is not checked"),
    (t.POSTED_BEFORE, "Posted before NG Runner; not checked"),
    (t.SAVE_THE_NEST, "Save the nest in the Optimizer so I can check it"),
])
def test_each_board_note_says_what_the_build_spec_says_and_passes_the_lint(note, words):
    assert note == words
    assert check_line(note) == []


def test_every_fixed_note_passes_the_lint():
    for note in (t.POSTED_BEFORE, t.SAVE_THE_NEST, t.NO_PROGRAMS_YET):
        assert check_line(note) == [], note


def test_a_clean_program_read_never_claims_the_right_sheet():
    # Build spec 3.1 and 5.2: a fit is a lower bound, so it can only prove a wrong sheet.
    note = t.programs_read_note(at=AT, sheet="49x97")
    assert " on 49x97" not in note and "nothing past 49x97" in note


# ----- P1b: the sheet size card


def sheet_card(on: str | None = "61x109"):
    return card(t.sheet_size_card(JOB, material="3-4 Classic White",
                                  programs=(("TESS07.TAP", on),), expected="49x97"))


def test_the_sheet_size_card_offers_jims_two_choices_and_points_at_no_job():
    made = sheet_card()
    assert made.kind == "sheet_size" and made.job == JOB and made.id == "TES-2"
    assert made.choices == t.SHEET_SIZE_CHOICES == ("I added this sheet on purpose", "Re-nest it")
    assert made.choice_ids == (None, None) and made.default_choice is None


def test_the_sheet_size_card_says_which_program_on_which_sheet():
    message = t.card_message(sheet_card())
    assert_clean(message)
    assert message.title == "TES-2 · Testerson_101426 · A program is past its sheet size"
    assert message.lines == (
        "Program TESS07 of 3/4 Classic White is on 61x109 and this colour is 49x97. It stays DO NOT"
        " CUT until you choose, and a re-post asks again.",
        "1. I added this sheet on purpose", "2. Re-nest it")


def test_the_sheet_size_card_says_so_when_the_sheet_is_not_one_it_knows():
    message = t.card_message(sheet_card(on=None))
    assert_clean(message)
    assert message.lines[0].startswith(
        "Program TESS07 of 3/4 Classic White runs past every sheet NG Runner knows, and this colour"
        " is 49x97.")


def test_one_sheet_size_card_names_how_many_programs_and_which():
    programs = tuple((f"TESS0{n}.TAP", "61x109") for n in range(1, 8))
    message = t.card_message(card(t.sheet_size_card(JOB, material="3-4 Classic White",
                                                    programs=programs, expected="49x97")))
    assert_clean(message)
    assert message.lines[0] == (
        "7 programs of 3/4 Classic White, programs TESS01-07, are on 61x109 and this colour is"
        " 49x97. It stays DO NOT CUT until you choose, and a re-post asks again.")


def test_a_sheet_size_card_over_several_sheets_counts_each():
    programs = (("TESS01.TAP", "61x109"), ("TESS02.TAP", "61x109"), ("TESS03.TAP", None))
    message = t.card_message(card(t.sheet_size_card(JOB, material="3-4 Classic White",
                                                    programs=programs, expected="49x97")))
    assert_clean(message)
    assert message.lines[0].startswith(
        "3 programs of 3/4 Classic White, programs TESS01-03, are past this colour's 49x97: 2 on"
        " 61x109, 1 past every sheet NG Runner knows.")


def test_a_card_counts_programs_whose_names_it_cant_show():
    programs = (("W=49 first.TAP", "61x109"), ("W=49 second.TAP", "61x109"))
    message = t.card_message(card(t.sheet_size_card(JOB, material="3-4 Classic White",
                                                    programs=programs, expected="49x97")))
    assert_clean(message)
    assert message.lines[0].startswith("2 programs of 3/4 Classic White are on 61x109")


def test_cleared_by_jim_names_all_the_programs_his_one_click_cleared():
    programs = tuple((f"TESS0{n}.TAP", "61x109") for n in range(1, 8))
    message = t.sheet_cleared(JOB, CLASSIC, programs=programs, at=NOW)
    assert_clean(message)
    assert "programs TESS01-07 of 3/4 Classic White" in message.lines[0]
    assert "he added those sheets on purpose" in message.lines[0]
    assert "clears the sheet size only" in message.lines[0]


def test_a_fault_gone_says_so_and_stops_short_of_saying_it_can_be_cut():
    message = t.fault_gone(JOB, "3-4 Classic White", at=NOW)
    assert_clean(message)
    assert message.title == "No longer DO NOT CUT · Testerson_101426 · 3/4 Classic White"
    assert "NOT CHECKED" in message.lines[0] and "nest itself is not checked" in message.lines[0]


def test_the_digests_open_do_not_cut_line_says_what_and_why():
    line = t.open_do_not_cut(JOB, "3-4 Classic White", trial=True, reasons=(
        "8 cam holes are doubled in programs TESS02 and TESS03",
        "A program is on 61x109 and this colour is 49x97"))
    assert check_line(line) == []
    assert line == ("DO NOT CUT Testerson_101426 3/4 Classic White (trial): 8 cam holes are doubled"
                    " in programs TESS02 and TESS03; a program is on 61x109 and this colour is"
                    " 49x97")


def test_the_open_not_checked_line_starts_as_the_watchdog_reads_it():
    posted = datetime(2026, 10, 30, 23, 40, tzinfo=PACIFIC)
    line = t.open_not_checked(JOB, "3-4 Classic White", posted=posted, now=posted)
    assert check_line(line) == []
    assert line == "Not checked: Testerson_101426 3/4 Classic White, posted 11:40 PM"
    older = t.open_not_checked(JOB, "3-4 Classic White", posted=posted,
                               now=posted + timedelta(days=2))
    assert check_line(older) == [] and "posted Fri 10/30 11:40 PM" in older


def test_many_open_not_checked_lines_end_in_a_count():
    assert t.more_not_checked(1) == "Not checked: 1 more material is on the board"
    assert t.more_not_checked(4) == "Not checked: 4 more materials are on the board"
    assert check_line(t.more_not_checked(4)) == []


def test_a_material_reads_as_jim_writes_it_for_the_board():
    assert t.material_words("3-4 Egger Classic White") == "3/4 Egger Classic White"


def test_the_sheet_size_card_comes_back_in_the_digest():
    message = t.digest(date(2026, 10, 30), cards=((sheet_card(), 3),), open_items=1, oldest_days=3)
    assert_clean(message)
    assert message.lines[0] == (
        "1. Card TES-2, open 3 days: Testerson_101426 still has a program past its sheet size; did"
        " you add that sheet on purpose?")


# ----- P1b: quiet hours, and what a message that fails the lint turns into


SETTINGS = Settings(ng_jobs=Path(r"C:\NG Jobs"), mozaik_shared=Path(r"C:\Mozaik Shared"),
                    control=Path(r"C:\control"))


def waits(message: Message, to: str = "jim", channel: str = "pushover") -> bool:
    return waits_for_morning(message.title, to=to, channel=channel, now=NIGHT, settings=SETTINGS)


def test_do_not_cut_goes_through_quiet_hours_by_its_title_alone():
    message = t.do_not_cut(JOB, CLASSIC, reasons=(SIZE_NOTE,), trial=True)
    assert not waits(message)                                   # Jim's phone, in the night
    assert not waits(message, to="george", channel="email")     # George, "at once, any hour"
    assert not waits(t.shadow(message), to="sulaiman")          # and marked for Sulaiman


def test_do_not_cut_goes_through_quiet_hours_even_once_replaced_by_its_fixed_template():
    bad = t.do_not_cut(JOB, CLASSIC, reasons=(r"Saved in G:\NG Jobs\TESS01.TAP",), trial=True)
    assert lint(bad)

    for sent in (bad, t.shadow(bad)):
        replaced = t.vetted(sent, now=NOW)
        assert replaced.replaced and replaced.message.urgent
        assert not waits(replaced.message)
        assert "NG Jobs" not in replaced.message.text


def test_only_do_not_cut_goes_through_quiet_hours():
    # Sanity on the check above: a phone message of any other kind does wait the night out.
    for message in (t.not_checked(JOB, CLASSIC, posted=NOW, now=NOW),
                    t.nest_checked(JOB, CLASSIC, at=NOW, programs=SIX_PROGRAMS, trial=False),
                    t.sheet_cleared(JOB, CLASSIC, programs=(("TESS07.TAP", "61x109"),), at=NOW),
                    t.card_message(sheet_card())):
        assert waits(message), message.kind


def test_a_fixed_do_not_cut_is_urgent_of_itself():
    assert t.fixed("do_not_cut", day=date(2026, 10, 30)).urgent


def test_every_new_kind_has_a_fixed_template_that_carries_no_outside_data():
    kinds = {message.kind for _, message in nest_templates()} - {"card"}
    assert kinds == {"do_not_cut", "not_checked", "nest_checked", "sheet_cleared", "fault_gone",
                     "pass_gone", "heads_up", "before_you_lock"}
    assert kinds <= set(t.FIXED)
    for kind in kinds:
        assert_clean(t.fixed(kind, day=date(2026, 10, 30)))
        assert JOB not in t.fixed(kind, day=date(2026, 10, 30)).text


@pytest.mark.parametrize("kind", ["heads_up", "before_you_lock", "nest_checked", "sheet_cleared",
                                  "not_checked", "do_not_cut"])
def test_the_fixed_templates_tell_people_with_no_board_where_to_look(kind):
    # George, Barbara and Will have no board of their own, so a bare "on the board" helps nobody.
    line = t.fixed(kind, day=date(2026, 10, 30)).lines[0]
    if kind == "heads_up":
        assert "board" not in line and "Jim" not in line
    elif kind == "before_you_lock":
        assert "on the board" in line                 # this one is Jim's own
    else:
        assert "his board" in line


def test_a_heads_up_can_only_carry_what_it_is_given():
    # Its reader picks named fields (build spec 5.8), so an address can't reach this template. The
    # lint still stands behind it, for anything else a flag may say.
    message = t.order_heads_up(JOB, sheets=((1, "3/4 Classic White"),), tape=(),
                               flags=("Call 310-555-0123 first",))
    vetted = t.vetted(message, now=NOW)
    assert vetted.replaced and "310-555-0123" not in vetted.message.text


# ----- P1b: no message ever says OK to cut


OK_TO_CUT = re.compile(
    r"\b(?:ok|okay|safe|clear|cleared|good|fine|ready|free|allowed|go(?: ahead)?|proceed|can|may)\b"
    r"(?:\W+\w+){0,3}?\W+cut(?:ting)?\b"
    r"|\bcut(?:ting)?\b(?:\W+\w+){0,3}?\W+(?:ok|okay|safe|allowed|fine|away)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("line", [
    "OK to cut", "Okay to cut", "ok to cut 3/4 Classic White", "Safe to cut", "Clear to cut",
    "Cleared to cut", "Good to cut", "Ready to cut", "Fine to cut", "Free to cut",
    "Go ahead and cut", "Go cut it", "You can cut", "You may cut this", "Proceed to cut", "Allowed to cut",
    "Cutting is OK", "Cut away",
])
def test_the_check_below_would_catch_every_way_of_saying_it(line):
    assert OK_TO_CUT.search(line)


@pytest.mark.parametrize("line", [
    "1. Testerson_101426: DO NOT CUT 3/4 Classic White.", "It stays DO NOT CUT until you choose.",
    "Cleared by Jim", "Nest checked 9:42 PM",
])
def test_the_check_below_passes_what_nest_messages_do_say(line):
    assert not OK_TO_CUT.search(line)


def said_about_nests() -> list[str]:
    """Every word the guards' templates can put in front of a person."""
    fixed = [t.fixed(kind, day=date(2026, 10, 30)) for kind in t.FIXED]
    samples = [*selftest.message_samples(), *(message for _, message in nest_templates()), *fixed]
    return [
        *(line for message in samples for line in (message.title, *message.lines)),
        t.QUESTIONS["sheet_size"], t._CARD_BACK["sheet_size"], *t.SHEET_SIZE_CHOICES,
        t.POSTED_BEFORE, t.SAVE_THE_NEST, t.NO_PROGRAMS_YET,
        t.programs_read_note(at=NOW, sheet="49x97"), t.nest_checked_note(NOW, SIX_PROGRAMS),
    ]


def test_no_message_ever_says_ok_to_cut():
    lines = said_about_nests()
    assert len(lines) > 100
    assert [line for line in lines if OK_TO_CUT.search(line)] == []


# ----- the nest files (P1b build spec 5.11, 5.12)


def test_a_pass_gone_says_what_changed_and_nothing_about_cutting():
    message = t.pass_gone(JOB, CLASSIC, at=NOW)

    assert message.title == "NOT CHECKED · Testerson_101426 · 3/4 Classic White"
    assert message.lines[0].startswith("1. Testerson_101426: NOT CHECKED 3/4 Classic White at ")
    assert message.lines[0].endswith("the nest, a program or a room changed since it was checked.")
    assert lint(message) == []


@pytest.mark.parametrize("programs, line", [
    (["TESS03.TAP"], "The nest names program TESS03, and it is not in the folder"),
    (["TESS03.TAP", "TESS04.TAP"], "The nest names programs TESS03 and TESS04, and they are not in the folder"),
])
def test_a_program_the_nest_names_and_isnt_there_is_said_plainly(programs, line):
    assert t.nest_names_missing(programs) == line
    assert check_line(line) == []


@pytest.mark.parametrize("optimizer, owner, age, line", [
    (False, "JDELA", timedelta(days=2, hours=1), "Open in Mozaik by JDELA, lock 2 days old"),
    (True, "JDELA", timedelta(minutes=5), "In the Optimizer by JDELA, lock 5 minutes old"),
    (False, None, timedelta(hours=3), "Open in Mozaik, lock 3 hours old"),
    (False, "", timedelta(seconds=10), "Open in Mozaik, lock 1 minute old"),
])
def test_a_lock_says_who_and_how_long_in_words_the_lint_allows(optimizer, owner, age, line):
    assert t.lock_held(optimizer, owner, age) == line
    assert check_line(line) == []


def test_a_nest_to_save_is_said_as_jim_reads_it():
    assert t.open_save_nest(JOB, "3-4 Classic White") == (
        "Testerson_101426 3/4 Classic White: save the nest in the Optimizer so I can check it")
