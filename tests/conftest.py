"""Shared test fixtures. Real IC summaries live only in .local-fixtures (gitignored)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))


def pytest_configure(config: pytest.Config) -> None:
    """Refuse a run without -X warn_default_encoding: Python then raises no EncodingWarning, so
    a text open without encoding= (AGENTS.md rule 7) would pass, whichever tests are run."""
    if not sys.flags.warn_default_encoding:
        raise pytest.UsageError(
            r"run the tests with .venv\Scripts\python.exe -X warn_default_encoding -m pytest -q"
            " (AGENTS.md: Commands)"
        )


@pytest.fixture(scope="session")
def summaries_dir() -> Path:
    """Folder of real IC summary PDFs; tests that need it are skipped when it is absent."""
    d = FIXTURES / "summaries"
    if not d.is_dir() or not any(d.rglob("*.pdf")):
        pytest.skip("no local IC summary fixtures: fill .local-fixtures or set NGR_FIXTURES"
                    " (see AGENTS.md: Fixtures)")
    return d
