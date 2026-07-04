from trades.config import AppConfig
from trades.market_data import symbol_search


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def get(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self._payload)


_PAYLOAD = {
    "quotes": [
        {"symbol": "AAPL", "longname": "Apple Inc.", "shortname": "Apple", "exchDisp": "NASDAQ"},
        {"symbol": "APLE", "shortname": "Apple Hospitality REIT", "exchDisp": "NYSE"},
        {"symbol": "BTC-USD", "isYahooFinance": True},  # no name field at all
    ]
}


def test_search_symbols_returns_symbol_name_and_exchange() -> None:
    session = _FakeSession(_PAYLOAD)
    results = symbol_search.search_symbols("apple", AppConfig(), session=session)
    assert results[0] == {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}
    assert results[1] == {"symbol": "APLE", "name": "Apple Hospitality REIT", "exchange": "NYSE"}


def test_search_symbols_falls_back_to_the_symbol_itself_when_no_name_is_given() -> None:
    session = _FakeSession(_PAYLOAD)
    results = symbol_search.search_symbols("btc", AppConfig(), session=session)
    assert results[2] == {"symbol": "BTC-USD", "name": "BTC-USD", "exchange": ""}


def test_search_symbols_passes_the_query_and_result_limit() -> None:
    session = _FakeSession({"quotes": []})
    config = AppConfig(symbol_search={"max_results": 3})
    symbol_search.search_symbols("voo", config, session=session)
    assert session.calls[0]["params"]["q"] == "voo"
    assert session.calls[0]["params"]["quotesCount"] == 3


def test_search_symbols_with_no_matches_is_an_empty_list() -> None:
    session = _FakeSession({"quotes": []})
    assert symbol_search.search_symbols("zzzznotasymbol", AppConfig(), session=session) == []


def test_search_symbols_skips_entries_with_no_symbol_field() -> None:
    session = _FakeSession({"quotes": [{"longname": "Missing Symbol"}]})
    assert symbol_search.search_symbols("x", AppConfig(), session=session) == []
