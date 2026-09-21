"""Standard Library for StateGuard.

Provides universally required business rules and generic compensation functions
so developers can enforce guardrails out-of-the-box.
"""

from .rules import (
    require_bounds,
    require_regex,
    require_length,
    require_keys,
    require_enum,
    valid_transition,
    require_monotonic,
    max_steps_breaker,
    require_not_empty,
)

from .compensations import (
    delete_file,
    webhook_rollback,
    log_warning,
)

__all__ = [
    "require_bounds",
    "require_regex",
    "require_length",
    "require_keys",
    "require_enum",
    "valid_transition",
    "require_monotonic",
    "max_steps_breaker",
    "require_not_empty",
    "delete_file",
    "webhook_rollback",
    "log_warning",
]

