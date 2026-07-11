import polars as pl

from accounting.utils.io_utils import write_csv_atomic


def test_write_csv_atomic_round_trips_a_dataframe(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    frame = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    write_csv_atomic(frame, path)
    assert pl.read_csv(path).equals(frame)


def test_write_csv_atomic_collects_a_lazyframe(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    frame = pl.DataFrame({"a": [1, 2]}).lazy()
    write_csv_atomic(frame, path)
    assert pl.read_csv(path)["a"].to_list() == [1, 2]


def test_write_csv_atomic_leaves_no_tmp_file_behind(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    write_csv_atomic(pl.DataFrame({"a": [1]}), path)
    assert list(tmp_path.iterdir()) == [path]
