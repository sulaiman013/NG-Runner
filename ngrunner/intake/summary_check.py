"""
Strict check of an IC summary before NG Runner files it (design 3.5, the Fetch gate).

Why: Studio can hand back an early render that is a valid PDF yet holds no designs (risk K8: a
4-page render of a 19-page summary), and Assembly Reports' verify_pdf accepts a PDF that has no
PROJECT NAME at all. A render passes only when code has confirmed, from the PDF alone:

  1. the file is whole, as it was written (below), pypdf reads every object of it in strict mode,
     and every page carries a "Page k of N" footer that adds up;
  2. page 1 shows PROJECT NAME with a name beside it (matching it to the job is the caller's job);
  3. every design page 1 lists has a page with its WALL/AREA NAME (W X H) table holding at least
     one wall, and at least one elevation page carrying a drawing; and every design's pages are
     listed on page 1;
  4. the PULLED parts list is there, in one known layout throughout, with at least one item.

Whatever it cannot confirm becomes a plain sentence in `missing`, with a stable code alongside
in details["codes"]; it never guesses a pass.

Whole means all of this, each checked here because strict pypdf reads on past it (damaged real
renders passed each):

  - The end. The file ends at its %%EOF, with only white space after it, and holds no second PDF
    after the first: pypdf reads back from the last %%EOF, blind to the rest.
  - The index. Every classic index (xref) section along the chain, from the startxref before that
    %%EOF through each /Prev, is read strictly before pypdf opens the file: subsection headers of
    two numbers, entries of exactly 20 bytes, each entry in use pointing exactly at its own object.
    Even strict, pypdf repairs an index it cannot parse. The chain may pass through a
    cross-reference stream, and each object that stream lists must be where it says; a startxref
    or /Prev that leads to anything else is refused. Every real render seen so far (61) uses
    classic tables only: one section, or two in a linearized render.
  - The objects. Every object the index lists is read, and its parse must end at its endobj: one
    bit in a 'stream' keyword, or a digit turned into '>', makes pypdf read a stream as a shorter
    plain dictionary. Every reference they hold must lead to an object the index lists, and so must
    whatever reading the pages looks up: pypdf finds an object marked free, or behind a /Prev that
    lost its name, by searching the file (the PDF standard reads it as null), past every check.
  - The filters. Every stream's /Filter is made of the standard filter names (not the short ones
    of inline images), with /DecodeParms in the shape they need.
  - The checksums. Every Flate stream inflates whole under its own checksum (zlib's adler32), any
    ASCII85 layer before it (Studio wraps most streams in one) decoded strictly first: pypdf's
    decoder drops the checksum and inflates what it can, so a flipped bit read on as other digits.
  - What the streams decode to, by pypdf's own decoders with any predictor applied. A picture or
    its mask: exactly the Height x ceil(Width x components x bits / 8) bytes its dictionary
    describes. A font file: its /Length1 (with /Length2 and /Length3 in a Type 1 file). A /ToUnicode
    stream: a CMap. Reading the text never decodes a picture or a font file, and no checksum reaches
    a stream whose /Filter key lost its name.
  - The drawings. Every Do on a page draws a picture that page holds, so the elevation check counts
    only what is there. Every real Do does; a form (no real render has one) is refused too, because
    nothing here reads inside it.

What passes, because no checksum covers it and neither strict pypdf nor any check here trips on it:

  - A changed number, name or string, or a renamed key, that nothing reads. For example a page's
    size or rotation, a font's widths or name, a mask's /Decode, a picture's /Type, the strings of
    the Info dictionary (CreationDate among them) and the trailer's /ID. The key that hangs a font
    file, a /ToUnicode map or a /SMask on its owner, which then goes unused. A /DecodeParms value
    that leaves a picture's size as it was. A stream's /Length one too high, which pypdf reads past
    to the same bytes. A reference changed to another object of a fitting kind (another picture's).
  - An index entry in use marked free when nothing refers to its object, and any change to such an
    object short of its checksum: a linearized render's first dictionary and hint stream, whose
    /Filter key can be renamed. A hybrid file's /XRefStm, left to pypdf: changed so that pypdf
    still finds the stream, it passes; if pypdf no longer does, the objects that stream alone
    lists cannot be read.
  - Stream data no checksum reaches: a picture in its own image format (JPEG and the like; real
    renders hold none, and it counts as stored), a compact font file (/FontFile3, which has no
    length to compare either), a stream with no filter (a linearized render's XML metadata), a
    stream behind a filter other than ASCII85 before Flate (which can change the text), anything
    after a Flate stream's end (no real stream has anything there), what a cross-reference stream
    stored unfiltered says of objects beyond where they start, and the slack in ASCII85's last
    group, which decodes to the same bytes.
  - What pypdf does not read: a comment, the PDF version in the first line, the bytes between
    objects, the second '>' closing a dictionary, white space turned into other white space.
  - A change that leaves a valid PDF. A well-formed incremental update appended after the %%EOF,
    with or without junk before it, passes with what it changes (Studio never appends one, and two
    fetches would both need it). A valid PDF of another design cannot be seen in one file at all.

Of these, two fetches see whatever changes the text or a picture, unless both carry the change.

A pass is not enough to file. A render Studio has not finished can be complete in itself: its
footers count its own pages, and nothing ties the number of elevation pages to the walls (five
walls and one elevation page is common), so no check of a single file can tell it from a finished
render. The Fetch gate's other half is the caller's (design 3.5): file only when two fetches at
least 10 minutes apart both pass and have the same content_fingerprint().

Reading is positional. pypdf reports where each run of text and each picture is drawn; runs are
joined into lines, and a label's value is the text beside it on the same baseline. A plain text
dump interleaves columns (BUILD.md Step 1), and when a value is blank a regex on it reads the
next label as the value.

The two PULLED layouts (BUILD.md Step 1) are told apart by the header row, never by page number:

  A  "PULLED QTY ITEM # ..."       two lists side by side, quantity first
  B  "PULLED ITEM # QUANTITY ..."  one list, item number first

Every render seen so far pairs A with landscape pages and B with portrait pages.

content_fingerprint() hashes what the summary shows: on each page, its lines in reading order and
then the sha256 of each picture it draws (decoded, with any mask), in the order it draws them. Two
renders that differ only in bytes or metadata (CreationDate) get the same value; a change to any
word, number or name, to a drawing, or one page more or fewer, gets a new one. Where a picture sits
on its page does not count, nor does a line, a box or an inline image (no real render has one) that
the page draws itself. A render that fails the check as damaged has no value; damage the check
cannot see (above) that leaves the text and the pictures as they were leaves the value as it was
too, so two fetches agree over it. The value is only as stable as pypdf's text extraction and
decoders, so details["pypdf"] names the version that read it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import re
import unicodedata
import zlib
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from pypdf import __version__ as _PYPDF_VERSION
from pypdf.filters import decode_stream_data
from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, IndirectObject, NameObject,
                           NullObject, NumberObject, StreamObject, read_object)

PulledLayout = Literal["A", "B"]
Source = str | os.PathLike[str] | bytes
Problem = tuple[str, str]  # (stable code, plain sentence)

# ----- text helpers (pure: they take strings, so they are tested with made-up text)

# "<name> - (<category>)": the category is the LAST bracket, so a name may carry its own brackets.
_DESIGN_RE = re.compile(r"^(?P<name>.*\S)\s+-\s+\((?P<category>[^()]*)\)$")
# '1: 49" X 95 1/2"': a wall or area name, then width X height in inches.
_INCHES = r'\d+(?:\.\d+)?(?: \d+/\d+)?"'
_WALL_AREA_RE = re.compile(rf"^(?P<name>[^:]*[^:\s]) *: *(?P<width>{_INCHES}) X (?P<height>{_INCHES})$")
# First three header words of one PULLED list, and how many lists sit side by side in that layout.
_PULLED_LAYOUTS: dict[tuple[str, ...], tuple[PulledLayout, int]] = {
    ("PULLED", "QTY", "ITEM #"): ("A", 2),
    ("PULLED", "ITEM #", "QUANTITY"): ("B", 1),
}


def _clean(text: str) -> str:
    """One canonical spelling of a piece of text: NFKC, single spaces, no ends."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def split_design_list(lines: Iterable[str]) -> tuple[list[tuple[str, str]], str]:
    """
    Split page 1's DESIGNS IN PROJECT lines into (name, category) pairs.

    Studio prints each design as "<name> - (<category>)" and wraps long ones over several lines,
    so a design ends on the first line where the text so far closes with " - (...)". Returns the
    pairs and whatever text never closed (empty when the whole list was read).
    """
    designs: list[tuple[str, str]] = []
    pending = ""
    for line in lines:
        pending = f"{pending} {_clean(line)}".strip()
        match = _DESIGN_RE.match(pending)
        if match:
            designs.append((match["name"], _clean(match["category"])))
            pending = ""
    return designs, pending


def parse_wall_area(text: str) -> tuple[str, str, str] | None:
    """Read one WALL/AREA NAME (W X H) entry as (name, width, height); None if it is not one."""
    match = _WALL_AREA_RE.match(_clean(text))
    return (match["name"], match["width"], match["height"]) if match else None


def pulled_layout_of(header: Sequence[str]) -> PulledLayout | None:
    """Name the PULLED layout from its header row (words left to right); None if unknown."""
    lists: list[list[str]] = []
    for word in (_clean(w).upper() for w in header):
        if word == "PULLED":
            lists.append([word])
        elif lists:
            lists[-1].append(word)
        elif word:
            return None  # a header row starts with PULLED
    if not lists:
        return None
    fields = tuple(lists[0][:3])
    known = _PULLED_LAYOUTS.get(fields)
    if known is None or any(tuple(other[:3]) != fields for other in lists):
        return None
    layout, list_count = known
    return layout if len(lists) == list_count else None


# ----- the verdict


@dataclass(frozen=True)
class SummaryCheck:
    """
    The verdict on one render of an IC summary.

    ok             True only when every check passed; `missing` is then empty. Not enough on its
                   own to file the render (see check_summary).
    project_name   the name beside PROJECT NAME on page 1; None when absent or blank.
    page_count     pages in the file (0 when it could not be read).
    designs        the design names page 1 lists, in order, without their "(category)".
    pulled_layout  "A" or "B" (see the module notes); None without one known layout throughout.
    missing        one plain sentence per problem, in page order, each fit for one numbered line.
    details        JSON-ready facts: "codes" (one stable code per `missing` line), "sha256",
                   "pypdf" (the version that read the text), "creation_date", "fingerprint",
                   "orientation", "page_numbers" ("match", "wrong" or "absent"), "designs" (each
                   with its category, WALL/AREA pages, walls and elevations) and "pulled" (its
                   "pages" and "item_lines", the lines of text under ITEM #). A file that could
                   not be read has only "codes", "sha256", "pypdf" and, when pypdf raised or the
                   file is damaged, "error": what pypdf raised, or which part is damaged (for
                   the log, e.g. "stream 12: Flate: ...").
    """
    ok: bool
    project_name: str | None
    page_count: int
    designs: list[str]
    pulled_layout: PulledLayout | None
    missing: list[str]
    details: dict[str, Any] = field(default_factory=dict)


# ----- reading the PDF: text lines and pictures, with their positions

_SAME_ROW = 1.0  # points: text this close vertically shares a baseline
_PAGE_NUMBER_RE = re.compile(r"^Page (\d+) of (\d+)$")


@dataclass(frozen=True)
class _Line:
    x: float
    y: float
    size: float
    text: str


@dataclass(frozen=True)
class _Picture:
    width: float  # as drawn, in points
    height: float
    digest: str  # what is drawn (_picture_digest)


@dataclass(frozen=True)
class _Page:
    number: int
    width: float
    height: float
    lines: tuple[_Line, ...]
    pictures: tuple[_Picture, ...]  # one per Do, in the order the page draws them


class _Unusable(Exception):
    """The file cannot be read far enough to judge its content."""

    def __init__(self, code: str, message: str, **facts: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.facts = code, message, facts


def _damaged(why: str) -> _Unusable:
    """The file is not as it was written: `why` goes to the log, plain words to Jim."""
    return _Unusable("unreadable", "The PDF is damaged.", error=why)


def _load(source: Source) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    return Path(source).read_bytes()


def _xobjects(page: Any) -> dict[Any, Any]:
    """What a page's Do operators can name: its /Resources /XObject, references kept."""
    resources = _entry(page, "/Resources")
    xobjects = _entry(resources, "/XObject") if isinstance(resources, DictionaryObject) else None
    return xobjects if isinstance(xobjects, DictionaryObject) else {}


def _read_page(number: int, page: Any, digests: dict[tuple[int, int], str]) -> _Page:
    """
    One pass of pypdf's text extraction, keeping where each text run and picture is drawn.

    `digests` gives each picture by (generation, number). A page that draws anything else is
    refused: every real page draws only pictures it holds, and nothing here reads inside a form.
    """
    runs: list[list[Any]] = []  # [x, y, size, text]; runs at one spot are one line (font change)
    pictures: list[_Picture] = []
    xobjects = _xobjects(page)

    def on_text(text: str, cm: list[float], tm: list[float], _font: Any, font_size: float) -> None:
        if not text.strip():
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        if runs and abs(runs[-1][0] - x) <= 0.5 and abs(runs[-1][1] - y) <= 0.5:
            runs[-1][3] += text
        else:
            size = font_size * math.hypot(tm[0], tm[1]) * math.hypot(cm[0], cm[1])
            runs.append([x, y, size, text])

    def on_operator(operator: bytes, operands: Any, cm: list[float], _tm: Any) -> None:
        if operator == b"Do":
            drawn = xobjects.get(operands[0])
            digest = isinstance(drawn, IndirectObject) and digests.get((drawn.generation, drawn.idnum))
            if not digest:
                raise _damaged(f"page {number} draws something that is not a picture it holds")
            pictures.append(_Picture(math.hypot(cm[0], cm[1]), math.hypot(cm[2], cm[3]), digest))

    page.extract_text(visitor_text=on_text, visitor_operand_before=on_operator)
    lines = tuple(_Line(round(x, 1), round(y, 1), round(size, 1), text)
                  for x, y, size, raw in runs if (text := _clean(raw)))
    box = page.mediabox
    return _Page(number, float(box.width), float(box.height), lines, tuple(pictures))


# ----- is the file whole, as it was written? (damage strict pypdf reads past)

_WHITESPACE = b"\0\t\n\f\r "  # PDF's white-space characters
_EOL = rb"(?:\r\n|\r|\n)"  # a line end
_PDF_HEADER = re.compile(rb"%PDF-\d\.\d")
_OBJECT_HEADER = re.compile(rb"(\d+)[\0\t\n\f\r ]+(\d+)[\0\t\n\f\r ]+obj[\0\t\n\f\r ]*")
_ENDOBJ = re.compile(rb"[\0\t\n\f\r ]*endobj")
_INFLATE_STEP = 1 << 20  # bytes of output per zlib call, then dropped: only the checksum is wanted
# A classic index (xref) section, as the PDF standard writes it (ISO 32000, 7.5.4): the keyword,
# subsections of "first-object count", each with `count` entries of exactly 20 bytes, then the
# trailer. The file ends with startxref, the newest section's offset, and %%EOF.
_TABLE = re.compile(rb"xref" + _EOL)
_SUBSECTION = re.compile(rb"(\d+) (\d+)" + _EOL)
_ENTRY = re.compile(rb"(\d{10}) (\d{5}) ([nf])(?: \r| \n|\r\n)")  # offset, generation, in use or free
_TRAILER = re.compile(rb"trailer[\0\t\n\f\r ]*")
_TAIL = re.compile(rb"startxref" + _EOL + rb"(\d+)" + _EOL + rb"%%EOF[\0\t\n\f\r ]*\Z")


def _end_damage(data: bytes) -> str | None:
    """One PDF, ending at its end marker: pypdf reads back from the last %%EOF, blind to the rest."""
    if data[data.rfind(b"%%EOF") + 5:].strip(_WHITESPACE):
        return "data after %%EOF"
    if _PDF_HEADER.search(data, data.find(b"%%EOF")):
        return "a second PDF after %%EOF"
    return None


class _Strict:
    """All that pypdf's parser asks of a reader for a trailer or cross-reference stream: to be strict."""
    strict = True


def _parsed(data: bytes, at: int) -> Any:
    """What pypdf's strict parser makes of the bytes at `at`, with no reader behind it; None if it cannot."""
    stream = io.BytesIO(data)
    stream.seek(at)
    try:
        return read_object(stream, _Strict())
    except Exception:  # noqa: BLE001 - pypdf's parser raises many types on a damaged dictionary
        return None


def _prev(dictionary: DictionaryObject, at: int) -> int | None:
    """The /Prev of a trailer or cross-reference stream: where the next older index section starts."""
    prev = dictionary.get("/Prev")
    if prev is not None and type(prev) is not NumberObject:
        raise ValueError(f"the index dictionary at byte {at} has a /Prev that is not an offset")
    return None if prev is None else int(prev)


def _stream_section(data: bytes, at: int, body: int) -> int | None:
    """
    The /Prev of the cross-reference stream at `at`, its dictionary starting at `body`.

    Anything else with an object header is refused: pypdf drops the sections behind it, with only
    a warning, and searches the file for their objects. What the stream lists, pypdf reads, and
    _index_damage checks.
    """
    stream = _parsed(data, body)
    if not isinstance(stream, StreamObject) or stream.get("/Type") != "/XRef":
        raise ValueError(f"the object at byte {at} is not a cross-reference stream")
    return _prev(stream, at)


def _section(data: bytes, at: int) -> tuple[list[tuple[int, int, int]], int | None]:
    """
    The classic index section at `at`, read strictly.

    Returns (number, generation, offset) for each entry in use, and the trailer's /Prev. Raises
    ValueError, saying where, at the first line that is not exactly as the standard writes it.
    """
    if (keyword := _TABLE.match(data, at)) is None:
        raise ValueError(f"no index at byte {at}")
    pos, in_use = keyword.end(), []
    while True:
        if (subsection := _SUBSECTION.match(data, pos)) is None:
            raise ValueError(f"the index line at byte {pos} is not a subsection header")
        pos, first, count = subsection.end(), int(subsection[1]), int(subsection[2])
        for number in range(first, first + count):
            if (entry := _ENTRY.match(data, pos)) is None:
                raise ValueError(f"the index entry for object {number}, at byte {pos}, is malformed")
            if entry[3] == b"n":
                in_use.append((number, int(entry[2]), int(entry[1])))
            pos = entry.end()
        if (trailer := _TRAILER.match(data, pos)) is not None:
            if not _is_dictionary(dictionary := _parsed(data, trailer.end())):
                raise ValueError(f"the trailer at byte {trailer.end()} cannot be read")
            return in_use, _prev(dictionary, trailer.end())


def _misplaced(data: bytes, entries: Iterable[tuple[int, int, int]]) -> str | None:
    """The first index entry (number, generation, offset) whose object does not start there."""
    for number, generation, offset in entries:
        header = _OBJECT_HEADER.match(data, offset)
        if header is None or (int(header[1]), int(header[2])) != (number, generation):
            return f"object {number} is not where the index says"
    return None


def _table_damage(data: bytes) -> str | None:
    """
    Every classic index (xref) section well formed, and each object where its entry says.

    Read from the bytes before pypdf sees them, along the chain pypdf follows: the last startxref,
    then each /Prev, through cross-reference streams too. Even strict, pypdf repairs an index it
    cannot parse (it guesses a garbled entry from a search of the file, reads a 19-byte entry as if
    it had 20, and rebuilds an index whose count is wrong from the whole file) and searches the file
    for an object whose entry it cannot use.
    """
    startxref = data.rfind(b"startxref")
    if (tail := _TAIL.match(data, startxref) if startxref >= 0 else None) is None:
        return "the file does not end with startxref, an offset and %%EOF"
    entries: list[tuple[int, int, int]] = []
    seen: set[int] = set()
    try:
        at: int | None = int(tail[1])
        while at is not None:
            if at in seen:
                return f"the index at byte {at} is chained to itself"
            seen.add(at)
            if (header := _OBJECT_HEADER.match(data, at)) is not None:
                at = _stream_section(data, at, header.end())
            else:
                in_use, at = _section(data, at)
                entries += in_use
    except ValueError as error:  # a malformed line, or a number too long for Python to read
        return str(error)
    return _misplaced(data, entries)


def _index_damage(data: bytes, reader: PdfReader) -> str | None:
    """Each object pypdf's index lists, cross-reference streams' too, must start where it says."""
    return _misplaced(data, ((number, generation, offset) for generation, objects in reader.xref.items()
                             for number, offset in objects.items()))


def _indexed(reader: PdfReader) -> int:
    """
    How many objects pypdf's index lists.

    It grows when pypdf finds by searching the file an object that a reference names and no section
    lists: one marked free (one bit), or behind a /Prev that lost its name. The PDF standard reads
    such an object as null.
    """
    return sum(len(objects) for objects in reader.xref.values())


def _inflate_damage(data: bytes) -> str | None:
    """Why zlib finds `data` is not one whole Flate stream (its adler32 covers all it inflates)."""
    inflater = zlib.decompressobj()
    try:
        while data and not inflater.eof:  # at the end, zlib keeps what follows in unconsumed_tail too
            inflater.decompress(data, _INFLATE_STEP)
            data = inflater.unconsumed_tail
        if not inflater.eof:
            inflater.flush()  # what zlib still holds once all the input is in
    except zlib.error as error:
        return f"Flate: {error}"
    if not inflater.eof:
        return "Flate: the data stops before its end"
    return None


def _entry(dictionary: DictionaryObject, key: str) -> Any:
    """A dictionary value, followed if indirect; None when absent or null (the same, to PDF)."""
    value = dictionary.get(key)
    value = None if value is None else value.get_object()
    return None if isinstance(value, NullObject) else value


def _filters(stream: StreamObject) -> list[Any]:
    """The stream's filters, in the order a reader applies them."""
    filters = _entry(stream, "/Filter")
    return [] if filters is None else list(filters) if isinstance(filters, ArrayObject) else [filters]


# The stream filters of the PDF standard (ISO 32000, 7.4). Their short names (AHx, A85, LZW, Fl,
# RL, CCF, DCT) belong to inline images only, so in a stream's dictionary they are damage too.
_STANDARD_FILTERS = frozenset({"/ASCIIHexDecode", "/ASCII85Decode", "/LZWDecode", "/FlateDecode",
                               "/RunLengthDecode", "/CCITTFaxDecode", "/JBIG2Decode", "/DCTDecode",
                               "/JPXDecode", "/Crypt"})


def _filter_damage(stream: StreamObject) -> str | None:
    """
    Why a stream's /Filter or /DecodeParms is not one a reader can apply, or None.

    A picture is never decoded to read the text, so a damaged filter name would leave a drawing
    no reader can show while its page still draws it.
    """
    names = _filters(stream)
    if not all(isinstance(name, NameObject) and name in _STANDARD_FILTERS for name in names):
        return "/Filter is not a standard filter name, or a list of them"
    parms = _entry(stream, "/DecodeParms")
    if parms is None or (_is_dictionary(parms) and len(names) == 1):
        return None
    if isinstance(parms, ArrayObject) and len(parms) == len(names) and all(
            isinstance(each.get_object(), NullObject) or _is_dictionary(each.get_object()) for each in parms):
        return None
    return "/DecodeParms does not match /Filter"


def _is_dictionary(value: Any) -> bool:
    """A dictionary, not a stream (pypdf's StreamObject is a DictionaryObject too)."""
    return isinstance(value, DictionaryObject) and not isinstance(value, StreamObject)


def _stream_damage(stream: StreamObject) -> str | None:
    """
    Why a stream does not decode whole: filters no reader can apply, or a failed checksum.

    Only Flate carries a checksum (zlib's adler32, over everything it inflates to), so the stored
    bytes are decoded up to the first Flate layer: through ASCII85 strictly (Studio wraps most
    streams in it), then by zlib itself. pypdf would drop the checksum and inflate what it can.
    None when the filters are sound and no checksum can be reached.
    """
    if (why := _filter_damage(stream)) is not None:
        return why
    names = _filters(stream)
    if "/FlateDecode" not in names:
        return None
    data = stream._data  # the bytes as stored: get_data() would decode them, damage and all
    for name in names[:names.index("/FlateDecode")]:
        if name != "/ASCII85Decode":
            return None  # a layer that cannot be decoded strictly here
        try:
            data = base64.a85decode(data.strip(_WHITESPACE), adobe=True, ignorechars=_WHITESPACE)
        except ValueError as error:
            return f"ASCII85: {error}"
    return _inflate_damage(data)


# How many numbers one pixel takes in each colour space of the PDF standard (ISO 32000, 8.6),
# bar the two that say so themselves: /ICCBased (its /N) and /DeviceN (its list of colorants).
_COMPONENTS = {"/DeviceGray": 1, "/CalGray": 1, "/Indexed": 1, "/Separation": 1,
               "/DeviceRGB": 3, "/CalRGB": 3, "/Lab": 3, "/DeviceCMYK": 4}
# Filters that leave a picture in its own image format: pypdf does not decode them to samples.
_IMAGE_FORMATS = frozenset({"/DCTDecode", "/JPXDecode", "/CCITTFaxDecode", "/JBIG2Decode"})
_CMAP = re.compile(rb"\bbegincmap\b.*\bendcmap\b", re.DOTALL)  # what a /ToUnicode stream decodes to


def _decoded(stream: StreamObject) -> bytes:
    """What pypdf's decoders make of a stream, predictors applied, kept nowhere (get_data() keeps it)."""
    return decode_stream_data(stream) if _filters(stream) else stream._data


def _components(space: Any) -> int | None:
    """How many numbers one pixel takes in a colour space; None if it is not one."""
    family = space[0] if isinstance(space, ArrayObject) else space
    if family == "/ICCBased":
        return int(_entry(space[1].get_object(), "/N"))
    if family == "/DeviceN":
        return len(space[1].get_object())
    return _COMPONENTS.get(family) if isinstance(family, NameObject) else None


def _samples_size(picture: StreamObject) -> int | None:
    """The bytes a picture's dictionary describes: Height x ceil(Width x components x bits / 8), or None."""
    try:
        components, bits = 1, 1
        if _entry(picture, "/ImageMask") != BooleanObject(True):
            components = _components(_entry(picture, "/ColorSpace"))
            bits = int(_entry(picture, "/BitsPerComponent"))
        row_bits = int(_entry(picture, "/Width")) * components * bits
        return int(_entry(picture, "/Height")) * ((row_bits + 7) // 8)
    except (AttributeError, IndexError, TypeError, ValueError):  # a key is missing, or holds no such thing
        return None


def _samples(number: int, picture: StreamObject) -> bytes:
    """
    A picture's decoded bytes, once they have proved to be what its dictionary describes.

    Nothing else looks: reading the text never decodes a picture, and no checksum covers its
    dictionary or reaches a stream whose /Filter key lost its name, so damage to either would pass
    while no reader can draw it. A picture in its own image format has no size to compare here, and
    counts as stored. pypdf will not decode one past 75 MB (the largest real one is 12 MB), so that
    one is refused too.
    """
    if any(name in _IMAGE_FORMATS for name in _filters(picture)):
        return picture._data
    try:
        decoded = _decoded(picture)
    except Exception as error:  # noqa: BLE001 - pypdf's decoders raise many types on damaged data
        raise _damaged(f"stream {number} cannot be decoded ({type(error).__name__})") from None
    if len(decoded) != _samples_size(picture):
        raise _damaged(f"stream {number} does not decode to the size its dictionary gives")
    return decoded


def _picture_digest(number: int, picture: StreamObject) -> str:
    """The sha256 of what a picture draws: its samples, then any mask's. Raises _Unusable if damaged."""
    digest = hashlib.sha256(_samples(number, picture))
    for key in ("/SMask", "/Mask"):  # a /Mask may instead be a list of colours, which does not count
        if isinstance(mask := _entry(picture, key), StreamObject):
            digest.update(_samples(number, mask))
    return digest.hexdigest()


def _font_file_damage(font_file: StreamObject) -> str | None:
    """Why a stream giving /Length1 does not decode to its stated size (a Type 1 file has three parts)."""
    try:
        size = sum(int(_entry(font_file, key) or 0) for key in ("/Length1", "/Length2", "/Length3"))
    except (TypeError, ValueError):
        size = None
    return None if len(_decoded(font_file)) == size else "it does not decode to the length it gives"


def _font_damage(dictionary: DictionaryObject) -> str | None:
    """
    Why a font descriptor's font file, or a font's /ToUnicode map, is not one a reader can use.

    A font file has no other check once its /Filter key is renamed, and a map that is not a CMap
    changes the text pypdf reads. A /ToUnicode may name a predefined map instead of holding one.
    """
    for key in ("/FontFile", "/FontFile2"):
        if key in dictionary and not (isinstance(font_file := _entry(dictionary, key), StreamObject)
                                      and "/Length1" in font_file):
            return f"{key} leads to no font file"
    to_unicode = _entry(dictionary, "/ToUnicode")
    if to_unicode is None or isinstance(to_unicode, NameObject):
        return None
    if isinstance(to_unicode, StreamObject) and _CMAP.search(_decoded(to_unicode)):
        return None
    return "/ToUnicode does not decode to a CMap"


def _nested(value: Any) -> Iterator[Any]:
    """All that one object holds: its dictionaries, arrays and references, none of them followed."""
    pending = [value]
    while pending:
        item = pending.pop()
        yield item
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _read_objects(data: bytes, reader: PdfReader) -> dict[tuple[int, int], str]:
    """
    Read every object pypdf's index lists, and refuse what strict reading passes over.

    Reading the text would reach only what the pages use. Returns each picture's digest by
    (generation, number). Raises _Unusable at the first damage.
    """
    if (why := _index_damage(data, reader)) is not None:
        raise _damaged(why)
    listed = [(generation, number) for generation, objects in reader.xref.items() for number in objects]
    for generation, number in listed:
        reader.resolved_objects.pop((generation, number), None)  # a stream's /Length may be read already
        reader.get_object(IndirectObject(number, generation, reader))
        if _ENDOBJ.match(data, reader.stream.tell()) is None:  # where pypdf stopped reading it
            raise _damaged(f"object {number} does not end at its endobj")
    for number in list(reader.xref_objStm):  # what an object stream holds has no endobj of its own
        reader.get_object(number)
    objects = list(reader.resolved_objects.items())
    for (_generation, number), obj in objects:  # every checksum first: what follows decodes these streams
        if isinstance(obj, StreamObject) and (why := _stream_damage(obj)) is not None:
            raise _damaged(f"stream {number}: {why}")
    digests: dict[tuple[int, int], str] = {}
    for key, obj in objects:
        if isinstance(obj, StreamObject):
            if obj.get("/Subtype") == "/Image":
                digests[key] = _picture_digest(key[1], obj)
            if "/Length1" in obj and (why := _font_file_damage(obj)) is not None:
                raise _damaged(f"stream {key[1]}: {why}")
        for item in _nested(obj):
            if isinstance(item, IndirectObject):
                item.get_object()  # pypdf finds one no section lists by searching (see _indexed), or raises
            elif isinstance(item, DictionaryObject) and (why := _font_damage(item)) is not None:
                raise _damaged(f"object {key[1]}: {why}")
    return digests


def _read(data: bytes) -> tuple[list[_Page], str | None]:
    """The file's pages and its CreationDate; raises _Unusable when they cannot be had."""
    if not data.startswith(b"%PDF-"):
        raise _Unusable("not_pdf", "The file is not a PDF.")
    if b"%%EOF" not in data[-1024:]:
        raise _Unusable("cut_off", "The PDF is cut off: its end marker is missing.")
    try:
        # the end and the index first, from the bytes: pypdf would repair them as it opens the file
        if (damage := _end_damage(data) or _table_damage(data)) is not None:
            raise _damaged(damage)
        # strict: a damaged object is an error. Lax pypdf drops the rest of it and reads on, so the
        # text it returns is no longer the file's (fuzzing a real render passed with changed text).
        reader = PdfReader(io.BytesIO(data), strict=True)
        indexed = _indexed(reader)
        digests = _read_objects(data, reader)
        pages = [_read_page(n, page, digests) for n, page in enumerate(reader.pages, 1)]
        created = (reader.metadata or {}).get("/CreationDate")
        if _indexed(reader) != indexed:  # what it found that way, no check here has seen
            raise _damaged("pypdf searched the file for an object no index section lists")
    except _Unusable:
        raise
    except Exception as error:  # noqa: BLE001 - pypdf raises many types on a damaged file: all unreadable
        raise _Unusable("unreadable", "The PDF could not be read.", error=type(error).__name__) from None
    if not pages:
        raise _Unusable("no_pages", "The PDF has no pages.")
    return pages, None if created is None else str(created)


def _find(page: _Page, text: str) -> _Line | None:
    return next((line for line in page.lines if line.text == text), None)


def _rows(lines: Iterable[_Line]) -> list[tuple[float, float, str]]:
    """Group lines into rows by baseline, top down: (y, largest size, text left to right)."""
    rows: list[list[_Line]] = []
    for line in sorted(lines, key=lambda ln: (-ln.y, ln.x)):
        if rows and abs(rows[-1][0].y - line.y) <= _SAME_ROW:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [(row[0].y, max(ln.size for ln in row),
             " ".join(ln.text for ln in sorted(row, key=lambda ln: ln.x))) for row in rows]


def _key(name: str) -> str:
    """Design names match ignoring case and spacing (Studio wraps them differently per page)."""
    return "".join(name.split()).casefold()


def _page_numbers(pages: list[_Page]) -> tuple[str, list[Problem]]:
    """Every page must carry "Page k of N", with k and N matching the file (all real renders do)."""
    marks: list[tuple[int, int] | None] = []
    for page in pages:
        found = [m for line in page.lines if (m := _PAGE_NUMBER_RE.match(line.text))]
        marks.append((int(found[0][1]), int(found[0][2])) if len(found) == 1 else None)
    if all(mark is None for mark in marks):
        return "absent", [("no_page_numbers",
                           "Could not verify that no pages are missing: the pages are not numbered.")]
    totals = {mark[1] for mark in marks if mark}
    if len(totals) == 1 and (total := totals.pop()) != len(pages):
        return "wrong", [("page_numbers",
                          f"The file has {len(pages)} pages but they are numbered out of {total}.")]
    if any(mark != (k, len(pages)) for k, mark in enumerate(marks, 1)):
        return "wrong", [("page_numbers", "The page numbers do not match the pages in the file.")]
    return "match", []


# ----- page 1


def _project_name(cover: _Page) -> str | None:
    """The text beside the PROJECT NAME label; "" when the label stands alone, None without it."""
    label = _find(cover, "PROJECT NAME")
    if label is None:
        return None
    beside = sorted((ln for ln in cover.lines if abs(ln.y - label.y) <= _SAME_ROW and ln.x > label.x + 1),
                    key=lambda ln: ln.x)
    return " ".join(ln.text for ln in beside)


def _design_list(cover: _Page) -> list[str]:
    """The DESIGNS IN PROJECT value: rows from the label's baseline down to the next field."""
    label = _find(cover, "DESIGNS IN PROJECT")
    if label is None:
        return []
    next_label_y = max((ln.y for ln in cover.lines
                        if abs(ln.x - label.x) <= 1 and ln.y < label.y - _SAME_ROW), default=-math.inf)
    rows = _rows(ln for ln in cover.lines
                 if ln.x > label.x + 1 and next_label_y + _SAME_ROW < ln.y <= label.y + _SAME_ROW)
    block: list[tuple[float, float, str]] = []
    for y, size, text in rows:
        if not block and abs(y - label.y) > _SAME_ROW:
            break  # nothing beside the label: the list is empty
        if block and block[-1][0] - y > 1.6 * size:
            break  # a blank line ends the field (the client's phone often follows, unlabelled)
        block.append((y, size, text))
    return [text for _, _, text in block]


# ----- design pages

_DRAWING_MIN = 144.0  # points: a drawing is at least 2 in across; the logo is under 1 in


@dataclass
class _Design:
    name: str
    summary_pages: list[int] = field(default_factory=list)
    walls: list[tuple[str, str, str]] = field(default_factory=list)
    elevations: list[tuple[int, str, bool]] = field(default_factory=list)  # page, wall, has drawing


def _has_drawing(page: _Page) -> bool:
    return any(max(picture.width, picture.height) >= _DRAWING_MIN for picture in page.pictures)


def _heading(page: _Page) -> tuple[str, float, float] | None:
    """A design page's "Design: <name>" heading, joined across wrapped lines: (name, size, bottom y)."""
    tops = [ln for ln in page.lines if ln.text.startswith("Design:")]
    if not tops:
        return None
    top = max(tops, key=lambda ln: ln.y)
    parts, last = [top.text.removeprefix("Design:")], top
    while below := [ln for ln in page.lines if abs(ln.x - top.x) <= 1 and abs(ln.size - top.size) <= 0.5
                    and 0 < last.y - ln.y <= 1.6 * top.size]:
        last = max(below, key=lambda ln: ln.y)
        parts.append(last.text)
    return _clean(" ".join(parts)), top.size, last.y


def _elevation_wall(page: _Page, heading_size: float, heading_bottom: float) -> str | None:
    """An elevation page is titled "Wall <name>" in type larger than its heading, just below it."""
    titles = [ln for ln in page.lines if ln.size > heading_size + 0.5 and ln.y < heading_bottom]
    if not titles:
        return None
    title = max(titles, key=lambda ln: ln.y).text
    return title.removeprefix("Wall ") if title.startswith("Wall ") else None


def _read_designs(pages: list[_Page]) -> dict[str, _Design]:
    """Each design's WALL/AREA pages (with walls) and elevation pages, keyed by design name."""
    designs: dict[str, _Design] = {}
    for page in pages[1:]:
        heading = _heading(page)
        if heading is None:
            continue
        name, size, bottom = heading
        table = _find(page, "WALL/AREA NAME (W X H)")
        if table is not None:
            design = designs.setdefault(_key(name), _Design(name))
            design.summary_pages.append(page.number)
            for line in sorted(page.lines, key=lambda ln: -ln.y):
                wall = parse_wall_area(line.text) if abs(line.x - table.x) <= 2 and line.y < table.y else None
                if wall:
                    design.walls.append(wall)
        elif (wall_name := _elevation_wall(page, size, bottom)) is not None:
            designs.setdefault(_key(name), _Design(name)).elevations.append(
                (page.number, wall_name, _has_drawing(page)))
    return designs


def _design_problems(listed: list[tuple[str, str]], found: dict[str, _Design]) -> list[Problem]:
    """Every listed design needs its WALL/AREA table and elevations; every design page needs listing."""
    problems: list[Problem] = []
    counts = Counter(_key(name) for name, _ in listed)
    reported: set[str] = set()
    for name, _category in listed:
        key = _key(name)
        if counts[key] > 1:
            if key not in reported:
                reported.add(key)
                problems.append(("same_name_designs", f'Page 1 lists more than one design named "{name}", '
                                                      "so their pages cannot be told apart."))
            continue
        design = found.get(key)
        if design is None or not design.summary_pages:
            problems.append(("no_wall_area", f'Design "{name}" has no WALL/AREA table.'))
        elif not design.walls:
            problems.append(("empty_wall_area",
                             f'Design "{name}" has a WALL/AREA table with no walls in it.'))
        elevations = design.elevations if design else []
        if not elevations:
            problems.append(("no_elevations", f'Design "{name}" has no elevation drawings.'))
        problems += [("no_drawing", f'Design "{name}", Wall {wall}: the elevation page has no drawing.')
                     for _page, wall, drawn in elevations if not drawn]
    problems += [("unlisted_design",
                  f'The pages for design "{design.name}" are not in the page 1 design list.')
                 for key, design in found.items() if key not in counts]
    return problems


def _design_facts(name: str, category: str, design: _Design | None) -> dict[str, Any]:
    design = design or _Design(name)
    return {"name": name, "category": category, "wall_area_pages": list(design.summary_pages),
            "walls": [{"name": n, "width": w, "height": h} for n, w, h in design.walls],
            "elevations": [{"page": p, "wall": wall, "drawing": drawn}
                           for p, wall, drawn in design.elevations]}


# ----- PULLED pages


def _read_pulled(pages: list[_Page]) -> list[tuple[int, PulledLayout | None, int]]:
    """
    Each PULLED page as (page, layout from its header row, lines of text under ITEM #).

    Lines, not items: Studio wraps a long item name onto two or more lines. Counting items would
    mean parsing the rows, which is the takeoff's job; telling an empty list from one with
    something in it needs only the lines.
    """
    found = []
    for page in pages:
        heads = [ln for ln in page.lines if ln.text.upper() == "PULLED"]
        if not heads:
            continue
        head = max(heads, key=lambda ln: ln.y)
        row = sorted((ln for ln in page.lines if abs(ln.y - head.y) <= _SAME_ROW), key=lambda ln: ln.x)
        item_xs = [ln.x for ln in row if ln.text.upper() == "ITEM #"]
        item_lines = sum(1 for ln in page.lines
                         if ln.y < head.y - _SAME_ROW and any(abs(ln.x - x) <= 1.5 for x in item_xs))
        found.append((page.number, pulled_layout_of([ln.text for ln in row]), item_lines))
    return found


def _pulled_problems(pulled: list[tuple[int, PulledLayout | None, int]]) -> tuple[PulledLayout | None,
                                                                                    list[Problem]]:
    """The PULLED list must be there, in one known layout throughout, with at least one item."""
    layouts = {layout for _, layout, _ in pulled}
    if not pulled:
        return None, [("no_pulled", "There is no PULLED parts list.")]
    if None in layouts:
        where = ", ".join(str(number) for number, layout, _ in pulled if layout is None)
        return None, [("unknown_pulled_layout",
                       f"The PULLED parts list (page {where}) is laid out in a way NG Runner does not know.")]
    if len(layouts) > 1:
        return None, [("mixed_pulled_layout", "The PULLED pages mix the two PULLED layouts.")]
    layout = layouts.pop()
    if not sum(item_lines for *_, item_lines in pulled):
        return layout, [("empty_pulled", "The PULLED parts list has no items.")]
    return layout, []


# ----- the two entry points


def _reading_order(line: _Line) -> tuple[float, float]:
    return (-line.y, line.x)


def _fingerprint(pages: list[_Page]) -> str:
    digest = hashlib.sha256()
    for page in pages:
        digest.update(b"\f\n")  # page break
        for line in sorted(page.lines, key=_reading_order):
            digest.update(line.text.encode("utf-8") + b"\n")
        for picture in page.pictures:  # in the order the page draws them; no text line starts with \f
            digest.update(b"\f" + picture.digest.encode("ascii") + b"\n")
    return digest.hexdigest()


def content_fingerprint(source: Source) -> str:
    """
    A sha256 (hex) of what the summary shows: every page's text and the pictures it draws.

    It is what the Fetch gate's two-fetch agreement compares (design 3.5). check_summary cannot
    tell a partial render that is complete in itself from a finished one, so the caller files a
    summary only when two fetches at least 10 minutes apart both pass check_summary and have equal
    fingerprints. Equal means the same lines of text, in the same order, spacing normalised, and
    the same pictures, decoded, drawn in the same order, on the same pages. Bytes and metadata such
    as CreationDate do not count, so a re-download of unchanged content agrees; any change to the
    words or to a drawing, or one page more or fewer, does not. Where on its page a picture is
    drawn does not count either.

    The value depends on pypdf's text extraction. Store the pypdf version with a fingerprint
    (check_summary records it as details["pypdf"]), and after a pypdf upgrade recompute stored
    fingerprints from their files before comparing them with new ones.

    Raises ValueError when the file cannot be read as a PDF, a damaged one included; for the
    two-fetch agreement that is a fetch that does not agree.
    """
    try:
        pages, _created = _read(_load(source))
    except _Unusable as unusable:
        raise ValueError(unusable.message) from None
    return _fingerprint(pages)


def read_contents(source: Source) -> list[tuple[list[str], list[str]]]:
    """Each page as (its lines in reading order, its pictures' digests in drawn order): what
    content_fingerprint hashes, so report_diff can say what differs. Raises ValueError when the
    file cannot be read as a PDF, as content_fingerprint does."""
    try:
        pages, _created = _read(_load(source))
    except _Unusable as unusable:
        raise ValueError(unusable.message) from None
    return [([line.text for line in sorted(page.lines, key=_reading_order)],
             [picture.digest for picture in page.pictures]) for page in pages]


def check_summary(source: Source) -> SummaryCheck:
    """
    Check one IC summary render, given as a path or as the PDF's bytes, before it is filed.

    ok=True says this render is complete in itself, not that Studio had finished it. Before
    filing, the caller fetches the summary again at least 10 minutes later and files only when
    that fetch passes too, with the same details["fingerprint"] (content_fingerprint; design 3.5,
    risk K8).

    A bad or incomplete PDF is a verdict, never an exception; a path that cannot be opened raises
    OSError.
    """
    data = _load(source)
    facts: dict[str, Any] = {"sha256": hashlib.sha256(data).hexdigest(), "pypdf": _PYPDF_VERSION}
    try:
        pages, created = _read(data)
    except _Unusable as unusable:
        return SummaryCheck(ok=False, project_name=None, page_count=0, designs=[], pulled_layout=None,
                            missing=[unusable.message],
                            details={"codes": [unusable.code], **facts, **unusable.facts})
    numbering, problems = _page_numbers(pages)

    cover = pages[0]
    project_name = _project_name(cover)
    if project_name is None:
        problems.append(("no_project_name", "Page 1 does not show PROJECT NAME."))
    elif not project_name:
        problems.append(("blank_project_name", "Page 1 shows PROJECT NAME with nothing next to it."))
        project_name = None

    listed, left_over = split_design_list(_design_list(cover))
    if left_over:
        problems.append(("unread_design_list", "Could not verify the design list on page 1."))
    elif not listed:
        problems.append(("no_designs", "Page 1 lists no designs."))
    found = _read_designs(pages)
    problems += _design_problems(listed, found)

    pulled = _read_pulled(pages)
    layout, pulled_problems = _pulled_problems(pulled)
    problems += pulled_problems

    facts.update(
        creation_date=created,
        fingerprint=_fingerprint(pages),
        orientation="landscape" if cover.width > cover.height else "portrait",
        page_numbers=numbering,
        designs=[_design_facts(name, category, found.get(_key(name))) for name, category in listed],
        pulled={"pages": [number for number, _, _ in pulled],
                "item_lines": sum(item_lines for *_, item_lines in pulled)},
    )
    return SummaryCheck(
        ok=not problems,
        project_name=project_name,
        page_count=len(pages),
        designs=[name for name, _ in listed],
        pulled_layout=layout,
        missing=[message for _, message in problems],
        details={"codes": [code for code, _ in problems], **facts},
    )
