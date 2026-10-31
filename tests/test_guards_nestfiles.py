"""
What a job's folder in Mozaik Shared\\Jobs holds, and what is kept between passes (guards/nestfiles.py).
The day simulation (test_e2e_day.py, scenario 60 on) shows these at work. Here are the rules a scenario
can't state as plainly. Nothing here has met a real file: BUILD 546 and build spec section 7 give the
layout, and every name is made up.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner.clock import to_iso
from ngrunner.guards.nestfiles import (
    JobFiles, NestListed, NestRead, in_ng_spelling, is_settled, list_jobs, nest_moved_since_read,
    nests_due, next_said, read_nest, rooms_moved,
)
from ngrunner.guards.opt import OPT_SETTLES_AFTER
from ngrunner.guards.reads import Listed, stat_words
from ngrunner.messages import templates

NOW = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(minutes=10)
JOB, MATERIAL = "Testerson_101426", "3-4 Classic White"
ONE = Listed(10, LONG_AGO)
OTHER = Listed(11, LONG_AGO)
PLACE = (JOB, MATERIAL)


def job(*, opts=(MATERIAL,), listed: Listed = ONE, rooms=None, locks=None) -> JobFiles:
    return JobFiles(path=Path(r"C:\made-up") / JOB,
                    opts={name: NestListed(Path(r"C:\made-up") / JOB / f"{name}.opt", listed)
                          for name in opts},
                    rooms=rooms or {}, locks=locks or {})


def said(listed: Listed = ONE, *, since: datetime = LONG_AGO, read: Listed | None = ONE, **more) -> dict:
    return {"opts": {MATERIAL: {"seen": stat_words(listed), "since": to_iso(since),
                                "read": None if read is None else stat_words(read),
                                "install": False, **more}}}


# ----- listing


def test_a_listing_takes_nests_rooms_and_locks_and_nothing_else(tmp_path: Path) -> None:
    folder = tmp_path / JOB
    folder.mkdir()
    for name in ("3-4 Classic White.opt", "1-4 Other.OPT", "Room0.des", "Room12.DES", "Room1.bak", "Room1.sbk",
                 "MozaikJob.UNS", "MozaikJob.LCK", "OptimizeJob.LCK", "JobDat.dat", "Roomy.des"):
        (folder / name).write_bytes(b"x")
    (folder / "Sub folder.opt").mkdir()

    jobs, unlisted = list_jobs(tmp_path)

    found = jobs[JOB]
    assert set(found.opts) == {"3-4 Classic White", "1-4 Other"}
    assert set(found.rooms) == {"Room0.des", "Room12.DES"}
    assert set(found.locks) == {"MozaikJob.UNS", "MozaikJob.LCK", "OptimizeJob.LCK"}
    assert unlisted == set()


def test_old_isnt_a_job_and_a_folder_that_cant_be_listed_says_nothing(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Old").mkdir()
    (tmp_path / JOB).mkdir()
    (tmp_path / "Fakeley_102026").mkdir()
    real = list_jobs.__globals__["_list_job"]

    def refuse(path: Path) -> JobFiles:
        if path.name == "Fakeley_102026":
            raise PermissionError("no")
        return real(path)

    monkeypatch.setitem(list_jobs.__globals__, "_list_job", refuse)

    jobs, unlisted = list_jobs(tmp_path)

    assert set(jobs) == {JOB} and unlisted == {"Fakeley_102026"}


def test_a_root_that_cant_be_listed_raises_so_nothing_is_concluded(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        list_jobs(tmp_path / "none here")


def test_a_nest_lands_on_its_programs_row_whatever_the_case(tmp_path: Path) -> None:
    jobs = {"TESTERSON_101426": job(opts=("3-4 CLASSIC WHITE", "1-4 Only Here"))}

    spelled, unlisted = in_ng_spelling(jobs, {"FAKELEY_102026"},
                                       {JOB: {MATERIAL: {}}, "Fakeley_102026": {}})

    assert set(spelled[JOB].opts) == {MATERIAL, "1-4 Only Here"}
    assert unlisted == {"Fakeley_102026"}


# ----- what has settled


@pytest.mark.parametrize("since, settled", [
    (NOW - OPT_SETTLES_AFTER, True),
    (NOW - OPT_SETTLES_AFTER + timedelta(seconds=1), False),
])
def test_a_nest_settles_after_two_minutes_of_listings_showing_the_same_file(since, settled) -> None:
    assert is_settled({"seen": stat_words(ONE), "since": to_iso(since)}, ONE, NOW) is settled


def test_a_nest_never_seen_or_seen_otherwise_hasnt_settled() -> None:
    assert is_settled(None, ONE, NOW) is False
    assert is_settled({"seen": stat_words(OTHER), "since": to_iso(LONG_AGO)}, ONE, NOW) is False


def test_a_read_of_a_nest_not_settled_reads_nothing_and_says_so() -> None:
    def never(_: Path) -> bytes:
        raise AssertionError("it must not be read")

    read = read_nest(NestListed(Path(r"C:\made-up.opt"), ONE), job(), None, NOW, read=never)

    assert (read.opt, read.problem, read.retry) == (None, templates.NEST_SETTLING, True)


def test_a_room_that_cant_be_read_can_never_be_part_of_a_pass(tmp_path: Path) -> None:
    from helpers_opt import NESTED
    from ngrunner.guards.reads import Unreadable

    nest = tmp_path / f"{MATERIAL}.opt"
    nest.write_bytes(NESTED)
    files = JobFiles(path=tmp_path, opts={MATERIAL: NestListed(nest, ONE)}, rooms={"Room1.des": ONE})

    def read(path: Path) -> bytes:
        if path.name == "Room1.des":
            raise Unreadable("Room1.des could not be read.")
        return path.read_bytes()

    got = read_nest(files.opts[MATERIAL], files, said()["opts"][MATERIAL], NOW, read=read)

    assert got.opt is not None and got.rooms == ()


# ----- what is due


def test_a_nest_settled_and_not_yet_read_is_due() -> None:
    assert nests_due({JOB: said(read=None)}, {JOB: job()}, set(), frozenset(), NOW) == {PLACE}


def test_a_nest_read_as_it_is_now_is_not_due() -> None:
    assert nests_due({JOB: said()}, {JOB: job()}, set(), frozenset(), NOW) == set()


def test_a_nest_still_settling_is_not_due() -> None:
    kept = said(since=NOW - timedelta(seconds=30), read=None)

    assert nests_due({JOB: kept}, {JOB: job()}, set(), frozenset(), NOW) == set()


def test_a_folder_that_couldnt_be_listed_is_not_due_for_anything() -> None:
    kept = said(missing=1)

    assert nests_due({JOB: kept}, {}, {JOB}, frozenset({PLACE}), NOW) == set()


def test_a_nest_gone_is_due_only_for_a_material_whose_verdict_counts_and_only_once() -> None:
    assert nests_due({JOB: said(missing=1)}, {}, set(), frozenset(), NOW) == set()
    assert nests_due({JOB: said(missing=1)}, {}, set(), frozenset({PLACE}), NOW) == {PLACE}
    assert nests_due({JOB: said(missing=2)}, {}, set(), frozenset({PLACE}), NOW) == set()


def test_a_nest_back_from_being_gone_is_due_only_for_one_whose_verdict_counts() -> None:
    assert nests_due({JOB: said(missing=2)}, {JOB: job()}, set(), frozenset(), NOW) == set()
    assert nests_due({JOB: said(missing=2)}, {JOB: job()}, set(), frozenset({PLACE}), NOW) == {PLACE}


def test_rooms_moved_only_counts_for_a_material_whose_verdict_counts_and_once_settled() -> None:
    files = job(rooms={"Room1.des": ONE})
    stamp = files.rooms_stamp()
    moved = {**said(), "rooms": {"seen": stamp, "since": to_iso(LONG_AGO), "read": "before"}}
    moving = {**said(), "rooms": {"seen": stamp, "since": to_iso(NOW), "read": "before"}}

    assert rooms_moved(moved, files, NOW) is True and rooms_moved(moving, files, NOW) is False
    assert nests_due({JOB: moved}, {JOB: files}, set(), frozenset(), NOW) == set()
    assert nests_due({JOB: moved}, {JOB: files}, set(), frozenset({PLACE}), NOW) == {PLACE}
    assert nests_due({JOB: moving}, {JOB: files}, set(), frozenset({PLACE}), NOW) == set()


# ----- whether a pass may stand


@pytest.mark.parametrize("files, kept, moved", [
    (None, said(), True),
    (job(opts=()), said(), True),
    (job(listed=OTHER), said(), True),
], ids=["the folder gone", "the nest gone", "the nest changed"])
def test_a_pass_falls_back_on_what_a_listing_shows(files, kept, moved) -> None:
    assert nest_moved_since_read(kept, files, MATERIAL) is moved


def test_a_pass_stands_only_while_the_nest_and_rooms_are_as_read() -> None:
    files = job(rooms={"Room1.des": ONE})
    stamp = files.rooms_stamp()
    as_read = {**said(), "rooms": {"seen": stamp, "read": stamp}}

    assert nest_moved_since_read(as_read, files, MATERIAL) is False
    assert nest_moved_since_read({**as_read, "rooms": {"seen": stamp, "read": "before"}}, files, MATERIAL) is True
    assert nest_moved_since_read(said(), files, MATERIAL) is True          # no rooms on record: no pass


# ----- what is kept


def test_the_first_listing_marks_all_of_it_as_from_before_install() -> None:
    kept = next_said(None, {JOB: job(rooms={"Room1.des": ONE})}, set(), {}, {}, NOW)

    one = kept[JOB]["opts"][MATERIAL]
    assert one["install"] is True and one["read"] == stat_words(ONE)
    assert kept[JOB]["rooms"]["read"] == kept[JOB]["rooms"]["seen"]


def test_a_nest_seen_after_that_is_new_and_starts_its_two_minutes() -> None:
    kept = next_said({}, {JOB: job()}, set(), {}, {}, NOW)

    one = kept[JOB]["opts"][MATERIAL]
    assert (one["install"], one["read"], one["since"]) == (False, None, to_iso(NOW))


def test_a_change_restarts_its_two_minutes_and_ends_being_from_before_install() -> None:
    before = {JOB: said(install=True)}
    before[JOB]["opts"][MATERIAL]["install"] = True

    kept = next_said(before, {JOB: job(listed=OTHER)}, set(), {}, {}, NOW)

    one = kept[JOB]["opts"][MATERIAL]
    assert (one["install"], one["since"], one["seen"]) == (False, to_iso(NOW), stat_words(OTHER))


def test_a_read_that_will_be_tried_again_isnt_marked_read() -> None:
    before = {JOB: said(read=None)}
    reads = {PLACE: NestRead(ONE, problem=templates.NEST_UNREADABLE, retry=True)}

    assert next_said(before, {JOB: job()}, set(), reads, {}, NOW)[JOB]["opts"][MATERIAL]["read"] is None


def test_a_read_of_bytes_that_wont_parse_is_marked_read_until_they_change() -> None:
    before = {JOB: said(read=None)}
    reads = {PLACE: NestRead(ONE, problem=templates.NEST_UNREADABLE, retry=False)}

    assert next_said(before, {JOB: job()}, set(), reads, {}, NOW)[JOB]["opts"][MATERIAL]["read"] == stat_words(ONE)


def test_a_nest_gone_is_kept_as_it_was_and_back_unchanged_is_what_it_was() -> None:
    before = {JOB: said(install=True)}
    before[JOB]["opts"][MATERIAL]["install"] = True

    once = next_said(before, {JOB: job(opts=())}, set(), {}, {}, NOW)
    twice = next_said(once, {}, set(), {}, {}, NOW)
    many = next_said(twice, {}, set(), {}, {}, NOW)
    back = next_said(many, {JOB: job()}, set(), {}, {}, NOW)

    assert once[JOB]["opts"][MATERIAL]["missing"] == 1
    assert twice[JOB]["opts"][MATERIAL]["missing"] == 2
    assert many[JOB]["opts"][MATERIAL]["missing"] == 2
    assert back[JOB]["opts"][MATERIAL] == before[JOB]["opts"][MATERIAL]


def test_a_folder_that_couldnt_be_listed_keeps_all_it_had() -> None:
    before = {JOB: said()}

    assert next_said(before, {}, {JOB}, {}, {}, NOW) == before
