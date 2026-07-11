from accounting.config import AccountingConfig
from accounting.llm.settings import (
    LLMCredentialOverride,
    load_llm_credential_override,
    resolve_llm_credentials,
    save_llm_credential_override,
)


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


def test_load_llm_credential_override_with_no_file_yet_is_empty(tmp_path) -> None:
    assert load_llm_credential_override(_config(tmp_path)) == LLMCredentialOverride()


def test_save_then_load_llm_credential_override_round_trips(tmp_path) -> None:
    config = _config(tmp_path)
    save_llm_credential_override(LLMCredentialOverride(gemini_api_key="gem-key", mistral_api_key="mis-key"), config)
    reloaded = load_llm_credential_override(config)
    assert reloaded.gemini_api_key == "gem-key"
    assert reloaded.mistral_api_key == "mis-key"


def test_resolve_llm_credentials_uses_the_saved_override(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = _config(tmp_path)
    save_llm_credential_override(LLMCredentialOverride(gemini_api_key="gem-key"), config)
    credentials = resolve_llm_credentials(config)
    assert credentials.gemini_api_key.get_secret_value() == "gem-key"


def test_resolve_llm_credentials_leaves_an_unset_field_to_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "env-mistral-key")
    config = _config(tmp_path)
    save_llm_credential_override(LLMCredentialOverride(gemini_api_key="gem-key"), config)
    credentials = resolve_llm_credentials(config)
    assert credentials.gemini_api_key.get_secret_value() == "gem-key"
    assert credentials.mistral_api_key.get_secret_value() == "env-mistral-key"
