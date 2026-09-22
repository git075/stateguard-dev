"""Tests for the StateGuard LangGraph integration."""

import pytest
from stateguard.proxy import GuardedState
from stateguard.invariants import InvariantRegistry, InvariantViolation
from stateguard.integrations.langgraph import guarded_node


def test_guarded_node_success():
    registry = InvariantRegistry()

    @registry.invariant(name="positive")
    def check_positive(state):
        return state.get("count", 0) >= 0

    # Mock LangGraph node
    @guarded_node(registry=registry)
    def my_node(state):
        state["count"] += 1
        return {"count": state["count"]}

    initial_state = {"id": "123", "count": 0}
    
    # Run the wrapped node
    result = my_node(initial_state)
    
    # State should be updated correctly
    assert result == {"id": "123", "count": 1}


def test_guarded_node_failure_blocks_and_rolls_back():
    registry = InvariantRegistry()

    @registry.invariant(name="positive", mode="block")
    def check_positive(state):
        return state.get("count", 0) >= 0

    @guarded_node(registry=registry)
    def my_node(state):
        # Bug: agent goes negative
        state["count"] -= 10
        return {"count": state["count"]}

    initial_state = {"id": "123", "count": 5}
    
    with pytest.raises(InvariantViolation):
        my_node(initial_state)
    
    # The original state should be untouched
    assert initial_state["count"] == 5


@pytest.mark.asyncio
async def test_guarded_node_with_custom_audit_store(tmp_path):
    registry = InvariantRegistry()
    db_path = str(tmp_path / "audit.db")

    from stateguard.store import AuditStore
    async with AuditStore(db_path) as store:
        assert str(store._path) == db_path
