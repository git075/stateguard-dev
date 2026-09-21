"""Minimal LangGraph + StateGuard integration example."""

from stateguard import GuardedState, Saga, guard
from stateguard.stdlib.rules import require_bounds

# Add business invariants
guard.add(require_bounds("budget", min_val=0))


def agent_node(state_dict: dict) -> dict:
    state = GuardedState(state_dict)

    with Saga(state) as tx:
        # Simulate agent execution
        state["budget"] -= 500  # Mutate state within saga

    return state.to_dict()


if __name__ == "__main__":
    initial_state = {"budget": 1000}
    result = agent_node(initial_state)
    print("Execution succeeded! Remaining budget:", result["budget"])
