"""CrewAI + StateGuard integration example showing Saga pattern with task execution."""

from stateguard import GuardedState, Saga, guard
from stateguard.stdlib.rules import require_keys, require_length

# 1. Define shared state dict
raw_state = {
    "research_topic": "Agent State Management",
    "research_output": "",
    "word_count": 0,
    "sources": [],
}
state = GuardedState(raw_state)

# 2. Add business invariants
guard.add(require_keys(["research_topic", "research_output", "word_count"]))
guard.add(require_length("research_output", min_len=50))  # Must produce substantial output


def run_research_pipeline():
    with Saga(state, saga_id="research-crew-001") as tx:
        # Simulate CrewAI execution outcome
        mock_crew_output = (
            "StateGuard provides transactional state management for AI agents, "
            "enforcing invariants and supporting automated rollback on failure."
        )

        state["research_output"] = mock_crew_output
        state["word_count"] = len(mock_crew_output.split())
        state["sources"] = ["https://stateguard.dev"]

    print("Research Crew Execution Succeeded!")
    print("Final State:", state.to_dict())


if __name__ == "__main__":
    run_research_pipeline()
