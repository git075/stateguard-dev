"""StateGuard: Debug multi-agent AI workflows by catching bad state before it spreads."""

from stateguard.proxy import GuardedState
from stateguard.invariants import (
    Invariant,
    InvariantRegistry,
    InvariantResult,
    InvariantViolation,
    guard,
)
from stateguard.saga import (
    CompensatingAction,
    CompensationError,
    CompensationFailure,
    Saga,
    SagaCoordinator,
    saga_step,
    get_active_saga,
)
from stateguard.store import (
    AuditStore,
    AuditEvent,
    EventType,
)
from stateguard.namespace import (
    NamespacedState,
    NamespaceViolation,
    create_agent_state,
)
from stateguard.drift import DriftDetector, DriftViolation
from stateguard import stdlib


def __getattr__(name: str):
    """Lazy exports that need langgraph, so `import stateguard` works without it."""
    if name == "StateGuardCheckpointer":
        from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer

        return StateGuardCheckpointer
    if name == "guarded_node":
        from stateguard.integrations.langgraph import guarded_node

        return guarded_node
    raise AttributeError(f"module 'stateguard' has no attribute {name!r}")


__all__ = [
    # Proxy
    "GuardedState",
    # Invariants
    "Invariant",
    "InvariantRegistry",
    "InvariantResult",
    "InvariantViolation",
    "guard",
    # Saga
    "CompensatingAction",
    "Saga",
    "SagaCoordinator",
    "CompensationError",
    "CompensationFailure",
    "saga_step",
    "get_active_saga",
    # Store
    "AuditStore",
    "AuditEvent",
    "EventType",
    # Namespace
    "NamespacedState",
    "NamespaceViolation",
    "create_agent_state",
    # Drift
    "DriftDetector",
    "DriftViolation",
    # Stdlib
    "stdlib",
    # LangGraph (lazy; requires `pip install langgraph`)
    "StateGuardCheckpointer",
    "guarded_node",
]
__version__ = "0.1.0"
