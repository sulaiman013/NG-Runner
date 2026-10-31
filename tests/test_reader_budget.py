"""
The reader evaluation's cost guard: a hard cap per run, in dollars, well under the design's USD 20 a
month (decision 16), that stops the run before any call that could cross it. Synthetic only.
"""
from __future__ import annotations

import pytest

from tools.reader_eval import budget


def test_the_default_cap_is_well_under_the_monthly_budget() -> None:
    assert 0 < budget.DEFAULT_CAP_USD <= budget.MONTHLY_BUDGET_USD / 4


def test_a_call_whose_worst_case_fits_goes_ahead_and_its_actual_cost_counts() -> None:
    guard = budget.CostGuard(1.00)
    guard.before(0.30)
    guard.after(0.10)
    assert guard.spent == pytest.approx(0.10) and guard.calls == 1


def test_a_call_that_could_cross_the_cap_is_stopped_before_it_is_made() -> None:
    guard = budget.CostGuard(1.00)
    guard.before(0.30)
    guard.after(0.10)
    with pytest.raises(budget.CapReached):
        guard.before(0.91)
    assert guard.calls == 1 and guard.spent == pytest.approx(0.10)


def test_a_worst_case_exactly_at_the_cap_still_fits() -> None:
    guard = budget.CostGuard(1.00)
    guard.before(1.00)
    guard.after(1.00)
    with pytest.raises(budget.CapReached):
        guard.before(0.000001)


def test_a_call_whose_cost_is_unknown_is_charged_its_worst_case() -> None:
    guard = budget.CostGuard(1.00)
    guard.before(0.40)
    guard.after(None)
    assert guard.spent == pytest.approx(0.40)


def test_a_cost_above_its_worst_case_is_charged_as_it_was() -> None:
    guard = budget.CostGuard(1.00)
    guard.before(0.10)
    guard.after(0.25)
    assert guard.spent == pytest.approx(0.25)


def test_a_second_call_before_the_first_is_settled_is_refused() -> None:
    guard = budget.CostGuard(1.00)
    guard.before(0.10)
    with pytest.raises(budget.CapReached):
        guard.before(0.10)


@pytest.mark.parametrize("cap", [0, -1, 20.01, float("nan")])
def test_a_cap_outside_the_monthly_budget_is_refused(cap: float) -> None:
    with pytest.raises(ValueError):
        budget.CostGuard(cap)


def test_a_negative_or_unknown_worst_case_is_refused() -> None:
    guard = budget.CostGuard(1.00)
    for bad in (-0.01, float("nan"), float("inf")):
        with pytest.raises(budget.CapReached):
            guard.before(bad)
