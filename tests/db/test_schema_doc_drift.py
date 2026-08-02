"""`docs/schema.md`'s table inventory is checked against `Base.metadata`, in both directions.

`alembic check` gates the models against the database on every PR. Nothing
gated the *document* against either, and `git log` showed three commits on it
across the whole post-rewrite history — so its "last verified" line was a
promise nobody could keep. This is item E8.

What this catches, and what it deliberately does not
----------------------------------------------------
It catches a table **added, removed or renamed** without the document moving,
and a **count that no longer adds up**. That is the drift that actually bites:
a reader looking up a table finds nothing, or finds a section for a table that
no longer exists.

It does **not** catch a stale sentence. No parser can see that a *Notes* column
still describes a column's old semantics, or that §4's rationale argues for a
decision that was reversed. Those stay a human pass, and §6's reproduction
commands are what that pass uses. Widening this file to assert columns, keys or
indexes would be the beginning of the generator PR C considered and declined
(item E7) — it would cover §3's inventory and still not cover the *Notes*
column, which is where the document's value is.

Three places name a table, and all three are checked
----------------------------------------------------
§3 has one `#### \\`schema.table\\`` heading per table, which is the obvious
one. But §3 was already exactly right when this gate was written — a
heading-only check would have shipped green as a no-op. The drift was in the
other two:

- **§1's counts**, three of which were wrong (`trades` said 5 with 6 tables,
  §3's conventions said 37, §4's RLS arithmetic started from 35).
- **§2's aggregate map**, which listed 35 of 38 tables — `trades.sync_runs` and
  both projection tables belonged to no aggregate at all, which is the more
  serious omission of the two, because §2 is the section that answers "who owns
  this table".

So a table is documented only when it appears in all three, and each is a
separate assertion naming what it found.

No database. This reads a file and a `MetaData`, so it runs in the default
suite rather than needing the migrated scratch database `test_rls_coverage.py`
builds.
"""

from __future__ import annotations

import re
from pathlib import Path

import accounting.db  # noqa: F401 — registers the accounting tables on the shared metadata
import db.models  # noqa: F401 — registers the public tables
import trades.db  # noqa: F401 — registers the trades tables
from db.base import Base

SCHEMA_DOC = Path(__file__).resolve().parents[2] / "docs" / "schema.md"
"""The document under test. Resolved from this file so it does not depend on the working directory."""

_ALEMBIC_VERSION = "alembic_version"
"""Alembic's own bookkeeping table, which is in the database and not in `Base.metadata`.

The document counts it separately for exactly that reason — it is not an
application table and nothing in `src/` reads it.
"""

_SECTION = re.compile(r"^## (\d+)\. ", re.MULTILINE)
"""Each top-level section heading, used to cut the document into the parts each assertion reads."""

_TABLE_HEADING = re.compile(r"^#### `([a-z_]+\.[a-z_]+)`", re.MULTILINE)
"""§3's per-table heading. Every table is schema-qualified and backticked, `public` included."""

_QUALIFIED_TABLE = re.compile(r"`([a-z_]+\.[a-z_]+)`")
"""A schema-qualified table name anywhere in a line — how §2's aggregate map names its tables."""

_SCHEMA_COUNT_ROW = re.compile(r"^\| `(public|accounting|trades)` \| (\d+) \|", re.MULTILINE)
"""§1's per-schema count table."""

_TOTAL_TABLES = re.compile(r"\*\*(\d+) application tables\*\*")
"""§1's headline figure, which the per-schema table has to sum to."""


def _section(number: int) -> str:
    """The text of one numbered section, from its heading to the next one.

    Returns
    -------
    str
    """
    starts = [(int(match.group(1)), match.start()) for match in _SECTION.finditer(SCHEMA_DOC.read_text())]
    text = SCHEMA_DOC.read_text()
    for index, (found, start) in enumerate(starts):
        if found == number:
            end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
            return text[start:end]
    message = f"docs/schema.md has no section {number}"
    raise AssertionError(message)


def _qualified(name: str) -> str:
    """One `Base.metadata` key as the document writes it — schema-qualified, `public` included.

    SQLAlchemy omits the default schema from its key, so `users` is `users`
    and `accounting.postings` is `accounting.postings`. The document
    qualifies every table, which is the more useful convention in prose and
    the one this normalises to.

    Returns
    -------
    str
    """
    return name if "." in name else f"public.{name}"


def _model_tables() -> set[str]:
    """Every table the models declare, schema-qualified.

    Returns
    -------
    set[str]
    """
    return {_qualified(name) for name in Base.metadata.tables}


def test_section_3_documents_exactly_the_tables_the_models_declare() -> None:
    """One `#### `schema.table`` heading per table, no heading without a table and no table without a heading."""
    documented = _TABLE_HEADING.findall(_section(3))

    assert len(documented) == len(set(documented)), (
        f"§3 documents a table twice: {sorted({name for name in documented if documented.count(name) > 1})}"
    )
    models = _model_tables()
    assert set(documented) - models == set(), (
        f"§3 documents tables the models do not declare: {sorted(set(documented) - models)}"
    )
    assert models - set(documented) == set(), (
        f"the models declare tables §3 does not document: {sorted(models - set(documented))}"
    )


def test_section_2_places_every_table_in_an_aggregate() -> None:
    """§2 answers "who owns this table", so a table missing from it has no owner on the page.

    This is the assertion that would have caught the drift that was there
    when the gate was written: `trades.sync_runs`, `accounting.resolved_postings`
    and `accounting.resolved_postings_dirty` were all documented in §3 and
    belonged to no aggregate.

    Exactly one aggregate per table, not at least one. §2's own rule is that
    "each aggregate loads and writes its own tables independently"; a table
    named in two rows contradicts that, and a set comparison alone would let
    it through.
    """
    rows = [line for line in _section(2).splitlines() if line.startswith("| **")]
    assert rows, "§2's aggregate table is not where this expects it"
    placements = [table for row in rows for table in _QUALIFIED_TABLE.findall(row)]
    twice = sorted({table for table in placements if placements.count(table) > 1})
    assert twice == [], f"§2 places these tables in more than one aggregate, so neither owns them: {twice}"

    placed = set(placements)
    models = _model_tables()
    assert placed - models == set(), f"§2 places tables the models do not declare: {sorted(placed - models)}"
    assert models - placed == set(), f"the models declare tables §2 places in no aggregate: {sorted(models - placed)}"


def test_section_1s_per_schema_counts_match_the_models() -> None:
    """The count table is the first thing a reader sees, and the easiest to leave behind."""
    counted = {schema: int(count) for schema, count in _SCHEMA_COUNT_ROW.findall(_section(1))}
    assert set(counted) == {"public", "accounting", "trades"}, f"§1's count table lists {sorted(counted)}"

    actual: dict[str, int] = {"public": 0, "accounting": 0, "trades": 0}
    for name in _model_tables():
        actual[name.split(".", 1)[0]] += 1
    assert counted == actual, f"§1 counts {counted}; the models declare {actual}"


def test_section_1s_headline_total_is_the_sum_of_its_own_table() -> None:
    """The headline figure has to be both the sum of the rows above it and the real number."""
    section = _section(1)
    totals = _TOTAL_TABLES.findall(section)
    assert len(totals) == 1, f"§1 states an application-table total {len(totals)} times"

    counted = {schema: int(count) for schema, count in _SCHEMA_COUNT_ROW.findall(section)}
    assert int(totals[0]) == sum(counted.values()), (
        f"§1 says {totals[0]} application tables and its own per-schema table sums to {sum(counted.values())}"
    )
    assert int(totals[0]) == len(Base.metadata.tables), (
        f"§1 says {totals[0]} application tables; the models declare {len(Base.metadata.tables)}"
    )
    assert f"{int(totals[0]) + 1} counting `{_ALEMBIC_VERSION}`" in section, (
        f"§1's `{_ALEMBIC_VERSION}` figure no longer follows from its application-table total"
    )
