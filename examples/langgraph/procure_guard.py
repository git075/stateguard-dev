"""Complex 3-agent procurement workflow with StateGuard Saga pattern."""

from stateguard import GuardedState, Saga, guard, saga_step
from stateguard.stdlib.rules import require_bounds, require_keys


def cancel_vendor_hold(state: dict):
    print(" [COMPENSATION] Cancelling vendor hold for order:", state.get("order_id"))


def refund_payment(state: dict):
    print(" [COMPENSATION] Refunding payment for order:", state.get("order_id"))


# Setup rules
guard.add(require_bounds("remaining_budget", min_val=0))
guard.add(require_keys(["order_id", "item", "cost", "remaining_budget"]))


@saga_step(compensate=cancel_vendor_hold)
def step1_reserve_vendor(state: GuardedState):
    print("1. Reserving vendor item...")
    state["order_id"] = "ORD-9876"
    state["vendor_reserved"] = True


@saga_step(compensate=refund_payment)
def step2_process_payment(state: GuardedState):
    print("2. Processing payment...")
    cost = state["cost"]
    state["remaining_budget"] -= cost
    state["payment_complete"] = True


@saga_step()
def step3_finalize_order(state: GuardedState):
    print("3. Finalizing order...")
    # Simulate bad agent decision or hallucination
    if state["remaining_budget"] < 0:
        raise ValueError("Budget depleted!")
    state["order_status"] = "COMPLETED"


def run_procurement():
    state_dict = {
        "item": "Enterprise AI License",
        "cost": 60000,
        "remaining_budget": 50000,  # Insufficient budget!
    }
    state = GuardedState(state_dict)

    print("Starting procurement saga...")
    try:
        with Saga(state, saga_id="procure-tx-1"):
            step1_reserve_vendor(state)
            step2_process_payment(state)
            step3_finalize_order(state)
    except Exception as e:
        print(f"\n🚨 Saga Failed: {e}")
        print("State safely restored to:", state.to_dict())


if __name__ == "__main__":
    run_procurement()
