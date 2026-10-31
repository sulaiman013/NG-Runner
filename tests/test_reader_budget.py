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


# ----- a total across every run, kept on disk (Claude: $50 for this evaluation, ever)


def test_a_ledger_keeps_its_total_across_restarts(tmp_path) -> None:
    path = tmp_path / "spend.json"
    ledger = budget.SpendLedger(path, 50.0)
    ledger.reserve(1.0)
    ledger.settle(0.4)
    assert budget.SpendLedger(path, 50.0).spent == pytest.approx(0.4)


def test_a_call_that_could_take_the_total_past_the_ledger_cap_is_refused(tmp_path) -> None:
    ledger = budget.SpendLedger(tmp_path / "spend.json", 50.0)
    ledger.reserve(49.0)
    ledger.settle(49.0)
    with pytest.raises(budget.CapReached):
        budget.SpendLedger(tmp_path / "spend.json", 50.0).reserve(1.01)


def test_a_crash_between_reserve_and_settle_leaves_the_worst_case_counted(tmp_path) -> None:
    path = tmp_path / "spend.json"
    budget.SpendLedger(path, 50.0).reserve(2.5)  # the process dies here
    again = budget.SpendLedger(path, 50.0)
    assert again.spent == pytest.approx(2.5)
    with pytest.raises(budget.CapReached):
        again.reserve(47.6)


def test_an_unreadable_ledger_refuses_rather_than_reading_as_zero(tmp_path) -> None:
    path = tmp_path / "spend.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(budget.CapReached):
        budget.SpendLedger(path, 50.0).reserve(0.01)


def test_a_ledger_cannot_be_reopened_with_a_higher_cap(tmp_path) -> None:
    path = tmp_path / "spend.json"
    budget.SpendLedger(path, 50.0).reserve(0.1)
    with pytest.raises(budget.CapReached):
        budget.SpendLedger(path, 80.0).reserve(0.1)


def test_a_guard_with_a_ledger_refuses_what_fits_the_run_but_not_the_total(tmp_path) -> None:
    path = tmp_path / "spend.json"
    old = budget.SpendLedger(path, 50.0)
    old.reserve(48.0)
    old.settle(48.0)
    guard = budget.CostGuard(3.0, ledger=budget.SpendLedger(path, 50.0))
    guard.before(1.5)
    guard.after(1.2)
    with pytest.raises(budget.CapReached):
        guard.before(1.0)  # 49.2 + 1.0 > 50, though the run has $1.80 left
    assert budget.SpendLedger(path, 50.0).spent == pytest.approx(49.2)
