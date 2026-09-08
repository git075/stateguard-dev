"""Tests for LangGraph Checkpointer Integration."""

import pytest
import sys
from typing import Any, Dict

from stateguard.invariants import InvariantRegistry, InvariantViolation
from stateguard.stdlib.rules import require_bounds

try:
    from langgraph.checkpoint.base import (
        BaseCheckpointSaver,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
    )
    from langchain_core.runnables import RunnableConfig
    from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False


if LANGGRAPH_AVAILABLE:
    class MockMemorySaver(BaseCheckpointSaver):
        """A simple mock of LangGraph's MemorySaver for testing."""
        
        def __init__(self):
            super().__init__()
            self.storage = {}
            self.put_called = False

        def get_tuple(self, config: RunnableConfig) -> Any:
            return self.storage.get(config.get("configurable", {}).get("thread_id"))

        def list(self, config: Any, **kwargs: Any) -> Any:
            return iter(self.storage.values())

        def put(
            self,
            config: RunnableConfig,
            checkpoint: Checkpoint,
            metadata: CheckpointMetadata,
            new_versions: Dict[str, str],
        ) -> RunnableConfig:
            thread_id = config.get("configurable", {}).get("thread_id", "default")
            self.storage[thread_id] = checkpoint
            self.put_called = True
            return config

        # Mock async versions too
        async def aget_tuple(self, config: RunnableConfig) -> Any:
            return self.get_tuple(config)

        async def alist(self, config: Any, **kwargs: Any) -> Any:
            for v in self.storage.values():
                yield v

        async def aput(
            self,
            config: RunnableConfig,
            checkpoint: Checkpoint,
            metadata: CheckpointMetadata,
            new_versions: Dict[str, str],
        ) -> RunnableConfig:
            return self.put(config, checkpoint, metadata, new_versions)


@pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="langgraph not installed")
def test_checkpointer_allows_valid_state():
    registry = InvariantRegistry()
    registry.add(require_bounds("balance", min_val=0))

    base_saver = MockMemorySaver()
    safe_saver = StateGuardCheckpointer(base_saver, registry=registry)

    config = {"configurable": {"thread_id": "1"}}
    checkpoint = {"v": 1, "id": "1", "ts": "1", "channel_values": {"balance": 100.0}}
    
    # Should pass without raising
    safe_saver.put(config, checkpoint, {}, {})
    
    # Verify underlying put was called
    assert base_saver.put_called
    assert base_saver.storage["1"] == checkpoint


@pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="langgraph not installed")
def test_checkpointer_blocks_invalid_state():
    registry = InvariantRegistry()
    registry.add(require_bounds("balance", min_val=0))

    base_saver = MockMemorySaver()
    safe_saver = StateGuardCheckpointer(base_saver, registry=registry)

    config = {"configurable": {"thread_id": "1"}}
    # Create invalid state (balance < 0)
    checkpoint = {"v": 1, "id": "1", "ts": "1", "channel_values": {"balance": -50.0}}
    
    # Should raise InvariantViolation and PREVENT the underlying put
    with pytest.raises(InvariantViolation) as exc:
        safe_saver.put(config, checkpoint, {}, {})
        
    assert "balance" in str(exc.value)
    assert not base_saver.put_called
    assert "1" not in base_saver.storage


@pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="langgraph not installed")
@pytest.mark.asyncio
async def test_async_checkpointer_blocks_invalid_state():
    registry = InvariantRegistry()
    registry.add(require_bounds("balance", min_val=0))

    base_saver = MockMemorySaver()
    safe_saver = StateGuardCheckpointer(base_saver, registry=registry)

    config = {"configurable": {"thread_id": "1"}}
    checkpoint = {"v": 1, "id": "1", "ts": "1", "channel_values": {"balance": -50.0}}
    
    with pytest.raises(InvariantViolation):
        await safe_saver.aput(config, checkpoint, {}, {})
        
    assert not base_saver.put_called
