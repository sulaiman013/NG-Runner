"""
Phase 0 material says what is true: the docs for Jim, the Setup Check scripts, the agent docs.

Why: Jim acts on these documents with nobody beside him, and they are committed, so they must
agree with each other and with the tools, and must never carry client or contact details.
The synthetic tests read only this repo. The one test marked `fixtures` checks every quote the
questionnaire takes from Jim's spec against his snapshot (local only, NGR_SPEC_DIR); its failures
show the questionnaire's own fragment and file:line, never his text.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
P0 = ROOT / "docs" / "p0"
SETUP_GUIDE = P0 / "setup-check.md"
ACCESS = P0 / "access-request.md"
QUESTIONNAIRE = P0 / "questionnaire-jim.md"
SETUP_PS1 = ROOT / "tools" / "setup_check.ps1"
SETUP_CMD = ROOT / "tools" / "setup_check.cmd"
POWERSHELL_LINE = (r'powershell -NoProfile -ExecutionPolicy Bypass -File "$((Get-ItemProperty '
                   r"'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell "
                   r"Folders').Desktop)\setup_check.ps1" + '"')


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text under a Markdown heading, up to the next heading of the same or a higher level."""
    found = re.search(rf"(?m)^(#+) {re.escape(heading)}[ \t]*$", text)
    assert found, f"no heading {heading!r}"
    following = re.compile(rf"(?m)^#{{1,{len(found[1])}}} ").search(text, found.end())
    return text[found.end(): following.start() if following else len(text)]


def _console_text(ps1: str) -> list[str]:
    """The literal text the Setup Check prints to its window (Write-Host and Read-Host)."""
    return [m[1].replace("''", "'") for m in
            re.finditer(r"(?:Write|Read)-Host\s+\(?'((?:[^']|'')*)'", ps1)]


# ----- files reach Sulaiman through Jim's private Drive folder (D1)

# Email or Slack offered as a way to move files. A sentence that forbids it is fine.
_MAIL_ROUTE = re.compile(
    r"(?i)\b(?:by|via|over|through) (?:e-?mail|slack)\b|\bor (?:e-?mail|slack)\b"
    r"|\be-?mail (?:it|them|both|the (?:files?|report))\b|\b(?:in|on) slack\b")
_FORBIDS = re.compile(r"(?i)\b(?:never|not|don't|no)\b")


def _mail_routes(text: str) -> list[int]:
    """Line numbers where a sentence offers email or Slack as a route for files."""
    return [no for no, line in enumerate(text.splitlines(), 1)
            for sentence in re.split(r"(?<=[.!?])\s+", line)
            if _MAIL_ROUTE.search(sentence) and not _FORBIDS.search(sentence)]


def test_the_setup_check_report_goes_to_the_private_drive_folder():
    assert "private Drive folder" in _section(_read(SETUP_GUIDE), "What to send back")
    closing = [line for line in _console_text(_read(SETUP_PS1)) if "Sulaiman" in line]
    assert any("private Drive folder" in line for line in closing), closing


def test_no_phase0_instruction_moves_files_by_email_or_slack():
    for path in (SETUP_GUIDE, ACCESS, SETUP_PS1, SETUP_CMD):
        lines = _mail_routes(_read(path))
        assert not lines, f"{path.relative_to(ROOT)} lines {lines} offer email or Slack"


# ----- the docs describe the Setup Check as it works (D2)


def _checklist_item(text: str, number: int) -> str:
    """One "- [ ] **N. ...**" item of the access list, with its sub-bullets."""
    found = re.search(rf"(?ms)^- \[ \] \*\*{number}\. .*?(?=^- \[ \] |^#|\Z)", text)
    assert found, f"no item {number}"
    return found[0]


def test_the_access_list_describes_the_setup_check_as_it_works():
    item = _checklist_item(_read(ACCESS), 7)
    assert "one report file" not in item
    assert re.search(r"\btwo files\b", item), "it writes two files"
    for needed in ("double-click", "setup_check.cmd", ".txt", ".json", "private Drive folder"):
        assert needed in item, needed


def test_the_setup_check_guide_leads_with_double_click_and_keeps_the_powershell_line():
    how = _section(_read(SETUP_GUIDE), "How to run it")
    assert 0 <= how.find("Double-click `setup_check.cmd`") < how.find(POWERSHELL_LINE)
    assert POWERSHELL_LINE in _read(SETUP_PS1)   # the line the script documents for itself


# ----- setup_check.cmd: what Jim double-clicks

WAIT_PROMPT = "Press any key to close this window."
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs cmd.exe and PowerShell")

# Stands in for setup_check.ps1: records what it was given and which process started it, then
# exits with the code given (7 means nothing to setup_check.cmd).
_STUB_PS1 = """\
param([string]$OutDir = '', [switch]$NoJobs, [switch]$NoPause)
$me = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $PID)
$parent = (Get-Process -Id ([int]$me.ParentProcessId)).ProcessName
$line = 'OutDir=' + $OutDir + '|NoPause=' + [bool]$NoPause + '|parent=' + $parent
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'ran.txt') -Value $line -Encoding Ascii
exit {code}
"""


def _stub(folder: Path, code: int = 7) -> None:
    (folder / "setup_check.ps1").write_text(_STUB_PS1.replace("{code}", str(code)),
                                            encoding="ascii")


@pytest.fixture
def desktop(tmp_path: Path) -> Path:
    """A folder with spaces in its name (like the repo's), holding a copy of setup_check.cmd."""
    folder = tmp_path / "Desk top copy"
    folder.mkdir()
    (folder / "setup_check.cmd").write_bytes(SETUP_CMD.read_bytes())
    return folder


def _double_click(folder: Path, args: str = "") -> tuple[int, str]:
    """Runs setup_check.cmd through cmd.exe as Explorer does, with no keyboard attached."""
    line = f'cmd.exe /d /s /c ""{folder / "setup_check.cmd"}" {args}"'
    done = subprocess.run(line, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    return done.returncode, done.stdout.decode("ascii", "replace")


def test_setup_check_cmd_is_plain_ascii_with_crlf_line_endings():
    data = SETUP_CMD.read_bytes()
    assert data.isascii()
    assert data.endswith(b"\r\n") and data.count(b"\n") == data.count(b"\r\n")


@windows_only
def test_setup_check_cmd_runs_the_script_beside_it_with_the_arguments_given(desktop):
    _stub(desktop)
    code, out = _double_click(desktop, r'-OutDir "C:\Reports (x86)\a b" -NoPause')
    ran = (desktop / "ran.txt").read_text(encoding="ascii").strip()
    # Started by cmd.exe, not Explorer, so the script's own wait for Enter never fires too.
    assert ran == r"OutDir=C:\Reports (x86)\a b|NoPause=True|parent=cmd"
    assert code == 7                   # the script's exit code
    assert WAIT_PROMPT not in out


@windows_only
def test_setup_check_cmd_waits_for_a_key_exactly_once(desktop):
    _stub(desktop)
    code, out = _double_click(desktop)
    assert out.count(WAIT_PROMPT) == 1
    assert code == 7


# What the window's last lines tell Jim to do. Exit 3: the script could not save the report and
# has said so itself, so the .cmd must not give a second, different instruction.
@windows_only
@pytest.mark.parametrize(("exit_code", "says"), [(0, None), (3, None), (1, "stopped before")])
def test_setup_check_cmd_adds_an_instruction_only_when_the_script_stopped_early(
        desktop, exit_code, says):
    _stub(desktop, exit_code)
    code, out = _double_click(desktop, "-NoPause")
    assert code == exit_code
    lines = [line for line in out.splitlines() if line.strip()]
    if says is None:
        assert lines == [], lines
    else:
        assert len(lines) == 1 and says in lines[0] and "photo" in lines[0], lines


@windows_only
def test_setup_check_cmd_says_so_when_the_script_is_missing(desktop):
    code, out = _double_click(desktop, "-NoPause")
    assert "setup_check.ps1 is missing" in out
    assert code != 0


def test_setup_check_cmd_has_no_labels_so_lf_line_endings_cannot_break_it():
    # cmd.exe finds labels unreliably in a file with LF line endings, which a git checkout can
    # produce (core.autocrlf stores this file with LF). Without labels or goto it can't matter.
    code = [line for line in SETUP_CMD.read_text(encoding="ascii").splitlines()
            if not re.match(r"(?i)\s*rem\b", line)]
    assert not [line for line in code if re.search(r"(?i)^\s*:|\bgoto\b|\bcall\s+:", line)]


@windows_only
def test_an_lf_copy_of_setup_check_cmd_behaves_the_same(desktop):
    lf = desktop.parent / "lf copy"
    lf.mkdir()
    (lf / "setup_check.cmd").write_bytes(SETUP_CMD.read_bytes().replace(b"\r\n", b"\n"))
    code, out = _double_click(lf)
    assert "setup_check.ps1 is missing" in out and out.count(WAIT_PROMPT) == 1 and code == 2
    _stub(lf, 3)
    code, out = _double_click(lf)
    assert (lf / "ran.txt").is_file() and out.count(WAIT_PROMPT) == 1 and code == 3


# ----- setup_check.ps1, one part at a time

# Loads the named functions of setup_check.ps1, found by PowerShell's own parser (nested ones
# too), into a fresh PowerShell; the check written after it then runs against them alone.
_LOAD_FUNCTIONS = """\
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{source}', [ref]$null, [ref]$null)
$wanted = @({names})
foreach ($f in $ast.FindAll({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }}, $true)) {{
    if ($wanted -contains $f.Name) {{
        . ([scriptblock]::Create($f.Extent.Text))
        $wanted = @($wanted | Where-Object {{ $_ -ne $f.Name }})
    }}
}}
if ($wanted.Count -gt 0) {{ Write-Host ('MISSING ' + ($wanted -join ', ')); exit 9 }}
"""


def _run_ps1_functions(folder: Path, names: list[str], check: str) -> str:
    """Console output of check, run in a fresh Windows PowerShell with those functions loaded."""
    harness = folder / "harness.ps1"
    quoted = ", ".join(f"'{name}'" for name in names)
    source = str(SETUP_PS1).replace("'", "''")   # inside a single-quoted PowerShell string
    harness.write_text(_LOAD_FUNCTIONS.format(source=source, names=quoted) + check,
                       encoding="utf-8-sig")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                           "Bypass", "-File", str(harness)],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    out = done.stdout.decode("ascii", "replace")
    assert done.returncode == 0, out + done.stderr.decode("ascii", "replace")
    return out


_CLOSING_CHECK = """
$att = @(@{ tag = 'BLOCKS'; text = 'First thing' }, @{ tag = 'CHECK'; text = 'Second thing' })
foreach ($saved in @($true, $false)) {
    Write-Host ('==== saved=' + $saved)
    $code = Write-Closing -Written $saved -TxtPath 'C:\\r\\a.txt' -JsonPath 'C:\\r\\a.json' `
        -Text "REPORT START`r`nREPORT END" -Attention $att -Notes @() -Seconds 12
    Write-Host ('==== exit=' + $code)
}
"""


@windows_only
def test_setup_check_ends_with_one_instruction_that_fits_what_happened(tmp_path):
    out = _run_ps1_functions(tmp_path, ["Write-Closing", "Protect-Text"], _CLOSING_CHECK)
    shown = dict(re.findall(r"==== saved=(\w+)\r?\n(.*?)==== exit=", out, re.S))
    saved, not_saved = shown["True"], shown["False"]
    saved_lines = [line.strip() for line in saved.splitlines() if line.strip()]
    assert saved_lines[-1] == ("Next: put both report files in the private Drive folder you "
                               "share with Sulaiman.")
    assert "C:\\r\\a.txt" in saved and "photo" not in saved
    lines = [line.strip() for line in not_saved.splitlines() if line.strip()]
    # No files exist, so no "put both report files"; the report is shown, then the list of what
    # needs attention comes last, where a photo of the window's last screen still catches it.
    assert "put both report files" not in not_saved
    order = [lines.index(x) for x in ("REPORT END", "1. [BLOCKS] First thing",
                                      "2. [CHECK] Second thing")]
    assert order == sorted(order) and order[-1] == len(lines) - 2, lines
    assert "photo" in lines[-1] and "private Drive folder" in lines[-1], lines[-1]
    # 3 tells setup_check.cmd that this window already said what to do.
    assert re.findall(r"==== exit=(\d+)", out) == ["0", "3"]


@windows_only
def test_setup_check_counts_only_folders_with_a_job_date_as_job_folders(tmp_path):
    # NG Jobs also holds folders such as Old and workshop or test ones: counting every folder
    # as a job states a number the check never established.
    ng_jobs = tmp_path / "NG Jobs"
    for name in ("Testerson_101426", "Fakeley_090926_Gar", "Testerson_101426-2",
                 "Old", "Workshop", "test jobs 2026", "Backup 20261010"):
        (ng_jobs / name).mkdir(parents=True)
    (ng_jobs / "Testerson_101426.txt").write_text("a file, not a folder", encoding="utf-8")
    out = _run_ps1_functions(tmp_path, ["Measure-NgJobs"], f"""
$r = Measure-NgJobs '{ng_jobs}'
Write-Host ('jobs=' + $r['jobs'] + ' other=' + $r['other'])
""")
    assert "jobs=3 other=4" in out, out


_DESKTOP_CHECK = """
$want = [Environment]::GetFolderPath('Desktop') + '\\setup_check.ps1'
Write-Host ('want=' + $want)
Write-Host ('full=' + "PATH")
$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
Write-Host ('clm=' + "PATH")
"""


@windows_only
def test_the_guides_powershell_line_finds_the_desktop_wherever_windows_keeps_it(tmp_path):
    # Jim's Desktop may be kept in OneDrive ("OneDrive - ICWC\Desktop"), and Smart App Control
    # can put PowerShell in Constrained Language Mode, where [Environment]::GetFolderPath fails.
    how = _section(_read(SETUP_GUIDE), "How to run it")
    line = next(x.strip() for x in how.splitlines() if x.strip().startswith("powershell "))
    path = re.fullmatch(r'powershell -NoProfile -ExecutionPolicy Bypass -File "(.+)"', line)[1]
    out = _run_ps1_functions(tmp_path, [], _DESKTOP_CHECK.replace("PATH", path))
    got = dict(re.findall(r"(?m)^(want|full|clm)=(.*?)\r?$", out))
    assert got["full"] == got["want"] and got["clm"] == got["want"], got
    assert "OneDrive\\Desktop" not in _read(SETUP_GUIDE), "a fixed OneDrive path fits few PCs"


# ----- no contact details or Drive folder IDs in what is committed (H3)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")   # reserved for examples
# North American numbers as people write them, and compact international (+E.164) ones.
_PHONE = re.compile(
    r"(?<![\w.])(?:\+?1[-. ]?)?(?:\(\d{3}\) ?|\d{3}[-. ])\d{3}[-. ]\d{4}(?!\w|\.\d)"
    r"|(?<![\w+])\+\d{11,15}(?!\w)")
_DRIVE_ID = (
    # an ID named after .shortcut-targets-by-id\ (Drive's IDs are 19 or more characters long);
    # <folder id>, * and short words such as AnyId are placeholders
    re.compile(r"shortcut-targets-by-id[\\/]+[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?:drive|docs)\.google\.com/\S*?(?:/d/|/folders/|[?&]id=)[\w-]{10,}"),
    # a bare Drive ID: 25-44 characters starting 0 or 1, with upper and lower case and digits
    re.compile(r"(?<![\w-])[01](?=[\w-]*[A-Z])(?=[\w-]*[a-z])(?=[\w-]*\d)[\w-]{24,43}(?![\w-])"),
)


def _is_example(domain: str) -> bool:
    return any(domain.lower() == d or domain.lower().endswith("." + d) for d in _EXAMPLE_DOMAINS)


def _is_example_phone(number: str) -> bool:
    """555 is for examples: the 555 exchange (as in 310-555-01xx), and area code 555, which no
    real North American number has."""
    digits = re.sub(r"\D", "", number)[-10:]
    return "555" in (digits[:3], digits[3:6])


def _private_details(text: str) -> list[str]:
    """Each line's contact details and Drive IDs, as "line N: kind", never the value itself."""
    found: list[str] = []
    for no, line in enumerate(text.splitlines(), 1):
        if any(not _is_example(m[1]) for m in _EMAIL.finditer(line)):
            found.append(f"line {no}: email address")
        if any(not _is_example_phone(m[0]) for m in _PHONE.finditer(line)):
            found.append(f"line {no}: phone number")
        if any(p.search(line) for p in _DRIVE_ID):
            found.append(f"line {no}: Drive folder ID")
    return found


def _example_phone(area: str, exchange: str, line: str, form: str = "({0}) {1}-{2}") -> str:
    """Built at run time from its parts, so this file holds nothing shaped like a phone number.
    The real-looking ones use area code 390: area codes with a middle 9 are kept for later, so
    no line in service has one."""
    return form.format(area, exchange, line)


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("Write to pat" + "@" + "example.com or ops" + "@" + "mail.example.org.", []),
        ("Write to pat" + "@" + "fakeley.test.", ["email address"]),
        ("Call " + _example_phone("310", "555", "0123") + " or "
         + _example_phone("310", "555", "0199", "{0}-{1}-{2}") + ".", []),
        ("Call " + _example_phone("390", "444", "0123") + ".", ["phone number"]),
        ("Call " + _example_phone("390", "444", "0123", "1 {0}.{1}.{2}") + " or "
         + _example_phone("390", "444", "0123", "+1{0}{1}{2}") + ".", ["phone number"]),
        ("Call " + _example_phone("555", "010", "0199") + ": no real number has area code 555.", []),
        ("RULES 330-339, BUILD 554-558, 2026-10-29, 0x80070005, 4294967296, 127.0.0.1", []),
        (r"G:\.shortcut-targets-by-id\<folder id>\CNC and G:\.shortcut-targets-by-id\*\x", []),
        (".shortcut-targets-by-id/AnyId: a word, too short to be a Drive ID", []),
        (r"G:\.shortcut-targets-by-id" + "\\" + "Ab3" * 7 + "\\CNC operations", ["Drive folder ID"]),
        ("https://drive.google.com/drive/folders/" + "x" * 20, ["Drive folder ID"]),
        ("ID " + "1" + "Ab3" * 10 + "xY" + " here", ["Drive folder ID"]),
        ("GUID 01443614-cd74-433a-b99e-2ecdc07bfc25 and sha " + "0a" * 32, []),
    ],
    ids=["examples", "email", "555", "phone", "phone forms", "555 area code", "not phones",
         "placeholders", "short placeholder", "shortcut id", "drive url", "bare id", "not ids"],
)
def test_the_privacy_scan_finds_real_shapes_and_allows_examples(text, kinds):
    assert [f.split(": ", 1)[1] for f in _private_details(text)] == kinds


def _files_git_would_commit() -> list[str]:
    """Every file git would commit: tracked, or untracked and not ignored. Empty without git
    (or outside a git checkout), so the scan below skips rather than half-runs."""
    if shutil.which("git") is None:
        return []
    done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                          cwd=ROOT, capture_output=True, timeout=60)
    if done.returncode != 0:
        return []
    return sorted(name for name in done.stdout.decode("utf-8").split("\0")
                  if name and (ROOT / name).is_file())


@pytest.mark.parametrize("path", _files_git_would_commit())
def test_no_file_git_would_commit_holds_contact_details_or_drive_folder_ids(path):
    data = (ROOT / path).read_bytes()
    if b"\0" in data:
        pytest.skip("a binary file")
    found = _private_details(data.decode("utf-8", "replace"))   # never prints the text itself
    assert not found, found


# ----- Jim's answers name jobs, so git never takes them


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize(("path", "ignored"), [
    ("docs/p0/questionnaire-jim-answered.md", True),
    ("docs/p0/questionnaire jim answered 2026-11-02.md", True),
    ("docs/p0/answers/questionnaire-jim.md", True),
    ("docs/p0/questionnaire-jim (1).md", True),        # a browser's second download from Drive
    ("docs/p0/questionnaire-jim-answers.md", True),
    ("docs/p0/questionnaire-jim-filled.md", True),
    ("docs/p0/questionnaire-jim 2026-11-02.md", True),
    ("docs/p0/questionnaire-jim.docx", True),
    ("docs/p0/questionnaire-jim.pdf", True),
    ("docs/p0/Jim NG notes.md", True),                 # any other file in docs/p0
    ("docs/p0/jim/questionnaire-jim.md", True),        # a subfolder
    ("docs/questionnaire-jim.md", True),               # one folder up
    ("questionnaire-jim (1).md", True),                # the repo root
    ("docs/p0/questionnaire-jim.md", False),           # the three blank documents
    ("docs/p0/access-request.md", False),
    ("docs/p0/setup-check.md", False),
    ("docs/adr/0001-example.md", False),               # other docs, and code, stay tracked
    ("tests/test_questionnaire.py", False),
])
def test_jims_answers_stay_out_of_git(path, ignored):
    done = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT,
                          capture_output=True, timeout=60)
    assert done.returncode == (0 if ignored else 1), done.stderr.decode("utf-8", "replace")


def _answered_lines(doc: str) -> list[int]:
    """Lines where an answer was written on a ">" answer line."""
    return [no for no, line in enumerate(doc.splitlines(), 1)
            if line.startswith(">") and line[1:].strip()]


def test_the_blank_questionnaire_holds_no_answers():
    # No ignore rule protects a tracked file, so answers saved over it would be committed.
    assert _answered_lines("### 1. A question?\n\n> yes, Testerson_101426\n") == [3]
    lines = _answered_lines(_read(QUESTIONNAIRE))
    assert not lines, (f"questionnaire-jim.md has answers on lines {lines}: keep Jim's answers "
                       "in his private Drive folder, never in the repo")


def test_agents_keep_jims_answers_outside_the_repo():
    row = next(line for line in _read(ROOT / "AGENTS.md").splitlines()
               if line.startswith("| `docs/p0/`"))
    assert "answers/" not in row and "*answered*" not in row, "it invites a copy into the repo"
    for needed in ("outside the repo", "private Drive folder", "never save"):
        assert needed in row, needed


# ----- AGENTS.md says what the code relies on (D3, HY8)


def _agents_item(heading: str, number: int) -> str:
    """Item N of a numbered list under an AGENTS.md heading."""
    found = re.search(rf"(?m)^{number}\. .*$", _section(_read(ROOT / "AGENTS.md"), heading))
    assert found, f"no item {number} under {heading!r}"
    return found[0]


def test_the_canary_proves_the_file_identity_every_commit_relies_on():
    # writes.py records a temp file's identity with os.fstat and checks it with os.lstat before
    # the rename and before removing it: on a drive where the two differ, every commit refuses.
    item = _agents_item("Before filing goes live", 3)
    for needed in ("os.fstat", "os.lstat", "(st_dev, st_ino)", "rename keeps it"):
        assert needed in item, needed


def test_rule_one_says_what_the_chokepoint_may_remove():
    # commit_write removes its own temp files and empty folders, and in a rollback a file it
    # renamed in moments before; read literally, "never deletes" would strip that code.
    rule = _agents_item("Rules (do not break)", 1)
    assert "It never deletes." not in rule
    for needed in ("did not create", "temp files", "empty folders", "rollback"):
        assert needed in rule, needed


_AGENTS_SECTION = re.compile(r"AGENTS\.md[:,] ([A-Z][\w -]*?)(?=[)\"'.,;:]|$)", re.M)
_AGENTS_RULES = re.compile(r"AGENTS\.md rules? (\d+)(?: and (\d+))?")


def test_every_pointer_into_agents_md_names_a_section_or_rule_it_has():
    # A skip message or comment that points at a section that was renamed sends readers nowhere.
    agents = _read(ROOT / "AGENTS.md")
    headings = [h.casefold() for h in re.findall(r"(?m)^#{2,} (.+)$", agents)]
    rules = set(re.findall(r"(?m)^(\d+)\. ", _section(agents, "Rules (do not break)")))
    found, wrong = 0, []
    for path in _files_git_would_commit():
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for m in _AGENTS_SECTION.finditer(text):
            found += 1
            if not any(m[1].casefold() in h for h in headings):
                wrong.append(f"{path}: section {m[1]!r}")
        for m in _AGENTS_RULES.finditer(text):
            found += 1
            wrong += [f"{path}: rule {n}" for n in m.groups() if n and n not in rules]
    assert found, "no pointers found: the patterns no longer match how they are written"
    assert not wrong, wrong


# ----- the glossary keeps a job, its branch and its runs apart (HY2)


def _glossary() -> dict[str, str]:
    """GLOSSARY.md's entries: the bold term, then its definition and _Avoid_ lines."""
    return {m[1]: m[2] for m in re.finditer(r"(?ms)^\*\*(.+?)\*\*:\n(.*?)(?=^\*\*|^#|\Z)",
                                            _read(ROOT / "GLOSSARY.md"))}


def test_the_glossary_keeps_a_jobs_branch_its_runs_and_other_jobs_apart():
    # As match.py has it: "_Gar" is a branch of the same job; remake, revN, -N and the rest are
    # runs of the parent job; a name that carries a run can still be a job of its own (a separate
    # "-2" sale, with its own Studio project ID). Merging that into its parent files a summary to
    # the wrong job. "Run" alone also means a pass of the pipeline, as in "the night run".
    entries = _glossary()
    assert "`_Gar`" in entries["Branch"] and "same job" in entries["Branch"]
    run = entries["Run (of a job)"]
    for token in ("remake", "revN", "redoN", "-N", "pickup", "top", "GB"):
        assert token in run, token
    assert "Studio project ID of its own" in run
    assert "Pipeline run" in entries and "night run" in entries["Pipeline run"]
    whole = _read(ROOT / "GLOSSARY.md")
    for wrong in ("A variant is a run", "never a new job", "not new jobs"):
        assert wrong not in whole, wrong


# ----- how the tests run (H1, H2) and what they depend on (S4)


def test_tests_run_from_any_folder(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run(
        [sys.executable, "-X", "warn_default_encoding", "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(ROOT / "tests")],
        cwd=tmp_path, env=env, capture_output=True, timeout=300)
    out = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert "No module named 'ngrunner'" not in out
    assert re.search(r"(?m)^tests/test_\w+\.py::", out), out[-2000:]   # pytest ids use "/"


def test_the_tests_run_with_encoding_warnings_on():
    # Without -X warn_default_encoding Python raises no EncodingWarning at all, so pyproject's
    # error filter can't catch a missing encoding="utf-8" (rule 7): a run without it isn't green.
    assert sys.flags.warn_default_encoding, (
        r"run the tests with .venv\Scripts\python.exe -X warn_default_encoding -m pytest -q "
        "(AGENTS.md, Commands)")


def test_documented_test_commands_turn_encoding_warnings_on():
    # pyproject's error::EncodingWarning filter only bites when Python runs with this flag.
    # CLAUDE.md is a local file that git never takes, so it is checked only where it exists.
    for doc in ("AGENTS.md", "CLAUDE.md"):
        if doc != "AGENTS.md" and not (ROOT / doc).exists():
            continue
        for no, line in enumerate(_read(ROOT / doc).splitlines(), 1):
            if re.search(r"-m pytest\b", line):
                assert "-X warn_default_encoding" in line, f"{doc}:{no}"


def test_pypdf_is_pinned_to_the_installed_version():
    # Content fingerprints hash pypdf's text extraction: another version may change them.
    with (ROOT / "pyproject.toml").open("rb") as f:
        declared = tomllib.load(f)["project"]["dependencies"]
    pins = [d.replace(" ", "") for d in declared if re.match(r"(?i)pypdf\b", d)]
    assert pins == [f"pypdf=={metadata.version('pypdf')}"], pins


# ----- the questionnaire's shape: one idea per question, a recommendation, a line to answer on

WEEK_ONE = {2, 3, 4, 5, 7, 9, 10, 11, 12, 13, 14, 15, 16, 18}   # design section 8, decision 6


def _questions(doc: str) -> list[str]:
    """Each "### " question of the questionnaire, heading first."""
    return [block for block in re.split(r"(?m)^(?=#{2,3} )", doc) if block.startswith("### ")]


def test_every_question_has_a_recommendation_a_one_word_accept_and_an_answer_line():
    # A plain question of fact (only Jim knows it) says so, and takes a yes or no instead.
    for block in _questions(_read(QUESTIONNAIRE)):
        title = block.splitlines()[0]
        if "**No recommendation:**" in block:
            assert 'reply "yes" or "no"' in block and "**Recommended" not in block, title
        else:
            assert "**Recommended" in block, title
            assert 'reply "yes" to accept' in block, title
        assert re.search(r"(?m)^> $", block), title


def _item(number: str) -> str:
    """One question of the questionnaire by its number ("1", "3a", "Q14")."""
    return next(b for b in _questions(_read(QUESTIONNAIRE)) if b.startswith(f"### {number}. "))


def test_the_hours_question_states_the_weekly_total_jim_agrees_to():
    # The design's dates assume all 20 hours go to building (P1a ~13 dev-days by week 5-6), so
    # the 2-4 hours of upkeep per live phase come on top: 1 phase once filing is live, 4 (filing,
    # the nest guard, the shared tools, checks) once checks are live.
    item = _item("1")
    assert "on top of the 20" in item
    assert "22–24 hours" in item and "28–36" in item, "20 + 1 × (2–4) and 20 + 4 × (2–4)"


def test_the_night_setup_question_says_what_a_morning_restart_can_cost():
    # Active hours span at most 18 hours, so update restarts get 5-11 AM, after the night run.
    # Jim can only weigh that if he knows a restart then can close an unsaved Mozaik job, and
    # he can give his start time so another window is looked for.
    item = _item("6")
    assert "unsaved" in item and "Mozaik" in item
    assert "the time you usually start" in item


def test_every_question_is_one_jim_can_answer_himself():
    # What Ron, Will and Mary want is theirs to say; Jim can say whether they may be offered it.
    titles = re.findall(r"(?m)^### (.*)$", _read(QUESTIONNAIRE))
    assert not [t for t in titles if re.search(r"\bwants?\b", t) and "you want" not in t]


def test_the_questionnaire_uses_no_unexplained_design_words():
    doc = _read(QUESTIONNAIRE).lower()
    assert not [w for w in ("pre-flight", "committed plan", "sealed list") if w in doc]


def test_the_context_promises_only_what_the_recommendations_keep():
    # Q1 recommends Jim's dated notes over RULES 580's numbering, so "always RULES" is untrue.
    context = _section(_read(QUESTIONNAIRE), "Context")
    assert "where RULES speaks the recommended answer is its text" not in context
    assert "usually" in context and "Q1" in context


def test_each_card_exception_is_credited_to_its_own_source():
    # How-we-work 37-38 backs "closet or garage?" only; "which job is this row?" is NG Runner's.
    assert not re.search(r'which job is this row\?"[^;]*How-we-work', _item("Q14"))


def test_the_bak_counts_say_which_folders_they_count():
    # 48 of 76 (Q19) and 29 of 79 (item 20) count different folders of the same July copy.
    assert re.search(r"48 of the 76 job folders with \.bak files", _item("Q19"))


def test_jims_docs_use_no_made_up_test_names():
    # Testerson and Fakeley are the tests' made-up clients; to Jim they read as a real job.
    for path in (QUESTIONNAIRE, ACCESS, SETUP_GUIDE):
        assert not re.search(r"Testerson|Fakeley", _read(path)), path.name


def _reading_minutes(text: str) -> tuple[float, float]:
    """Minutes to read text at 200 and at 150 words a minute, plus 20 seconds an answer."""
    answers = len(re.findall(r"(?m)^> $", text)) / 3
    return len(text.split()) / 200 + answers, len(text.split()) / 150 + answers


def _covers(stated: tuple[str, str], estimate: tuple[float, float]) -> bool:
    """A stated "about A–B minutes" range holds the estimate, give or take rounding to 5."""
    low, high = int(stated[0]), int(stated[1])
    return low - 2.5 <= estimate[0] and estimate[1] <= high + 2.5 and high - low <= 15


def test_the_time_estimates_cover_reading_and_answering():
    # Jim is asked to finish sections 1-4 (with the introduction he reads first) before the
    # call, so both docs give that time as well as the whole.
    doc = _read(QUESTIONNAIRE)
    whole = _reading_minutes(doc)
    needed_now = _reading_minutes(doc[: re.search(r"(?m)^## 5\. ", doc).start()])
    said = re.search(r"About (\d+)–(\d+) minutes in all, of which sections 1–4 take about "
                     r"(\d+)–(\d+)", _section(doc, "How to answer"))
    listed = re.search(r"questionnaire \(about (\d+)–(\d+) minutes; sections 1–4, needed before "
                       r"the call, about (\d+)–(\d+)\)", _read(ACCESS))
    assert said and listed, "both docs state the whole time and the time for sections 1-4"
    for found in (said, listed):
        assert _covers(found.groups()[:2], whole), (found[0], whole)
        assert _covers(found.groups()[2:], needed_now), (found[0], needed_now)


def test_remakes_are_asked_apart_from_which_stages_count():
    # One idea per question: the stages, then a yes or no on remakes that decides Complete.
    # AGENTS.md's "Before filing goes live" waits on both answers.
    blocks = {b.split(". ", 1)[0]: b for b in _questions(_read(QUESTIONNAIRE))}
    stages, remakes = blocks["### 3a"], blocks["### 3b"]
    assert "Ready to Order and Ready to Install" in stages and "remake" not in stages.lower()
    assert "**No recommendation:**" in remakes and "Complete" in remakes
    assert "3a" in _agents_item("Before filing goes live", 6)
    assert "3b" in _agents_item("Before filing goes live", 6)


def test_the_questionnaire_keeps_its_template_parts():
    doc = _read(QUESTIONNAIRE)
    for part in ("**Purpose:**",
                 "**From:** Sulaiman, **To:** Jim, **How your answers will be used:**",
                 "## Context", "## How to answer", "## Anything else?"):
        assert part in doc, part


def test_items_are_numbered_in_order_and_every_item_reference_resolves():
    doc = _read(QUESTIONNAIRE)
    items = [(int(n), sub) for n, sub in re.findall(r"(?m)^### (\d+)([a-z]?)\. ", doc)]
    numbers = sorted({n for n, _ in items})
    assert [n for n, _ in items] == sorted(n for n, _ in items)
    assert numbers == list(range(1, len(numbers) + 1))
    for number in numbers:   # a split item runs 3a, 3b, ...
        subs = [sub for n, sub in items if n == number]
        assert subs in ([""], list("abcdef"[:len(subs)])), f"item {number}: {subs}"
    for n, sub in re.findall(r"\bitems? (\d+)([a-z]?)\b", doc):
        assert ((int(n), sub) in items) if sub else (int(n) in numbers), f"item {n}{sub}"


def test_every_q_from_q1_to_q19_is_asked_once_with_sub_ids_a_b_c():
    asked = re.findall(r"(?m)^### Q(\d+)([a-z]?)\. ", _read(QUESTIONNAIRE))
    assert {int(n) for n, _ in asked} == set(range(1, 20))
    assert len(asked) == len(set(asked)), "a Q is asked twice"
    for q in {n for n, _ in asked}:
        letters = sorted(sub for n, sub in asked if n == q)
        assert letters in ([""], list("abcdef"[:len(letters)])), f"Q{q}: {letters}"


def test_week_one_qs_are_in_the_needed_now_sections():
    doc = _read(QUESTIONNAIRE)
    later = re.search(r"(?m)^## \d+\. (?!.*needed now)", doc).start()
    for asked in re.finditer(r"(?m)^### Q(\d+)[a-z]?\. ", doc):
        assert (int(asked[1]) in WEEK_ONE) == (asked.start() < later), asked[0]


# "Signed in" is for signing in to Windows or an account; a program's publisher stamp is its
# "digital signature", so a code-signing question can't be read as "which login?".
_SIGNING = re.compile(r"(?i)\bsign(?:ed|ing|s)?\b(?! in\b| out\b)|\bsign(?:ed)? in [\w']+ name\b")


def test_jims_docs_keep_signing_in_and_digital_signatures_apart():
    for path in (QUESTIONNAIRE, ACCESS):
        assert not [m[0] for m in _SIGNING.finditer(_read(path))], path.name
    item_10 = next(b for b in _questions(_read(QUESTIONNAIRE)) if b.startswith("### 10. "))
    assert "digital signature" in item_10 and "publisher stamp" in item_10


def test_jims_docs_have_no_pictographs_or_absolute_windows_paths():
    for path in (QUESTIONNAIRE, ACCESS):
        text = _read(path)
        assert not [c for c in text if 0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF]
        assert not re.search(r"[A-Za-z]:\\", text), path.name


# ----- every quote from Jim's docs is in the lines it cites

SPEC_DIR = Path(os.environ.get(
    "NGR_SPEC_DIR", r"C:\Sulaiman Automation - Assembly Reports\NG IC MOZAIK"))
# How the questionnaire names Jim's documents (its "How to answer" section), and their files.
SPEC_FILES = {
    "rules": "RULES.md", "build": "BUILD.md", "schema": "SCHEMA.md", "budget": "BUDGET.md",
    "failures": "FAILURES.md", "index": "INDEX.md", "skill": "SKILL - ng-ic-to-mozaik.md",
    "closet runcard": "SKILL-ref - runcard closet.md",
    "garage runcard": "SKILL-ref - garage-runcard.md", "garage spec": "SKILL-ref - garage-spec.md",
    "how-we-work": "How-we-work-together-IC-to-Mozaik.md",
    "10-06 archive": "ARCHIVE - RULES_full_20261006.md",
    "pre-10-11 archive": "ARCHIVE - RULES_before_20261011.md",
    "handoff brief": "NG-Pipeline-App-Handoff-Brief.md",
}
# "RULES 3-4, 57" or "closet runcard 12 and 36-37". A number followed by "archive" belongs to an
# archive's name ("10-06 archive"), never to the citation before it.
_CITE = re.compile(
    r"(?i)\b(" + "|".join(map(re.escape, sorted(SPEC_FILES, key=len, reverse=True))) + r")"
    r"((?:\s*(?:,|and)?\s*\d+(?:-\d+)?(?![\d-]*\s*archive))+)")


def _loose(text: str) -> str:
    """For comparing quotes: no Markdown marks, quote marks or inch signs; one space; lowercase."""
    text = text.replace("**", "").replace("`", "").replace("\\", "").replace("\u2026", " ... ")
    text = re.sub("[\"'\u201c\u201d\u2018\u2019\u2033*]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _citations(line: str) -> list[tuple[str, int, int]]:
    """(document, first line, last line) for every citation on a line."""
    return [(m[1].lower(), int(a), int(b or a)) for m in _CITE.finditer(line)
            for a, b in re.findall(r"(\d+)(?:-(\d+))?", m[2])]


def _fragments(line: str) -> list[str]:
    """The quoted text on a line, split at "..." and middle dots; bits under 4 letters skipped."""
    pieces = (piece for quote in re.findall(r'"([^"]+)"', line)
              for piece in re.split(r"\s*(?:\.\.\.|\u2026|\u00b7)\s*", quote))
    return [piece for piece in pieces if len(_loose(piece)) >= 4]


def misquotes(doc: str, spec: dict[str, list[str]], name: str) -> tuple[list[str], int]:
    """Quotes on bullet lines that are not within three lines of the lines they cite.

    Returns the problems ("<name>:<line>: ...", showing only the questionnaire's own fragment,
    never Jim's text) and how many fragments were checked.
    """
    problems: list[str] = []
    checked = 0
    for no, line in enumerate(doc.splitlines(), 1):
        cites = _citations(line) if line.startswith("- ") else []
        problems += [f"{name}:{no}: a citation runs past the end of {title}"
                     for title, _, last in cites if last > len(spec[title])]
        nearby = [_loose(" ".join(spec[title][max(0, first - 4): last + 3]))
                  for title, first, last in cites]
        for fragment in _fragments(line) if cites else []:
            checked += 1
            if not any(_loose(fragment) in text for text in nearby):
                problems.append(f"{name}:{no}: not in the cited lines: {fragment!r}")
    return problems, checked


@pytest.fixture(scope="module")
def jims_spec() -> dict[str, list[str]]:
    """Jim's spec snapshot, line by line. It names clients, so it is local only (NGR_SPEC_DIR)."""
    if not SPEC_DIR.is_dir():
        pytest.skip("Jim's spec snapshot is not here (set NGR_SPEC_DIR)")
    missing = sorted(f for f in SPEC_FILES.values() if not (SPEC_DIR / f).is_file())
    assert not missing, f"NGR_SPEC_DIR lacks {missing}"
    return {title: (SPEC_DIR / f).read_text(encoding="utf-8-sig").splitlines()
            for title, f in SPEC_FILES.items()}


def test_the_quote_check_finds_misquotes_and_citations_past_the_end():
    spec = {"rules": ["one", "two", "three", "The rule wins \u2014 flag it.", "five"],
            "build": ["Detect, never assume."]}
    doc = "\n".join([
        '- RULES 4: "the rule wins \u2014 flag it" and BUILD 1: "detect ... never assume"',
        '- RULES 1: "The rule wins"',      # three lines away from what it cites: close enough
        '- RULES 2: "never said"',
        '- BUILD 2-3: "detect"',
        'RULES 2: "never said", on a line that is not a bullet, cites nothing',
    ])
    problems, checked = misquotes(doc, spec, "q.md")
    assert problems == ["q.md:3: not in the cited lines: 'never said'",
                        "q.md:4: a citation runs past the end of build"]
    assert checked == 6


@pytest.mark.fixtures
def test_questionnaire_quotes_are_in_the_lines_they_cite(jims_spec):
    problems, checked = misquotes(_read(QUESTIONNAIRE), jims_spec, QUESTIONNAIRE.name)
    print(f"\n{checked} quoted fragments checked against Jim's spec")
    assert checked > 0
    assert not problems, "\n".join(problems)
