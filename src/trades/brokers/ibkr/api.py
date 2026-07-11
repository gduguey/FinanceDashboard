"""IBKR Flex Web Service client.

Fetches the "Trade History API" Flex Query (Cash Report + Open Positions +
Trades) via IBKR's Flex Web Service and parses the response. See
docs/trades/ibkr_flex_api.md for how the two-step SendRequest/GetStatement
protocol and its error codes work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

import requests
from defusedxml import ElementTree

from trades.brokers.ibkr.models import IbkrCashTransaction, IbkrTrade, parse_ibkr_datetime
from trades.utils.statement_archive import DEFAULT_USER_ID, StatementArchive

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from trades.config import AppConfig, IbkrFlexCredentials

# IBKR's own documented Flex Web Service v3 error codes (docs/trades/ibkr_flex_api.md)
# — protocol facts, not tunable parameters.
_RETRYABLE_GENERATING_CODES = frozenset({"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1019", "1021"})
_RETRYABLE_THROTTLED_CODES = frozenset({"1018"})


class FlexApiError(RuntimeError):
    """The Flex Web Service returned a non-retryable error code."""

    def __init__(self, code: str, message: str) -> None:
        """Store the error code and message and build the exception text.

        Parameters
        ----------
        code
            IBKR's Flex Web Service error code.
        message
            IBKR's error message text.
        """
        self.code = code
        self.message = message
        super().__init__(f"IBKR Flex API error {code}: {message}")


@dataclass(frozen=True)
class ParsedStatement:
    """One parsed `<FlexStatement>`."""

    from_date: date
    to_date: date
    when_generated: datetime
    trades: list[IbkrTrade]
    cash_transactions: list[IbkrCashTransaction]


def _send_flex_request(
    credentials: IbkrFlexCredentials, config: AppConfig, on_progress: Callable[[str, float], None] | None = None
) -> tuple[str, str]:
    if on_progress:
        on_progress("Requesting IBKR statement", 5.0)
    try:
        response = requests.get(
            config.ibkr.send_request_url,
            params={"v": "3", "t": credentials.token.get_secret_value(), "q": credentials.query_id},
            headers=config.ibkr.request_headers,
            timeout=config.ibkr.request_timeout_seconds,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        # Not str(error): a request-level failure's own message includes the
        # full request URL, which embeds the token as a query param above —
        # never let that reach a caller.
        raise FlexApiError("network_error", "Could not reach IBKR's Flex Web Service") from error
    root = ElementTree.fromstring(response.text)
    if root.findtext("Status") != "Success":
        raise FlexApiError(root.findtext("ErrorCode", "unknown"), root.findtext("ErrorMessage", response.text))
    reference_code = root.findtext("ReferenceCode")
    if not reference_code:
        raise FlexApiError("unknown", "SendRequest succeeded but returned no ReferenceCode")
    return reference_code, root.findtext("Url") or config.ibkr.fallback_statement_url


def _poll_flex_statement(
    reference_code: str,
    statement_url: str,
    credentials: IbkrFlexCredentials,
    config: AppConfig,
    on_progress: Callable[[str, float], None] | None = None,
) -> str:
    for attempt in range(config.ibkr.max_poll_attempts):
        if on_progress:
            # Spends most of its allotted band waiting on IBKR to finish
            # generating the statement, which is usually the slowest step.
            fraction_done = attempt / config.ibkr.max_poll_attempts
            on_progress("Waiting for IBKR to respond", 10.0 + 30.0 * fraction_done)
        response = requests.get(
            statement_url,
            params={"v": "3", "t": credentials.token.get_secret_value(), "q": reference_code},
            headers=config.ibkr.request_headers,
            timeout=config.ibkr.request_timeout_seconds,
        )
        response.raise_for_status()
        if "<FlexQueryResponse" in response.text:
            return response.text

        root = ElementTree.fromstring(response.text)
        code = root.findtext("ErrorCode", "")
        if code in _RETRYABLE_GENERATING_CODES:
            time.sleep(config.ibkr.server_busy_retry_seconds)
        elif code in _RETRYABLE_THROTTLED_CODES:
            time.sleep(config.ibkr.throttled_retry_seconds)
        else:
            raise FlexApiError(code or "unknown", root.findtext("ErrorMessage", response.text))
    message = f"Statement not ready after {config.ibkr.max_poll_attempts} attempts"
    raise FlexApiError("timeout", message)


def verify_flex_credentials(credentials: IbkrFlexCredentials, config: AppConfig) -> None:
    """Check that the token/query id actually authenticate, without generating a full report.

    Just the SendRequest step — a single fast HTTP call — not the
    GetStatement poll loop `fetch_flex_statement` does afterward, which
    can take up to a minute waiting for IBKR to generate the report.
    An invalid token or query id surfaces here immediately as a non-Success
    status, so this is cheap enough to run whenever Settings wants to know
    "does this actually work" rather than just "is something typed in." Lets
    `_send_flex_request`'s `FlexApiError` propagate as-is if IBKR rejects
    the token/query id.

    Parameters
    ----------
    credentials
        The IBKR Flex Web Service token and query id to check.
    config
        Application configuration; `config.ibkr` is read.
    """
    _send_flex_request(credentials, config)


def fetch_flex_statement(
    credentials: IbkrFlexCredentials, config: AppConfig, on_progress: Callable[[str, float], None] | None = None
) -> str:
    """Run the SendRequest -> GetStatement exchange and return the raw statement XML.

    Parameters
    ----------
    credentials
        The IBKR Flex Web Service token and query id.
    config
        Application configuration; `config.ibkr` is read.
    on_progress
        Called with a short step description and a 0-100 percentage as the
        exchange proceeds, for a live sync-progress display. Optional.

    Returns
    -------
    str
        The raw `<FlexQueryResponse>` XML.
    """
    reference_code, statement_url = _send_flex_request(credentials, config, on_progress)
    return _poll_flex_statement(reference_code, statement_url, credentials, config, on_progress)


def parse_statement(xml_text: str) -> ParsedStatement:
    """Parse a `<FlexQueryResponse>` document's `<Trade>`/`<CashTransaction>` rows.

    Everything else the statement carries (positions, cash balance) is
    left alone — it is still archived verbatim in `raw_statements/`, just
    not parsed.

    Parameters
    ----------
    xml_text
        The raw `<FlexQueryResponse>` XML.

    Returns
    -------
    ParsedStatement
        The statement's coverage window and validated trade/cash-transaction
        rows. `when_generated` is naive UTC.

    Raises
    ------
    FlexApiError
        If the XML has no `<FlexStatement>` element.
    """
    root = ElementTree.fromstring(xml_text)
    statement = root.find(".//FlexStatement")
    if statement is None:
        raise FlexApiError("unknown", "FlexQueryResponse XML has no <FlexStatement> element")
    return ParsedStatement(
        from_date=datetime.strptime(statement.attrib["fromDate"], "%Y-%m-%d").date(),  # noqa: DTZ007
        to_date=datetime.strptime(statement.attrib["toDate"], "%Y-%m-%d").date(),  # noqa: DTZ007
        when_generated=cast("datetime", parse_ibkr_datetime(statement.attrib["whenGenerated"])),
        trades=[IbkrTrade.model_validate(element.attrib) for element in root.iter("Trade")],
        cash_transactions=[
            IbkrCashTransaction.model_validate(element.attrib) for element in root.iter("CashTransaction")
        ],
    )


def save_raw_statement(xml_text: str, received_at: datetime, config: AppConfig) -> None:
    """Archive the exact bytes IBKR returned, before any parsing is attempted.

    Written to R2 (`statements/<user_id>/ibkr/...`) when configured, else to
    `config.ibkr.raw_statement_dir` on disk — see `utils.statement_archive`.
    Files are never overwritten: a same-second collision (e.g. two calls in
    a fast test loop, or two overlapping syncs) gets a numeric suffix
    instead of clobbering the first — enforced by attempting an atomic
    create per candidate name (`StatementArchive.write_if_absent`) rather
    than checking existence first and writing second, which two concurrent
    callers could both pass before either had written anything.

    Parameters
    ----------
    xml_text
        The raw statement XML to archive.
    received_at
        The naive-UTC time the statement was received (see
        `models.py`'s storage convention).
    config
        Application configuration; `config.ibkr.raw_statement_dir` is read.
    """
    archive = StatementArchive(config.ibkr.raw_statement_dir, f"statements/{DEFAULT_USER_ID}/ibkr")
    data = xml_text.encode("utf-8")
    suffix = 0
    while True:
        relative_path = (
            f"{received_at:%Y%m%dT%H%M%S}.xml" if suffix == 0 else f"{received_at:%Y%m%dT%H%M%S}-{suffix}.xml"
        )
        if archive.write_if_absent(relative_path, data):
            return
        suffix += 1


def last_synced_at(config: AppConfig) -> datetime | None:
    """Look up the naive-UTC time of the most recent sync.

    Parameters
    ----------
    config
        Application configuration; `config.ibkr.raw_statement_dir` is read.

    Returns
    -------
    datetime.datetime or None
        The receive time of the most recently archived raw statement, or
        None if nothing has ever been synced.
    """
    archive = StatementArchive(config.ibkr.raw_statement_dir, f"statements/{DEFAULT_USER_ID}/ibkr")
    relative_paths = archive.list_relative_paths("*.xml")
    if not relative_paths:
        return None
    # "-N" suffix is the same-second collision tag `save_raw_statement` appends.
    stems = (relative_path.removesuffix(".xml").split("-")[0] for relative_path in relative_paths)
    return max(datetime.strptime(stem, "%Y%m%dT%H%M%S") for stem in stems)  # noqa: DTZ007
