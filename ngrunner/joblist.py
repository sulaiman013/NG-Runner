"""
The job list: Salesforce's Opportunities, as the online Assembly Reports exports them.

NG Runner can't sign in to Salesforce, and the hosted Assembly Reports (on Render) can't either:
the office PC pushes it the Opportunity list about every 11 minutes, or within a minute of a
Sync request. NG Runner reads that copy from Render's key-guarded GET /api/jobs/export, and
asks for a fresh push through the same Sync request the page's button sends (design section
3.1, "Job list"; build spec 6.3).

Matching a Fab row to a job is where a summary could be filed to the wrong job, so a list is
used only when every part of it can be trusted, and is otherwise refused whole, with the reason
in plain words (JobListError):
- every job, complete: one missing from the list, or one without a name, a stage or a designer,
  could hide a rival and let the surname rule pick another family's job;
- nothing private: the export carries no email, Salesforce record ID or account name, and a
  reply that does is refused (not quietly trimmed), so the leak is fixed where it starts; other
  extra fields are simply not read;
- the time it was read (synced_at), with its zone: a row is never matched against a list read
  before the row first appeared (design section 5, "Matching"; fresh_enough). That time is
  Render's clock while a row's first sighting is this PC's, so a PC clock running behind
  Render's, which would make an old list look new, is refused too.

Messages say "the online Assembly Reports", never "Render": in NG Runner's words a render is one
download of an IC summary (GLOSSARY). The export key comes from the caller (Windows Credential
Manager in the app), is sent only to the export, and is never logged or put in an error.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import requests

from ngrunner.clock import Clock, SystemClock, utc
from ngrunner.match import Opportunity

_EXPORT = "/api/jobs/export"
_SYNC = "/api/sync"
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

# The export's contract (build spec 6.3): these parts, and these fields in every job. The
# matcher needs a name, a stage and a designer for every job.
_PARTS = ("synced_at", "job_count", "jobs")
_FIELDS = ("studio_id", "name", "stage", "designer", "client", "created", "modified")
_NEEDED = frozenset({"name", "stage", "designer"})
_STUDIO_ID = re.compile(r"[0-9]+")       # digits, as Assembly Reports keeps it; it goes into links

# Fields the export must never carry: Assembly Reports' private columns (the client's email, the
# Salesforce record ID, the account name, which holds the client's address) and the Salesforce
# fields they come from. Any field naming an email, a phone or an address counts too.
_PRIVATE_FIELDS = frozenset({"opp_id", "opportunity_id", "id", "account_name", "account",
                             "accountid", "account_id"})
_PRIVATE_WORDS = ("email", "phone", "address")

MAX_CLOCK_SKEW = timedelta(minutes=2)    # how far this PC's clock may run behind Render's
_FUTURE_SLACK = timedelta(minutes=1)     # how far past its clock a list's read time may be
# 10,800 synthetic jobs export as 1.9 MB (measured 2026-10-29); imports stop at 16 MB.
MAX_BYTES = 32_000_000

_TRANSIENT = frozenset({"timeout", "network", "server_error"})
_SOURCE = "The online Assembly Reports"
_LIST = "The job list from the online Assembly Reports"
_NOT_READ = "the job list was not read."


class JobListError(Exception):
    """The job list could not be read, or can't be trusted. str() is the reason in plain words,
    without the key or any private value; code says which case it is:
    - "timeout", "network", "server_error" (transient: trying again later may simply work);
    - "refused" (the export key is unset on Render or a different one), "not_found" (Render has
      no export yet: its update is not deployed), "unexpected_reply" (any other status, a
      redirect included: none is followed), "too_big", "not_json";
    - "bad_list" (a part, a field or a value missing or unreadable, a count that disagrees),
      "private_fields", "naive_time" (a read time without a zone), "clock" (this PC's clock
      is behind Render's), "future" (a read time after the clock it came with).
    """

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code

    @property
    def transient(self) -> bool:
        """True when trying again later may simply work (a timeout, a lost connection, a
        problem on Render's side); otherwise something needs fixing first."""
        return self.code in _TRANSIENT


@dataclass(frozen=True)
class JobList:
    """One export of the job list. Times are timezone-aware and kept in UTC.

    synced_at: when the list was read from Salesforce (stored on Render), by Render's clock.
        None only when Render has no list yet: then there are no opportunities, and the list
        is never fresh enough. match.match_fab_row takes this time as list_fetched_at.
    fetched_at: when NG Runner fetched it, by this PC's clock.
    opportunities: every Opportunity in the list, in Render's order; owner is its designer.
    clients: each Opportunity's client name by Studio project ID ("" when Render has none), for
        the report link (fetch's FetchTarget.client) and the store's opportunities table.
    clock_offset: how far Render's clock read ahead of this PC's (negative: behind), measured
        from the reply's Date header; None when there was none, or it was not checked.
    """

    synced_at: datetime | None
    fetched_at: datetime
    opportunities: tuple[Opportunity, ...]
    clients: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}), hash=False)
    clock_offset: timedelta | None = None

    def __post_init__(self) -> None:
        if self.synced_at is not None:
            utc(self.synced_at)             # a naive time is refused, never guessed
        utc(self.fetched_at)


def fresh_enough(job_list: JobList, first_seen: datetime) -> bool:
    """True when the list was read at or after the row first appeared (the same instant
    counts): only then may the row be matched against it (design section 5). A list with no
    read time never is. first_seen must be timezone-aware."""
    utc(first_seen)
    return job_list.synced_at is not None and job_list.synced_at >= first_seen


class RenderClient:
    """The online Assembly Reports' job list: export() reads it, request_push() asks for a fresh
    one. One thread at a time (a requests.Session is not made for sharing).

    base_url: Settings.render_url; https only (plain http only to this PC, for tests).
    export_key: Render's ASSEMBLY_EXPORT_KEY, from NG Runner's secrets; never logged or shown.
    http: a requests.Session, or a stand-in with get and post taking (url, headers=, timeout=,
        stream=, allow_redirects=) and returning a reply with status_code, headers,
        iter_content(chunk_size) and close(). Default: a new Session.
    clock: this PC's clock (default SystemClock); fetched_at comes from it.
    timeout_s: for connecting and for each read; max_bytes: the largest reply read.
    max_clock_skew: how far this PC's clock may run behind Render's, by the reply's Date header,
        before a list is refused. None ignores Render's clock altogether, for a day simulated
        on a FakeClock behind a real HTTP server (whose Date header is real time); the read
        time is then checked against the fake clock instead.
    """

    def __init__(self, base_url: str, export_key: str, *, http: Any = None,
                 clock: Clock | None = None, timeout_s: float = 60.0, max_bytes: int = MAX_BYTES,
                 max_clock_skew: timedelta | None = MAX_CLOCK_SKEW) -> None:
        self._base = _usable_url(base_url)
        self._key = _usable_key(export_key)
        self._http = http if http is not None else requests.Session()
        self._clock = clock if clock is not None else SystemClock()
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._max_skew = max_clock_skew

    def __repr__(self) -> str:                  # never the key
        return f"RenderClient({self._base!r})"

    def export(self) -> JobList:
        """The job list as it is on Render now. Raises JobListError when it can't be read or
        can't be trusted; nothing is retried here (the caller polls)."""
        data, server_date = self._fetch()
        fetched_at = self._clock.now()
        body = _parse(data)
        _refuse_private(body)
        jobs = _jobs(body)
        synced_at = _synced_at(body["synced_at"], has_jobs=bool(jobs))
        offset = self._check_clocks(synced_at, server_date, fetched_at)
        opportunities = tuple(Opportunity(name=j["name"], studio_id=j["studio_id"],
                                          owner=j["designer"], stage=j["stage"]) for j in jobs)
        clients = MappingProxyType({j["studio_id"]: j["client"] or "" for j in jobs})
        return JobList(synced_at, fetched_at, opportunities, clients, offset)

    def request_push(self) -> bool:
        """Ask Render for a fresh list through its own Sync request; the office PC pushes within
        about a minute. True when Render took the request (HTTP 200). It never raises for a busy,
        odd or missing reply: it is only a nudge, and the office PC pushes on its own about every
        11 minutes anyway. No key is sent: Sync needs none."""
        try:
            reply = self._http.post(self._base + _SYNC, headers={"Accept": "application/json"},
                                    timeout=self._timeout_s, stream=True, allow_redirects=False)
            try:
                return reply.status_code == 200
            finally:
                reply.close()
        except Exception:  # noqa: BLE001 - a failed nudge only means the list comes on schedule
            return False

    def _fetch(self) -> tuple[bytes, datetime | None]:
        """The export's bytes, and Render's clock from the reply's Date header. Errors from the
        HTTP layer are re-raised without their chain (`from None`): a requests error carries the
        request, and so the key."""
        try:
            reply = self._http.get(self._base + _EXPORT, headers={"X-Export-Key": self._key},
                                   timeout=self._timeout_s, stream=True, allow_redirects=False)
        except requests.Timeout:
            raise JobListError("timeout", f"{_SOURCE} did not answer within"
                                          f" {self._timeout_s:g} seconds; {_NOT_READ}") from None
        except (requests.RequestException, OSError):
            raise JobListError("network", f"{_SOURCE} could not be reached; {_NOT_READ}") from None
        try:
            _check_status(reply.status_code)
            return self._read(reply), _server_time(reply.headers)
        except (requests.RequestException, OSError):
            raise JobListError("network", f"{_SOURCE} stopped sending the job list part-way;"
                                          f" {_NOT_READ}") from None
        finally:
            reply.close()

    def _read(self, reply: Any) -> bytes:
        """The body, refused once it grows past max_bytes (no real list comes near it)."""
        chunks, size = [], 0
        for chunk in reply.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > self._max_bytes:
                limit = (f"{self._max_bytes / 1_000_000:g} MB" if self._max_bytes >= 1_000_000
                         else f"{self._max_bytes:,} bytes")
                raise JobListError("too_big", f"{_SOURCE} sent a reply larger than any job list"
                                              f" (over {limit}); {_NOT_READ}")
            chunks.append(chunk)
        return b"".join(chunks)

    def _check_clocks(self, synced_at: datetime | None, server_date: datetime | None,
                      fetched_at: datetime) -> timedelta | None:
        """Refuse a list when this PC's clock runs behind Render's by more than max_clock_skew,
        or when it was read after the clock it came with; return the measured offset."""
        if self._max_skew is None or server_date is None:
            _refuse_future(synced_at, fetched_at, "this PC's time",
                           beyond=self._max_skew or timedelta(0))
            return None
        offset = server_date - fetched_at
        if offset > self._max_skew:
            raise JobListError("clock", f"This PC's clock is {_about(offset)} behind the online"
                               " Assembly Reports' clock, so NG Runner can't tell whether the job"
                               " list is newer than a row; the list was not used. Turn on \"Set"
                               " time automatically\" in Windows.")
        _refuse_future(synced_at, server_date, "the online Assembly Reports' own time")
        return offset


# ----- the address and the key


def _usable_url(url: str) -> str:
    """Render's address as scheme://host[:port]. https only (the key travels in a header);
    plain http only to this PC, for tests. Never echoed: it could hold a sign-in."""
    parts = urlsplit(url.strip()) if isinstance(url, str) else None
    local = parts is not None and parts.scheme == "http" and parts.hostname in _LOOPBACK
    if (parts is None or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/")
            or parts.query or parts.fragment or not (parts.scheme == "https" or local)):
        raise ValueError("the job-list address must start with https:// and hold no path or"
                         " sign-in (http:// only to this PC, for tests)")
    return f"{parts.scheme}://{parts.netloc}"


def _usable_key(key: str) -> str:
    """The export key, trimmed. Never echoed, logged or put in an error."""
    text = key.strip() if isinstance(key, str) else ""
    if not text:
        raise ValueError("NG Runner has no export key for the job list: add it to its secrets"
                         " as export_key, the same value as ASSEMBLY_EXPORT_KEY on Render")
    if not (text.isascii() and text.isprintable()) or any(c.isspace() for c in text):
        raise ValueError("the export key must be one unbroken run of plain letters, digits and"
                         " punctuation, as ASSEMBLY_EXPORT_KEY on Render is")
    return text


# ----- reading Render's reply


def _check_status(status: int) -> None:
    """Refuse any reply but 200, saying what it means."""
    if status == 403:
        raise JobListError("refused", f"{_SOURCE} refused NG Runner's export key: it is not set"
                                      " there, or it is a different one.")
    if status == 429 or status >= 500:
        raise JobListError("server_error", f"{_SOURCE} had a problem on its side (HTTP {status});"
                                           f" {_NOT_READ}")
    if status == 404:
        raise JobListError("not_found", f"{_SOURCE} has no job-list export yet: its update is not"
                                        " deployed.")
    if status != 200:
        raise JobListError("unexpected_reply", f"{_SOURCE} answered the job-list request oddly"
                                               f" (HTTP {status}); {_NOT_READ}")


def _parse(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (ValueError, RecursionError):        # bad JSON or text encoding; absurd nesting
        raise JobListError("not_json", f"{_SOURCE} answered, but not with a job list;"
                                       f" {_NOT_READ}") from None


def _refuse_private(body: Any) -> None:
    """Refuse a reply carrying a private field anywhere, naming the field, never its value."""
    if not isinstance(body, dict):
        return
    jobs = body.get("jobs")
    records = [body, *(j for j in jobs if isinstance(j, dict))] if isinstance(jobs, list) else [
        body]
    for record in records:
        for name in record:
            if _is_private(name):
                raise JobListError("private_fields", f'{_SOURCE} sent the job list with private'
                                   f' client details ("{name}"), which NG Runner must never'
                                   " receive; the list was not used, and the export needs fixing.")


def _is_private(name: str) -> bool:
    folded = name.casefold()
    return folded in _PRIVATE_FIELDS or any(word in folded for word in _PRIVATE_WORDS)


def _jobs(body: Any) -> list[dict[str, Any]]:
    """The jobs of a reply shaped as the export promises, every value readable."""
    if not isinstance(body, dict):
        raise _bad("is not a list of jobs")
    for part in _PARTS:
        if part not in body:
            raise _bad(f'is missing "{part}"')
    jobs = body["jobs"]
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        raise _bad("is not a list of jobs")
    for name in _FIELDS:
        missing = sum(name not in j for j in jobs)
        if missing:
            raise _bad(f'is missing "{name}" in {missing} of {len(jobs)} jobs')
    _check_count(body["job_count"], len(jobs))
    _check_values(jobs)
    return jobs


def _check_count(count: Any, carried: int) -> None:
    if type(count) is not int or count < 0:
        raise _bad("has no readable job count")
    if count != carried:
        raise _bad(f"says it holds {_jobs_word(count)} but carries {carried}")


def _check_values(jobs: list[dict[str, Any]]) -> None:
    """A Studio project ID of digits, a name, a stage and a designer in every job (the matcher
    needs them all), text or nothing for the rest, and one Studio project ID per job."""
    for name in _FIELDS:
        problems = Counter(p for j in jobs if (p := _problem(name, j[name])) is not None)
        if problems:
            problem, count = next(iter(problems.items()))
            raise _bad(f"has {_jobs_word(count)} {problem}")
    if len({j["studio_id"] for j in jobs}) < len(jobs):
        raise _bad("gives one Studio project ID to more than one job")


def _problem(name: str, value: Any) -> str | None:
    if name == "studio_id":
        ok = isinstance(value, str) and _STUDIO_ID.fullmatch(value) is not None
        return None if ok else "whose Studio project ID is not a number"
    if value is not None and not isinstance(value, str):
        return f'whose "{name}" is not text'
    if name in _NEEDED and not (value or "").strip():
        return f"with no {name}"
    return None


def _synced_at(value: Any, *, has_jobs: bool) -> datetime | None:
    """When the list was read, in UTC; None only for Render with no list yet (and no jobs)."""
    if value is None:
        if has_jobs:
            raise _bad("has jobs but no time they were read")
        return None
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None:
        raise _bad("has no readable time it was read")
    if when.utcoffset() is None:
        raise JobListError("naive_time", f"{_LIST} gives the time it was read with no time zone,"
                                         " so NG Runner can't tell how old it is; the list was"
                                         " not used.")
    if not has_jobs:
        raise _bad("is empty")
    return utc(when)


# ----- clocks


def _server_time(headers: Mapping[str, str]) -> datetime | None:
    """Render's clock when it answered, from the Date header; None when absent or unreadable."""
    value = next((v for k, v in headers.items() if k.lower() == "date"), None)
    try:
        when = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None
    if when is None or when.utcoffset() is None:
        return None
    return when.astimezone(timezone.utc)


def _refuse_future(synced_at: datetime | None, now: datetime, whose: str, *,
                   beyond: timedelta = timedelta(0)) -> None:
    """Refuse a list read after the clock it came with (a wrong zone or clock on Render).
    beyond widens the allowance when that clock is this PC's, which may lag Render's."""
    if synced_at is not None and synced_at - now > _FUTURE_SLACK + beyond:
        raise JobListError("future", f"{_LIST} says it was read {_about(synced_at - now)} after"
                                     f" {whose}, so its time can't be trusted; the list was not"
                                     " used.")


def _about(span: timedelta) -> str:
    minutes = round(abs(span).total_seconds() / 60)
    if minutes < 120:
        return f"about {minutes} minute{'' if minutes == 1 else 's'}"
    hours = round(minutes / 60)
    return f"about {hours} hours" if hours < 48 else f"about {round(hours / 24)} days"


def _jobs_word(count: int) -> str:
    return "1 job" if count == 1 else f"{count} jobs"


def _bad(what: str) -> JobListError:
    return JobListError("bad_list", f"{_LIST} {what}, so it can't be used to match rows.")
