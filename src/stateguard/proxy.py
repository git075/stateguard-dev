"""GuardedState Proxy Module.

Provides a proxy wrapper around standard Python dictionaries to support state snapshotting,
rollback, commit, and mutation interception for multi-agent workflows.
"""

from typing import Any, Dict, Iterator, List, MutableMapping, Optional
import copy
from contextvars import ContextVar


class GuardedState(MutableMapping[str, Any]):
    """A transactional proxy wrapper for agent state dictionaries.

    GuardedState wraps standard Python dicts to provide transaction management:
    - `begin()`: Creates a deepcopy snapshot of the state and pushes it onto a stack.
    - `commit()`: Discards the snapshot baseline, committing changes.
    - `rollback()`: Restores the state to the last snapshot on the stack.
    """

    def __init__(self, initial_state: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the GuardedState with an optional dictionary.

        Args:
            initial_state: Initial state dict. Defaults to an empty dict if None.
        """
        if initial_state is None:
            self._data: Dict[str, Any] = {}
        elif isinstance(initial_state, dict):
            self._data = copy.deepcopy(initial_state)
        elif isinstance(initial_state, GuardedState):
            self._data = copy.deepcopy(initial_state._data)
        else:
            raise TypeError(f"Expected dict or GuardedState, got {type(initial_state).__name__}")

        self._snapshot_stack_var: ContextVar[List[Dict[str, Any]]] = ContextVar(f"snapshot_stack_{id(self)}")

    @property
    def _snapshot_stack(self) -> List[Dict[str, Any]]:
        try:
            return self._snapshot_stack_var.get()
        except LookupError:
            new_stack: List[Dict[str, Any]] = []
            self._snapshot_stack_var.set(new_stack)
            return new_stack

    def begin(self) -> None:
        """Begin a new state transaction.

        Pushes a deep copy of the current state onto the snapshot stack.
        """
        self._snapshot_stack.append(copy.deepcopy(self._data))

    def commit(self) -> None:
        """Commit the current transaction.

        Pops and discards the baseline snapshot from the top of the snapshot stack.

        Raises:
            RuntimeError: If no transaction is active (snapshot stack is empty).
        """
        if not self._snapshot_stack:
            raise RuntimeError("Cannot commit: No active transaction snapshot stack.")
        self._snapshot_stack.pop()

    def rollback(self) -> None:
        """Roll back state to the most recent transaction snapshot.

        Restores `_data` to the snapshot popped from top of snapshot stack.

        Raises:
            RuntimeError: If no transaction is active (snapshot stack is empty).
        """
        if not self._snapshot_stack:
            raise RuntimeError("Cannot rollback: No active transaction snapshot stack.")
        self._data = self._snapshot_stack.pop()

    @property
    def is_in_transaction(self) -> bool:
        """Return True if there is at least one active snapshot in the transaction stack."""
        return len(self._snapshot_stack) > 0

    @property
    def transaction_depth(self) -> int:
        """Return the current depth of nested transactions."""
        return len(self._snapshot_stack)

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the underlying state as a standard Python dictionary."""
        return copy.deepcopy(self._data)

    # Dictionary Interface Methods (MutableMapping)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __getattr__(self, name: str) -> Any:
        """Allow attribute-style access for dictionary keys (e.g., state.user_id)."""
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        """Allow attribute-style mutation for dictionary keys (e.g., state.user_id = 42)."""
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def __delattr__(self, name: str) -> None:
        """Allow attribute-style deletion for dictionary keys (e.g., del state.user_id)."""
        if name.startswith("_"):
            super().__delattr__(name)
        else:
            try:
                del self._data[name]
            except KeyError:
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __repr__(self) -> str:
        return f"GuardedState({self._data!r})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, GuardedState):
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        return False
