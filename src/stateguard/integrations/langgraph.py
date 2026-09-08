"""LangGraph Integration Adapter and End-to-End Demo for StateGuard.

Provides a `guarded_node` decorator that wraps any LangGraph node function with:
- GuardedState proxy (transactional snapshot + rollback)
- InvariantRegistry evaluation pre-commit
- Saga LIFO compensation on failure

Usage::

    from stateguard.integrations.langgraph import guarded_node
    from stateguard import InvariantRegistry

    registry = InvariantRegistry()

    @registry.invariant(name="balance_non_negative", mode="block")
    def check_balance(state):
        return state.get("balance", 0) >= 0

    @guarded_node(registry=registry)
    def my_node(state: dict) -> dict:
        state["balance"] -= 500
        return state
"""

import logging
import functools
from typing import Any, Callable, Dict, Optional, Union

from stateguard.proxy import GuardedState
from stateguard.invariants import InvariantRegistry, InvariantViolation, guard
from stateguard.saga import SagaCoordinator

logger = logging.getLogger("stateguard.integrations.langgraph")


def guarded_node(
    registry: Optional[InvariantRegistry] = None,
    saga_coordinator: Optional[SagaCoordinator] = None,
    mode: str = "block",
    node_name: Optional[str] = None,
) -> Callable:
    """Decorator that wraps a LangGraph node with StateGuard protection.

    When the decorated node is called:
    1. The incoming ``state`` dict is wrapped in a ``GuardedState`` proxy.
    2. ``begin()`` snapshots current state.
    3. The original node function executes and returns its output dict.
    4. ``InvariantRegistry.check()`` evaluates all rules against the proposed state.
    5. If all pass → ``commit()`` finalises and the merged output is returned.
    6. If any rule fails (block mode) → ``rollback()`` restores state and raises.

    Args:
        registry:         InvariantRegistry of rules to enforce. Defaults to global ``guard``.
        saga_coordinator: Optional SagaCoordinator for LIFO compensation tracking.
        mode:             Override evaluation mode ('block' or 'warn').
        node_name:        Human-readable name for logging. Defaults to function name.

    Example::

        @guarded_node(registry=my_registry)
        def planner_node(state: dict) -> dict:
            state["plan"] = llm.invoke(state["task"])
            return state
    """
    _registry = registry or guard

    def decorator(fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Callable:
        _name = node_name or fn.__name__

        @functools.wraps(fn)
        def wrapper(state: Union[Dict[str, Any], GuardedState]) -> Dict[str, Any]:
            # Wrap in GuardedState if not already wrapped
            if isinstance(state, GuardedState):
                guarded = state
            else:
                guarded = GuardedState(state)

            guarded.begin()
            logger.debug(f"[GUARDED NODE] '{_name}' — transaction begun.")

            try:
                # Execute the original node (LangGraph convention: returns a dict of updates)
                output = fn(guarded)

                # Apply returned dict updates onto guarded state
                if isinstance(output, dict):
                    for k, v in output.items():
                        guarded[k] = v

            except Exception as node_err:
                guarded.rollback()
                logger.error(f"[GUARDED NODE] '{_name}' raised {type(node_err).__name__}: {node_err}")
                if saga_coordinator and saga_coordinator.is_active:
                    saga_coordinator.rollback(reason=str(node_err))
                raise

            # Pre-commit invariant check
            try:
                _registry.check(guarded, mode_override=mode)
            except InvariantViolation as inv_err:
                guarded.rollback()
                logger.error(
                    f"[GUARDED NODE] '{_name}' — invariant '{inv_err.rule_name}' blocked commit."
                )
                if saga_coordinator and saga_coordinator.is_active:
                    saga_coordinator.rollback(reason=f"Invariant: {inv_err.message}")
                raise

            guarded.commit()
            logger.debug(f"[GUARDED NODE] '{_name}' — committed successfully.")

            # Return plain dict (LangGraph expects dict updates from nodes)
            return guarded.to_dict()

        return wrapper

    return decorator
