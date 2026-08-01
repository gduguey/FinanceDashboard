"""What resolution reads is declared, what is declared has a trigger, and the declaration is checked against reality.

`precedence.RESOLUTION_INPUT_TABLES` is the list the projection's whole
invalidation story hangs off. A list is exactly the thing this repo has been
bitten by three times — RLS policies, wipe-table sets, invalidation keys —
so it gets three guards rather than a comment saying to keep it updated:

1. **`AFFECTED_TRANSACTIONS` covers it exactly.** Asserted at import time in
   `accounting.db.projection`, and again here so the failure is a named test
   rather than a collection error.
2. **Every declared table carries its triggers**, and nothing undeclared
   does — read out of `pg_trigger`, the same way
   `tests/db/test_rls_coverage.py` reads policies out of `pg_policies`.
3. **A real resolution reads nothing undeclared.** This is the one that
   matters. The first two compare a list against another list; this one runs
   `ledger.resolution.resolve_postings` over a ledger exercising every
   overlay and compares the declaration against *the tables the resolver
   actually touched*. A stage that starts joining a new table is caught on
   the day it does, by the resolver's own behaviour rather than by anyone
   remembering.

Guard 3's honesty depends entirely on the fixture covering every stage,
which is why it uses `conftest.seeded_ledger` — the one ledger in this suite
with an assertion of its own that it really does
(`test_the_fixture_exercises_every_overlay`) — rather than seeding something
simpler.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import event, text

import accounting.db  # noqa: F401 — registers the accounting tables on the shared metadata
from accounting.db.projection import (
    AFFECTED_TRANSACTIONS,
    TRIGGER_FUNCTION_PREFIX,
    TRIGGER_NAME_PREFIX,
    ResolvedPosting,
    ResolvedPostingDirty,
)
from accounting.ledger.resolution import resolve_postings
from accounting.precedence import (
    NON_STAGE_SOURCES,
    OVERLAY_SOURCES,
    RESOLUTION_INPUT_TABLES,
    RESOLUTION_READ_EXEMPT,
    RESOLUTION_TRIGGERED_TABLES,
)
from db.base import Base
from tests.conftest import DEFAULT_USER_ID

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_ACCOUNTING_TABLE = re.compile(r"\baccounting\.([a-z_]+)")
"""Every `accounting.<table>` mention in an emitted statement.

Every model in this package declares `schema="accounting"` (see
`accounting.db.core.SCHEMA`), so SQLAlchemy schema-qualifies each one in the
SQL it compiles — which makes the emitted text a faithful record of what a
query touched, with no need to parse it.
"""

_PROJECTION_TABLES = {ResolvedPosting.__tablename__, ResolvedPostingDirty.__tablename__}
"""The projection's own two tables, which resolution neither reads nor writes."""


def _trigger_rows(session: Session) -> set[tuple[str, str]]:
    """Every installed staleness trigger, as `(table, trigger name)`.

    Returns
    -------
    set[tuple[str, str]]
    """
    rows = session.execute(
        text(
            "SELECT c.relname, t.tgname "
            "FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'accounting' AND t.tgname LIKE :prefix"
        ),
        {"prefix": f"{TRIGGER_NAME_PREFIX}%"},
    ).all()
    return {(table, trigger) for table, trigger in rows}


def test_every_declared_input_has_a_mapping_and_every_mapping_a_declared_input() -> None:
    """Guard 1, as a named failure rather than an import error."""
    assert set(AFFECTED_TRANSACTIONS) == set(RESOLUTION_TRIGGERED_TABLES)


def test_the_input_set_is_the_two_declarations_and_nothing_else() -> None:
    """`RESOLUTION_INPUT_TABLES` is derived, never a third list beside the two it unions."""
    declared: set[str] = set()
    for tables in (*OVERLAY_SOURCES.values(), *NON_STAGE_SOURCES.values()):
        declared |= set(tables)
    assert set(RESOLUTION_INPUT_TABLES) == declared
    assert set(RESOLUTION_TRIGGERED_TABLES) == declared - set(RESOLUTION_READ_EXEMPT)


def test_every_declared_input_is_a_real_table() -> None:
    """A typo in the declaration would otherwise install a trigger on nothing and be caught nowhere."""
    known = {table.name for table in Base.metadata.tables.values() if table.schema == "accounting"}
    assert set(RESOLUTION_INPUT_TABLES) <= known, f"declared but not a table: {set(RESOLUTION_INPUT_TABLES) - known}"


def test_every_exemption_carries_a_reason() -> None:
    """An exemption is a claim that no write to the table can change a resolved value — it has to be argued."""
    for table, reason in RESOLUTION_READ_EXEMPT.items():
        assert len(reason) > 100, f"{table} is exempt with a reason too short to be one: {reason!r}"


def test_every_triggered_table_carries_its_four_triggers(db_session: Session) -> None:
    """Guard 2. Four per table, because Postgres refuses a transition table on a multi-event trigger."""
    installed = _trigger_rows(db_session)
    for table in sorted(RESOLUTION_TRIGGERED_TABLES):
        names = {trigger for source, trigger in installed if source == table}
        assert names == {f"{TRIGGER_NAME_PREFIX}{table}_{suffix}" for suffix in ("ins", "upd_new", "upd_old", "del")}, (
            f"{table} does not carry the four staleness triggers; it has {sorted(names)}"
        )


def test_no_undeclared_table_carries_a_staleness_trigger(db_session: Session) -> None:
    """The other direction: a trigger left behind on a table no longer declared an input is dead invalidation."""
    triggered = {table for table, _trigger in _trigger_rows(db_session)}
    assert triggered == set(RESOLUTION_TRIGGERED_TABLES)


def test_every_exempt_table_carries_no_trigger(db_session: Session) -> None:
    """An exemption that still fired would make the argument for it untestable."""
    triggered = {table for table, _trigger in _trigger_rows(db_session)}
    assert triggered.isdisjoint(RESOLUTION_READ_EXEMPT)


def test_each_triggered_table_has_its_own_trigger_function(db_session: Session) -> None:
    """One function per table, so the whole set is greppable in `pg_proc` and each is statically planned."""
    installed = {
        row.proname
        for row in db_session.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'accounting' AND p.proname LIKE :prefix"
            ),
            {"prefix": f"{TRIGGER_FUNCTION_PREFIX.split('.', 1)[1]}%"},
        ).all()
    }
    expected = {f"{TRIGGER_FUNCTION_PREFIX.split('.', 1)[1]}{table}" for table in RESOLUTION_TRIGGERED_TABLES}
    assert installed == expected


def test_a_real_resolution_reads_no_table_outside_the_declaration(seeded_ledger, db_session: Session) -> None:
    """Guard 3, and the reason the other two are not enough.

    Runs the resolver over a ledger that exercises every overlay stage,
    recording the `accounting` tables its statements actually name, and
    holds that set against what `precedence` declares. A stage that starts
    joining a new table fails here on the day it does — no list to remember,
    and no way to add an input silently.

    Both directions are asserted. Reading something undeclared is the
    dangerous one: it means a write that can change a resolved value has no
    trigger behind it. Declaring something never read is the harmless one,
    but it is still wrong — an over-declaration is an invalidation nobody
    can justify, and `budgets` is in `RESOLUTION_READ_EXEMPT` precisely
    because the honest place for "read, but cannot change the answer" is an
    argued exemption rather than a quietly wider trigger set.
    """
    # The fixture ran its writes through this same session, so identity-mapped
    # objects and unexpired attributes are still resident. A resolution stage
    # reading one of them emits no statement, the table never enters
    # `observed`, and this fails for a reason that has nothing to do with the
    # declaration — or, worse, an undeclared read is masked the same way.
    db_session.expire_all()
    seen: set[str] = set()

    def _record(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        seen.update(_ACCOUNTING_TABLE.findall(statement))

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        resolve_postings(db_session, DEFAULT_USER_ID)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    observed = seen - _PROJECTION_TABLES
    undeclared = observed - set(RESOLUTION_INPUT_TABLES)
    assert not undeclared, (
        f"resolution read {sorted(undeclared)}, which `precedence` does not declare as an input — so a write to "
        f"one of them changes a resolved value with no staleness trigger behind it"
    )
    never_read = set(RESOLUTION_INPUT_TABLES) - observed
    assert not never_read, (
        f"`precedence` declares {sorted(never_read)} as resolution inputs, but resolving a ledger that exercises "
        f"every overlay never read them — either the declaration is too wide or the fixture stopped covering a stage"
    )
