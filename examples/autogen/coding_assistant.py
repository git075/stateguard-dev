"""AutoGen + StateGuard integration example showing namespace isolation."""

from stateguard import GuardedState, create_agent_state

# Shared underlying state
base_state = GuardedState({
    "shared_task": "Write a Python quicksort algorithm",
    "coder_scratchpad": "",
    "reviewer_feedback": "",
})

# Create isolated views for each agent
coder_view = create_agent_state(base_state, "coder", shared_keys=["shared_task"])
reviewer_view = create_agent_state(base_state, "reviewer", shared_keys=["shared_task", "coder_scratchpad"])

# Coder agent writes to its own scratchpad
coder_view["coder_scratchpad"] = "def quicksort(arr): return arr if len(arr) <= 1 else ..."

# Reviewer agent reads scratchpad and writes feedback
print("Reviewer inspecting code:", reviewer_view["coder_scratchpad"])
reviewer_view["reviewer_feedback"] = "LGTM! Implementation is valid."

print("\nCoder View Dict:", coder_view.to_dict())
print("Reviewer View Dict:", reviewer_view.to_dict())
print("Full Underlying State:", base_state.to_dict())
