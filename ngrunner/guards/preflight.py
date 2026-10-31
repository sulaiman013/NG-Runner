"""
The lock pre-flight, and the two small files it reads: the crosscheck and the CNC job sheet.

Why it exists: a lock with a $0 row, a stale check or a material still NOT CHECKED costs Jim rework
after the fact, so before he locks in chat the board shows what the tracker and NG Runner hold about
the job (docs/p1b/build-spec.md, section 5.5). Lock stays in chat in P1b, so this blocks nothing:
Preflight has items and nothing to gate on. Status words are reported as typed and never judged,
until decision 9 settles them in P2.

A tracker snapshot over 20 minutes old makes the whole result read "could not verify": a stale
tracker can't say a row is priced, and half an answer would read as a clean one. Each text has to
pass the lint, since the board and the job's items file show it. What came from a cell and fails is
replaced by a fixed line, so a hand-typed cell can't carry a path or an email through.

A job sheet is typed by hand, so its counts are read as typed and only its named lines are read.
Special Notes is never kept.
"""
from __future__ import annotations

import re
import zipfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree

from ngrunner.clock import fmt_date, pacific_day, utc
from ngrunner.guards.labels import DesignMatch
from ngrunner.guards.tracker import (
    SheetRow,
    TapeRow,
    TrackerSnapshot,
    plain_material,
    split_tape_name,
)
from ngrunner.messages.lint import check_line

MAX_SNAPSHOT_AGE = timedelta(minutes=20)    # design 5: the same age a drop to the tracker needs
CODES = frozenset({
    "snapshot_old", "no_rows", "duplicate_row", "not_priced", "zero_cost", "key_spelled_twice",
    "status_unknown", "nest_state", "no_check", "check_flagged", "check_older_than_design",
    "check_same_day", "labels_not_in_design",
})


class NestFacts(Protocol):
    """What the pre-flight reads of a store NestRecord, and nothing else."""

    @property
    def material(self) -> str: ...
    @property
    def state(self) -> str: ...
    @property
    def reasons(self) -> Sequence[str]: ...


@dataclass(frozen=True)
class PreflightItem:
    code: str
    text: str


@dataclass(frozen=True)
class Preflight:
    items: tuple[PreflightItem, ...]
    snapshot_taken: datetime


@dataclass(frozen=True)
class CheckFile:
    """A crosscheck in its newest form. verdict is "clean" or "flagged". job is the name on its
    first line: a check that names another job is no check of this one."""

    at: datetime
    verdict: str
    checkers: tuple[str, ...]
    job: str = ""


# ----- the pre-flight


def preflight(job: str, snap: TrackerSnapshot, nests: Sequence[NestFacts],
              check: CheckFile | None, catalog_date: date | None, *, now: datetime,
              labels: DesignMatch | None = None) -> Preflight:
    """What to look at before locking job. It reads and reports; it decides nothing."""
    if abs(utc(now) - snap.taken) > MAX_SNAPSHOT_AGE:
        return Preflight((_item("snapshot_old", _FIXED["snapshot_old"]),), snap.taken)
    return Preflight(
        (*_row_items(job, snap), *_nest_items(nests), *_check_items(job, check, catalog_date),
         *_label_items(labels)),
        snap.taken,
    )


# Each code's fixed line: what shows when a cell's words can't pass the lint, and never names them.
_FIXED = {
    "snapshot_old": "The tracker snapshot is over 20 minutes old, so this pre-flight could not"
                    " verify anything",
    "no_rows": "The tracker has no rows for this job",
    "duplicate_row": "A material has more than one row in the tracker; its name can't be shown"
                     " here",
    "not_priced": "A row has no price in the tracker; its name can't be shown here",
    "zero_cost": "A row costs $0 in the tracker; its name can't be shown here",
    "key_spelled_twice": "A material is in Pricing twice, so its price could not be verified; its"
                         " name can't be shown here",
    "status_unknown": "The tracker's status for this job can't be shown here",
    "nest_state": "A material's nest state could not be verified; its name can't be shown here",
    "no_check": "No crosscheck in the newest form was found, so the check could not be verified",
    "check_flagged": "The crosscheck flagged something; its checker's name can't be shown here",
    "check_older_than_design": "The crosscheck is older than the design's last change",
    "check_same_day": "Could not verify that the crosscheck is newer than the design",
    "labels_not_in_design": "Could not verify the labels against the parts catalog",
}


def _item(code: str, *tries: str) -> PreflightItem:
    """The first wording that passes the lint, else the code's fixed line."""
    for text in tries:
        if not check_line(text):
            return PreflightItem(code, text)
    return PreflightItem(code, _FIXED[code])


def _count(n: float, one: str, many: str) -> str:
    return f"{n:g} {one if n == 1 else many}"


# ----- rows, prices and status


def _row_items(job: str, snap: TrackerSnapshot) -> list[PreflightItem]:
    sheets = [row for row in snap.sheets if row.job == job]
    tape = [row for row in snap.tape if row.job == job]
    summary = [row for row in snap.jobs if row.job == job]
    items = _no_rows(sheets, tape, bool(summary))
    items += _duplicates(sheets, "Sheet Goods") + _duplicates(tape, "Edgeband")
    items += [item for row in sheets for item in _sheet_prices(row)]
    items += [item for row in tape for item in _tape_prices(row)]
    items += _keys_twice(snap, sheets, tape)
    items += [_status(row.status) for row in summary]
    return items


def _no_rows(sheets: list[SheetRow], tape: list[TapeRow], summary: bool) -> list[PreflightItem]:
    missing = [tab for tab, there in (("Sheet Goods", sheets), ("Edgeband", tape),
                                      ("Job Summary", summary)) if not there]
    if not missing:
        return []
    if len(missing) == 3:
        return [_item("no_rows", _FIXED["no_rows"])]
    return [_item("no_rows", f"The tracker has no {' or '.join(missing)} row for this job")]


def _duplicates(rows: Sequence[SheetRow | TapeRow], tab: str) -> list[PreflightItem]:
    counts = Counter(row.material for row in rows)
    return [_item("duplicate_row", f"{_shown(material)} has {count} rows on the {tab} tab")
            for material, count in counts.items() if count > 1]


def _shown(material: str) -> str:
    """A tracker name in words: a tape key loses its gauge, and an underscore becomes a space."""
    tape = split_tape_name(material)
    if tape:
        return f"{tape[1]} {plain_material(tape[0])} tape"
    return plain_material(material)


def _has(n: float) -> str:
    return "has" if n == 1 else "have"


def _costs(n: float) -> str:
    return "costs" if n == 1 else "cost"


def _sheet_prices(row: SheetRow) -> list[PreflightItem]:
    """Only a nested row has a cost to lose: the tracker fills Optimizer Sheets at the nest."""
    nested = row.optimizer_sheets
    if not nested or nested <= 0:
        return []
    amount = f"{_count(nested, 'sheet', 'sheets')} of {_shown(row.material)}"
    if row.price is None:
        return [_item("not_priced", f"{amount} {_has(nested)} no price in the tracker")]
    if row.price == 0 or not row.cost:
        return [_item("zero_cost", f"{amount} {_costs(nested)} $0 in the tracker")]
    return []


def _tape_prices(row: TapeRow) -> list[PreflightItem]:
    feet = row.optimizer_feet if row.optimizer_feet else row.calc_feet
    if not feet or feet <= 0:
        return []
    amount = f"{_count(feet, 'foot', 'feet')} of {_shown(row.material)}"
    if row.price is None:
        return [_item("not_priced", f"{amount} {_has(feet)} no price in the tracker")]
    # Cost follows Optimizer Lin Ft, so a row with only calc feet has none yet, and that is no fault
    if row.optimizer_feet and (row.price == 0 or not row.cost):
        return [_item("zero_cost", f"{amount} {_costs(feet)} $0 in the tracker")]
    return []


def _keys_twice(snap: TrackerSnapshot, sheets: list[SheetRow],
                tape: list[TapeRow]) -> list[PreflightItem]:
    """Only a key this job uses: another job's material can't change this job's price."""
    used = {row.material.strip() for row in (*sheets, *tape)}
    return [_item("key_spelled_twice",
                  f"{_shown(key)} is in Pricing twice, spelled two ways, so its price could not be"
                  " verified")
            for key in snap.keys_spelled_twice() if key in used]


def _status(status: str) -> PreflightItem:
    if not status.strip():
        return _item("status_unknown", "The tracker shows no status for this job")
    return _item("status_unknown", f'The tracker\'s status reads "{status}";'
                 " NG Runner reports it and does not judge it")


# ----- nest state


_STATE_WORDS = {
    "not_checked": "NOT CHECKED",
    "do_not_cut": "DO NOT CUT",
    "save_nest": "Save the nest in the Optimizer so it can be checked",
    "not_posted": "not posted yet",
}
_FAILED_NEST = "Don't fill Optimizer Sheets from this nest"


def _nest_items(nests: Sequence[NestFacts]) -> list[PreflightItem]:
    if not nests:
        return [_item("nest_state",
                      "NG Runner has read no programs for this job, so its nest state could not be"
                      " verified")]
    return [item for nest in nests for item in _nest_item(nest)]


def _nest_item(nest: NestFacts) -> list[PreflightItem]:
    state = str(getattr(nest.state, "value", nest.state)).casefold()
    if state == "nest_checked":
        return []
    name = plain_material(nest.material)
    words = _STATE_WORDS.get(state)
    if words is None:
        return [_item("nest_state", f"{name}: could not verify its nest state")]
    reasons = (nest.reasons,) if isinstance(nest.reasons, str) else tuple(nest.reasons)
    tail = [_FAILED_NEST] if state == "do_not_cut" else []
    full = ". ".join((f"{name}: {words}", *(r.rstrip(".") for r in reasons), *tail))
    short = ". ".join((f"{name}: {words}", *tail))
    return [_item("nest_state", full, short)]


# ----- the crosscheck


def _check_items(job: str, check: CheckFile | None, design: date | None) -> list[PreflightItem]:
    if check is None or (check.job and check.job != job):
        return [_item("no_check", _FIXED["no_check"])]
    items = []
    if check.verdict != "clean":
        names = ", ".join(name.replace("_", " ") for name in check.checkers)
        items.append(_item("check_flagged", f"The crosscheck flagged: {names}" if names
                           else "The crosscheck flagged something"))
    return items + _check_age(pacific_day(check.at), design)


def _check_age(day: date, design: date | None) -> list[PreflightItem]:
    """By Pacific day. The catalog's date has no time and comes from Jim's PC, and against it the
    five real checks read as the build spec measured them only in Pacific time (section 3.3). On
    one day the order can't be told, so it reads "could not verify", and so does no date at all."""
    if design is None:
        return [_item("check_same_day", "Could not verify that the crosscheck is newer than the"
                      " design: the parts catalog has no date for this job")]
    if day < design:
        return [_item("check_older_than_design", f"The crosscheck is from {fmt_date(day)}, older"
                      f" than the design's last change on {fmt_date(design)}")]
    if day == design:
        return [_item("check_same_day", "Could not verify that the crosscheck is newer than the"
                      f" design: both are from {fmt_date(day)}")]
    return []


def _label_items(labels: DesignMatch | None) -> list[PreflightItem]:
    """The labels fallback only ever adds a line. It clears nothing above."""
    if labels is None or labels.code != "some_missing":
        return []
    return [_item("labels_not_in_design", labels.note)]


# ----- reading a crosscheck file


_CHECK_HEAD = re.compile(r"(?P<job>\S.*) - crosscheck "
                         r"(?P<at>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z)")
_FLAGGED = re.compile(r"CROSSCHECK FLAGGED:\s*(?P<checkers>\S.*)")


def read_check_file(path: Path) -> CheckFile | None:
    """The newest form only: <Job> - crosscheck <UTC> on line 1, the verdict on line 4. Anything
    else, older forms included, is None, which the pre-flight counts as no check."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    head = _CHECK_HEAD.fullmatch(lines[0].strip()) if lines else None
    if head is None or len(lines) < 4:
        return None
    try:
        at = datetime.strptime(head["at"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    verdict = lines[3].strip()
    if verdict == "CROSSCHECK CLEAN":
        return CheckFile(at, "clean", (), head["job"])
    flagged = _FLAGGED.fullmatch(verdict)
    if flagged is None:
        return None
    names = tuple(name.strip() for name in flagged["checkers"].split(",") if name.strip())
    return CheckFile(at, "flagged", names, head["job"])


# ----- reading a job sheet


@dataclass(frozen=True)
class SheetCount:
    """One thickness line: 3/4, 1/4 or 1, the count as typed (None when blank) and its colour."""

    thickness: str
    sheets: int | None
    color: str


@dataclass(frozen=True)
class JobSheet:
    name: str
    abbr: str
    install: str        # as typed: Friday 10/16, 9 /29
    counts: tuple[SheetCount, ...]


_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_DOCUMENT = 20_000_000      # a job sheet's text is a few KB; this only stops a runaway file
_FIELD = re.compile(r"\s*(Job Name|Job Abbr|Install Date)\s*:\s*(.*)", re.IGNORECASE)
_THICKNESS = re.compile(
    r'\s*(?:(?P<glyph>[¾¼])[”"″]?|(?P<typed>3/4|1/4|1)[”"″])\s*:?\s*(?P<rest>.*)'
)
_THICK_WORDS = {"¾": "3/4", "¼": "1/4"}
_COUNT = re.compile(r"([0-9]+)(?:\s*sheets?)?", re.IGNORECASE)
_COLOR = re.compile(r"\s*Colou?r\s*:?\s*(.*)", re.IGNORECASE)
_FILE_NAME = re.compile(r"\s*File\s*name\s*:", re.IGNORECASE)
_NOTES = re.compile(r"\s*Special Notes", re.IGNORECASE)


def _paragraphs(data: bytes) -> list[str]:
    """The text of a .docx, a paragraph a line, with the standard library: no new dependency."""
    with zipfile.ZipFile(BytesIO(data)) as document:
        info = document.getinfo("word/document.xml")
        if info.file_size > _MAX_DOCUMENT:
            raise ValueError("too large to be a job sheet")
        root = ElementTree.fromstring(document.read(info))
    return ["".join(run.text or "" for run in paragraph.iter(f"{_WORD}t"))
            for paragraph in root.iter(f"{_WORD}p")]


def read_job_sheet(path: Path) -> JobSheet | None:
    """A CNC job sheet's named lines, or None when the file isn't one. Reading stops at Special
    Notes, which can say anything about a client."""
    try:
        paragraphs = _paragraphs(path.read_bytes())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return None
    fields: dict[str, str] = {}
    counts: list[SheetCount] = []
    install_open = False
    for line in paragraphs:
        if _NOTES.match(line):
            break
        found = _FIELD.fullmatch(line)
        thick = _THICKNESS.fullmatch(line)
        colour = _COLOR.fullmatch(line)
        if found:
            fields[found[1].casefold()] = found[2].strip()
            install_open = found[1].casefold() == "install date"
        elif thick:
            install_open = False
            counts.append(SheetCount(_thickness_of(thick), _count_of(thick["rest"]), ""))
        elif colour and counts and not counts[-1].color:
            install_open = False
            last = counts[-1]
            counts[-1] = SheetCount(last.thickness, last.sheets, colour[1].strip())
        elif not line.strip() or _FILE_NAME.match(line):
            install_open = False
        elif install_open:
            fields["install date"] = f"{fields['install date']} {line.strip()}".strip()
    return JobSheet(fields.get("job name", ""), fields.get("job abbr", ""),
                    fields.get("install date", ""), tuple(counts))


def _thickness_of(found: re.Match[str]) -> str:
    return _THICK_WORDS[found["glyph"]] if found["glyph"] else found["typed"]


def _count_of(text: str) -> int | None:
    """The count only when it is one: typed words are no number, and blank is not zero."""
    found = _COUNT.fullmatch(text.strip())
    return int(found[1]) if found else None
