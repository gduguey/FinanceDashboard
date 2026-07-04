import json

from trades.utils.io_utils import write_json_atomic


def test_write_json_atomic_round_trips_a_dict(tmp_path) -> None:
    path = tmp_path / "settings.json"
    write_json_atomic({"target_allocation": {"VOO": 60.0, "BND": 40.0}}, path)
    assert json.loads(path.read_text()) == {"target_allocation": {"VOO": 60.0, "BND": 40.0}}


def test_write_json_atomic_creates_missing_parent_directories(tmp_path) -> None:
    path = tmp_path / "nested" / "settings.json"
    write_json_atomic({"a": 1}, path)
    assert json.loads(path.read_text()) == {"a": 1}


def test_write_json_atomic_leaves_no_tmp_file_behind(tmp_path) -> None:
    path = tmp_path / "settings.json"
    write_json_atomic({"a": 1}, path)
    assert list(tmp_path.iterdir()) == [path]
