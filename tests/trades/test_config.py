import pytest
from pydantic import ValidationError

from trades.config import (
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
    assert config.prices.request_timeout_seconds == pytest.approx(10.0)
    assert config.cpi.series_id == "CPIAUCSL"
    assert config.returns.hysa_annual_rate == pytest.approx(0.04)
    assert config.ibkr.max_poll_attempts == 10


def test_app_config_is_frozen() -> None:
    config = AppConfig()
    with pytest.raises(ValidationError):
        config.returns = ReturnsConfig(hysa_annual_rate=0.05)


def test_returns_config_default_benchmark_symbol() -> None:
    assert ReturnsConfig().benchmark_symbol == "VOO"


def test_returns_config_rejects_negative_hysa_rate() -> None:
    with pytest.raises(ValidationError):
        ReturnsConfig(hysa_annual_rate=-0.01)


def test_config_objects_are_frozen() -> None:
    config = ReturnsConfig()
    with pytest.raises(ValidationError):
        config.hysa_annual_rate = 0.05


def test_ibkr_flex_credentials_requires_both_fields() -> None:
    with pytest.raises(ValidationError):
        IbkrFlexCredentials(token="secret-token", query_id="")  # noqa: S106


def test_ibkr_flex_credentials_round_trips_explicit_fields() -> None:
    credentials = IbkrFlexCredentials(token="secret-token", query_id="12345")  # noqa: S106
    assert credentials.token.get_secret_value() == "secret-token"
    assert credentials.query_id == "12345"


def test_ibkr_flex_credentials_token_is_not_exposed_in_repr() -> None:
    credentials = IbkrFlexCredentials(token="secret-token", query_id="12345")  # noqa: S106
    assert "secret-token" not in repr(credentials)


def test_ibkr_flex_api_config_defaults() -> None:
    config = IbkrFlexApiConfig()
    assert config.max_poll_attempts == 10
    assert config.cache_dir.name == "ibkr"


def test_ibkr_flex_api_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        IbkrFlexApiConfig(request_timeout_seconds=0)
