"""
The lint on every message a person sees (design section 4, "Format rules").

Jim reads his messages on a phone at 6:30 AM and refers to items by number, so each one is held
to his format before it leaves: numbered, one line per item, each item naming its job; times like
9:42 PM and dates like 10/30 in Pacific time; inches with fractions; no paths, hashes, IDs,
attribute names, addresses or phone numbers; no intro or sign-off; and a clean check reads
exactly "Nothing to fix — matches IC summary." Templates are linted in the tests; at runtime a
message that fails is swapped for its kind's fixed template (templates.vetted) and the problems
open a DEV item, so nothing waits on Sulaiman.

No message ever says it is OK to cut. The strongest thing NG Runner says is "Nest checked", and what
to do about it is left to the people cutting. That is a rule here and not just a test of today's
words, since a nest guard's reasons can come from a tool's own output.

What Barbara typed comes back quoted, as she typed it (Fab row "Testerson 10/14/26"), so that she
can find her row. Those words are hers, so the rules on how NG Runner writes don't judge them; the
rules that keep addresses, phone numbers, paths, hashes and keys out of every message still do.
The same goes for the numbered items under a title that quotes a Fab row: they are the jobs that
row could be, as Salesforce spells them, so a name with no date in it still names its job.

A problem names the rule and the place ("line 2: has a path"), never the offending text: the
text may be an address or a key, and problems go to the DEV lane and the logs. redact() makes
text from outside NG Runner (a send error, say) safe for those places too.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ngrunner.messages import Message

CLEAN = "Nothing to fix — matches IC summary."   # a clean check, exactly (RULES section 0)
FOR_YOU = "For you: "       # the digest's owner line, before its items
YOUR_LIST = "Your list: "   # the digest's last line: what is open for Jim
ABOUT_ITSELF = "NG Runner"  # an item that starts so is about the app, not a job

# A job name: a name, its MMDDYY date, then any variant (Testerson_101426_Gar, ..._101426-2,
# ..._101426b2). Variant words are short, so a hash stuck on after an underscore is not taken for
# one. A few real ones have a space or nothing for the underscore (Testerson 101426,
# Testerson101426): there the name must be a capitalised word and the date a real one, and a word
# that stands before a number in a sentence does not count, so "The 101426 job" stays a bare
# number and a key that happens to end like a date stays a key.
JOB_NAME = re.compile(
    r"(?<![\w'’-])(?:"
    r"[^\W\d_][\w'’-]*?_[0-9]{6}(?![0-9])(?:[A-Za-z]{1,10}[0-9]{0,2})?"
    r"|(?!(?:The|Studio|Project|Job|Opportunity) )(?=[\w'’-]*[a-z])"
    r"[^\W\d_a-z](?:[^\W\d_]|['’-])*? ?"
    r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])[0-9]{2}(?![0-9])"
    r")(?:[_-][A-Za-z0-9]{1,10})*(?![A-Za-z0-9])"
)
# A card ID: up to three capital letters from the job's name and a number (TES-2).
CARD_ID = re.compile(r"(?<![\w-])[A-Z]{1,3}-[1-9][0-9]*(?![\w-])")
EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(
    r"(?<![\w/:-])(?:\+?1[\s.-]?)?(?:\([0-9]{3}\)\s?|[0-9]{3}[\s.-])[0-9]{3}[\s.-][0-9]{4}"
    r"(?![\w/-])"                                       # (310) 555-0123, 310-555-0123
    r"|(?<![\w/:.-])[0-9]{3}-[0-9]{4}(?![\w/-])"        # 555-0123
)


@dataclass(frozen=True)
class Problem:
    """One way a message breaks the format. code is stable: tests and the DEV lane group by
    it (structure, numbering, job, path, time, date, hash, key, studio_id, attribute, email,
    phone, size, intro, signoff, clean, verify, leak, ok_to_cut)."""

    code: str
    where: str
    text: str

    def __str__(self) -> str:
        return f"{self.where}: {self.text}"


# ----- the whole message


def lint(message: Message) -> list[str]:
    """Every problem with message, in plain words; an empty list means it may be sent."""
    return [str(p) for p in check(message)]


def check(message: Message) -> list[Problem]:
    """lint(), with each problem's code."""
    problems = check_line(message.title, "title")
    if not message.lines:
        problems.append(Problem("structure", "message", "has no lines under its title"))
    problems += _layout(message.lines)
    problems += _naming(message)
    about_a_row = _QUOTED_ROW.search(message.title) is not None
    for number, line in enumerate(message.lines, start=1):
        problems += check_line(line, f"line {number}",
                               candidate=about_a_row and _ITEM.match(line) is not None)
    return problems


# ----- layout: numbered, one line per item

# How a body line reads. In order: an optional For you line, at most one plain statement, the
# items 1., 2., 3., an optional Your list line, and the choices last. The clean line stands
# alone.
_ITEM = re.compile(r"(?P<n>[1-9][0-9]*)\. (?=\S)")
_OTHER_NUMBERING = re.compile(
    r"""
    \(?[0-9]+[)\]]             # 1)  (1)  1]
    | [0-9]+\.(?=\S)           # 1.text
    | 0[0-9]*\.\s              # 0. or 01.
    | [0-9]+\s?-\s             # 1 - text
    | [0-9]+:(?![0-9]{2})      # 1: text (9:42 is a time)
    | \#\s?[0-9]+              # #1
    | [-*•‣▪●◦–—]\s            # a bullet
    | [a-z][.)]\s              # a. or a)
    """,
    re.VERBOSE,
)
_CHOICES = re.compile(
    r"(?:.*\S\s+)?\[[^\[\]]+\](?:\s+\[[^\[\]]+\])*(?:\s+\([^()]*\))?"
)  # [2nd]  [3rd]  (drawing crop below), alone or after a sentence
_START, _AFTER_FOR_YOU, _AFTER_STATEMENT, _IN_ITEMS, _AFTER_YOUR_LIST, _AFTER_CHOICES = range(6)


def _kind(line: str) -> str:
    if line == CLEAN:
        return "clean"
    if line.startswith(FOR_YOU):
        return "for_you"
    if line.startswith(YOUR_LIST):
        return "your_list"
    if _ITEM.match(line):
        return "item"
    if _OTHER_NUMBERING.match(line):
        return "other_numbering"
    if _CHOICES.fullmatch(line):
        return "choices"
    return "statement"


def _layout(lines: Sequence[str]) -> list[Problem]:
    problems: list[Problem] = []
    phase, expected = _START, 1
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue  # check_line names it
        kind = _kind(line)
        problem: tuple[str, str] | None = None
        if phase == _AFTER_CHOICES:
            problem = ("structure", "comes after the choices; the choices are the last line")
        elif kind == "clean":
            if len(lines) > 1:
                problem = ("clean", "the clean line stands alone: nothing else goes with it")
        elif kind == "for_you":
            if phase != _START:
                problem = ("structure", "the For you line comes first")
            phase = max(phase, _AFTER_FOR_YOU)
        elif kind == "statement":
            problem, phase = _statement_problem(phase)
        elif kind in ("item", "other_numbering"):
            problem = _item_problem(line, kind, phase, expected)
            phase, expected = max(phase, _IN_ITEMS), expected + 1
        elif kind == "your_list":
            if phase >= _AFTER_YOUR_LIST:
                problem = ("structure", "is a second Your list line")
            phase = _AFTER_YOUR_LIST
        else:  # the choices
            phase = _AFTER_CHOICES
        if problem:
            problems.append(Problem(problem[0], f"line {number}", problem[1]))
    return problems


def _statement_problem(phase: int) -> tuple[tuple[str, str] | None, int]:
    if phase >= _IN_ITEMS:
        return ("structure", "comes after the numbered items; only the Your list line or the"
                " choices may follow them"), phase
    if phase == _AFTER_STATEMENT:
        return ("numbering", "is a second unnumbered line; number the items 1., 2., 3."), phase
    return None, _AFTER_STATEMENT


def _item_problem(line: str, kind: str, phase: int, expected: int) -> tuple[str, str] | None:
    if phase > _IN_ITEMS:
        return ("structure", "is an item after the Your list line; items come before it")
    if kind == "other_numbering":
        return ("numbering", "is a bullet or is numbered another way; number items 1., 2., 3.")
    found = _ITEM.match(line)
    if found and int(found["n"]) != expected:
        return ("numbering", f"is numbered {found['n']}; the items run 1., 2., 3. in order,"
                f" so this one is {expected}.")
    return None


# ----- every item names its job


def _names_a_subject(text: str) -> bool:
    return JOB_NAME.search(text) is not None or CARD_ID.search(text) is not None


def _naming(message: Message) -> list[Problem]:
    """A title that names a job or a card covers every item under it, and so does one that
    quotes a Fab row, whose items are the jobs it could be; otherwise each item names its own job
    or card, or starts with "NG Runner" (a line about the app itself)."""
    if _names_a_subject(message.title) or _QUOTED_ROW.search(message.title):
        return []
    problems = []
    for number, line in enumerate(message.lines, start=1):
        item = _ITEM.match(line)
        if not item:
            continue
        text = line[item.end():]
        if not (_names_a_subject(text) or text.startswith(ABOUT_ITSELF)):
            problems.append(Problem("job", f"line {number}", "doesn't name its job or its card"))
    return problems


# ----- one line of text


def check_line(text: str, where: str = "line", *, candidate: bool = False) -> list[Problem]:
    """The rules for one line a person reads (a title, a line, a board string): its shape,
    then what it holds. A candidate is a job as Salesforce spells it, so like Barbara's quoted
    words it answers only to the rules kept for the whole text."""
    shape = _shape(text)
    if shape:
        return [Problem("structure", where, shape)]
    whole = JOB_NAME.sub("Job", text)  # a job name's date and underscores are not an ID
    ours = "Row" if candidate else _QUOTED_ROW.sub(r'\1 "Row"', whole)
    return [Problem(code, where, phrase) for code, test, phrase in _CONTENT_RULES
            if test(whole if code in _ON_THE_WHOLE_TEXT else ours)]


# Barbara's words as the templates quote them (they turn any quote of hers into a curly one, so
# the first quote here is the end of her words), and the rules her words are still held to.
_QUOTED_ROW = re.compile(r'\b(Fab row|now reads) "([^"]*)"')
_ON_THE_WHOLE_TEXT = frozenset({"path", "hash", "key", "email", "phone", "ok_to_cut"})

_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}")


def _shape(text: str) -> str | None:
    if not text.strip():
        return "is empty"
    if any(ch in _LINE_BREAKS for ch in text):
        return "has a line break inside it; one item is one line"
    if any(unicodedata.category(ch) == "Cc" for ch in text):
        return "has a tab or another control character"
    if text != text.strip():
        return "has spaces at its start or end"
    return None


def _search(regex: re.Pattern[str]) -> Callable[[str], bool]:
    return lambda text: regex.search(text) is not None


# ----- paths and web addresses

_WEB = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|\bwww\.[A-Za-z0-9-]")
_DRIVE = re.compile(r"[A-Za-z]:/")
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,4}$")
_LETTER = re.compile(r"[A-Za-z]")
_EDGE_PUNCTUATION = ".,;:!?()[]{}<>\"'“”‘’"


def _has_path(text: str) -> bool:
    """Any backslash or web address; a drive, a rooted or a home path; a/b/c with letters in
    it; folder/file.ext. Fractions (3/4), dates (11/3) and and/or are not paths."""
    if "\\" in text or _WEB.search(text):
        return True
    for token in text.split():
        token = token.strip(_EDGE_PUNCTUATION)
        if "/" not in token:
            continue
        parts = token.split("/")
        if _DRIVE.match(token) or (token.startswith(("/", "~/")) and _LETTER.search(token)):
            return True
        if len(parts) >= 3 and any(_LETTER.search(p) for p in parts):
            return True
        if parts[0] and _EXTENSION.search(parts[-1]):
            return True
    return False


# ----- times: 9:42 PM, Pacific

_ZONE = re.compile(r"\b(?:UTC|GMT)\b|\d(?::\d{2}){1,2}(?:\.\d+)?Z\b")
_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}")
_CLOCK = re.compile(r"(?<![\d:])(?P<h>\d{1,2}):(?P<m>\d{2})(?P<s>:\d{2})?(?!\d)")
_MERIDIEM = re.compile(r" (?:AM|PM)(?![A-Za-z])")


def _bad_time(text: str) -> bool:
    """A time that isn't Jim's 9:42 PM: UTC or Z, ISO, 24-hour, no AM/PM, a leading zero,
    seconds."""
    if _ZONE.search(text) or _ISO_TIME.search(text):
        return True
    for clock in _CLOCK.finditer(text):
        hour, minute = clock["h"], int(clock["m"])
        if (clock["s"] or hour.startswith("0") or not 1 <= int(hour) <= 12 or minute > 59
                or not _MERIDIEM.match(text, clock.end())):
            return True
    return False


# ----- dates: 10/30 or Fri 10/30

_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
          r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b\.?")
_BAD_DATE = re.compile(
    rf"""
    \b\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}\b                  # 2026-11-03
    | (?<![\d/])\d{{1,2}}/\d{{1,2}}/\d{{2,4}}(?![\d/])      # 11/3/26
    | (?<![\d-])\d{{1,2}}-\d{{1,2}}-\d{{2,4}}(?![\d-])      # 11-3-26
    | \b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b             # Nov 3, November 3rd
    | \b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}     # 3 Nov, 3rd of November
    | \b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b\.?,?\s+\d{{1,2}}/\d{{1,2}}  # Tuesday 11/3
    | \b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[.,]\s*\d{{1,2}}/\d{{1,2}}   # Tue, 11/3
    | (?<![\d/])0\d/\d{{1,2}}(?![\d/])                      # 09/3
    | (?<![\d/])\d{{1,2}}/0\d(?![\d/])                      # 11/03
    """,
    re.VERBOSE,
)


# ----- hashes, keys and internal numbers

_HEX_RUN = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{12,}(?![0-9A-Fa-f])")
_HASH_WORD = re.compile(r"\b(?:md5|sha-?1|sha-?256|checksums?|hash(?:es)?)\b", re.IGNORECASE)
# A key or token: 24+ letters, digits, _ or -, with both cases and a digit (job names are
# masked first).
_KEY = re.compile(
    r"(?<![\w-])(?=[\w-]*[0-9])(?=[\w-]*[a-z])(?=[\w-]*[A-Z])[\w-]{24,}(?![\w-])"
)
_STUDIO_ID = re.compile(
    r"\bstudio\s+(?:project\s+)?id\b[^0-9]{0,12}[0-9]"          # Studio ID is 84213
    r"|(?<![\w/$])(?<![0-9][.,])[0-9]{5,}(?![\w/]|[.,][0-9])",  # a bare run of 5+ digits
    re.IGNORECASE,
)


def _has_hash(text: str) -> bool:
    return _HEX_RUN.search(text) is not None or _HASH_WORD.search(text) is not None


# ----- attribute, table and setting names

# Mozaik's attribute and element names seen in Jim's RULES, BUILD and SCHEMA, and its true and
# false. Plain words Jim uses (Height, Depth, Product) are not here.
_MOZAIK_NAMES = frozenset({
    "AnnotationLocator", "AnnotationSet", "Ang", "BTop", "BaseMatTempSel", "BotNail",
    "CabNo", "CabProdParm", "CabProdPart", "CabProdParts", "CategoryId", "EBand", "EBandLock",
    "Elev", "FEnd", "Fals", "FlipSideOp", "HangNotchSkin", "HasLeftEndCap", "HasRightEndCap",
    "IDTag", "IdTagCount", "IsFromAdjCab", "IsInterior", "IsUserOp", "JobDat", "JobParms",
    "JointFast1", "JointFast2", "LineBore", "MatORId", "MatORSel", "MiterBack", "NailW",
    "NoShelfHolesA", "NoShelfHolesC", "OperationGroove", "OperationHole", "OperationLineBore",
    "OptStatus", "OptimizeGCodeRun", "OptimizePart", "OptimizePartLocation", "OptimizeSheet",
    "PartType", "PosX", "PosY", "ProdName", "RootTemplateId", "RunId", "SUDirty", "ShapePoints",
    "ShapeType", "SysHoleSpacing", "SysHoles", "SysHolesT", "SysHolesW", "TNailW",
    "TextureORId", "Tru", "UBack", "UEnd", "UniqueID", "WNailW", "WTop", "WallJoint",
})
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CODE_NAME = re.compile(
    r"(?<![\w'’-])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w'’-])"   # fab_rows, studio_id
    r"|</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>"                            # <OptimizeSheet>
    r"|\b[A-Za-z]\w*=\S"                                               # state=open, X="2"
)


def _names_attribute(text: str) -> bool:
    return (any(word in _MOZAIK_NAMES for word in _WORD.findall(text))
            or _CODE_NAME.search(text) is not None)


# ----- sizes: inches with fractions, mm only in parentheses right after inches

_INCH = r"(?:\"|″|”|\s?in\b\.?|\s?inch(?:es)?\b)"
_MM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s?mm\b", re.IGNORECASE)
_MM_AFTER_INCHES = re.compile(
    rf"(?:\d+\s)?\d+(?:/\d+)?{_INCH}\s\(\d+(?:\.\d+)?\s?mm\)", re.IGNORECASE
)  # 9/64" (3.6 mm), 36 9/16" (928mm)
_OTHER_UNITS = re.compile(
    r"\d\.\d+(?:\"|″|”|\s?inch(?:es)?\b)"                       # decimal inches: 0.25"
    r"|\d\s?(?:cm|centimet(?:er|re)s?|met(?:er|re)s?)\b",       # cm, metres
    re.IGNORECASE,
)


def _bad_size(text: str) -> bool:
    allowed = [m.span() for m in _MM_AFTER_INCHES.finditer(text)]
    for mm in _MM.finditer(text):
        if not any(start <= mm.start() and mm.end() <= end for start, end in allowed):
            return True
    return _OTHER_UNITS.search(text) is not None


# ----- no intro or sign-off

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_INTRO = re.compile(
    r"(?:hi|hello|hey|dear|greetings|good\s+(?:morning|afternoon|evening|day))\b(?![-'’])"
    r"|(?:here\s+(?:is|are)|here's|here’s|below\s+(?:is|are)|fyi|as\s+requested|please\s+find"
    r"|just\s+a\s+(?:quick\s+)?(?:note|update|heads[- ]up)|quick\s+update"
    r"|this\s+is\s+(?:your|the|a)\s+(?:daily\s+)?(?:digest|list|update|summary|report))\b",
    re.IGNORECASE,
)
_SIGN_OFF = re.compile(
    r"(?:thanks|thank\s+you|thx|regards|kind\s+regards|best\s+regards|warm\s+regards"
    r"|best\s+wishes|sincerely|cheers|yours(?:\s+truly)?|talk\s+soon|take\s+care)\b(?![-'’])"
    r"|best\s*(?:[,.!]|$)",
    re.IGNORECASE,
)
_SIGN_OFF_ANYWHERE = re.compile(
    r"\b(?:let\s+me\s+know|hope\s+(?:this|that)\s+helps|feel\s+free"
    r"|have\s+a\s+(?:great|good|nice)\s+(?:day|night|evening|weekend))\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    item = _ITEM.match(text)
    return _SENTENCE_BREAK.split(text[item.end():] if item else text)


def _has_intro(text: str) -> bool:
    return any(_INTRO.match(sentence) for sentence in _sentences(text))


def _has_sign_off(text: str) -> bool:
    return (any(_SIGN_OFF.match(sentence) for sentence in _sentences(text))
            or _SIGN_OFF_ANYWHERE.search(text) is not None)


# ----- clean means exactly CLEAN; never "unknown", a promise nobody checked, or program text

_CLEAN_LIKE = re.compile(
    r"nothing\s+(?:else\s+)?to\s+fix|\ball\s+(?:clear|good)\b|\blooks\s+good\b"
    r"|\bno\s+(?:issues|problems)(?:\s+found)?\b"
    r"|\b(?:crosscheck|result|verdict)\s*:?\s+clean\b|^clean[.!]?$",
    re.IGNORECASE,
)
_UNVERIFIED = re.compile(
    r"\bunknown\b|(?<![\w/])n/a(?![\w/])|\bTBD\b|\?\?"
    r"|\bnothing\s+(?:is|was|will\s+be|has\s+been|got)\s+lost\b",
    re.IGNORECASE,
)
_LEAK = re.compile(
    r"\bNone\b(?!\s+[a-z])|\b(?:null|undefined|NaN|nan)\b"     # a missing value
    r"|\b[A-Z][A-Za-z]*(?:Error|Exception)\b|\bTraceback\b"    # an error
    r"|\b[A-Za-z_]\w*\.[A-Za-z_]\w*\("                         # a repr: datetime.datetime(
    r"|[{}]|->|=>"                                             # JSON, a placeholder, an arrow
)


def _says_clean_otherwise(text: str) -> bool:
    return text != CLEAN and _CLEAN_LIKE.search(text) is not None


# ----- never OK to cut, in any words: only people decide that

_OK_TO_CUT = re.compile(
    r"\b(?:o\.k\.?|ok(?:ay)?|safe|clear(?:ed)?|good|fine|ready|free|allowed|proceed|go\s+ahead|go"
    r"|can|may)(?!\w)(?:[\s-]+(?:to|for|and|be|you|it|this))*[\s-]+cut(?:ting)?\b"
    r"|\bcut(?:ting)?\b(?:\W+\w+){0,2}?\W+(?:o\.k\.|ok(?:ay)?|safe|allowed|fine|away)(?!\w)",
    re.IGNORECASE,
)


_CONTENT_RULES: tuple[tuple[str, Callable[[str], bool], str], ...] = (
    ("path", _has_path, "has a path or a web address"),
    ("time", _bad_time, "has a time that is not written like 9:42 PM, Pacific time"),
    ("date", _search(_BAD_DATE), "has a date that is not written like 10/30 or Fri 10/30"),
    ("hash", _has_hash, "has a hash"),
    ("key", _search(_KEY), "has what looks like a key or a code"),
    ("studio_id", _search(_STUDIO_ID), "has a Studio ID or another internal number"),
    ("attribute", _names_attribute, "names an attribute, a table or a setting"),
    ("email", _search(EMAIL), "has an email address"),
    ("phone", _search(PHONE), "has a phone number"),
    ("size", _bad_size, "has a size that is not in inches with fractions (mm goes in"
     " parentheses right after the inches)"),
    ("intro", _has_intro, "has a greeting or an intro; start with the first item"),
    ("signoff", _has_sign_off, "has a sign-off; end with the last item"),
    ("clean", _says_clean_otherwise,
     f"says it is clean another way; a clean result reads exactly: {CLEAN}"),
    ("verify", _search(_UNVERIFIED),
     "says unknown, N/A or nothing is lost; say what could not be verified"),
    ("leak", _search(_LEAK), "has program text (None, null, an error name, code or JSON)"),
    ("ok_to_cut", _search(_OK_TO_CUT), "says it may be cut; the most NG Runner says is Nest checked"),
)


# ----- a subject: short enough for MailApp whatever a row holds, and for a phone to show

SUBJECT_LIMIT = 100


def subject_line(title: str) -> str:
    """title as a subject: at most SUBJECT_LIMIT characters, cut at a word. Where it quotes a Fab
    row, only her words are cut: the lint reads a quote only once it is closed, and what follows
    the quote says what is being asked. Cutting one that fits changes nothing."""
    if len(title) <= SUBJECT_LIMIT:
        return title
    row = _QUOTED_ROW.search(title)
    room = 0 if row is None else SUBJECT_LIMIT - (len(title) - len(row[2]))
    if room < 1:
        return _cut(title, SUBJECT_LIMIT)
    return f"{title[:row.start(2)]}{_cut(row[2], room)}{title[row.end(2):]}"


def _cut(text: str, room: int) -> str:
    """text in room characters, an ellipsis last: its whole words, or with none to keep, as much
    of its first word as fits."""
    if len(text) <= room:
        return text
    space = text.rfind(" ", 0, room)
    return f"{text[:space if space > 0 else room - 1].rstrip()}…"


# ----- outside text, made safe to log

_WEB_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+|\bwww\.\S+")
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\\S*|\\\\\S+")
REDACT_LIMIT = 300


def redact(text: str, *, limit: int = REDACT_LIMIT) -> str:
    """Text from outside NG Runner (a send error, another program's reason) made safe for the
    DEV lane and the logs: on one line, with web addresses, paths, email addresses, phone
    numbers, keys and hashes replaced by a word in brackets, and at most limit characters. A job
    name stays, however long it runs: one of its file names can look like a key."""
    text = " ".join(str(text).split())
    for pattern, word in ((_WEB_TOKEN, "[web address]"), (_WINDOWS_PATH, "[path]"),
                          (EMAIL, "[address]"), (PHONE, "[phone]"), (_HEX_RUN, "[code]")):
        text = pattern.sub(word, text)
    text = _KEY.sub(lambda key: key[0] if JOB_NAME.fullmatch(key[0]) else "[code]", text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
