"""
Nightly pruning of what would otherwise pile up (integration spec section 6; decisions.md, "Nightly
work"): old commit rows, and old renders with their cached files.

Why: the store keeps a row for each of about 300 commits a day, and every fetch of a summary has
new bytes, so a job's cached PDFs would pile up on Jim's PC through 30 nights of re-fetches. owned
is never pruned here, as a job's own files can be rewritten at any age, and neither is a journal
row still pending, which start-up must settle. Removing a cached file is allowed because it is in
NG Runner's own data folder: one anywhere else is never removed (AGENTS.md rule 1).
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ngrunner.store.db import Db, Store, StoreError, iso, session, time_of


@dataclass(frozen=True)
class PrunedRenders:
    """removed: render rows that went. left: cached files that were not removed: one in use keeps
    its row and is tried again next night; one outside the data folder is never removed, and loses
    only its row."""

    removed: int = 0
    left: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _Render:
    id: int
    studio_id: str
    fetched_at: datetime
    fingerprint: str | None
    governing: bool
    cache_path: Path | None


def prune_write_records(db: Db, *, before: datetime) -> int:
    """Forget commits made before that time, and journal rows closed by then; returns how many
    commits went."""
    cutoff = iso(before, "before")
    with session(db, write=True) as conn:
        conn.execute("DELETE FROM pending_writes WHERE state <> 'pending' AND at < ?", (cutoff,))
        return conn.execute("DELETE FROM writes WHERE at < ?", (cutoff,)).rowcount


def prune_renders(store: Store, *, before: datetime) -> PrunedRenders:
    """Remove renders fetched before that time, and their cached files. Each job keeps its governing
    render (with none yet, its newest) and the latest one whose fingerprint differs from it, however
    old. A file another kept row still uses stays. Pass a time before any fetch still being
    compared: a day-old pair is in use, a week-old one is not. Removing files is slow work, so a
    transaction is refused (StoreError)."""
    if not isinstance(store, Store):
        raise StoreError("Old renders can't be pruned inside one of the store's transactions.")
    cutoff = time_of(iso(before, "before"))
    rows = _all_renders(store)
    going = _renders_to_drop(rows, cutoff)
    going_ids = {r.id for r in going}
    in_use = {_file_key(r.cache_path) for r in rows if r.id not in going_ids and r.cache_path}
    removed, left = [], []
    for render in going:
        path = render.cache_path
        if path is None or _file_key(path) in in_use:
            removed.append(render.id)
        elif not _inside_data_folder(path, store):
            removed.append(render.id)
            left.append(path)
        elif _remove_file(path):
            removed.append(render.id)
        else:
            left.append(path)
    return PrunedRenders(_drop_rows(store, removed), tuple(left))


def _all_renders(store: Store) -> list[_Render]:
    with session(store, write=False) as conn:
        rows = conn.execute(
            "SELECT id, studio_id, fetched_at, fingerprint, governing, cache_path FROM renders"
            " ORDER BY studio_id, fetched_at, id"
        ).fetchall()
    return [
        _Render(
            r["id"], r["studio_id"], time_of(r["fetched_at"]), r["fingerprint"],
            bool(r["governing"]), Path(r["cache_path"]) if r["cache_path"] else None,
        )
        for r in rows
    ]


def _renders_to_drop(rows: list[_Render], cutoff: datetime) -> list[_Render]:
    by_job: dict[str, list[_Render]] = defaultdict(list)
    for render in rows:
        by_job[render.studio_id].append(render)
    going = []
    for renders in by_job.values():
        kept = _keepers(renders)
        going += [r for r in renders if r.id not in kept and r.fetched_at < cutoff]
    return going


def _keepers(renders: list[_Render]) -> set[int]:
    """Ids of the render that counts for a job and the latest that differs from it. renders run
    oldest first."""
    main = next((r for r in renders if r.governing), renders[-1])
    differs = next(
        (r for r in reversed(renders)
         if r.id != main.id and r.fingerprint is not None and r.fingerprint != main.fingerprint),
        None,
    )
    return {main.id} if differs is None else {main.id, differs.id}


def _remove_file(path: Path) -> bool:
    """True when the file is gone, so its row can go; False when it is in use: try next night."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _inside_data_folder(path: Path, store: Store) -> bool:
    folder = [part.casefold() for part in Path(os.path.realpath(store.path.parent)).parts]
    here = [part.casefold() for part in Path(os.path.realpath(path)).parts]
    return len(here) > len(folder) and here[: len(folder)] == folder


def _drop_rows(store: Store, ids: list[int]) -> int:
    with session(store, write=True) as conn:
        return sum(
            conn.execute("DELETE FROM renders WHERE id = ? AND governing = 0", (rid,)).rowcount
            for rid in ids
        )


def _file_key(path: Path) -> str:
    return os.path.normcase(str(path))
