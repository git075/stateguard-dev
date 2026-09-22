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
        raises: bool = False,
    ) -> None:
        """Initialize an Invariant instance.

        Args:
            fn: Callable accepting state. By default it must return True if valid and
                False if invalid; returning None is treated as a failure (a forgotten
                ``return`` must never silently pass a safety rule).
            name: Rule identifier. Defaults to function name.
            description: Description of the rule. Defaults to function docstring.
            mode: Operating mode ('block' or 'warn').
            error_message: Custom failure message.
            raises: Assertion style. If True the rule passes when ``fn`` returns
                normally (any value except an explicit False) and fails when it raises;
                the exception text becomes the failure message.
        """
        self.raises = raises
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
            if self.raises:
                is_valid = result is not False
                msg = "Passed" if is_valid else self.error_message
            elif result is None:
                is_valid = False
                msg = (
                    f"Invariant rule '{self.name}' returned None. Return True/False, or "
                    "register it with raises=True if it signals violations by raising."
                )
            else:
                is_valid = bool(result)
                msg = "Passed" if is_valid else self.error_message
        except Exception as e:
            is_valid = False
            msg = str(e) if self.raises and str(e) else f"Exception during rule execution: {str(e)}"

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
        raises: bool = False,
    ) -> Callable:
        """Decorator to register an invariant function with the registry.

        Usage:
            @registry.invariant(name="non_negative_balance", mode="block")
            def check_balance(state):
                return state["balance"] >= 0

            @registry.invariant(name="non_negative_balance", raises=True)
            def check_balance(state):            # assertion style
                if state["balance"] < 0:
                    raise ValueError("Balance cannot go negative")
        """

        def decorator(fn: Callable[[Any], bool]) -> Callable[[Any], bool]:
            inv = Invariant(
                fn=fn,
                name=name,
                description=description,
                mode=mode,
                error_message=error_message,
                raises=raises,
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
        raises: bool = False,
    ) -> Callable:
        """Alias for register()."""
        return self.register(
            name=name,
            mode=mode,
            description=description,
            error_message=error_message,
            raises=raises,
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
