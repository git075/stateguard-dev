"""Unit tests for Invariant Registry & Execution Engine module."""

import unittest
import logging
from stateguard.proxy import GuardedState
from stateguard.invariants import (
    InvariantRegistry,
    InvariantViolation,
    InvariantResult,
)


class TestInvariantEngine(unittest.TestCase):
    """Test suite for InvariantRegistry and rule evaluation engine."""

    def setUp(self):
        """Create a fresh registry for each test."""
        self.registry = InvariantRegistry()

    def test_register_and_evaluate_passing_invariant(self):
        """Test registering a rule and evaluating it against a valid state."""

        @self.registry.invariant(name="positive_balance", mode="block")
        def check_balance(state):
            return state.get("balance", 0) >= 0

        state = {"balance": 150}
        results = self.registry.check(state)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].is_valid)
        self.assertEqual(results[0].rule_name, "positive_balance")
        self.assertEqual(results[0].message, "Passed")

    def test_blocking_mode_raises_violation(self):
        """Test that a failing rule in 'block' mode raises InvariantViolation."""

        @self.registry.invariant(
            name="valid_role",
            mode="block",
            error_message="User role must be 'admin' or 'member'.",
        )
        def check_role(state):
            return state.get("role") in ("admin", "member")

        invalid_state = {"role": "hacker"}

        with self.assertRaises(InvariantViolation) as ctx:
            self.registry.check(invalid_state)

        exception = ctx.exception
        self.assertEqual(exception.rule_name, "valid_role")
        self.assertIn("User role must be 'admin' or 'member'.", exception.message)
        self.assertEqual(exception.state, invalid_state)

    def test_warning_mode_logs_and_returns(self):
        """Test that a failing rule in 'warn' mode does NOT raise exception."""

        @self.registry.invariant(
            name="budget_soft_cap",
            mode="warn",
            error_message="Budget exceeded recommended limit.",
        )
        def check_budget(state):
            return state.get("spent", 0) <= 100

        over_budget_state = {"spent": 250}

        # Catch log output
        with self.assertLogs("stateguard.invariants", level="WARNING") as cm:
            results = self.registry.check(over_budget_state)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_valid)
        self.assertEqual(results[0].mode, "warn")
        self.assertIn("[STATEGUARD WARN]", cm.output[0])
        self.assertIn("budget_soft_cap", cm.output[0])

    def test_mode_override(self):
        """Test that check(state, mode_override=...) overrides the rule's native mode."""

        @self.registry.invariant(name="strict_rule", mode="block")
        def check_x(state):
            return state.get("x") == 10

        invalid_state = {"x": 999}

        # Native mode is block (raises exception)
        with self.assertRaises(InvariantViolation):
            self.registry.check(invalid_state)

        # Override with 'warn': returns results without throwing
        results = self.registry.check(invalid_state, mode_override="warn")
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_valid)
        self.assertEqual(results[0].mode, "warn")

    def test_invariant_rule_exception_handling(self):
        """Test that exceptions raised inside invariant functions are handled gracefully."""

        @self.registry.invariant(name="faulty_rule", mode="warn")
        def faulty_rule(state):
            return state["non_existent_key"] > 0  # Raises KeyError

        results = self.registry.check({"other_key": 1})
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_valid)
        self.assertIn("Exception during rule execution", results[0].message)

    def test_guarded_state_integration(self):
        """Test checking invariants against a GuardedState proxy instance."""

        @self.registry.invariant(name="non_empty_messages", mode="block")
        def check_messages(state):
            return len(state.get("messages", [])) > 0

        state = GuardedState({"messages": ["Hello"]})
        results = self.registry.check(state)
        self.assertTrue(results[0].is_valid)

        # Mutate GuardedState and check again
        state["messages"].clear()
        with self.assertRaises(InvariantViolation):
            self.registry.check(state)


if __name__ == "__main__":
    unittest.main()
