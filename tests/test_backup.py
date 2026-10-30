"""Backups: encrypted to an ICLA-held key, restorable, refused when anything is wrong; and the
pruning of commit_write's own copies after 60 days, which never reaches outside the data folder."""
from __future__ import annotations

import _winapi
import io
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner import __main__ as cli
from ngrunner import backup
from ngrunner.backup import BackupError
from ngrunner.clock import FakeClock
from ngrunner.config import AppPaths

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=61)
RECENT = NOW - timedelta(days=59)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "NGRunner")


@pytest.fixture(scope="module")
def keys() -> tuple[str, str]:
    return backup.new_keypair()


@pytest.fixture
def live_db(paths: AppPaths) -> Iterator[sqlite3.Connection]:
    """NG Runner's database as it is while NG Runner runs: WAL mode, open, rows not yet
    checkpointed into the main file."""
    paths.app_data.mkdir(parents=True)
    con = sqlite3.connect(paths.db, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE jobs(studio_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    con.executemany("INSERT INTO jobs VALUES (?, ?)",
                    [("1001", "Testerson_101426"), ("1002", "Fakeley_102026"), ("1003", "Mockford_103026")])
    con.execute("CREATE TABLE cards(id TEXT PRIMARY KEY)")
    con.execute("PRAGMA user_version = 4")
    yield con
    con.close()


# ----- a backup and its restore


def test_a_nightly_backup_of_the_running_database_restores_to_the_same_database(paths, keys, live_db):
    private, public = keys
    made = backup.nightly_backup(paths, public, clock=FakeClock(datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)))
    assert (made.folder, made.name) == ("backups", "20261029.ngrbak")     # 11 PM on 10/29, Pacific
    assert b"Testerson" not in made.data
    facts = backup.describe(backup.restore(made.data, private))
    assert facts.ok and facts.schema_version == 4
    assert facts.tables == {"cards": 0, "jobs": 3}


def test_each_backup_is_locked_with_a_fresh_key(keys):
    private, public = keys
    first, second = backup.encrypt(b"SQLite format 3\x00same", public), backup.encrypt(b"SQLite format 3\x00same", public)
    assert first != second
    assert backup.restore(first, private) == backup.restore(second, private) == b"SQLite format 3\x00same"


def test_there_is_nothing_to_back_up_before_the_database_exists(paths, keys):
    with pytest.raises(BackupError, match="no database to back up"):
        backup.nightly_backup(paths, keys[1], clock=FakeClock(NOW))


# ----- refusals, each with a plain reason


def test_the_wrong_private_key_is_refused(keys):
    data = backup.encrypt(b"SQLite format 3\x00rows", keys[1])
    other_private, _ = backup.new_keypair()
    with pytest.raises(BackupError, match="locked with a different backup key"):
        backup.restore(data, other_private)


def test_the_public_key_never_restores_and_says_which_key_is_needed(keys):
    data = backup.encrypt(b"SQLite format 3\x00rows", keys[1])
    with pytest.raises(BackupError, match="public key; restoring needs the private key"):
        backup.restore(data, keys[1])


@pytest.mark.parametrize("where", [0, 6, 7, 15, 20, 50, 59, 70, -1])
def test_a_flipped_byte_anywhere_is_refused(keys, where):
    data = bytearray(backup.encrypt(b"SQLite format 3\x00" + b"rows" * 50, keys[1]))
    data[where] ^= 0x01
    with pytest.raises(BackupError) as refused:
        backup.restore(bytes(data), keys[0])
    assert _plain(str(refused.value))


@pytest.mark.parametrize("cut", [0, 5, 40, 74, -1])
def test_a_cut_short_file_is_refused(keys, cut):
    data = backup.encrypt(b"SQLite format 3\x00" + b"rows" * 50, keys[1])
    with pytest.raises(BackupError) as refused:
        backup.restore(data[:cut], keys[0])
    assert _plain(str(refused.value))


def test_a_newer_format_is_refused_by_name(keys):
    data = bytearray(backup.encrypt(b"SQLite format 3\x00rows", keys[1]))
    data[6] = 2
    with pytest.raises(BackupError, match="newer NG Runner"):
        backup.restore(bytes(data), keys[0])


def test_a_file_that_is_no_backup_is_named_as_such(keys):
    with pytest.raises(BackupError, match="not an NG Runner backup"):
        backup.restore(b"%PDF-1.7 " + b"x" * 200, keys[0])


@pytest.mark.parametrize("text", ["", "ngrbak-private-1-xyz", "0" * 64, "ngrbak-private-1-" + "0" * 63])
def test_a_key_that_is_not_a_backup_key_is_refused(keys, text):
    data = backup.encrypt(b"SQLite format 3\x00rows", keys[1])
    with pytest.raises(BackupError, match="not an NG Runner backup key"):
        backup.restore(data, text)
    with pytest.raises(BackupError, match="not an NG Runner backup key"):
        backup.encrypt(b"rows", text)


def test_each_key_pair_has_one_short_id_that_names_it(keys):
    private, public = keys
    assert backup.key_id(private) == backup.key_id(public)
    assert len(backup.key_id(public)) == 16
    assert backup.key_id(backup.new_keypair()[1]) != backup.key_id(public)


def _plain(reason: str) -> bool:
    return reason[0].isupper() and reason.endswith(".") and not any(
        word in reason for word in ("Traceback", "InvalidTag", "Error", "0x"))


# ----- describing a database


def test_a_damaged_database_is_described_as_damaged_never_raised():
    for junk in (b"SQLite format 3\x00" + bytes(300), b"not a database", b""):
        facts = backup.describe(junk)
        assert not facts.ok and facts.problems


# ----- pruning commit_write's copies


def _old_copy(paths: AppPaths, stamp: str, *parts: str, when: datetime = OLD) -> Path:
    path = paths.backup.joinpath(stamp, *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7 earlier render")
    _age(paths.backup / stamp, when)
    return path


def _age(top: Path, when: datetime) -> None:
    for folder, dirs, files in os.walk(top):
        for name in files + dirs:
            os.utime(Path(folder) / name, (when.timestamp(), when.timestamp()))
    os.utime(top, (when.timestamp(), when.timestamp()))


def test_copies_older_than_60_days_are_pruned_and_newer_ones_kept(paths):
    _old_copy(paths, "20260801T120000000000Z", "ng_jobs", "Testerson_101426", "Testerson_101426_summary.pdf")
    _old_copy(paths, "20260801T120000000000Z-2", "control", "heartbeat", "20260801", "1.json")
    kept = _old_copy(paths, "20261001T120000000000Z", "ng_jobs", "Fakeley_102026", "Fakeley_102026_summary.pdf",
                     when=RECENT)
    pruned = backup.prune(paths, clock=FakeClock(NOW))
    assert pruned.removed == ("20260801T120000000000Z", "20260801T120000000000Z-2")
    assert pruned.left == ()
    assert sorted(p.name for p in paths.backup.iterdir()) == ["20261001T120000000000Z"]
    assert kept.is_file()


def test_a_copy_whose_files_changed_recently_is_kept_whatever_its_name_says(paths):
    copy = _old_copy(paths, "20260801T120000000000Z", "ng_jobs", "Testerson_101426", "Testerson_101426_summary.pdf")
    os.utime(copy, (RECENT.timestamp(), RECENT.timestamp()))
    assert backup.prune(paths, clock=FakeClock(NOW)).removed == ()
    assert copy.is_file()


def test_pruning_leaves_alone_anything_ngrunner_did_not_make(paths):
    _old_copy(paths, "notes", "readme.txt")
    _old_copy(paths, "20260801T120000000000Z.extra", "x.pdf")
    (paths.backup / "loose.txt").write_bytes(b"x")
    pruned = backup.prune(paths, clock=FakeClock(NOW))
    assert pruned.removed == ()
    assert sorted(p.name for p in paths.backup.iterdir()) == ["20260801T120000000000Z.extra", "loose.txt", "notes"]


def test_pruning_never_follows_a_junction_out_of_the_data_folder(paths, tmp_path):
    outside = tmp_path / "Mozaik Shared" / "Jobs" / "Testerson_101426"
    outside.mkdir(parents=True)
    (outside / "Room1.des").write_bytes(b"Jim's design")
    copy = _old_copy(paths, "20260801T120000000000Z", "ng_jobs", "Testerson_101426", "Testerson_101426_summary.pdf")
    _winapi.CreateJunction(str(outside), str(copy.parent / "linked"))
    _age(paths.backup / "20260801T120000000000Z", OLD)
    pruned = backup.prune(paths, clock=FakeClock(NOW))
    assert pruned.removed == ()
    assert len(pruned.left) == 1 and "link" in pruned.left[0]
    assert (outside / "Room1.des").read_bytes() == b"Jim's design"
    assert copy.is_file() and (copy.parent / "linked").exists()


def test_a_backup_folder_that_is_itself_a_link_is_never_pruned(paths, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    old = elsewhere / "20260801T120000000000Z"
    old.mkdir(parents=True)
    (old / "keep.txt").write_bytes(b"not NG Runner's")
    _age(old, OLD)
    paths.app_data.mkdir(parents=True)
    _winapi.CreateJunction(str(elsewhere), str(paths.backup))
    pruned = backup.prune(paths, clock=FakeClock(NOW))
    assert pruned.removed == () and len(pruned.left) == 1
    assert (old / "keep.txt").is_file()


def test_with_no_backup_folder_there_is_nothing_to_prune(paths):
    assert backup.prune(paths, clock=FakeClock(NOW)) == backup.Pruned((), ())


def test_removing_a_tree_refuses_anything_not_inside_the_given_folder(paths, tmp_path):
    other = tmp_path / "other"
    (other / "sub").mkdir(parents=True)
    (other / "sub" / "x.txt").write_bytes(b"x")
    paths.backup.mkdir(parents=True)
    assert backup.remove_own_tree(other, inside=paths.backup) is not None
    assert backup.remove_own_tree(paths.backup, inside=paths.backup) is not None
    assert backup.remove_own_tree(paths.backup / ".." / ".." / "other", inside=paths.backup) is not None
    assert (other / "sub" / "x.txt").is_file()


def test_removing_a_tree_removes_all_of_it_when_it_is_plain(paths):
    copy = _old_copy(paths, "20260801T120000000000Z", "ng_jobs", "a", "b", "c.pdf")
    assert backup.remove_own_tree(paths.backup / "20260801T120000000000Z", inside=paths.backup) is None
    assert not copy.exists() and list(paths.backup.iterdir()) == []


# ----- the command line: keygen, the restore drill, and a restore onto a new PC


def _cli(argv: list[str], paths: AppPaths, *, key: str = "", running: bool = False) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(argv, out=out, environ={"NGR_APP_DATA": str(paths.app_data)},
                    read_secret=lambda prompt: key, running=lambda: running)
    return code, out.getvalue()


def test_keygen_prints_a_key_pair_that_locks_and_opens_a_backup(paths):
    code, text = _cli(["keygen"], paths)
    lines = text.splitlines()
    assert code == 0 and len(lines) == 3
    private, public = lines[1].rsplit(" ", 1)[1], lines[2].rsplit(" ", 1)[1]
    assert private.startswith("ngrbak-private-1-") and public.startswith("ngrbak-public-1-")
    assert lines[0] == f"New backup key pair, key {backup.key_id(public)}:"
    assert backup.restore(backup.encrypt(b"SQLite format 3\x00rows", public), private) == b"SQLite format 3\x00rows"
    assert not paths.app_data.exists()                               # keygen writes nothing


@pytest.fixture
def backup_file(paths, keys, live_db, tmp_path) -> Path:
    made = backup.nightly_backup(paths, keys[1], clock=FakeClock(NOW))
    path = tmp_path / "from the control folder" / made.name
    path.parent.mkdir()
    path.write_bytes(made.data)
    return path


def test_the_restore_drill_checks_a_backup_and_writes_nothing(paths, keys, backup_file, tmp_path):
    new_pc = AppPaths(tmp_path / "new PC" / "NGRunner")
    code, text = _cli(["restore", str(backup_file)], new_pc, key=keys[0])
    assert (code, text) == (0, "Restore drill passed: schema version 4, 2 tables, 3 rows.\n")
    assert not new_pc.app_data.exists()


def test_a_drill_with_the_wrong_key_fails_and_says_why(paths, backup_file):
    code, text = _cli(["restore", str(backup_file)], paths, key=backup.new_keypair()[0])
    assert code == 1 and "locked with a different backup key" in text


def test_a_restore_puts_the_database_in_place_on_a_new_pc_only(paths, keys, backup_file, tmp_path):
    new_pc = AppPaths(tmp_path / "new PC" / "NGRunner")
    code, text = _cli(["restore", str(backup_file), "--install"], new_pc, key=keys[0])
    assert code == 0 and text.splitlines()[1].startswith("The database is in place")
    assert backup.describe(new_pc.db.read_bytes()).tables == {"cards": 0, "jobs": 3}
    code, text = _cli(["restore", str(backup_file), "--install"], new_pc, key=keys[0])
    assert code == 1 and "already has a database here (ngrunner.db)" in text


def test_a_restore_waits_until_ngrunner_has_quit(keys, backup_file, tmp_path):
    new_pc = AppPaths(tmp_path / "new PC" / "NGRunner")
    code, text = _cli(["restore", str(backup_file), "--install"], new_pc, key=keys[0], running=True)
    assert code == 1 and "Quit it from the tray first" in text and not new_pc.db.exists()


def test_a_restore_left_half_done_earlier_is_never_overwritten(keys, backup_file, tmp_path):
    new_pc = AppPaths(tmp_path / "new PC" / "NGRunner")
    new_pc.app_data.mkdir(parents=True)
    left = new_pc.db.with_name(new_pc.db.name + ".restoring")
    left.write_bytes(b"from an earlier attempt")
    code, text = _cli(["restore", str(backup_file), "--install"], new_pc, key=keys[0])
    assert code == 1 and "could not be written" in text and "nothing was put in place" in text
    assert left.read_bytes() == b"from an earlier attempt" and not new_pc.db.exists()
