"""The P1a templates: every message NG Runner sends, each lint-clean with realistic data."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import pytest

from ngrunner.cards import CARD_KINDS, CardSpec, new_card, parse_command
from ngrunner.clock import fmt_clock, fmt_day
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
        "No message has been confirmed sent since 2:58 PM, and 3 are waiting. Urgent items now"
        " come here directly; the board has the rest.",)
    yesterday = t.cant_send(since=NOW - timedelta(days=1), now=NOW, waiting=1)
    assert_clean(yesterday)
    assert yesterday.lines[0].startswith(
        "No message has been confirmed sent since Thu 10/29 3:05 PM, and 1 is waiting.")


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
