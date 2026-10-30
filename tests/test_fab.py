"""The Fab export: Barbara's rows are read exactly, followed across exports, and dated only when sure."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ngrunner import fab, support, writes
from ngrunner.digest import DigestView, compose
from ngrunner.messages import templates
from ngrunner.messages.outbox import outbox_file
from ngrunner.signing import EnvelopeError, key_from_text, new_key, open_envelope, seal

KEY = key_from_text("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
WHEN = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)
GOOGLE = Path(__file__).resolve().parent.parent / "google"


def _row(sheet_row: int, job: str, designer: str = "PE",
         install: tuple[str, str] = ("Wednesday", " 7 /1"), fab_date: str = "") -> dict:
    return {"sheet_row": sheet_row, "job": job, "designer": designer, "install": list(install),
            "fab_date": fab_date}


def _export(rows: list[dict], *, seq: int = 1, kind: str = "fab_export", key: bytes = KEY,
            payload: dict | None = None) -> bytes:
    if payload is None:
        payload = {"sheet": "Sheet1", "exported_at": "2026-10-30T13:30:00+00:00", "rows": rows}
    return seal(kind, payload, key=key, seq=seq, issued_at=WHEN)


# ----- reading the export


def test_an_export_is_read_with_its_sequence_time_and_rows_as_shown():
    data = _export([_row(3, "  Testerson   closet ", "PE", ("Friday", " 11 /6"), "10/28/26"),
                    _row(4, "Fakeley_070126", "LC", ("7/15/26", "7/15/26"))], seq=42)
    export = fab.read_export(data, key=KEY)
    assert export.seq == 42
    assert export.exported_at == WHEN
    assert export.rows == (
        fab.ExportRow(3, "  Testerson   closet ", "PE", ("Friday", " 11 /6"), "10/28/26"),
        fab.ExportRow(4, "Fakeley_070126", "LC", ("7/15/26", "7/15/26"), ""),
    )


def test_a_file_that_is_not_a_signed_fab_export_is_refused():
    with pytest.raises(EnvelopeError, match="signature"):
        fab.read_export(_export([_row(3, "Testerson")]), key=key_from_text(new_key()))
    with pytest.raises(EnvelopeError, match="not a fab_export file"):
        fab.read_export(_export([_row(3, "Testerson")], kind="fab_edits"), key=KEY)


_GOOD = {"sheet": "Sheet1", "exported_at": "2026-10-30T13:30:00+00:00",
         "rows": [_row(3, "Testerson")]}


@pytest.mark.parametrize("payload", [
    {k: v for k, v in _GOOD.items() if k != "rows"},                    # no rows
    _GOOD | {"extra": 1},                                                # an unknown field
    _GOOD | {"sheet": "Archive"},                                        # another tab
    _GOOD | {"exported_at": "2026-10-30T13:30:00"},                     # no offset
    _GOOD | {"exported_at": 1761831000},
    _GOOD | {"rows": {"3": "Testerson"}},
    _GOOD | {"rows": [_row(3, "Testerson") | {"note": "x"}]},           # an unknown row field
    _GOOD | {"rows": [{k: v for k, v in _row(3, "Testerson").items() if k != "designer"}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"sheet_row": "3"}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"sheet_row": True}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"sheet_row": 0}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"job": None}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"install": ["Wednesday"]}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"install": ["Wednesday", 7]}]},
    _GOOD | {"rows": [_row(3, "Testerson") | {"fab_date": 46218}]},
    _GOOD | {"rows": [_row(3, "Testerson"), _row(3, "Fakeley")]},      # one row twice
])
def test_a_signed_export_in_any_other_form_is_refused_whole(payload):
    with pytest.raises(fab.FabError):
        fab.read_export(_export([], payload=payload), key=KEY)


def test_a_refused_export_is_still_an_envelope_error_for_callers_that_catch_those():
    assert issubclass(fab.FabError, EnvelopeError)


def test_the_export_carries_its_envelope_id_for_the_replay_guard():
    data = seal("fab_export", _GOOD, key=KEY, seq=5, issued_at=WHEN, id="ab" * 16)
    assert fab.read_export(data, key=KEY).id == "ab" * 16


# ----- reading the edit log


def _edit(sheet_row: int, column: str, old: str | None, new: str | None,
          at: str = "2026-10-30T13:31:00+00:00") -> dict:
    return {"at": at, "sheet_row": sheet_row, "column": column, "old": old, "new": new}


def test_the_edit_log_is_read_entry_by_entry():
    data = seal("fab_edits", {"edits": [_edit(7, "job", "Testersn", "Testerson"),
                                        _edit(8, "install", None, " 7 /8")]},
                key=KEY, seq=9, issued_at=WHEN, id="cd" * 16)
    edits = fab.read_edits(data, key=KEY)
    assert (edits.seq, edits.id) == (9, "cd" * 16)
    assert edits.edits == (
        fab.Edit(datetime(2026, 10, 30, 13, 31, tzinfo=timezone.utc), 7, "job", "Testersn",
                 "Testerson"),
        fab.Edit(datetime(2026, 10, 30, 13, 31, tzinfo=timezone.utc), 8, "install", None,
                 " 7 /8"),
    )


@pytest.mark.parametrize("payload", [
    {},
    {"edits": [], "extra": 1},
    {"edits": [_edit(7, "job", "a", "b")], "rows": []},
    {"edits": {}},
    {"edits": [_edit(7, "notes", "a", "b")]},                  # a column it does not watch
    {"edits": [_edit(7, "job", 1, "b")]},
    {"edits": [_edit(0, "job", "a", "b")]},
    {"edits": [_edit(7, "job", "a", "b", at="yesterday")]},
    {"edits": [_edit(7, "job", "a", "b") | {"user": "x"}]},
])
def test_an_edit_log_in_any_other_form_is_refused(payload):
    with pytest.raises(fab.FabError):
        fab.read_edits(seal("fab_edits", payload, key=KEY, seq=1, issued_at=WHEN), key=KEY)
    with pytest.raises(EnvelopeError, match="not a fab_edits file"):
        fab.read_edits(_export([_row(3, "Testerson")]), key=KEY)


# ----- following rows from one export to the next

# Synthetic rows in the shapes Sheet1 holds (build spec section 2): a bare surname, Name_MMDDYY,
# a surname plus initials, a surname plus a first name, a surname plus a room word with full
# dates, a five-digit date typo, a run-on project tag, and a designer given by first name.
SHEET1 = (
    fab.ExportRow(3, "Testerson", "PE", ("Wednesday", " 7 /1"), "6/24/26"),
    fab.ExportRow(4, "Fakeley_070826", "QA", ("Wednesday", " 7 /8"), ""),
    fab.ExportRow(5, "Mockford JD", "ZT", ("Thursday", " 7 /9"), ""),
    fab.ExportRow(6, "Dummitt Avery", "XB", ("Friday", " 7 /10"), ""),
    fab.ExportRow(7, "Testerson garage", "PE", ("7/15/26", "7/15/26"), ""),
    fab.ExportRow(8, "Fakeley_07126", "QA", ("Monday", " 7 /20"), ""),
    fab.ExportRow(9, "MockfordPrj1", "Avery", ("Tuesday", " 7 /21"), ""),
)


def _at(rows, *numbers: int) -> tuple[fab.ExportRow, ...]:
    """rows renumbered to the given sheet rows, as Sheet1 shows them after rows move."""
    return tuple(replace(r, sheet_row=n) for r, n in zip(rows, numbers, strict=True))


def _kinds(changes) -> list[tuple[str, str | None, str | None]]:
    return [(c.kind, c.previous.job if c.previous else None, c.row.job) for c in changes]


def test_a_row_inserted_mid_list_is_the_only_change():
    inserted = fab.ExportRow(6, "Dummitt", "ZT", ("Thursday", " 7 /9"), "")
    current = SHEET1[:3] + (inserted,) + _at(SHEET1[3:], 7, 8, 9, 10)
    changes = fab.diff(SHEET1, current)
    assert changes == [fab.RowChange("new", inserted, None)]


def test_rows_moved_out_are_gone_and_never_anything_else():
    current = _at(SHEET1[:2] + SHEET1[4:], 3, 4, 5, 6, 7)       # rows 5 and 6 went to Archive
    assert _kinds(fab.diff(SHEET1, current)) == [
        ("gone", None, "Mockford JD"), ("gone", None, "Dummitt Avery")]
    assert [c.row.sheet_row for c in fab.diff(SHEET1, current)] == [5, 6]   # where they were


def test_a_whole_re_sort_changes_nothing():
    resorted = _at(tuple(reversed(SHEET1)), *range(20, 27))
    assert fab.diff(SHEET1, resorted) == []


def test_blank_rows_are_ignored_on_both_sides():
    blank = fab.ExportRow(10, "   ", "PE", ("Friday", " 7 /24"), "")
    assert fab.diff(SHEET1 + (blank,), SHEET1) == []
    assert fab.diff(SHEET1, SHEET1 + (blank,)) == []
    assert fab.diff(SHEET1, SHEET1 + (replace(blank, job=" \t"),)) == []


def test_whitespace_alone_never_makes_a_row_new():
    spaced = (replace(SHEET1[0], job=" Testerson  ", designer="PE "),
              replace(SHEET1[1], install=("Wednesday", "7/8"))) + SHEET1[2:]
    assert fab.diff(SHEET1, spaced) == []


def test_duplicate_rows_are_counted_as_a_multiset():
    twin = replace(SHEET1[0], sheet_row=10)
    assert fab.diff(SHEET1 + (twin,), SHEET1 + (twin,)) == []
    assert fab.diff(SHEET1, SHEET1 + (twin,)) == [fab.RowChange("new", twin, None)]
    assert fab.diff(SHEET1 + (twin,), SHEET1) == [fab.RowChange("gone", twin, None)]
    # The twin nearest the old row is the old row; the far one is the newcomer.
    far = replace(SHEET1[0], sheet_row=40)
    assert fab.diff((SHEET1[0],), (far, replace(SHEET1[0], sheet_row=4))) == [
        fab.RowChange("new", far, None)]


def test_the_first_export_after_install_sees_every_row_and_starts_none():
    changes = fab.diff(None, SHEET1 + (fab.ExportRow(10, "", "", ("", ""), ""),))
    assert [c.kind for c in changes] == ["seen"] * len(SHEET1)
    assert [c.row for c in changes] == list(SHEET1)
    assert fab.diff((), SHEET1[:1]) == [fab.RowChange("new", SHEET1[0], None)]  # an empty export is not install


def _log(*pairs: tuple[str, str], column: str = "job") -> tuple[fab.Edit, ...]:
    at = datetime(2026, 10, 30, 13, 31, tzinfo=timezone.utc)
    return tuple(fab.Edit(at, 99, column, old, new) for old, new in pairs)


def test_a_corrected_name_is_an_edit_of_its_row_without_any_log():
    typo = replace(SHEET1[2], job="Mockfrod JD")
    previous = SHEET1[:2] + (typo,) + SHEET1[3:]
    assert fab.diff(previous, SHEET1) == [fab.RowChange("edited", SHEET1[2], typo)]


# Two rows that share designer and install. Barbara corrects both names, then sorts the block,
# so the rows swap places before the next export.
TWIN_DATES = (fab.ExportRow(3, "Fakely", "QA", ("Friday", " 7 /24"), ""),
              fab.ExportRow(4, "Mockfrd", "QA", ("Friday", " 7 /24"), ""))
SORTED_FIXED = (fab.ExportRow(3, "Mockford", "QA", ("Friday", " 7 /24"), ""),
                fab.ExportRow(4, "Fakeley", "QA", ("Friday", " 7 /24"), ""))


def test_without_a_log_the_nearest_row_with_the_same_designer_and_install_is_the_edit():
    assert _kinds(fab.diff(TWIN_DATES, SORTED_FIXED)) == [
        ("edited", "Fakely", "Mockford"), ("edited", "Mockfrd", "Fakeley")]


def test_the_edit_log_settles_which_row_a_corrected_name_belongs_to():
    log = _log(("Fakely ", "Fakeley"), ("Mockfrd", "Mockford"))
    assert _kinds(fab.diff(TWIN_DATES, SORTED_FIXED, log)) == [
        ("edited", "Mockfrd", "Mockford"), ("edited", "Fakely", "Fakeley")]


def test_the_edit_log_follows_a_name_corrected_twice_between_exports():
    log = _log(("Fakely", "Fakeleey"), ("Mockfrd", "Mockford"), ("Fakeleey", "Fakeley"))
    assert _kinds(fab.diff(TWIN_DATES, SORTED_FIXED, log)) == [
        ("edited", "Mockfrd", "Mockford"), ("edited", "Fakely", "Fakeley")]


def test_edit_log_entries_for_other_columns_or_unknown_old_text_settle_nothing():
    log = _log(("Fakely", "Fakeley"), ("Mockfrd", "Mockford"), column="designer")
    log += (fab.Edit(log[0].at, 3, "job", None, "Fakeley"),)
    assert _kinds(fab.diff(TWIN_DATES, SORTED_FIXED, log)) == [
        ("edited", "Fakely", "Mockford"), ("edited", "Mockfrd", "Fakeley")]


def test_the_edit_log_is_never_the_only_evidence():
    old = fab.ExportRow(3, "Testersn", "PE", ("Friday", " 7 /24"), "")
    new = fab.ExportRow(3, "Testerson", "ZT", ("Monday", " 8 /3"), "")
    log = _log(("Testersn", "Testerson"))
    assert fab.diff((old,), (new,), log) == [fab.RowChange("new", new, None),
                                            fab.RowChange("gone", old, None)]


def test_a_rescheduled_install_date_is_an_edit_of_the_same_row():
    moved = fab.ExportRow(9, "Testerson", "PE", ("Wednesday", " 7 /22"), "6/24/26")
    current = _at(SHEET1[1:], 3, 4, 5, 6, 7, 8) + (moved,)
    assert fab.diff(SHEET1, current) == [fab.RowChange("edited", moved, SHEET1[0])]


def test_a_corrected_designer_is_an_edit_of_the_same_row():
    fixed = replace(SHEET1[2], designer="ZX")
    assert fab.diff(SHEET1, SHEET1[:2] + (fixed,) + SHEET1[3:]) == [
        fab.RowChange("edited", fixed, SHEET1[2])]


def test_the_same_name_with_another_designer_and_install_is_another_row():
    other = fab.ExportRow(3, "Testerson", "ZT", ("Monday", " 9 /14"), "")
    assert fab.diff(SHEET1[:1], (other,)) == [fab.RowChange("new", other, None),
                                             fab.RowChange("gone", SHEET1[0], None)]


def test_a_matching_name_outranks_a_matching_install_date():
    # Barbara swaps two installs: each row keeps its name, so neither is read as a rename.
    a = fab.ExportRow(3, "Testerson", "PE", ("Wednesday", " 7 /1"), "")
    b = fab.ExportRow(4, "Fakeley", "PE", ("Wednesday", " 7 /8"), "")
    swapped = (replace(a, install=b.install), replace(b, install=a.install))
    assert _kinds(fab.diff((a, b), swapped)) == [
        ("edited", "Testerson", "Testerson"), ("edited", "Fakeley", "Fakeley")]


def test_unchanged_rows_can_be_followed_to_their_new_sheet_rows():
    inserted = fab.ExportRow(6, "Dummitt", "ZT", ("Thursday", " 7 /9"), "")
    current = SHEET1[:3] + (inserted,) + _at(SHEET1[3:], 7, 8, 9, 10)
    changes = fab.diff(SHEET1, current, unchanged=True)
    assert {c.previous.sheet_row: c.row.sheet_row for c in changes if c.kind == "same"} == {
        3: 3, 4: 4, 5: 5, 6: 7, 7: 8, 8: 9, 9: 10}
    assert [c for c in changes if c.kind != "same"] == fab.diff(SHEET1, current)


_NAMES = ("Testerson", "Fakeley", "Mockford", "Dummitt", "Testerson closet", "Fakeley_070126")


def _random_sheet(rng: random.Random, n: int) -> list[fab.ExportRow]:
    return [fab.ExportRow(i + 3, rng.choice(_NAMES) + rng.choice(("", "", " JD", "_07126")),
                          rng.choice(("PE", "QA", "ZT")),
                          (rng.choice(("Wednesday", "Friday", "")), f" {rng.randint(7, 8)} /{rng.randint(1, 4)}"),
                          "")
            for i in range(n)]


def _mutate(rng: random.Random, rows: list[fab.ExportRow]) -> list[fab.ExportRow]:
    rows = list(rows)
    for _ in range(rng.randint(0, 6)):
        op = rng.choice(("insert", "drop", "rename", "move", "blank", "twin", "sort"))
        at = rng.randrange(len(rows) + 1)
        if op == "insert" or not rows:
            rows.insert(at, _random_sheet(rng, 1)[0])
        elif op == "drop":
            rows.pop(at % len(rows))
        elif op == "rename":
            rows[at % len(rows)] = replace(rows[at % len(rows)], job=rng.choice(_NAMES) + " X")
        elif op == "move":
            r = rows[at % len(rows)]
            rows[at % len(rows)] = replace(r, install=(r.install[0], " 9 /9"))
        elif op == "blank":
            rows.insert(at, fab.ExportRow(0, " ", "PE", ("", ""), ""))
        elif op == "twin":
            rows.insert(at, rows[at % len(rows)])
        else:
            rng.shuffle(rows)
    return [replace(r, sheet_row=i + 3) for i, r in enumerate(rows)]


@pytest.mark.parametrize("seed", range(200))
def test_every_row_is_accounted_for_exactly_once(seed):
    rng = random.Random(seed)
    previous = _random_sheet(rng, rng.randint(0, 12))
    current = _mutate(rng, previous)
    changes = fab.diff(previous, current, unchanged=True)
    as_current = [c.row for c in changes if c.kind in ("same", "edited", "new")]
    as_previous = [c.previous for c in changes if c.kind in ("same", "edited")]
    as_previous += [c.row for c in changes if c.kind == "gone"]
    assert sorted(r.sheet_row for r in as_current) == [r.sheet_row for r in current if r.job.strip()]
    assert sorted(r.sheet_row for r in as_previous) == [r.sheet_row for r in previous if r.job.strip()]
    for c in changes:
        if c.kind == "same":
            assert " ".join(c.row.job.split()) == " ".join(c.previous.job.split())
        if c.kind == "edited":   # an edit keeps the name, or both the designer and the install
            assert (c.row.job == c.previous.job
                    or (c.row.designer, c.row.install) == (c.previous.designer, c.previous.install))
    # The order rows are given in never changes the answer, only their sheet rows do.
    shuffled_prev, shuffled_cur = previous[:], current[:]
    rng.shuffle(shuffled_prev)
    rng.shuffle(shuffled_cur)
    assert fab.diff(shuffled_prev, shuffled_cur, unchanged=True) == changes


# ----- reading the install date

JUNE = date(2026, 6, 20)


@pytest.mark.parametrize("install, expected", [
    (("Wednesday", " 7 /1"), date(2026, 7, 1)),          # weekday + a short date, odd spacing
    (("Friday", " 11 /6"), date(2026, 11, 6)),
    (("wed.", "7/1"), date(2026, 7, 1)),                 # abbreviations, any case
    (("Thurs", " 7 / 9 "), date(2026, 7, 9)),
    (("7/15/26", "7/15/26"), date(2026, 7, 15)),         # a full date in both cells
    (("7/15/2026", ""), date(2026, 7, 15)),
    (("", "7/15/26"), date(2026, 7, 15)),
    (("7/15/26", " 7 /15"), date(2026, 7, 15)),          # a full date and its short form
    (("", " 7 /1"), date(2026, 7, 1)),                   # a short date alone
    (("Wednesday", "7/1-2"), date(2026, 7, 1)),          # a multi-day install: its first day
    (("Wed", " 7 / 1 - 2 "), date(2026, 7, 1)),
    (("Tuesday", "6/30-7/2"), date(2026, 6, 30)),        # across a month
    (("", "7/1–2"), date(2026, 7, 1)),                   # the dash Sheets may autocorrect to
    (("7/1/26", "7/1-2"), date(2026, 7, 1)),             # a date and a range that starts on it
])
def test_the_two_install_date_cells_are_read_as_one_date(install, expected):
    assert fab.install_date(install, near=JUNE) == expected


@pytest.mark.parametrize("install", [
    ("", ""), ("  ", " "),
    ("Wednesday", ""),                                   # a weekday with no date
    ("Thursday", " 7 /1"),                               # 7/1/26 is a Wednesday
    ("7/15/26", "7/16/26"), ("7/15/26", " 7 /16"), ("7/15/26", "7/15/27"),
    ("TBD", " 7 /1"), ("Wednesday", "week of 7/1"), ("Wednesday", "7-1"),
    ("Thursday", "7/1-2"), ("7/2/26", "7/1-2"),          # a range is its first day: 7/1 is a Wednesday
    ("", "7/1-"), ("", "-7/1"), ("", "7/1-2-3"), ("", "7/1-2/3/4"),
    ("", "13/1"), ("", "2/30"), ("", "0/5"), ("", "7/1/123"), ("", "7/1/26/1"),
    ("", "7/15/0026"), ("", "7/15/1926"), ("", "٧/١"),          # no year 26 AD; ASCII digits only
])
def test_anything_but_one_clear_date_could_not_be_verified(install):
    assert fab.install_date(install, near=JUNE) is None


def test_a_date_without_a_year_takes_the_year_nearest_the_row():
    assert fab.install_date(("", " 1 /4"), near=date(2026, 12, 28)) == date(2027, 1, 4)
    assert fab.install_date(("", "12/30"), near=date(2027, 1, 3)) == date(2026, 12, 30)
    assert fab.install_date(("Monday", " 1 /4"), near=date(2026, 12, 28)) == date(2027, 1, 4)
    assert fab.install_date(("", " 2 /29"), near=date(2027, 11, 1)) == date(2028, 2, 29)


def test_near_may_be_a_time_and_is_read_as_a_pacific_day():
    late_on_6_30 = datetime(2026, 7, 1, 6, 30, tzinfo=timezone.utc)      # 11:30 PM PDT, 6/30
    noon_on_7_1 = datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc)
    # 12/30 is 182 days before 6/30 and 183 after it; from 7/1 it is the other way round.
    assert fab.install_date(("", "12/30"), near=late_on_6_30) == date(2025, 12, 30)
    assert fab.install_date(("", "12/30"), near=noon_on_7_1) == date(2026, 12, 30)
    with pytest.raises(ValueError):
        fab.install_date(("", " 7 /1"), near=datetime(2026, 7, 1, 6, 30))   # naive: refused


# ----- the Apps Script side (google/), run under node with envelopes sealed here


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    return str(default) if default.is_file() else None


def _python_envelopes() -> dict:
    """Files NG Runner would leave in the control folder, for the Apps Script tests to read."""
    issued = datetime(2026, 10, 30, 13, 55, 0, 123456, tzinfo=timezone.utc)      # 6:55 AM PDT
    items = [
        ("outbox", {"channel": "email", "to": "barbara", "subject": "Filed as Testerson_070126",
                    "lines": ["1. Testerson_070126: filed the IC summary — 3/4\" café."],
                    "urgent": False, "ref": "filed:Testerson_070126"}, 7, "ab" * 16),
        ("heartbeat", {"app_version": "0.0.1", "paused": False,
                       "open_risks": ["Not checked: Dummitt 3/4 Classic White, posted 6:50 AM"],
                       "last_fab_seq": 12, "list_synced_at": "2026-10-30T13:00:00+00:00",
                       "pending": 1, "dev_open": 0}, 3, "cd" * 16),
        ("pause", {"reason": "testing the pause path"}, 1, "ef" * 16),
    ]
    # Prune.gs names these files itself, so the two sides are checked against each other. The
    # chokepoint keeps its job-file names private, and this is the one reader outside it.
    names = {"support": support.bundle_name(WHEN), "job_suffixes": list(writes._JOB_FILE_SUFFIXES),
             "temp_suffix": writes._TMP_SUFFIX}
    # A quiet night's digest from NG Runner's own code, sent on as the shadow run sends it.
    digest = templates.shadow(compose(DigestView(now=issued)))
    shadow = outbox_file(digest, to="sulaiman", channel="email", key=KEY, seq=8, now=issued)
    envelopes = [
        {"kind": kind, "payload": payload, "seq": seq, "id": id_,
         "text": seal(kind, payload, key=KEY, seq=seq, issued_at=issued, id=id_).decode("utf-8")}
        for kind, payload, seq, id_ in items]
    return {"key_hex": KEY.hex(), "names": names, "envelopes": envelopes,
            "shadow_digest": {"path": "/".join(shadow.path_parts), "subject": digest.title,
                              "text": shadow.data.decode("utf-8")}}


@pytest.fixture(scope="module")
def apps_script(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """Runs google/test once under node; returns where the Apps Script left files for Python."""
    node = _node()
    if node is None:
        pytest.skip("node is not installed, so the Apps Script tests can't run here")
    work = tmp_path_factory.mktemp("apps_script")
    out = work / "from_apps_script"
    out.mkdir()
    sealed = work / "python_envelopes.json"
    sealed.write_text(json.dumps(_python_envelopes(), ensure_ascii=False), encoding="utf-8")
    tests = sorted(str(p) for p in (GOOGLE / "test").glob("*.test.js"))
    env = {**os.environ, "NGR_PY_ENVELOPES": str(sealed), "NGR_JS_OUT": str(out)}
    result = subprocess.run([node, "--test", "--test-reporter=tap", *tests], capture_output=True,
                            encoding="utf-8", errors="replace", env=env, timeout=600, check=False)
    return out, result


def test_the_apps_script_passes_its_node_tests_with_nothing_skipped(apps_script):
    _, result = apps_script
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
    assert "# fail 0" in result.stdout and "# skipped 0" in result.stdout, result.stdout[-3000:]


def test_the_apps_script_seals_byte_for_byte_what_python_seals(apps_script):
    out, _ = apps_script
    sealed = json.loads((out / "sealed.json").read_text(encoding="utf-8"))
    assert {s["kind"] for s in sealed} == {"receipt", "fab_export"}
    for s in sealed:
        ours = seal(s["kind"], s["payload"], key=KEY, seq=s["seq"], issued_at=WHEN, id=s["id"])
        assert s["text"].encode("utf-8") == ours
        assert open_envelope(s["text"].encode("utf-8"), key=KEY, kind=s["kind"]).payload == s["payload"]


def test_the_apps_script_export_reads_as_sheet1(apps_script):
    out, _ = apps_script
    export = fab.read_export((out / "export.json").read_bytes(), key=KEY)
    assert export.exported_at == WHEN
    assert [(r.sheet_row, r.job, r.designer) for r in export.rows] == [
        (2, "Testerson", "PE"), (3, "Fakeley_070826", "QA"), (5, "Mockford JD", "ZT"),
        (6, "Dummitt garage", "Avery"), (7, "Fakeley_07126", "QA")]
    assert [fab.install_date(r.install, near=date(2026, 6, 20)) for r in export.rows] == [
        date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15), date(2026, 7, 17), date(2026, 7, 20)]
    assert fab.diff(None, export.rows)[0] == fab.RowChange("seen", export.rows[0], None)


def test_the_apps_script_edit_log_reads_as_edits(apps_script):
    out, _ = apps_script
    edits = fab.read_edits((out / "edits.json").read_bytes(), key=KEY)
    assert edits.edits == (fab.Edit(WHEN, 2, "job", "Testerson", "Testerson closet"),)


def test_receipts_from_the_apps_script_open_in_python(apps_script):
    out, _ = apps_script
    for name, outbox_id in (("receipt.json", None), ("python-receipt.json", "ab" * 16)):
        receipt = open_envelope((out / name).read_bytes(), key=KEY, kind="receipt").payload
        assert set(receipt) == {"outbox_id", "sent_at", "channel", "ok", "error"}
        assert (receipt["channel"], receipt["ok"], receipt["error"]) == ("email", True, None)
        if outbox_id:
            assert receipt["outbox_id"] == outbox_id


def test_without_node_the_apps_script_tests_are_skipped_not_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert _node() is None
