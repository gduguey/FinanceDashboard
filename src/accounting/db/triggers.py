"""The one invariant in this schema that no key, `CHECK`, or index can express.

Everything else structural in `accounting.db` is declarative: a `CHECK`
looks at one row, a `UNIQUE` looks at one column tuple, a `FOREIGN KEY`
looks at one referenced row. Double-entry's defining rule is none of those
shapes — "the postings of a transaction sum to zero" is a statement about a
*set* of rows, and it is only ever true or false at the end of a
transaction, never in the middle of one (the first leg of a two-leg
transfer is unbalanced by construction).

So this is a `CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`: the
one mechanism Postgres offers that runs at `COMMIT` rather than per
statement. Deferring is not a convenience here, it is the only thing that
makes the check possible at all — `importers.ingest._write_ledger` writes
postings leg by leg and prunes stale ones in a separate pass, and every
intermediate state it passes through is unbalanced. Callers need no
`SET CONSTRAINTS`: `INITIALLY DEFERRED` already puts the check at commit
time for every transaction, so a bulk write is checked exactly once, on the
whole final state, rather than once per statement.

## What is checked, precisely

A transaction is checked **only when all of its postings share one
currency**. A cross-currency transfer's two legs are equal-and-opposite
only after a conversion this schema deliberately does not store (see
`models.ManualTransfer`: the user enters what actually left one side and
what actually arrived at the other, never a rate), so summing them is
meaningless. That is the same rule
`ledger.replay.unbalanced_transactions` documents, and this is now the
place it is enforced.

`ledger.replay.validate_balanced` stays where it is rather than being
deleted. It is not redundant with this: it runs over the *imported* frame
before anything is written, so a bad statement fails the import with a
message naming the offending transaction and its running total, instead of
surfacing as a `COMMIT`-time trigger abort with no idea which of ten
thousand rows caused it. The engine is the guarantee; the Python check is
the diagnostic.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from accounting.db.core import SCHEMA, Posting

ZERO_SUM_FUNCTION_NAME = f"{SCHEMA}.assert_transaction_balances"
"""The trigger function, schema-qualified so `DROP SCHEMA ... CASCADE` takes it with everything else."""

ZERO_SUM_TRIGGER_NAME = "postings_balance_at_commit"
"""The constraint trigger on `accounting.postings`, greppable in `pg_trigger`."""

_CREATE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {ZERO_SUM_FUNCTION_NAME}() RETURNS trigger AS $$
DECLARE
    affected uuid := COALESCE(NEW.transaction_id, OLD.transaction_id);
    imbalance numeric;
BEGIN
    SELECT SUM(amount) INTO imbalance
    FROM {SCHEMA}.postings
    WHERE transaction_id = affected
    HAVING COUNT(DISTINCT currency) = 1;

    IF imbalance IS NOT NULL AND imbalance <> 0 THEN
        RAISE EXCEPTION
            'transaction % postings sum to %, not zero', affected, imbalance
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = '{ZERO_SUM_TRIGGER_NAME}';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""  # noqa: S608 — the only interpolations are this module's own constants, never input

_CREATE_TRIGGER_SQL = f"""
CREATE CONSTRAINT TRIGGER {ZERO_SUM_TRIGGER_NAME}
AFTER INSERT OR UPDATE OR DELETE ON {SCHEMA}.postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION {ZERO_SUM_FUNCTION_NAME}();
"""

ZERO_SUM_STATEMENTS: tuple[str, ...] = (_CREATE_FUNCTION_SQL, _CREATE_TRIGGER_SQL)
"""Every statement that installs the zero-sum guard, in order.

Executed from two places against one definition: the baseline migration
(the real database) and the `after_create` hook below (the test suite's
`Base.metadata.create_all`, which knows nothing about triggers). Without
the hook, the guard would exist in production and in no test.
"""

for _statement in ZERO_SUM_STATEMENTS:
    # `DDL` runs its text through `%`-interpolation before executing it, and
    # the `RAISE EXCEPTION` format string above is full of `%` placeholders
    # PL/pgSQL wants to keep. Doubling them here rather than in
    # `ZERO_SUM_STATEMENTS` keeps the migration's own `op.execute` — which
    # does no such interpolation — reading the SQL exactly as written.
    event.listen(Posting.__table__, "after_create", DDL(_statement.replace("%", "%%")))
