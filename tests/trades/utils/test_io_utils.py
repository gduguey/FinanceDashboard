import polars as pl
import pytest

from trades.utils import cache_backup
from trades.utils.io_utils import read_csv_recovering_from_corruption, write_csv_atomic


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


def test_read_csv_recovering_from_corruption_reads_a_healthy_file_normally(tmp_path) -> None:
    path = tmp_path / "prices.csv"
    write_csv_atomic(pl.DataFrame({"a": [1, 2]}), path)

    result = read_csv_recovering_from_corruption(path, "prices/AAPL.csv")

    assert result["a"].to_list() == [1, 2]


def test_read_csv_recovering_from_corruption_repairs_from_backup_and_retries(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    path = tmp_path / "prices.csv"
    good = pl.DataFrame({"a": [1, 2]})
    write_csv_atomic(good, path)
    cache_backup.backup_cache_file(
        path, "prices/AAPL.csv", credentials=cache_backup.CacheBackupR2Credentials(_env_file=None)
    )

    # Corrupt the file after it's been backed up — an unescaped quote makes
    # polars raise ComputeError, the same failure mode a botched write or a
    # Docker volume issue would produce.
    path.write_text('a\n1\n"unterminated\n')

    result = read_csv_recovering_from_corruption(path, "prices/AAPL.csv")

    assert result["a"].to_list() == [1, 2]
    assert path.read_text() == good.write_csv()


def test_read_csv_recovering_from_corruption_raises_when_no_backup_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    path = tmp_path / "prices.csv"
    path.write_text('a\n1\n"unterminated\n')

    with pytest.raises(pl.exceptions.ComputeError):
        read_csv_recovering_from_corruption(path, "prices/AAPL.csv")

    # The still-broken file is left exactly as it was — no silent "empty
    # cache" fallback that would hide the corruption from a human.
    assert path.read_text() == 'a\n1\n"unterminated\n'
