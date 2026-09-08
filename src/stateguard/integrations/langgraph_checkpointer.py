"""LangGraph Checkpointer Integration.

Provides StateGuardCheckpointer, which acts as a wrapper around any
LangGraph BaseCheckpointSaver. It enforces StateGuard invariants before
allowing state to be persisted to LangGraph's memory/database.
"""

from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Tuple
import copy

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


class StateGuardCheckpointer(BaseCheckpointSaver):
    """A transactional wrapper for LangGraph checkpointers.

    Wraps an existing LangGraph Checkpointer (like MemorySaver or SqliteSaver).
    Intercepts the `put()` operation. If the new state violates any StateGuard
    invariants, it raises an `InvariantViolation` and PREVENTS the state from
    being saved to the underlying checkpointer.

    This turns LangGraph's passive replay system into an active, automatic
    rollback mechanism.

    Example:
        from langgraph.checkpoint.memory import MemorySaver
        from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer

        base_saver = MemorySaver()
        safe_saver = StateGuardCheckpointer(base_saver)

        # Use safe_saver in your LangGraph compiled graph:
        # graph.compile(checkpointer=safe_saver)
    """

    def __init__(self, backend: BaseCheckpointSaver, registry: Optional[Any] = None) -> None:
        """Initialize the checkpointer.

        Args:
            backend: The underlying LangGraph checkpointer (e.g., MemorySaver).
            registry: The InvariantRegistry to check against. Defaults to the global `guard`.
        """
        super().__init__()
        self.backend = backend
        self.registry = registry or global_guard

    def _check_invariants(self, current_checkpoint: Checkpoint) -> None:
        """Run StateGuard invariants against the channel values before saving."""
        # LangGraph stores the state dict inside the checkpoint's channel_values
        # (or similar structure depending on the specific LangGraph version).
        # We need to extract the raw dict, wrap it in a GuardedState temporarily,
        # and run the checks.
        
        # Typically the state we care about is the entire channel values dict.
        state_data = current_checkpoint.get("channel_values", {})
        
        # We wrap it in a temporary GuardedState to mimic transaction boundaries
        # in case any rules look at the snapshot stack, though for just checking
        # the current state, a plain dict also works for most rules.
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
        self._check_invariants(checkpoint)
        return await self.backend.aput(config, checkpoint, metadata, new_versions)
