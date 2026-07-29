"""Re-exports what `trades.api.api` mounts (`router`, `install_error_handlers`) and what tests patch (`state`)."""

from accounting.api.api import install_error_handlers, router
from accounting.api.dependencies import state

__all__ = ["install_error_handlers", "router", "state"]
