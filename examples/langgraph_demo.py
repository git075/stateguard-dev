"""StateGuard LangGraph End-to-End Demo.

Scenario: E-Commerce Return & Refund Pipeline
─────────────────────────────────────────────
A customer returns a $100 jacket.

Step 1 — InventoryAgent   : Restock the item (+1 to inventory_stock)
Step 2 — RefundAgent      : Calculate refund amount (BUG: LLM hallucinates $150)
Step 3 — PayoutAgent      : Add refund to wallet (never reached)

Without StateGuard: Step 2 silently corrupts state. Step 3 processes a $150
                    payout on a $100 item.

With StateGuard:    Step 2 is blocked by the 'max_refund_rule' invariant.
                    Step 1 is automatically rolled back (inventory restored).
                    Audit trail shows exactly what happened and why.

Run:
    PYTHONPATH=src python3 examples/langgraph_demo.py
"""

import asyncio
import sys
import os

# ── Setup path so we can import stateguard without installing ──────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stateguard import GuardedState, InvariantRegistry, InvariantViolation, Saga
from stateguard.store import AuditStore, EventType, AuditEvent

# ── 1. Define Initial State ────────────────────────────────────────────────────

INITIAL_STATE = {
    "customer_id":        "CUST-99",
    "order_id":           "ORD-500",
    "original_item_price": 100.00,
    "inventory_stock":    10,
    "wallet_balance":     50.00,
    "refund_amount":      0.00,
    "status":             "PENDING",
}

from stateguard.stdlib.rules import require_bounds, require_enum

# ── 2. Define Business Invariants ──────────────────────────────────────────────

registry = InvariantRegistry()

@registry.invariant(name="max_refund_rule", mode="block",
                    error_message="Refund cannot exceed original item price!")
def max_refund_rule(state):
    return state.get("refund_amount", 0) <= state.get("original_item_price", 0)

# Use stdlib rules for the common invariants
registry.add(require_bounds("wallet_balance", min_val=0, mode="block"))
registry.add(require_enum("status", ["PENDING", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"], mode="block"))

# ── 3. Agent Step Functions ────────────────────────────────────────────────────

def step1_restock_inventory(state: GuardedState) -> None:
    """InventoryAgent: Restock the returned item."""
    old = state["inventory_stock"]
    state["inventory_stock"] += 1
    print(f"  [Step 1] InventoryAgent: stock {old} → {state['inventory_stock']}")


def undo_restock(state: GuardedState, snapshot: dict) -> None:
    """Compensation: Restore inventory from snapshot."""
    state["inventory_stock"] = snapshot["inventory_stock"]
    print(f"  [Undo 1] Inventory restored: stock → {state['inventory_stock']}")


def step2_calculate_refund(state: GuardedState) -> None:
    """RefundAgent: Calculate refund amount.

    BUG INJECTED: LLM hallucinates $150.00 on a $100.00 item.
    StateGuard's max_refund_rule will catch this.
    """
    state["refund_amount"] = 150.00      # ← BUG: exceeds original_item_price
    state["status"] = "PROCESSING"
    print(f"  [Step 2] RefundAgent: calculated refund = ${state['refund_amount']:.2f}  ← BUG!")


def undo_refund(state: GuardedState, snapshot: dict) -> None:
    state["refund_amount"] = snapshot["refund_amount"]
    state["status"]        = snapshot["status"]
    print(f"  [Undo 2] Refund calculation undone.")


def step3_payout(state: GuardedState) -> None:
    """PayoutAgent: Add refund to customer wallet."""
    state["wallet_balance"] += state["refund_amount"]
    state["status"] = "COMPLETED"
    print(f"  [Step 3] PayoutAgent: wallet ${state['wallet_balance']:.2f}, status=COMPLETED")


# ── 4. Run the Demo ────────────────────────────────────────────────────────────

async def run_demo() -> None:
    print()
    print("=" * 60)
    print(" StateGuard — E-Commerce Return Pipeline Demo")
    print("=" * 60)
    print()
    print(" Scenario: Customer returning a $100 jacket.")
    print(" BUG: RefundAgent hallucinates $150 refund.")
    print()

    # Set up audit store
    db_path = ".stateguard/demo_audit.db"
    store = AuditStore(db_path)
    await store.start()

    state = GuardedState(INITIAL_STATE.copy())

    print(" Running pipeline with StateGuard protection...")
    print()

    saga = Saga(state=state, registry=registry, saga_id="return-ORD-500", mode="block")

    # Capture snapshots BEFORE each step for clean compensation
    snapshot_before_step1 = state.to_dict()

    # Register steps with snapshot-based compensation
    @saga.step(compensate=lambda: undo_restock(state, snapshot_before_step1))
    def run_step1(s):
        step1_restock_inventory(s)

    snapshot_before_step2 = None   # filled after step 1 runs

    @saga.step(compensate=lambda: undo_refund(state, snapshot_before_step2))
    def run_step2(s):
        step2_calculate_refund(s)

    @saga.step()
    def run_step3(s):
        step3_payout(s)

    await store.log_saga_start("return-ORD-500")

    try:
        with saga as coord:
            # Step 1 — runs fine
            run_step1(coord.state)
            snapshot_before_step2 = coord.state.to_dict()   # snapshot after step 1 committed
            await store.log_commit(
                saga_id="return-ORD-500", step_name="restock_inventory",
                state_before={"inventory_stock": snapshot_before_step1["inventory_stock"]},
                state_after={"inventory_stock": coord.state["inventory_stock"]},
            )

            # Step 2 — mutates state, then we check invariants inline BEFORE step 3
            run_step2(coord.state)
            # ← Inline pre-commit check: catches the $150 bug NOW, before step 3 runs
            registry.check(coord.state, mode_override="block")

            # Only reached if Step 2 passed all invariants
            run_step3(coord.state)
            await store.log_commit(
                saga_id="return-ORD-500", step_name="payout",
                state_before={"wallet_balance": snapshot_before_step2["wallet_balance"]},
                state_after={"wallet_balance": coord.state["wallet_balance"]},
            )

    except InvariantViolation as e:
        await store.log_block(
            saga_id="return-ORD-500", rule_name=e.rule_name, state=e.state,
            message=e.message,
        )
        await store.log_compensation("return-ORD-500", step_name="undo_restock")
        await store.log_rollback("return-ORD-500", reason=e.message)
        await store.log_saga_end("return-ORD-500", success=False)

        print()
        print("─" * 60)
        print(" StateGuard caught the bug! ✅")
        print(f"  Rule violated : {e.rule_name}")
        print(f"  Reason        : {e.message}")
        print(f"  State restored: inventory_stock = {state['inventory_stock']} (back to 10)")
        print(f"  Refund amount : ${state['refund_amount']:.2f} (never committed)")
        print("─" * 60)
        print()
        print(" Run `stateguard log --db .stateguard/demo_audit.db` to see the audit trail.")
        print()

    except Exception as e:
        print(f"\n  Unexpected error: {e}")
    finally:
        await asyncio.sleep(0.1)   # Give writer loop time to flush
        await store.stop()

    # Print summary from audit log
    saved = store.read_last(20)
    print(f" Audit log: {len(saved)} events recorded to {db_path}")
    print()


if __name__ == "__main__":
    asyncio.run(run_demo())
