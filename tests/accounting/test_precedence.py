"""Overlay precedence is declared data, not the line order of the resolution pipeline.

`accounting.precedence` is the declaration; `accounting.ledger.resolution`
walks it; each overlay table's `stage` column CHECK restates the subset of
it that table may hold. These tests are what keeps those three from
drifting apart — a stage nobody applies, an applier for a stage nobody
declares, or a table pinned to a stage that isn't in the vocabulary would
all otherwise fail silently, by an overlay quietly never running.
"""

from __future__ import annotations

import re

import accounting.db  # noqa: F401 — registers every accounting table on the shared metadata
from accounting.ledger.resolution import overlay_context
from accounting.precedence import OVERLAY_PRECEDENCE, OverlayStage
from db.base import Base

_STAGE_CHECK_VALUE = re.compile(r"'([^']+)'")

_EXPECTED_ORDER = ("counterparty", "split", "override", "merge", "link")
"""The order the pipeline used to hard-code, written out once here.

Deliberately a literal rather than derived from `OVERLAY_PRECEDENCE`:
reordering the declaration is exactly the change that silently alters every
resolved ledger, so it has to break a test that spells the old order out.

`manual_transfer` used to sit between `merge` and `link`, generating
postings out of a table of its own. It is gone, not reordered: a manual
transfer is a real `manual`-origin transaction now, so its postings arrive
with the raw ledger and there is nothing left for a stage to contribute.
"""


def _stage_checks() -> dict[str, set[str]]:
    """Every table with a `stage` column, mapped to the values its CHECK allows.

    Returns
    -------
    dict[str, set[str]]
    """
    allowed: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        if "stage" not in table.columns:
            continue
        for constraint in table.constraints:
            sqltext = str(getattr(constraint, "sqltext", ""))
            if sqltext.startswith("stage IN ("):
                allowed[table.name] = set(_STAGE_CHECK_VALUE.findall(sqltext))
    return allowed


def test_the_declared_order_is_the_order_the_pipeline_used_to_hard_code() -> None:
    assert OVERLAY_PRECEDENCE == _EXPECTED_ORDER


def test_the_precedence_tuple_is_the_vocabulary_itself() -> None:
    """`OVERLAY_PRECEDENCE` is `OverlayStage`'s own declaration order, never a second copy of it."""
    assert set(OVERLAY_PRECEDENCE) == set(OverlayStage.__args__)
    assert len(OVERLAY_PRECEDENCE) == len(OverlayStage.__args__)


def test_every_declared_stage_has_an_applier(db_session, test_user_id) -> None:
    """A stage with no applier is an overlay that silently never runs."""
    assert set(overlay_context(db_session, test_user_id, rules=[]).appliers) == set(OVERLAY_PRECEDENCE)


def test_every_stage_carrying_table_pins_itself_to_a_declared_stage() -> None:
    checks = _stage_checks()
    assert checks, "no table declares a stage — the precedence column has gone missing"
    for table, allowed in checks.items():
        assert allowed <= set(OVERLAY_PRECEDENCE), f"{table} allows a stage nobody declares: {allowed}"


def test_every_overlay_table_the_resolver_reads_declares_its_stage() -> None:
    """The tables behind the five stages, each pinned to the one (or two) it is applied at."""
    assert _stage_checks() == {
        "categorization_rules": {"counterparty", "override"},
        "posting_splits": {"split"},
        "posting_overrides": {"override"},
        "posting_merges": {"merge"},
        "transfer_links": {"link"},
    }


def test_category_resolution_is_not_an_overlay_and_declares_no_stage() -> None:
    """`categories` is a dimension the overlays point *into*, not a layer over the ledger.

    Resolving a posting's imported category through the taxonomy's own
    retirements runs before the first stage (see
    `ledger.resolution.apply_overlays`), and deliberately
    carries no `stage` column: giving it one would declare a precedence
    relative to the overlays that it does not have, since every overlay's
    own `category_id` is a foreign key into the same table.
    """
    assert "categories" not in _stage_checks()
    assert "taxonomy" not in OVERLAY_PRECEDENCE
