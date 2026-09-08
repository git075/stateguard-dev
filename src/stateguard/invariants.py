"""Invariant Registry & Execution Engine module for StateGuard.

Provides state invariant definition, registry management, and pre-commit
rule evaluation in 'warn' and 'block' modes.
"""

from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass
import logging

logger = logging.getLogger("stateguard.invariants")


class InvariantViolation(Exception):
    """Raised when a state invariant rule fails in 'block' mode."""

    def __init__(self, rule_name: str, message: str, state: Dict[str, Any]) -> None:
        self.rule_name = rule_name
        self.message = message
        self.state = state
        super().__init__(f"Invariant Violation [{rule_name}]: {message}")


@dataclass
class InvariantResult:
    """Represents the evaluation outcome of an invariant rule."""

    rule_name: str
    is_valid: bool
    mode: str
    message: str
    state_snapshot: Dict[str, Any]


class Invariant:
    """Encapsulates a single invariant rule definition."""

    def __init__(
        self,
        fn: Callable[[Any], bool],
        name: Optional[str] = None,
        description: Optional[str] = None,
        mode: str = "block",
        error_message: Optional[str] = None,
    ) -> None:
        """Initialize an Invariant instance.

        Args:
            fn: Callable accepting state and returning True if valid, False if invalid.
            name: Rule identifier. Defaults to function name.
            description: Description of the rule. Defaults to function docstring.
            mode: Operating mode ('block' or 'warn').
            error_message: Custom failure message.
        """
        self.fn = fn
        self.name = name or fn.__name__
        self.description = description or (fn.__doc__.strip() if fn.__doc__ else "")
        self.mode = mode.lower()
        self.error_message = error_message or f"Invariant rule '{self.name}' failed."

        if self.mode not in ("block", "warn"):
            raise ValueError(f"Invalid mode '{self.mode}'. Must be 'block' or 'warn'.")

    def evaluate(self, state: Any) -> InvariantResult:
        """Evaluate the invariant function against state."""
        try:
            result = self.fn(state)
            is_valid = bool(result)
            msg = "Passed" if is_valid else self.error_message
        except Exception as e:
            is_valid = False
            msg = f"Exception during rule execution: {str(e)}"

        state_dict = state.to_dict() if hasattr(state, "to_dict") else dict(state)

        return InvariantResult(
            rule_name=self.name,
            is_valid=is_valid,
            mode=self.mode,
            message=msg,
            state_snapshot=state_dict,
        )


class InvariantRegistry:
    """Registry for managing and executing invariant rules against state."""

    def __init__(self) -> None:
        self._invariants: List[Invariant] = []

    def register(
        self,
        name: Optional[str] = None,
        mode: str = "block",
        description: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Callable:
        """Decorator to register an invariant function with the registry.

        Usage:
            @registry.invariant(name="non_negative_balance", mode="block")
            def check_balance(state):
                return state["balance"] >= 0
        """

        def decorator(fn: Callable[[Any], bool]) -> Callable[[Any], bool]:
            inv = Invariant(
                fn=fn,
                name=name,
                description=description,
                mode=mode,
                error_message=error_message,
            )
            self.add(inv)
            return fn

        return decorator

    def add(self, invariant: Invariant) -> None:
        """Directly add an already constructed Invariant object. Useful for stdlib rules."""
        self._invariants.append(invariant)

    def invariant(
        self,
        name: Optional[str] = None,
        mode: str = "block",
        description: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Callable:
        """Alias for register()."""
        return self.register(
            name=name, mode=mode, description=description, error_message=error_message
        )

    def check(
        self,
        state: Any,
        mode_override: Optional[str] = None,
    ) -> List[InvariantResult]:
        """Evaluate all registered invariants against proposed state.

        Args:
            state: The state (GuardedState or dict) to evaluate.
            mode_override: If specified ('warn' or 'block'), overrides the rule's native mode.

        Returns:
            List of InvariantResult for all evaluated rules.

        Raises:
            InvariantViolation: If any evaluated rule fails in 'block' mode.
        """
        results: List[InvariantResult] = []

        for inv in self._invariants:
            effective_mode = mode_override.lower() if mode_override else inv.mode
            res = inv.evaluate(state)
            res.mode = effective_mode
            results.append(res)

            if not res.is_valid:
                if effective_mode == "warn":
                    logger.warning(
                        f"[STATEGUARD WARN] Invariant '{res.rule_name}' failed: {res.message}. State: {res.state_snapshot}"
                    )
                elif effective_mode == "block":
                    logger.error(
                        f"[STATEGUARD BLOCK] Invariant '{res.rule_name}' failed: {res.message}. State: {res.state_snapshot}"
                    )
                    raise InvariantViolation(
                        rule_name=res.rule_name,
                        message=res.message,
                        state=res.state_snapshot,
                    )

        return results

    def clear(self) -> None:
        """Clear all registered invariants."""
        self._invariants.clear()

    def __len__(self) -> int:
        return len(self._invariants)


# Global default guard instance for convenience
guard = InvariantRegistry()
