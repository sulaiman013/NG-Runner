"""
The store under concurrent use (build spec section 4): card IDs and sequence numbers are never
handed out twice, and a replayed envelope is accepted once, whether the callers are threads of
one process or separate processes on the same file. Databases live under tmp_path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ngrunner.store import (
    LATEST, Store, accept_envelope, accept_seq, allocate_card_id, next_seq, open_store, seen_id,
)

REPO = Path(__file__).resolve().parent.parent
THREADS, EACH = 8, 25
ENVELOPE_ID = "c" * 32


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "ngrunner.db")


def all_at_once(count: int, work: Callable[[], object]) -> list[object]:
    """Run work in count threads, released together, and return what each returned."""
    gate = threading.Barrier(count)

    def run() -> object:
        gate.wait(10)
        return work()

    with ThreadPoolExecutor(count) as pool:
        return [f.result(30) for f in [pool.submit(run) for _ in range(count)]]


# ----- threads


def test_threads_never_get_the_same_card_id_or_seq(store: Store) -> None:
    def take_many() -> list[tuple[str, int]]:
        return [(allocate_card_id(store, "TES"), next_seq(store, "heartbeat")) for _ in range(EACH)]

    taken = [pair for batch in all_at_once(THREADS, take_many) for pair in batch]

    ids, seqs = [card for card, _ in taken], [seq for _, seq in taken]
    total = THREADS * EACH
    by_number = sorted(ids, key=lambda i: int(i.split("-")[1]))
    assert by_number == [f"TES-{n}" for n in range(1, total + 1)]
    assert sorted(seqs) == list(range(1, total + 1))


def test_threads_starting_a_new_stream_together_never_get_the_same_seq(store: Store) -> None:
    start = 1_793_000_000

    def take_many() -> list[int]:
        return [next_seq(store, "outbox", start=start) for _ in range(EACH)]

    taken = [seq for batch in all_at_once(THREADS, take_many) for seq in batch]

    assert sorted(taken) == list(range(start, start + THREADS * EACH))


@pytest.mark.parametrize("replay", [
    lambda s: accept_seq(s, "fab_export", 5),
    lambda s: seen_id(s, "pause", ENVELOPE_ID),
    lambda s: accept_envelope(s, "pause", seq=5, id=ENVELOPE_ID),
], ids=["accept_seq", "seen_id", "accept_envelope"])
def test_one_envelope_read_by_many_threads_is_accepted_once(store: Store, replay) -> None:
    answers = all_at_once(THREADS, lambda: replay(store))

    assert sorted(answers) == [False] * (THREADS - 1) + [True]


def test_threads_opening_a_new_store_together_all_get_it(tmp_path: Path) -> None:
    # The first switch to write-ahead logging is the one lock SQLite doesn't wait for. Before it
    # waited itself, about 6 rounds in 10 of this failed, and 2 processes failed about 1 in 10.
    for n in range(10):
        path = tmp_path / f"{n}.db"
        stores = all_at_once(THREADS, lambda: open_store(path))
        assert {s.schema_version() for s in stores} == {LATEST}


# ----- processes


CHILD = r'''
import json, sys, time
from pathlib import Path
repo, db, ready, go, each = sys.argv[1:6]
sys.path.insert(0, repo)
from ngrunner.store import allocate_card_id, next_seq, open_store

Path(ready).write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 30
while not Path(go).exists():              # start together with the other process
    if time.monotonic() > deadline:
        sys.exit("never told to go")
    time.sleep(0.005)
store = open_store(Path(db))              # both may be the first to open it: one migrates
taken = [(allocate_card_id(store, "TES"), next_seq(store, "outbox")) for _ in range(int(each))]
print(json.dumps(taken))
'''


def test_two_processes_never_get_the_same_card_id_or_seq(tmp_path: Path) -> None:
    script, db, go, each = tmp_path / "child.py", tmp_path / "ngrunner.db", tmp_path / "go", 40
    script.write_text(CHILD, encoding="utf-8")
    ready = [tmp_path / f"ready{n}" for n in (1, 2)]
    children = [
        subprocess.Popen(
            [sys.executable, "-I", "-X", "warn_default_encoding", str(script), str(REPO), str(db),
             str(flag), str(go), str(each)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
        )
        for flag in ready
    ]
    try:
        deadline = time.monotonic() + 60
        while not all(flag.exists() for flag in ready) and time.monotonic() < deadline:
            time.sleep(0.01)
        go.write_text("go", encoding="utf-8")
        outputs = [child.communicate(timeout=120) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()

    for child, (_, err) in zip(children, outputs):
        assert child.returncode == 0, err
    taken = [tuple(pair) for out, _ in outputs for pair in json.loads(out)]
    total = 2 * each
    assert sorted(card for card, _ in taken) == sorted(f"TES-{n}" for n in range(1, total + 1))
    assert sorted(seq for _, seq in taken) == list(range(1, total + 1))
