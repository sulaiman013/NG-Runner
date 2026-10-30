"""The job list: the online Assembly Reports' export becomes the Opportunities NG Runner matches
Fab rows against, and a list that can't be trusted (partial, private, timeless, from a wrong
clock) is refused in plain words, never used."""
from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from ngrunner.clock import FakeClock, SystemClock, to_iso
from ngrunner.joblist import JobList, JobListError, RenderClient, fresh_enough
from ngrunner.match import Opportunity

Z = timezone.utc
NOW = datetime(2026, 10, 30, 13, 0, tzinfo=Z)
KEY = "test-export-key-0123456789abcdef"
URL = "https://render.example.com"

TESTERSON = {"studio_id": "100001", "name": "Testerson_101426", "stage": "Ready to Order",
             "designer": "Pat Example", "client": "Terry Testerson", "created": "2026-10-14",
             "modified": "2026-10-28"}
FAKELEY = {"studio_id": "100002", "name": "Fakeley_102126", "stage": "Propose",
           "designer": "Sam Sample", "client": "Fran Fakeley", "created": "2026-10-21",
           "modified": "2026-10-29"}


def listing(jobs: list[dict] | None = None, synced_at: str | None = "2026-10-30T12:55:00+00:00",
            **extra: object) -> dict:
    jobs = [TESTERSON, FAKELEY] if jobs is None else jobs
    return {"synced_at": synced_at, "job_count": len(jobs), "jobs": jobs, **extra}


# ----- a fake Render: the two routes NG Runner uses, over a requests.Session-like interface


class Reply:
    """What NG Runner reads from a requests.Response: status, headers, body chunks, close()."""

    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status_code, self.body, self.headers = status, body, headers or {}
        self.closed = False

    def iter_content(self, chunk_size: int = 1):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeRender:
    """The export answers only the right key, like the real route; a Sync request is remembered
    (the real one sets publish_requested for the office PC)."""

    def __init__(self, body: object = None, *, status: int = 200, raw: bytes | None = None,
                 date: datetime | None = NOW, key: str = KEY, fails: Exception | None = None,
                 breaks: Exception | None = None):
        self.body = listing() if body is None else body
        self.status, self.raw, self.date, self.key = status, raw, date, key
        self.fails = fails          # raised instead of answering, as requests does
        self.breaks = breaks        # raised while the body arrives
        self.push_requested = False
        self.replies: list[Reply] = []

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.date is not None:
            headers["Date"] = format_datetime(self.date, usegmt=True)
        return headers

    def get(self, url: str, **kw: object) -> Reply:
        if self.fails is not None:
            raise self.fails
        if url != URL + "/api/jobs/export":
            return self._reply(404, b"<html>Not Found</html>")
        if kw["headers"].get("X-Export-Key") != self.key:      # type: ignore[union-attr]
            return self._reply(403, b'{"error": "Not allowed."}')
        body = self.raw if self.raw is not None else json.dumps(self.body).encode("utf-8")
        reply = self._reply(self.status, body)
        if self.breaks is not None:
            reply.iter_content = self._broken           # type: ignore[method-assign]
        return reply

    def _broken(self, chunk_size: int = 1):
        yield b'{"synced_at": '
        raise self.breaks                               # type: ignore[misc]

    def post(self, url: str, **kw: object) -> Reply:
        if url != URL + "/api/sync":
            return self._reply(404, b"<html>Not Found</html>")
        self.push_requested = True
        self.key_sent = "X-Export-Key" in kw.get("headers", {})    # type: ignore[operator]
        return self._reply(200, b'{"ok": true, "requesting": true}')

    def _reply(self, status: int, body: bytes) -> Reply:
        reply = Reply(status, body, self._headers())
        self.replies.append(reply)
        return reply


class OddRender:
    """A Render that answers a Sync request with whatever the test hands it, or fails."""

    def __init__(self, answer: Reply | Exception):
        self.answer = answer

    def post(self, url: str, **kw: object) -> Reply:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def client(render: object, **kw: object) -> RenderClient:
    return RenderClient(URL, KEY, http=render, clock=FakeClock(NOW), **kw)


# ----- a good list


def test_a_good_list_becomes_opportunities_with_the_designer_as_owner():
    jobs = client(FakeRender()).export()
    assert jobs.opportunities == (
        Opportunity(name="Testerson_101426", studio_id="100001", owner="Pat Example",
                    stage="Ready to Order"),
        Opportunity(name="Fakeley_102126", studio_id="100002", owner="Sam Sample", stage="Propose"),
    )
    assert jobs.synced_at == datetime(2026, 10, 30, 12, 55, tzinfo=Z)
    assert jobs.fetched_at == NOW
    assert dict(jobs.clients) == {"100001": "Terry Testerson", "100002": "Fran Fakeley"}


# ----- Render can't be read


def refusal(render: object, **kw: object) -> JobListError:
    with pytest.raises(JobListError) as caught:
        client(render, **kw).export()
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)
    return caught.value


def test_a_wrong_or_unset_key_is_refused_in_plain_words():
    error = refusal(FakeRender(key="the key Render holds"))
    assert error.code == "refused" and not error.transient
    assert str(error) == ("The online Assembly Reports refused NG Runner's export key: it is not"
                          " set there, or it is a different one.")


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_a_problem_on_renders_side_is_worth_trying_again(status):
    error = refusal(FakeRender(status=status, raw=b"<html>Service Unavailable</html>"))
    assert error.code == "server_error" and error.transient
    assert str(error) == (f"The online Assembly Reports had a problem on its side (HTTP {status});"
                          " the job list was not read.")


def test_a_render_without_the_export_says_its_update_is_not_deployed():
    error = refusal(FakeRender(status=404, raw=b"<html>Not Found</html>"))
    assert error.code == "not_found" and not error.transient
    assert str(error) == ("The online Assembly Reports has no job-list export yet: its update is"
                          " not deployed.")


@pytest.mark.parametrize("status", [301, 302, 400, 401, 204])
def test_any_other_reply_is_refused_and_named(status):
    error = refusal(FakeRender(status=status, raw=b""))
    assert error.code == "unexpected_reply" and not error.transient
    assert str(error) == (f"The online Assembly Reports answered the job-list request oddly"
                          f" (HTTP {status}); the job list was not read.")


@pytest.mark.parametrize("fails, code, words", [
    (requests.ReadTimeout(), "timeout", "did not answer within 60 seconds"),
    (requests.ConnectTimeout(), "timeout", "did not answer within 60 seconds"),
    (requests.ConnectionError(), "network", "could not be reached"),
    (requests.exceptions.SSLError(), "network", "could not be reached"),
    (OSError("socket closed"), "network", "could not be reached"),
])
def test_a_timeout_or_a_lost_connection_is_worth_trying_again(fails, code, words):
    error = refusal(FakeRender(fails=fails))
    assert error.code == code and error.transient
    assert str(error) == f"The online Assembly Reports {words}; the job list was not read."


def test_a_connection_lost_while_the_list_arrives_is_worth_trying_again():
    error = refusal(FakeRender(breaks=requests.exceptions.ChunkedEncodingError()))
    assert error.code == "network" and error.transient


@pytest.mark.parametrize("raw", [
    b"<!doctype html><html><body>Sign in</body></html>",
    b"",
    b'{"synced_at": "2026-10-30T12:55:00+00:00", "jobs": [',
    "{}".encode("utf-16")[:3],
    b"[" * 100_000 + b"]" * 100_000,
])
def test_a_reply_that_is_not_json_is_refused(raw):
    error = refusal(FakeRender(raw=raw))
    assert error.code == "not_json" and not error.transient
    assert str(error) == ("The online Assembly Reports answered, but not with a job list; the job"
                          " list was not read.")


def test_a_reply_larger_than_any_job_list_is_refused_unread():
    render = FakeRender(raw=b"[" + b" " * 5000 + b"]")
    error = refusal(render, max_bytes=1000)
    assert error.code == "too_big" and not error.transient
    assert str(error) == ("The online Assembly Reports sent a reply larger than any job list"
                          " (over 1,000 bytes); the job list was not read.")
    assert client(FakeRender()).export().opportunities          # the default limit reads a list


def test_every_reply_is_closed_so_no_connection_is_left_open():
    good, refused, broken = FakeRender(), FakeRender(key="another"), FakeRender(raw=b"<html>")
    client(good).export()
    client(good).request_push()
    for render in (refused, broken):
        with pytest.raises(JobListError):
            client(render).export()
    replies = good.replies + refused.replies + broken.replies
    assert len(replies) == 4 and all(reply.closed for reply in replies)


# ----- a list that can't be trusted is refused whole
# Matching needs every Opportunity: a job missing from the list could let the surname rule
# pick another family's job.


PRIVATE = {"email": "testerson@example.com", "opp_id": "006TESTONLY000001",
           "account_name": "Terry Testerson - 1 Example Way, Faketown, CA",
           "Consumer_Email__c": "testerson@example.com", "Id": "006TESTONLY000001",
           "Account": {"Name": "Terry Testerson - 1 Example Way, Faketown, CA"},
           "client_email": "testerson@example.com"}


@pytest.mark.parametrize("name", sorted(PRIVATE))
def test_a_job_carrying_a_private_field_refuses_the_list_without_repeating_it(name):
    error = refusal(FakeRender(listing([TESTERSON | {name: PRIVATE[name]}, FAKELEY])))
    assert error.code == "private_fields" and not error.transient
    assert str(error) == (f'The online Assembly Reports sent the job list with private client'
                          f' details ("{name}"), which NG Runner must never receive; the list'
                          f' was not used, and the export needs fixing.')
    assert all(part not in str(error) for part in ("example.com", "Example Way", "006TEST"))


def test_a_private_field_beside_the_list_refuses_it_too():
    error = refusal(FakeRender(listing(emails=["testerson@example.com"])))
    assert error.code == "private_fields" and '("emails")' in str(error)


def test_other_extra_fields_are_dropped_so_a_harmless_addition_never_stops_filing():
    extra = listing([TESTERSON | {"stage_group": "Ready to Order"}, FAKELEY], generator="v2")
    assert client(FakeRender(extra)).export() == client(FakeRender()).export()


FIELDS = ("studio_id", "name", "stage", "designer", "client", "created", "modified")


@pytest.mark.parametrize("name", FIELDS)
def test_a_job_missing_a_field_refuses_the_whole_list(name):
    partial = {k: v for k, v in FAKELEY.items() if k != name}
    error = refusal(FakeRender(listing([TESTERSON, partial])))
    assert error.code == "bad_list" and not error.transient
    assert str(error) == (f'The job list from the online Assembly Reports is missing "{name}" in'
                          f' 1 of 2 jobs, so it can\'t be used to match rows.')


@pytest.mark.parametrize("name", ["synced_at", "job_count", "jobs"])
def test_a_reply_missing_a_part_of_the_list_is_refused(name):
    body = {k: v for k, v in listing().items() if k != name}
    error = refusal(FakeRender(body))
    assert error.code == "bad_list"
    assert str(error) == (f'The job list from the online Assembly Reports is missing "{name}",'
                          f' so it can\'t be used to match rows.')


@pytest.mark.parametrize("change, words", [
    ({"studio_id": "10000a"}, "has 1 job whose Studio project ID is not a number"),
    ({"studio_id": " 100002"}, "has 1 job whose Studio project ID is not a number"),
    ({"studio_id": ""}, "has 1 job whose Studio project ID is not a number"),
    ({"studio_id": None}, "has 1 job whose Studio project ID is not a number"),
    ({"studio_id": 100002}, "has 1 job whose Studio project ID is not a number"),
    ({"studio_id": "100002.0"}, "has 1 job whose Studio project ID is not a number"),
    ({"name": ""}, "has 1 job with no name"),
    ({"name": "  "}, "has 1 job with no name"),
    ({"name": None}, "has 1 job with no name"),
    ({"stage": None}, "has 1 job with no stage"),
    ({"stage": ""}, "has 1 job with no stage"),
    ({"designer": None}, "has 1 job with no designer"),
    ({"designer": " "}, "has 1 job with no designer"),
    ({"name": 7}, 'has 1 job whose "name" is not text'),
    ({"client": 7}, 'has 1 job whose "client" is not text'),
    ({"created": ["2026-10-21"]}, 'has 1 job whose "created" is not text'),
    ({"modified": 20261029}, 'has 1 job whose "modified" is not text'),
    ({"studio_id": "100001"}, "gives one Studio project ID to more than one job"),
])
def test_a_job_with_an_unreadable_value_refuses_the_whole_list(change, words):
    error = refusal(FakeRender(listing([TESTERSON, FAKELEY | change])))
    assert error.code == "bad_list"
    assert str(error) == (f"The job list from the online Assembly Reports {words}, so it can't be"
                          f" used to match rows.")


def test_two_jobs_with_no_designer_are_counted():
    nobody = {"designer": None}
    error = refusal(FakeRender(listing([TESTERSON | nobody, FAKELEY | nobody])))
    assert "has 2 jobs with no designer" in str(error)


@pytest.mark.parametrize("count, words", [
    (3, "says it holds 3 jobs but carries 2"),
    (1, "says it holds 1 job but carries 2"),
    ("2", "has no readable job count"),
    (True, "has no readable job count"),
    (None, "has no readable job count"),
])
def test_a_count_that_disagrees_with_the_jobs_refuses_the_list(count, words):
    error = refusal(FakeRender(listing() | {"job_count": count}))
    assert error.code == "bad_list" and words in str(error)


def test_a_missing_client_or_date_is_kept_as_unknown():
    blank = {"client": None, "created": None, "modified": ""}
    jobs = client(FakeRender(listing([TESTERSON, FAKELEY | blank]))).export()
    assert dict(jobs.clients) == {"100001": "Terry Testerson", "100002": ""}
    assert len(jobs.opportunities) == 2


@pytest.mark.parametrize("account, person", [
    ("Terry Testerson - 12 Main St, Fakeville 90000", "Terry Testerson"),
    ("Terry Testerson 12 Main St", "Terry Testerson"),
    ("Testerson,12 Main St", "Testerson"),
    ("Testerson, Terry, 12 Main St", "Testerson, Terry"),
    ("Mary-Jo Testerson-Fakeley - 3B Oak", "Mary-Jo Testerson-Fakeley"),
    ("12 Main St", ""),
    ("Terry Testerson", "Terry Testerson"),
], ids=["a hyphen, then an address", "the address right after", "a comma with no space",
        "a comma inside the name", "hyphens inside names stay", "nothing but an address",
        "no address"])
def test_the_client_never_holds_an_address(account, person):
    jobs = client(FakeRender(listing([TESTERSON | {"client": account}]))).export()
    assert dict(jobs.clients) == {"100001": person}


# ----- the time the list was read


def test_render_with_no_list_yet_gives_an_empty_list_that_is_never_fresh():
    jobs = client(FakeRender(listing([], synced_at=None))).export()
    assert (jobs.synced_at, jobs.fetched_at, jobs.opportunities) == (None, NOW, ())
    assert not fresh_enough(jobs, NOW - timedelta(days=365))


def test_a_naive_sync_time_is_refused():
    error = refusal(FakeRender(listing(synced_at="2026-10-30T12:55:00")))
    assert error.code == "naive_time" and not error.transient
    assert str(error) == ("The job list from the online Assembly Reports gives the time it was"
                          " read with no time zone, so NG Runner can't tell how old it is; the"
                          " list was not used.")


@pytest.mark.parametrize("synced_at", ["yesterday", "", 1793451300, "2026-10-30 12:55 PDT"])
def test_an_unreadable_sync_time_is_refused(synced_at):
    error = refusal(FakeRender(listing(synced_at=synced_at)))
    assert error.code == "bad_list" and "has no readable time it was read" in str(error)


def test_jobs_with_no_sync_time_are_refused():
    error = refusal(FakeRender(listing(synced_at=None)))
    assert error.code == "bad_list" and "has jobs but no time they were read" in str(error)


def test_an_empty_list_with_a_sync_time_is_refused():
    error = refusal(FakeRender(listing([])))
    assert error.code == "bad_list" and "is empty" in str(error)


def test_a_sync_time_in_another_zone_is_kept_in_utc():
    jobs = client(FakeRender(listing(synced_at="2026-10-30T05:55:00-07:00"))).export()
    assert jobs.synced_at == datetime(2026, 10, 30, 12, 55, tzinfo=Z)
    assert jobs.synced_at.tzinfo == Z


def test_a_list_is_fresh_enough_only_when_read_at_or_after_the_rows_first_appearance():
    read = datetime(2026, 10, 30, 12, 55, tzinfo=Z)
    jobs = JobList(synced_at=read, fetched_at=NOW, opportunities=())
    assert fresh_enough(jobs, read)                                   # the same instant
    assert fresh_enough(jobs, read - timedelta(seconds=1))
    assert not fresh_enough(jobs, read + timedelta(seconds=1))        # read before the row
    pacific = timezone(timedelta(hours=-7))
    assert fresh_enough(jobs, datetime(2026, 10, 30, 5, 55, tzinfo=pacific))
    assert not fresh_enough(JobList(None, NOW, ()), read)


def test_a_naive_time_is_refused_by_fresh_enough_and_by_a_job_list():
    with pytest.raises(ValueError):
        fresh_enough(JobList(NOW, NOW, ()), datetime(2026, 10, 30, 12, 55))
    for synced_at, fetched_at in [(datetime(2026, 10, 30, 12, 55), NOW),
                                  (NOW, datetime(2026, 10, 30, 13, 0))]:
        with pytest.raises(ValueError):
            JobList(synced_at, fetched_at, ())


# ----- the key and the address


def test_the_key_never_shows_in_the_clients_repr():
    assert KEY not in repr(client(FakeRender())) and KEY not in str(client(FakeRender()))


def test_a_key_pasted_with_spaces_around_it_still_works():
    render = FakeRender()
    jobs = RenderClient(URL, f"  {KEY}\r\n", http=render, clock=FakeClock(NOW)).export()
    assert len(jobs.opportunities) == 2


@pytest.mark.parametrize("key", ["", "   ", "two words", "line\nbreak", "naïve-key-0123", None])
def test_a_missing_or_unusable_key_is_refused_before_any_request(key):
    with pytest.raises(ValueError) as caught:
        RenderClient(URL, key, http=FakeRender(), clock=FakeClock(NOW))   # type: ignore[arg-type]
    secret = (key or "").strip()
    assert not secret or secret not in str(caught.value)


@pytest.mark.parametrize("url", ["http://render.example.com", "ftp://render.example.com",
                                 "https://user:pw@render.example.com", "render.example.com",
                                 "https://render.example.com/api", "https://render.example.com?x=1",
                                 "https://"])
def test_only_an_encrypted_address_with_no_path_is_accepted(url):
    with pytest.raises(ValueError, match="https://"):
        RenderClient(url, KEY, http=FakeRender())


@pytest.mark.parametrize("url", ["https://render.example.com/", "http://127.0.0.1:8770",
                                 "http://localhost:8770/"])
def test_an_https_address_or_this_pc_for_tests_is_accepted(url):
    RenderClient(url, KEY, http=FakeRender())


# ----- asking for a fresh push


def test_request_push_asks_render_for_a_fresh_list_without_sending_the_key():
    render = FakeRender()
    assert client(render).request_push() is True
    assert render.push_requested and not render.key_sent      # Sync needs no key: none is sent


@pytest.mark.parametrize("answer", [
    Reply(401, b'{"error": "Please sign in again."}'),       # sign-in turned on at Render
    Reply(302, b"", {"Location": "/setup"}),
    Reply(503, b"<html>Service Unavailable</html>"),
    Reply(502, b""),
    requests.ReadTimeout(),
    requests.ConnectionError(),
    requests.exceptions.InvalidHeader(),
    OSError("socket closed"),
])
def test_request_push_tolerates_a_busy_or_odd_reply(answer):
    assert client(OddRender(answer)).request_push() is False


def test_request_push_counts_any_plain_ok_as_taken():
    assert client(OddRender(Reply(200, b"<html>odd but fine</html>"))).request_push() is True


# ----- clocks: synced_at is Render's clock, a row's first sighting is this PC's


def test_a_pc_clock_behind_renders_refuses_the_list():
    # 10 minutes behind, a list read 9 minutes before a row appeared would look newer than it.
    error = refusal(FakeRender(date=NOW + timedelta(minutes=10)))
    assert error.code == "clock" and not error.transient
    assert str(error) == ("This PC's clock is about 10 minutes behind the online Assembly"
                          " Reports' clock, so NG Runner can't tell whether the job list is newer"
                          " than a row; the list was not used. Turn on \"Set time automatically\""
                          " in Windows.")


def test_a_pc_clock_ahead_only_makes_the_list_look_older_so_it_is_used():
    render = FakeRender(listing(synced_at="2026-10-30T12:45:00+00:00"),
                        date=NOW - timedelta(minutes=10))      # Render reads 12:50
    jobs = client(render).export()
    assert jobs.clock_offset == timedelta(minutes=-10)
    assert jobs.synced_at == datetime(2026, 10, 30, 12, 45, tzinfo=Z)


def test_a_small_clock_difference_is_tolerated_and_measured():
    assert client(FakeRender(date=NOW + timedelta(seconds=90))).export().clock_offset == (
        timedelta(seconds=90))


def test_without_a_date_header_the_clock_can_not_be_checked():
    assert client(FakeRender(date=None)).export().clock_offset is None


def test_a_list_read_in_the_future_is_refused():
    error = refusal(FakeRender(listing(synced_at="2026-10-30T16:00:00+00:00")))
    assert error.code == "future" and not error.transient
    assert str(error) == ("The job list from the online Assembly Reports says it was read about"
                          " 3 hours after the online Assembly Reports' own time, so its time"
                          " can't be trusted; the list was not used.")


def test_a_list_read_in_the_future_of_this_pc_is_refused_when_render_gives_no_time():
    error = refusal(FakeRender(listing(synced_at="2026-10-30T13:30:00+00:00"), date=None))
    assert error.code == "future" and "about 30 minutes after this PC's time" in str(error)


def test_a_test_on_a_fake_clock_can_ignore_renders_clock():
    # A real HTTP server sends real time; a fake-clock day doesn't: max_clock_skew=None skips it.
    jobs = client(FakeRender(date=NOW + timedelta(days=3)), max_clock_skew=None).export()
    assert jobs.clock_offset is None and len(jobs.opportunities) == 2


# ----- over real HTTP: a local server and a real requests.Session, as on Jim's PC


class LocalRender(BaseHTTPRequestHandler):
    """Render's two routes over HTTP on 127.0.0.1. The server's `mode` picks the answer."""

    def do_GET(self) -> None:
        server = self.server
        if server.mode == "hang_up":                      # type: ignore[attr-defined]
            return self._hang_up()
        if self.path == "/elsewhere":                     # where a redirect would lead
            return self._json(200, server.listing())      # type: ignore[attr-defined]
        if self.path != "/api/jobs/export":
            return self._json(404, {"error": "Not found."})
        if self.headers.get("X-Export-Key") != KEY:
            return self._json(403, {"error": "Not allowed."})
        if server.mode == "redirect":                     # type: ignore[attr-defined]
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{server.server_port}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if server.mode == "slow":                         # type: ignore[attr-defined]
            server.release.wait(5)                        # type: ignore[attr-defined]
        return self._json(200, server.listing())          # type: ignore[attr-defined]

    def do_POST(self) -> None:
        if self.server.mode == "hang_up":                 # type: ignore[attr-defined]
            return self._hang_up()
        if self.path != "/api/sync":
            return self._json(404, {"error": "Not found."})
        self.server.push_requested = True                 # type: ignore[attr-defined]
        return self._json(200, {"ok": True, "requesting": True})

    def _hang_up(self) -> None:
        """Close the connection without a word, as a dropped line does."""
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)

    def _json(self, status: int, body: object) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)                        # adds a real Date header
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:   # quiet
        pass


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), LocalRender)
        self.mode, self.push_requested, self.release = "good", False, threading.Event()

    def listing(self) -> dict:
        read = datetime.now(timezone.utc) - timedelta(minutes=1)     # read a minute ago
        return listing(synced_at=to_iso(read))

    def handle_error(self, request: object, client_address: object) -> None:
        pass                    # the slow route's client is gone by the time it answers


@pytest.fixture
def local() -> Iterator[LocalServer]:
    server = LocalServer()
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def real_client(port: int, **kw: object) -> RenderClient:
    session = requests.Session()
    session.trust_env = False                   # no proxy or netrc from this PC in a test
    return RenderClient(f"http://127.0.0.1:{port}", KEY, http=session, clock=SystemClock(), **kw)


def test_over_real_http_a_good_list_arrives_and_a_push_is_asked_for(local):
    jobs = real_client(local.server_port).export()
    assert [o.name for o in jobs.opportunities] == ["Testerson_101426", "Fakeley_102126"]
    assert jobs.clock_offset is not None and abs(jobs.clock_offset) < timedelta(seconds=5)
    assert jobs.fetched_at - jobs.synced_at >= timedelta(minutes=1)
    assert real_client(local.server_port).request_push() is True and local.push_requested


def test_over_real_http_a_redirect_is_not_followed_so_the_key_stays_with_render(local):
    local.mode = "redirect"
    with pytest.raises(JobListError) as caught:
        real_client(local.server_port).export()
    assert caught.value.code == "unexpected_reply" and "HTTP 302" in str(caught.value)


def test_over_real_http_a_slow_render_times_out(local):
    local.mode = "slow"
    with pytest.raises(JobListError) as caught:
        real_client(local.server_port, timeout_s=0.3).export()
    assert caught.value.code == "timeout" and "within 0.3 seconds" in str(caught.value)


def test_over_real_http_a_dropped_connection_is_a_network_error_and_a_push_is_just_false(local):
    local.mode = "hang_up"
    with pytest.raises(JobListError) as caught:
        real_client(local.server_port).export()
    assert caught.value.code == "network" and caught.value.transient
    assert real_client(local.server_port).request_push() is False
