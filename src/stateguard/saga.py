"""Saga Transaction Coordinator module for StateGuard.

Provides saga transaction coordination, LIFO compensation stacks, and automatic
rollback execution upon unhandled exceptions or invariant violations.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import functools
import logging
import inspect
from dataclasses import dataclass, field
from stateguard.proxy import GuardedState
from stateguard.invariants import InvariantRegistry, InvariantViolation, guard

logger = logging.getLogger("stateguard.saga")


@dataclass
class CompensatingAction:
    """Represents a registered compensating action in a Saga stack."""

    fn: Callable[..., Any]
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def execute(self) -> Any:
        """Execute the compensating action synchronously."""
        import asyncio
        logger.info(f"[SAGA UNDO] Executing compensation: {self.description or self.fn.__name__}")
        res = self.fn(*self.args, **self.kwargs)
        if asyncio.iscoroutine(res):
            logger.warning(
                f"[SAGA ASYNC WARNING] Compensation '{self.description or self.fn.__name__}' "
                "returned a coroutine but was called from synchronous rollback(). "
                "Use async with Saga and arollback() to properly await async compensations."
            )
        return res

    async def aexecute(self) -> Any:
        """Execute the compensating action asynchronously."""
        import asyncio
        logger.info(f"[SAGA UNDO] Async executing compensation: {self.description or self.fn.__name__}")
        res = self.fn(*self.args, **self.kwargs)
        if asyncio.iscoroutine(res):
            res = await res
        return res


class SagaCoordinator:
    """Manages LIFO compensation stack and state snapshots for a Saga transaction."""

    def __init__(
        self,
        state: Optional[GuardedState] = None,
        registry: Optional[InvariantRegistry] = None,
        saga_id: Optional[str] = None,
    ) -> None:
        self.saga_id = saga_id or "saga_default"
        self.state = state if isinstance(state, GuardedState) else (GuardedState(state) if state is not None else None)
        self.registry = registry or guard
        self._compensation_stack: List[CompensatingAction] = []
        self._execution_history: List[str] = []
        self._is_completed: bool = False
        self._is_rolled_back: bool = False

    def add_compensation(
        self,
        fn: Callable[..., Any],
        *args: Any,
        description: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Push a compensating action onto the LIFO stack.

        Args:
            fn: The undo function to call on rollback.
            *args: Positional arguments for the undo function.
            description: Optional human-readable description.
            **kwargs: Keyword arguments for the undo function.
        """
        action_desc = description or fn.__name__
        action = CompensatingAction(fn=fn, args=args, kwargs=kwargs, description=action_desc)
        self._compensation_stack.append(action)
        logger.debug(f"[SAGA STACK] Registered compensation '{action_desc}' (Stack depth: {len(self._compensation_stack)})")

    def record_step(self, step_name: str) -> None:
        """Record the execution of a saga step."""
        self._execution_history.append(step_name)

    def commit(self) -> None:
        """Commit the saga transaction.

        If a GuardedState is associated, commits its state transaction.
        Clears the compensation stack.
        """
        if self.state and self.state.is_in_transaction:
            self.state.commit()
        self._compensation_stack.clear()
        self._is_completed = True
        logger.info(f"[SAGA COMMIT] Saga '{self.saga_id}' committed successfully.")

    def rollback(self, reason: Optional[str] = None) -> List[Any]:
        """Roll back the saga transaction.

        1. Restores GuardedState baseline snapshot if active.
        2. Executes compensating actions in reverse order (LIFO).

        Returns:
            List of results returned by compensating functions.
        """
        if self._is_rolled_back:
            logger.warning(f"[SAGA ROLLBACK] Saga '{self.saga_id}' was already rolled back.")
            return []

        logger.error(f"[SAGA ROLLBACK] Rolling back saga '{self.saga_id}'. Reason: {reason or 'Not specified'}")

        # Restore state proxy snapshot first if active
        if self.state and self.state.is_in_transaction:
            try:
                self.state.rollback()
                logger.info(f"[SAGA ROLLBACK] GuardedState proxy restored to baseline.")
            except Exception as e:
                logger.error(f"[SAGA ROLLBACK] Error restoring GuardedState: {e}")

        compensation_results = []
        while self._compensation_stack:
            action = self._compensation_stack.pop()
            try:
                res = action.execute()
                compensation_results.append(res)
            except Exception as e:
                logger.critical(
                    f"[SAGA COMPENSATE ERROR] Compensation '{action.description}' failed: {e}",
                    exc_info=True,
                )

        self._is_rolled_back = True
        return compensation_results

    async def arollback(self, reason: Optional[str] = None) -> List[Any]:
        """Roll back the saga and async execute all compensations in LIFO order."""
        if self._is_completed or self._is_rolled_back:
            return []

        logger.error(
            f"[SAGA ROLLBACK] Rolling back saga '{self.saga_id}'. "
            f"Reason: {reason or 'Not specified'}"
        )

        try:
            self.state.rollback()
        except RuntimeError as e:
            logger.warning(f"Could not rollback state: {e}")

        compensation_results = []
        while self._compensation_stack:
            action = self._compensation_stack.pop()
            try:
                res = await action.aexecute()
                compensation_results.append(res)
            except Exception as e:
                logger.critical(
                    f"[SAGA COMPENSATE ERROR] Async compensation '{action.description}' failed: {e}",
                    exc_info=True,
                )

        self._is_rolled_back = True
        return compensation_results

    @property
    def stack_depth(self) -> int:
        """Return the number of compensating actions in the LIFO stack."""
        return len(self._compensation_stack)

    @property
    def is_active(self) -> bool:
        """Return True if transaction is neither completed nor rolled back."""
        return not self._is_completed and not self._is_rolled_back


# Thread-local / Context management for active saga
_active_saga_context: Optional[SagaCoordinator] = None


def get_active_saga() -> Optional[SagaCoordinator]:
    """Get the currently active SagaCoordinator context."""
    return _active_saga_context


def retry_on_violation(max_retries: int = 3) -> Callable[..., Any]:
    """Decorator to retry a function if it raises an InvariantViolation.
    
    This allows LLMs to catch their mistakes and try again before failing.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        import asyncio
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_err = None
                for attempt in range(max_retries):
                    try:
                        return await func(*args, **kwargs)
                    except InvariantViolation as e:
                        logger.warning(f"[SAGA RETRY] Attempt {attempt + 1}/{max_retries} failed: {e.message}")
                        last_err = e
                raise last_err or RuntimeError("Max retries exceeded")
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_err = None
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except InvariantViolation as e:
                        logger.warning(f"[SAGA RETRY] Attempt {attempt + 1}/{max_retries} failed: {e.message}")
                        last_err = e
                raise last_err or RuntimeError("Max retries exceeded")
            return sync_wrapper
    return decorator


class Saga:
    """Factory and context manager for creating and running Saga transactions."""

    def __init__(
        self,
        state: Optional[Union[GuardedState, Dict[str, Any]]] = None,
        registry: Optional[InvariantRegistry] = None,
        saga_id: Optional[str] = None,
        mode: str = "block",
    ) -> None:
        self.guarded_state = state if isinstance(state, GuardedState) else GuardedState(state or {})
        self.registry = registry if registry is not None else guard
        self.saga_id = saga_id or "saga"
        self.mode = mode
        self.coordinator = SagaCoordinator(state=self.guarded_state, registry=self.registry, saga_id=self.saga_id)

    def __enter__(self) -> SagaCoordinator:
        global _active_saga_context
        self._previous_context = _active_saga_context
        _active_saga_context = self.coordinator
        self.guarded_state.begin()
        return self.coordinator

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        global _active_saga_context
        try:
            if not self.coordinator.is_active:
                return False
                
            if exc_type is not None:
                self.coordinator.rollback(reason=str(exc_val))
                return False
            else:
                try:
                    self.registry.check(self.guarded_state, mode_override=self.mode)
                    self.coordinator.commit()
                except InvariantViolation as e:
                    self.coordinator.rollback(reason=f"Invariant Violation: {e.message}")
                    raise e
        finally:
            _active_saga_context = self._previous_context
        return False

    async def __aenter__(self) -> SagaCoordinator:
        global _active_saga_context
        self._previous_context = _active_saga_context
        _active_saga_context = self.coordinator
        self.guarded_state.begin()
        return self.coordinator

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        global _active_saga_context
        try:
            if not self.coordinator.is_active:
                return False
                
            if exc_type is not None:
                await self.coordinator.arollback(reason=str(exc_val))
                return False
            else:
                try:
                    self.registry.check(self.guarded_state, mode_override=self.mode)
                    self.coordinator.commit()
                except InvariantViolation as e:
                    await self.coordinator.arollback(reason=f"Invariant Violation: {e.message}")
                    raise e
        finally:
            _active_saga_context = self._previous_context
        return False

    def step(
        self,
        compensate: Optional[Callable[..., Any]] = None,
        name: Optional[str] = None,
    ) -> Callable:
        """Decorator for saga steps.

        Usage:
            @saga_instance.step(compensate=undo_func)
            def do_step(state):
                ...
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            step_name = name or fn.__name__

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                coord = get_active_saga() or self.coordinator
                coord.record_step(step_name)

                try:
                    result = fn(*args, **kwargs)

                    if compensate is not None:
                        sig = inspect.signature(compensate)
                        comp_args = ()
                        comp_kwargs = {}

                        if len(sig.parameters) > 0 and args:
                            comp_args = args
                            
                        # If the compensation function accepts a 'result' kwarg, pass it!
                        if "result" in sig.parameters:
                            comp_kwargs["result"] = result

                        coord.add_compensation(
                            compensate,
                            *comp_args,
                            description=f"Undo {step_name}",
                            **comp_kwargs,
                        )

                    return result
                except Exception as e:
                    coord.rollback(reason=f"Error in step '{step_name}': {str(e)}")
                    raise

            return wrapper

        return decorator


def saga_step(compensate: Optional[Callable[..., Any]] = None, name: Optional[str] = None) -> Callable:
    """Global step decorator that binds to currently active saga context."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        step_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            coord = get_active_saga()
            if coord is not None:
                coord.record_step(step_name)

            try:
                result = fn(*args, **kwargs)
                if compensate is not None and coord is not None:
                    sig = inspect.signature(compensate)
                    comp_args = args if len(sig.parameters) > 0 else ()
                    comp_kwargs = {}
                    if "result" in sig.parameters:
                        comp_kwargs["result"] = result
                        
                    coord.add_compensation(compensate, *comp_args, description=f"Undo {step_name}", **comp_kwargs)
                return result
            except Exception as e:
                if coord is not None:
                    coord.rollback(reason=f"Error in step '{step_name}': {str(e)}")
                raise

        return wrapper

    return decorator
