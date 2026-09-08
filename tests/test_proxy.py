"""Unit tests for GuardedState proxy module."""

import unittest
from stateguard.proxy import GuardedState


class TestGuardedState(unittest.TestCase):
    """Test suite for GuardedState proxy class."""

    def test_initialization_and_access(self):
        """Test standard dict initialization, item access, and attribute access."""
        raw = {"user_id": "usr_101", "balance": 500, "meta": {"role": "admin"}}
        state = GuardedState(raw)

        # Dictionary subscription access
        self.assertEqual(state["user_id"], "usr_101")
        self.assertEqual(state["balance"], 500)

        # Attribute access
        self.assertEqual(state.user_id, "usr_101")
        self.assertEqual(state.balance, 500)

        # Standard dict methods
        self.assertEqual(state.get("user_id"), "usr_101")
        self.assertEqual(state.get("non_existent", "default"), "default")
        self.assertEqual(len(state), 3)
        self.assertTrue("user_id" in state)
        self.assertEqual(list(state.keys()), ["user_id", "balance", "meta"])

        # Export to dict
        exported = state.to_dict()
        self.assertEqual(exported, raw)
        self.assertIsNot(exported, raw)

    def test_mutations(self):
        """Test dict key insertions, updates, and deletions."""
        state = GuardedState({"a": 1})

        # Set item via dict subscription
        state["b"] = 2
        self.assertEqual(state["b"], 2)

        # Set item via attribute access
        state.c = 3
        self.assertEqual(state["c"], 3)

        # Update method
        state.update({"d": 4, "e": 5})
        self.assertEqual(state["d"], 4)

        # Delete item via subscript
        del state["a"]
        self.assertNotIn("a", state)

        # Delete item via attribute
        del state.b
        self.assertNotIn("b", state)

    def test_single_transaction_commit(self):
        """Test that commit() keeps modified state and clears transaction stack."""
        state = GuardedState({"status": "pending", "retries": 0})

        self.assertFalse(state.is_in_transaction)
        state.begin()
        self.assertTrue(state.is_in_transaction)
        self.assertEqual(state.transaction_depth, 1)

        # Modify state inside transaction
        state["status"] = "processing"
        state["retries"] += 1

        # Commit transaction
        state.commit()
        self.assertFalse(state.is_in_transaction)
        self.assertEqual(state["status"], "processing")
        self.assertEqual(state["retries"], 1)

    def test_single_transaction_rollback(self):
        """Test that rollback() restores baseline state prior to begin()."""
        state = GuardedState({"status": "pending", "balance": 100})

        state.begin()
        state["status"] = "failed"
        state["balance"] = 0

        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["balance"], 0)

        # Rollback transaction
        state.rollback()
        self.assertFalse(state.is_in_transaction)
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["balance"], 100)

    def test_nested_transactions(self):
        """Test nested begin(), rollback(), and commit() calls."""
        state = GuardedState({"step": 0})

        state.begin()  # Level 1
        state["step"] = 1

        state.begin()  # Level 2
        state["step"] = 2

        self.assertEqual(state.transaction_depth, 2)
        self.assertEqual(state["step"], 2)

        # Rollback Level 2
        state.rollback()
        self.assertEqual(state.transaction_depth, 1)
        self.assertEqual(state["step"], 1)

        # Commit Level 1
        state.commit()
        self.assertEqual(state.transaction_depth, 0)
        self.assertEqual(state["step"], 1)

    def test_nested_structures_rollback(self):
        """Test deep snapshot integrity when modifying nested lists/dicts."""
        state = GuardedState({"user": {"name": "Alice", "tags": ["admin", "dev"]}})

        state.begin()
        state["user"]["name"] = "Bob"
        state["user"]["tags"].append("superuser")

        self.assertEqual(state["user"]["name"], "Bob")
        self.assertIn("superuser", state["user"]["tags"])

        state.rollback()
        self.assertEqual(state["user"]["name"], "Alice")
        self.assertEqual(state["user"]["tags"], ["admin", "dev"])

    def test_invalid_transaction_operations(self):
        """Test that committing or rolling back without active transaction raises error."""
        state = GuardedState({"x": 10})

        with self.assertRaisesRegex(RuntimeError, "Cannot commit"):
            state.commit()

        with self.assertRaisesRegex(RuntimeError, "Cannot rollback"):
            state.rollback()


if __name__ == "__main__":
    unittest.main()
