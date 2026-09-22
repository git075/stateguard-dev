"""LangGraph Checkpointer Integration.

Provides StateGuardCheckpointer, which acts as a wrapper around any
LangGraph BaseCheckpointSaver. It enforces StateGuard invariants before
allowing state to be persisted to LangGraph's memory/database.

Only ``put()`` / ``aput()`` add behaviour (the invariant check). Every other
saver method is forwarded to the wrapped backend, because LangGraph calls
``put_writes``, ``get_next_version`` etc. during a normal run and the base class
raises NotImplementedError for them.

Note: this wrapper only *blocks a bad checkpoint from being saved*. It does not run
saga compensations. Side effects performed inside a node need ``Saga`` /
``guarded_node`` (see stateguard.integrations.langgraph) to be undone.
"""

from typing import Any, AsyncIterator, Collection, Dict, Iterator, Optional, Sequence, Tuple

try:
    from langgraph.checkpoint.base import (
        BaseCheckpointSaver,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
    )
    from langchain_core.runnables import RunnableConfig
except ImportError as exc:
    raise ImportError(
        "StateGuardCheckpointer requires langgraph to be installed. "
        "Run `pip install langgraph` to use this feature."
    ) from exc

from stateguard.invariants import guard as global_guard
from stateguard.invariants import InvariantViolation
from stateguard.proxy import GuardedState

# Saver methods forwarded untouched. Looked up on the backend at call time so the
# wrapper keeps working across LangGraph versions that add or drop optional methods.
_SYNC_FORWARDED = (
    "put_writes",
    "delete_thread",
    "delete_for_runs",
    "copy_thread",
    "prune",
    "get_next_version",
)
_ASYNC_FORWARDED = (
    "aput_writes",
    "adelete_thread",
    "adelete_for_runs",
    "acopy_thread",
    "aprune",
)


def _forward_sync(name: str):
    def method(self: "StateGuardCheckpointer", *args: Any, **kwargs: Any) -> Any:
        return getattr(self.backend, name)(*args, **kwargs)

    method.__name__ = name
    method.__qualname__ = f"StateGuardCheckpointer.{name}"
    return method


def _forward_async(name: str):
    async def method(self: "StateGuardCheckpointer", *args: Any, **kwargs: Any) -> Any:
        return await getattr(self.backend, name)(*args, **kwargs)

    method.__name__ = name
    method.__qualname__ = f"StateGuardCheckpointer.{name}"
    return method


class StateGuardCheckpointer(BaseCheckpointSaver):
    """A transactional wrapper for LangGraph checkpointers.

    Wraps an existing LangGraph Checkpointer (like MemorySaver or SqliteSaver).
    Intercepts the `put()` operation. If the new state violates any StateGuard
    invariants, it raises an `InvariantViolation` and PREVENTS the state from
    being saved to the underlying checkpointer.

    Example:
        from langgraph.checkpoint.memory import MemorySaver
        from stateguard import InvariantRegistry
        from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer

        registry = InvariantRegistry()

        @registry.invariant(name="no_negative_balance")
        def no_negative_balance(state):
            return state.get("balance", 0) >= 0

        safe_saver = StateGuardCheckpointer(MemorySaver(), registry=registry)
        app = graph.compile(checkpointer=safe_saver)
    """

    def __init__(
        self,
        backend: BaseCheckpointSaver,
        registry: Optional[Any] = None,
        skip_sources: Collection[str] = ("input",),
    ) -> None:
        """Initialize the checkpointer.

        Args:
            backend: The underlying LangGraph checkpointer (e.g., MemorySaver).
            registry: The InvariantRegistry to check against. Defaults to the global `guard`.
            skip_sources: Checkpoint ``source`` values that are saved without checking.
                LangGraph writes an "input" checkpoint *before* any input is applied to the
                state, so it is empty or partial and would make any rule that requires a key
                (``require_keys``, ``require_bounds``, ``state["x"]``) block every run.
        """
        super().__init__(serde=getattr(backend, "serde", None))
        self.backend = backend
        self.registry = registry or global_guard
        self.skip_sources = frozenset(skip_sources)

    @property
    def config_specs(self) -> list:
        return self.backend.config_specs

    def _check_invariants(self, current_checkpoint: Checkpoint) -> None:
        """Run StateGuard invariants against the channel values before saving."""
        state_data = current_checkpoint.get("channel_values", {})
        temp_state = GuardedState(state_data)
        # This will raise InvariantViolation if it fails
        self.registry.check(temp_state, mode_override="block")

    # ── Sync methods ──

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.backend.get_tuple(config)

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        return self.backend.list(config, filter=filter, before=before, limit=limit)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Dict[str, str],
    ) -> RunnableConfig:
        """Intercept the put operation to validate state before saving."""
        if (metadata or {}).get("source") not in self.skip_sources:
            self._check_invariants(checkpoint)
        return self.backend.put(config, checkpoint, metadata, new_versions)

    # ── Async methods ──

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return await self.backend.aget_tuple(config)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async for item in self.backend.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Dict[str, str],
    ) -> RunnableConfig:
        """Intercept the async put operation to validate state before saving."""
        if (metadata or {}).get("source") not in self.skip_sources:
            self._check_invariants(checkpoint)
        return await self.backend.aput(config, checkpoint, metadata, new_versions)


for _name in _SYNC_FORWARDED:
    setattr(StateGuardCheckpointer, _name, _forward_sync(_name))
for _name in _ASYNC_FORWARDED:
    setattr(StateGuardCheckpointer, _name, _forward_async(_name))
del _name
