"""Cards: questions for Jim with fixed choices, IDs that are never reused, and a strict parser
for what he types in the command box (design section 4, "How Jim answers")."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from ngrunner.cards import (
    CARD_KINDS, CardSpec, Command, Rejection, age_days, answered, card_from_record, card_prefix,
    new_card, parse_command, reminder_due,
)
from ngrunner.messages import templates as t
from ngrunner.messages.templates import SHEET_SIZE_CHOICES
from ngrunner.store import (
    SHEET_SIZE, add_card, add_clearance, answer_card, cleared_programs, open_store,
)

WHEN = datetime(2026, 10, 30, 22, 5, tzinfo=timezone.utc)   # Fri 10/30 3:05 PM Pacific


def counter():
    """A stand-in for the store's card numbers: the next number per prefix, never reused."""
    last: dict[str, int] = {}

    def allocate(prefix: str) -> int:
        last[prefix] = last.get(prefix, 0) + 1
        return last[prefix]
    return allocate


def spec(kind="closet_or_garage", job="Testerson_101426", row=None,
         choices=("Closet", "Garage"), default=None) -> CardSpec:
    return CardSpec(kind, job=job, row=row, text="Its designs disagree.", choices=choices,
                    default_choice=default)


# ----- prefixes and IDs


@pytest.mark.parametrize("name, prefix", [
    ("Testerson_101426", "TES"),
    ("Testerson_101426_Gar", "TES"),
    ("fakeley closet", "FAK"),            # a Fab row as Barbara typed it
    ("O'Testa_101426", "OTE"),
    ("Van Testa_101426", "VAN"),
    ("Ñúñez_101426", "NUN"),              # accents folded: easy to type
    ("Xy_101426", "XY"),                  # a short name keeps what it has
    ("1234", "ROW"),                      # a row with no letters at all
])
def test_a_card_prefix_is_the_first_three_letters_in_capitals(name, prefix):
    assert card_prefix(name) == prefix


def test_card_ids_come_from_the_store_and_are_never_reused():
    allocate = counter()
    first = new_card(spec(), created_at=WHEN, allocate=allocate)
    second = new_card(spec(job="Testerson_101426"), created_at=WHEN, allocate=allocate)
    other = new_card(spec(job="Testova_102226"), created_at=WHEN, allocate=allocate)  # TES too
    row = new_card(spec(kind="which_job", job=None, row="Dummitt",
                        choices=("Dummitt_101426", "None of these")),
                   created_at=WHEN, allocate=allocate)
    assert [c.id for c in (first, second, other, row)] == ["TES-1", "TES-2", "TES-3", "DUM-1"]
    assert first.state == "open" and first.created_at == WHEN and first.choices == (
        "Closet", "Garage")


@pytest.mark.parametrize("bad", [0, -1, True, "2", 2.0])
def test_a_card_number_that_is_not_a_whole_number_from_1_is_refused(bad):
    with pytest.raises(ValueError):
        new_card(spec(), created_at=WHEN, allocate=lambda prefix: bad)


@pytest.mark.parametrize("change", [
    {"choices": ()},                                   # no choices
    {"choices": ("Closet", "closet")},                 # the same choice twice
    {"choices": ("Closet", " Garage")},                # spaces around a choice
    {"choices": ("Closet", "Gar\nage")},               # a choice on two lines
    {"default": "Shed"},                               # a default that is not a choice
    {"kind": "lock"},                                  # not a P1a card
    {"job": None, "row": None},                        # about nothing
])
def test_a_card_that_could_not_be_answered_cleanly_is_refused(change):
    args = {"kind": "closet_or_garage", "job": "Testerson_101426", "row": None,
            "choices": ("Closet", "Garage"), "default": None} | change
    with pytest.raises(ValueError):
        new_card(spec(**args), created_at=WHEN, allocate=counter())


def test_a_naive_creation_time_is_refused():
    with pytest.raises(ValueError):
        new_card(spec(), created_at=WHEN.replace(tzinfo=None), allocate=counter())


# ----- the command box


def open_cards():
    allocate = counter()
    allocate("TES")                                   # TES-1 was answered long ago
    radius = new_card(spec(choices=("2nd", "3rd", "Build the whole job in chat")),
                      created_at=WHEN, allocate=allocate)                         # TES-2
    garage = new_card(spec(job="Fakeley_102726"), created_at=WHEN, allocate=allocate)  # FAK-1
    return [radius, garage]


@pytest.mark.parametrize("typed, card_id, choice", [
    ("TES-2 3rd", "TES-2", "3rd"),
    ("tes-2 3RD", "TES-2", "3rd"),                    # case aside
    ("  TES-2   Build  the whole job in chat ", "TES-2", "Build the whole job in chat"),
    ("TES-2 1", "TES-2", "2nd"),                      # a choice number
    ("FAK-1 2", "FAK-1", "Garage"),
    ("FAK-1 garage", "FAK-1", "Garage"),
])
def test_a_card_answer_names_the_card_and_one_of_its_choices(typed, card_id, choice):
    command = parse_command(typed, open_cards())
    assert isinstance(command, Command)
    assert (command.kind, command.card_id, command.choice) == ("answer", card_id, choice)


def test_an_answer_is_kept_verbatim():
    command = parse_command("tes-2  3RD ", open_cards())
    assert command.text == "tes-2  3RD "              # exactly as typed
    assert command.words == "3RD"                     # Jim's words after the card ID


@pytest.mark.parametrize("typed", [
    "TES-2 2",        # choice 2 is "3rd", but "2" reads as "2nd": never guess which he meant
    "TES-2 3",        # choice 3 is "Build...", but "3" reads as "3rd"
])
def test_a_number_that_could_mean_two_choices_is_refused(typed):
    rejection = parse_command(typed, open_cards())
    assert isinstance(rejection, Rejection)
    assert rejection.text == "I didn't understand. Try: TES-2 2nd"


@pytest.mark.parametrize("typed, kind", [
    ("pause", "pause"), ("Pause", "pause"), ("  resume ", "resume"), ("RESUME", "resume"),
])
def test_pause_and_resume(typed, kind):
    command = parse_command(typed, open_cards())
    assert isinstance(command, Command) and command.kind == kind and command.text == typed


@pytest.mark.parametrize("typed, name, studio_id", [
    ("add job Testerson_101426", "Testerson_101426", None),
    ("Add Job  Van Testa_101426 ", "Van Testa_101426", None),
    ("add job 84213", None, "84213"),
])
def test_add_job_takes_a_name_or_a_studio_project_id(typed, name, studio_id):
    command = parse_command(typed, open_cards())
    assert isinstance(command, Command) and command.kind == "add_job"
    assert (command.name, command.studio_id) == (name, studio_id)


@pytest.mark.parametrize("typed, example", [
    ("", "TES-2 2nd"),
    ("yes", "TES-2 2nd"),
    ("TES-2", "TES-2 2nd"),                 # no answer
    ("TES-2 4", "TES-2 2nd"),               # no 4th choice
    ("TES-2 third", "TES-2 2nd"),           # not a label: no fuzzy matching
    ("TES-2: 3rd", "TES-2 2nd"),            # strict: the ID, a space, the answer
    ("TES2 3rd", "TES-2 2nd"),
    ("FAK-1 shed", "FAK-1 Closet"),         # the example uses the card he named
    ("TES-1 2nd", "TES-2 2nd"),             # TES-1 is not open: an open card instead
    ("TES-9 2nd", "TES-2 2nd"),
    ("pause now", "TES-2 2nd"),
    ("add job", "TES-2 2nd"),
    ("add job   ", "TES-2 2nd"),
    ("check Testerson", "TES-2 2nd"),       # a later phase's command
    ("lock Testerson", "TES-2 2nd"),
])
def test_anything_else_is_refused_with_a_real_open_card_to_copy(typed, example):
    rejection = parse_command(typed, open_cards())
    assert isinstance(rejection, Rejection)
    assert rejection.text == f"I didn't understand. Try: {example}"


def test_with_no_open_card_the_example_is_pause_which_is_always_safe():
    assert parse_command("yes", []).text == "I didn't understand. Try: pause"


def test_cards_that_are_not_open_are_never_answered():
    answered = new_card(spec(), created_at=WHEN, allocate=counter())
    closed = dataclasses.replace(answered, state="answered", answer="Garage",
                                 answer_words="2", answered_by="jim", answered_at=WHEN,
                                 channel="board")
    assert parse_command(f"{closed.id} 1", [closed]).text == "I didn't understand. Try: pause"


def test_a_multi_word_first_choice_makes_a_numbered_example():
    card = new_card(spec(choices=("Keep trying", "Stop trying"), kind="fetch_failed"),
                    created_at=WHEN, allocate=counter())
    assert parse_command("hmm", [card]).text == "I didn't understand. Try: TES-1 1"


def test_every_example_given_parses_as_the_command_it_shows():
    cards = open_cards()
    for typed in ("", "nope", "TES-2", "TES-2 9", "FAK-1 shed", "TES-1 2nd", "TES-2 2"):
        example = parse_command(typed, cards).text.removeprefix("I didn't understand. Try: ")
        assert isinstance(parse_command(example, cards), Command), example


# ----- reminders at 1, 3 and 7 days


def card_made_at(when=WHEN, reminded=()):
    card = new_card(spec(), created_at=when, allocate=counter())
    return dataclasses.replace(card, reminded_at=tuple(reminded))


def days(n, hours=0):
    return WHEN + timedelta(days=n, hours=hours)


@pytest.mark.parametrize("now, age", [
    (days(0, 20), 0), (days(1), 1), (days(2, 23), 2), (days(3), 3), (days(10), 10),
])
def test_a_cards_age_is_its_whole_days_open(now, age):
    assert age_days(card_made_at(), now) == age


def test_an_open_card_comes_back_at_1_3_and_7_days_once_each():
    card, seen = card_made_at(), []
    for day in range(0, 12):
        now = days(day, 1)
        due = reminder_due(card, now)
        if due is not None:
            seen.append((day, due))
            card = dataclasses.replace(card, reminded_at=(*card.reminded_at, now))
    assert seen == [(1, 1), (3, 3), (7, 7)]


def test_missed_digests_bring_a_card_back_once_not_every_day():
    card = card_made_at()                      # the PC was off for its 1- and 3-day digests
    assert reminder_due(card, days(5, 1)) == 3
    card = dataclasses.replace(card, reminded_at=(days(5, 1),))
    assert reminder_due(card, days(6, 1)) is None
    assert reminder_due(card, days(7, 1)) == 7


def test_an_answered_card_never_comes_back():
    card = answered(card_made_at(), "Garage", words="2", by="jim", at=days(0, 2),
                    channel="board")
    assert reminder_due(card, days(3, 1)) is None


# ----- answers


def test_an_answer_is_recorded_verbatim_with_who_when_and_how():
    command = parse_command("tes-1  GARAGE", [card_made_at()])
    card = answered(card_made_at(), command.choice, words=command.words, by="jim",
                    at=days(0, 2), channel="board")
    assert (card.state, card.answer, card.answer_words, card.answered_by, card.answered_at,
            card.channel) == ("answered", "Garage", "GARAGE", "jim", days(0, 2), "board")


@pytest.mark.parametrize("choice", ["Shed", "garage"])
def test_an_answer_must_be_one_of_the_cards_choices_exactly(choice):
    with pytest.raises(ValueError):
        answered(card_made_at(), choice, words=choice, by="jim", at=days(0, 2),
                 channel="board")


def test_a_card_is_answered_once():
    card = answered(card_made_at(), "Garage", words="2", by="jim", at=days(0, 2),
                    channel="board")
    with pytest.raises(ValueError):
        answered(card, "Closet", words="1", by="jim", at=days(0, 3), channel="board")


def test_every_rejection_reads_cleanly_on_the_board():
    from ngrunner.messages.lint import check_line

    for cards in (open_cards(), []):
        for typed in ("", "yes", "TES-2 2", "FAK-1 shed", "check Testerson"):
            assert check_line(parse_command(typed, cards).text) == [], typed


# ----- fitting the store: its allocate_card_id gives the whole ID, add_card gives a record


def test_allocate_may_give_the_whole_id_as_the_store_does():
    card = new_card(spec(), created_at=WHEN, allocate=lambda prefix: f"{prefix}-7")
    assert card.id == "TES-7"


@pytest.mark.parametrize("given", ["FAK-7", "TES-0", "TES-x", "TES7", "tes-7"])
def test_a_whole_id_for_another_prefix_or_badly_formed_is_refused(given):
    with pytest.raises(ValueError):
        new_card(spec(), created_at=WHEN, allocate=lambda prefix: given)


def test_a_spec_knows_the_prefix_the_store_allocates_for():
    assert spec().prefix == "TES"
    assert spec(kind="which_job", job=None, row="dummitt closet",
                choices=("None of these",)).prefix == "DUM"


def test_a_stored_record_becomes_a_card_with_its_job_or_row_for_display():
    @dataclasses.dataclass(frozen=True)
    class Record:          # the store's CardRecord, field for field
        id: str = "DUM-3"
        studio_id: str | None = None
        fab_row_id: int | None = 12
        kind: str = "which_job"
        text: str = "Fab row \"Dummitt\" could be more than one job."
        choices: tuple[str, ...] = ("Dummitt_101426", "None of these")
        default_choice: str | None = None
        created_at: datetime = WHEN
        state: str = "answered"
        answer: str | None = "Dummitt_101426"
        answer_words: str | None = "1"
        answered_by: str | None = "jim"
        answered_at: datetime | None = WHEN
        channel: str | None = "command"
        reminded_at: tuple[datetime, ...] = ()
        choice_ids: tuple[str | None, ...] = ("201", None)

    card = card_from_record(Record(), row="Dummitt")
    assert (card.id, card.row, card.job, card.fab_row_id, card.state, card.answer_words) == (
        "DUM-3", "Dummitt", None, 12, "answered", "1")
    assert card.choice_ids == ("201", None)


# ----- which job each choice means: a Studio project ID beside its label, never read from it


def which_job(choices=("Dummitt_101426", "Dummitt_102226", "None of these"),
              ids=("201", "202", None)) -> CardSpec:
    return CardSpec("which_job", job=None, row="Dummitt", text="Which one?", choices=choices,
                    choice_ids=ids)


def test_a_card_carries_its_choice_ids_from_its_spec():
    made = new_card(which_job(), created_at=WHEN, allocate=counter())
    assert made.choice_ids == ("201", "202", None)


def test_an_answer_leads_to_the_id_beside_its_choice_by_number_or_by_label():
    made = new_card(which_job(), created_at=WHEN, allocate=counter())
    for typed in ("DUM-1 2", "dum-1 dummitt_102226"):
        command = parse_command(typed, [made])
        assert made.choice_ids[made.choices.index(command.choice)] == "202"
    none = parse_command("DUM-1 none of these", [made])
    assert made.choice_ids[made.choices.index(none.choice)] is None


@pytest.mark.parametrize("left_out", [(), None, []])
def test_a_card_with_no_ids_means_no_job_by_any_choice(left_out):
    assert CardSpec("closet_or_garage", job="Testerson_101426", row=None, text="Which?",
                    choices=("Closet", "Garage"), choice_ids=left_out).choice_ids == (None, None)
    assert spec().choice_ids == (None, None)
    made = new_card(spec(), created_at=WHEN, allocate=counter())
    assert made.choice_ids == (None, None)
    assert dataclasses.replace(made, choice_ids=left_out).choice_ids == (None, None)


@pytest.mark.parametrize("ids", [
    ("201",), ("201", None, None, None), ("", None, None), (" 201", None, None), (201, None, None),
    "201",                                            # three characters, for three choices
])
def test_choice_ids_that_could_point_an_answer_at_the_wrong_job_are_refused(ids):
    with pytest.raises((TypeError, ValueError)):
        new_card(which_job(ids=ids), created_at=WHEN, allocate=counter())


def test_an_answered_card_keeps_its_ids():
    made = new_card(which_job(), created_at=WHEN, allocate=counter())
    done = answered(made, "Dummitt_102226", words="2", by="jim", at=days(0, 2), channel="board")
    assert done.choice_ids == ("201", "202", None)


def test_choice_ids_go_through_the_real_store_and_come_back(tmp_path):
    from ngrunner.store import add_card, get_card, open_store

    store, made = open_store(tmp_path / "ngrunner.db"), which_job()
    record = add_card(store, prefix=made.prefix, kind=made.kind, text=made.text,
                      choices=made.choices, created_at=WHEN, choice_ids=made.choice_ids)
    card = card_from_record(get_card(store, record.id), row=made.row)
    assert (card.choices, card.choice_ids) == (made.choices, ("201", "202", None))
    garage = spec()
    record = add_card(store, prefix=garage.prefix, kind=garage.kind, text=garage.text,
                      choices=garage.choices, created_at=WHEN, choice_ids=garage.choice_ids)
    assert card_from_record(record, job=garage.job).choice_ids == (None, None)


def test_the_remake_and_design_cards_are_kinds_of_their_own():
    assert {"remake", "design_mismatch"} <= set(CARD_KINDS)


@pytest.mark.parametrize("typed, choice", [
    ("TES-1 1", "Fetch it again"), ("TES-1 2", "File it as it is"),
    ("tes-1 file it as it is", "File it as it is"),
])
def test_a_design_card_is_answered_by_number_or_label(typed, choice):
    card = new_card(spec("design_mismatch", choices=("Fetch it again", "File it as it is")),
                    created_at=WHEN, allocate=counter())
    parsed = parse_command(typed, [card])
    assert isinstance(parsed, Command) and parsed.choice == choice


# ----- P1b: a program past its sheet size


def test_the_sheet_size_card_is_a_kind_of_its_own():
    assert "sheet_size" in CARD_KINDS


def test_a_clearance_is_of_the_same_kind_as_the_card_that_asks_for_it():
    assert SHEET_SIZE == "sheet_size" and SHEET_SIZE in CARD_KINDS


def sheet_size_card():
    made = spec("sheet_size", choices=SHEET_SIZE_CHOICES)
    return new_card(made, created_at=WHEN, allocate=counter())


@pytest.mark.parametrize("typed, choice", [
    ("TES-1 1", "I added this sheet on purpose"), ("TES-1 2", "Re-nest it"),
    ("tes-1 re-nest it", "Re-nest it"),
    ("TES-1  i added this sheet on purpose", "I added this sheet on purpose"),
])
def test_a_sheet_size_card_is_answered_by_number_or_label(typed, choice):
    parsed = parse_command(typed, [sheet_size_card()])
    assert isinstance(parsed, Command) and parsed.choice == choice


@pytest.mark.parametrize("typed", ["TES-1 3", "TES-1 yes", "TES-1 clear it", "TES-1 ok to cut"])
def test_anything_else_typed_at_a_sheet_size_card_is_refused_and_it_stays_open(typed):
    made = sheet_size_card()
    parsed = parse_command(typed, [made])
    assert isinstance(parsed, Rejection) and made.state == "open"


def test_a_sheet_size_card_goes_through_the_store_and_its_answer_clears_only_its_hash(tmp_path):
    store = open_store(tmp_path / "ngrunner.db")
    made = t.sheet_size_card("Testerson_101426", material="3-4 Classic White",
                             program="TESS07.TAP", on="61x109", expected="49x97")
    record = add_card(store, prefix=made.prefix, kind=made.kind, text=made.text,
                      choices=made.choices, created_at=WHEN, choice_ids=made.choice_ids)
    assert card_from_record(record, job=made.job).choice_ids == (None, None)
    answer_card(store, record.id, answer=SHEET_SIZE_CHOICES[0], answer_words="1",
                answered_by="jim", answered_at=WHEN, channel="board")

    add_clearance(store, folder="Testerson_101426", material="3-4 Classic White",
                  program_sha256="a" * 64, card_id=record.id, at=WHEN)

    assert cleared_programs(store, "Testerson_101426", "3-4 Classic White") == {"a" * 64}
