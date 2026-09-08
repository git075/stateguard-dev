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
]
__version__ = "0.1.0"
