r"""
Makes the delivery for Jim's toolkit: scripts\ng_convert.py, one file holding NG Runner's converter and
workbook, offered only when his own regression is clean on a staged copy of his toolkit with it added.

Why one file of NG Runner's modules, unchanged: the converter is NG Runner's code, and his chat should
run exactly what NG Runner runs, so the two can't drift. The file embeds each module NG Runner's
converter needs (found by reading imports with ast, from tools/delivery/front.py down), byte for byte,
with its sha256; a small loader serves them as modules and refuses any whose bytes changed. It needs
the standard library, pypdf and openpyxl, nothing of NG Runner's build, nothing tied to Windows.

Why offered only on a clean regression: Jim's RULES section 8 adopts a script change only when
regression.py is clean, and his approval (2026-10-31) lets NG Runner's tools in as reviewed deliveries,
through that gate. offer() lays his toolkit out as his tree in a work folder on this PC's disk, adds the
script, and adopts that copy exactly as NG Runner adopts one each night (toolkit.try_adoption): his
regression.py run on that very copy, clean only on his clean line. Anything else offers nothing. Then
it runs the script on his three frozen summaries, from that copy, and records what his takeoff.py
says of its parts beside what baseline.json holds.

Where it writes: the delivery folder only, by default .local-fixtures\delivery, which git ignores.
Never into a toolkit (a folder named _NG_Automation, or one holding scripts\regression.py), never in
Mozaik Shared, NG Jobs or NG Conversion program Claude, never on a drive that isn't this PC's own disk
(Google Drive for desktop's G: included), and never elsewhere in the repo. His toolkit is only read.
Sulaiman delivers it.

    python -m tools.delivery.package [--toolkit <folder>] [--out <folder>] [--python <tools' Python>]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ngrunner.clock import Clock, SystemClock  # noqa: E402 - after the path is set
from ngrunner.toolkit import (ADOPTED, Regression, Snapshot, ToolkitError,  # noqa: E402
                              on_local_disk, run_regression, run_tool, try_adoption)

ENTRY = "tools.delivery.front"
SCRIPT = "ng_convert.py"
ALLOWED = frozenset({"pypdf", "openpyxl"})
OURS = ("ngrunner", "tools")
DELIVERY = ROOT / ".local-fixtures" / "delivery"
NEVER_IN = ("Mozaik Shared", "NG Jobs", "NG Conversion program Claude")
_ZIP_PARTS = {"NG_Automation_scripts": "scripts", "NG_Automation_reference": "reference",
              "NG_Automation_templates": "templates"}


class PackagingRefused(Exception):
    """Why no delivery was made, in plain words."""


# ----- the modules it carries


@dataclass(frozen=True)
class Module:
    name: str
    path: Path | None            # None: a folder with no __init__.py, served as an empty package
    package: bool

    @property
    def source(self) -> str:
        """The module's text as git holds it, LF line endings (.gitattributes), whatever the checkout:
        Python reads a string literal's line endings as LF anyway, so its hash must be taken so."""
        return self.path.read_bytes().decode("utf-8").replace("\r\n", "\n") if self.path else ""


def _find(name: str) -> Module | None:
    parts = name.split(".")
    if parts[0] not in OURS:
        return None
    folder = ROOT.joinpath(*parts)
    if (folder / "__init__.py").is_file():
        return Module(name, folder / "__init__.py", True)
    if folder.is_dir():
        return Module(name, None, True)
    file = ROOT.joinpath(*parts[:-1], parts[-1] + ".py")
    return Module(name, file, False) if file.is_file() else None


def _imported(module: Module) -> set[str]:
    """Every absolute name module imports, at the top or inside a function, and for from-imports the
    names under it too (a submodule is imported that way)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(module.source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise PackagingRefused(f"{module.name} uses a relative import, which the delivery doesn't carry.")
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def modules(entry: str = ENTRY) -> list[Module]:
    """The modules the delivered script runs, from entry down, with every package above each one."""
    found: dict[str, Module] = {}
    todo = [entry]
    while todo:
        name = todo.pop()
        if name in found:
            continue
        module = _find(name)
        if module is None:
            continue
        found[name] = module
        parts = name.split(".")
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]
        todo += sorted(n for n in _imported(module) if n.split(".")[0] in OURS)
    return [found[name] for name in sorted(found)]


def third_party(found: Iterable[Module] | None = None) -> set[str]:
    """The packages outside the standard library and NG Runner the carried modules import."""
    names: set[str] = set()
    for module in found if found is not None else modules():
        names |= {n.split(".")[0] for n in _imported(module)}
    return {n for n in names if n and n not in sys.stdlib_module_names and n not in OURS}


def version(found: Iterable[Module] | None = None) -> str:
    """12 hex characters naming the carried sources: the same code, the same version."""
    lines = [f"{m.name}\t{hashlib.sha256(m.source.encode('utf-8')).hexdigest()}"
             for m in (found if found is not None else modules())]
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()[:12]


# ----- the script


_HEAD = r'''r"""ng_convert.py - an IC summary to its IC Conversion, the .json and the .xlsx, by code: no part is typed.

    python scripts/ng_convert.py <NG Jobs>/<Job>/<Job>_summary.pdf
        writes <Job>_IC_Conversion.json and <Job>_IC_Conversion.xlsx beside the summary
    python scripts/ng_convert.py <summary.pdf> --out <folder>    the same, in <folder>
    python scripts/ng_convert.py <summary.pdf> --job <Job>       and refuses a summary of another job
    python scripts/ng_convert.py <summary.pdf> --replace         writing over the two files if there
    python scripts/ng_convert.py --version                       which ng_convert this is

Exit 0 = written, takeoff complete. 3 = written, with lines it could not verify: each one is listed,
never filled in, and is an item for Jim. 1 = nothing written; it says why. 2 = a wrong command line.

Every run, before writing, it checks itself against takeoff.py, parse_pulled.py and classify.py beside
it: takeoff.py's rules must be the ones it was made from, parse_pulled.py (pdfplumber) must read every
board line of the PULLED list the same, and takeoff.py must read its parts to the same count, area and
tape. Otherwise it writes nothing and says so. Closets only: a garage or mixed job is refused.

NG Runner's converter, packaged for this toolkit by Sulaiman: version @VERSION@, made @MADE@. Below the
loader are NG Runner's own modules, unchanged, each checked against its sha256 before it runs. Never
edit this file: ask Sulaiman for a new one.
"""
import hashlib
import importlib
import importlib.abc
import importlib.util
import linecache
import os
import sys
from pathlib import Path

VERSION = "@VERSION@"
_PACKAGES = @PACKAGES@
_SHA256 = @SHA256@
_SOURCES = {}


class _Changed(ImportError):
    """A carried module whose bytes are not the ones it was made with."""


class _Carried(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves the modules below as ordinary modules, each only as made."""

    def find_spec(self, name, path=None, target=None):
        if name not in _SOURCES:
            return None
        return importlib.util.spec_from_loader(name, self, origin=f"ng_convert:{name}",
                                               is_package=name in _PACKAGES)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__
        source = _SOURCES[name]
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != _SHA256[name]:
            raise _Changed(f"ng_convert.py was changed after it was made ({name}), so nothing was converted."
                           " Ask Sulaiman for a new one.")
        filename = f"<ng_convert {name}>"
        linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
        exec(compile(source, filename, "exec"), module.__dict__)


def _main():
    if "ngrunner" in sys.modules or "tools" in sys.modules:
        print("ng_convert must run as its own script (python scripts/ng_convert.py ...), so nothing was converted.")
        return 1
    sys.meta_path.insert(0, _Carried())
    try:
        from tools.delivery import front
        if sys.argv[1:] == ["--version"]:
            for name in sorted(_SOURCES):
                importlib.import_module(name)
            print(f"ng_convert {VERSION}: {len(_SOURCES)} modules, each as made.")
            return 0
    except _Changed as why:
        print(why)
        return 1
    except ImportError as why:
        print(f"{why.name or 'A package it needs'} isn't installed in this Python, so nothing was converted.")
        return 1
    front.VERSION = VERSION
    return front.main(sys.argv[1:], scripts=Path(os.path.abspath(__file__)).parent)


# ===== NG Runner's modules, unchanged. Each: its name, then its source.
'''

_TAIL = '''

if __name__ == "__main__":
    sys.exit(_main())
'''


def build_script(*, made: str) -> str:
    """The delivered file's text, for the modules as they are now."""
    found = modules()
    unsafe = [m.name for m in found if "'''" in m.source or m.source.endswith("\\")]
    if unsafe:
        raise PackagingRefused(f"{', '.join(unsafe)} can't be carried as written (a ''' in it).")
    extra = third_party(found) - ALLOWED
    if extra:
        raise PackagingRefused(f"The converter now needs {', '.join(sorted(extra))}, which the delivery doesn't carry.")
    v = version(found)
    packages = "{\n" + "".join(f"    {m.name!r},\n" for m in found if m.package) + "}"
    hashes = "{\n" + "".join(f"    {m.name!r}: {hashlib.sha256(m.source.encode('utf-8')).hexdigest()!r},\n"
                             for m in found) + "}"
    head = (_HEAD.replace("@VERSION@", v).replace("@MADE@", made).replace("@PACKAGES@", packages)
            .replace("@SHA256@", hashes))
    body = "".join(f"\n_SOURCES[{m.name!r}] = r'''{m.source}'''\n" for m in found)
    return head + body + _TAIL


# ----- where it may be written


def refuse_out(out: Path, *, repo: Path = ROOT) -> None:
    """Raises PackagingRefused unless out is a place a delivery may be written: never a toolkit, never
    where Jim's work lives, never off this PC's own disk, and in the repo only under .local-fixtures."""
    out = Path(os.path.abspath(out))
    chain = (out, *out.parents)
    if any(folder.name == "_NG_Automation" or (folder / "scripts" / "regression.py").is_file()
           or (folder.name == "scripts" and (folder / "regression.py").is_file()) for folder in chain):
        raise PackagingRefused("The delivery is never written into a toolkit: Sulaiman delivers it by hand.")
    named = {folder.name.casefold() for folder in chain}
    for name in NEVER_IN:
        if name.casefold() in named:
            raise PackagingRefused(f"The delivery is never written in {name}.")
    if not on_local_disk(out):
        raise PackagingRefused("The delivery is written only on this PC's local disk, never on Drive or a"
                               " network drive.")
    inside = os.path.normcase(str(out))
    root, fixtures = os.path.normcase(str(repo)), os.path.normcase(str(repo / ".local-fixtures"))
    if (inside == root or inside.startswith(root + os.sep)) and not (
            inside == fixtures or inside.startswith(fixtures + os.sep)):
        raise PackagingRefused("The delivery would be in the repo where git would take it: give a folder in"
                               " .local-fixtures, or outside the repo.")


# ----- his toolkit, staged


def stage_toolkit(source: Path, into: Path) -> Path:
    """His toolkit laid out as his tree, _NG_Automation, in into (which must be new): from the four
    parts his zip came as, or from a tree that is already laid out. Only ever reads source."""
    tree = into / "_NG_Automation"
    if (source / "NG_Automation_scripts").is_dir():
        tree.mkdir(parents=True)
        for part, folder in _ZIP_PARTS.items():
            shutil.copytree(source / part / folder, tree / folder)
        for doc in sorted((source / "NG_Automation_docs").iterdir()):
            if doc.is_file():
                shutil.copyfile(doc, tree / doc.name)
    elif (source / "scripts").is_dir():
        shutil.copytree(source, tree)
    else:
        raise PackagingRefused("That folder isn't Jim's toolkit: it has no scripts folder.")
    return tree


# ----- the offer


@dataclass(frozen=True)
class Offer:
    offered: bool
    words: str
    files: tuple[Path, ...] = ()
    manifest: dict[str, Any] = field(default_factory=dict)


Frozen = Callable[[Snapshot, Path], list[dict[str, Any]]]


def offer(*, toolkit: Path, out: Path, work: Path, clock: Clock, regress: Callable[[Snapshot], Regression],
          made: str, frozen: Frozen | None = None, score: str | None = None) -> Offer:
    """The delivery, written into out/ng_convert-<version>, only when his regression is clean on a
    copy of his toolkit with the script added. work: a new folder on this PC's disk for the copies."""
    refuse_out(out)
    found = modules()
    script = build_script(made=made)
    tree = stage_toolkit(toolkit, work / "staged")
    with open(tree / "scripts" / SCRIPT, "x", encoding="utf-8", newline="\n") as f:
        f.write(script)
    tried = try_adoption(tree, work / "toolkit", adopted=None, clock=clock, regress=regress)
    if tried.outcome != ADOPTED or tried.sha256 is None or tried.taken_at is None:
        return Offer(False, f"Not offered: {tried.words}")
    copy = work / "toolkit" / tried.sha256
    snapshot = Snapshot(path=copy, sha256=tried.sha256, taken_at=tried.taken_at, files=0)
    results = frozen(snapshot, work / "frozen") if frozen else []
    manifest = {
        "version": version(found), "made": made,
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "modules": {m.name: hashlib.sha256(m.source.encode("utf-8")).hexdigest() for m in found},
        "needs": sorted(ALLOWED) + ["Jim's takeoff.py, parse_pulled.py (with pdfplumber) and classify.py beside it"],
        "regression": {"clean": True, "words": tried.words, "toolkit_copy_sha256": tried.sha256},
        "frozen": results,
    }
    folder = out / f"ng_convert-{manifest['version']}"
    folder.mkdir(parents=True, exist_ok=True)
    files = (folder / SCRIPT, folder / "NOTE-for-Jim.md", folder / "MANIFEST.json")
    texts = (script, note_for_jim(version=manifest["version"], made=made, regression="clean", frozen=results,
                                  score=score), json.dumps(manifest, indent=1) + "\n")
    for path, text in zip(files, texts):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return Offer(True, f"Offered: ng_convert {manifest['version']}. {tried.words}", files, manifest)


def frozen_jobs(snapshot: Snapshot, work: Path, *, python: Path | None,
                run: Callable[..., Any] = run_tool) -> list[dict[str, Any]]:
    """The script, run from the adopted copy on each of his frozen summaries, and what his takeoff.py
    said of its parts against baseline.json's takeoff layer. Jobs are named by position only."""
    regression = snapshot.path / "reference" / "regression"
    baseline = json.loads((regression / "baseline.json").read_text(encoding="utf-8"))
    results = []
    for n, job in enumerate(sorted(baseline), 1):
        entry: dict[str, Any] = {"job": f"frozen job {n}"}
        summary = regression / job / "summary.pdf"
        if not summary.is_file():
            results.append({**entry, "ran": False, "why": "no summary in the reference folder"})
            continue
        target = work / f"frozen-{n}"
        target.mkdir(parents=True)
        try:
            said = run(snapshot, f"scripts/{SCRIPT}", [str(summary), "--out", str(target)], timeout_s=600,
                       python=python)
        except ToolkitError as why:
            results.append({**entry, "ran": False, "why": str(why)})
            continue
        entry.update(ran=True, exit=said.exit_code)
        written = sorted(target.glob("*_IC_Conversion.json"))
        if written:
            conv = json.loads(written[0].read_text(encoding="utf-8"))
            mine, theirs = conv["ng_convert"].get("takeoff_py") or {}, baseline[job].get("takeoff") or {}
            entry.update(board_parts=conv["total_board_parts"], complete=conv["ng_convert"]["complete"],
                         takeoff_equals_baseline=mine == theirs,
                         differs=sorted(k for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k)),
                         board_parts_equal_baseline=mine.get("board_parts") == theirs.get("board_parts"))
        results.append(entry)
    return results


# ----- the note for Jim


def note_for_jim(*, version: str, made: str, regression: str, frozen: list[dict[str, Any]], score: str | None) -> str:
    same = sum(1 for f in frozen if f.get("takeoff_equals_baseline"))
    frozen_line = (f"- On your three frozen jobs, takeoff.py reads its parts exactly as baseline.json on {same} of"
                   f" {len(frozen)}; the board-part count is the same on"
                   f" {sum(1 for f in frozen if f.get('board_parts_equal_baseline'))}." if frozen else
                   "- Your three frozen jobs: not run with this delivery.")
    return f"""# ng_convert: a note for Jim

Version {version}, made {made} by Sulaiman for your toolkit. Its file goes in `_NG_Automation\\scripts\\`.

**What it does.** Give it a closet job's IC summary and it writes the job's IC Conversion, the `.json`
and the `.xlsx`, the same two files your chat writes today, in the same layout. It reads the parts off
the summary's PULLED list itself, so nobody types them and there is no per-job script to write.

**How your chat uses it.** One line, in place of writing `ic_conversion_<client>.py` and a workbook script:

    python scripts/ng_convert.py "<NG Jobs>/<Job>/<Job>_summary.pdf"

It writes `<Job>_IC_Conversion.json` and `.xlsx` beside the summary and says what it did. It is made
to run in your chat's Linux box as on Windows; so far it has been run on Windows only.

**It checks itself on every run, against your own scripts.** Your `parse_pulled.py` reads the summary a
second way and must find every board line the same. Your `takeoff.py` must still hold the rules it was
made from, and must count its parts to the same number, area and tape. If anything fails, it writes
nothing and says why. If you change `takeoff.py`, it stops until I make a new one: there is only ever
one set of rules, yours.

**What it never guesses.** A line your rules don't settle is never filled in. It is listed in the
`.json`, in the workbook under COULD NOT VERIFY, and in what it prints, and each one is an item for
you: today, for example, a part printed 0.6in thick, a hamper front, a drawer face printed at a
nominal size, or a rail cover with no colour on a two-colour job. A colour with no sheet size in RULES'
table says "could not verify" where the sheets would be. It never reads the client's name, address or
phone, so those rows stay blank.

**What it doesn't do yet.** Closets only: a garage or mixed job is refused, and converted as before.
It doesn't make the wall angles, the verticals or the runs; those still come from the drawings.
Sheet sizes follow RULES' table (Classic White 3/4 on 49x97), not Materials.dat, until you say which
is right. Tafisa colours are named like "Tafisa (IC SU)", not by their Tafisa name.

**What it was tested on.**
- Your `regression.py` is {regression} on a copy of your toolkit with ng_convert in it.
{frozen_line}
- {score or "Scored against your chat's own workbooks: see the report that came with it."}

**The RULES line it needs**, in RULES section 1, for your approval the section 8 way (Sulaiman's page
of RULES changes has it word for word, with three small ones beside it):

> **Never write a per-job script.** For a closet, run `python scripts\\ng_convert.py <summary>`: it
> writes the IC Conversion `.json` and `.xlsx`. Every line it lists as could not verify is a `d.act()`
> item for Jim, never typed in. If it refuses, say why and stop; never work around it. A garage or
> mixed job is converted as before.

Please look over the first 10 conversions it makes before relying on it.
"""


# ----- running it


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the ng_convert delivery for Jim's toolkit.")
    parser.add_argument("--toolkit", help="his toolkit: the newest .local-fixtures\\toolkit\\<date> by default")
    parser.add_argument("--out", help="where the delivery goes: .local-fixtures\\delivery by default")
    parser.add_argument("--python", help="the Python to run his scripts and ng_convert in (NGR_TOOLS_PYTHON by default)")
    parser.add_argument("--score", help="one line on how it scored against the chat's workbooks, for the note")
    args = parser.parse_args(argv)
    kits = sorted(p for p in (ROOT / ".local-fixtures" / "toolkit").glob("2*") if p.is_dir())
    kit = Path(args.toolkit) if args.toolkit else (kits[-1] if kits else None)
    given = args.python or os.environ.get("NGR_TOOLS_PYTHON", "")
    python = Path(given) if given else None
    if kit is None or python is None or not python.is_file():
        print("Name Jim's toolkit (--toolkit) and the tools' Python (--python or NGR_TOOLS_PYTHON).")
        return 2
    work = Path(tempfile.mkdtemp(prefix="ngr-delivery-"))
    try:
        offered = offer(toolkit=kit, out=Path(args.out) if args.out else DELIVERY, work=work, clock=SystemClock(),
                        regress=lambda s: run_regression(s, python=python), made=date.today().isoformat(),
                        frozen=lambda s, w: frozen_jobs(s, w, python=python), score=args.score)
    except PackagingRefused as why:
        print(why)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(offered.words)
    for result in offered.manifest.get("frozen", []):
        print(f"  {json.dumps(result)}")
    for path in offered.files:
        print(f"  {path}")
    return 0 if offered.offered else 1


if __name__ == "__main__":
    raise SystemExit(main())

