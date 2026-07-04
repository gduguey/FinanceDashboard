"""All charting lives here.

Every function takes data already computed by `transactions.py` /
`returns.py` and returns a `plotly.graph_objects.Figure` — no aggregation
or fetching happens in this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
from plotly.subplots import make_subplots

if TYPE_CHECKING:
    import numpy as np
    import polars as pl

    from trades.config import AppConfig


def plot_monthly_invested(monthly_df: pl.DataFrame) -> go.Figure:
    """Render a stacked bar of USD invested per month, one series per symbol.

    Parameters
    ----------
    monthly_df
        One row per month (see `transactions.monthly_invested`), with a
        `month` column, one column per symbol, and a `Total` column.

    Returns
    -------
    plotly.graph_objects.Figure
        The stacked bar chart.
    """
    symbols = [column for column in monthly_df.columns if column not in {"Total", "month"}]
    fig = go.Figure()
    for symbol in symbols:
        fig.add_trace(go.Bar(x=monthly_df["month"], y=monthly_df[symbol], name=symbol))
    fig.update_layout(
        title="Monthly invested, by symbol",
        xaxis_title="Month",
        yaxis_title="USD invested",
        barmode="stack",
    )
    return fig


def plot_daily_investment_timeline(daily_df: pl.DataFrame) -> go.Figure:
    """Render bars of USD invested per investment day plus a cumulative-invested line.

    Hovering a bar also shows the gap since the previous buy.

    Parameters
    ----------
    daily_df
        One row per investment day (see `transactions.daily_investment_timeline`).

    Returns
    -------
    plotly.graph_objects.Figure
        The combined bar-and-line chart.
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=daily_df["trade_date"],
            y=daily_df["usd_spent"],
            name="Invested that day",
            marker_color="steelblue",
            customdata=daily_df.select("days_since_previous_investment").to_numpy(),
            hovertemplate=(
                "%{x|%Y-%m-%d}<br>Invested: $%{y:,.2f}<br>Days since prior buy: %{customdata[0]}<extra></extra>"
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
            line={"color": "firebrick"},
        ),
        secondary_y=True,
    )
    fig.update_layout(title="Investment timeline", hovermode="x unified")
    fig.update_yaxes(title_text="USD invested (day)", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative USD invested", secondary_y=True)
    return fig


def plot_investment_pie(options: dict[str, pl.DataFrame]) -> go.Figure:
    """Render a pie chart with a dropdown menu to switch between breakdowns.

    Parameters
    ----------
    options
        Label -> two-column breakdown (see `transactions.pie_chart_options`);
        the first column supplies pie labels, the second supplies values.

    Returns
    -------
    plotly.graph_objects.Figure
        The pie chart with a dropdown selector.
    """
    labels = list(options.keys())
    default_label_column, default_value_column = options[labels[0]].columns
    fig = go.Figure(
        data=[
            go.Pie(
                labels=options[labels[0]][default_label_column].cast(str).to_list(),
                values=options[labels[0]][default_value_column].to_list(),
            )
        ]
    )
    buttons = [
        {
            "label": label,
            "method": "update",
            "args": [
                {"labels": [df[df.columns[0]].cast(str).to_list()], "values": [df[df.columns[1]].to_list()]},
                {"title": label},
            ],
        }
        for label, df in options.items()
    ]
    fig.update_layout(
        title=labels[0],
        updatemenus=[{"active": 0, "buttons": buttons, "x": 1.2, "y": 1, "xanchor": "left"}],
    )
    return fig


def plot_return_curve(
    returns_df: pl.DataFrame,
    trend_x: np.ndarray,
    trend_y: np.ndarray,
    config: AppConfig,
) -> go.Figure:
    """Render per-trade annualized return vs. days held, with a fitted trend line.

    Also draws a flat line for the HYSA benchmark defined by
    `config.returns.hysa_annual_rate`.

    Parameters
    ----------
    returns_df
        A returns table (see `returns.build_returns_table`).
    trend_x
        Trend-line x-coordinates (see `returns.fit_trend`).
    trend_y
        Trend-line y-coordinates (see `returns.fit_trend`).
    config
        Application configuration; `config.returns.hysa_annual_rate` is read.

    Returns
    -------
    plotly.graph_objects.Figure
        The scatter plot with trend and benchmark lines.
    """
    hysa_annual_rate = config.returns.hysa_annual_rate
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
    fig.add_trace(go.Scatter(x=trend_x, y=trend_y, mode="lines", name="Trend", line={"color": "black", "dash": "dash"}))
    fig.add_hline(
        y=hysa_annual_rate * 100,
        line={"color": "green", "dash": "dot"},
        annotation_text=f"{hysa_annual_rate:.0%} HYSA benchmark",
    )
    fig.update_layout(
        title="Per-trade annualized return vs. holding period",
        xaxis_title="Days held",
        yaxis_title="Annualized return (%)",
    )
    return fig


def render_table(df: pl.DataFrame, title: str) -> go.Figure:
    """Render a DataFrame as a table, for notebook display.

    Parameters
    ----------
    df
        The data to render.
    title
        The chart title.

    Returns
    -------
    plotly.graph_objects.Figure
        The table figure.
    """
    cell_values = [df[column].to_list() for column in df.columns]
    fig = go.Figure(data=[go.Table(header={"values": df.columns}, cells={"values": cell_values})])
    fig.update_layout(title=title)
    return fig
