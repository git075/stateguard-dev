"""Tests for the StateGuard async SQLite AuditStore."""

import asyncio
import pytest
from stateguard.store import AuditStore, EventType, AuditEvent

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_audit.db")

@pytest.mark.asyncio
async def test_audit_store_lifecycle(db_path):
    store = AuditStore(db_path)
    await store.start()
    
    # Starting twice should be a no-op
    await store.start()
    
    assert store._started
    
    await store.stop()
    # Stopping twice should be a no-op
    await store.stop()
    
    assert not store._started


@pytest.mark.asyncio
async def test_audit_store_context_manager(db_path):
    async with AuditStore(db_path) as store:
        assert store._started
    assert not store._started


@pytest.mark.asyncio
async def test_audit_store_write_and_read(db_path):
    async with AuditStore(db_path) as store:
        await store.log_saga_start("saga-1")
        await store.log_commit("saga-1", "step_one", {"a": 1}, {"a": 2})
        await store.log_saga_end("saga-1")
        
        # Allow writer loop to flush
        await asyncio.sleep(0.1)
        
        # Read back
        events = store.read_saga("saga-1")
        assert len(events) == 3
        
        assert events[0].event_type == EventType.SAGA_START
        assert events[0].saga_id == "saga-1"
        
        assert events[1].event_type == EventType.COMMIT
        assert events[1].step_name == "step_one"
        assert events[1].state_before == {"a": 1}
        assert events[1].state_after == {"a": 2}
        
        assert events[2].event_type == EventType.SAGA_END

        # Test list_sagas
        sagas = store.list_sagas()
        assert sagas == ["saga-1"]

        # Test read_last
        last_events = store.read_last(2)
        assert len(last_events) == 2
        assert last_events[0].event_type == EventType.COMMIT
        assert last_events[1].event_type == EventType.SAGA_END

        # Test read_last with filter
        filtered_events = store.read_last(10, event_type=EventType.SAGA_START.value)
        assert len(filtered_events) == 1
        assert filtered_events[0].event_type == EventType.SAGA_START


@pytest.mark.asyncio
async def test_audit_store_export_json(db_path):
    async with AuditStore(db_path) as store:
        await store.log_warn("saga-2", "my_rule", {"balance": -10}, "Too low")
        await asyncio.sleep(0.1)
        
        json_str = store.export_json("saga-2")
        assert "my_rule" in json_str
        assert "Too low" in json_str
        assert "INVARIANT_WARN" in json_str or "invariant_warn" in json_str


@pytest.mark.asyncio
async def test_log_methods_raise_if_not_started(db_path):
    store = AuditStore(db_path)
    with pytest.raises(RuntimeError, match="AuditStore not started"):
        await store.log_saga_start("saga-3")
