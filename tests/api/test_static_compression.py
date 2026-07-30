"""Pin how the built frontend is served: one variant per `Accept-Encoding`.

These build their own tiny `web/dist`-shaped tree and mount the real
`_PrecompressedStaticFiles` on it, rather than reaching for the repo's actual
`web/dist/`. That directory only exists after someone has run `npm run build`,
so a test that depended on it would pass or fail on whether the machine had
built the frontend — the same trap `serve_frontend` is kept out of the OpenAPI
schema to avoid.

The end-to-end assertions all use gzip, because the standard library can
produce it and so the test proves the bytes really are a valid encoding of the
original rather than merely that a header claimed so. Brotli has no stdlib
encoder, so the `.br` files here hold placeholder bytes and brotli is asserted
at the level that actually decides it — `_negotiated_variant`, which chooses a
file and never reads it.
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trades.api.api import (
    _IMMUTABLE,
    _acceptable_encodings,
    _identity_content_type,
    _negotiated_variant,
    _PrecompressedStaticFiles,
)

if TYPE_CHECKING:
    from pathlib import Path

BODY = b"export const answer = 42;\n" * 200
"""Big enough that the gzip variant is unambiguously smaller than the original."""

CSS = b"body { color: red }\n" * 200


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    """A `dist/assets`-shaped tree: one file with both variants, one with neither."""
    directory = tmp_path / "assets"
    directory.mkdir()
    (directory / "app-abc123.js").write_bytes(BODY)
    (directory / "app-abc123.js.gz").write_bytes(gzip.compress(BODY))
    (directory / "app-abc123.js.br").write_bytes(b"pretend-brotli")
    (directory / "plain-def456.css").write_bytes(CSS)
    return directory


def _client(assets: Path) -> TestClient:
    app = FastAPI()
    app.mount("/assets", _PrecompressedStaticFiles(directory=assets, cache_control=_IMMUTABLE), name="a")
    return TestClient(app)


def test_a_gzip_client_gets_gzip_bytes_that_decode_to_the_original(assets: Path) -> None:
    response = _client(assets).get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    # httpx decodes transparently, so equality here proves the body was a
    # genuine gzip stream of the original file.
    assert response.content == BODY


def test_the_media_type_survives_compression(assets: Path) -> None:
    response = _client(assets).get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})

    # A `.js.gz` is still JavaScript. Guessed from the variant's own filename it
    # would be octet-stream, and the browser would refuse to execute it.
    assert response.headers["content-type"] == "text/javascript; charset=utf-8"


def test_a_client_accepting_no_coding_gets_the_uncompressed_file(assets: Path) -> None:
    response = _client(assets).get("/assets/app-abc123.js", headers={"Accept-Encoding": "identity"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == BODY
    assert response.headers["content-type"] == "text/javascript; charset=utf-8"


def test_a_file_with_no_variant_falls_back_to_the_original(assets: Path) -> None:
    response = _client(assets).get("/assets/plain-def456.css", headers={"Accept-Encoding": "br, gzip"})

    # Precompression is an optimisation, never a requirement: a build that
    # skipped it still serves every file correctly to every client.
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == CSS
    assert response.headers["content-type"] == "text/css; charset=utf-8"


@pytest.mark.parametrize("accept", ["gzip", "identity", "br, gzip"])
def test_every_asset_response_varies_on_accept_encoding(assets: Path, accept: str) -> None:
    response = _client(assets).get("/assets/plain-def456.css", headers={"Accept-Encoding": accept})

    # Without this a shared cache can store one client's encoded body and hand
    # it to a client that cannot decode it.
    assert response.headers["vary"].lower() == "accept-encoding"


def test_hashed_assets_are_cached_immutably(assets: Path) -> None:
    response = _client(assets).get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})

    assert response.headers["cache-control"] == _IMMUTABLE


def test_a_missing_asset_is_still_a_404(assets: Path) -> None:
    response = _client(assets).get("/assets/nope.js", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 404


def test_brotli_wins_when_both_variants_are_acceptable(assets: Path) -> None:
    served, encoding = _negotiated_variant(assets / "app-abc123.js", "br, gzip")

    assert (served.name, encoding) == ("app-abc123.js.br", "br")


def test_a_wildcard_takes_the_best_variant(assets: Path) -> None:
    served, encoding = _negotiated_variant(assets / "app-abc123.js", "*")

    assert (served.name, encoding) == ("app-abc123.js.br", "br")


def test_a_refused_coding_is_not_served(assets: Path) -> None:
    served, encoding = _negotiated_variant(assets / "app-abc123.js", "br;q=0, gzip")

    # br is on disk and would otherwise win the preference order.
    assert (served.name, encoding) == ("app-abc123.js.gz", "gzip")


def test_negotiation_falls_back_to_the_original_when_no_variant_exists(assets: Path) -> None:
    served, encoding = _negotiated_variant(assets / "plain-def456.css", "br, gzip")

    assert (served.name, encoding) == ("plain-def456.css", None)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, set()),
        ("", set()),
        ("gzip", {"gzip"}),
        ("br, gzip", {"br", "gzip"}),
        ("gzip;q=0.5, br;q=1.0", {"gzip", "br"}),
        # q=0 is a refusal, not a weak preference: `gzip;q=0` means "anything
        # but gzip", so treating it as acceptance would send a body the client
        # has just said it cannot take.
        ("gzip;q=0", set()),
        ("br;q=0, gzip", {"gzip"}),
        ("*", {"*"}),
        ("identity", {"identity"}),
        ("GZIP", {"gzip"}),
        ("gzip;q=nonsense", set()),
    ],
)
def test_accept_encoding_parsing(header: str | None, expected: set[str]) -> None:
    assert _acceptable_encodings(header) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("app.js", "text/javascript; charset=utf-8"),
        ("app.css", "text/css; charset=utf-8"),
        ("index.html", "text/html; charset=utf-8"),
        ("icons.svg", "image/svg+xml"),
        ("data.json", "application/json"),
    ],
)
def test_identity_content_type_ignores_the_compression_suffix(filename: str, expected: str) -> None:
    assert _identity_content_type(filename) == expected
