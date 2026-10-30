"""The message lint: every message a person sees follows Jim's format (design section 4).

The design's own examples must pass (their illustrative job names swapped for synthetic ones),
and each violation in the table must fail for its stated reason.
"""
from __future__ import annotations

import pytest

from ngrunner.messages import Message
from ngrunner.messages.lint import check, lint, redact


def msg(text: str, *, kind: str = "test") -> Message:
    """A message from its text: the first line is the title, the rest are its lines."""
    title, *lines = text.split("\n")
    return Message(kind, title, tuple(lines))


# The four examples of design section 4 ("Examples (illustrative)"), with made-up names.
DESIGN_EXAMPLES = [
    "NG Runner · Fri 10/30\n"
    "For you: Dummitt_101426 installs 11/3, not locked. Open DO NOT CUT: Testova 3/4 Walnut.\n"
    "1. Testerson_101426: built except Closet 3 wall 2 (radius position not confirmed)."
    " Card TES-2.\n"
    "2. Fakeley_102726: garage or closet? Card FAK-1. Nothing built yet.\n"
    "3. Mockford_061626: not run. Mozaik was open at 1:30 AM. Runs tonight, or tap Build now.\n"
    "Your list: 3 open, oldest 2 days.",

    "Testerson_101426 checked 9:42 PM (trial)\n"
    '1. R1C3: section stops 1/4" short of its left panel. In chat: "fix Testerson item 1".\n'
    '2. R1C1 FM 84: line bore runs 9/64" (3.6 mm) past the top of the panel. Fix in Mozaik.',

    "TES-2 · Testerson_101426 · Closet 3 wall 2\n"
    "No second source confirms which panel is the radius: 2nd or 3rd from left."
    " Wall 2 is not built.\n"
    "[2nd]  [3rd]  [Build the whole job in chat]          (drawing crop below)",

    "NOT CHECKED · Dummitt_101426 · 3/4 Classic White\n"
    "This nest doesn't match the saved design. Save Dummitt in Mozaik (or re-nest) and I'll"
    " check again.",
]


@pytest.mark.parametrize("text", DESIGN_EXAMPLES)
def test_the_designs_own_examples_pass(text):
    assert lint(msg(text)) == []


DIGEST_TITLE = "NG Runner · Fri 10/30"

# Each row: why it is wrong, the message (text, or a Message when the text can't show it), and
# the reason code the lint must give.
VIOLATIONS = [
    # layout: numbered, one line per item
    ("no lines at all", "Filed as Testerson_101426", "structure"),
    ("an empty title", "\nTesterson_101426 is filed.", "structure"),
    ("a blank line", "Filed as Testerson_101426\n\nTesterson_101426 is filed.", "structure"),
    ("a line break inside a line",
     Message("test", "Filed as Testerson_101426", ("Testerson_101426 is\nfiled.",)), "structure"),
    ("a line break inside the title",
     Message("test", "Filed as\nTesterson_101426", ("Testerson_101426 is filed.",)), "structure"),
    ("a Unicode line separator",
     Message("test", "Filed as Testerson_101426", (f"It is{chr(0x2028)}filed.",)), "structure"),
    ("a tab", Message("test", "Filed as Testerson_101426", ("Testerson_101426\tis filed.",)),
     "structure"),
    ("spaces at the end of a line", "Filed as Testerson_101426\nTesterson_101426 is filed. ",
     "structure"),
    ("items numbered 1) and 2)", f"{DIGEST_TITLE}\n1) Testerson_101426: filed.\n"
     "2) Fakeley_102826: filed.", "numbering"),
    ("bullets instead of numbers", f"{DIGEST_TITLE}\n- Testerson_101426: filed.\n"
     "- Fakeley_102826: filed.", "numbering"),
    ("a number without its space", f"{DIGEST_TITLE}\n1.Testerson_101426: filed.", "numbering"),
    ("numbers that skip", f"{DIGEST_TITLE}\n1. Testerson_101426: filed.\n"
     "3. Fakeley_102826: filed.", "numbering"),
    ("numbers that don't start at 1", f"{DIGEST_TITLE}\n2. Testerson_101426: filed.", "numbering"),
    ("two items not numbered", "Testerson_101426 checked 9:42 PM\n"
     "Testerson_101426 is filed.\nFakeley_102826 is filed.", "numbering"),
    ("an item running onto a second line", f"{DIGEST_TITLE}\n1. Testerson_101426: filed,\n"
     "and checked twice.", "structure"),
    ("the For you line after the items", f"{DIGEST_TITLE}\n1. Testerson_101426: filed.\n"
     "For you: Dummitt_101426 installs 11/3.", "structure"),
    ("the Your list line before the items", f"{DIGEST_TITLE}\nYour list: 1 open, oldest 2 days.\n"
     "1. Testerson_101426: filed.", "structure"),
    ("choices that are not the last line", "TES-2 · Testerson_101426 · Closet or garage?\n"
     "[Closet]  [Garage]\nIts designs disagree.", "structure"),
    # every item names its job
    ("a digest item that names no job", f"{DIGEST_TITLE}\n1. Testerson_101426: filed.\n"
     "2. Filed and checked twice.", "job"),
    ("a list of choices with no job or card above it", "Which one?\n1. Closet\n2. Garage",
     "job"),
    # what each line may hold: no paths
    ("a Windows path", "Filed as Testerson_101426\n"
     "The summary is in G:\\NG Jobs\\Testerson_101426\\.", "path"),
    ("a network path", "Filed as Testerson_101426\nIt is on \\\\office-pc\\share.", "path"),
    ("a drive path with forward slashes", "Filed as Testerson_101426\nSee C:/Users/example.",
     "path"),
    ("a container path", "Testerson_101426 checked 9:42 PM\n"
     "1. Checked against /tmp/claude-0/scratchpad/conversion.json.", "path"),
    ("a relative path to a file", "Testerson_101426 checked 9:42 PM\n"
     "1. Written to outbox/message.json.", "path"),
    ("a folder path with forward slashes", "Filed as Testerson_101426\n"
     "It is in NG Jobs/Testerson_101426/summary.", "path"),
    ("a web address", "Filed as Testerson_101426\n"
     "Fetched from https://studio.example.com/install_summary/x/y/.", "path"),
    ("a web address without its scheme", "Filed as Testerson_101426\nSee www.example.com.",
     "path"),
    # times like 9:42 PM, in Pacific time
    ("a UTC time marked Z", "Testerson_101426 checked 02:57Z\n1. R1C3: fix it.", "time"),
    ("an ISO time", "Filed as Testerson_101426\nFiled at 2026-10-30T13:30:00+00:00.", "time"),
    ("a 24-hour time", "Filed as Testerson_101426\nFiled at 21:42.", "time"),
    ("a time without AM or PM", "Filed as Testerson_101426\nFiled at 9:42.", "time"),
    ("a lowercase pm without its space", "Filed as Testerson_101426\nFiled at 9:42pm.", "time"),
    ("a time with a leading zero", "Filed as Testerson_101426\nFiled at 09:42 PM.", "time"),
    ("a time with seconds", "Filed as Testerson_101426\nFiled at 9:42:10 PM.", "time"),
    ("a time in UTC", "Filed as Testerson_101426\nFiled at 4:42 AM UTC.", "time"),
    # dates like 10/30 or Fri 10/30
    ("an ISO date", "Filed as Testerson_101426\nIt installs 2026-11-03.", "date"),
    ("a date with its year", "Filed as Testerson_101426\nIt installs 11/3/26.", "date"),
    ("a date with a four-digit year", "Filed as Testerson_101426\nIt installs 11/3/2026.",
     "date"),
    ("a date with dashes", "Filed as Testerson_101426\nIt installs 11-3-26.", "date"),
    ("a month name", "Filed as Testerson_101426\nIt installs Nov 3.", "date"),
    ("a spelled-out month", "Filed as Testerson_101426\nIt installs November 3rd.", "date"),
    ("a full weekday before the date", "Filed as Testerson_101426\nIt installs Tuesday 11/3.",
     "date"),
    ("a weekday with a comma", "Filed as Testerson_101426\nIt installs Tue, 11/3.", "date"),
    ("a month with a leading zero", "Filed as Testerson_101426\nIt installs 09/3.", "date"),
    ("a day with a leading zero", "Filed as Testerson_101426\nIt installs 11/03.", "date"),
    # no hashes, keys or internal IDs
    ("a sha256", "Filed as Testerson_101426\nIts sha256 is " + "3f" * 32 + ".", "hash"),
    ("a short hash", "Filed as Testerson_101426\nRender ab12cd34ef56 governs.", "hash"),
    ("md5 talk", "Filed as Testerson_101426\nThe md5 read back from disk matches.", "hash"),
    ("a hash stuck on a job name", "Filed as Testerson_101426\n"
     "Wrote Testerson_101426_3fa9c1e2b4d5a6f7.", "hash"),
    ("a key", "Filed as Testerson_101426\nToken k3Yv9QzR7mT2wX8pL4nB6cD1fG5hJ0 works.", "key"),
    ("a Studio ID", "Filed as Testerson_101426\nIts Studio ID is 84213.", "studio_id"),
    ("a Studio ID in passing", "Filed as Testerson_101426\nFiled (84213) twice.", "studio_id"),
    ("a bare MMDDYY date", "Filed as Testerson_101426\nThe 101426 job is filed.", "studio_id"),
    # no attribute or table names
    ("a Mozaik attribute", "Testerson_101426 checked 9:42 PM\n1. R1C3: UniqueID is off.",
     "attribute"),
    ("Mozaik's true and false", "Testerson_101426 checked 9:42 PM\n"
     "1. R1C3: 5 x 'Fals' became 'Tru'.", "attribute"),
    ("a table or column name", "Filed as Testerson_101426\nIts fab_rows entry is filed.",
     "attribute"),
    ("an XML tag", "Testerson_101426 checked 9:42 PM\n1. The nest has no <OptimizeSheet> yet.",
     "attribute"),
    ("an attribute with its value", "Testerson_101426 checked 9:42 PM\n"
     '1. R1C1: JointFast="2" on the left panel.', "attribute"),
    ("a setting with its value", "Filed as Testerson_101426\nIt is filed with state=open.",
     "attribute"),
    # no addresses or phone numbers
    ("an email address", "Filed as Testerson_101426\nTold barbara@example.com.", "email"),
    ("a phone number", "Filed as Testerson_101426\nCall (310) 555-0123 first.", "phone"),
    ("a phone number with dashes", "Filed as Testerson_101426\nCall 310-555-0123.", "phone"),
    ("a phone number with its country code", "Filed as Testerson_101426\n"
     "Call +1 310 555 0123.", "phone"),
    ("a local phone number", "Filed as Testerson_101426\nCall 555-0123.", "phone"),
    # inches with fractions; mm only in parentheses after inches
    ("a size in mm only", "Testerson_101426 checked 9:42 PM\n1. R1C3: stops 6.35 mm short.",
     "size"),
    ("mm in parentheses not after inches", "Testerson_101426 checked 9:42 PM\n"
     "1. R1C3: stops short (6.35 mm).", "size"),
    ("other words with the mm", "Testerson_101426 checked 9:42 PM\n"
     '1. R1C1: runs 9/64" (max 3.6 mm) past the top.', "size"),
    ("decimal inches", "Testerson_101426 checked 9:42 PM\n1. R1C3: stops 0.25\" short.",
     "size"),
    ("centimetres", "Testerson_101426 checked 9:42 PM\n1. R1C3: stops 2 cm short.", "size"),
    # no intro or sign-off
    ("a greeting", "Filed as Testerson_101426\nHi Jim, Testerson_101426 is filed.", "intro"),
    ("a greeting in the title", "Hello Barbara\nTesterson_101426 is filed.", "intro"),
    ("a letter opening", "Filed as Testerson_101426\nDear Barbara, your row is filed.",
     "intro"),
    ("good morning", f"{DIGEST_TITLE}\nGood morning! Testerson_101426 is filed.", "intro"),
    ("an intro line", f"{DIGEST_TITLE}\nHere is today's list.\n1. Testerson_101426: filed.",
     "intro"),
    ("thanks at the end", "Filed as Testerson_101426\nTesterson_101426 is filed. Thanks!",
     "signoff"),
    ("regards", "Filed as Testerson_101426\nTesterson_101426 is filed. Regards, NG Runner",
     "signoff"),
    ("best", "Filed as Testerson_101426\nTesterson_101426 is filed. Best,", "signoff"),
    ("cheers", "Filed as Testerson_101426\nCheers, Testerson_101426 is filed.", "signoff"),
    ("an offer to help", "Filed as Testerson_101426\nTesterson_101426 is filed; let me know"
     " if that is wrong.", "signoff"),
    # a clean check reads exactly "Nothing to fix — matches IC summary."
    ("a clean line with a hyphen", "Testerson_101426 checked 9:42 PM\n"
     "Nothing to fix - matches IC summary.", "clean"),
    ("a clean line reworded", "Testerson_101426 checked 9:42 PM\n"
     "Nothing to fix: it matches the IC summary.", "clean"),
    ("the clean line with an item", "Testerson_101426 checked 9:42 PM\n"
     "Nothing to fix — matches IC summary.\n1. R1C3: fix it.", "clean"),
    ("all clear", "Testerson_101426 checked 9:42 PM\nAll clear.", "clean"),
    ("no issues found", "Testerson_101426 checked 9:42 PM\nNo issues found.", "clean"),
    ("a checker's verdict", "Testerson_101426 checked 9:42 PM\nCROSSCHECK CLEAN", "clean"),
    # could not verify, never unknown
    ("unknown", "Filed as Testerson_101426\nIts install date is unknown.", "verify"),
    ("N/A", "Filed as Testerson_101426\nInstall date: N/A.", "verify"),
    ("TBD", "Filed as Testerson_101426\nInstall date TBD.", "verify"),
    ("question marks for a value", "Filed as Testerson_101426\nInstall date ??.", "verify"),
    ("nothing is lost", "NG Runner has not checked in\nNothing is lost.", "verify"),
    # no program text leaking through
    ("None for a missing value", "Filed as Testerson_101426\nIt installs None.", "leak"),
    ("null for a missing value", "Filed as Testerson_101426\nIt installs null.", "leak"),
    ("an error's class name", "Filed as Testerson_101426\nThe fetch hit ConnectionError.",
     "leak"),
    ("a traceback", "Filed as Testerson_101426\nTraceback (most recent call last):", "leak"),
    ("a Python value", "Filed as Testerson_101426\nFiled datetime.datetime(2026, 10, 30).",
     "leak"),
    ("JSON", 'Filed as Testerson_101426\nPayload {"job": "Testerson_101426"} sent.', "leak"),
    ("an arrow", "Testerson_101426 checked 9:42 PM\n1. R1C3: Left->Right.", "leak"),
]


def _message(text: str | Message) -> Message:
    return text if isinstance(text, Message) else msg(text)


@pytest.mark.parametrize("why, text, code", VIOLATIONS, ids=[v[0] for v in VIOLATIONS])
def test_each_violation_fails_for_its_stated_reason(why, text, code):
    problems = check(_message(text))
    assert code in {p.code for p in problems}, f"{why}: got {[str(p) for p in problems]}"


@pytest.mark.parametrize("text", [
    # The title names the job or the card, so the items need not repeat it.
    "Testerson_101426 checked 9:42 PM\n1. R1C3: section stops 1/4\" short.\n2. R1C1: fix it.",
    "DUM-1 · Fab row \"Dummitt\" · Which job is this row?\n"
    "Fab row \"Dummitt\" could be more than one job.\n1. Dummitt_101426\n2. None of these",
    # A digest item may name its card instead, or be about NG Runner itself.
    f"{DIGEST_TITLE}\n1. Card DUM-1, open 3 days: which job is Fab row \"Dummitt\"?\n"
    "2. NG Runner at 10:05 PM: the Fab export is 42 minutes old.",
    # Garage and run names are job names too.
    f"{DIGEST_TITLE}\n1. Testerson_101426_Gar: filed.\n2. Fakeley_pantry_030926-2: filed.\n"
    "3. MockfordJane_091026: filed.",
])
def test_items_name_their_job_one_way_or_another(text):
    assert lint(msg(text)) == []


@pytest.mark.parametrize("line", [
    'Open DO NOT CUT: Testova 3/4 Classic White.',          # thickness, not a path
    'Line bore runs 9/64" (3.6 mm) past the top; 1/4" short on the left.',
    "Testerson_101426 installs 11/3 and/or 11/4.",           # a date, and one slash in words
    "Is Testerson_101426 a closet/garage job?",
    "Filed as Testerson_101426_Gar; its summary is Testerson_101426_Gar_summary.pdf.",
])
def test_ordinary_slashes_fractions_and_file_names_are_not_paths(line):
    assert lint(msg(f"Filed as Testerson_101426\n{line}")) == []


@pytest.mark.parametrize("line", [
    "Filed 10/30 at 9:42 PM, checked at 12:05 AM and again at 1:30 AM.",
    "Testerson_101426 waits on RULES:81 and BUILD:221.",       # file:line, not times
])
def test_times_in_jims_form_pass(line):
    assert lint(msg(f"Filed as Testerson_101426\n{line}")) == []


@pytest.mark.parametrize("line", [
    "Testerson_101426 installs Fri 10/30; Fakeley_102826 installs 11/3.",
    "Mockford_101426 installs Wednesday; the date could not be verified.",
    "No second source confirms the radius: 2nd or 3rd from left.",
    "NG Runner 0.1.10 ran Fri 10/30.",                        # a version, not a date
    "Testerson_101426 has 3 Marble shelves and 2 Decor panels for Jane.",   # not months
])
def test_dates_in_jims_form_pass(line):
    assert lint(msg(f"Filed as Testerson_101426\n{line}")) == []


@pytest.mark.parametrize("line", [
    "Locked Testerson_101426: 9 sheets, $27,006.49; programs TESS01-06, R1C1 FM 84.",
    "Your list holds 10,643 jobs on 4x96 sheets for 2026.",
    "Fakeley_pantry_030926 and Testerson_101426_Gar are filed.",
])
def test_money_counts_programs_and_job_names_are_not_ids(line):
    assert lint(msg(f"Filed as Testerson_101426\n{line}")) == []


@pytest.mark.parametrize("line", [
    "OneDrive is paused, so McTesterson_101426 waits; DeFakeley_092226 is filed.",
    "Testerson_101426: the line bore on R1C3 is fine; 1 + 1 = 2.",
])
def test_product_names_and_camel_case_surnames_are_not_attributes(line):
    assert lint(msg(f"Filed as Testerson_101426\n{line}")) == []


@pytest.mark.parametrize("line", [
    'R1C1: bore at 36 9/16" (928mm) runs 9/64" (3.6 mm) past the top.',
    "R1C2: top line 83 15/16″ (2132 mm); 1/4\" back; 3/4 Walnut; FM 84.",
    "R1C3: 2 1/2 in (63.5 mm) from the floor.",
    "R1C4: the $1.50 in the tracker is fine.",                 # money, not decimal inches
])
def test_inches_with_mm_in_parentheses_pass(line):
    assert lint(msg(f"Testerson_101426 checked 9:42 PM\n1. {line}")) == []


@pytest.mark.parametrize("line", [
    "Best tier drawers on R1C2; High shelf on R1C3; This nest is fine.",
    "Thanksgiving week: Testerson_101426 installs Fri 11/27.",
    "Hiking boots go on the High shelf of Testerson_101426.",
])
def test_words_that_merely_start_like_a_greeting_pass(line):
    assert lint(msg(f"Filed as Testerson_101426\n{line}")) == []


def test_none_of_these_is_a_choice_not_a_leak():
    assert lint(msg("DUM-1 · Fab row \"Dummitt\" · Which job is this row?\n"
                    "1. Dummitt_101426\n2. None of these")) == []


def test_the_exact_clean_line_passes_alone():
    assert lint(msg("Testerson_101426 checked 9:42 PM\nNothing to fix — matches IC summary.")) == []


def test_a_problem_says_where_and_why_without_repeating_the_text():
    # Problems go to the DEV lane and the logs, so they never carry the offending text.
    problems = lint(msg("Filed as Testerson_101426\nIt is in C:\\Users\\example\\x.pdf."))
    assert problems == ["line 1: has a path or a web address"]


# ----- redact: outside text (a send error, a reason from elsewhere) made safe to log


def test_redact_removes_addresses_numbers_web_addresses_and_codes():
    text = ("Invalid recipient barbara@example.com (310) 555-0123 at"
            " https://hooks.example.com/services/T0/B0/k3Yv9QzR7mT2wX8pL4nB6cD1 id " + "ab" * 16)
    assert redact(text) == ("Invalid recipient [address] [phone] at [web address] id [code]")


def test_redact_keeps_plain_words_and_caps_the_length():
    assert redact("Service invoked too many times for one day: email.") == (
        "Service invoked too many times for one day: email.")
    long = redact("x " * 400)
    assert len(long) <= 300 and long.endswith("…")
