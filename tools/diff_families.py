r"""
The family diff: what Jim's per-job scripts hold, and where the helpers they copy from each other
drifted apart. Dev only and read-only (docs/p2/build-spec.md, contract 5.5).

Why it exists: Jim's chat writes a script per job, and copies its helpers from the last one, so
snap_depth has 7 bodies in 14 conversion scripts and imp 9 (section 3.4). Before one shared
converter replaces them, each difference has to be either a rule takeoff.py already holds or a
question for Jim (the handoff brief's risk 3: "some of those differences are rules nobody wrote
down"). This lists them; deciding which is which stays with a person.

It reads source text and parses it with ast, and never imports or runs a script: his code is his
(AGENTS.md rule 4), and a per-job script writes files at import. Comments are not in the tree, so
dates come from the text with a pattern.

Per family (the script's name up to its job, section 3.4's list, "shared" for the rest): each
function's name and three hashes, literal data lines against logic lines, imports, and hard-coded
paths. The three hashes tell drift from noise: text (the whole function), code (no docstring), and
shape (no docstring, and the function's own names numbered in order, so a renamed local doesn't
count). A helper drifted when one name has more than one text hash in a family, or across all
scripts ("all").

For a drifted helper it says which body is newest and whether each other body is a subset of it,
statement by statement on their shapes. "Newest" goes by a date: the newest conversion JSON in NG
Jobs of a job the script names, when --ng-jobs is given, else the newest date written in the
script. Neither is proof of when a body was written, so a tie or no date says so instead.

The report names clients (scripts and jobs are named after them), so it goes only to --out, which
must be outside this repo and outside the scripts' folder. It writes families.md and families.json
there and nothing else. It prints counts only.

Usage:  python tools\diff_families.py --out <folder> [--scripts <folder>] [--ng-jobs <NG Jobs>]
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLKIT = Path(os.environ.get("NGR_TOOLKIT", ROOT / ".local-fixtures" / "toolkit" / "20261031"))
FAMILIES = ("ic_conversion", "ic_workbook", "tracker", "build", "verify", "wb", "run",
            "panelcheck", "fix", "harvest", "swap")
NAMED = ("snap_depth", "imp", "snap_h", "eb")        # the drifted helpers section 3.4 names
_JOB = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Za-z-]*(?:_[A-Za-z]+)*_[0-9]{6}(?![0-9])")
_DATE = re.compile(r"(?<![0-9])20[0-9]{2}-[01][0-9]-[0-3][0-9](?![0-9])")
_LINUX = re.compile(r"^(?:/mnt/|/tmp/|/home/|/root/|/usr/|/var/)|/mnt/user-data|/tmp/claude")
_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)|\\Users\\|OneDrive")
_DATA_METHODS = frozenset({"append", "extend", "update", "insert", "add"})


# ----- one function


@dataclass(frozen=True)
class Function:
    name: str
    line: int
    text_hash: str
    code_hash: str
    shape_hash: str
    statements: tuple[str, ...]       # each statement's shape, in order
    shown: tuple[str, ...]            # the same statements as they read in the source
    reads: tuple[str, ...]            # module constants it reads, filled in by read_script


def read_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Function:
    code = _without_docstring(node)
    shape = _renamed(code)
    statements, shown = _statements(code, shape)
    return Function(node.name, node.lineno, _hash(_dump(node)), _hash(_dump(code)),
                    _hash(_dump(shape)), statements, shown, ())


def _dump(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The function without its name, place or decorators' places: what it does."""
    return ast.dump(ast.Module(body=[*node.body], type_ignores=[])) + ast.dump(node.args)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _without_docstring(node):
    copied = copy.deepcopy(node)
    body = copied.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        copied.body = body[1:] or [ast.Pass()]
    return copied


class _Renamer(ast.NodeTransformer):
    """The function's own names (arguments and whatever it assigns) as v0, v1... in the order
    first met, so two bodies that differ only in their names have one shape."""

    def __init__(self, own: set[str]) -> None:
        self.own, self.names = own, {}

    def _name(self, name: str) -> str:
        if name not in self.own:
            return name
        return self.names.setdefault(name, f"v{len(self.names)}")

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self._name(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._name(node.arg)
        self.generic_visit(node)
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.ExceptHandler:
        if node.name:
            node.name = self._name(node.name)
        self.generic_visit(node)
        return node


def _own_names(node) -> set[str]:
    own = {a.arg for a in ast.walk(node) if isinstance(a, ast.arg)}
    own |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
            and isinstance(n.ctx, (ast.Store, ast.Del))}
    own |= {h.name for h in ast.walk(node) if isinstance(h, ast.ExceptHandler) and h.name}
    declared = {name for n in ast.walk(node) if isinstance(n, (ast.Global, ast.Nonlocal))
                for name in n.names}
    return own - declared


def _renamed(node):
    copied = copy.deepcopy(node)
    return _Renamer(_own_names(copied)).visit(copied)


_COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try,
             ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Match)


def _statements(code, shape) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Every statement inside the function, as its shape and as it reads. A compound one is its
    first line only, so a line added inside an if is one more statement, not a changed if."""
    keys, shown = [], []
    pairs = zip(ast.walk(code), ast.walk(shape))
    next(pairs)                                   # the function itself
    for original, renamed in pairs:
        if not isinstance(original, ast.stmt):
            continue
        if isinstance(original, _COMPOUND):
            keys.append(type(renamed).__name__ + ":" + _header(renamed, dump=True))
            shown.append(_header(original, dump=False))
        else:
            keys.append(ast.dump(renamed))
            shown.append(ast.unparse(original))
    return tuple(keys), tuple(shown)


def _header(node, *, dump: bool) -> str:
    """A compound statement's first line, as text or as a dump of its parts."""
    if dump:
        parts = [getattr(node, name) for name in ("test", "target", "iter", "items", "args",
                                                  "subject", "name") if hasattr(node, name)]
        return "|".join(ast.dump(p) if isinstance(p, ast.AST) else
                        ",".join(ast.dump(x) for x in p) if isinstance(p, list) else str(p)
                        for p in parts)
    hollow = copy.copy(node)
    for name in ("body", "orelse", "finalbody", "handlers", "cases"):
        if hasattr(hollow, name):
            setattr(hollow, name, [ast.Pass()] if name == "body" else [])
    return ast.unparse(hollow).splitlines()[0]


# ----- one script


@dataclass(frozen=True)
class Script:
    name: str
    family: str
    problem: str                      # "" or why it was not read
    functions: tuple[Function, ...] = ()
    imports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    data_lines: int = 0
    logic_lines: int = 0
    paths: dict[str, tuple[str, ...]] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    jobs: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()


def family_of(name: str) -> str:
    """The family a script's name starts with, then "_"; panelcheck.py alone is a shared tool,
    as section 3.4 counts it (126 per-job, 57 shared)."""
    stem = name.removesuffix(".py")
    for family in FAMILIES:
        if stem.startswith(family + "_"):
            return family
    return "shared"


def read_script(path: Path, toolkit: Iterable[str] = ()) -> Script:
    """One script, read as text and parsed, never imported. toolkit is the names of the other
    scripts beside it, so an import of one of them is told from a library."""
    family = family_of(path.name)
    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=path.name)
    except (OSError, UnicodeDecodeError):
        return Script(path.name, family, "could not be read")
    except (SyntaxError, ValueError):
        return Script(path.name, family, "does not parse")
    constants = _constants(tree)
    functions = tuple(_with_reads(read_function(node), node, constants)
                      for node in ast.walk(tree)
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    data, logic = _line_counts(tree)
    texts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
             and isinstance(n.value, str)]
    return Script(
        name=path.name, family=family, problem="", functions=functions,
        imports=_imports(tree, set(toolkit)), data_lines=data, logic_lines=logic,
        paths={kind: found for kind, found in _paths(texts).items() if found},
        constants=constants,
        jobs=tuple(sorted({job for text in texts for job in _JOB.findall(text)})),
        dates=tuple(sorted(set(_DATE.findall(source)))),
    )


def _with_reads(function: Function, node, constants: Mapping[str, str]) -> Function:
    own = _own_names(node)
    reads = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Load) and n.id in constants and n.id not in own})
    return Function(**{**asdict(function), "reads": tuple(reads)})


def _constants(tree: ast.Module) -> dict[str, str]:
    """Module-level names given a literal value: the rules a helper reads."""
    found: dict[str, str] = {}
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) and node.value else [])
        if not targets or not _is_data(node.value):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = ast.unparse(node.value)
    return found


def _is_data(node: ast.AST | None) -> bool:
    """Literals only: numbers, strings, containers of them, sums of them, and calls on them (a
    part typed as P("SH", 14, 23.5))."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_data(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is None or _is_data(k) for k in node.keys) and all(
            _is_data(v) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        return _is_data(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_data(node.left) and _is_data(node.right)
    if isinstance(node, ast.Call):
        return all(_is_data(a) for a in node.args) and all(_is_data(k.value)
                                                           for k in node.keywords)
    return False


def _is_data_statement(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return node.value is not None and _is_data(node.value)
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        func = node.value.func
        return (isinstance(func, ast.Attribute) and func.attr in _DATA_METHODS
                and _is_data(ast.Tuple(elts=list(node.value.args), ctx=ast.Load())))
    return False


def _line_counts(tree: ast.Module) -> tuple[int, int]:
    """(data, logic) lines. A compound statement's own line is logic; docstrings are neither."""
    data: set[int] = set()
    logic: set[int] = set()
    docstrings = {id(body[0]) for owner in ast.walk(tree)
                  if isinstance(owner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef))
                  for body in [owner.body]
                  if body and isinstance(body[0], ast.Expr)
                  and isinstance(body[0].value, ast.Constant)
                  and isinstance(body[0].value.value, str)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt) or id(node) in docstrings:
            continue
        if isinstance(node, _COMPOUND):
            first = node.body[0].lineno if getattr(node, "body", None) else node.lineno + 1
            logic.update(range(node.lineno, max(first, node.lineno + 1)))
        elif _is_data_statement(node):
            data.update(range(node.lineno, node.end_lineno + 1))
        else:
            logic.update(range(node.lineno, node.end_lineno + 1))
    return len(data), len(logic - data)


def _imports(tree: ast.Module, toolkit: set[str]) -> dict[str, tuple[str, ...]]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    sorted_out: dict[str, list[str]] = {"standard": [], "toolkit": [], "other": []}
    for name in sorted(names):
        top = name.lstrip(".").split(".")[0]
        kind = ("toolkit" if top in toolkit or name.startswith(".")
                else "standard" if top in sys.stdlib_module_names else "other")
        sorted_out[kind].append(name)
    return {kind: tuple(found) for kind, found in sorted_out.items()}


def _paths(texts: Iterable[str]) -> dict[str, tuple[str, ...]]:
    linux, windows = set(), set()
    for text in texts:
        if "\n" in text or len(text) > 300:
            continue
        if _LINUX.search(text):
            linux.add(text)
        elif _WINDOWS.search(text):
            windows.add(text)
    return {"linux": tuple(sorted(linux)), "windows": tuple(sorted(windows))}


def analyse(folder: Path) -> list[Script]:
    """Every .py in folder, by name."""
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix == ".py")
    toolkit = {p.stem for p in files}
    return [read_script(path, toolkit) for path in files]


# ----- dates


def date_scripts(scripts: Sequence[Script], ng_jobs: Path | None = None
                 ) -> dict[str, tuple[str, str] | None]:
    """Each script's date and what it rests on, or None. A conversion JSON in NG Jobs of a job
    the script names wins; else the newest date written in the script."""
    saved = _conversion_dates(ng_jobs) if ng_jobs else {}
    out: dict[str, tuple[str, str] | None] = {}
    for script in scripts:
        dates = [saved[job] for job in script.jobs if job in saved]
        if dates:
            out[script.name] = (max(dates), "its job's conversion JSON")
        elif script.dates:
            out[script.name] = (script.dates[-1], "date in its text")
        else:
            out[script.name] = None
    return out


def _conversion_dates(ng_jobs: Path) -> dict[str, str]:
    """Job folder name to the day its newest IC Conversion JSON was saved, top level and Old."""
    found: dict[str, str] = {}
    for top in (ng_jobs, ng_jobs / "Old"):
        try:
            folders = [p for p in top.iterdir() if p.is_dir() and p.name != "Old"]
        except OSError:
            continue
        for folder in folders:
            try:
                times = [p.stat().st_mtime for p in folder.iterdir()
                         if p.suffix.lower() == ".json" and "ic_conversion" in p.name.lower()]
            except OSError:
                continue
            if times:
                day = datetime.fromtimestamp(max(times), timezone.utc).date().isoformat()
                found[folder.name] = max(found.get(folder.name, ""), day)
    return found


# ----- drift


@dataclass(frozen=True)
class Body:
    text_hash: str
    shape_hash: str
    files: tuple[str, ...]
    date: tuple[str, str] | None          # the newest of its files' dates, and its basis
    subset_of_newest: bool | None         # None for the newest itself, or with no newest
    only_here: tuple[str, ...]            # its statements the newest lacks
    only_in_newest: tuple[str, ...]       # the newest's statements it lacks


@dataclass(frozen=True)
class Drift:
    family: str                           # a family, or "all"
    name: str
    bodies: tuple[Body, ...]
    shapes: int
    newest: str | None                    # its text hash, None when the dates can't tell
    constants: dict[str, dict[str, tuple[str, ...]]]   # name -> value -> files


def drifts(scripts: Sequence[Script], dates: Mapping[str, tuple[str, str] | None]
           ) -> list[Drift]:
    """Every helper with more than one body, per family and across all scripts."""
    groups: dict[tuple[str, str], list[tuple[Script, Function]]] = {}
    for script in scripts:
        for function in script.functions:
            for family in (script.family, "all"):
                groups.setdefault((family, function.name), []).append((script, function))
    found = [_drift(family, name, members, dates)
             for (family, name), members in groups.items()
             if len({f.text_hash for _, f in members}) > 1]
    return sorted(found, key=lambda d: (d.family != "all", d.family, -len(d.bodies), d.name))


def _drift(family: str, name: str, members: list[tuple[Script, Function]],
           dates: Mapping[str, tuple[str, str] | None]) -> Drift:
    by_body: dict[str, list[tuple[Script, Function]]] = {}
    for script, function in members:
        by_body.setdefault(function.text_hash, []).append((script, function))
    dated = {key: max((dates.get(s.name) for s, _ in group if dates.get(s.name)), default=None)
             for key, group in by_body.items()}
    newest = _newest(dated)
    first = {key: group[0][1] for key, group in by_body.items()}
    bodies = []
    for key, group in sorted(by_body.items(), key=lambda kv: (dated[kv[0]] or ("", ""), kv[0]),
                             reverse=True):
        files = tuple(sorted(s.name for s, _ in group))
        if newest is None or key == newest:
            bodies.append(Body(key, first[key].shape_hash, files, dated[key], None, (), ()))
            continue
        here, there = first[key], first[newest]
        only_here = tuple(shown for stmt, shown in zip(here.statements, here.shown)
                          if stmt not in set(there.statements))
        only_newest = tuple(shown for stmt, shown in zip(there.statements, there.shown)
                            if stmt not in set(here.statements))
        bodies.append(Body(key, here.shape_hash, files, dated[key], not only_here, only_here,
                           only_newest))
    constants: dict[str, dict[str, list[str]]] = {}
    for script, function in members:
        for constant in function.reads:
            value = script.constants[constant]
            constants.setdefault(constant, {}).setdefault(value, []).append(script.name)
    return Drift(family, name, tuple(bodies), len({b.shape_hash for b in bodies}), newest,
                 {c: {v: tuple(sorted(f)) for v, f in values.items()}
                  for c, values in sorted(constants.items())})


def _newest(dated: Mapping[str, tuple[str, str] | None]) -> str | None:
    known = {key: date[0] for key, date in dated.items() if date}
    if not known:
        return None
    latest = max(known.values())
    top = [key for key, day in known.items() if day == latest]
    return top[0] if len(top) == 1 else None


# ----- the report


def summary(scripts: Sequence[Script]) -> dict[str, dict]:
    """Per family: files, lines, imports, paths and each function's bodies."""
    out: dict[str, dict] = {}
    for family in (*FAMILIES, "shared"):
        members = [s for s in scripts if s.family == family]
        if not members:
            continue
        imports: dict[str, Counter] = {"standard": Counter(), "toolkit": Counter(),
                                       "other": Counter()}
        functions: dict[str, dict[str, list[str]]] = {}
        for script in members:
            for kind, names in script.imports.items():
                imports[kind].update(names)
            for function in script.functions:
                functions.setdefault(function.name, {}).setdefault(
                    function.text_hash, []).append(script.name)
        out[family] = {
            "files": len(members),
            "unreadable": [s.name for s in members if s.problem],
            "data_lines": sum(s.data_lines for s in members),
            "logic_lines": sum(s.logic_lines for s in members),
            "imports": {kind: dict(counter.most_common()) for kind, counter in imports.items()},
            "files_with_linux_paths": sum(1 for s in members if "linux" in s.paths),
            "files_with_windows_paths": sum(1 for s in members if "windows" in s.paths),
            "functions": {name: bodies for name, bodies in sorted(functions.items())},
        }
    return out


def write_report(out: Path, scripts: Sequence[Script], found: Sequence[Drift],
                 dates: Mapping[str, tuple[str, str] | None]) -> None:
    families = summary(scripts)
    data = {"families": families, "scripts": [asdict(s) for s in scripts],
            "dates": dates, "drifts": [asdict(d) for d in found]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "families.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8", newline="\n")
    (out / "families.md").write_text(_markdown(families, scripts, found, dates),
                                     encoding="utf-8", newline="\n")


def _markdown(families: Mapping[str, dict], scripts: Sequence[Script], found: Sequence[Drift],
              dates: Mapping[str, tuple[str, str] | None]) -> str:
    lines = ["# Family diff (local only: names clients)", "",
             f"{len(scripts)} scripts. Made {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by"
             " tools/diff_families.py, by ast; nothing was imported or run.", "",
             "## Families", "",
             "| Family | Files | Data lines | Logic lines | Linux paths | Windows paths |"
             " Libraries |", "|---|---|---|---|---|---|---|"]
    for family, info in families.items():
        libraries = ", ".join(f"{k} {v}" for k, v in info["imports"]["other"].items())
        lines.append(f"| {family} | {info['files']} | {info['data_lines']} |"
                     f" {info['logic_lines']} | {info['files_with_linux_paths']} |"
                     f" {info['files_with_windows_paths']} | {libraries} |")
    lines += ["", "## Drifted helpers", "",
              "Text: whole function. Shape: without docstring, its own names numbered. A body is"
              " a subset of the newest when every statement of its shape is in the newest's.", ""]
    named = [d for d in found if d.name in NAMED and d.family != "all"]
    rest = [d for d in found if d not in named]
    for drift in named + rest:
        lines += _drift_lines(drift)
    lines += ["", "## Dates", "", "| Script | Date | Basis |", "|---|---|---|"]
    lines += [f"| {name} | {d[0] if d else ''} | {d[1] if d else 'no date'} |"
              for name, d in sorted(dates.items())]
    return "\n".join(lines) + "\n"


def _drift_lines(drift: Drift) -> list[str]:
    files = sum(len(b.files) for b in drift.bodies)
    head = (f"### {drift.name} ({drift.family}): {len(drift.bodies)} bodies, {drift.shapes}"
            f" shapes, in {files} files")
    out = [head, "", "Newest: " + (drift.newest or "could not tell (no dates, or a tie)"), ""]
    for body in drift.bodies:
        when = f"{body.date[0]} ({body.date[1]})" if body.date else "no date"
        subset = ("newest" if body.text_hash == drift.newest
                  else "-" if body.subset_of_newest is None
                  else "subset of newest" if body.subset_of_newest else "NOT a subset")
        out.append(f"- `{body.text_hash}` shape `{body.shape_hash}`, {when}, {subset}:"
                   f" {', '.join(body.files)}")
        out += [f"    - only here: `{line}`" for line in body.only_here]
        out += [f"    - only in newest: `{line}`" for line in body.only_in_newest]
    for name, values in drift.constants.items():
        if len(values) > 1:
            out.append(f"- constant {name}: " + "; ".join(
                f"`{value[:80]}` in {len(files)}" for value, files in values.items()))
    return out + [""]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff Jim's script families, read-only.")
    parser.add_argument("--out", required=True, help="a folder outside the repo for the report")
    parser.add_argument("--scripts", default=str(TOOLKIT / "NG_Automation_scripts" / "scripts"))
    parser.add_argument("--ng-jobs", help="NG Jobs, to date scripts by their job's JSON")
    args = parser.parse_args(argv)
    scripts_dir, out = Path(args.scripts).resolve(), Path(args.out).resolve()
    if out.is_relative_to(ROOT) or out.is_relative_to(scripts_dir):
        print("The report names clients, so it must go outside the repo and outside the"
              " scripts' folder.", file=sys.stderr)
        return 1
    try:
        scripts = analyse(scripts_dir)
        dates = date_scripts(scripts, Path(args.ng_jobs) if args.ng_jobs else None)
        found = drifts(scripts, dates)
        write_report(out, scripts, found, dates)
    except OSError as err:          # its text holds a path, and a path can name a client
        print(f"Could not make the report: {type(err).__name__}", file=sys.stderr)
        return 1
    per_family = Counter(s.family for s in scripts)
    print(f"{len(scripts)} scripts; " + ", ".join(f"{k} {v}" for k, v in per_family.items()))
    print(f"{sum(1 for d in found if d.family != 'all')} drifted helpers within families,"
          f" {sum(1 for d in found if d.family == 'all')} across all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
