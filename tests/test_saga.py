"""Unit tests for Saga Transaction Coordinator module."""

import unittest
from stateguard.proxy import GuardedState
from stateguard.invariants import InvariantRegistry, InvariantViolation
from stateguard.saga import CompensationError, Saga


class TestSagaCoordinator(unittest.TestCase):
    """Test suite for Saga transaction management and compensation stacks."""

    def test_successful_saga_commit(self):
        """Test multi-step saga execution that completes and commits successfully."""
        compensated_steps = []

        def undo_step1(state):
            compensated_steps.append("undo_1")

        def undo_step2(state):
            compensated_steps.append("undo_2")

        saga = Saga(state={"balance": 500, "status": "init"})

        @saga.step(compensate=undo_step1)
        def step1(state):
            state["balance"] -= 100

        @saga.step(compensate=undo_step2)
        def step2(state):
            state["status"] = "completed"

        with saga as coord:
            step1(coord.state)
            step2(coord.state)

        # On successful exit, saga commits state and compensation stack is cleared
        self.assertEqual(saga.guarded_state["balance"], 400)
        self.assertEqual(saga.guarded_state["status"], "completed")
        self.assertEqual(compensated_steps, [])  # No undo functions executed
        self.assertFalse(saga.guarded_state.is_in_transaction)

    def test_step_failure_triggers_lifo_rollback(self):
        """Test that an exception during step execution runs completed steps' compensations in LIFO order."""
        undo_log = []

        def undo_charge(state):
            undo_log.append("refund_charge")

        def undo_hotel(state):
            undo_log.append("cancel_hotel")

        saga = Saga(state={"hotel_booked": False, "flight_booked": False, "charged": False})

        @saga.step(compensate=undo_charge)
        def charge_user(state):
            state["charged"] = True

        @saga.step(compensate=undo_hotel)
        def book_hotel(state):
            state["hotel_booked"] = True

        @saga.step()
        def book_flight(state):
            state["flight_booked"] = True
            raise RuntimeError("Flight booking service unavailable!")

        with self.assertRaises(RuntimeError):
            with saga as coord:
                charge_user(coord.state)
                book_hotel(coord.state)
                book_flight(coord.state)

        # LIFO order: book_hotel compensation ran first, then charge_user compensation
        self.assertEqual(undo_log, ["cancel_hotel", "refund_charge"])
        # GuardedState proxy state must be restored to baseline
        self.assertFalse(saga.guarded_state["charged"])
        self.assertFalse(saga.guarded_state["hotel_booked"])
        self.assertFalse(saga.guarded_state["flight_booked"])

    def test_invariant_violation_triggers_saga_rollback(self):
        """Test that an invariant check failure at saga end triggers LIFO rollback."""
        undo_log = []
        registry = InvariantRegistry()

        @registry.invariant(name="min_balance", mode="block")
        def check_min_balance(state):
            return state.get("balance", 0) >= 0

        def undo_withdraw(state):
            undo_log.append("redeposit")

        saga = Saga(state={"balance": 50}, registry=registry)

        @saga.step(compensate=undo_withdraw)
        def withdraw(state):
            state["balance"] -= 100  # Drops balance to -50

        with self.assertRaises(InvariantViolation):
            with saga as coord:
                withdraw(coord.state)

        # Compensation triggered because invariant check failed on context exit
        self.assertEqual(undo_log, ["redeposit"])
        # State restored to baseline (balance 50)
        self.assertEqual(saga.guarded_state["balance"], 50)

    def test_saga_state_restoration(self):
        """Verify state is completely restored to pre-saga baseline on error."""
        saga = Saga(state={"counter": 0, "logs": ["start"]})

        @saga.step()
        def modify_state(state):
            state["counter"] = 999
            state["logs"].append("corrupted")
            raise ValueError("Something broke")

        with self.assertRaises(ValueError):
            with saga as coord:
                modify_state(coord.state)

        self.assertEqual(saga.guarded_state["counter"], 0)
        self.assertEqual(saga.guarded_state["logs"], ["start"])

    def test_compensation_execution_resilience(self):
        """Verify that if one compensation fails, remaining compensations still run."""
        executed = []

        def successful_undo(state):
            executed.append("successful_executed")

        def faulty_undo(state):
            executed.append("faulty_attempted")
            raise Exception("Undo function internal failure")

        saga = Saga(state={"x": 1})

        @saga.step(compensate=successful_undo)
        def step_a(state):
            pass

        @saga.step(compensate=faulty_undo)
        def step_b(state):
            pass

        @saga.step()
        def step_c(state):
            raise RuntimeError("Step C failed")

        # A failed undo is no longer swallowed: the caller gets a CompensationError chained
        # to the error that triggered the rollback, and the remaining undos still ran.
        with self.assertRaises(CompensationError) as ctx:
            with saga as coord:
                step_a(coord.state)
                step_b(coord.state)
                step_c(coord.state)

        self.assertIsInstance(ctx.exception.original, RuntimeError)
        self.assertEqual([f.description for f in ctx.exception.failures], ["Undo step_b"])

        # Both compensations attempted in LIFO order (step_b's faulty_undo first, then step_a's successful_undo)
        self.assertEqual(executed, ["faulty_attempted", "successful_executed"])


if __name__ == "__main__":
    unittest.main()
