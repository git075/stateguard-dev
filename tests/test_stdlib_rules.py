"""Tests for the StateGuard stdlib rules."""

import pytest
from stateguard.stdlib import rules
from stateguard.invariants import InvariantRegistry, InvariantViolation


@pytest.fixture
def registry():
    return InvariantRegistry()


def test_require_bounds(registry):
    rule = rules.require_bounds("balance", min_val=0, max_val=100)
    registry.add(rule)

    # Valid
    results = registry.check({"balance": 50})
    assert results[0].is_valid

    # Valid exact bounds
    assert registry.check({"balance": 0})[0].is_valid
    assert registry.check({"balance": 100})[0].is_valid

    # Invalid (Negative)
    with pytest.raises(InvariantViolation):
        registry.check({"balance": -1})

    # Invalid (Too high)
    with pytest.raises(InvariantViolation):
        registry.check({"balance": 101})

    # Invalid (Missing key)
    with pytest.raises(InvariantViolation):
        registry.check({"other": 50})


def test_require_regex(registry):
    rule = rules.require_regex("uuid", pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    registry.add(rule)

    # Valid
    assert registry.check({"uuid": "123e4567-e89b-12d3-a456-426614174000"})[0].is_valid

    # Invalid format
    with pytest.raises(InvariantViolation):
        registry.check({"uuid": "invalid-uuid"})

    # Invalid type
    with pytest.raises(InvariantViolation):
        registry.check({"uuid": 123})


def test_require_length(registry):
    rule = rules.require_length("summary", min_len=5, max_len=20)
    registry.add(rule)

    # Valid string
    assert registry.check({"summary": "1234567890"})[0].is_valid

    # Valid list
    assert registry.check({"summary": [1, 2, 3, 4, 5, 6]})[0].is_valid

    # Invalid short
    with pytest.raises(InvariantViolation):
        registry.check({"summary": "1234"})

    # Invalid long
    with pytest.raises(InvariantViolation):
        registry.check({"summary": "123456789012345678901"})

    # Invalid type (no len)
    with pytest.raises(InvariantViolation):
        registry.check({"summary": 123})


def test_require_keys(registry):
    rule = rules.require_keys(["user_id", "status"])
    registry.add(rule)

    assert registry.check({"user_id": 1, "status": "active", "extra": "data"})[0].is_valid

    with pytest.raises(InvariantViolation):
        registry.check({"user_id": 1})  # missing status


def test_require_keys_exact(registry):
    rule = rules.require_keys(["user_id", "status"], exact=True)
    registry.add(rule)

    # Exact match works
    assert registry.check({"user_id": 1, "status": "active"})[0].is_valid

    # Missing key fails
    with pytest.raises(InvariantViolation) as exc1:
        registry.check({"user_id": 1})
    assert "schema violation" in str(exc1.value)

    # Injected extra key fails (strict schema check)
    with pytest.raises(InvariantViolation) as exc2:
        registry.check({"user_id": 1, "status": "active", "free_money": True})
    assert "schema violation" in str(exc2.value)


def test_require_enum(registry):
    rule = rules.require_enum("role", ["admin", "user", "guest"])
    registry.add(rule)

    assert registry.check({"role": "user"})[0].is_valid

    with pytest.raises(InvariantViolation):
        registry.check({"role": "superadmin"})


def test_require_monotonic(registry):
    # To test monotonic, we need a state object that has history.
    # InvariantRegistry takes any object. Let's mock a GuardedState-like object.
    class MockState(dict):
        def __init__(self, data, stack):
            super().__init__(data)
            self._snapshot_stack = stack
            
    rule = rules.require_monotonic("counter")
    registry.add(rule)
    
    # Valid (no history)
    assert registry.check(MockState({"counter": 1}, []))[0].is_valid
    
    # Valid (increased)
    assert registry.check(MockState({"counter": 5}, [{"counter": 2}]))[0].is_valid
    
    # Invalid (decreased)
    with pytest.raises(InvariantViolation):
        registry.check(MockState({"counter": 1}, [{"counter": 5}]))


def test_valid_transition(registry):
    graph = {
        "PENDING": ["PROCESSING", "CANCELLED"],
        "PROCESSING": ["COMPLETED", "FAILED"],
    }
    rule = rules.valid_transition("status", graph)
    registry.add(rule)
    
    class MockState(dict):
        def __init__(self, data, stack):
            super().__init__(data)
            self._snapshot_stack = stack

    # Valid (initial state, no history)
    assert registry.check(MockState({"status": "PENDING"}, []))[0].is_valid

    # Valid (no change)
    assert registry.check(MockState({"status": "PENDING"}, [{"status": "PENDING"}]))[0].is_valid

    # Valid transition
    assert registry.check(MockState({"status": "PROCESSING"}, [{"status": "PENDING"}]))[0].is_valid

    # Invalid transition
    with pytest.raises(InvariantViolation):
        registry.check(MockState({"status": "COMPLETED"}, [{"status": "PENDING"}]))


def test_max_steps_breaker(registry):
    rule = rules.max_steps_breaker("step_count", max_steps=3)
    registry.add(rule)

    assert registry.check({"step_count": 1})[0].is_valid
    assert registry.check({"step_count": 3})[0].is_valid

    with pytest.raises(InvariantViolation):
        registry.check({"step_count": 4})


def test_require_schema(registry):
    try:
        from pydantic import BaseModel
    except ImportError:
        pytest.skip("pydantic not installed")

    class UserState(BaseModel):
        user_id: int
        username: str
        is_active: bool

    rule = rules.require_schema(UserState)
    registry.add(rule)

    # Valid schema
    assert registry.check({
        "user_id": 101,
        "username": "shankar",
        "is_active": True
    })[0].is_valid

    # Missing field
    with pytest.raises(InvariantViolation):
        registry.check({
            "user_id": 101,
            "username": "shankar"
        })

    # Type mismatch (string instead of int)
    with pytest.raises(InvariantViolation):
        registry.check({
            "user_id": "not_an_int",
            "username": "shankar",
            "is_active": True
        })

    # Extra fields (BaseModel default ignores them, but doesn't fail unless extra='forbid' is set in model config.
    # We test that the rule itself handles the BaseModel behavior correctly).
    assert registry.check({
        "user_id": 101,
        "username": "shankar",
        "is_active": True,
        "extra_field": "hello"
    })[0].is_valid


def test_require_not_empty(registry):
    rule = rules.require_not_empty("data")
    registry.add(rule)

    # Valid string and list
    assert registry.check({"data": "hello"})[0].is_valid
    assert registry.check({"data": [1, 2, 3]})[0].is_valid

    # Invalid empty string
    with pytest.raises(InvariantViolation):
        registry.check({"data": ""})

    # Invalid empty list
    with pytest.raises(InvariantViolation):
        registry.check({"data": []})

    # Invalid missing/None
    with pytest.raises(InvariantViolation):
        registry.check({"other": "val"})

