"""Dump the FastAPI app's OpenAPI schema to `web/openapi.json`.

`openapi-typescript` (see `web/package.json`'s `generate:schema` script)
turns that file into `web/src/types/schema.ts`, so the frontend's types
come from the same schema FastAPI itself serves at `/openapi.json` — no
separate hand-maintained TS interfaces to keep in sync by hand.

Run via `uv run python scripts/export_openapi_schema.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from trades.api import app

_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def main() -> None:
    """Write the app's current OpenAPI schema to `web/openapi.json`."""
    _OUTPUT_PATH.write_text(json.dumps(app.openapi(), indent=2) + "\n")


if __name__ == "__main__":
    main()
