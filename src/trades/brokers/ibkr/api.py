"""IBKR Flex Web Service: fetch the "Trade History API" Flex Query (Cash
Report + Open Positions + Trades) and keep a local, append-only ledger
cache of it.

See docs/ibkr_flex_api.md for how the two-step SendRequest/GetStatement
protocol and its error codes work.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from trades.brokers.ibkr.models import IbkrCashTransaction, IbkrTrade, drop_tz_suffix
from trades.config import IbkrFlexApiConfig, IbkrFlexCredentials

# IBKR's own documented Flex Web Service v3 error codes (docs/ibkr_flex_api.md)
# — protocol facts, not tunable parameters.
_RETRYABLE_GENERATING_CODES = frozenset(
    {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1019", "1021"}
)
_RETRYABLE_THROTTLED_CODES = frozenset({"1018"})


class FlexApiError(RuntimeError):
    """The Flex Web Service returned a non-retryable error code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"IBKR Flex API error {code}: {message}")


@dataclass(frozen=True)
class ParsedStatement:
    from_date: date
    to_date: date
    when_generated: datetime
    trades: list[IbkrTrade]
    cash_transactions: list[IbkrCashTransaction]


# ---------------------------------------------------------------------------
# Network: the two-step SendRequest / GetStatement protocol
# ---------------------------------------------------------------------------


def _send_flex_request(
    credentials: IbkrFlexCredentials, config: IbkrFlexApiConfig
) -> tuple[str, str]:
    """Step 1: exchange the query ID for a reference code and statement URL."""
    response = requests.get(
        config.send_request_url,
        params={"v": "3", "t": credentials.token.get_secret_value(), "q": credentials.query_id},
        headers=config.request_headers,
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    if root.findtext("Status") != "Success":
        raise FlexApiError(
            root.findtext("ErrorCode", "unknown"), root.findtext("ErrorMessage", response.text)
        )
    reference_code = root.findtext("ReferenceCode")
    if not reference_code:
        raise FlexApiError("unknown", "SendRequest succeeded but returned no ReferenceCode")
    return reference_code, root.findtext("Url") or config.fallback_statement_url


def _poll_flex_statement(
    reference_code: str,
    statement_url: str,
    credentials: IbkrFlexCredentials,
    config: IbkrFlexApiConfig,
) -> str:
    """Step 2: poll until the statement is ready, honoring IBKR's documented
    retry codes; raises on any other error or after `max_poll_attempts`."""
    for _ in range(config.max_poll_attempts):
        response = requests.get(
            statement_url,
            params={"v": "3", "t": credentials.token.get_secret_value(), "q": reference_code},
            headers=config.request_headers,
            timeout=config.request_timeout_seconds,
        )
        response.raise_for_status()
        if "<FlexQueryResponse" in response.text:
            return response.text

        root = ET.fromstring(response.text)
        code = root.findtext("ErrorCode", "")
        if code in _RETRYABLE_GENERATING_CODES:
            time.sleep(config.server_busy_retry_seconds)
        elif code in _RETRYABLE_THROTTLED_CODES:
            time.sleep(config.throttled_retry_seconds)
        else:
            raise FlexApiError(code or "unknown", root.findtext("ErrorMessage", response.text))
    raise FlexApiError("timeout", f"Statement not ready after {config.max_poll_attempts} attempts")


def fetch_flex_statement(credentials: IbkrFlexCredentials, config: IbkrFlexApiConfig) -> str:
    """The one network entrypoint: run the full SendRequest -> GetStatement
    exchange and return the raw `FlexQueryResponse` XML."""
    reference_code, statement_url = _send_flex_request(credentials, config)
    return _poll_flex_statement(reference_code, statement_url, credentials, config)


# ---------------------------------------------------------------------------
# Parsing: raw XML -> validated pydantic rows (no I/O below this line)
# ---------------------------------------------------------------------------


def parse_statement(xml_text: str) -> ParsedStatement:
    """Only `<Trade>` and `<CashTransaction>` feed the ledger; everything
    else the statement carries (positions, cash balance) is left alone —
    it's still archived verbatim in `raw_statements/`, just not parsed."""
    root = ET.fromstring(xml_text)
    statement = root.find(".//FlexStatement")
    if statement is None:
        raise FlexApiError("unknown", "FlexQueryResponse XML has no <FlexStatement> element")
    return ParsedStatement(
        from_date=datetime.strptime(statement.attrib["fromDate"], "%Y-%m-%d").date(),
        to_date=datetime.strptime(statement.attrib["toDate"], "%Y-%m-%d").date(),
        when_generated=datetime.strptime(
            str(drop_tz_suffix(statement.attrib["whenGenerated"])), "%Y-%m-%d %H:%M:%S"
        ),
        trades=[IbkrTrade.model_validate(el.attrib) for el in root.iter("Trade")],
        cash_transactions=[
            IbkrCashTransaction.model_validate(el.attrib) for el in root.iter("CashTransaction")
        ],
    )


# ---------------------------------------------------------------------------
# Local cache: read/write ledger.csv under config.cache_dir
# ---------------------------------------------------------------------------


def save_raw_statement(xml_text: str, received_at: datetime, config: IbkrFlexApiConfig) -> Path:
    """Archive the exact bytes IBKR returned, before any parsing is
    attempted, so a parsing or merge bug can never lose a fetched statement.
    Files are never overwritten."""
    raw_dir = config.raw_statement_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{received_at:%Y%m%dT%H%M%S}.xml"
    suffix = 1
    while path.exists():
        path = raw_dir / f"{received_at:%Y%m%dT%H%M%S}-{suffix}.xml"
        suffix += 1
    path.write_text(xml_text, encoding="utf-8")
    return path


def last_synced_at(config: IbkrFlexApiConfig) -> datetime | None:
    """Local wall-clock time of the most recent sync."""
    raw_paths = list(config.raw_statement_dir.glob("*.xml"))
    if not raw_paths:
        return None
    # "-N" suffix is the same-second collision tag `_save_raw_statement` appends.
    stems = (path.stem.split("-")[0] for path in raw_paths)
    return max(datetime.strptime(stem, "%Y%m%dT%H%M%S") for stem in stems)