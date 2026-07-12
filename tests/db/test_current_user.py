import uuid

import pytest

from db.current_user import DEFAULT_USER_ID, get_current_user_id


def test_default_user_id_is_a_fixed_uuid() -> None:
    """Not tied to any real account — see this module's own docstring. Just a stable test fixture id."""
    assert uuid.UUID("00000000-0000-0000-0000-000000000001") == DEFAULT_USER_ID


def test_get_current_user_id_raises_when_never_overridden() -> None:
    with pytest.raises(RuntimeError):
        get_current_user_id()
