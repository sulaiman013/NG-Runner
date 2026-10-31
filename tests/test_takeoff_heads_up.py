"""
The order heads-up's source (guards/takeoffs.py), now that NG Runner makes its own takeoff on
intake: a complete takeoff of NG Runner's is the source; the chat's IC Conversion JSON is only
when NG Runner has none (a garage, a summary it couldn't read, a job from before); and a takeoff
that isn't complete says "could not verify the takeoff" and how many lines, never a partial list.
What P1b proved still holds: one message per content, "revised" on a change, nothing to Barbara or
Will until order_flags_on, one copy in shadow mode, and no client detail ever. Made-up names only.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from helpers_pdf import summary_pdf
from test_guards_headsup import takeoff_json
from ngrunner.clock import FakeClock
from ngrunner.config import Settings
from ngrunner.convert import convert, rules
from ngrunner.guards import headsup
from ngrunner.guards.kept import TAKEOFFS
from ngrunner.guards.reads import Listed
from ngrunner.guards.scan import Plan, Scan, TakeoffRead
from ngrunner.guards.takeoffs import UNREADABLE_CODE, take_takeoffs
from ngrunner.intake import pipeline
from ngrunner.store import (
    Store, add_takeoff, ensure_job, get_heads_up, get_state, list_dev, list_pending, open_store,
    record_heads_up, set_state,
)

Z = timezone.utc
NOW = datetime(2026, 10, 31, 17, 0, tzinfo=Z)          # a Saturday morning, 10 AM Pacific
SID, JOB = "4242", "Testerson_101426"
SHA_1, SHA_2 = "1" * 64, "2" * 64
WH = {"colour": "WH", "thk_in": 0.75, "parts": 8, "sqft": 30.33, "sheet_in": [49.0, 97.0],
      "sheets_with_waste": 2, "calc_sheets_32": 0.9478, "tape_lf": 26.0}
AP = {"colour": "AP", "thk_in": 0.75, "parts": 4, "sqft": 9.33, "sheet_in": [49.0, 97.0],
      "sheets_with_waste": 1, "calc_sheets_32": 0.2916, "tape_lf": 12.4}
ADDRESS, EMAIL = "12 Fakeview Lane", "tester@example.com"
NEVER = ("Fakeview", "example.com", "Testville", "555")


def result(*buckets: dict[str, Any], total: float | None = 7999.05) -> dict[str, Any]:
    buckets = buckets or (WH,)
    return {"buckets": list(buckets), "board_parts": sum(b["parts"] for b in buckets),
            "tape_lf": sum(b["tape_lf"] for b in buckets), "estimate_total": total,
            "gate_pulled_equals_designs": True, "unplaced": []}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    made = open_store(tmp_path / "ngrunner.db")
    ensure_job(made, SID, JOB, now=NOW)
    return made


def ctx_of(store: Store, tmp_path: Path, *, mode: str = "live", on: bool = True) -> Any:
    settings = Settings(ng_jobs=tmp_path / "NG Jobs", mozaik_shared=tmp_path / "Mozaik Shared",
                        control=tmp_path / "control", mode=mode, order_flags_on=on)
    return SimpleNamespace(store=store, settings=settings, mode=mode, clock=FakeClock(NOW))


def filed(store: Store, sha: str = SHA_1, folder: str = JOB) -> None:
    """The job as the pipeline keeps it once filed (pipeline.filed_facts reads this key)."""
    set_state(store, f"job.filed.{SID}", json.dumps({"folder": folder, "sha256": sha,
                                                     "where": "ng_jobs", "wrote": True}))


def ours(store: Store, sha: str = SHA_1, *, state: str = "made", complete: bool = True,
         could_not_verify: int = 0, takeoff: dict[str, Any] | None = None,
         at: datetime = NOW, branch: str | None = None) -> None:
    filed(store, sha)
    made = state == "made"
    add_takeoff(store, studio_id=SID, render_sha256=sha, rules_sha256=rules.fingerprint(),
                state=state, branch=branch or ("closet" if made else "garage"),
                complete=complete and made, could_not_verify=could_not_verify,
                result=(takeoff or result()) if made else None, at=at)


def chat_json(**changes: Any) -> TakeoffRead:
    data = json.dumps(takeoff_json(**changes)).encode("utf-8")
    return TakeoffRead(JOB, Listed(len(data), NOW), hashlib.sha256(data).hexdigest(),
                       headsup.read_takeoff(data))


def run(ctx: Any, *reads: TakeoffRead) -> None:
    plan = Plan(ng_jobs=ctx.settings.ng_jobs,
                takeoffs=json.loads(get_state(ctx.store, TAKEOFFS) or "{}"))
    found = Scan(takeoffs_listed={read.folder: read.listed for read in reads},
                 takeoffs=list(reads))
    take_takeoffs(ctx, plan, found, NOW)


def told(store: Store) -> list[dict[str, Any]]:
    """Every message queued, as the pipeline keeps it until it is sent."""
    return [json.loads(get_state(store, f"tell.{op.id}"))
            for op in list_pending(store) if op.kind.startswith("tell_")]


def said(message: dict[str, Any]) -> str:
    return "\n".join((message["title"], *message["lines"]))


# ----- NG Runner's complete takeoff is the source


def test_a_complete_takeoff_of_ng_runners_goes_to_barbara_and_will_once(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store)

    run(ctx)
    run(ctx)

    messages = told(store)
    assert sorted(m["to"] for m in messages) == ["barbara", "will"]
    message = messages[0]
    assert message["kind"] == "heads_up" and "revised" not in message["title"]
    assert message["title"] == f"{JOB}: order heads-up (takeoff estimate; the nest decides)"
    assert f"{JOB}: 2 sheets 3/4 Egger Classic White (takeoff estimate; the nest decides)." in (
        said(message))
    assert "NG Runner's own takeoff of the IC summary" in message["lines"][0]
    assert get_heads_up(store, JOB).sent_at == NOW


def test_its_flags_are_p1bs_own(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, takeoff=result(WH, AP, total=26000.0))

    run(ctx)

    text = said(told(store)[0])
    assert f"{JOB}: Not all Classic White: Tafisa (IC AP)." in text
    assert "Estimate total $26,000.00 is at or above $25,000" in text


def test_a_changed_takeoff_from_a_changed_summary_says_revised(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store)
    run(ctx)
    ours(store, SHA_2, takeoff=result(WH, AP), at=NOW + timedelta(days=1))

    run(ctx)

    revised = [m for m in told(store) if "revised" in m["title"]]
    assert sorted(m["to"] for m in revised) == ["barbara", "will"]
    assert "Tafisa (IC AP)" in said(revised[0])


def test_a_new_summary_whose_takeoff_reads_the_same_sends_nothing(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store)
    run(ctx)
    ours(store, SHA_2, at=NOW + timedelta(days=1))           # rod numbers moved: same takeoff

    run(ctx)

    assert len(told(store)) == 2


# ----- a takeoff that isn't complete: never a partial list


def test_a_takeoff_that_isnt_complete_says_could_not_verify_and_how_many_lines(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, complete=False, could_not_verify=3)

    run(ctx)

    message = told(store)[0]
    assert "could not verify the takeoff" in message["title"]
    assert "could not verify 3 lines" in said(message)
    assert "sheet" not in said(message) and "tape" not in said(message)


def test_the_unplaced_rows_text_never_reaches_a_message(store, tmp_path):
    """A real takeoff from a made-up summary whose every row, placed or not, quotes an address
    and an email: the message holds counts, codes and fixed words only."""
    ctx = ctx_of(store, tmp_path)
    data = summary_pdf(parts=(("VT-WH", 2, f"VT 14x84x3/4 FD WH {ADDRESS} {EMAIL}"),
                              ("CK", 2, f"CK DBack 3.6x22.2x0.6 NN WH {ADDRESS} Testville"),
                              ("XQ-1", 1, f"XQ 3x9x3/4 {ADDRESS} {EMAIL}")))
    takeoff = convert(data, branch="closet")
    assert len(takeoff.unplaced) == 2
    ours(store, complete=False, could_not_verify=len(takeoff.could_not_verify),
         takeoff=pipeline._takeoff_result(takeoff))

    run(ctx)

    text = "\n".join(said(m) for m in told(store))
    assert [word for word in NEVER if word in text] == []
    assert "kind the rules" not in text and "0.6" not in text and "XQ" not in text
    assert "could not verify 2 lines" in text


# ----- no takeoff of NG Runner's: the chat's JSON, as in P1b


def test_with_no_takeoff_of_ng_runners_the_chats_json_is_the_source(store, tmp_path):
    ctx = ctx_of(store, tmp_path)

    run(ctx, chat_json())

    messages = told(store)
    assert len(messages) == 2 and "NG Runner's own" not in said(messages[0])
    assert "Counted as sheets with 10% waste" in said(messages[0])
    assert messages[0]["lines"][0].endswith("From the chat's takeoff.")     # it says its source


@pytest.mark.parametrize("state", ["in_chat", "failed"])
def test_a_job_whose_takeoff_stays_in_chat_or_failed_uses_the_chats_json(store, tmp_path, state):
    ctx = ctx_of(store, tmp_path)
    ours(store, state=state, branch="garage" if state == "in_chat" else "")

    run(ctx, chat_json())

    assert len(told(store)) == 2 and "NG Runner's own" not in said(told(store)[0])


def test_a_takeoff_of_a_summary_no_longer_filed_is_not_the_source(store, tmp_path):
    """The summary was replaced by hand: NG Runner's takeoff is of another file, so the chat's
    takeoff is the source again."""
    ctx = ctx_of(store, tmp_path)
    ours(store)
    filed(store, SHA_2)

    run(ctx, chat_json())

    assert len(told(store)) == 2 and "NG Runner's own" not in said(told(store)[0])


def test_with_a_takeoff_of_ng_runners_the_chats_json_is_noted_and_never_sent(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store)

    run(ctx, chat_json(), )
    run(ctx, chat_json(estimate={"total": 30000.0}))

    assert {"NG Runner's own" in said(m) for m in told(store)} == {True}
    assert len(told(store)) == 2
    kept = json.loads(get_state(store, TAKEOFFS))[JOB]
    assert kept["read"] and kept["bad"] is False


def test_the_chats_json_that_wont_read_says_nothing_once_ng_runner_has_a_takeoff(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    bad = TakeoffRead(JOB, Listed(3, NOW), "3" * 64, None)
    run(ctx, bad)
    assert json.loads(get_state(store, TAKEOFFS))[JOB]["bad"] is True
    assert [d.code for d in list_dev(store)] == [UNREADABLE_CODE]

    ours(store)
    run(ctx)

    assert json.loads(get_state(store, TAKEOFFS))[JOB]["bad"] is False
    assert list_dev(store) == []


def test_a_chat_heads_up_already_sent_makes_ng_runners_one_revised(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    record_heads_up(store, folder=JOB, json_sha256="4" * 64, sent_at=NOW - timedelta(days=1))
    ours(store)

    run(ctx)

    assert {"revised" in m["title"] for m in told(store)} == {True}


# ----- who hears, as P1b ruled


def test_while_order_flags_are_off_nothing_goes_and_turning_them_on_sends_no_pile(store, tmp_path):
    ours(store)
    run(ctx_of(store, tmp_path, on=False))
    assert told(store) == [] and get_heads_up(store, JOB) is not None

    run(ctx_of(store, tmp_path, on=True))

    assert told(store) == []


def test_in_shadow_one_copy_goes_through_the_messenger_which_sends_it_to_sulaiman(store, tmp_path):
    ours(store)

    run(ctx_of(store, tmp_path, mode="shadow", on=False))

    assert [m["to"] for m in told(store)] == ["barbara"]   # messenger.py redirects it, marked


# ----- a takeoff of NG Runner's that isn't complete: the chat's list when its JSON reads
# (Sulaiman's ruling, 2026-10-31): never a partial list, never a mix of the two, and "could not
# verify the takeoff" only when neither gives a whole list.


def test_an_incomplete_takeoff_falls_back_to_the_chats_whole_list_when_its_json_reads(
        store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, complete=False, could_not_verify=1)

    run(ctx, chat_json())

    messages = told(store)
    assert sorted(m["to"] for m in messages) == ["barbara", "will"]
    message, text = messages[0], said(messages[0])
    assert message["title"] == f"{JOB}: order heads-up (takeoff estimate; the nest decides)"
    assert message["lines"][0].endswith("From the chat's takeoff, since NG Runner's own takeoff"
                                        " could not verify 1 line.")
    assert "12 sheets 3/4 Egger Classic White" in text            # the chat's list, whole
    assert "NG Runner's own takeoff of the IC summary" not in text           # none of ours
    assert re.search(r"(?<!\d)2 sheets", text) is None
    assert json.loads(get_state(store, TAKEOFFS))[JOB]["source"] == "chat"


def test_an_incomplete_takeoff_whose_chat_json_wont_read_says_could_not_verify(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, complete=False, could_not_verify=2)

    run(ctx, TakeoffRead(JOB, Listed(3, NOW), "3" * 64, None))

    message = told(store)[0]
    assert "could not verify the takeoff" in message["title"]
    assert "could not verify 2 lines" in said(message) and "sheet" not in said(message)
    assert [d.code for d in list_dev(store)] == [UNREADABLE_CODE]
    kept = json.loads(get_state(store, TAKEOFFS))[JOB]
    assert kept["bad"] is True and "source" not in kept


def test_could_not_verify_first_then_the_chats_list_revised_when_its_json_arrives(
        store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, complete=False, could_not_verify=1)
    run(ctx)
    assert {"could not verify the takeoff" in m["title"] for m in told(store)} == {True}

    run(ctx, chat_json())

    later = told(store)[2:]
    assert len(later) == 2 and all("revised" in m["title"] for m in later)
    assert "12 sheets 3/4 Egger Classic White" in said(later[0])
    assert "From the chat's takeoff" in later[0]["lines"][0]


def test_the_chats_list_already_sent_stays_when_ng_runners_takeoff_is_not_complete(
        store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    run(ctx, chat_json())                       # P1b: the chat's list went out
    ours(store, complete=False, could_not_verify=1)

    run(ctx)
    run(ctx)

    assert len(told(store)) == 2                 # the same list from the same source: nothing more
    assert json.loads(get_state(store, TAKEOFFS))[JOB]["source"] == "chat"


def test_a_chat_json_noted_while_ng_runners_was_complete_is_read_again_and_sent_when_it_isnt(
        store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store)
    run(ctx, chat_json())                       # noted, not sent: NG Runner's complete one went
    ours(store, SHA_2, complete=False, could_not_verify=1, at=NOW + timedelta(days=1))

    run(ctx)                                    # no read of it this pass, so it is asked for
    assert "read" not in json.loads(get_state(store, TAKEOFFS))[JOB]
    assert len(told(store)) == 2
    run(ctx, chat_json())                       # and the next pass reads it, off the tick's thread

    later = told(store)[2:]
    assert len(later) == 2 and all("revised" in m["title"] for m in later)
    assert "From the chat's takeoff" in later[0]["lines"][0]


def test_a_later_complete_takeoff_of_ng_runners_takes_over_from_the_chats_list_revised(
        store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, complete=False, could_not_verify=1)
    run(ctx, chat_json())
    ours(store, SHA_2, at=NOW + timedelta(days=1))

    run(ctx)
    run(ctx)

    later = told(store)[2:]
    assert len(later) == 2 and all("revised" in m["title"] for m in later)
    assert "NG Runner's own takeoff" in said(later[0]) and "12 sheets" not in said(later[0])
    assert "chat" not in said(later[0])


def test_a_change_to_the_chats_json_while_it_is_the_fallback_sends_one_revised(store, tmp_path):
    ctx = ctx_of(store, tmp_path)
    ours(store, complete=False, could_not_verify=1)
    run(ctx, chat_json())
    run(ctx, chat_json())                       # the same bytes again: nothing

    run(ctx, chat_json(estimate={"total": 26000.0}))

    later = told(store)[2:]
    assert len(later) == 2 and "revised" in later[0]["title"]
    assert "$26,000.00" in said(later[0])
