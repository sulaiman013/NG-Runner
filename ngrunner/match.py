"""
Job matching: which job a file, a folder or a Fab row belongs to (design section 5, "Matching").

Names on disk drift: spaces for underscores, "_Gar" added by the pipeline, Studio's own
"<client>_<project>_summary.pdf", dated and browser copies, remakes and revisions. Filing a
summary to the wrong job is the one mistake intake must never make, so matching is exact after
one fixed normalisation, never fuzzy, and never on a surname or a date alone. Anything short of
that comes back "ambiguous" (a person picks from the candidates) or "none", with a reason.
Identity is the Studio project ID; names are only how it is found.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import PureWindowsPath
from typing import Literal, Protocol

Status = Literal["matched", "ambiguous", "none", "stale_list"]

# Salesforce stages that count as "sold or install-stage" for the surname rule.
SOLD_STAGES = frozenset({"Ready to Order", "Ready to Install"})


# ----- public types


@dataclass(frozen=True)
class NormalizedName:
    """A file or folder name reduced to the job name it carries.

    base: the slug that must equal a job's normalised name, e.g. "testerson_101426". Text after
        the date that is not a variant stays in it ("testerson_101426_2"): Salesforce has
        Opportunities named so, and such a name is a job name too.
    variant: what followed the date, canonical and "+"-joined ("gar", "remake", "rev2", "-2",
        "redo2", "pickup", "top", "gb_5_13_26", "claude", "gar+rev2"), or None. For a Fab row
        whose date carries none, the variant words typed anywhere after the first word,
        including run words only rows use: "re-make", "re-made", "remakes" as "remake"; "re-do"
        as "redo"; "revision" and "revised" as "rev"; "go back" and "G.B." as "gb";
        "warranty"; "add on" as "addon"; "second", "II" or "two" as "-2", and so on to "ten"
        or "X" as "-10". "gar" is the pipeline's garage flag, a branch of the same job; the
        others mark a run of the parent job. A job whose own name carries a run can still be a
        job of its own (a separate "-2" sale), so a file is matched to it only when it is that
        run or a run of it.
    kind: how the name was made: "studio" (Studio's <client>_<project>_summary.pdf, client
        slug dropped), "pipeline" (NG Jobs <Job>_summary.pdf), "dated" (Assembly Reports'
        "<Name> (YYYY-MM-DD #n)" copy), "plain" (<Name> as a program writes it, or a clean
        folder name), "hand" (anything else) or "text" (a job name or typed text).
    alternates: the other readings of a lowercase <client>_<project>_summary.pdf, one per way
        to split off the client slug (no slug, or two, three... words), the variant shared.
        base is the one-word reading. The name matches only when every reading that names a
        job names the same one, and no reading names a sale that is not a listed job; otherwise
        "client_slug_unclear" or "unlisted_sale".
    """

    base: str
    variant: str | None
    kind: str
    alternates: tuple[str, ...] = ()

    @property
    def is_job_name(self) -> bool:
        """True when the base has the Name_MMDDYY shape: a name, then a 6-digit date."""
        return _JOB_NAME.fullmatch(self.base) is not None


class JobLike(Protocol):
    """What matching needs from a job: its frozen name and its later names."""

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> Sequence[str]: ...


@dataclass(frozen=True)
class MatchResult[T]:
    """The verdict, with what a person needs when it is not "matched".

    status: "matched" (job is set and is the only candidate), "ambiguous" (a person picks from
        candidates), "none", or "stale_list" (Fab rows: get a fresh list, then match again).
    reason, for files and folders: matched "exact" (name and variant equal), "base" (name
        equal, and the job's own run, if any, leads the file's: "-2" for "-2 remake"), "alias"
        (a later name of the job) or "folder" (the job folder decided); ambiguous
        "several_jobs" (the job whose run goes furthest first), "client_slug_unclear" (the
        readings of a lowercase Studio name name different listed jobs; the job folder of one
        of them settles it), "unlisted_sale" (a reading of a lowercase Studio name is a sale in
        opportunity_names that no listed job is; no folder settles it), "not_a_job_name" (equal,
        but no Name_MMDDYY to trust) or "folder_disagrees" (the file names another job than its
        folder's, may be an unlisted sale's, starts with another listed job's name more fully,
        or carries a Name_MMDDYY that is not the folder job's own, run included, listed or not;
        the folder's job comes first); none "no_job" (a Name_MMDDYY no listed job has, or a
        sale's or a run of a sale's that no listed job is) or "not_a_job_name".
    reason, for Fab rows: matched "exact_name" or "surname_designer"; ambiguous
        "several_opportunities", "unconfirmed" (offered: a word or a number in the row that rule
        (b) cannot explain, or no candidate that fits every rule) or "variant_of_parent" (the
        row names a run, "remake" or "-2", that the one candidate's name lacks: ask whether it
        is a run of that job; the run is in normalized.variant); none "no_opportunity" or
        "not_a_name"; and "stale_list".
    normalized: how the file name or the typed text was read.
    """

    status: Status
    job: T | None = None
    candidates: tuple[T, ...] = ()
    reason: str = ""
    normalized: NormalizedName | None = None


@dataclass(frozen=True)
class FabRow:
    """A Fab Schedule row as NG Runner first saw it. first_seen is timezone-aware and stays
    the same when Barbara later edits the row."""

    text: str
    designer: str
    first_seen: datetime


@dataclass(frozen=True)
class Opportunity:
    """One Salesforce Opportunity from the job list."""

    name: str
    studio_id: str
    owner: str
    stage: str


# ----- normalisation

# The decorations around a job name, in the order programs add them. The name is lazy, so
# every decoration present is stripped.
_DECORATED = re.compile(
    r"""
    (?P<name>.+?)
    (?P<summary>[\s_-]+summary)?                                  # "_summary" or " summary"
    (?P<dated>\s*\(\d{4}-\d{2}-\d{2}(?:\s*\#\d+)?\))?             # Assembly Reports' dated copy
    (?P<copy>\s*\(\d{1,3}\)|\s+-\s+copy(?:\s*\(\d{1,3}\))?)?      # a browser's or Explorer's copy
    (?:\.pdf)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_PLAIN = re.compile(r"[A-Za-z0-9_-]+")
_LOWER_SLUG = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)+")
_JOB_NAME = re.compile(r"(?=.*[a-z])[a-z0-9_]*_\d{6}")
# A Name_MMDDYY at the start of a longer slug: "testerson_101426" in "testerson_101426_2".
_JOB_PREFIX = re.compile(r"[a-z0-9_]*?[a-z][a-z0-9_]*?_\d{6}(?!\d)")
_DATE = re.compile(r"(?<!\d)\d{6}(?!\d)")

# One variant token, read only after the date (before it, the same words are part of the name).
_VARIANT = re.compile(
    r"""
    [\s_]*-(?P<dash>\d{1,2})(?!\d)                         # -2: the hyphen is the marker
    | [\s_-]*(?:
        (?P<gar>gar)
        | (?P<remake>remake)
        | (?P<rev>rev)[\s_-]?(?P<rev_n>\d{0,2})
        | (?P<redo>redo)[\s_-]?(?P<redo_n>\d{0,2})
        | (?P<pickup>pick[\s_-]?up)
        | (?P<top>top)
        | (?P<gb>gb)(?:[\s_-]*(?P<gb_date>\d{1,2}[\s_.-]\d{1,2}[\s_.-]\d{2,4}|\d{6}))?
        | (?P<claude>claude)
    )(?![a-z0-9])
    """,
    re.VERBOSE,
)


def slugify(text: str) -> str:
    """Lowercase with accents folded; every run of other characters becomes one "_"."""
    return re.sub(r"[^a-z0-9]+", "_", _fold(text)).strip("_")


def normalize_job_file(filename: str) -> NormalizedName:
    """Read the job name in a file or folder name (a path is fine: its last part is read).

    Strips " (YYYY-MM-DD)", " (YYYY-MM-DD #n)", "_summary", " summary", ".pdf" and the
    " (1)" / " - Copy" a browser or Explorer adds; drops the client slug Studio puts first;
    slugifies; splits off a variant after the date.
    """
    stem = PureWindowsPath(filename.strip()).name
    m = _DECORATED.fullmatch(stem)
    if m is None:
        return NormalizedName(slugify(stem), None, "hand")
    name, summary = m["name"], m["summary"] or ""
    if summary == "_summary" and _LOWER_SLUG.fullmatch(name):
        studio = _read_studio_name(name)
        if studio is not None:
            return studio
    base, variant = _split_variant(name)
    return NormalizedName(base, variant, _kind(name, summary, bool(m["dated"])))


def _read_studio_name(name: str) -> NormalizedName | None:
    """Studio prefixes the project slug with the client slug; drop it if a job name remains.

    The client slug can be any number of words, or none (a lowercase archive name), so every
    split that still leaves a job name is kept as an alternate.
    """
    tokens = name.split("_")
    skips = (1, 0, *range(2, len(tokens)))
    (base, variant), *others = [_split_variant("_".join(tokens[skip:])) for skip in skips]
    if not _JOB_NAME.fullmatch(base):
        return None
    alternates = dict.fromkeys(b for b, _ in others if b != base and _JOB_NAME.fullmatch(b))
    return NormalizedName(base, variant, "studio", tuple(alternates))


def _kind(name: str, summary: str, dated: bool) -> str:
    if summary.casefold() == "_summary":
        return "pipeline"
    if dated:
        return "dated"
    if not summary and _PLAIN.fullmatch(name):
        return "plain"
    return "hand"


@lru_cache(maxsize=32768)
def _normalize_name(text: str) -> NormalizedName:
    """A job name, an Opportunity Name or typed text: slugified, variant split off."""
    return NormalizedName(*_split_variant(text), "text")


def _split_variant(text: str) -> tuple[str, str | None]:
    """(base slug, variant) for one name; the variant is whatever follows a 6-digit date."""
    folded = _fold(text)
    for date in _DATE.finditer(folded):
        variants = _parse_variants(folded[date.end():])
        if variants is not None:
            return slugify(folded[: date.end()]), "+".join(variants) or None
    return slugify(folded), None


def _parse_variants(tail: str) -> tuple[str, ...] | None:
    """The canonical variant tokens when the whole tail is variants, else None."""
    found: list[str] = []
    pos = 0
    while tail[pos:].strip(" _-."):
        m = _VARIANT.match(tail, pos)
        if m is None:
            return None
        found.append(_canonical_variant(m))
        pos = m.end()
    return tuple(found)


def _lead(base: str) -> str | None:
    """The Name_MMDDYY a slug starts with ("testerson_101426" in "testerson_101426_2"), if any.
    A name that goes on after its date is still a job name: Salesforce has such Opportunities."""
    found = _JOB_PREFIX.match(base)
    return found[0] if found else None


def _run(variant: str | None) -> str | None:
    """The variant without "gar": the pipeline's garage flag is a branch, not a run of the job."""
    tokens = [t for t in (variant or "").split("+") if t and t != "gar"]
    return "+".join(tokens) or None


def _canonical_variant(m: re.Match[str]) -> str:
    if m["dash"] is not None:
        return f"-{int(m['dash'])}"
    if m["rev"] or m["redo"]:
        word, number = ("rev", m["rev_n"]) if m["rev"] else ("redo", m["redo_n"])
        return word + (str(int(number)) if number else "")
    if m["gb"]:
        return "_".join(["gb", *re.findall(r"\d+", m["gb_date"] or "")])
    return next(word for word in ("gar", "remake", "pickup", "top", "claude") if m[word])


def _deaccent(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _fold(text: str) -> str:
    """Casefold and drop accents, keeping every other character as it is."""
    return _deaccent(text).casefold()


# ----- files and folders


def match_file_to_job[J: JobLike](
    filename: str,
    jobs: Sequence[J],
    *,
    folder: str | None = None,
    opportunity_names: Collection[str],
) -> MatchResult[J]:
    """The job a summary file belongs to: its base must equal a job's name or a later name.

    folder is the job folder the file sits in (the first folder under NG Jobs or Old, not a
    material subfolder). A job's folder decides for hand-named files and for the job's own
    files. A file that is, or starts with, another Name_MMDDYY ("Otherson_101426_summary.pdf",
    "Otherson_101426.des"), listed or not, is "folder_disagrees", never filed; a non-summary file
    named for a piece of the folder job's name ("Loft_031126.des" in "VanTest_Loft_031126") is.

    opportunity_names (required, with no default, so that forgetting it fails closed) is every
    Opportunity Name in the Salesforce list, so that sales with no job yet count too. A Studio
    name one of whose readings is such a sale is "unlisted_sale", and no folder settles it; a
    file named for such a sale, or for a run of it, is "no_job", never given to the parent its
    name looks like a run of ("Testerson_101426-2_summary.pdf", "Testerson_101426-2_rev2.pdf");
    and a file in a job folder that starts with such a sale's name more fully than with the
    folder job's ("Testerson_101426_2.des" in "Testerson_101426") is "folder_disagrees". A
    folder is read with the same names, so a folder named for such a sale, or for a run of it,
    is no job's folder: never the parent's. An empty collection is the caller saying it has no
    Salesforce list: only jobs are weighed then.
    """
    by_name = _match_name(normalize_job_file(filename), jobs, opportunity_names)
    if folder is None:
        return by_name
    by_folder = _match_name(normalize_job_file(folder), jobs, opportunity_names)
    return _with_folder(filename, by_name, by_folder, jobs, opportunity_names)


def match_by_folder[J: JobLike](folder: str, jobs: Sequence[J]) -> MatchResult[J]:
    """The job a job folder (or a path to one) belongs to, by the same rules as a file name."""
    return _match_name(normalize_job_file(folder), jobs)


def _match_name[J: JobLike](
    name: NormalizedName, jobs: Sequence[J], opportunity_names: Collection[str] = ()
) -> MatchResult[J]:
    """Every reading of the name counts: it matches only when they all name the same one job,
    and none names a sale that is not in jobs. Readings that name different listed jobs are
    "client_slug_unclear" (the job folder may settle it); a reading that names an unlisted sale
    is "unlisted_sale" (nothing settles it but a person, since that sale has no job folder)."""
    if not re.search(r"[a-z]", name.base):
        return MatchResult("none", reason="not_a_job_name", normalized=name)
    readings = [replace(name, base=base) for base in (name.base, *name.alternates)]
    hits = {r.base: hit for r in readings if (hit := _named_by(r, jobs)) is not None}
    if not hits:
        reason = "no_job" if _lead(name.base) is not None else "not_a_job_name"
        return MatchResult("none", reason=reason, normalized=name)
    named = {id(job) for _, found in hits.values() for job, _ in found}
    candidates = tuple(job for job in jobs if id(job) in named)
    if _names_a_sale([r for r in readings if r.base not in hits], opportunity_names):
        return MatchResult("ambiguous", None, candidates, "unlisted_sale", name)
    if len(hits) > 1 and len(named) > 1:
        return MatchResult("ambiguous", None, candidates, "client_slug_unclear", name)
    base, (reason, found) = next(iter(hits.items()))
    reading = replace(name, base=base)
    if reason == "base" and _is_unlisted_sale(reading, found, jobs, opportunity_names):
        return MatchResult("none", reason="no_job", normalized=name)    # not a run of the parent
    return _decide(name, found, reason)


def _is_unlisted_sale(
    reading: NormalizedName,
    found: Sequence[tuple[JobLike, bool]],
    jobs: Sequence[JobLike],
    opportunity_names: Collection[str],
) -> bool:
    """True when the name is a sale's that no listed job is, or a run of it: an Opportunity Name
    with the reading's base whose run ("_Gar" aside) leads the reading's and goes further than
    the run of every job found. "Testerson_101426-2" in Salesforce, only "Testerson_101426"
    listed: "-2", "-2_rev2", "-2 remake" and "-2-2" are that sale's, never the parent's."""
    run = _run(reading.variant)
    found_run = max(_run_length(job, reading.base, run) for job, _ in found)
    listed = {_identity(n) for job in jobs for n in (job.name, *job.aliases)}
    for sale in opportunity_names:
        base, sale_run = _identity(sale)
        if (base == reading.base and _leads(sale_run, run) and _tokens(sale_run) > found_run
                and (base, sale_run) not in listed):
            return True
    return False


def _run_length(job: JobLike, base: str, run: str | None) -> int:
    """How many run tokens of a name the job's own or later name explains: its run's length
    when it has that base and its run leads the name's ("-2" explains one of "-2+rev2")."""
    lengths = [_tokens(own_run) for own_base, own_run in map(_identity, (job.name, *job.aliases))
               if own_base == base and _leads(own_run, run)]
    return max(lengths, default=0)


def _tokens(run: str | None) -> int:
    return len(run.split("+")) if run else 0


def _identity(name: str) -> tuple[str, str | None]:
    """A name's base and run: what tells one sale from another ("_Gar" is only a branch)."""
    n = _normalize_name(name)
    return n.base, _run(n.variant)


@dataclass(frozen=True)
class _Sale:
    """An Opportunity Name, read as a job is."""

    name: str
    aliases: tuple[str, ...] = ()


def _names_a_sale(readings: Sequence[NormalizedName], opportunity_names: Collection[str]) -> bool:
    """True when one of these readings is the name of an Opportunity in the Salesforce list."""
    if not readings or not opportunity_names:
        return False
    wanted = {r.base for r in readings}
    sales = [_Sale(n) for n in opportunity_names if _normalize_name(n).base in wanted]
    return any(_named_by(r, sales) is not None for r in readings)


def _named_by[J: JobLike](
    reading: NormalizedName, jobs: Sequence[J]
) -> tuple[str, list[tuple[J, bool]]] | None:
    """(tier, jobs) for one reading: the jobs with its name and variant, else with its name, the
    job whose run goes furthest first."""
    for reason, test in _tiers(reading):
        found = _jobs_named(jobs, test)
        if found:
            starts = [(reading.base, reading.variant)]
            return reason, sorted(found, key=lambda f: _depth(f[0], starts), reverse=True)
    return None


def _tiers(reading: NormalizedName) -> tuple[tuple[str, Callable[[NormalizedName], bool]], ...]:
    """The tests a job's name must pass to be named by a reading, best first.

    A job whose own name carries a run ("-2", "remake") can be a job of its own, so a name alone
    finds it only when the file is that run or a run of it ("-2" for "-2 remake").
    """
    run = _run(reading.variant)
    return (
        ("exact", lambda n: (n.base, n.variant) == (reading.base, reading.variant)),
        ("base", lambda n: n.base == reading.base and _leads(_run(n.variant), run)),
    )


def _leads(run: str | None, longer: str | None) -> bool:
    """True when a job's run is a leading part of a file's: None leads any, "-2" leads
    "-2+remake", and "-2" never leads None or "remake"."""
    if run is None:
        return True
    tokens = run.split("+")
    return (longer or "").split("+")[: len(tokens)] == tokens


def _jobs_named[J: JobLike](
    jobs: Sequence[J], test: Callable[[NormalizedName], bool]
) -> list[tuple[J, bool]]:
    """(job, found by its own name rather than a later one) for each job with a passing name."""
    found = []
    for job in jobs:
        own = test(_normalize_name(job.name))
        if own or any(test(_normalize_name(alias)) for alias in job.aliases):
            found.append((job, own))
    return found


def _decide[J](name: NormalizedName, found: list[tuple[J, bool]], reason: str) -> MatchResult[J]:
    candidates = tuple(job for job, _ in found)
    if _lead(name.base) is None:
        return MatchResult("ambiguous", None, candidates, "not_a_job_name", name)
    if len(candidates) > 1:
        return MatchResult("ambiguous", None, candidates, "several_jobs", name)
    job, own = found[0]
    return MatchResult("matched", job, candidates, reason if own else "alias", name)


def _with_folder[J: JobLike](
    filename: str,
    by_name: MatchResult[J],
    by_folder: MatchResult[J],
    jobs: Sequence[J],
    opportunity_names: Collection[str] = (),
) -> MatchResult[J]:
    """A file in a job's folder is that job's, unless its name says otherwise: it names another
    listed job, it starts with another job's or sale's name more fully than with the folder
    job's ("Testerson_101426_2.des" in "Testerson_101426"), or the Name_MMDDYY it carries or
    starts with ("Otherson_101426.des") is not the folder job's own, listed or not."""
    if by_folder.status == "none":
        return by_name
    if by_name.status == "matched" and _holds(by_folder.candidates, by_name.job):
        return by_name
    if by_folder.status == "ambiguous":
        return replace(by_folder, normalized=by_name.normalized)
    home, name = by_folder.candidates[0], by_name.normalized
    if by_name.status == "matched":
        return _disagrees(home, (by_name.job,), name)
    if by_name.status == "ambiguous":
        if _settles(by_name, home):
            return MatchResult("matched", home, (home,), "folder", name)
        return _disagrees(home, by_name.candidates, name)
    if name is not None and _is_summary(name):     # named for its job, and that is not this one
        if _lead(name.base) is not None:
            return _disagrees(home, (), name)
        return MatchResult("matched", home, (home,), "folder", name)
    starts = _starts(filename)
    own = _depth(home, starts)
    fuller = tuple(job for job in jobs if job is not home and _depth(job, starts) > own)
    if fuller:
        return _disagrees(home, fuller, name)
    if own == _NOT_OWN and _points_away(name, home):
        return _disagrees(home, (), name)
    if any(_depth(sale, starts) > own for sale in _sales_starting(starts, opportunity_names)):
        return _disagrees(home, (), name)          # another sale's, one NG Runner has no job for
    return MatchResult("matched", home, (home,), "folder", name)


def _sales_starting(
    starts: Sequence[tuple[str, str | None]], opportunity_names: Collection[str]
) -> list[_Sale]:
    """The Opportunity Names a file name may start with."""
    bases = {base for base, _ in starts}
    return [_Sale(n) for n in opportunity_names if _normalize_name(n).base in bases]


def _settles[J: JobLike](by_name: MatchResult[J], home: J) -> bool:
    """The folder settles among the jobs a name names when its job is one of them, unless the
    name's run goes further into another ("-2_rev2" in the parent's folder, "-2" job listed) or
    one of its readings names a sale that is not a listed job ("unlisted_sale")."""
    if by_name.reason == "unlisted_sale" or not _holds(by_name.candidates, home):
        return False
    if by_name.reason != "several_jobs" or by_name.normalized is None:
        return True
    starts = [(by_name.normalized.base, by_name.normalized.variant)]
    return all(_depth(home, starts) >= _depth(job, starts) for job in by_name.candidates)


def _disagrees[J](home: J, others: Sequence[J], name: NormalizedName | None) -> MatchResult[J]:
    candidates = (home, *(job for job in others if job is not home))
    return MatchResult("ambiguous", None, candidates, "folder_disagrees", name)


def _is_summary(name: NormalizedName) -> bool:
    """True when the whole file name is a job name, as summaries are named: "<Job>_summary.pdf",
    Studio's name, a dated copy, or "<Job>.pdf". Other files only start with one ("<Job>.des")."""
    return name.is_job_name or name.kind in ("pipeline", "studio", "dated")


def _points_away(name: NormalizedName | None, home: JobLike) -> bool:
    """True when a file that is not a summary starts with a Name_MMDDYY that is more than a
    piece of the folder job's name ("Otherson_101426.des", never "Loft_031126.des")."""
    carried = _lead(name.base) if name is not None else None
    return carried is not None and not _within(carried, home)


# Where a word ends after a date, so a longer job name can end there: "testerson_101426_2" ends
# before ".des" or "remake".
_WORD_END = re.compile(r"[a-z0-9](?![a-z0-9])|\d(?=[a-z])")
_NOT_OWN = (-1, -1, False)


def _starts(filename: str) -> list[tuple[str, str | None]]:
    """Each way a file name can start with a job name, as (base, variant): the slug up to its
    first date or up to a later word end, and the variant tokens right after it."""
    stem = PureWindowsPath(filename.strip()).name
    decorated = _DECORATED.fullmatch(stem)
    text = _fold(decorated["name"] if decorated else stem)
    date = next((d for d in _DATE.finditer(text) if re.search(r"[a-z]", text[: d.start()])), None)
    if date is None:
        return []
    ends = [date.end(), *(m.end() for m in _WORD_END.finditer(text, date.end()))]
    return [(slugify(text[:end]), _leading_variants(text[end:])) for end in dict.fromkeys(ends)]


def _leading_variants(tail: str) -> str | None:
    """The variant tokens a text starts with ("-2" in "-2.des"), "+"-joined, or None."""
    found: list[str] = []
    pos = 0
    while (m := _VARIANT.match(tail, pos)) is not None and m.end() > pos:
        found.append(_canonical_variant(m))
        pos = m.end()
    return "+".join(found) or None


def _depth(job: JobLike, starts: Sequence[tuple[str, str | None]]) -> tuple[int, int, bool]:
    """How fully a file name starts with the job's own or later name: (length of the name and
    run it explains, run length, whether both or neither carry "_Gar"), or _NOT_OWN when it never
    does. The run follows the tiers: the job's run must lead the file's. "-2" explains as much as
    "_2" and is the more exact, and the branch only breaks a tie, so a separate "_Gar" job and
    its closet job never take each other's files."""
    best = _NOT_OWN
    for own in (job.name, *job.aliases):
        n = _normalize_name(own)
        run = _run(n.variant)
        tokens = run.split("+") if run else []
        for base, variant in starts:
            if base == n.base and _leads(run, _run(variant)):
                explained = len(n.base) + sum(len(t) for t in tokens)
                best = max(best, (explained, len(tokens), _is_gar(n.variant) == _is_gar(variant)))
    return best


def _is_gar(variant: str | None) -> bool:
    return "gar" in (variant or "").split("+")


def _within(name: str, job: JobLike) -> bool:
    """True when a job-like name is only a piece of the job's own or later name: its last whole
    words up to the date, "loft_031126" in "vantest_loft_031126". Never "oft_031126", and never
    the job's own name with another run ("testerson_101426" for "Testerson_101426-2")."""
    leads = (_lead(_normalize_name(n).base) or "" for n in (job.name, *job.aliases))
    return any(lead.endswith("_" + name) for lead in leads)


def _holds(items: Sequence[object], item: object) -> bool:
    return any(x is item for x in items)


# ----- Fab rows


def match_fab_row(
    row: FabRow,
    opportunities: Sequence[Opportunity],
    ng_jobs: Collection[str],
    list_fetched_at: datetime | None,
    *,
    sold_stages: Collection[str] = SOLD_STAGES,
) -> MatchResult[Opportunity]:
    """The Opportunity a new Fab row is for.

    Auto-matches only on one of two rules.
    (a) An exact Name_MMDDYY with the same variant (case and separators aside, the whole name
        including any text after the date: "Testerson_101426_2"; "_Gar", the garage branch,
        never stops a match and is kept in normalized.variant), whatever its stage and whether
        or not it already has an NG job.
    (b) Strict: every word of the row is explained, and the words left name exactly one sold
        Opportunity that the row's designer owns, that surely has no NG job yet and that no
        other Opportunity could be ("Testerson", "Testerson closet", "Van Testa 10/14/26"). A
        row's words are its name words, one date in a common form (10/14/26, 10-14-2026,
        2026-10-14, 101426), room words (ROOM_WORDS, never the first word), run and branch
        words after the first word, and punctuation; anything else (a first name, a second
        surname, a note, another number) means asking. A row that names a run the
        Opportunity's name lacks ("Testerson remake", "Testerson -2") is a run of that job,
        not a new one: "variant_of_parent", asked, never matched. _by_surname has the rule in
        full.

    ng_jobs holds the Studio project IDs NG Runner already has, and may also hold NG Jobs
    folder names; a folder that only starts with an Opportunity's Name_MMDDYY may be its job,
    so that Opportunity is never matched by (b) and still counts as a rival. list_fetched_at is
    when the list was read from Salesforce; a list older than the row is never used
    ("stale_list": ask for a fresh push).
    """
    if row.first_seen is None or row.first_seen.utcoffset() is None:
        raise ValueError("row.first_seen must be a timezone-aware time")
    if list_fetched_at is not None and list_fetched_at.utcoffset() is None:
        raise ValueError("list_fetched_at must be timezone-aware")
    if list_fetched_at is None or list_fetched_at < row.first_seen:
        return MatchResult("stale_list", reason="stale_list")
    typed = _normalize_name(row.text)
    rules = _RowRules.make(row.designer, ng_jobs, sold_stages, opportunities)
    if _lead(typed.base) is not None:
        exact = _exact_opportunity(typed, opportunities, rules)
        if exact is not None:
            return exact
    if typed.variant is None:
        typed = replace(typed, variant=_typed_variant(row.text))
    return _by_surname(typed, row.text, opportunities, rules)


# Whether an Opportunity already has an NG job: "yes", "maybe" (an NG Jobs folder only starts with
# its Name_MMDDYY, so the folder may be its own or another sale's) or "no".
_JobState = Literal["yes", "maybe", "no"]


@dataclass(frozen=True)
class _RowRules:
    """The surname rule's conditions other than the surname, for one row."""

    designer: str
    sold_stages: frozenset[str]
    ng_ids: frozenset[str]
    ng_leads: frozenset[str]

    @classmethod
    def make(
        cls,
        designer: str,
        ng_jobs: Collection[str],
        sold_stages: Collection[str],
        opportunities: Sequence[Opportunity],
    ) -> _RowRules:
        folders = [n for n in map(normalize_job_file, ng_jobs) if _lead(n.base) is not None]
        owners = _folder_owners(folders, opportunities)
        return cls(
            designer,
            frozenset(_stage(s) for s in sold_stages),
            frozenset(s.strip() for s in ng_jobs) | {o.studio_id.strip() for o in owners},
            frozenset(_lead(n.base) or "" for n in folders),
        )

    def sold(self, o: Opportunity) -> bool:
        return _stage(o.stage) in self.sold_stages

    def owned(self, o: Opportunity) -> bool:
        return _owner_matches(o.owner, self.designer)

    def job(self, o: Opportunity) -> _JobState:
        if o.studio_id.strip() in self.ng_ids:
            return "yes"
        return "maybe" if _lead(_normalize_name(o.name).base) in self.ng_leads else "no"

    def fits(self, o: Opportunity) -> bool:
        """Sold, this designer's, and not surely an NG job already."""
        return self.sold(o) and self.owned(o) and self.job(o) != "yes"

    def rank(self, o: Opportunity) -> tuple[bool, bool, bool, int, int]:
        """Sort key for offered candidates: closest to fitting first, then newest."""
        fit, sold, owned = self.fits(o), self.sold(o), self.owned(o)
        state = ("no", "maybe", "yes").index(self.job(o))
        return (not fit, not sold, not owned, state, -_date_order(o.name))


def _folder_owners(
    folders: Sequence[NormalizedName], opportunities: Sequence[Opportunity]
) -> list[Opportunity]:
    """The Opportunities NG Jobs folders surely belong to, by the same tiers as a file name: a
    "Testerson_101426-2" folder is the "-2" sale's when there is one, else the parent's. A folder
    that could be either of two is neither's for sure (its Name_MMDDYY makes both "maybe")."""
    by_base: dict[str, list[Opportunity]] = {}
    for o in opportunities:
        by_base.setdefault(_normalize_name(o.name).base, []).append(o)
    owners = []
    for folder in folders:
        same = by_base.get(folder.base, [])
        for _, test in _tiers(folder):
            found = [o for o in same if test(_normalize_name(o.name))]
            if found:
                owners += found if len(found) == 1 else []
                break
    return owners


def _exact_opportunity(
    typed: NormalizedName, opportunities: Sequence[Opportunity], rules: _RowRules
) -> MatchResult[Opportunity] | None:
    """Rule (a): the typed name and variant equal the Opportunity's whole name ("_Gar", the
    garage branch, aside). Several Opportunities with the typed name are narrowed by rule (b)'s
    checks.

    A typed run ("remake", "-2") of an Opportunity named without it is a run of that job, never
    a new one, so it is asked about ("variant_of_parent"), not matched.
    """
    run = _run(typed.variant)
    tiers: tuple[tuple[str, Callable[[NormalizedName], bool]], ...] = (
        ("exact_name", lambda n: (n.base, n.variant) == (typed.base, typed.variant)),
        ("exact_name", lambda n: n.base == typed.base and _run(n.variant) == run),
        ("variant_of_parent",
         lambda n: n.base == typed.base and run is not None and _run(n.variant) is None),
    )
    for reason, test in tiers:
        same = [o for o in opportunities if test(_normalize_name(o.name))]
        fitting = [o for o in same if rules.fits(o)] if len(same) > 1 else same
        if len(fitting) == 1 and reason == "exact_name":
            return MatchResult("matched", fitting[0], (fitting[0],), reason, typed)
        if len(fitting) == 1:
            return MatchResult("ambiguous", None, (fitting[0],), reason, typed)
        if same:
            return MatchResult("ambiguous", None, tuple(same), "several_opportunities", typed)
    return None


def _by_surname(
    typed: NormalizedName, text: str, opportunities: Sequence[Opportunity], rules: _RowRules
) -> MatchResult[Opportunity]:
    """Rule (b), strict: a row auto-matches only when every word of it is explained.

    A row (_read_row) is its name words, run together into its key ("Van Testa" -> vantesta),
    one typed date, room words and run and branch words after its first word, and punctuation.
    An Opportunity's key is its name words before its date, room words after the first dropped
    ("Van Testa Garage_041326" -> vantesta; "Closet_Fakeley_041326" -> closetfakeley).
    - Its candidates are the Opportunities with its key, of the typed date when one was typed,
      that share a room word with it when both carry some ("Fakeley closet" names
      "Fakeley_030926", never "Fakeley_pantry_030926").
    - Its rivals are the others that carry its first name word, start with the typed name at a
      word or capital-letter boundary ("Testerson Fakeley", "TestersonFakeley" for
      "Testerson"), or have a key its key starts with ("Testerson_051526" for "Testerson
      Fakeley"); when a date was typed, only those of that date count.
    It matches ("surname_designer") the one candidate that fits (sold, the designer's, no NG job
    yet) only when all of these hold:
    - no other candidate ("several_opportunities") and no rival fits (sold, the designer's, not
      surely an NG job already: an NG Jobs folder that only starts with its name may be
      another sale's);
    - no other candidate of the designer's may be newer (its name date is later, the same, or
      missing), unless it is Lost or Dead Lead: Salesforce lags, so a newer one still at
      Propose may be the sale this row is for;
    - the row's run is the candidate's, "_Gar" aside: a run the candidate's name lacks makes
      it "variant_of_parent", and a run or other text the candidate's name carries after its
      date that the row lacks ("Testerson_101426-2", "Testerson_101426_2") makes it asked about;
    - the designer names one owner among the candidates and rivals: initials that fit two
      owners count for neither;
    - the row holds no number besides its date and runs, and is no other Opportunity's whole
      name.
    Otherwise it offers ("unconfirmed") the candidates, then the rivals, then looser names
    (_offer). SOLD_STAGES leaves out Complete, so a remake row of an installed job is offered,
    that job first, and never matched.
    """
    row = _read_row(text)
    if not row.words:
        return MatchResult("none", reason="not_a_name", normalized=typed)
    named = [o for o in opportunities if _names(row, _read_name(o.name))]
    rivals = [o for o in opportunities
              if not _holds(named, o) and _is_rival(row, _read_name(o.name))]
    candidates = [o for o in named if row.date in (None, _read_name(o.name).date)]
    counted = [o for o in rivals if row.date in (None, _read_name(o.name).date)]
    fits = _fit_test(rules, [*candidates, *counted])
    fitting = [o for o in candidates if fits(o)]
    if len(fitting) > 1:
        return MatchResult("ambiguous", None, tuple(fitting), "several_opportunities", typed)
    one = fitting[0] if fitting else None
    if (one is not None and rules.job(one) == "no" and not row.unread
            and not any(fits(o) for o in counted)):
        verdict = _verdict(one, typed, text, candidates, opportunities, rules)
        if verdict is not None:
            return verdict
    offered = _offer(row, named, rivals, opportunities, rules)
    if offered:
        return MatchResult("ambiguous", None, offered, "unconfirmed", typed)
    return MatchResult("none", reason="no_opportunity", normalized=typed)


def _names(row: _Row, name: _Name) -> bool:
    """True when the row names the Opportunity, dates aside: the same key, and a room word in
    common when both carry some."""
    return name.key == row.key and (not row.rooms or not name.rooms or bool(row.rooms & name.rooms))


def _is_rival(row: _Row, name: _Name) -> bool:
    """True when the Opportunity may be the family the row means, though the row does not name
    it: it carries the row's first name word, starts with the typed name at a word or
    capital-letter boundary, or has a key the row's key starts with."""
    return bool(name.key) and (
        row.words[0] in name.tokens or row.key in name.starts or row.key.startswith(name.key))


def _fit_test(rules: _RowRules, contenders: Sequence[Opportunity]) -> Callable[[Opportunity], bool]:
    """rules.fits, unless the row's designer names more than one owner among the contenders
    (two designers with the same initials): then it names none of them, and nothing fits."""
    owners = {tuple(_person_words(o.owner)) for o in contenders if rules.owned(o)}
    return rules.fits if len(owners) < 2 else (lambda o: False)


def _verdict(
    one: Opportunity,
    typed: NormalizedName,
    text: str,
    candidates: Sequence[Opportunity],
    opportunities: Sequence[Opportunity],
    rules: _RowRules,
) -> MatchResult[Opportunity] | None:
    """The one fitting candidate's verdict, or None to offer it with the others: matched when no
    newer sale of the designer's may be the row's, the row is no other Opportunity's whole name
    and its run is the candidate's; "variant_of_parent" when it names a run the candidate's name
    lacks."""
    if any(_may_be_newer(o, one, rules) for o in candidates):
        return None
    if not _not_another(one, text, opportunities):
        return None
    own, run = _read_name(one.name), _run(typed.variant)
    if own.extra:                       # text after its date that the row does not explain
        return None
    if run == own.run:
        return MatchResult("matched", one, (one,), "surname_designer", typed)
    if own.run is None:
        return MatchResult("ambiguous", None, (one,), "variant_of_parent", typed)
    return None


# Stages an Opportunity is never sold from.
_NEVER_SOLD = frozenset({"lost", "dead lead"})


def _may_be_newer(o: Opportunity, one: Opportunity, rules: _RowRules) -> bool:
    """True when o, another sale of the row's designer that may still be sold, is not surely
    older than one: its name date is later or the same (a name date cannot order two sales of
    one day), or either name has none."""
    if o is one or not rules.owned(o) or _stage(o.stage) in _NEVER_SOLD:
        return False
    theirs, mine = _date_order(o.name), _date_order(one.name)
    return min(theirs, mine) < 0 or theirs >= mine


def _joins(words: Sequence[str]) -> set[str]:
    """The name run together up to each word: van, vantesta, vantestagarage."""
    return {"".join(words[:k]) for k in range(1, len(words) + 1)}


def _not_another(o: Opportunity, text: str, opportunities: Sequence[Opportunity]) -> bool:
    """False when the typed text is the whole name of an Opportunity other than o."""
    typed = slugify(text)
    return not any(slugify(p.name) == typed and p is not o for p in opportunities)


def _offer(
    row: _Row,
    named: Sequence[Opportunity],
    rivals: Sequence[Opportunity],
    opportunities: Sequence[Opportunity],
    rules: _RowRules,
) -> tuple[Opportunity, ...]:
    """Candidates for the person who decides; never a match. The Opportunities the row names,
    then its rivals, each of the typed date first, then closest to fitting; then names whose
    first word starts with the row's or that it is a capitalised part of ("Mockford" ->
    "MockfordJane"); then, only when there is none of these, sold Opportunities of the typed
    date with no NG job yet."""
    def order(o: Opportunity) -> tuple[bool, tuple[bool, bool, bool, int, int]]:
        return _off_date(o, row.date), rules.rank(o)

    offered = [*sorted(named, key=order), *sorted(rivals, key=order)]
    keys = {row.words[0], "".join(row.words[:2])}
    loose = [o for o in opportunities if not _holds(offered, o) and _loosely_named(o.name, keys)]
    if not offered and not loose and row.date:
        loose = [o for o in opportunities
                 if _date_of(o.name) == row.date and rules.sold(o) and rules.job(o) != "yes"]
    return (*offered, *sorted(loose, key=order))


def _off_date(o: Opportunity, date: str | None) -> bool:
    """True when a date was typed and it is not the Opportunity's."""
    return date not in (None, _date_of(o.name))


# ----- Fab rows: a row, or an Opportunity Name, read word by word

# Room words (GLOSSARY: Room word): words a Fab row or an Opportunity Name may carry beside the
# surname, which never name the job. The list is closed on purpose. A room word is dropped from
# the name, so a surname read as one would hide a second family name, which is how the wrong
# family got matched before (finding N2): words that are also plausible surnames (Hall, Wall,
# Bath, Bed, Murphy and the like) stay out. In a spelling, a space stands for spaces or
# underscores.
ROOM_WORDS = frozenset({
    "closet", "closets", "garage", "garages", "pantry", "pantries", "laundry", "office",
    "wardrobe", "mudroom", "bedroom", "master", "primary", "guest", "kids", "linen", "entry",
    "storage", "cabinet", "cabinets", "shelving", "room", "his", "hers",
    "walkin", "walk in", "walk-in", "reachin", "reach in", "reach-in",
})
_ROOM_WORD = re.compile(
    r"(?<![a-z0-9'’])(?:"
    + "|".join(w.replace(" ", r"[\s_]+") for w in sorted(ROOM_WORDS, key=len, reverse=True))
    + r")(?![a-z0-9])"
)
_ROOM_SINGULAR = {"closets": "closet", "garages": "garage", "pantries": "pantry",
                  "cabinets": "cabinet"}

# A first word; an apostrophe never ends a word ("O'Gar" is one word, never O and "gar").
_FIRST_WORD = re.compile(r"[a-z0-9]+(?:['’][a-z0-9]+)*")
_APOSTROPHES = str.maketrans("", "", "'’")


@dataclass(frozen=True)
class _Row:
    """A Fab row read word by word."""

    words: tuple[str, ...]      # its name words, in order: van, testa
    date: str | None            # its one typed date, as MMDDYY
    rooms: frozenset[str]       # its room words, one spelling each: closet, walkin
    unread: bool                # a digit that is neither its date nor in a run word

    @property
    def key(self) -> str:
        return "".join(self.words)


def _read_row(text: str) -> _Row:
    """What each part of a Fab row is: run and branch words after its first word, one typed
    date, room words after its first word, and the name words left; the rest is punctuation."""
    folded = _fold(text)
    spans = [span for _, span in _run_tokens(folded)]
    date = None
    for m in _TYPED_DATE.finditer(folded):
        if not _in_spans(m.start(), spans) and (date := _mmddyy(m)) is not None:
            spans.append(m.span())
            break
    rooms = [m for m in _ROOM_WORD.finditer(folded, _after_first_word(folded))
             if not _in_spans(m.start(), spans)]
    rest = _blank(folded, [*spans, *(m.span() for m in rooms)]).translate(_APOSTROPHES)
    return _Row(tuple(re.findall(r"[a-z]+", rest)), date,
                frozenset(_room(m[0]) for m in rooms), re.search(r"\d", rest) is not None)


@dataclass(frozen=True)
class _Name:
    """An Opportunity Name read word by word, as a Fab row is."""

    words: tuple[str, ...]      # its name words before its date, room words after the first dropped
    date: str | None            # its MMDDYY
    rooms: frozenset[str]       # its room words, before or after its date
    run: str | None             # its run after its date, "_Gar" aside: -2 in "Testerson_101426-2"
    extra: bool                 # other text after its date: the 2 in "Testerson_101426_2"
    tokens: frozenset[str]      # every word and capital-letter part before its date
    starts: frozenset[str]      # those parts run together up to each one: van, vantest

    @property
    def key(self) -> str:
        return "".join(self.words)


@lru_cache(maxsize=32768)
def _read_name(name: str) -> _Name:
    """An Opportunity Name read as a row is: "Van Testa Garage_041326" is vantesta with the room
    word garage, "Testerson_101426-2" is testerson with the run -2."""
    date = _date_of(name)
    head, tail = _around_date(_fold(name), date)
    words, rooms = _words_and_rooms(head)
    variant = _normalize_name(name).variant
    left, tail_rooms = _words_and_rooms(tail, first=False) if variant is None else ((), frozenset())
    parts = _capital_parts(_around_date(_deaccent(name).translate(_APOSTROPHES), date)[0])
    return _Name(words, date, rooms | tail_rooms, _run(variant), bool(left),
                 frozenset((*words, *parts)), frozenset(_joins(parts)))


def _around_date(text: str, date: str | None) -> tuple[str, str]:
    """The text before and after the date of its leading Name_MMDDYY: the first such six digits
    with a letter somewhere before them and neither a letter nor a digit right before them."""
    if date is not None:
        for m in re.finditer(r"(?<![A-Za-z0-9])" + date + r"(?!\d)", text):
            if re.search(r"[A-Za-z]", text[: m.start()]):
                return text[: m.start()], text[m.end():]
    return text, ""


def _words_and_rooms(text: str, *, first: bool = True) -> tuple[tuple[str, ...], frozenset[str]]:
    """A folded text's words, with its room words set apart. When first is set, the first word
    is a name word whatever it is ("closet_fakeley" -> closet, fakeley)."""
    rooms = list(_ROOM_WORD.finditer(text, _after_first_word(text) if first else 0))
    rest = _blank(text, [m.span() for m in rooms]).translate(_APOSTROPHES)
    return tuple(re.findall(r"[a-z0-9]+", rest)), frozenset(_room(m[0]) for m in rooms)


def _capital_parts(text: str) -> list[str]:
    """A name's words, split at capital letters too: "VanTest_Loft" -> van, test, loft."""
    return [p.casefold() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", text)]


def _room(word: str) -> str:
    """A room word's one spelling: "walk-in" and "walk in" are walkin, "closets" is closet."""
    joined = re.sub(r"[\s_-]+", "", word)
    return _ROOM_SINGULAR.get(joined, joined)


def _after_first_word(folded: str) -> int:
    first = _FIRST_WORD.search(folded)
    return first.end() if first else len(folded)


def _blank(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """The text with each span blanked out, so the words on either side stay apart."""
    return "".join(" " if _in_spans(i, spans) else c for i, c in enumerate(text))


def _in_spans(i: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(a <= i < b for a, b in spans)


# A date as Barbara may type it: 2026-10-14, 10/14/26, 10-14-2026, 10.14.26, 10142026, 101426.
_TYPED_DATE = re.compile(
    r"""
    (?<!\d)(?:
        (?P<iso_y>(?:19|20)\d\d)[/.-](?P<iso_m>\d{1,2})[/.-](?P<iso_d>\d{1,2})
      | (?P<us_m>\d{1,2})[/.-](?P<us_d>\d{1,2})[/.-](?P<us_y>\d{4}|\d{2})
      | (?P<long_m>\d\d)(?P<long_d>\d\d)(?P<long_y>(?:19|20)\d\d)
      | (?P<mmddyy>\d{6})
    )(?!\d)
    """,
    re.VERBOSE,
)


def _mmddyy(m: re.Match[str]) -> str | None:
    """The MMDDYY of one typed date, or None when it is no real month and day."""
    if m["mmddyy"]:
        return m["mmddyy"]
    month, day, year = next(
        (m[f"{k}_m"], m[f"{k}_d"], m[f"{k}_y"]) for k in ("iso", "us", "long") if m[f"{k}_m"])
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return None
    return f"{int(month):02d}{int(day):02d}{year[-2:]}"


# Where a variant word can start in typed text: a word (never right after an apostrophe), or a
# "-N" that is not inside a date.
_TYPED_WORD = re.compile(r"(?<![a-z0-9'’])[a-z]|(?<!\d)-\d")


def _typed_variant(text: str) -> str | None:
    """Variant words typed anywhere after the first word ("Testerson closet remake"), for text
    whose date does not carry one."""
    found = [token for token, _ in _run_tokens(_fold(text))]
    return "+".join(dict.fromkeys(found)) or None


# Run words Barbara may type that the pipeline's file names never use, and their variant token.
# They make a card clearer, nothing more: rule (b) asks about any word it cannot explain anyway.
_TYPED_RUN = re.compile(
    r"""(?<![a-z0-9'’])(?:
        (?P<remake>re[\s_-]make|re[\s_-]?made|remakes) | (?P<redo>re[\s_-]do)
      | (?P<rev>revision|revised) | (?P<gb>go[\s_-]?back|g(?:\.\s*|\s+)b\.?)
      | (?P<warranty>warrant(?:y|ee)) | (?P<addon>add[\s_-]?on)
      | (?P<n2>second|two|ii) | (?P<n3>third|three|iii) | (?P<n4>four|iv) | (?P<n5>five|v)
      | (?P<n6>six|vi) | (?P<n7>seven|vii) | (?P<n8>eight|viii) | (?P<n9>nine|ix)
      | (?P<n10>ten|x)
    )(?![a-z0-9])""",
    re.VERBOSE,
)
_TYPED_RUN_TOKEN = {"remake": "remake", "redo": "redo", "rev": "rev", "gb": "gb",
                    "warranty": "warranty", "addon": "addon",
                    **{f"n{n}": f"-{n}" for n in range(2, 11)}}


def _run_tokens(folded: str) -> list[tuple[str, tuple[int, int]]]:
    """(variant token, where it is) for each run or branch word in typed text: anywhere after
    its first word, or right after a date ("Testerson_101426-2")."""
    after = _after_first_word(folded)
    found = [(_canonical_variant(m), m.span()) for start in _TYPED_WORD.finditer(folded, after)
             if (m := _VARIANT.match(folded, start.start()))]
    found += [(_TYPED_RUN_TOKEN[m.lastgroup or ""], m.span())
              for m in _TYPED_RUN.finditer(folded, after)]
    for date in _DATE.finditer(folded):
        pos = date.end()
        while (m := _VARIANT.match(folded, pos)) is not None and m.end() > pos:
            found.append((_canonical_variant(m), m.span()))
            pos = m.end()
    return sorted(found, key=lambda t: t[1])


@lru_cache(maxsize=32768)
def _name_part(text: str) -> tuple[str, ...]:
    """The name words of a name or of typed text, up to its first number: "Fakeley_pantry_030926"
    -> fakeley, pantry; "Van Testa 101426 closet" -> van, testa."""
    words = _name_words(text)
    stop = next((i for i, w in enumerate(words) if w.isdigit()), len(words))
    return tuple(words[:stop])


def _loosely_named(name: str, keys: set[str]) -> bool:
    first = re.split(r"[\s_]+", _deaccent(name).strip())[0]
    parts = {p.casefold() for p in re.findall(r"[A-Z][a-z]+", first)}
    surname = (_name_part(name) or ("",))[0]
    return any(len(k) > 2 and (surname.startswith(k) or k in parts) for k in keys)


# Dropped inside a name before it is split into words: "O'Test-Jones" -> "otestjones".
_JOINERS = str.maketrans("", "", "'.-" + chr(0x2019))


def _name_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", _fold(text).translate(_JOINERS)) if w]


def _date_of(name: str) -> str | None:
    """The MMDDYY of the Name_MMDDYY a name starts with ("Testerson_101426_2" -> "101426")."""
    lead = _lead(_normalize_name(name).base)
    return lead[-6:] if lead else None


def _date_order(name: str) -> int:
    date = _date_of(name)
    return int(date[4:] + date[:4]) if date else -1


def _owner_matches(owner: str, designer: str) -> bool:
    """The row's designer names the owner: initials ("PE", "P.E.", "PQE") or a name ("Pat",
    "Example", "Pat Example")."""
    names, given = _person_words(owner), _person_words(designer)
    if not names or not given:
        return False
    if len(given) > 1 and all(len(w) == 1 for w in given):
        given = ["".join(given)]
    if len(given) > 1:
        return given == names or (given[0], given[-1]) == (names[0], names[-1])
    word = given[0]
    initials = {"".join(w[0] for w in names), names[0][0] + names[-1][0]}
    return len(word) > 1 and (word in initials or word in (names[0], names[-1]))


def _person_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z]+", _fold(text).translate(_JOINERS)) if w]


def _stage(stage: str) -> str:
    return " ".join(stage.casefold().split())
