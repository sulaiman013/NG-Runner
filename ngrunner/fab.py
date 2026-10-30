"""
The NG Fab Schedule as NG Runner sees it: the Apps Script's signed export of Sheet1, and the
changes between two exports.

Barbara's row is the trigger for a new job (design section 1), but Sheet1 gives NG Runner no
row IDs: rows are free text, kept roughly in install-date order, inserted anywhere, corrected in
place and moved to Archive when done (build spec section 2). NG Runner never edits the workbook
(risk C3), so it can't add IDs either. This module therefore decides row identity from the rows
alone, the same way every time: an unchanged row is the same row wherever it moved, a corrected
row is an edit of the row it replaced, and only a row nothing explains is new. Install dates are
read from the two Install Date cells only when they say one date; anything else is "could not
verify" (None), never a guess.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from ngrunner.clock import from_iso, pacific_day
from ngrunner.signing import EnvelopeError, open_envelope

EXPORT_KIND = "fab_export"
EDITS_KIND = "fab_edits"
SHEET = "Sheet1"
EditColumn = Literal["job", "designer", "install"]
COLUMNS: tuple[EditColumn, ...] = ("job", "designer", "install")   # the edit log's (B, C, D-E)
_EXPORT_KEYS = frozenset({"sheet", "exported_at", "rows"})
_ROW_KEYS = frozenset({"sheet_row", "job", "designer", "install", "fab_date"})
_EDIT_KEYS = frozenset({"at", "sheet_row", "column", "old", "new"})


class FabError(EnvelopeError):
    """A signed Fab file whose payload is not in the agreed form; the message says why, in plain
    words. It is an EnvelopeError, so a caller that drops unusable control-folder files catches
    both with one except."""


# ----- what an export holds


@dataclass(frozen=True)
class ExportRow:
    """One Sheet1 row as the sheet shows it. Text is kept exactly as exported, for display;
    comparisons normalise it."""

    sheet_row: int
    job: str
    designer: str
    install: tuple[str, str]
    fab_date: str


@dataclass(frozen=True)
class FabExport:
    """One accepted export. id is the envelope's, for the receiver's replay guard."""

    seq: int
    exported_at: datetime
    rows: tuple[ExportRow, ...]
    id: str = ""


@dataclass(frozen=True)
class Edit:
    """One cell edit the Apps Script saw in Sheet1. old is None when the cell was empty, or when
    Google did not say (an edit of several cells at once). A hint for linking a corrected name
    to its row, never the only evidence."""

    at: datetime
    sheet_row: int
    column: EditColumn
    old: str | None
    new: str | None


@dataclass(frozen=True)
class FabEdits:
    seq: int
    edits: tuple[Edit, ...]
    id: str = ""


# ----- reading the signed files


def read_export(data: bytes, *, key: bytes) -> FabExport:
    """The Fab export in data, checked: a signed fab_export envelope with Sheet1's rows.

    Raises EnvelopeError when the file is not a signed fab_export, and FabError when its payload
    is not exactly {"sheet": "Sheet1", "exported_at": ISO, "rows": [...]}. One bad row refuses
    the whole export: a partly read Sheet1 could make rows look gone, then new.
    """
    envelope = open_envelope(data, key=key, kind=EXPORT_KIND)
    payload = envelope.payload
    if set(payload) != _EXPORT_KEYS:
        raise FabError("the Fab export does not hold exactly sheet, exported_at and rows")
    if payload["sheet"] != SHEET:
        raise FabError(f"the Fab export is not of {SHEET}")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise FabError("the Fab export's rows are not a list")
    read = tuple(_export_row(r) for r in rows)
    if len({r.sheet_row for r in read}) != len(read):
        raise FabError("the Fab export lists one sheet row twice")
    return FabExport(envelope.seq, _time(payload["exported_at"], "export time"), read,
                     envelope.id)


def read_edits(data: bytes, *, key: bytes) -> FabEdits:
    """The edit log in data, checked: a signed fab_edits envelope, {"edits": [...]}, oldest
    first. Raises EnvelopeError or FabError as read_export does."""
    envelope = open_envelope(data, key=key, kind=EDITS_KIND)
    payload = envelope.payload
    if set(payload) != {"edits"} or not isinstance(payload["edits"], list):
        raise FabError("the Fab edit log does not hold exactly a list of edits")
    return FabEdits(envelope.seq, tuple(_edit(e) for e in payload["edits"]), envelope.id)


def _edit(raw: Any) -> Edit:
    if not isinstance(raw, dict) or set(raw) != _EDIT_KEYS:
        raise FabError("a Fab edit does not hold exactly at, sheet_row, column, old and new")
    if raw["column"] not in COLUMNS:
        raise FabError("a Fab edit names a column the edit log does not watch")
    if not all(v is None or isinstance(v, str) for v in (raw["old"], raw["new"])):
        raise FabError("a Fab edit's old or new value is not text")
    return Edit(_time(raw["at"], "edit time"), _sheet_row(raw["sheet_row"]), raw["column"],
                raw["old"], raw["new"])


def _export_row(raw: Any) -> ExportRow:
    if not isinstance(raw, dict) or set(raw) != _ROW_KEYS:
        raise FabError("a Fab export row does not hold exactly sheet_row, job, designer, install"
                       " and fab_date")
    install = raw["install"]
    if not (isinstance(install, list) and len(install) == 2):
        raise FabError("a Fab export row's install date is not its two cells")
    if not all(isinstance(v, str) for v in (raw["job"], raw["designer"], raw["fab_date"],
                                             *install)):
        raise FabError("a Fab export row holds something other than the text shown")
    return ExportRow(_sheet_row(raw["sheet_row"]), raw["job"], raw["designer"],
                     (install[0], install[1]), raw["fab_date"])


def _sheet_row(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FabError("a Fab row number is not a whole number from 1 up")
    return value


def _time(value: Any, what: str) -> datetime:
    if not isinstance(value, str):
        raise FabError(f"the {what} is not a time")
    try:
        return from_iso(value)
    except ValueError as exc:
        raise FabError(f"the {what} is not a full time with an offset") from exc


# ----- following rows from one export to the next

ChangeKind = Literal["new", "edited", "gone", "seen", "same"]


@dataclass(frozen=True)
class RowChange:
    """What happened to one row between two exports.

    new: row is in the current export only; previous is None.
    edited: row is the current form of previous (a corrected name, designer or install date).
    gone: row was in the previous export and is in the current one no more (most often moved
        to Archive); previous is None. Logged, never acted on.
    seen: the first export after install. Every row is seen there; none is new.
    same: only with diff(..., unchanged=True): row is previous unchanged, perhaps at another
        sheet row.
    """

    kind: ChangeKind
    row: ExportRow
    previous: ExportRow | None


def diff(
    previous_rows: Sequence[ExportRow] | None,
    current_rows: Sequence[ExportRow],
    edits: Iterable[Edit] = (),
    *,
    unchanged: bool = False,
) -> list[RowChange]:
    """The changes from previous_rows (the last accepted export; None before the first one) to
    current_rows, given the edit log entries made since (edits; any older ones only add noise).

    1. Rows with the same job, designer and install text (whitespace aside) are the same row, as
       a multiset: twins pair nearest sheet row first, and a third twin is new.
    2. A row left over with the same job text as a left-over previous row, and the same
       designer or the same install, at the nearest sheet row, is that row with its install
       date moved or its designer corrected. A name match outranks the rule below, so two rows
       that swap install dates stay themselves. A row whose designer and install both changed
       may be another job of the same name, so it is not paired here.
    3. A row left over whose designer and install equal a left-over previous row's, at the
       nearest sheet row, is that row with its name corrected. Where several could be, an edit
       log entry (or a chain of them) that turns the old job text into the new settles it; the
       log never pairs rows on its own.
    4. Whatever is left is new (current rows) or gone (previous rows).
    Rows with no job text are ignored. Changes come in current sheet order, then the gone rows
    in previous sheet order.
    """
    current = [r for r in current_rows if not _blank(r)]
    if previous_rows is None:
        return [RowChange("seen", r, None) for r in current]
    previous = [r for r in previous_rows if not _blank(r)]
    prev_left, cur_left = set(range(len(previous))), set(range(len(current)))
    same = _pair(previous, current, prev_left, cur_left, _row_key)
    moved = _pair(previous, current, prev_left, cur_left, lambda r: _job_key(r.job),
                  fits=_keeps_designer_or_install)
    renames = _renames(edits)
    corrected = _pair(previous, current, prev_left, cur_left, _place_key,
                      first=lambda p, c: _renamed(renames, _job_key(p.job), _job_key(c.job)))
    changes = [RowChange("same", current[j], previous[i]) for i, j in same] if unchanged else []
    changes += [RowChange("edited", current[j], previous[i]) for i, j in moved + corrected]
    changes += [RowChange("new", current[j], None) for j in cur_left]
    changes.sort(key=lambda c: c.row.sheet_row)
    gone = sorted((previous[i] for i in prev_left), key=lambda r: r.sheet_row)
    return changes + [RowChange("gone", r, None) for r in gone]


def _pair(
    previous: Sequence[ExportRow],
    current: Sequence[ExportRow],
    prev_left: set[int],
    cur_left: set[int],
    key: Callable[[ExportRow], Hashable],
    *,
    fits: Callable[[ExportRow, ExportRow], bool] | None = None,
    first: Callable[[ExportRow, ExportRow], bool] | None = None,
) -> list[tuple[int, int]]:
    """Pairs the rows still left whose keys are equal (and that fits() accepts), and takes them
    out of prev_left and cur_left. Pairs that first() holds for go first, then the nearest sheet
    rows; ties go to the higher row (the lower sheet row)."""
    by_key: dict[Hashable, list[int]] = {}
    for j in sorted(cur_left):
        by_key.setdefault(key(current[j]), []).append(j)
    options = sorted(
        (first is None or not first(previous[i], current[j]),
         abs(previous[i].sheet_row - current[j].sheet_row), previous[i].sheet_row,
         current[j].sheet_row, i, j)
        for i in sorted(prev_left) for j in by_key.get(key(previous[i]), ())
        if fits is None or fits(previous[i], current[j])
    )
    pairs = []
    for *_, i, j in options:
        if i in prev_left and j in cur_left:
            prev_left.discard(i)
            cur_left.discard(j)
            pairs.append((i, j))
    return pairs


def _renames(edits: Iterable[Edit]) -> dict[str, set[str]]:
    """The job texts the edit log says each job text became (old -> new, whitespace aside)."""
    graph: dict[str, set[str]] = {}
    for e in edits:
        if e.column == "job" and e.old is not None and e.new is not None:
            old, new = _job_key(e.old), _job_key(e.new)
            if old and new and old != new:
                graph.setdefault(old, set()).add(new)
    return graph


def _renamed(graph: dict[str, set[str]], old: str, new: str) -> bool:
    """Whether the edit log turns old into new, in one edit or a chain of them."""
    seen, todo = {old}, [old]
    while todo:
        for text in graph.get(todo.pop(), ()):
            if text == new:
                return True
            if text not in seen:
                seen.add(text)
                todo.append(text)
    return False


def _row_key(row: ExportRow) -> tuple[str, str, tuple[str, str]]:
    return (_job_key(row.job), _designer_key(row.designer), _install_key(row.install))


def _place_key(row: ExportRow) -> tuple[str, tuple[str, str]]:
    """Designer and install: what a row with a corrected name keeps."""
    return (_designer_key(row.designer), _install_key(row.install))


def _keeps_designer_or_install(previous: ExportRow, current: ExportRow) -> bool:
    return (_designer_key(previous.designer) == _designer_key(current.designer)
            or _install_key(previous.install) == _install_key(current.install))


def _job_key(text: str) -> str:
    """The job text for comparison: whitespace runs (spaces, tabs, no-break spaces) collapsed."""
    return " ".join(text.split())


def _designer_key(text: str) -> str:
    return " ".join(text.split()).casefold()


def _install_key(install: tuple[str, str]) -> tuple[str, str]:
    """Spaces inside a date mean nothing (" 7 /1" is "7/1"), so they are dropped entirely."""
    return ("".join(install[0].split()).casefold(), "".join(install[1].split()).casefold())


def _blank(row: ExportRow) -> bool:
    return not _job_key(row.job)


# ----- reading the install date

_WEEKDAYS = {
    name: number
    for number, names in enumerate((
        ("monday", "mon"), ("tuesday", "tue", "tues"), ("wednesday", "wed"),
        ("thursday", "thu", "thur", "thurs"), ("friday", "fri"), ("saturday", "sat"),
        ("sunday", "sun"),
    ))
    for name in names
}
# M/D, M/D/YY or M/D/20YY, ASCII digits only (\d alone would take any script's digits).
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|20\d{2}))?", re.ASCII)


@dataclass(frozen=True)
class _Cell:
    """What one Install Date cell says: a weekday, a month and day (and a year), or nothing."""

    weekday: int | None = None
    month_day: tuple[int, int] | None = None
    year: int | None = None


def install_date(install: tuple[str, str], *, near: date | datetime) -> date | None:
    """The date the two Install Date cells give ("Wednesday" + " 7 /1", "7/15/26" + "7/15/26"),
    or None when they don't say one date for sure: "could not verify", never a guess.

    Each cell may hold a weekday ("Wednesday", "wed."), a date (M/D, M/D/YY or M/D/20YY, spaces
    anywhere ignored) or nothing. Every date given must be the same day and every weekday must
    be that day's; anything else in a cell (a note, "TBD", "week of 7/1") can't be read. A date
    with no year takes the year that puts it nearest near (a date, or a time read as its Pacific
    day; a naive time is refused), the later one on a tie.
    """
    day = pacific_day(near) if isinstance(near, datetime) else near
    cells = [_read_cell(text) for text in install]
    if None in cells:
        return None
    read = [c for c in cells if c is not None]
    month_days = {c.month_day for c in read if c.month_day is not None}
    years = {c.year for c in read if c.year is not None}
    if len(month_days) != 1 or len(years) > 1:
        return None
    (month, dom), = month_days
    found = _on(years.pop(), month, dom) if years else _nearest(month, dom, day)
    if found is None or any(c.weekday not in (None, found.weekday()) for c in read):
        return None
    return found


def _read_cell(text: str) -> _Cell | None:
    """The cell read, or None when it holds something that is not a weekday or a date."""
    squeezed = "".join(text.split())
    if not squeezed:
        return _Cell()
    weekday = _WEEKDAYS.get(squeezed.rstrip(".,").casefold())
    if weekday is not None:
        return _Cell(weekday=weekday)
    m = _DATE.fullmatch(squeezed)
    if m is None:
        return None
    year = None if m[3] is None else int(m[3]) + (2000 if len(m[3]) == 2 else 0)
    return _Cell(month_day=(int(m[1]), int(m[2])), year=year)


def _on(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _nearest(month: int, day: int, near: date) -> date | None:
    """month/day in the year that puts it nearest near (the later date on a tie)."""
    options = [d for y in (near.year - 1, near.year, near.year + 1)
               if (d := _on(y, month, day)) is not None]
    if not options:
        return None
    return min(options, key=lambda d: (abs((d - near).days), -d.toordinal()))
