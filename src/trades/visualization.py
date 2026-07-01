"""All charting lives here. Every function takes data already computed by
`transactions.py` / `returns.py` and returns a `plotly.graph_objects.Figure` —
no aggregation or fetching happens in this module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trades.config import ReturnsConfig


def plot_monthly_invested(monthly_df: pd.DataFrame) -> go.Figure:
    """Stacked bar of USD invested per month, one series per symbol."""
    symbols = [c for c in monthly_df.columns if c != "Total"]
    fig = go.Figure()
    for symbol in symbols:
        fig.add_trace(go.Bar(x=monthly_df.index, y=monthly_df[symbol], name=symbol))
    fig.update_layout(
        title="Monthly invested, by symbol",
        xaxis_title="Month",
        yaxis_title="USD invested",
        barmode="stack",
    )
    return fig


def plot_daily_investment_timeline(daily_df: pd.DataFrame) -> go.Figure:
    """Bars of USD invested per investment day plus a cumulative-invested
    line; hovering a bar also shows the gap since the previous buy."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=daily_df["trade_date"],
            y=daily_df["usd_spent"],
            name="Invested that day",
            marker_color="steelblue",
            customdata=daily_df[["days_since_previous_investment"]].to_numpy(),
            hovertemplate=(
                "%{x|%Y-%m-%d}<br>Invested: $%{y:,.2f}"
                "<br>Days since prior buy: %{customdata[0]}<extra></extra>"
            ),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=daily_df["trade_date"],
            y=daily_df["cumulative_usd_spent"],
            name="Cumulative invested",
            mode="lines+markers",
            line=dict(color="firebrick"),
        ),
        secondary_y=True,
    )
    fig.update_layout(title="Investment timeline", hovermode="x unified")
    fig.update_yaxes(title_text="USD invested (day)", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative USD invested", secondary_y=True)
    return fig


def plot_investment_pie(options: dict[str, pd.Series]) -> go.Figure:
    """Pie chart with a dropdown menu switching between the given (label ->
    series) breakdowns, e.g. "whole portfolio by symbol" vs "SYMBOL by date"."""
    labels = list(options.keys())
    default = options[labels[0]]
    fig = go.Figure(
        data=[go.Pie(labels=[str(i) for i in default.index], values=default.to_numpy().tolist())]
    )
    buttons = [
        dict(
            label=label,
            method="update",
            args=[
                {
                    "labels": [[str(i) for i in series.index]],
                    "values": [series.to_numpy().tolist()],
                },
                {"title": label},
            ],
        )
        for label, series in options.items()
    ]
    fig.update_layout(
        title=labels[0],
        updatemenus=[dict(active=0, buttons=buttons, x=1.2, y=1, xanchor="left")],
    )
    return fig


def plot_return_curve(
    returns_df: pd.DataFrame,
    trend_x: np.ndarray,
    trend_y: np.ndarray,
    config: ReturnsConfig,
) -> go.Figure:
    """Per-trade annualized return vs. days held, with a fitted trend line
    and a flat line for the HYSA benchmark defined by `config.hysa_annual_rate`."""
    hysa_annual_rate = config.hysa_annual_rate
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=returns_df["days_held"],
            y=returns_df["annualized_return_pct"],
            mode="markers",
            name="Per-trade annualized return",
            text=returns_df["symbol"],
            hovertemplate="%{text}<br>Days held: %{x}<br>Annualized: %{y:.1f}%<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=trend_x, y=trend_y, mode="lines", name="Trend", line=dict(color="black", dash="dash")
        )
    )
    fig.add_hline(
        y=hysa_annual_rate * 100,
        line=dict(color="green", dash="dot"),
        annotation_text=f"{hysa_annual_rate:.0%} HYSA benchmark",
    )
    fig.update_layout(
        title="Per-trade annualized return vs. holding period",
        xaxis_title="Days held",
        yaxis_title="Annualized return (%)",
    )
    return fig


def render_table(df: pd.DataFrame, title: str) -> go.Figure:
    """Quick, presentable rendering of a DataFrame for notebook display."""
    fig = go.Figure(
        data=[
            go.Table(
                header=dict(values=list(df.columns)), cells=dict(values=[df[c] for c in df.columns])
            )
        ]
    )
    fig.update_layout(title=title)
    return fig
