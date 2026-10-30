"""The P1a templates: every message NG Runner sends, each lint-clean with realistic data."""
from __future__ import annotations

import dataclasses
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from ngrunner.cards import CARD_KINDS, CardSpec, new_card, parse_command
from ngrunner.clock import at_pacific, fmt_clock, fmt_day
from ngrunner.digest import DigestView, compose
from ngrunner.match import Opportunity
from ngrunner.messages import Message
from ngrunner.messages import templates as t
from ngrunner.messages.lint import check_line, lint

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
        ("DEV", t.dev((t.DevLine(NOW, "the job list is 2 hours old"),), day=day)),
        ("ran", t.ran(day, version="0.1.10", new_rows=1, filed=1, cards_opened=0,
                      cards_answered=2, writes_refused=1, dev_open=0)),
        ("can't send", t.cant_send(since=NOW, now=NOW, waiting=0)),
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
    back = [(which, 1), (nothing, 3), (changed, 7)]
    aged = [dataclasses.replace(made, created_at=NOW - timedelta(days=age)) for made, age in back]
    return [
        ("filed", t.filed("Testerson_101426", row=row, designer="PE", install=INSTALL)),
        ("filed, garage", t.filed("Testerson_101426_Gar", row=row, garage=True)),
        ("which job email", t.which_job_email(row, designer="PE", install=INSTALL,
                                              candidates=CANDIDATES)),
        ("which job email, no candidates", t.which_job_email(row)),
        ("which job card", t.card_message(which)),
        ("which job card, no candidates", t.card_message(nothing)),
        ("row changed card", t.card_message(changed)),
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
        assert vetted.replaced, name
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
