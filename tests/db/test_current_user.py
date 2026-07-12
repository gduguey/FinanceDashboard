import pytest

from db.current_user import get_current_user_id


def test_get_current_user_id_raises_when_never_overridden() -> None:
    with pytest.raises(RuntimeError):
        get_current_user_id()
