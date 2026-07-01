# IBKR Portfolio

Personal finance dashboard, built one package at a time. `trades` (this
package) enriches a broker trade export, fetches and caches daily price
history, and compares per-trade returns against a HYSA benchmark. Future
packages (budget, accounting, ...) will live alongside it and feed the same
dashboard.

## Setup

```bash
uv sync
```

## Sync after pulling changes

`uv sync` reads `pyproject.toml`/`uv.lock` and updates `.venv` to match —
run it whenever dependencies change (yours or a teammate's). It installs the
`dev` group (pytest, ruff, jupyter) by default; use `uv sync --no-dev` for a
runtime-only install with none of that.

```bash
uv sync            # everything, including dev tools
uv sync --no-dev   # runtime deps only
```

## Use

Open `notebooks/portfolio.ipynb` and run it — it loads `data/*.csv`, updates
the local price cache, and renders the schedule/return charts and tables.

```bash
uv run jupyter lab notebooks/portfolio.ipynb
```

## Dev

```bash
uv run pytest
uv run ruff check .
```

See `docs/` for how the pieces fit together, how the price cache and Yahoo
API integration work, and how the return math is derived.
