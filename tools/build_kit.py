r"""
One command for the test kit: build the release, prove it starts, zip what Jim's PC needs.

Why: docs\testing\jims-pc.md wants a test kit before the first window, and the time on Jim's PC
is short. A zip made by hand can carry a build that never passed its self-test, or the repo's
installer\ instead of the release's, whose extra files install.ps1 refuses. Here a kit exists
only for a build that said its version, passed its self-test and still matches its manifest.

Usage, on Sulaiman's PC:
    .venv\Scripts\python.exe tools\build_kit.py --out <new, empty folder outside the repo>

It writes only below that folder:
    dist\                the release (docs\p1a\runbook.md, "Update and rollback": the build)
    build\               PyInstaller's work folder
    pyinstaller-config\  PyInstaller's cache, which would otherwise go in this PC's profile
    selftest-data\       the self-test's data folder, so %LOCALAPPDATA%\NGRunner is left alone
    NGRunner-test-kit-<version>.zip

The zip is the release itself plus the rest of the kit, at the same paths as in the repo:
    app\  launcher\  installer\  manifest.json   the release; install.ps1 takes the folder above
                                                 itself as the release and checks only these
    tools\setup_check.cmd  tools\setup_check.ps1
    docs\testing\jims-pc.md

Exit codes: 0 the kit is made; 1 no kit, and the last lines say why; 2 a wrong command line.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = "dist"
WORK = "build"
PYINSTALLER_CONFIG = "pyinstaller-config"
SELF_TEST_DATA = "selftest-data"
APP_EXE = "app/NGRunner.exe"
# What the kit takes from the repo as it is, at the same path in the kit.
KIT_FILES = ("tools/setup_check.cmd", "tools/setup_check.ps1", "docs/testing/jims-pc.md")
BUILD_TIMEOUT_S = 30 * 60
VERSION_TIMEOUT_S = 2 * 60
SELF_TEST_TIMEOUT_S = 10 * 60

Say = Callable[[str], None]
Build = Callable[[Path, Path], None]        # (repo, out): leaves a release in out\dist
RunExe = Callable[[Path, Sequence[str], Mapping[str, str], float], tuple[int, str]]


def _load_release_manifest():
    """installer\\manifest.py, the format install.ps1 checks, loaded as ngrunner.spec loads it."""
    spec = importlib.util.spec_from_file_location("ngr_release_manifest",
                                                  REPO / "installer" / "manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANIFEST = _load_release_manifest()


class KitError(Exception):
    """Why no kit was made, as a sentence for the person running this."""


@dataclass(frozen=True)
class Kit:
    path: Path
    size: int
    sha256: str
    manifest_sha256: str


# ----- the real build and the real exe (the tests inject both)


def pyinstaller_build(repo: Path, out: Path) -> None:
    """The runbook's build command, from the repo root, with nothing left in the repo or profile."""
    python = repo / ".venv" / "Scripts" / "python.exe"
    command = [str(python), "-m", "PyInstaller", "ngrunner.spec", "--noconfirm",
               "--distpath", str(out / DIST), "--workpath", str(out / WORK)]
    env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(out / PYINSTALLER_CONFIG),
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                              timeout=BUILD_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise KitError(f"The build did not finish in {BUILD_TIMEOUT_S // 60} minutes,"
                       " so no test kit was made.") from None
    except OSError as exc:
        raise KitError(f"The build could not start ({exc.strerror or exc}): it needs {python}."
                       " No test kit was made.") from None
    if done.returncode != 0:
        raise KitError(f"The build failed (exit code {done.returncode}), so no test kit was made."
                       " PyInstaller's own lines above say why.")


def run_exe(exe: Path, args: Sequence[str], env: Mapping[str, str],
            timeout_s: float) -> tuple[int, str]:
    """The exit code and everything the exe printed."""
    done = subprocess.run([str(exe), *args], env=dict(env), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace", timeout=timeout_s, check=False)
    return done.returncode, done.stdout


# ----- the steps


def checked_out_folder(out: Path, repo: Path) -> Path:
    """The output folder's real path, once it is known to be new or empty and outside the repo.

    The real path, so a junction into the repo or another spelling of it is seen for what it is.
    """
    if not out.is_absolute():
        raise KitError(f"{out} is not a full path. Give the full path of a new, empty folder"
                       " outside the repo.")
    full = out.resolve()
    home = repo.resolve()
    if full.is_relative_to(home):
        raise KitError(f"{full} is inside the repo, and a build must stay out of it."
                       " Give a new, empty folder outside the repo.")
    if full.exists() and (not full.is_dir() or any(full.iterdir())):
        raise KitError(f"{full} is not a new, empty folder, and each build needs one"
                       " (docs\\p1a\\runbook.md).")
    return full


def read_version(repo: Path) -> str:
    try:
        return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise KitError(f"The version could not be read from pyproject.toml ({exc}),"
                       " so no test kit was made.") from None


def _run_built(run: RunExe, exe: Path, flag: str, env: Mapping[str, str],
               timeout_s: float) -> tuple[int, str]:
    try:
        code, text = run(exe, [flag], env, timeout_s)
    except subprocess.TimeoutExpired:
        raise KitError(f"NGRunner.exe {flag} did not finish in {int(timeout_s // 60)} minutes,"
                       " so no test kit was made.") from None
    except OSError as exc:
        raise KitError(f"NGRunner.exe {flag} could not start ({exc.strerror or exc}),"
                       " so no test kit was made.") from None
    return code, text.strip()


def prove_it_starts(out: Path, version: str, run: RunExe, say: Say) -> None:
    """--version names the version being packed, and --self-test passes (exit code 0)."""
    exe = out / DIST / APP_EXE
    if not exe.is_file():
        raise KitError(f"The build finished without {APP_EXE}, so no test kit was made.")
    # NG Runner's data folder is the self-test's sandbox: here, a folder below the output.
    env = {**os.environ, "NGR_APP_DATA": str(out / SELF_TEST_DATA)}
    code, text = _run_built(run, exe, "--version", env, VERSION_TIMEOUT_S)
    if code != 0 or text != f"NG Runner {version}":
        raise KitError(f"NGRunner.exe --version said {text!r} (exit code {code}), and"
                       f" pyproject.toml says {version}, so no test kit was made.")
    say(text)
    code, text = _run_built(run, exe, "--self-test", env, SELF_TEST_TIMEOUT_S)
    if code != 0:
        raise KitError(f"The self-test failed (exit code {code}), so no test kit was made."
                       f" It said:\n{text}")
    say(text)


def check_release(dist: Path) -> None:
    """A release install.ps1 would refuse never goes in a kit."""
    problems = MANIFEST.verify(dist)
    if problems:
        raise KitError("The build does not match its manifest, so install.ps1 would refuse it,"
                       " and no test kit was made: " + "; ".join(problems[:5]) + ".")


def zip_kit(out: Path, version: str, repo: Path) -> Kit:
    """The zip, under a temporary name until it is whole, so a zip with its name is complete."""
    dist = out / DIST
    target = out / f"NGRunner-test-kit-{version}.zip"
    part = target.with_name(target.name + ".part")
    members = [(path, path.relative_to(dist).as_posix())
               for path in sorted(dist.rglob("*")) if path.is_file()]
    members += [(repo / name, name) for name in KIT_FILES]
    try:
        with zipfile.ZipFile(part, "x", zipfile.ZIP_DEFLATED, strict_timestamps=False) as kit:
            for source, name in members:
                kit.write(source, name)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return Kit(target, target.stat().st_size, MANIFEST.sha256(target),
               MANIFEST.sha256(dist / MANIFEST.MANIFEST))


def make_kit(out: Path, *, repo: Path = REPO, build: Build = pyinstaller_build,
             run: RunExe = run_exe, say: Say = print) -> Kit:
    """The whole job, in order. Raises KitError, with no zip left, at the first thing wrong."""
    out = checked_out_folder(out, repo)
    version = read_version(repo)
    missing = [name for name in KIT_FILES if not (repo / name).is_file()]
    if missing:   # before a build that takes minutes
        raise KitError(f"The kit needs {', '.join(missing)}, not found in {repo},"
                       " so no test kit was made.")
    out.mkdir(parents=True, exist_ok=True)
    say(f"Building NG Runner {version} into {out / DIST}")
    build(repo, out)
    prove_it_starts(out, version, run, say)
    check_release(out / DIST)
    return zip_kit(out, version, repo)


# ----- the command


def _say(line: str) -> None:
    print(line, flush=True)   # flushed, so these lines stay in order among PyInstaller's


def main(argv: Sequence[str] | None = None, *, repo: Path = REPO,
         build: Build = pyinstaller_build, run: RunExe = run_exe, say: Say = _say) -> int:
    parser = argparse.ArgumentParser(prog="build_kit.py", description="Build the test kit zip.")
    parser.add_argument("--out", required=True, type=Path,
                        help="the full path of a new, empty folder outside the repo")
    args = parser.parse_args(argv)
    try:
        kit = make_kit(args.out, repo=repo, build=build, run=run, say=say)
    except KitError as exc:
        say(str(exc))
        return 1
    except OSError as exc:
        say(f"The test kit could not be made ({exc.strerror or exc}).")
        return 1
    say(f"Test kit: {kit.path}")
    say(f"Size: {kit.size:,} bytes ({kit.size / (1024 * 1024):.1f} MB)")
    say(f"sha256: {kit.sha256}")
    say(f"For install.ps1 -ManifestSha256: {kit.manifest_sha256}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")   # a self-test line this console can't show still prints
    raise SystemExit(main())
