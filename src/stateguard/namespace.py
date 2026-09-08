"""Agent Namespace Isolation Module.

Provides memory isolation for multi-agent systems sharing a single GuardedState.
Each agent is given a `NamespacedState` view that restricts read/write access
to its own prefix, preventing agents from accidentally (or maliciously)
overwriting each other's data or reserved fields.
"""

from typing import Any, Iterator, List, MutableMapping, Optional
from stateguard.proxy import GuardedState


class NamespaceViolation(Exception):
    """Raised when an agent attempts to access memory outside its allowed namespace."""
    pass


class NamespacedState(MutableMapping[str, Any]):
    """A restricted view over a GuardedState for a specific agent.

    Enforces that the agent can only read/write keys that begin with its
    namespace prefix, OR keys explicitly defined as shared.

    Example:
        base_state = GuardedState({"shared_data": 1, "agent1_score": 5})
        view1 = NamespacedState(base_state, "agent1_", shared_keys=["shared_data"])

        view1["agent1_score"] = 10      # Allowed
        view1["shared_data"] = 2        # Allowed
        view1["agent2_score"] = 100     # Raises NamespaceViolation
    """

    def __init__(
        self,
        base_state: GuardedState,
        namespace_prefix: str,
        shared_keys: Optional[List[str]] = None,
        can_write_shared: bool = False,
    ) -> None:
        """Initialize a NamespacedState view.

        Args:
            base_state: The underlying GuardedState.
            namespace_prefix: String prefix that keys must start with to be accessible.
            shared_keys: List of exact key names that are globally readable.
            can_write_shared: If True, this agent can also mutate shared keys.
                              Usually False for worker agents, True for orchestrators.
        """
        if not isinstance(base_state, GuardedState):
            raise TypeError("base_state must be a GuardedState instance.")

        self._base = base_state
        self._prefix = namespace_prefix
        self._shared_keys = set(shared_keys or [])
        self._can_write_shared = can_write_shared

    def _check_read_access(self, key: str) -> None:
        if key in self._shared_keys:
            return
        if not key.startswith(self._prefix):
            raise NamespaceViolation(
                f"Agent with namespace '{self._prefix}' is not permitted to read key '{key}'."
            )

    def _check_write_access(self, key: str) -> None:
        if key in self._shared_keys:
            if not self._can_write_shared:
                raise NamespaceViolation(
                    f"Agent with namespace '{self._prefix}' is not permitted to write to shared key '{key}'."
                )
            return
        if not key.startswith(self._prefix):
            raise NamespaceViolation(
                f"Agent with namespace '{self._prefix}' is not permitted to write key '{key}'."
            )

    # ── MutableMapping implementation ──

    def __getitem__(self, key: str) -> Any:
        self._check_read_access(key)
        return self._base[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._check_write_access(key)
        self._base[key] = value

    def __delitem__(self, key: str) -> None:
        self._check_write_access(key)
        del self._base[key]

    def __iter__(self) -> Iterator[str]:
        # Only yield keys this agent is allowed to see
        for key in self._base:
            if key.startswith(self._prefix) or key in self._shared_keys:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if not (key.startswith(self._prefix) or key in self._shared_keys):
            return False
        return key in self._base

    # ── Passthrough GuardedState methods ──

    def begin(self) -> None:
        """Pass-through to underlying GuardedState."""
        self._base.begin()

    def commit(self) -> None:
        """Pass-through to underlying GuardedState."""
        self._base.commit()

    def rollback(self) -> None:
        """Pass-through to underlying GuardedState."""
        self._base.rollback()

    @property
    def is_in_transaction(self) -> bool:
        return self._base.is_in_transaction

    @property
    def transaction_depth(self) -> int:
        return self._base.transaction_depth

    def to_dict(self) -> dict:
        """Return a dict of ONLY the state visible to this namespace."""
        return {k: self._base[k] for k in self}


def create_agent_state(
    base_state: GuardedState,
    agent_id: str,
    shared_keys: Optional[List[str]] = None,
    is_orchestrator: bool = False,
) -> NamespacedState:
    """Factory for creating an isolated state view for an agent.

    Args:
        base_state: The global GuardedState.
        agent_id: The unique ID of the agent. Its namespace will be ``f"{agent_id}_"``.
        shared_keys: Keys globally readable by all agents.
        is_orchestrator: If True, the agent is allowed to mutate the shared keys.
    """
    return NamespacedState(
        base_state=base_state,
        namespace_prefix=f"{agent_id}_",
        shared_keys=shared_keys,
        can_write_shared=is_orchestrator,
    )
