"""The gates that measure cost rather than correctness.

Two of them, both deselected from the default `pytest` run by `-m "not perf"`
(see `pyproject.toml`) and both executed by the same CI job.

`test_read_path_latency.py` is the latency gate over the paginated
`accounting` read paths: a migrated, bulk-seeded database, two tenants five
times apart in size, and a ratio per path.

`test_replay_scaling.py` is the cost-curve gate over `trades`'
`ledger.replay.replay_ledger`. It needs no database at all — the thing it
measures is a fold over an in-memory frame — and it exists because nothing
measured that fold, which is how a quadratic survived in it until item C4b.

Each module says what it measures and where its own bounds come from.
"""
