"""Standard library of pre-built business rules (invariants) for StateGuard.

These rules return constructed `Invariant` objects that can be directly
added to the registry using `registry.add(rule)`.
"""

import re
from typing import Any, List, Optional, Dict, Set, Type
from stateguard.invariants import Invariant


def require_bounds(
    key: str, min_val: Optional[float] = None, max_val: Optional[float] = None, mode: str = "block"
) -> Invariant:
    """Ensures a numeric field stays within specified bounds (inclusive)."""

    def fn(state: Any) -> bool:
        val = state.get(key)
        if val is None:
            return False
        if min_val is not None and val < min_val:
            return False
        if max_val is not None and val > max_val:
            return False
        return True

    msg = f"Field '{key}' must be "
    if min_val is not None and max_val is not None:
        msg += f"between {min_val} and {max_val}."
    elif min_val is not None:
        msg += f">= {min_val}."
    elif max_val is not None:
        msg += f"<= {max_val}."

    return Invariant(fn, name=f"require_bounds_{key}", mode=mode, error_message=msg)


def require_regex(key: str, pattern: str, mode: str = "block") -> Invariant:
    """Ensures a string field matches the provided regex pattern."""
    regex = re.compile(pattern)

    def fn(state: Any) -> bool:
        val = state.get(key)
        if not isinstance(val, str):
            return False
        return bool(regex.match(val))

    return Invariant(
        fn,
        name=f"require_regex_{key}",
        mode=mode,
        error_message=f"Field '{key}' must match pattern '{pattern}'.",
    )


def require_length(
    key: str, min_len: Optional[int] = None, max_len: Optional[int] = None, mode: str = "block"
) -> Invariant:
    """Ensures a string or list field falls within length bounds."""

    def fn(state: Any) -> bool:
        val = state.get(key)
        if not hasattr(val, "__len__"):
            return False
        length = len(val)
        if min_len is not None and length < min_len:
            return False
        if max_len is not None and length > max_len:
            return False
        return True

    return Invariant(
        fn,
        name=f"require_length_{key}",
        mode=mode,
        error_message=f"Field '{key}' length out of bounds.",
    )


def require_keys(
    keys: List[str],
    mode: str = "block",
    exact: bool = False,
) -> Invariant:
    """Ensures the state dictionary contains all the specified keys.

    Args:
        keys:   List of keys that MUST be present in state.
        mode:   'block' (raises) or 'warn' (logs only).
        exact:  If True, also BLOCKS any key not in ``keys`` (schema lock).
                This prevents LLM key injection attacks where an agent
                adds unexpected fields like ``free_money: True``.
    """
    required: Set[str] = set(keys)

    def fn(state: Any) -> bool:
        state_keys: Set[str] = set(state.keys())

        # Check all required keys are present
        missing = required - state_keys
        if missing:
            return False

        # Strict mode: reject any key not in the allowed set
        if exact:
            injected = state_keys - required
            if injected:
                return False

        return True

    suffix = " (strict/exact mode)" if exact else ""
    msg = (
        f"State is missing required keys: {sorted(required)}{suffix}."
        if not exact
        else (
            f"State schema violation: only {sorted(required)} are allowed. "
            f"Unexpected keys will be rejected."
        )
    )

    return Invariant(
        fn,
        name=f"require_keys{'_exact' if exact else ''}",
        mode=mode,
        error_message=msg,
    )


def require_schema(model: Type, mode: str = "block") -> Invariant:
    """Validates state against a Pydantic BaseModel schema.

    Checks two things:
    1. All fields declared in the model are present in state.
    2. All field values match the declared types.

    Requires ``pydantic`` to be installed. If not installed, raises
    ``ImportError`` with an actionable message.

    Args:
        model:  A Pydantic ``BaseModel`` subclass defining the schema.
        mode:   'block' (raises) or 'warn' (logs only).

    Example::

        from pydantic import BaseModel
        from stateguard.stdlib.rules import require_schema

        class OrderState(BaseModel):
            order_id: str
            amount: float
            status: str

        registry.add(require_schema(OrderState, mode="block"))
    """
    try:
        from pydantic import BaseModel, ValidationError  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "require_schema() requires pydantic. "
            "Install it with: pip install pydantic"
        ) from exc

    from pydantic import ValidationError

    model_name = getattr(model, "__name__", str(model))

    def fn(state: Any) -> bool:
        state_dict = state.to_dict() if hasattr(state, "to_dict") else dict(state)
        try:
            model(**state_dict)
            return True
        except (ValidationError, TypeError):
            return False

    return Invariant(
        fn,
        name=f"require_schema_{model_name}",
        mode=mode,
        error_message=(
            f"State failed schema validation against '{model_name}'. "
            f"Check field names and types."
        ),
    )


def require_enum(key: str, allowed_values: List[Any], mode: str = "block") -> Invariant:
    """Ensures a field's value is exactly one of the allowed values."""

    def fn(state: Any) -> bool:
        val = state.get(key)
        return val in allowed_values

    return Invariant(
        fn,
        name=f"require_enum_{key}",
        mode=mode,
        error_message=f"Field '{key}' must be one of {allowed_values}.",
    )


def valid_transition(key: str, allowed_graph: Dict[Any, List[Any]], mode: str = "block") -> Invariant:
    """Ensures state machine transitions follow the allowed graph.
    
    The previous state is compared against the new state. 
    (Requires state tracking, so it checks `_snapshot_stack` if using GuardedState).
    """

    def fn(state: Any) -> bool:
        new_val = state.get(key)
        
        # If it's a GuardedState, we can look at the snapshot to find the old value
        if hasattr(state, "_snapshot_stack") and state._snapshot_stack:
            old_state = state._snapshot_stack[-1]
            old_val = old_state.get(key)
        else:
            # If no history, assume transition is valid (initial state)
            return True

        if old_val == new_val:
            return True # No transition happened

        allowed_next_states = allowed_graph.get(old_val, [])
        return new_val in allowed_next_states

    return Invariant(
        fn,
        name=f"valid_transition_{key}",
        mode=mode,
        error_message=f"Invalid transition for field '{key}'.",
    )


def require_monotonic(key: str, mode: str = "block") -> Invariant:
    """Ensures a numerical field or counter never decreases."""

    def fn(state: Any) -> bool:
        new_val = state.get(key)
        if new_val is None:
            return False

        if hasattr(state, "_snapshot_stack") and state._snapshot_stack:
            old_state = state._snapshot_stack[-1]
            old_val = old_state.get(key)
            if old_val is not None and new_val < old_val:
                return False
        return True

    return Invariant(
        fn,
        name=f"require_monotonic_{key}",
        mode=mode,
        error_message=f"Field '{key}' must be monotonically increasing.",
    )


def max_steps_breaker(key: str = "step_count", max_steps: int = 10, mode: str = "block") -> Invariant:
    """Fails if the step counter exceeds max_steps, preventing infinite loops."""

    def fn(state: Any) -> bool:
        count = state.get(key, 0)
        if not isinstance(count, int):
            return False
        return count <= max_steps

    return Invariant(
        fn,
        name=f"max_steps_breaker_{key}",
        mode=mode,
        error_message=f"Step count exceeded max limit of {max_steps}.",
    )


def require_not_empty(key: str, mode: str = "block") -> Invariant:
    """Ensures a string or list field is present and not empty."""

    def fn(state: Any) -> bool:
        val = state.get(key)
        if val is None:
            return False
        if hasattr(val, "__len__") and len(val) == 0:
            return False
        return True

    return Invariant(
        fn,
        name=f"require_not_empty_{key}",
        mode=mode,
        error_message=f"Field '{key}' must not be empty.",
    )

