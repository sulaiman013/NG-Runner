"""
P3's replay trial and the audit of chat-built jobs, on every real job here (docs/decisions.md, "P3: the
replay trial and the audit"; ruling R1).

NG Runner's check runs as it runs in the app: Jim's crosscheck from a staged copy of his toolkit, in the
tools' Python, on a staged copy of each job, its IC Conversion read from NG Jobs on G: (never written)
and his template job, "Lux Closet Template Claude", staged beside it for parm_check. The jobs: Cases A to
J, the 40 P5 jobs (P-tags) and his 3 frozen jobs (X, Y, Z). Marked fixtures: skips when the toolkit, the
tools' Python, the template job or NG Jobs is absent. Jobs are named by case letter or tag only; nothing
a checker or the chat printed is shown, only codes, tags and sizes.

The replay. What the chat reported on a job is compared with NG Runner's check of it finding by finding
(ngrunner/checks/trial.py), on the checkers the chat's output speaks for:
- Jim's crosscheck file as the chat saved it (Cases C to G, in .local-fixtures\\p1b\\checks) is read by
  NG Runner's own readers, so nothing is transcribed.
- The chat's own reports (panel checks, crosschecks it wrote itself) were read by a person into the same
  vocabulary: CHAT below, as each report's last word to Jim stood. A report's items about what no checker
  of NG Runner's looks at (materials, the tracker, prices, the nest) are counted as outside.
Every disagreement has a ruling in RULED, with the line of Jim's docs it rests on, or "can't tell"; the
test fails if one is missing or one more appears. The rooms in the fixtures are as Jim last saved them,
so most reports, written days or weeks before, speak of rooms that changed since.
"""
from __future__ import annotations

import collections
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import FIXTURES
from helpers_toolkit import CASES, ng_jobs, real_toolkit, stage_tree, tools_python
from ngrunner.checks import Planned, findings as fd, run_crosscheck, topline, trial
from ngrunner.checks.readers import NO_REFERENCE
from ngrunner.clock import FakeClock
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED
from ngrunner.toolkit import take_snapshot

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
TEMPLATE = FIXTURES / "answers20261031" / "Lux Closet Template Claude" / "mozaik"
FOURTEEN = ("section_align", "section_span", "drill_depth_check", "outset_check", "part_geom_check", "br_cam_check",
            "op_schema_check", "wm_variant_check", "notch_check", "eb_tops", "top_line", "parm_check",
            "panel_xcheck", "ic_code_check")
PLAN = tuple(Planned(name, name == "ic_code_check") for name in FOURTEEN)
P = "panel_xcheck"


# ----- the jobs, and NG Runner's check of each


def _conversion(root: Path, name: str) -> Path | None:
    for place in (root / name, root / "Old" / name):
        path = place / f"{name}_IC_Conversion.json"
        if path.is_file():
            return path
    return None


def real_jobs(tmp: Path) -> dict[str, tuple[str, Path, Path | None]]:
    """tag: (job set, Mozaik folder, IC Conversion or None), for all 53 jobs, or a skip."""
    root = ng_jobs()
    case_key, p5_key = FIXTURES / "p1b" / "case_key.json", FIXTURES / "reader" / "p5jobs" / "key.json"
    if root is None or not root.is_dir() or not case_key.is_file() or not p5_key.is_file():
        pytest.skip("NG Jobs or the job keys aren't on this PC")
    toolkit = real_toolkit()
    jobs: dict[str, tuple[str, Path, Path | None]] = {}
    for case, name in json.loads(case_key.read_text(encoding="utf-8")).items():
        if case in CASES and (toolkit / "jobs" / case).is_dir():
            jobs[case] = ("A to J", next((toolkit / "jobs" / case).iterdir()), _conversion(root, name))
    for tag, name in json.loads(p5_key.read_text(encoding="utf-8")).items():
        folder = FIXTURES / "reader" / "p5jobs" / tag / name
        if folder.is_dir():
            jobs[tag] = ("P5", folder, _conversion(root, name))
    regression = toolkit / "NG_Automation_reference" / "reference" / "regression"
    for tag, source in zip("XYZ", sorted(p for p in regression.iterdir() if (p / "rooms").is_dir())):
        folder = tmp / "frozen" / tag / source.name
        folder.mkdir(parents=True)
        for room in (source / "rooms").glob("Room*.des"):
            shutil.copyfile(room, folder / room.name)
        shutil.copyfile(source / "NG Closets-JobParms.dat", folder / "NG Closets-JobParms.dat")
        jobs[tag] = ("frozen", folder, source / "ic_conversion.json")
    if len(jobs) != 53:
        pytest.skip("the 53 real jobs aren't all on this PC")
    return jobs


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """tag: (job set, NG Runner's crosscheck or None with no IC Conversion, the rooms' bytes)."""
    python = tools_python()
    if not TEMPLATE.is_dir():
        pytest.skip("Jim's template job isn't on this PC (.local-fixtures/answers20261031)")
    jobs = real_jobs(tmp_path_factory.mktemp("jobs"))
    copy = take_snapshot(stage_tree(tmp_path_factory.mktemp("jims")), tmp_path_factory.mktemp("data") / "copy",
                         clock=FakeClock(NOW))
    work = tmp_path_factory.mktemp("staging")

    def one(tag: str):
        kind, folder, conversion = jobs[tag]
        rooms = {path.name: path.read_bytes() for path in folder.glob("Room*.des")}
        if conversion is None:
            return tag, (kind, None, rooms)
        return tag, (kind, run_crosscheck(copy, folder, conversion, work / tag, template=TEMPLATE, python=python),
                     rooms)

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(one, sorted(jobs)))


def checked(runs, tag: str, *, kind: str = "check") -> fd.Findings:
    _, result, rooms = runs[tag]
    return fd.findings_of(result, kind=kind, room_files=rooms)


# ----- the template job: parm_check compares at last


def test_with_the_template_job_staged_parm_check_compares_on_every_job(runs) -> None:
    verdicts = {tag: next(v for v in result.verdicts if v.checker == "parm_check")
                for tag, (_, result, _) in runs.items() if result is not None}

    assert len(verdicts) == 45 and not [t for t, v in verdicts.items() if v.why == NO_REFERENCE]
    assert sorted(t for t, v in verdicts.items() if v.code == PASSED) == ["C", "D", "F", "G"]
    assert {v.code for t, v in verdicts.items() if t not in "CDFG"} == {FAILED}


# ----- the replay: Jim's crosscheck files as the chat saved them, read by NG Runner's own readers


JIMS_FORMAT = {   # case: (both, NG Runner only, chat only), as measured on 2026-11-01
    "C": ((), (), ()), "F": ((), (), ()), "G": ((), (), ()),
    "E": ((), (), ()),          # its SysHolesW of 4 is known noise on both sides (RULES 604-614)
    "D": ((), (("br_cam_check", "doubled_holes", "R1C5"),), (("drill_depth_check", "other", "R1C1"),)),
}


def _chats_crosscheck_files() -> dict[str, str]:
    index, key = FIXTURES / "p1b" / "checks" / "index.json", FIXTURES / "p1b" / "case_key.json"
    if not index.is_file() or not key.is_file():
        pytest.skip("the chat's crosscheck files aren't on this PC")
    case_of = {name: case for case, name in json.loads(key.read_text(encoding="utf-8")).items()}
    return {case_of[e["job"]]: (FIXTURES / "p1b" / "checks" / e["file"]).read_text(encoding="utf-8")
            for e in json.loads(index.read_text(encoding="utf-8")) if e["role"] == "crosscheck"}


def test_jims_crosscheck_files_as_the_chat_saved_them_compare_finding_by_finding(runs) -> None:
    measured = {}
    for case, text in sorted(_chats_crosscheck_files().items()):
        chat, _ = trial.chat_crosscheck(text, plan=PLAN)
        compared = trial.compare(fd.findings_of(runs[case][1]), chat)
        assert not compared.unverified and not compared.outside
        measured[case] = tuple(tuple(sorted((f.checker, f.what, f.part) for f in getattr(compared, side)))
                               for side in ("both", "ours_only", "chat_only"))

    assert measured == JIMS_FORMAT


# ----- the replay: the chat's own reports, read into NG Runner's vocabulary by a person


def _f(checker: str, what: str, *parts: str) -> list[tuple[str, str, str]]:
    return [(checker, what, part) for part in parts or ("",)]


CHAT = {   # tag: (the checkers its report speaks for, what it raised for Jim, how many items outside the check)
    "A": ((P,), [], 0),
    "B": (("section_align",), _f("section_align", "off_grid", "R2C8", "R2C10", "R2C12")
          + _f("section_align", "top_off", "R2C8", "R2C10", "R2C12") + _f("section_align", "bore_pitch", "R2C7"), 0),
    "H": ((P,), [], 2),
    "J": ((P,), _f(P, "chat", *(f"mismatch {n}" for n in range(1, 17))), 2),     # a closet and garage job (R4)
    "P04": ((P,), _f(P, "chat", "TR Base built short"), 2),
    "P05": ((P, "notch_check"), [], 0),
    "P08": ((P,), [], 2),
    "P16": ((P,), [], 0),
    "P20": ((P, "br_cam_check", "drill_depth_check"), _f(P, "missing_part", "88.9x2438.4"), 1),
    "P22": ((P,), [], 0),
    "P23": ((P,), [], 1),
    "P24": ((P,), [], 0),
    "P26": ((P,), _f(P, "missing_part", "330.2x2356.0", "356.0x370.8", "356.0x607.1", "254.0x2028.0"), 1),
    "P27": ((P,), [], 0),
    "P28": (("section_align", "drill_depth_check", "br_cam_check", "wm_variant_check", "op_schema_check", P), [], 0),
    "P29": ((P,), [], 2),
    "P30": ((P, "section_align", "section_span", "br_cam_check", "notch_check"),
            _f(P, "chat", "a toe missing", "a toe extra", "an adjustable extra", "two walls with no sections",
               "four nailers extra") + _f("section_span", "span", "R6C4"), 3),
    "P33": ((P, "notch_check"), _f("notch_check", "wm_banding", "R1", "R2", "R3"), 1),
    "P35": ((P,), _f(P, "missing_part", "607.1x985.5") + _f(P, "extra_part", "606.0x984.2", "536.2x906.4"), 0),
    "P39": (("section_align", "br_cam_check", "wm_variant_check", "notch_check", "op_schema_check", "outset_check", P,
             "drill_depth_check", "parm_check"), _f("drill_depth_check", "bore_depth", "R3C3", "R4C3", "R4C5", "R5C3",
                                                     "R8C2"), 0),
}

# Who is right, by Jim's docs (his toolkit of 2026-10-31). "ours": NG Runner's finding is real; "later": real
# by a rule made after the chat's check; "chat": NG Runner's is noise; "since": the rooms were saved after
# the chat's check and NG Runner reads them so, so no miss; "jim": Jim's own word on that job; "can't tell".
# A part of "*" stands for every part of that kind on that job.
RULED = {
    ("D", "ours", "br_cam_check", "doubled_holes", "R1C5"): ("ours", "RULES 536-539: this job's doubled housing reached its programs (line 538 names it)"),
    ("D", "chat", "drill_depth_check", "other", "R1C1"): ("since", "the chat's own delivery logged it as no defect, and NG Runner reads the rooms as saved since without it"),
    ("B", "chat", "section_align", "*", "*"): ("since", "BUILD 8a: the chat fixed the sections that day; NG Runner reads every section on the grid"),
    ("P05", "ours", "notch_check", "notch_face", "*"): ("later", "RULES 404-409: the notch face rule applies from 2026-09-22, after the chat's check; archived jobs aren't swept"),
    ("P05", "ours", "notch_check", "wm_banding", "*"): ("later", "BUILD 516: WM banding taped and locked from 2026-09-21, after the chat's check"),
    ("P20", "ours", "br_cam_check", "doubled_holes", "*"): ("later", "RULES 524-541: doubled housings, a check made after the chat's"),
    ("P20", "ours", "drill_depth_check", "bore_depth", "*"): ("chat", "Jim said that day these products are bridges; his checkers know a bridge only by BR in its name (BUILD 8b), so a question for Jim"),
    ("P20", "ours", P, "missing_part", "356.0x370.8"): ("chat", "the same unnamed bridges: RULES 0 item 3 matches a bridge by count"),
    ("P20", "ours", P, "extra_part", "266.3x356.0"): ("chat", "the same unnamed bridges: RULES 0 item 3 matches a bridge by count"),
    ("P20", "ours", P, "missing_part", "574.0x774.7"): ("chat", "RULES 659-661: count and sizes overall; the hamper front is built as a door"),
    ("P20", "ours", P, "extra_part", "573.0x774.8"): ("chat", "RULES 659-661: count and sizes overall; the hamper front is built as a door"),
    ("P20", "ours", P, "missing_part", "188.0x622.3"): ("can't tell", "drawer fronts at other heights than IC's; the chat called them exact before the rooms were saved again"),
    ("P20", "ours", P, "extra_part", "184.8x622.4"): ("can't tell", "the same drawer fronts"),
    ("P20", "ours", P, "extra_part", "194.7x622.4"): ("can't tell", "the same drawer fronts"),
    ("P20", "chat", P, "missing_part", "88.9x2438.4"): ("later", "RULES 245-247: rail covers disregarded (Jim, 2026-10-19), after the chat's check"),
    ("P26", "ours", P, "extra_part", "356.0x2356.0"): ("ours", "the vertical both found, there now at another depth than IC's 13 in (RULES 659-661)"),
    ("P26", "ours", P, "extra_part", "6.0x2356.0"): ("chat", "RULES 103 and 667: lighting is third party and hardware is left out"),
    ("P26", "ours", P, "missing_part", "63.5x370.8"): ("can't tell", "toe kicks of the bays whose shelves both found missing; the chat named no toes"),
    ("P26", "ours", P, "extra_part", "253.6x356.0"): ("can't tell", "a top and bottom on an empty bay, which the chat calls Mozaik's structure; no line of RULES says so"),
    ("P26", "ours", P, "extra_part", "254.0x356.0"): ("can't tell", "the same"),
    ("P26", "ours", P, "extra_part", "63.5x253.6"): ("can't tell", "the same"),
    ("P26", "ours", P, "extra_part", "63.5x254.0"): ("can't tell", "the same"),
    ("P26", "chat", P, "missing_part", "356.0x607.1"): ("jim", "Jim set a section of this job aside for the chat's count; NG Runner can't know it"),
    ("P26", "chat", P, "missing_part", "254.0x2028.0"): ("since", "NG Runner reads every back within tolerance in the rooms as saved since"),
    ("P28", "ours", P, "missing_part", "76.2x688.3"): ("chat", "RULES 258-264 and 659-661: the cleat is in Room0, counted under another family"),
    ("P28", "ours", P, "extra_part", "76.2x689.0"): ("chat", "the same cleat"),
    ("P28", "ours", P, "missing_part", "124.5x497.8"): ("chat", "BUILD 373-374: a loose part built up to the next clean size, noted, never flagged"),
    ("P28", "ours", P, "extra_part", "127.0x501.6"): ("chat", "the same hook board"),
    ("P28", "ours", P, "extra_part", "63.5x689.0"): ("jim", "an extra toe kick Jim said is fine on this job"),
    ("P28", "ours", P, "extra_part", "356.0x689.0"): ("can't tell", "a top the chat calls Mozaik's structure; no line of RULES says so"),
    ("P30", "ours", "br_cam_check", "doubled_holes", "*"): ("later", "RULES 524-541: doubled housings, a check made after the chat's"),
    ("P30", "ours", "section_align", "stale_rows", "*"): ("ours", "BUILD 416-422: a hard failure until a re-run proves otherwise, the re-run the chat asked for"),
    ("P30", "ours", P, "extra_part", "104.8x2438.4"): ("chat", "RULES 245-247: a Hang Rail is never in a panel check, though RULES 662-665 counts it"),
    ("P30", "ours", P, "missing_part", "393.7x1272.5"): ("ours", "RULES 666: a worksurface 2.7 mm off, past the 1.5 mm tolerance"),
    ("P30", "ours", P, "extra_part", "393.7x1269.8"): ("ours", "the same worksurface"),
    ("P30", "chat", P, "chat", "*"): ("since", "the sections were added since; NG Runner reads the panel set as tying but the worksurface"),
    ("P30", "chat", "section_span", "span", "R6C4"): ("since", "NG Runner reads every bay filled in the rooms as saved since"),
    ("P33", "ours", "notch_check", "notch_face", "*"): ("later", "RULES 404-409: the notch face rule applies from 2026-09-22, after the chat's check"),
    ("P33", "chat", "notch_check", "wm_banding", "*"): ("since", "the chat had the fix ready; NG Runner reads the WM banding taped and locked, its panels renumbered since"),
    ("P35", "chat", P, "missing_part", "607.1x985.5"): ("since", "the sections' depth was fixed since; NG Runner reads the shelves at 606"),
    ("P39", "ours", "br_cam_check", "doubled_holes", "*"): ("later", "RULES 524-541: doubled housings, a check made after the chat's"),
    ("P39", "ours", "parm_check", "parameters", ""): ("later", "RULES 527-535: JointFast2 is 1 since the cams were found doubled, after the chat's check; an existing job keeps its own"),
    ("P39", "ours", P, "extra_part", "104.8x2438.4"): ("chat", "RULES 245-247: a Hang Rail is never in a panel check, though RULES 662-665 counts it"),
    ("P39", "chat", "drill_depth_check", "bore_depth", "*"): ("since", "the chat said a recalc in Mozaik would clear them; NG Runner reads every bore depth right since"),
}


def _replayed(runs) -> dict[str, trial.Compared]:
    out = {}
    for tag, (covers, raised, outside) in CHAT.items():
        findings = {trial.Finding(*f) for f in raised} | {trial.Finding(trial.OUTSIDE, "item", str(n))
                                                           for n in range(outside)}
        out[tag] = trial.compare(checked(runs, tag), trial.ChatReport(covers, frozenset(findings)))
    return out


def _ruling(tag: str, side: str, finding: trial.Finding) -> tuple[str, str] | None:
    for part in (finding.part, "*"):
        for what in (finding.what, "*"):
            found = RULED.get((tag, side, finding.checker, what, part))
            if found:
                return found
    return None


def test_the_chats_reports_compared_with_every_disagreement_ruled(runs) -> None:
    replayed = _replayed(runs)
    used, unruled = set(), []
    for tag, compared in replayed.items():
        for side, found in (("ours", compared.ours_only), ("chat", compared.chat_only)):
            for finding in found:
                ruling = _ruling(tag, side, finding)
                if ruling is None:
                    unruled.append((tag, side, finding))
                used |= {key for key in RULED if key[:3] == (tag, side, finding.checker)
                         and key[3] in (finding.what, "*") and key[4] in (finding.part, "*")}

    assert unruled == [] and set(RULED) - set(JIMS_RULED) == used
    assert trial.counts(replayed.values()) == {"both": 4, "ours_only": 106, "chat_only": 25, "unverified": 17,
                                               "outside": 17}


JIMS_RULED = {key for key in RULED if key[0] == "D"}


def test_the_replay_against_p3s_exit_misses_nothing_and_shows_where_the_noise_is(runs) -> None:
    replayed = _replayed(runs)
    rulings = collections.Counter()
    noise = collections.Counter()
    for tag, compared in replayed.items():
        for side, found in (("ours", compared.ours_only), ("chat", compared.chat_only)):
            for finding in found:
                verdict, _ = _ruling(tag, side, finding)
                rulings[(side, verdict)] += 1
                noise[tag] += side == "ours" and verdict == "chat"

    # Missed: a finding the chat raised that is real and NG Runner didn't raise. None: each was fixed in the
    # rooms since, superseded by a later rule, or Jim's word on that job.
    assert not [k for k in rulings if k[0] == "chat" and k[1] in ("chat", "ours", "can't tell")]
    assert rulings == {("ours", "later"): 76, ("ours", "ours"): 5, ("ours", "chat"): 15, ("ours", "can't tell"): 9,
                       ("ours", "jim"): 1, ("chat", "since"): 23, ("chat", "later"): 1, ("chat", "jim"): 1}
    # Noise: NG Runner's findings the chat was right to leave out, by job (design 7: at most 1 a job). P35's TR
    # Base pair (2) left with the known noise below.
    assert {tag: n for tag, n in noise.items() if n} == {"P20": 8, "P26": 1, "P28": 4, "P30": 1, "P39": 1}


KNOWN_NOISE = {   # tag: (checker, what, parts or parameters), on every real job, as measured on 2026-11-01
    "E": [("parm_check", fd.SPARSE_UPPERS, 1)],          # RULES 604-614: Jim's sparse uppers
    "P35": [(P, fd.TR_BASE_4_1, 4)],                      # RULES 266-268, on an older IC Conversion
    "X": [(P, fd.TR_BASE_4_1, 1)],                        # the same, on his frozen job's own
}


def test_known_noise_by_jims_rules_is_counted_on_each_job_and_is_never_an_item(runs) -> None:
    measured = {}
    for tag, (_, result, rooms) in runs.items():
        if result is None:
            continue
        for kind in ("check", "audit"):
            found = fd.findings_of(result, kind=kind, room_files=rooms)
            if found.noise:
                measured.setdefault(kind, {})[tag] = [(n.checker, n.what, n.count) for n in found.noise]

    assert measured == {"check": KNOWN_NOISE, "audit": KNOWN_NOISE}
    tr_base = [s for i in checked(runs, "P35").items for s in i.sizes if s[:2] in ((104.1, 2438.4), (101.6, 2438.4))]
    assert tr_base == [] and not [i for i in checked(runs, "E").items if i.checker == "parm_check" and i.kind == "fix"]


# ----- the audit of chat-built jobs: the panel set, the top line, the operations and the parameters


AUDITED = {   # job set: (audited, not run for want of an IC Conversion, {(kind, checker, what): (items, parts)})
    # E's SysHolesW and the TR Base pairs of P35 and X are known noise: E's parameters wait for Q1 as a
    # matching file's do, and X's panel set, whose one pair it was, passes.
    "A to J": (10, 0, {("fix", P, "missing_part"): (1, 1), ("fix", P, "extra_part"): (2, 2),
                       ("fix", "parm_check", "parameters"): (5, 5), ("fix", "top_line", "top_line"): (1, 1),
                       ("could_not_verify", P, "checker"): (3, 3), ("could_not_verify", "parm_check", "q1"): (10, 10),
                       ("could_not_verify", "op_schema_check", "op_by_hand"): (2, 2),
                       ("could_not_verify", "room_top", "top_as_built"): (1, 1)}),
    "P5": (32, 8, {("fix", P, "missing_part"): (8, 41), ("fix", P, "extra_part"): (7, 21),
                   ("fix", "parm_check", "parameters"): (32, 32), ("fix", "top_line", "top_line"): (4, 7),
                   ("could_not_verify", P, "checker"): (17, 17), ("could_not_verify", "parm_check", "q1"): (25, 25),
                   ("could_not_verify", "op_schema_check", "op_by_hand"): (7, 7),
                   ("could_not_verify", "room_top", "top_as_built"): (3, 3)}),
    "frozen": (3, 0, {("fix", "parm_check", "parameters"): (3, 3), ("could_not_verify", P, "checker"): (1, 1),
                      ("could_not_verify", "parm_check", "q1"): (3, 3),
                      ("could_not_verify", "op_schema_check", "op_by_hand"): (1, 1),
                      ("could_not_verify", "room_top", "top_as_built"): (1, 1)}),
}


def test_the_audit_of_every_real_job_counts_by_kind_and_job_set(runs) -> None:
    measured = {}
    for job_set in AUDITED:
        tags = [tag for tag, (kind, _, _) in runs.items() if kind == job_set]
        audited = [tag for tag in tags if runs[tag][1] is not None]
        counts: dict[tuple[str, str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
        for tag in audited:
            found = checked(runs, tag, kind="audit")
            assert found.code in (FAILED, COULD_NOT_VERIFY)            # Q1 open: no audit passes whole
            assert {v.checker for v in found.verdicts} == {*fd.AUDIT_CHECKERS, fd.ROOM_FILES, fd.ROOM_TOP}
            for item in found.items:
                counts[(item.kind, item.checker, item.what)][0] += 1
                counts[(item.kind, item.checker, item.what)][1] += max(1, len(item.tags) + len(item.sizes))
        measured[job_set] = (len(audited), len(tags) - len(audited), {k: tuple(v) for k, v in counts.items()})

    assert measured == AUDITED


def test_every_audit_item_reads_in_jims_format_with_a_chat_line_and_names_no_client(runs) -> None:
    from ngrunner.messages import Message, templates
    from ngrunner.messages.lint import lint

    job = "Testerson_101426"
    names = {name.split("_")[0] for key in ("p1b/case_key.json", "reader/p5jobs/key.json")
             for name in json.loads((FIXTURES / key).read_text(encoding="utf-8")).values()}
    for tag, (_, result, _) in runs.items():
        if result is None:
            continue
        found = checked(runs, tag, kind="audit")
        lines = tuple(f"{i.n}. {templates.check_item_line(job, i)}" for i in found.items)
        assert lint(Message("check_result", f"{job} build checked 9:42 PM (trial)", lines)) == [], tag
        assert all(templates.check_chat_line(job, i) for i in found.items if i.kind == "fix"), tag
        shown = "\n".join(lines) + json.dumps(fd.to_json(found))
        assert not [name for name in names if len(name) > 3 and name in shown], tag


def test_the_parameters_that_differ_from_the_template_are_jointfast2_on_all_but_one_job(runs) -> None:
    # RULES 527-535: JointFast2 = 1 (None) in the template since the cams were found doubled, and "an
    # existing job keeps its own copy". The one other job carries SysHolesW 4, Jim's sparse uppers (RULES
    # 604-614), which NG Runner reads as known noise (test_known_noise_...). BTop, WTop and BotNail wait for Q1.
    drifted: dict[str, set[str]] = {}
    for tag, (_, result, _) in runs.items():
        verdict = None if result is None else next(v for v in result.verdicts if v.checker == "parm_check")
        if verdict is not None and verdict.code == FAILED:
            named = set(re.findall(r"(?m)^        (\S+) +.+ this job = .* reference = .*\(want .*\)", verdict.text))
            drifted[tag] = named - fd.TOP_PARAMETERS - {"?"}

    assert len(drifted) == 41 and drifted.pop("E") == {"SysHolesW"}
    assert set(map(frozenset, drifted.values())) == {frozenset({"JointFast2"})}


def test_ng_runners_own_top_line_agrees_with_jims_on_every_room_both_read(runs) -> None:
    agree = collections.Counter()
    for tag, (_, result, rooms) in runs.items():
        if result is None:
            continue
        his = {t for i in fd.findings_of(result, kind="audit").items if i.what == "top_line" for t in i.tags}
        for top in topline.rooms_top(rooms):
            ours = top.code == FAILED and bool({topline.OFF_TOP, topline.TWO_TOPS} & set(top.found))
            agree[(ours, f"R{top.room}" in his, top.code)] += 1

    assert agree == {(True, True, FAILED): 8, (False, False, PASSED): 47, (False, False, topline.NONE): 39,
                     (False, False, COULD_NOT_VERIFY): 5}
