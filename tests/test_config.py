import pytest
from pydantic import ValidationError

from trades.config import AggregationConfig, ReturnsConfig


def test_aggregation_config_default_tolerance() -> None:
    assert AggregationConfig().same_day_price_tolerance == pytest.approx(0.0001)


def test_aggregation_config_rejects_non_positive_tolerance() -> None:
    with pytest.raises(ValidationError):
        AggregationConfig(same_day_price_tolerance=0)


def test_returns_config_rejects_negative_hysa_rate() -> None:
    with pytest.raises(ValidationError):
        ReturnsConfig(hysa_annual_rate=-0.01)


def test_config_objects_are_frozen() -> None:
    config = ReturnsConfig()
    with pytest.raises(ValidationError):
        config.hysa_annual_rate = 0.05
