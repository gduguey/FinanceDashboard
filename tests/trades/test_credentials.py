from trades.config import AppConfig, CredentialOverridesConfig
from trades.credentials import (
    IbkrCredentialOverride,
    ibkr_is_configured,
    load_ibkr_credential_override,
    resolve_ibkr_credentials,
    save_ibkr_credential_override,
)


def _config(tmp_path) -> AppConfig:
    return AppConfig(credentials=CredentialOverridesConfig(ibkr_credentials_path=tmp_path / "credentials.json"))


def test_load_ibkr_credential_override_with_no_file_yet_is_empty(tmp_path) -> None:
    override = load_ibkr_credential_override(_config(tmp_path))
    assert override == IbkrCredentialOverride()


def test_save_then_load_ibkr_credential_override_round_trips(tmp_path) -> None:
    config = _config(tmp_path)
    override = IbkrCredentialOverride(token="secret-token", query_id="12345")  # noqa: S106
    save_ibkr_credential_override(override, config)
    reloaded = load_ibkr_credential_override(config)
    assert reloaded.token == "secret-token"  # noqa: S105
    assert reloaded.query_id == "12345"


def test_resolve_ibkr_credentials_uses_the_saved_override(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("IBKR_FLEX_WEB_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_QUERY_ID", raising=False)
    config = _config(tmp_path)
    override = IbkrCredentialOverride(token="secret-token", query_id="12345")  # noqa: S106
    save_ibkr_credential_override(override, config)
    credentials = resolve_ibkr_credentials(config)
    assert credentials.token.get_secret_value() == "secret-token"
    assert credentials.query_id == "12345"


def test_resolve_ibkr_credentials_falls_back_to_the_environment_for_an_unset_field(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IBKR_QUERY_ID", "env-query-id")
    config = _config(tmp_path)
    override = IbkrCredentialOverride(token="secret-token")  # noqa: S106
    save_ibkr_credential_override(override, config)
    credentials = resolve_ibkr_credentials(config)
    assert credentials.token.get_secret_value() == "secret-token"
    assert credentials.query_id == "env-query-id"


def test_ibkr_is_configured_true_once_an_override_is_saved(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("IBKR_FLEX_WEB_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_QUERY_ID", raising=False)
    config = _config(tmp_path)
    override = IbkrCredentialOverride(token="secret-token", query_id="12345")  # noqa: S106
    save_ibkr_credential_override(override, config)
    assert ibkr_is_configured(config) is True
