"""Tests for StateGuard Agent Namespace Isolation."""

import pytest
from stateguard.proxy import GuardedState
from stateguard.namespace import NamespacedState, NamespaceViolation, create_agent_state


@pytest.fixture
def base_state():
    return GuardedState({
        "shared_data": "public",
        "agent1_score": 10,
        "agent2_score": 20,
    })


def test_agent_can_read_and_write_own_namespace(base_state):
    view = NamespacedState(base_state, "agent1_")

    # Read
    assert view["agent1_score"] == 10

    # Write
    view["agent1_score"] = 15
    assert view["agent1_score"] == 15
    assert base_state["agent1_score"] == 15

    # Delete
    del view["agent1_score"]
    assert "agent1_score" not in view
    assert "agent1_score" not in base_state


def test_agent_cannot_read_or_write_other_namespace(base_state):
    view = NamespacedState(base_state, "agent1_")

    # Read blocks
    with pytest.raises(NamespaceViolation) as exc:
        _ = view["agent2_score"]
    assert "not permitted to read" in str(exc.value)

    # Write blocks
    with pytest.raises(NamespaceViolation) as exc:
        view["agent2_score"] = 99
    assert "not permitted to write" in str(exc.value)

    # Delete blocks
    with pytest.raises(NamespaceViolation) as exc:
        del view["agent2_score"]


def test_agent_can_read_shared_keys(base_state):
    view = NamespacedState(base_state, "agent1_", shared_keys=["shared_data"])

    # Read is allowed
    assert view["shared_data"] == "public"

    # But write is blocked by default (worker agent)
    with pytest.raises(NamespaceViolation) as exc:
        view["shared_data"] = "hacked"
    assert "not permitted to write to shared key" in str(exc.value)


def test_orchestrator_can_write_shared_keys(base_state):
    view = create_agent_state(
        base_state,
        agent_id="orch",
        shared_keys=["shared_data"],
        is_orchestrator=True
    )

    view["shared_data"] = "updated_by_orch"
    assert view["shared_data"] == "updated_by_orch"
    assert base_state["shared_data"] == "updated_by_orch"


def test_iteration_and_len_only_show_visible_keys(base_state):
    view = create_agent_state(base_state, "agent1", shared_keys=["shared_data"])

    keys = list(view.keys())
    assert "agent1_score" in keys
    assert "shared_data" in keys
    assert "agent2_score" not in keys

    assert len(view) == 2
    assert "agent1_score" in view
    assert "agent2_score" not in view

    assert view.to_dict() == {
        "shared_data": "public",
        "agent1_score": 10
    }


def test_transaction_passthrough(base_state):
    view = create_agent_state(base_state, "agent1")

    assert not view.is_in_transaction
    view.begin()
    assert view.is_in_transaction
    assert view.transaction_depth == 1

    view["agent1_score"] = 999
    view.rollback()

    assert not view.is_in_transaction
    assert view["agent1_score"] == 10


def test_namespaced_state_to_dict_only_returns_visible_keys(base_state):
    view = NamespacedState(base_state, "agent1_", shared_keys=["shared_data"])
    d = view.to_dict()
    assert "agent1_score" in d
    assert "shared_data" in d
    assert "agent2_score" not in d
    assert d == {"agent1_score": 10, "shared_data": "public"}

