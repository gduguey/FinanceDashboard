"""The latency gate over the paginated read paths.

Deselected from the default `pytest` run by `-m "not perf"` (see
`pyproject.toml`) and executed by its own CI job. See
`test_read_path_latency.py` for what is measured and why the thresholds are
the numbers they are.
"""
