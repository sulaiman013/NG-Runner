"""
Tests for the strict Studio fetch and the two-fetch agreement (ngrunner.intake.fetch).

Studio is played by a fake server on 127.0.0.1 that answers only the addresses a test gives it
(404 for the rest, as Studio does for a wrong slug) and logs every address asked for
(tests/helpers_studio.py). Summaries are synthetic (tests/helpers_pdf.py) with made-up names; the
Studio IDs are made up too.
"""
from __future__ import annotations

import inspect
import re
import socket
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import requests

from helpers_pdf import summary_pdf
from helpers_studio import FakeStudio, local_session, running_studio
from ngrunner.intake.fetch import (
    MAX_CANDIDATES,
    FetchTarget,
    Render,
    agree,
    candidate_slugs,
    fetch_render,
)
from ngrunner.intake.summary_check import check_summary, content_fingerprint

# ----- a fake Studio


@pytest.fixture
def studio() -> Iterator[FakeStudio]:
    with running_studio() as fake:
        yield fake


@pytest.fixture
def http() -> Iterator[requests.Session]:
    with local_session() as session:
        yield session


TESTERSON = FetchTarget("4242", "Testerson_101426", "Pat Testerson", "landscape")
LANDSCAPE_PATH = "/install_summary_landscape/testerson/testerson_101426/4242/"


def fetch(studio: FakeStudio, http: requests.Session, target: FetchTarget = TESTERSON, **kwargs: object):
    return fetch_render(target, http=http, base_url=studio.base_url, **kwargs)  # type: ignore[arg-type]


def assert_plain(reason: str, job: str) -> None:
    """A reason a person reads: one line naming the job, no address and no Studio ID (design 4)."""
    assert reason.strip() and "\n" not in reason
    assert job in reason
    assert "http" not in reason and "4242" not in reason and "/" not in reason.replace(job, "")


# ----- finding the report


def test_fetches_the_jobs_summary_from_its_first_derived_address(studio, http):
    pdf = summary_pdf()
    studio.serve(LANDSCAPE_PATH, pdf)
    fetched = fetch(studio, http)
    assert (fetched.ok, fetched.code, fetched.status) == (True, "ok", 200)
    assert fetched.data == pdf
    assert fetched.url == studio.base_url + LANDSCAPE_PATH
    assert studio.asked == [LANDSCAPE_PATH]


def test_a_404_at_every_address_is_not_found_after_asking_each_in_order(studio, http):
    # Assembly Reports' order: client slugs (the name's first word, its camel-case words last to
    # first, the client's last then first word), each with the project slug, then with the
    # project slug split at capitals.
    target = FetchTarget("4242", "VanTesta_101426", "Mockford Dummitt", "landscape")
    fetched = fetch(studio, http, target)
    assert (fetched.ok, fetched.code, fetched.data, fetched.url, fetched.status) == (
        False, "not_found", None, None, 404)
    assert studio.asked == [f"/install_summary_landscape/{client}/{project}/4242/" for client, project in [
        ("vantesta", "vantesta_101426"), ("vantesta", "van_testa_101426"),
        ("testa", "vantesta_101426"), ("testa", "van_testa_101426"),
        ("van", "vantesta_101426"), ("van", "van_testa_101426"),
        ("dummitt", "vantesta_101426"), ("dummitt", "van_testa_101426"),
        ("mockford", "vantesta_101426"), ("mockford", "van_testa_101426")]]
    assert_plain(fetched.reason, "VanTesta_101426")


# The forms below are the shapes of real sold jobs Assembly Reports' order misses: 4 of 60 failed
# without them (measured on Studio, 2026-10-30), each meaning a 24-hour wait and then a card.
@pytest.mark.parametrize(("name", "client", "address"), [
    pytest.param("Mockford_101426", "Pat Testerson- 1234 Fake Street, Sampletown CA, CA 90000",
                 ("testerson", "mockford_101426"), id="the surname sits before a street address"),
    pytest.param("Testerson-Fakeley_101426", "Jo Testerson-Fakeley",
                 ("testerson-fakeley", "testerson-fakeley_101426"), id="Studio keeps a hyphen in both"),
    pytest.param("PatMockford_101426", "Pat Mockford-Dummitt",
                 ("mockford-dummitt", "patmockford_101426"), id="a hyphen only in the client"),
    pytest.param("Testerson_101426", "Pat Sam Testerson Jr",
                 ("sam", "testerson_101426"), id="any word of the client, not just its first and last"),
    pytest.param("Testerson-Fakeley_101426", "Jo Fakeley",
                 ("fakeley", "testerson-fakeley_101426"), id="a plain client with a hyphenated project"),
])
def test_the_address_forms_real_jobs_need_are_asked(studio, http, name, client, address):
    path = f"/install_summary_landscape/{address[0]}/{address[1]}/4242/"
    studio.serve(path, pdf := summary_pdf(project_name=name))
    fetched = fetch(studio, http, FetchTarget("4242", name, client, "landscape"))
    assert fetched.ok and fetched.data == pdf
    assert studio.asked[-1] == path and len(studio.asked) == len(set(studio.asked))


def test_the_usual_address_is_still_the_first_one_asked():
    # 53 of 56 real sold jobs answered at the first address: nothing may come before it.
    assert candidate_slugs("Testerson_101426", "Jo Testerson-Fakeley 1234 Fake St")[0] == (
        "testerson", "testerson_101426")


def test_the_words_of_a_street_address_are_never_asked_as_the_client():
    clients = {client for client, _ in candidate_slugs(
        "Mockford_101426", "Pat Testerson- 1234 Fake Street, Sampletown CA, CA 90000")}
    assert {"pat", "testerson", "mockford"} <= clients
    assert not {"1234", "fake", "street", "sampletown", "ca"} & clients


def test_the_addresses_asked_stay_few_and_safe_whatever_the_names_hold():
    # Wrong addresses 404 in a fraction of a second, but a job that never fetches asks them all,
    # at every retry. Each slug goes into a web address.
    name, client = "VanTesta-Fakeley-Mockford_101426", "Pat Sam Lee Jo Dummitt-Testova-Fakeo Mo Bo"
    pairs = candidate_slugs(name, client)
    assert len(pairs) == MAX_CANDIDATES == 24 and len(set(pairs)) == len(pairs)
    for weird_name, weird_client in [("Testerson_101426", "-- , -"), ("Testerson_101426", "../.. %2f"),
                                     ("Testerson-_101426", "Pat-")]:
        for pair in candidate_slugs(weird_name, weird_client):
            assert all(re.fullmatch(r"[a-z0-9]+(?:[_-]+[a-z0-9]+)*", slug) for slug in pair), pair


def test_a_portrait_target_asks_for_the_portrait_report_only(studio, http):
    portrait = summary_pdf(layout="portrait")
    studio.serve("/install_summary/testerson/testerson_101426/4242/", portrait)
    studio.serve(LANDSCAPE_PATH, summary_pdf(layout="landscape"))
    fetched = fetch(studio, http, replace(TESTERSON, layout="portrait"))
    assert fetched.ok and fetched.data == portrait
    assert studio.asked == ["/install_summary/testerson/testerson_101426/4242/"]


def test_a_known_address_is_tried_first(studio, http):
    # Studio's own slugs can differ from the ones derived from the name (a client slug of the
    # first name, a renamed project): the address that worked last time is asked for first.
    known = "/install_summary_landscape/pat/testerson_rebuild_101426/4242/"
    studio.serve(LANDSCAPE_PATH, summary_pdf(created="D:20261014120000-07'00'"))
    studio.serve(known, pdf := summary_pdf(created="D:20261014120500-07'00'"))
    fetched = fetch(studio, http, replace(TESTERSON, known_url=studio.base_url + known))
    assert fetched.ok and fetched.data == pdf
    assert fetched.url == studio.base_url + known
    assert studio.asked == [known]


def test_a_blank_known_address_is_no_known_address(studio, http):
    studio.serve(LANDSCAPE_PATH, summary_pdf())
    assert fetch(studio, http, replace(TESTERSON, known_url="")).ok
    assert studio.asked == [LANDSCAPE_PATH]


def test_a_known_address_that_is_gone_falls_back_to_the_derived_ones(studio, http):
    studio.serve(LANDSCAPE_PATH, summary_pdf())
    gone = "/install_summary_landscape/pat/testerson_old_101426/4242/"
    fetched = fetch(studio, http, replace(TESTERSON, known_url=studio.base_url + gone))
    assert fetched.ok and fetched.url == studio.base_url + LANDSCAPE_PATH
    assert studio.asked == [gone, LANDSCAPE_PATH]


def test_only_a_known_addresss_slugs_are_used_never_its_host_or_layout(studio, http):
    # Every fetch of a job uses one layout (AGENTS.md, Before filing goes live), and Studio's
    # slugs are the same in both, so a known address is asked for on Studio, in the target's.
    portrait = "/install_summary/pat/testerson_101426/4242/"
    studio.serve(portrait, summary_pdf(layout="portrait"))
    known = "https://elsewhere.example.com/install_summary_landscape/pat/testerson_101426/4242/"
    fetched = fetch(studio, http, replace(TESTERSON, layout="portrait", known_url=known))
    assert fetched.ok and fetched.url == studio.base_url + portrait
    assert studio.asked == [portrait]


@pytest.mark.parametrize("fields", [
    {"studio_id": "42a"}, {"studio_id": ""}, {"studio_id": "../4242"},
    {"layout": "sideways"}, {"name": "  "}, {"name": "__"},
    {"known_url": "https://studio.example.com/install_summary_landscape/pat/testerson_101426/9999/"},
    {"known_url": "https://studio.example.com/project/details/4242/"},
    {"known_url": "https://studio.example.com/install_summary_landscape/../testerson_101426/4242/"},
], ids=["letters in the Studio ID", "no Studio ID", "a path for a Studio ID", "an unknown layout",
        "a blank name", "a name with no letters or digits", "a known address of another Studio ID",
        "a known address that is not a report", "a known address with a slug of dots"])
def test_a_target_that_cannot_name_one_report_is_refused(fields):
    # A programming error, refused at once: a Studio ID goes into the address, a name with no
    # slug can verify nothing, and a known address of another Studio ID is another job's report.
    with pytest.raises(ValueError):
        replace(TESTERSON, **fields)


# ----- strict: only this job's whole PDF is ok


def refused(fetched, code: str, status: int | None = 200, url: str | None = LANDSCAPE_PATH) -> None:
    """A refusal carries no bytes, says why in plain words, and names the address that gave it."""
    assert (fetched.ok, fetched.code, fetched.data, fetched.check, fetched.status) == (
        False, code, None, None, status)
    assert fetched.url is None if url is None else (fetched.url or "").endswith(url)
    assert_plain(fetched.reason, "Testerson_101426")


def test_a_page_that_is_not_a_pdf_is_refused(studio, http):
    studio.serve(LANDSCAPE_PATH, b"<html><body>Studio is down for maintenance</body></html>")
    refused(fetch(studio, http), "not_pdf")


@pytest.mark.parametrize("cut", [
    lambda pdf: {"body": pdf[: len(pdf) // 2], "length": len(pdf)},
    lambda pdf: {"body": pdf[: len(pdf) // 2]},
    lambda pdf: {"body": pdf[: pdf.rindex(b"%%EOF")]},
    lambda pdf: {"body": pdf + b"<html><body>504 Gateway Time-out</body></html>\n"},
], ids=["the connection drops halfway", "a whole download of a file cut in half",
        "everything but the end marker", "an error page glued on after the end marker"])
def test_a_download_that_does_not_end_like_a_whole_pdf_is_truncated(studio, http, cut):
    studio.serve(LANDSCAPE_PATH, **cut(summary_pdf()))
    refused(fetch(studio, http), "truncated")


def test_white_space_after_the_end_marker_is_part_of_a_whole_pdf(studio, http):
    studio.serve(LANDSCAPE_PATH, summary_pdf() + b"\r\n \n")
    assert fetch(studio, http).ok


@pytest.mark.parametrize("length", [-1, None], ids=["its Content-Length says so", "it has no Content-Length"])
def test_a_file_larger_than_the_cap_is_too_big(studio, http, length):
    pdf = summary_pdf()
    studio.serve(LANDSCAPE_PATH, pdf, length=length)
    refused(fetch(studio, http, max_bytes=len(pdf) - 1), "too_big")
    assert fetch(studio, http, max_bytes=len(pdf)).ok


@pytest.mark.parametrize("project_name", [None, ""],
                         ids=["no PROJECT NAME", "PROJECT NAME with nothing beside it"])
def test_a_report_without_its_project_name_is_refused_never_accepted(studio, http, project_name):
    # Assembly Reports accepts a report whose page 1 has no PROJECT NAME. Nothing then says
    # whose report it is, so NG Runner refuses it (design 3.5).
    studio.serve(LANDSCAPE_PATH, summary_pdf(project_name=project_name))
    refused(fetch(studio, http), "no_project_name")


@pytest.mark.parametrize("other", ["Fakeley_101426", "Testerson_101426_2", "Testerson_101526"],
                         ids=["another family", "another sale of the family", "another date"])
def test_a_report_with_another_jobs_project_name_is_refused(studio, http, other):
    studio.serve(LANDSCAPE_PATH, summary_pdf(project_name=other))
    fetched = fetch(studio, http)
    refused(fetched, "wrong_job")
    assert other in fetched.reason


def test_a_project_name_matches_whatever_its_case_and_separators(studio, http):
    studio.serve(LANDSCAPE_PATH, summary_pdf(project_name="TESTERSON 101426"))
    assert fetch(studio, http).ok


def test_a_pdf_that_cannot_be_read_is_refused_as_not_a_pdf(studio, http):
    studio.serve(LANDSCAPE_PATH, b"%PDF-1.4\nthis is not really a PDF\n%%EOF\n")
    refused(fetch(studio, http), "not_pdf")


# ----- Studio failing


@pytest.mark.parametrize("status", [500, 503, 403, 204])
def test_an_answer_other_than_the_report_or_404_is_an_http_error(studio, http, status):
    studio.serve(LANDSCAPE_PATH, b"" if status == 204 else b"<html><body>Server Error</body></html>",
                 status=status)
    refused(fetch(studio, http), "http_error", status=status)


def test_studio_answering_too_slowly_is_a_timeout_and_ends_the_search(studio, http):
    # Studio renders the PDF before it answers. A wrong address 404s at once, so a slow one is
    # likely the report itself: the other addresses would only take longer.
    studio.serve(LANDSCAPE_PATH, summary_pdf(), delay=1.5)
    started = time.monotonic()
    fetched = fetch(studio, http, timeout_s=0.3)
    assert time.monotonic() - started < 1.2
    refused(fetched, "timeout", status=None)
    assert studio.asked == [LANDSCAPE_PATH]


def test_a_report_still_arriving_when_time_is_up_is_cut_off_as_a_timeout(studio, http):
    # 1 KB every 0.05 s never trips the wait for the next bytes, but the whole 200 KB would take
    # 10 s: the fetch stops soon after its time is up instead of reading on to the end.
    studio.serve(LANDSCAPE_PATH, b"%PDF-1.4\n%" + b"x" * 200_000, drip=0.05, piece=1000)
    started = time.monotonic()
    refused(fetch(studio, http, timeout_s=0.3), "timeout")
    assert time.monotonic() - started < 2


def test_a_report_that_stalls_mid_file_is_a_timeout(studio, http):
    # requests reports a wait that runs out mid-file as a broken connection, not as a timeout.
    studio.serve(LANDSCAPE_PATH, summary_pdf(), drip=0.6)
    refused(fetch(studio, http, timeout_s=0.3), "timeout")
    assert studio.asked == [LANDSCAPE_PATH]


class Logged:
    """The real session, with every address it is asked for logged (for a Studio that is gone)."""

    def __init__(self, session: requests.Session) -> None:
        self.session, self.urls = session, []

    def get(self, url: str, **kwargs: object) -> requests.Response:
        self.urls.append(url)
        return self.session.get(url, **kwargs)  # type: ignore[arg-type]


def test_studio_out_of_reach_is_a_network_failure_and_ends_the_search(http):
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    logged = Logged(http)
    fetched = fetch_render(TESTERSON, http=logged, base_url=f"http://127.0.0.1:{port}")
    refused(fetched, "network", status=None)
    assert len(logged.urls) == 1


# ----- the search as a whole

PAT_PATH = "/install_summary_landscape/pat/testerson_101426/4242/"  # the second derived address


def test_a_failure_at_one_address_moves_on_and_a_later_one_can_still_give_the_report(studio, http):
    studio.serve(LANDSCAPE_PATH, b"<html><body>Please sign in</body></html>")
    studio.serve(PAT_PATH, pdf := summary_pdf())
    fetched = fetch(studio, http)
    assert fetched.ok and fetched.data == pdf
    assert studio.asked == [LANDSCAPE_PATH, PAT_PATH]


def test_when_no_address_gives_the_report_the_most_telling_failure_is_reported(studio, http):
    # Another job's whole report says more than an error page does.
    studio.serve(LANDSCAPE_PATH, b"<html><body>Server Error</body></html>", status=500)
    studio.serve(PAT_PATH, summary_pdf(project_name="Fakeley_101426"))
    refused(fetch(studio, http), "wrong_job", url=PAT_PATH)


def test_an_early_render_is_fetched_with_the_check_that_says_it_is_not_complete(studio, http):
    # Fetching is not checking: an early render is this job's whole PDF, so it is fetched, and
    # its check says what is missing, for the caller to fetch it again later.
    two = (("Testerson - Primary Closet", "His and Her Master"), ("Testerson - Garage", "Garage"))
    studio.serve(LANDSCAPE_PATH, summary_pdf(designs=two, finished=1))
    fetched = fetch(studio, http)
    assert fetched.ok and fetched.check is not None
    assert (fetched.check.ok, fetched.check.details["codes"]) == (False, ["no_wall_area", "no_elevations"])


def test_studio_is_asked_as_assembly_reports_asks_it(studio, http):
    studio.serve(LANDSCAPE_PATH, summary_pdf())
    fetch(studio, http)
    assert studio.agents[0].startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    assert "NGRunner" in studio.agents[0]


def test_without_a_session_it_makes_its_own(studio, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")  # the default session reads this PC's proxy settings
    studio.serve(LANDSCAPE_PATH, summary_pdf())
    assert fetch_render(TESTERSON, base_url=studio.base_url).ok


@pytest.mark.fixtures
def test_every_archived_summary_is_fetched_as_its_own_jobs_and_refused_as_another_jobs(
        summaries_dir, studio, http):
    # The strict name check against real PROJECT NAMEs: the job name is the one Jim filed it
    # under (<Job>[_Gar]_summary.pdf). Files are named by index only.
    paths = sorted((summaries_dir / "ngjobs").glob("*.pdf"), key=lambda p: p.name.lower())
    if not paths:
        pytest.skip("no archived NG Jobs summaries in the fixtures")
    on_studio = "/install_summary_landscape/client/project/4242/"
    refused_files = []
    for i, path in enumerate(paths):
        studio.serve(on_studio, path.read_bytes())
        job = path.stem.removesuffix("_summary").removesuffix("_Gar")
        target = FetchTarget("4242", job, None, "landscape", known_url=studio.base_url + on_studio)
        if not fetch(studio, http, target).ok:
            refused_files.append(f"n{i:02d}")
    assert refused_files == []
    studio.serve(on_studio, paths[0].read_bytes())
    other = FetchTarget("4242", "Fakeley_101426", None, "landscape", known_url=studio.base_url + on_studio)
    assert fetch(studio, http, other).code == "wrong_job"


# ----- two fetches must agree before anything is filed (AGENTS.md, Before filing goes live)

T0 = datetime(2026, 10, 14, 22, 5, tzinfo=UTC)  # 3:05 PM in Los Angeles
TWO_DESIGNS = (("Testerson - Primary Closet", "His and Her Master"), ("Testerson - Garage", "Garage"))


def render(minutes: float, **summary: object) -> Render:
    """A render fetched `minutes` after T0; its CreationDate differs with the time, as Studio's does."""
    at = T0 + timedelta(minutes=minutes)
    summary.setdefault("created", at.strftime("D:%Y%m%d%H%M%S+00'00'"))
    layout = str(summary.get("layout", "landscape"))
    return Render(summary_pdf(**summary), at, layout)  # type: ignore[arg-type]


def test_two_fetches_ten_minutes_apart_with_the_same_text_agree():
    first, second = render(0), render(10)
    assert first.source != second.source  # other bytes: CreationDate
    agreement = agree(first, second)
    assert (agreement.ok, agreement.code) == (True, "agree")
    assert agreement.fingerprint == content_fingerprint(first.source)
    assert agreement.reason == "Two fetches 10 minutes apart hold the same IC summary."


def disagree(agreement, code: str) -> None:
    assert (agreement.ok, agreement.code, agreement.fingerprint) == (False, code, None)
    assert agreement.reason.strip() and "\n" not in agreement.reason


def test_a_partial_render_complete_in_itself_and_the_finished_one_differ():
    # Risk K8: Studio's unfinished render can pass the check on its own (one design of two,
    # its pages numbered out of its own count); only a second fetch shows it was not finished.
    partial, finished = render(0), render(10, designs=TWO_DESIGNS)
    assert check_summary(partial.source).ok and check_summary(finished.source).ok
    disagree(agree(partial, finished), "differ")


def test_an_early_incomplete_render_never_agrees_and_the_complete_pair_after_it_does():
    early = render(0, designs=TWO_DESIGNS, finished=1)  # both designs listed, one drawn so far
    complete, again = render(30, designs=TWO_DESIGNS), render(40, designs=TWO_DESIGNS)
    agreement = agree(early, complete)
    disagree(agreement, "first_incomplete")
    assert '"Testerson - Garage"' in agreement.reason  # what the check found missing
    assert agree(complete, again).code == "agree"


def test_a_second_fetch_that_is_incomplete_does_not_agree():
    complete, early = render(0, designs=TWO_DESIGNS), render(10, designs=TWO_DESIGNS, finished=1)
    disagree(agree(complete, early), "second_incomplete")


def test_fetches_in_different_layouts_are_never_compared():
    # Each layout prints the same summary as other text, so their fingerprints never agree; the
    # rule is one layout per job (AGENTS.md, Before filing goes live, item 2).
    disagree(agree(render(0, layout="landscape"), render(10, layout="portrait")), "layouts_differ")


def test_fetches_whose_pages_are_not_in_the_layout_asked_for_are_never_compared():
    # Both asked for landscape, both sent portrait pages: the same text, but not the layout the
    # job is filed in.
    first = Render(summary_pdf(layout="portrait", created="D:20261014220500+00'00'"), T0, "landscape")
    second = Render(summary_pdf(layout="portrait", created="D:20261014221500+00'00'"),
                    T0 + timedelta(minutes=10), "landscape")
    agreement = agree(first, second)
    disagree(agreement, "layouts_differ")
    assert "portrait" in agreement.reason and "landscape" in agreement.reason


@pytest.mark.parametrize("minutes", [9.9, 0, -10],
                         ids=["9.9 minutes apart", "one fetch twice", "the second before the first"])
def test_fetches_less_than_ten_minutes_apart_are_too_soon_though_they_hold_the_same(minutes):
    # The same text in other bytes, as Studio renders it afresh: identical bytes are same_bytes.
    second = render(minutes, created="D:20261014150000+00'00'")
    disagree(agree(render(0), second), "too_soon")


def test_one_file_served_twice_is_same_bytes_however_far_apart():
    # All 6 real pairs differ in bytes, so identical bytes prove nothing about a stable render.
    pdf = summary_pdf()
    later = T0 + timedelta(minutes=10)
    disagree(agree(Render(pdf, T0, "landscape"), Render(pdf, later, "landscape")), "same_bytes")


def test_same_bytes_comes_after_the_incomplete_codes_and_before_layouts_differ():
    early = summary_pdf(designs=TWO_DESIGNS, finished=1)
    later = T0 + timedelta(minutes=10)
    first = Render(early, T0, "landscape")
    assert agree(first, Render(early, later, "landscape")).code == "first_incomplete"
    pdf = summary_pdf()
    assert agree(Render(pdf, T0, "landscape"), Render(pdf, later, "portrait")).code == "same_bytes"


def test_a_fetch_waits_four_minutes_by_default_as_real_renders_take_up_to_95_seconds():
    assert inspect.signature(fetch_render).parameters["timeout_s"].default == 240


def test_a_wrong_job_carries_the_name_page_1_prints_and_no_other_refusal_does(studio, http):
    studio.serve(LANDSCAPE_PATH, summary_pdf(project_name="Fakeley 101426"))
    fetched = fetch(studio, http)
    assert (fetched.code, fetched.found_name) == ("wrong_job", "Fakeley 101426")
    studio.serve(LANDSCAPE_PATH, b"<html>no</html>")
    assert fetch(studio, http).found_name is None
    studio.serve(LANDSCAPE_PATH, summary_pdf())
    assert fetch(studio, http).found_name is None


def test_ten_minutes_apart_is_enough_and_the_gap_can_be_longer():
    assert agree(render(0), render(10)).ok
    assert agree(render(0), render(10), min_gap=timedelta(minutes=30)).code == "too_soon"
    assert agree(render(0), render(30), min_gap=timedelta(minutes=30)).ok


def test_what_the_content_says_comes_before_the_gap():
    # The code says what to do next (integration spec 5): an incomplete fetch is fetched again,
    # and on "differ" the second becomes the new first. Waiting longer would change neither.
    assert agree(render(0), render(3, designs=TWO_DESIGNS, finished=1)).code == "second_incomplete"
    assert agree(render(0), render(3, designs=TWO_DESIGNS)).code == "differ"


def test_a_fetch_cached_to_a_file_compares_as_its_bytes_do(tmp_path):
    first, second = render(0), render(10, designs=TWO_DESIGNS)
    cached = tmp_path / "first.pdf"
    cached.write_bytes(first.source)  # type: ignore[arg-type]
    assert agree(replace(first, source=cached), render(10)).code == "agree"
    assert agree(replace(first, source=str(cached)), second).code == "differ"


@pytest.mark.parametrize("bad", [
    lambda: Render(b"%PDF-1.4", datetime(2026, 10, 14, 15, 5), "landscape"),  # noqa: DTZ001 - the point
    lambda: Render(b"%PDF-1.4", T0, "sideways"),  # type: ignore[arg-type]
    lambda: agree(render(0), render(10), min_gap=timedelta(0)),
], ids=["a time with no time zone", "an unknown layout", "no gap at all"])
def test_a_comparison_that_could_not_mean_anything_is_refused(bad):
    with pytest.raises(ValueError):
        bad()


@pytest.mark.parametrize("junk", [b"<html><body>Not found</body></html>", b"%PDF-1.4\nnot really\n%%EOF\n"],
                         ids=["a web page", "a damaged PDF"])
def test_a_fetch_that_cannot_be_read_is_incomplete_never_an_error(junk):
    disagree(agree(Render(junk, T0, "landscape"), render(10)), "first_incomplete")
    disagree(agree(render(0), Render(junk, T0 + timedelta(minutes=10), "landscape")), "second_incomplete")
