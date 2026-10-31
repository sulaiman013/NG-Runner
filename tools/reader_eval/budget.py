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
"""
from __future__ import annotations

import math

MONTHLY_BUDGET_USD = 20.0
DEFAULT_CAP_USD = 3.0


class CapReached(Exception):
    """The next call could cross the run's cap, so it is not made."""


class CostGuard:
    def __init__(self, cap_usd: float = DEFAULT_CAP_USD) -> None:
        if not (isinstance(cap_usd, int | float) and 0 < cap_usd <= MONTHLY_BUDGET_USD):
            raise ValueError(f"a run's cap is above $0 and at most the ${MONTHLY_BUDGET_USD:g} monthly budget")
        self.cap = float(cap_usd)
        self.spent = 0.0
        self.calls = 0
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
        self._pending = float(worst_case_usd)

    def after(self, actual_usd: float | None) -> None:
        """Charge the call just made: its actual cost, or its worst case when that is unknown."""
        if self._pending is None:
            raise CapReached("no call was allowed")
        known = actual_usd is not None and math.isfinite(actual_usd) and actual_usd >= 0
        self.spent += float(actual_usd) if known else self._pending
        self.calls += 1
        self._pending = None
