"""
Fetching a job's IC summary from Studio, strictly, and the two-fetch agreement (design 3.5, the
Fetch gate).

Why: the IC summary is the input to everything, and filing one to the wrong job is the mistake
intake must never make. Studio's report address needs no sign-in but carries the exact client
and project slugs and the Studio project ID, in one form per report layout:

    /install_summary_landscape/<client-slug>/<project-slug>/<studio-id>/   landscape
    /install_summary/<client-slug>/<project-slug>/<studio-id>/             portrait

A wrong slug gets a fast 404, so the slugs are derived from the job's name and client as
Assembly Reports' resolver derives them (app/studio.py there) and tried in order, after the
address that worked last time. P1a has no Studio sign-in and no pasted links.

Where NG Runner departs from Assembly Reports: a fetch is ok only for this job's whole PDF. Its
bytes start with %PDF and end with %%EOF (white space aside), and page 1's PROJECT NAME, as
check_summary reads it, is there and slug-equal to the job's name. Assembly Reports accepts a
report with no PROJECT NAME; nothing then says whose it is, so NG Runner refuses it.

A fetch is not a check: an early render is this job's whole PDF, so it is fetched, and its check
(Fetched.check) says what it lacks. Nor is a check enough to file: a render Studio has not
finished can be complete in itself. agree() is the rest of the gate: two fetches at least 10
minutes apart, both complete, in the one layout the job uses, with the same content_fingerprint().
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import ReadTimeoutError

from ngrunner.intake.summary_check import SummaryCheck, check_summary, content_fingerprint
from ngrunner.match import slugify

Source = str | os.PathLike[str] | bytes  # a PDF: its bytes or its path (as summary_check takes it)

Layout = Literal["landscape", "portrait"]
FetchCode = Literal["ok", "not_found", "timeout", "network", "http_error", "not_pdf", "truncated",
                    "too_big", "no_project_name", "wrong_job"]

STUDIO_URL = "https://studio.inspiredclosets.com"
LAYOUT_PATHS: dict[str, str] = {"landscape": "install_summary_landscape", "portrait": "install_summary"}
MAX_CANDIDATES = 24  # derived slug pairs tried per fetch; wrong ones 404 in a fraction of a second
# Bytes read at a time. urllib3 returns a read only once it holds this much (or the file ends),
# and the time limit is checked between reads, so a small read keeps a slow download from
# running long past it.
_CHUNK = 1 << 13
# Assembly Reports' browser-like User-Agent, which Studio has always answered, with our own name.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
              " Chrome/140.0 Safari/537.36 NGRunner/1.0")

# ----- what is fetched


@dataclass(frozen=True)
class FetchTarget:
    """
    The job whose IC summary is fetched.

    studio_id  the Studio project ID, digits only (it goes into the address).
    name       the job's name (the Opportunity Name); page 1's PROJECT NAME must slug-equal it.
    client     the client's name from the job list, for more slug guesses; None or "" when unknown.
    layout     "landscape" or "portrait": every fetch of one job uses the same one.
    known_url  an address that gave this job's report before (renders.url), tried first; None or
               "" for none. Only its slugs are used: it is asked for on Studio, in `layout`, so it
               can never lead to another host, layout or job. It must carry this Studio ID.

    Raises ValueError for a target that cannot name one report.
    """

    studio_id: str
    name: str
    client: str | None
    layout: Layout
    known_url: str | None = None

    def __post_init__(self) -> None:
        if not (isinstance(self.studio_id, str) and self.studio_id.isascii() and self.studio_id.isdigit()):
            raise ValueError("studio_id must be the Studio project ID, digits only")
        if self.layout not in LAYOUT_PATHS:
            raise ValueError(f"layout must be one of: {', '.join(LAYOUT_PATHS)}")
        if not slugify(self.name):
            raise ValueError("name must hold letters or digits to compare with PROJECT NAME")
        if self.known_url and _known_slugs(self.known_url)[2] != self.studio_id:
            raise ValueError("known_url carries another Studio ID")


@dataclass(frozen=True)
class Fetched:
    """
    The outcome of one fetch.

    ok      True only for this job's whole PDF (fetch_render); then data and check are set.
    url     the address the outcome came from. When ok, the report's: keep it as the job's next
            known_url. None when every address answered 404.
    data    the PDF's bytes when ok, else None: a refused file can never be filed.
    code    "ok", or why not: not_found (404 at every address), timeout, network, http_error,
            not_pdf (not a PDF, or one that cannot be read), truncated (it does not end like a
            whole PDF), too_big, no_project_name (page 1 has no PROJECT NAME, or nothing beside
            it) or wrong_job (another job's PROJECT NAME).
    reason  one plain sentence naming the job, with no address or Studio ID.
    status  that answer's HTTP status; None when Studio gave none (timeout, network).
    check   when ok, check_summary's verdict on data. check.ok False is an early render: fetch
            it again later. Even check.ok True is not enough to file (agree()).
    found_name  on wrong_job, the name page 1 of the report carries, as printed, so the pipeline
            can tell a renamed job from a real mismatch; else None. Compare it by slug, as the
            refusal itself does.
    """

    ok: bool
    url: str | None
    data: bytes | None = field(repr=False)
    code: FetchCode
    reason: str
    status: int | None
    check: SummaryCheck | None = field(default=None, repr=False)
    found_name: str | None = None


# ----- the addresses


def candidate_slugs(name: str, client: str | None = None) -> list[tuple[str, str]]:
    """
    (client slug, project slug) guesses from the job's name and client, best first, at most 24.

    First Assembly Reports' order: the client slug is the name's first word, then its camel-case
    words last to first (VanTesta: testa, van), then the client's last and first words; each is
    paired with the project slug (the whole name), then with the name split at its capitals
    (van_testa_101426). Those slugs are match.slugify's: lowercase, accents folded, every other
    run of characters one "_".

    Then the forms that order misses, which real sold jobs need (4 of 60 failed without them on
    Studio, 2026-10-30): Studio keeps a hyphen in both slugs, and an account name can carry a
    street address after the person's name, so its surname is neither its first nor its last word.
    """
    project = slugify(name)
    camel = [slugify(word) for word in re.findall(r"[A-Z][a-z0-9]*", _deaccented(name).split("_")[0])]
    clients = [project.split("_")[0], *camel[-1:], *camel[:1]]
    if client and (words := slugify(client).split("_")):
        clients += [words[-1], words[0]]
    projects = [project]
    if len(camel) > 1:
        projects.append("_".join(camel) + "_" + project.split("_")[-1])
    first = [(c, p) for c in clients for p in projects]
    every_client = [*clients, *_client_words(client or ""), _studio_slug(name.split("_")[0])]
    every_project = [*projects, _studio_slug(name)]
    pairs = dict.fromkeys((c, p) for c, p in [*first, *((c, p) for c in every_client for p in every_project)]
                          if c and p)
    return list(pairs)[:MAX_CANDIDATES]


def _client_words(client: str) -> list[str]:
    """The person's words at the start of an account name, each as Studio spells it and in its
    hyphenated parts. The first word holding a digit starts an address, so the words stop there."""
    words: list[str] = []
    for raw in re.split(r"[\s,]+", client):
        if any(c.isdigit() for c in raw):
            break
        word = _studio_slug(raw)
        words += [word, *word.split("-")] if "-" in word else [word]
    return words


def _studio_slug(text: str) -> str:
    """Lowercase with accents folded and a hyphen kept, as Studio's own slugs are; every other run
    of characters is one "_"."""
    return re.sub(r"[^a-z0-9-]+", "_", _deaccented(text).lower()).strip("_-")


def _deaccented(text: str) -> str:
    """The text with accents dropped, case kept, so camel-case words split as slugify spells them."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def report_urls(target: FetchTarget, base_url: str = STUDIO_URL) -> list[str]:
    """The addresses fetch_render asks, in order: the known address's slugs, then the derived
    ones (candidate_slugs), each pair once, all in target.layout on base_url."""
    base = base_url.rstrip("/")
    known = [_known_slugs(target.known_url)[:2]] if target.known_url else []
    pairs = dict.fromkeys([*known, *candidate_slugs(target.name, target.client)])
    return [f"{base}/{LAYOUT_PATHS[target.layout]}/{client}/{project}/{target.studio_id}/"
            for client, project in pairs]


# A report address: /install_summary[_landscape]/<client slug>/<project slug>/<Studio ID>/. A
# slug is URL-safe and holds a letter or digit (a slug of dots would lead elsewhere on Studio).
_SLUG = r"(?=[A-Za-z0-9._~%-]*[A-Za-z0-9])[A-Za-z0-9._~%-]+"
_REPORT_PATH = re.compile(rf"/install_summary(?:_landscape)?/(?P<client>{_SLUG})/(?P<project>{_SLUG})"
                          r"/(?P<studio_id>[0-9]+)/?")


def _known_slugs(url: str) -> tuple[str, str, str]:
    """(client slug, project slug, Studio ID) of a report address; ValueError if it is not one."""
    path = _REPORT_PATH.fullmatch(urlsplit(url).path)
    if path is None:
        raise ValueError("known_url is not a Studio report address")
    return path["client"], path["project"], path["studio_id"]


# ----- fetching


class Http(Protocol):
    """What fetch_render needs of its session: requests.Session.get."""

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


def fetch_render(target: FetchTarget, *, http: Http | None = None, timeout_s: float = 240,
                 max_bytes: int = 50_000_000, base_url: str = STUDIO_URL) -> Fetched:
    """
    Fetch the job's IC summary from Studio and say whether it is this job's whole PDF.

    The addresses (report_urls) are asked in turn until one gives the report. A 404 moves on to
    the next. A timeout or a network failure stops at once: Studio is slow or out of reach, and
    every other address would fail alike. Any other failure (an error status, a file that is not
    this job's whole report) moves on too; when no address gives the report, the most telling
    failure is returned: wrong_job, no_project_name, too_big, truncated, not_pdf, http_error,
    then not_found.

    http       a requests.Session or anything with its get(); None makes one and closes it after.
    timeout_s  bounds each address: connecting, waiting while Studio renders, each wait for more
               bytes, and the download as a whole, which is stopped at its next read (8 KB) once
               the time is up. Real fetches took 7 to 95 s, and a timeout ends the search at
               once, so this is Assembly Reports' 240 s and no less.
    max_bytes  a larger file is too_big, and is not read at all when its Content-Length says so.
    base_url   Studio's address (Settings.studio_url); tests point it at a fake Studio.

    Whatever Studio does, the outcome is a Fetched, never an exception.
    """
    urls = report_urls(target, base_url)
    if http is not None:
        return _search(target, http, urls, timeout_s, max_bytes)
    with requests.Session() as own:
        return _search(target, own, urls, timeout_s, max_bytes)


# Studio slow or out of reach: every other address would fail alike, so the search stops there.
_ENDS_SEARCH = frozenset({"timeout", "network"})
# The failures a search reports when no address gave the report, most telling first.
_RANK: tuple[FetchCode, ...] = ("wrong_job", "no_project_name", "too_big", "truncated", "not_pdf",
                                "http_error", "not_found")


def _search(target: FetchTarget, session: Http, urls: list[str], timeout_s: float,
            max_bytes: int) -> Fetched:
    """Each address in turn until one gives this job's report; else the most telling failure."""
    failures: list[Fetched] = []
    for url in urls:
        fetched = _fetch_one(target, session, url, timeout_s, max_bytes)
        if fetched.ok or fetched.code in _ENDS_SEARCH:
            return fetched
        failures.append(fetched)
    told = [failure for failure in failures if failure.code != "not_found"]
    if not told:
        return _refused(target, "not_found", None, 404, tried=len(urls))
    return min(told, key=lambda failure: _RANK.index(failure.code))


def _fetch_one(target: FetchTarget, session: Http, url: str, timeout_s: float,
               max_bytes: int) -> Fetched:
    deadline = time.monotonic() + timeout_s
    facts = {"seconds": f"{timeout_s:g}", "limit": _size(max_bytes)}
    try:
        response = session.get(url, stream=True, timeout=(timeout_s, timeout_s),
                               headers={"User-Agent": USER_AGENT})
    except requests.Timeout:  # connecting, or waiting for Studio to render (ConnectTimeout too)
        return _refused(target, "timeout", url, None, **facts)
    except requests.ConnectionError:  # refused, unreachable, no DNS, a TLS failure
        return _refused(target, "network", url, None)
    except requests.RequestException:  # too many redirects, a malformed answer
        return _refused(target, "http_error", url, None)
    with closing(response):
        status = response.status_code
        if status == 404:
            return _refused(target, "not_found", url, 404, tried=1)
        if status != 200:
            return _refused(target, "http_error", url, status)
        body = _read_body(response, max_bytes, deadline)
    if isinstance(body, str):
        return _refused(target, body, url, status, **facts)  # type: ignore[arg-type]
    return _verdict(target, url, status, body)


def _read_body(response: requests.Response, max_bytes: int, deadline: float) -> bytes | str:
    """The whole body, or the failure code that stopped it: "too_big", "timeout" or "truncated"."""
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        return "too_big"  # refused before a byte of it is read
    chunks: list[bytes] = []
    got = 0
    try:
        for chunk in response.iter_content(_CHUNK):
            got += len(chunk)
            if got > max_bytes:
                return "too_big"
            if time.monotonic() > deadline:
                return "timeout"  # still arriving when time is up
            chunks.append(chunk)
    except requests.RequestException as error:
        return "timeout" if _timed_out(error) else "truncated"  # stalled, or broke off mid-file
    return b"".join(chunks)


def _timed_out(error: requests.RequestException) -> bool:
    """True for a wait that ran out. Mid-body, requests raises it as a ConnectionError that wraps
    urllib3's ReadTimeoutError (requests.models.Response.iter_content)."""
    return isinstance(error, requests.Timeout) or any(isinstance(arg, ReadTimeoutError) for arg in error.args)


def _size(n: int) -> str:
    """A byte count in plain words: 50 MB, 3.6 KB, 900 bytes."""
    for unit, scale in (("MB", 1_000_000), ("KB", 1_000)):
        if n >= scale:
            return f"{n / scale:.1f}".removesuffix(".0") + f" {unit}"
    return f"{n} bytes"


# ----- the verdict on the bytes

_PDF_WHITESPACE = b"\0\t\n\f\r "  # the PDF standard's white-space characters


def _verdict(target: FetchTarget, url: str, status: int, data: bytes) -> Fetched:
    """
    Ok only for this job's whole PDF: it starts with %PDF and ends with %%EOF (white space
    aside), and page 1's PROJECT NAME, as check_summary reads it, is there and slug-equal to the
    job's name. A missing PROJECT NAME is refused: nothing else says whose report it is.
    """
    if not data.startswith(b"%PDF-"):
        return _refused(target, "not_pdf", url, status)
    if not data.rstrip(_PDF_WHITESPACE).endswith(b"%%EOF"):
        return _refused(target, "truncated", url, status)
    check = check_summary(data)
    if check.page_count == 0:  # check_summary could not read the file at all
        return _refused(target, "not_pdf", url, status, why="unreadable")
    if check.project_name is None:
        blank = "blank_project_name" in check.details.get("codes", ())
        return _refused(target, "no_project_name", url, status, why="blank_project_name" if blank else None)
    if slugify(check.project_name) != slugify(target.name):
        return _refused(target, "wrong_job", url, status, found=check.project_name)
    return Fetched(True, url, data, "ok", _reason("ok", target), status, check)


_REASONS: dict[str, str] = {
    "ok": "Fetched the IC summary for {name} from Studio.",
    "not_found": "Studio has no IC summary for {name} at any of the {tried} addresses tried.",
    "http_error": "Studio answered with an error instead of the IC summary for {name}.",
    "timeout": "Studio took longer than {seconds} seconds to send the IC summary for {name}.",
    "network": "Could not reach Studio to fetch the IC summary for {name}.",
    "not_pdf": "Studio sent something that is not a PDF as the IC summary for {name}.",
    "unreadable": "Studio sent a PDF that cannot be read as the IC summary for {name}.",
    "truncated": "The IC summary for {name} did not arrive whole from Studio.",
    "too_big": "The IC summary for {name} is larger than the {limit} NG Runner accepts.",
    "no_project_name": "Page 1 of the IC summary for {name} does not show PROJECT NAME.",
    "blank_project_name": "Page 1 of the IC summary for {name} shows PROJECT NAME with nothing next to it.",
    "wrong_job": "Studio sent the IC summary for {found}, not for {name}.",
}


def _reason(why: str, target: FetchTarget, **facts: object) -> str:
    return _REASONS[why].format(name=target.name, **facts)


def _refused(target: FetchTarget, code: FetchCode, url: str | None, status: int | None, *,
             why: str | None = None, **facts: object) -> Fetched:
    """A failure: no bytes and no check, so nothing about it can be filed."""
    found = facts.get("found")
    return Fetched(False, url, None, code, _reason(why or code, target, **facts), status,
                   found_name=found if isinstance(found, str) else None)


# ----- two fetches must agree (design 3.5; AGENTS.md, Before filing goes live, items 1 and 2)

MIN_GAP = timedelta(minutes=10)
AgreementCode = Literal["agree", "differ", "first_incomplete", "second_incomplete", "same_bytes",
                        "layouts_differ", "too_soon"]


@dataclass(frozen=True)
class Render:
    """
    One fetch of a job's IC summary, as agree() compares it (GLOSSARY: Render).

    source      the PDF: its bytes, or the path of the file it was cached to.
    fetched_at  when it was fetched, timezone-aware.
    layout      the report layout it was fetched in (FetchTarget.layout).
    """

    source: Source = field(repr=False)
    fetched_at: datetime
    layout: Layout

    def __post_init__(self) -> None:
        if self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be a timezone-aware time")
        if self.layout not in LAYOUT_PATHS:
            raise ValueError(f"layout must be one of: {', '.join(LAYOUT_PATHS)}")


@dataclass(frozen=True)
class Agreement:
    """
    The verdict on two fetches of one job (agree()).

    ok           True only when code is "agree": the summary may be filed.
    code         what holds, which says what to do next (agree()).
    reason       one plain sentence, with no address or Studio ID.
    fingerprint  the content_fingerprint() the two share, when they agree; else None.
    """

    ok: bool
    code: AgreementCode
    reason: str
    fingerprint: str | None = None


def agree(first: Render, second: Render, *, min_gap: timedelta = MIN_GAP) -> Agreement:
    """
    Whether two fetches of one job hold the same complete IC summary, far enough apart to trust.

    The first rule that fails decides the code, in this order:
      first_incomplete   the first fails check_summary (an early render), so it is no basis;
      second_incomplete  the second fails check_summary: fetch again later;
      same_bytes         the two are one file byte for byte: something served one render twice
                         (a cache, say). Studio renders afresh, so all 6 real pairs measured
                         differ, and identical bytes prove nothing about a stable render;
      layouts_differ     they were fetched in different layouts, or Studio sent pages in another
                         layout than the one asked for: a job has one layout, so that its
                         fingerprints can be compared;
      differ             their content_fingerprint()s differ: Studio changed the summary or had
                         not finished it, so the second becomes the new first;
      too_soon           the second came less than min_gap after the first, or not after it;
      agree              all hold.
    What a PDF says comes before when it was fetched: waiting would not change it.

    Each PDF is read once, from its bytes or its cached file (OSError when that cannot be read).
    Raises ValueError when min_gap is not more than zero.
    """
    if min_gap <= timedelta(0):
        raise ValueError("min_gap must be more than zero: one fetch can never agree with itself")
    one = _load(first.source)
    if not (first_check := check_summary(one)).ok:
        return Agreement(False, "first_incomplete", _incomplete("first", first_check))
    two = _load(second.source)
    if not (second_check := check_summary(two)).ok:
        return Agreement(False, "second_incomplete", _incomplete("second", second_check))
    if one == two:
        return Agreement(False, "same_bytes", "The two fetches are one file byte for byte, so"
                                              " something served one render twice.")
    if (why := _layout_problem(first, first_check, second, second_check)) is not None:
        return Agreement(False, "layouts_differ", why)
    fingerprint = content_fingerprint(one)
    if content_fingerprint(two) != fingerprint:
        return Agreement(False, "differ", "The two fetches hold different text: Studio changed the IC"
                                          " summary between them, or had not finished it.")
    gap = second.fetched_at - first.fetched_at
    if gap <= timedelta(0):
        return Agreement(False, "too_soon", "The second fetch is not later than the first.")
    if gap < min_gap:
        return Agreement(False, "too_soon", f"The second fetch came {_span(gap)} after the first; they"
                                            f" must be at least {_span(min_gap)} apart.")
    return Agreement(True, "agree", f"Two fetches {_span(gap)} apart hold the same IC summary.", fingerprint)


def _layout_problem(first: Render, first_check: SummaryCheck, second: Render,
                    second_check: SummaryCheck) -> str | None:
    """Why the two cannot be compared for their layout: asked for in two, or not sent in the one asked."""
    if first.layout != second.layout:
        return (f"The two fetches are in different report layouts ({first.layout} and {second.layout}),"
                " so they cannot be compared.")
    for which, render, check in (("first", first, first_check), ("second", second, second_check)):
        pages = check.details.get("orientation")
        if pages != render.layout:
            return (f"The {which} fetch was asked for in the {render.layout} layout,"
                    f" but its pages are {pages}.")
    return None


def _incomplete(which: str, check: SummaryCheck) -> str:
    """'The first fetch is not a complete IC summary: page 1 lists no designs (and 1 more problem).'"""
    problems = check.missing or ["The check did not pass."]
    first = problems[0].rstrip(".")
    more = len(problems) - 1
    tail = f" (and {more} more problem{'s' * (more != 1)})" if more else ""
    return f"The {which} fetch is not a complete IC summary: {first[:1].lower()}{first[1:]}{tail}."


def _load(source: Source) -> bytes:
    """The PDF's bytes, read once, so both checks see the same file."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    return Path(source).read_bytes()


def _span(gap: timedelta) -> str:
    """A time span in plain words: under a minute, 1 minute, 10 minutes, 2 hours 5 minutes."""
    minutes = int(gap.total_seconds() // 60)
    if minutes < 1:
        return "under a minute"
    hours, minutes = divmod(minutes, 60)
    parts = [f"{hours} hour{'s' * (hours != 1)}"] if hours else []
    parts += [f"{minutes} minute{'s' * (minutes != 1)}"] if minutes or not hours else []
    return " ".join(parts)
