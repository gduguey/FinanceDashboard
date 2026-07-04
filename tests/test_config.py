import pytest
from pydantic import ValidationError

from trades.config import (
    AggregationConfig,
    AppConfig,
    IbkrFlexApiConfig,
    IbkrFlexCredentials,
    LedgerConfig,
    ReturnsConfig,
)


def test_ledger_config_default_cash_symbol() -> None:
    assert LedgerConfig().cash_symbol == "CASH"


def test_app_config_composes_every_sub_config() -> None:
    config = AppConfig()
    assert config.ledger.cash_symbol == "CASH"
    assert config.aggregation.same_day_price_tolerance == pytest.approx(0.0001)
    assert config.prices.request_timeout_seconds == pytest.approx(10.0)
    assert config.cpi.series_id == "CPIAUCSL"
    assert config.returns.hysa_annual_rate == pytest.approx(0.04)
    assert config.ibkr.max_poll_attempts == 10


def test_app_config_is_frozen() -> None:
    config = AppConfig()
    with pytest.raises(ValidationError):
        config.returns = ReturnsConfig(hysa_annual_rate=0.05)


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


def test_ibkr_flex_credentials_requires_env_vars(monkeypatch) -> None:
    monkeypatch.delenv("IBKR_FLEX_WEB_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_QUERY_ID", raising=False)
    with pytest.raises(ValidationError):
        IbkrFlexCredentials(_env_file=None)


def test_ibkr_flex_credentials_reads_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "secret-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")
    credentials = IbkrFlexCredentials(_env_file=None)
    assert credentials.token.get_secret_value() == "secret-token"
    assert credentials.query_id == "12345"


def test_ibkr_flex_credentials_token_is_not_exposed_in_repr(monkeypatch) -> None:
    monkeypatch.setenv("IBKR_FLEX_WEB_SERVICE_TOKEN", "secret-token")
    monkeypatch.setenv("IBKR_QUERY_ID", "12345")
    credentials = IbkrFlexCredentials(_env_file=None)
    assert "secret-token" not in repr(credentials)


def test_ibkr_flex_api_config_defaults() -> None:
    config = IbkrFlexApiConfig()
    assert config.max_poll_attempts == 10
    assert config.cache_dir.name == "ibkr"


def test_ibkr_flex_api_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        IbkrFlexApiConfig(request_timeout_seconds=0)
