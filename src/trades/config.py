"""Every tunable value in this package lives here, as a field on one of
these frozen config objects — never as a bare module-level constant that a
function silently falls back to. Callers construct the config they need
(defaults below are the package's suggested values, not hidden ones) and
pass it explicitly; nothing is picked up implicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_REPO_ROOT = Path(__file__).resolve().parents[2]


class AggregationConfig(BaseModel):
    """Controls how same-day, same-symbol fills are merged."""

    model_config = ConfigDict(frozen=True)

    same_day_price_tolerance: float = Field(
        default=0.0001,
        gt=0,
        lt=1,
        description="Max relative $/share difference for two same-day fills to be merged.",
    )


class PriceApiConfig(BaseModel):
    """Where price history is cached and how the Yahoo Finance API is called."""

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "prices"
    chart_url_template: str = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    request_headers: dict[str, str] = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 (compatible; trades/0.1)"}
    )
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class ReturnsConfig(BaseModel):
    """The return/annualization math and the cash benchmark to compare against."""

    model_config = ConfigDict(frozen=True)

    annualization_days: int = Field(default=365, gt=0)
    hysa_annual_rate: float = Field(default=0.04, ge=0)
    trend_fit_kind: Literal["linear", "mean"] = "linear"
