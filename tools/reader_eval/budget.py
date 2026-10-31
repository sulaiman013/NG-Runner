"""
The reader evaluation's cost guard: a hard dollar cap per run.

Why before the call, on the worst case: a call's cost is known only after it is made, so the guard
asks each provider for the most that call could cost (its full input at the uncached price, any
cache-write premium, and every output token max_output_tokens allows) and refuses the call when
what is already spent plus that could cross the cap. After the call it charges what the usage says,
or the worst case again when the cost is unknown (an error, a timeout). One call at a time: a call
not yet settled blocks the next.

The default, USD 3, is well under the design's USD 20 a month (decision 16), and no cap above that
monthly budget is accepted.

A run's cap resets with every run, so a total across runs needs a SpendLedger: a JSON file under
.local-fixtures/reader/ that survives restarts (Claude: $50 for this evaluation, ever; Sulaiman,
2026-10-31). Before each call the call's worst case is written to it as pending, and a call whose
worst case could take the total past the ledger's cap is refused. After the call the pending amount
becomes what the call cost (its worst case again when unknown). A process that dies mid-call leaves
the worst case counted. A ledger that can't be read refuses every call, and its cap can't be raised
by reopening it with a larger one.
"""
from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

MONTHLY_BUDGET_USD = 20.0
DEFAULT_CAP_USD = 3.0


class CapReached(Exception):
    """The next call could cross the run's cap, so it is not made."""


class SpendLedger:
    """A spending total kept on disk across runs, with a hard cap (module notes)."""

    def __init__(self, path: Path, cap_usd: float) -> None:
        if not (isinstance(cap_usd, int | float) and math.isfinite(cap_usd) and cap_usd > 0):
            raise ValueError("a ledger's cap is a positive number of dollars")
        self.path = Path(path)
        self.cap = float(cap_usd)
        self._pending: float | None = None

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"cap_usd": self.cap, "spent_usd": 0.0, "pending_usd": 0.0, "calls": 0, "history": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for k in ("cap_usd", "spent_usd", "pending_usd"):
                if not (isinstance(data[k], int | float) and math.isfinite(data[k]) and data[k] >= 0):
                    raise ValueError(k)
        except (OSError, ValueError, KeyError, TypeError):
            raise CapReached(f"the spend ledger {self.path.name} can't be read, so nothing is sent") from None
        if self.cap > data["cap_usd"] + 1e-9:
            raise CapReached(f"the spend ledger's cap is ${data['cap_usd']:.2f} and can't be raised")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def spent(self) -> float:
        """Every dollar charged, plus any worst case still pending (a call that never settled)."""
        data = self._load()
        return data["spent_usd"] + data["pending_usd"]

    def reserve(self, worst_case_usd: float) -> None:
        data = self._load()
        if self._pending is None and data["pending_usd"]:  # left by a run that died mid-call
            data["spent_usd"] += data["pending_usd"]
            data["history"].append({"at": datetime.now(UTC).isoformat(), "charged": data["pending_usd"],
                                    "note": "a call that never settled, charged its worst case"})
            data["pending_usd"] = 0.0
        if data["spent_usd"] + worst_case_usd > min(self.cap, data["cap_usd"]) + 1e-12:
            self._save(data)
            raise CapReached(f"the next call could cost up to ${worst_case_usd:.4f}, and ${data['spent_usd']:.4f} "
                             f"of the ${data['cap_usd']:.2f} total is spent")
        data["pending_usd"] = float(worst_case_usd)
        self._save(data)
        self._pending = float(worst_case_usd)

    def settle(self, actual_usd: float | None) -> float:
        """Charge the call just made; returns what was charged."""
        if self._pending is None:
            raise CapReached("no call was reserved")
        data = self._load()
        known = actual_usd is not None and math.isfinite(actual_usd) and actual_usd >= 0
        charged = float(actual_usd) if known else self._pending
        data["spent_usd"] += charged
        data["pending_usd"] = 0.0
        data["calls"] += 1
        data["history"].append({"at": datetime.now(UTC).isoformat(), "charged": charged,
                                "note": "reported cost" if known else "cost unknown: worst case charged"})
        self._save(data)
        self._pending = None
        return charged


class CostGuard:
    def __init__(self, cap_usd: float = DEFAULT_CAP_USD, *, ledger: SpendLedger | None = None) -> None:
        if not (isinstance(cap_usd, int | float) and 0 < cap_usd <= MONTHLY_BUDGET_USD):
            raise ValueError(f"a run's cap is above $0 and at most the ${MONTHLY_BUDGET_USD:g} monthly budget")
        self.cap = float(cap_usd)
        self.spent = 0.0
        self.calls = 0
        self.ledger = ledger
        self._pending: float | None = None

    def before(self, worst_case_usd: float) -> None:
        """Allow one call whose cost is at most worst_case_usd, or raise CapReached."""
        if self._pending is not None:
            raise CapReached("the last call is not settled yet")
        if not (isinstance(worst_case_usd, int | float) and math.isfinite(worst_case_usd) and worst_case_usd >= 0):
            raise CapReached("the call's worst-case cost is unknown")
        if self.spent + worst_case_usd > self.cap + 1e-12:
            raise CapReached(f"the next call could cost up to ${worst_case_usd:.4f}, and ${self.spent:.4f} "
                             f"of the ${self.cap:.2f} cap is spent")
        if self.ledger is not None:
            self.ledger.reserve(worst_case_usd)
        self._pending = float(worst_case_usd)

    def after(self, actual_usd: float | None) -> None:
        """Charge the call just made: its actual cost, or its worst case when that is unknown."""
        if self._pending is None:
            raise CapReached("no call was allowed")
        known = actual_usd is not None and math.isfinite(actual_usd) and actual_usd >= 0
        self.spent += float(actual_usd) if known else self._pending
        if self.ledger is not None:
            self.ledger.settle(actual_usd if known else None)
        self.calls += 1
        self._pending = None
