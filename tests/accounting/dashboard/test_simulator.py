import pytest

from accounting.dashboard.simulator import project


def test_project_with_no_growth_or_contribution_stays_flat() -> None:
    points = project(1000.0, 0.0, 1, 0.0)
    assert points[0].balance == pytest.approx(1000.0)
    assert points[-1].balance == pytest.approx(1000.0)
    assert len(points) == 13  # month 0 through month 12, inclusive


def test_project_with_monthly_compounding_matches_the_closed_form_monthly_rate() -> None:
    points = project(1000.0, 0.0, 1, 12.0, compounding_frequency="monthly")
    # 12%/yr compounding monthly is exactly 1%/month.
    assert points[12].balance == pytest.approx(1000.0 * (1.01**12))


def test_project_adds_the_monthly_contribution_every_month() -> None:
    points = project(0.0, 100.0, 1, 0.0)
    assert points[1].balance == pytest.approx(100.0)
    assert points[12].balance == pytest.approx(1200.0)
    assert points[12].contributions_to_date == pytest.approx(1200.0)


def test_project_annual_compounding_grows_slower_than_monthly_at_the_same_nominal_rate() -> None:
    annually = project(1000.0, 0.0, 1, 12.0, compounding_frequency="annually")
    monthly = project(1000.0, 0.0, 1, 12.0, compounding_frequency="monthly")
    assert annually[12].balance < monthly[12].balance
    assert annually[12].balance == pytest.approx(1120.0)


def test_project_rounds_a_fractional_horizon_to_the_nearest_month() -> None:
    points = project(1000.0, 0.0, 0.5, 0.0)
    assert len(points) == 7  # 6 months, plus month 0
