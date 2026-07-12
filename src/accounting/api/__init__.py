"""Re-exports `router` and `state` for `trades.api.api` to mount and for tests to patch directly."""

from accounting.api.api import router
from accounting.api.dependencies import state

__all__ = ["router", "state"]
