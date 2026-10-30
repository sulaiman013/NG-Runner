r"""
The P1a drafts for Jim quote his docs word for word and carry no client or contact details.

Why: Jim's chat applies docs\p1a\rules-lines.md and skill-update.md by finding each quoted
passage at the line it cites and putting the new text in its place (his RULES section 8
process), so a quote that is not word for word at its line sends an edit to the wrong place,
or nowhere. The drafts are committed, so a client's or a job's name, a made-up test name (to
Jim it reads as a real job), an email address or a phone number must never be in them.

The quote checks read Jim's spec snapshot (local only, NGR_SPEC_DIR, as tests\test_docs_p0.py
does) and skip when it is absent. Their failures show the draft's own words and file:line,
never his text.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from test_docs_p0 import ROOT, SPEC_DIR, SPEC_FILES, _files_git_would_commit, _private_details

ROOT = Path(__file__).resolve().parent.parent
P1A = ROOT / "docs" / "p1a"
DRAFTS = ("rules-lines.md", "skill-update.md")
# The files a draft may cite: those the P0 questionnaire cites, and Where Claude works.
CITABLE = (*SPEC_FILES.values(), "Where-Claude-works-paths.md")


def _draft(name: str) -> str:
    return (P1A / name).read_text(encoding="utf-8")


# ----- reading a draft: its fenced blocks, the line above each, and its citations

NOW, NEW = "**Now**", "**New**"   # a Now block quotes Jim's lines; a New block is the new text
ELIDED = "[…]"               # words left out of a quoted line (a client's name)
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_CITE = re.compile(r"`([^`]+?\.md):(\d+)(?:-(\d+))?`")   # `RULES.md:87-88`
_QUOTED = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class Block:
    """A fenced block of a draft, and the label line just above it (1-based line numbers)."""
    label: str
    label_line: int
    lines: tuple[str, ...]


def _fences(lines: Sequence[str]) -> Iterator[tuple[int, int]]:
    """(opening, closing) indexes of each fenced block; an unclosed one runs to the end."""
    i = 0
    while i < len(lines):
        found = _FENCE.match(lines[i])
        if not found:
            i += 1
            continue
        end = next((k for k in range(i + 1, len(lines)) if lines[k].startswith(found[1])),
                   len(lines))
        yield i, end
        i = end + 1


def blocks(doc: str) -> list[Block]:
    """Every fenced block of a draft, with the nearest non-blank line above it as its label."""
    lines = doc.splitlines()
    found = []
    for start, end in _fences(lines):
        above = next((k for k in range(start - 1, -1, -1) if lines[k].strip()), None)
        label = lines[above] if above is not None else ""
        found.append(Block(label, 0 if above is None else above + 1,
                           tuple(lines[start + 1:end])))
    return found


def prose(doc: str) -> Iterator[tuple[int, str]]:
    """(line number, line) for every line outside the fenced blocks."""
    lines = doc.splitlines()
    fenced = {k for start, end in _fences(lines) for k in range(start, end + 1)}
    yield from ((k + 1, line) for k, line in enumerate(lines) if k not in fenced)


def citations(line: str) -> list[tuple[str, int, int]]:
    """(file, first line, last line) of each `file.md:N` or `file.md:N-M` on a line."""
    return [(m[1], int(m[2]), int(m[3] or m[2])) for m in _CITE.finditer(line)]


# ----- the quote check itself


def _same_line(quoted: str, actual: str) -> bool:
    """A quoted line is the cited line word for word; each […] stands for words left out."""
    pattern = ".+".join(re.escape(piece) for piece in quoted.rstrip().split(ELIDED))
    return re.fullmatch(pattern, actual.rstrip()) is not None


def _one_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def quote_problems(doc: str, spec: Mapping[str, Sequence[str]],
                   name: str) -> tuple[list[str], int]:
    """Quotes in a draft that are not word for word at the lines they cite.

    A Now block must be exactly the lines its label cites. A "quoted" fragment on any other
    line that cites spec lines must be inside one of them (a fragment may run across them).
    Every citation must be inside its file. Returns the problems in draft order, each naming
    only the draft's own words and file:line, never Jim's text, and the count of quotes checked.
    """
    found: dict[tuple[int, str], None] = {}   # (draft line, problem), in order, once each
    checked = 0

    def cited(no: int, file: str, first: int, last: int) -> Sequence[str] | None:
        if file not in spec:
            found[no, f"cites {file}, which the spec snapshot lacks"] = None
            return None
        if not 1 <= first <= last <= len(spec[file]):
            found[no, f"{file}:{first}-{last} runs past the end of {file} "
                      f"({len(spec[file])} lines)"] = None
            return None
        return spec[file][first - 1:last]

    for block in blocks(doc):
        if not block.label.startswith(NOW):
            continue
        no, cites = block.label_line, citations(block.label)
        if len(cites) != 1:
            found[no, "a Now block cites exactly one file:line"] = None
            continue
        file, first, last = cites[0]
        actual = cited(no, *cites[0])
        if actual is None:
            continue
        checked += 1
        if len(block.lines) != len(actual):
            span = f"{file}:{first}" + (f"-{last}" if last > first else "")
            found[no, f"the quote has {len(block.lines)} lines, {span} has {len(actual)}"] = None
            continue
        for k, (quoted, line) in enumerate(zip(block.lines, actual), 1):
            if not _same_line(quoted, line):
                found[no, f"line {k} of the quote is not {file}:{first + k - 1}"] = None

    for no, line in prose(doc):
        texts = [cited(no, *cite) for cite in citations(line)]
        texts = [_one_space(" ".join(t)) for t in texts if t is not None]
        for fragment in _QUOTED.findall(line) if texts else []:
            checked += 1
            if not any(_one_space(fragment) in text for text in texts):
                found[no, f"not in the cited lines: {fragment!r}"] = None

    return [f"{name}:{no}: {problem}" for no, problem in sorted(found, key=lambda p: p[0])], checked


def test_the_quote_check_finds_misquotes_and_lines_past_the_end():
    spec = {
        "RULES.md": ["one", "Never guess — say so.", "  three `x`", "four"],
        "INDEX.md": ["_Rev 1 — names Somebody here. History._"],
    }
    doc = "\n".join([
        "**Now**, `RULES.md:2-3`:",                          # 1: right, the indent included
        "```text",
        "Never guess — say so.",
        "  three `x`",
        "```",
        "**New**, in their place:",                         # 6: new text is never checked
        "```text",
        "anything at all",
        "```",
        "**Now**, `INDEX.md:1`, a name left out:",          # 10: [...] stands for the name
        "```text",
        "_Rev 1 — names […] here. History._",
        "```",
        "**Now**, `RULES.md:4`:",                           # 14: wrong case
        "```text",
        "Four",
        "```",
        "**Now**, `RULES.md:3`:",                           # 18: two lines for one
        "```text",
        "  three `x`",
        "four",
        "```",
        "**Now**, `RULES.md:4-5`:",                         # 23: past the end
        "```text",
        "four",
        "```",
        "**Now**, `INDEX.md:1`:",                           # 27: [...] must leave out something
        "```text",
        "_Rev 1 — names […]Somebody here. History._",
        "```",
        '- `RULES.md:2`: "Never guess" and "say so." fit; "never said" does not.',      # 31
        '- `RULES.md:1-2` and `INDEX.md:1`: "one Never guess" runs across two lines.',  # 32
        '- `BUILD.md:1`: "anything"',                       # 33: a file the snapshot lacks
        'Quotes on a line that cites nothing, like "this", are not checked.',
    ])
    problems, checked = quote_problems(doc, spec, "d.md")
    assert problems == [
        "d.md:14: line 1 of the quote is not RULES.md:4",
        "d.md:18: the quote has 2 lines, RULES.md:3 has 1",
        "d.md:23: RULES.md:4-5 runs past the end of RULES.md (4 lines)",
        "d.md:27: line 1 of the quote is not INDEX.md:1",
        "d.md:31: not in the cited lines: 'never said'",
        "d.md:33: cites BUILD.md, which the spec snapshot lacks",
    ]
    assert checked == 9   # five Now blocks with lines to compare, four fragments


# ----- each draft shows every change as Now (Jim's lines, cited) and New (Build spec 6.8)


def _changes(doc: str) -> dict[str, str]:
    """Each numbered change of a draft ("## 1. ..."), up to the next "## " heading."""
    sections = re.split(r"(?m)^(?=## )", doc)
    return {s.splitlines()[0]: s for s in sections if re.match(r"## \d+\. ", s)}


def _new_text(name: str) -> str:
    """Everything a draft's New blocks would put into Jim's files."""
    return "\n".join(line for block in blocks(_draft(name)) if block.label.startswith(NEW)
                     for line in block.lines)


@pytest.mark.parametrize("name", DRAFTS)
def test_every_change_quotes_the_lines_it_changes_and_gives_the_new_text(name):
    # A block under any other label would be a quote nobody checks.
    doc = _draft(name)
    changes = _changes(doc)
    assert changes, f"{name} has no numbered change"
    for heading, text in changes.items():
        labels = [block.label for block in blocks(text)]
        assert any(label.startswith(NOW) and len(citations(label)) == 1
                   for label in labels), heading
        assert any(label.startswith(NEW) for label in labels), heading
    unlabelled = [b.label_line for b in blocks(doc) if not b.label.startswith((NOW, NEW))]
    assert not unlabelled, f"{name}: the blocks under lines {unlabelled} are neither Now nor New"


def test_the_new_text_says_where_the_summary_is_what_chat_reads_and_the_way_back():
    # Build spec 6.8: NG Runner files the summary into NG Jobs\<Job>\; chat reads
    # <Job>_NGRunner.json before working a job; with NG Runner down, chat works as before.
    # The skill takes the summary from the NG Jobs folder instead of an upload.
    rules, skill = _new_text("rules-lines.md"), _new_text("skill-update.md")
    for needed in (r"`NG Jobs\<Job>\<Job>_summary.pdf`", r"`NG Jobs\<Job>\<Job>_NGRunner.json`",
                   "as before"):
        assert needed in rules, needed
    for needed in (r"`NG Jobs\<Job>[_Gar]\<Job>[_Gar]_summary.pdf`", "as before"):
        assert needed in skill, needed


def test_the_rules_lines_go_through_jims_regression_check_and_move_the_index_rev():
    # Design section 7, P1a row: Jim approves the RULES lines "with regression" (his RULES
    # section 8), and his INDEX asks for its Rev line to move whenever RULES.md changes.
    doc = _draft("rules-lines.md")
    assert r"python scripts\regression.py" in doc
    assert any(block.label.startswith(NOW) and citations(block.label) == [("INDEX.md", 3, 3)]
               for block in blocks(doc))


# ----- every quote is word for word at the line it cites (Jim's snapshot, local only)


@pytest.fixture(scope="module")
def jims_lines() -> dict[str, list[str]]:
    """Jim's docs line by line, for the files a draft may cite. They name clients: local only."""
    if not SPEC_DIR.is_dir():
        pytest.skip("Jim's spec snapshot is not here (set NGR_SPEC_DIR)")
    return {f: (SPEC_DIR / f).read_text(encoding="utf-8-sig").splitlines()
            for f in CITABLE if (SPEC_DIR / f).is_file()}


@pytest.mark.fixtures
@pytest.mark.parametrize("name", DRAFTS)
def test_every_quote_is_word_for_word_at_the_line_it_cites(name, jims_lines):
    problems, checked = quote_problems(_draft(name), jims_lines, name)
    assert checked > 0
    assert not problems, "\n".join(problems)


# ----- no client, job, made-up or contact details (AGENTS.md rule 5)

# A job's name: a capitalised word, maybe more words, then its MMDDYY date (Name_MMDDYY).
_JOB = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z]*)(?:_[A-Za-z]+)*_\d{6}(?![0-9])")


def names_pattern(names: Iterable[str]) -> re.Pattern[str]:
    """Any of the names as a word of its own, also where "_" or "'s" follows it (unlike \\b)."""
    alternatives = "|".join(map(re.escape, sorted(names)))
    return re.compile(rf"(?<![A-Za-z])(?:{alternatives})(?![a-z])")


_MADE_UP = names_pattern(["Testerson", "Fakeley", "Mockford", "Dummitt"])


@pytest.mark.parametrize(("line", "named"), [
    ("Testerson_101426 and Fakeley_090926_Gar", True),
    ("Testerson's job", True),
    ("files for Fakeley.", True),
    ("a Testersonian word", False),
    ("testerson in lower case", False),
])
def test_the_name_check_finds_a_name_however_it_is_followed(line, named):
    assert bool(_MADE_UP.search(line)) is named


@pytest.mark.parametrize("name", DRAFTS)
def test_the_drafts_hold_no_job_name_made_up_name_or_contact_details(name):
    doc = _draft(name)
    assert not _MADE_UP.search(doc), f"{name}: a made-up test name reads to Jim as a real job"
    jobs = [no for no, line in enumerate(doc.splitlines(), 1) if _JOB.search(line)]
    assert not jobs, f"{name}: lines {jobs} name a job; write <Job>"
    private = _private_details(doc)   # "line N: kind", never the value
    assert not private, f"{name}: {private}"


@pytest.fixture(scope="module")
def jims_job_words() -> re.Pattern[str]:
    """The first word of every job name in Jim's docs (a surname, mostly), as one pattern."""
    if not SPEC_DIR.is_dir():
        pytest.skip("Jim's spec snapshot is not here (set NGR_SPEC_DIR)")
    words = {m[1] for path in SPEC_DIR.iterdir() if path.suffix.lower() in (".md", ".txt")
             for m in _JOB.finditer(path.read_text(encoding="utf-8-sig"))}
    assert words, "no job names found in Jim's docs: the pattern no longer fits how he names jobs"
    return names_pattern(words)


@pytest.mark.fixtures
@pytest.mark.parametrize("name", DRAFTS)
def test_the_drafts_name_no_client_from_jims_docs(name, jims_job_words):
    # A quote copied whole from his docs is how a name would get in: INDEX's Rev line names one.
    lines = [no for no, line in enumerate(_draft(name).splitlines(), 1)
             if jims_job_words.search(line)]
    assert not lines, f"{name}: lines {lines} name a client from Jim's docs; leave the name out"


DESIGN_DIR = Path(os.environ.get(
    "NGR_DESIGN_DIR", r"C:\Sulaiman Automation - Assembly Reports\NG Runner design"))
# First words of dated names in those docs that are also ordinary words or made-up names here.
_NOT_CLIENTS = frozenset({"Name", "Job", "Test", "Testerson", "Fakeley", "Mockford", "Dummitt"})


@pytest.mark.fixtures
def test_no_file_git_would_commit_names_a_client_from_jims_docs_or_the_design():
    # Their examples name real jobs, and copying one is how a real name gets into this public
    # repo: a builder once did, in a comment mapping real names to made-up ones. Reports file
    # and line, never the name.
    docs = [path for folder in (SPEC_DIR, DESIGN_DIR) if folder.is_dir()
            for path in folder.rglob("*") if path.suffix.lower() in (".md", ".txt")]
    if not docs:
        pytest.skip("neither Jim's spec snapshot nor the design is here")
    words = {m[1] for path in docs
             for m in _JOB.finditer(path.read_text(encoding="utf-8-sig", errors="replace"))}
    assert words, "no job names found: the pattern no longer fits how he names jobs"
    pattern = names_pattern(words - _NOT_CLIENTS)
    found = []
    for name in _files_git_would_commit():
        data = (ROOT / name).read_bytes()
        if b"\0" not in data:
            found += [f"{name}:{no}" for no, line in enumerate(
                data.decode("utf-8", "replace").splitlines(), 1) if pattern.search(line)]
    assert not found, f"these lines name a real client; use a made-up name: {found}"
