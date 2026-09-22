"""Saga Transaction Coordinator module for StateGuard.

Provides saga transaction coordination, LIFO compensation stacks, and automatic
rollback execution upon unhandled exceptions or invariant violations.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
import functools
import logging
import inspect
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from stateguard.proxy import GuardedState
from stateguard.invariants import InvariantRegistry, InvariantViolation, guard

logger = logging.getLogger("stateguard.saga")


@dataclass
class CompensationFailure:
    """One compensating action that raised (or could not run) during a rollback."""

    saga_id: str
    description: str
    error: BaseException


class CompensationError(Exception):
    """Raised when one or more compensating actions failed during a rollback.

    State inside the process was restored, but the real-world side effects of the
    failed actions may still be in place and need manual or queued follow-up.
    ``original`` is the exception that triggered the rollback (if any) and
    ``failures`` lists every undo that did not complete.
    """

    def __init__(
        self,
        saga_id: str,
        failures: Sequence[CompensationFailure],
        original: Optional[BaseException] = None,
    ) -> None:
        self.saga_id = saga_id
        self.failures = list(failures)
        self.original = original
        names = ", ".join(f.description for f in self.failures)
        super().__init__(
            f"Saga '{saga_id}': {len(self.failures)} compensation(s) failed and need "
            f"follow-up: {names}"
        )


def _build_compensation_call(
    compensate: Callable[..., Any],
    step_fn: Callable[..., Any],
    step_args: Tuple[Any, ...],
    step_kwargs: Dict[str, Any],
    result: Any,
) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Work out how to call ``compensate`` for a step that just succeeded.

    Declare only what your undo function needs:
      * if every parameter of the undo function (other than ``result``) has the same
        name as a parameter of the step, the values are matched BY NAME, so
        ``undo(order_id, result)`` gets the step's ``order_id`` and never its ``state``;
      * otherwise the step's positional arguments fill the undo function's parameters
        in order (legacy behaviour), and extras it cannot accept are dropped instead of
        causing a TypeError at rollback time;
      * the step's return value is passed as ``result`` if the undo function has a
        parameter with that name.
    """
    try:
        sig = inspect.signature(compensate)
    except (TypeError, ValueError):  # some builtins expose no signature
        return tuple(step_args), {}

    P = inspect.Parameter
    params = list(sig.parameters.values())
    by_name = sig.parameters
    has_var_pos = any(p.kind is P.VAR_POSITIONAL for p in params)
    has_var_kw = any(p.kind is P.VAR_KEYWORD for p in params)

    call_args: List[Any] = []
    call_kwargs: Dict[str, Any] = {}

    # ---- 1. name-based binding (safest: a value can only reach a parameter of the same name)
    step_bound: Dict[str, Any] = {}
    try:
        step_bound = dict(inspect.signature(step_fn).bind_partial(*step_args, **step_kwargs).arguments)
    except (TypeError, ValueError):
        pass

    named = [p for p in params if p.kind in (P.POSITIONAL_OR_KEYWORD, P.KEYWORD_ONLY) and p.name != "result"]
    name_based = (
        not has_var_pos
        and not any(p.kind is P.POSITIONAL_ONLY for p in params)
        and all(p.name in step_bound or p.default is not P.empty for p in named)
    )

    if name_based:
        for p in named:
            if p.name in step_bound:
                call_kwargs[p.name] = step_bound[p.name]
    # ---- 2. positional fallback
    elif has_var_pos:
        call_args = list(step_args)
    else:
        slots = [
            p for p in params
            if p.kind in (P.POSITIONAL_ONLY, P.POSITIONAL_OR_KEYWORD) and p.name != "result"
        ]
        for slot, value in zip(slots, step_args):
            if slot.kind is P.POSITIONAL_ONLY:
                call_args.append(value)
            else:
                call_kwargs[slot.name] = value  # by name, so it can never collide with `result`
        for key, value in step_kwargs.items():
            if key == "result" or key in call_kwargs:
                continue
            p = by_name.get(key)
            if (p is not None and p.kind is not P.POSITIONAL_ONLY) or (p is None and has_var_kw):
                call_kwargs[key] = value

    res_param = by_name.get("result")
    if res_param is not None and res_param.kind in (P.POSITIONAL_OR_KEYWORD, P.KEYWORD_ONLY):
        call_kwargs["result"] = result

    return tuple(call_args), call_kwargs


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
            res.close()  # never awaited, so make sure it is not left dangling
            raise RuntimeError(
                f"Compensation '{self.description or self.fn.__name__}' is async but the saga "
                "rolled back synchronously, so it was NOT executed. "
                "Use 'async with Saga(...)' so async compensations are awaited."
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
        on_compensation_failure: Optional[Callable[[CompensationFailure], None]] = None,
    ) -> None:
        self.saga_id = saga_id or "saga_default"
        self.state = state if isinstance(state, GuardedState) else (GuardedState(state) if state is not None else None)
        self.registry = registry or guard
        self._compensation_stack: List[CompensatingAction] = []
        self._execution_history: List[str] = []
        self._is_completed: bool = False
        self._is_rolled_back: bool = False
        self._on_compensation_failure = on_compensation_failure
        #: Every undo that raised during rollback. Empty when all compensations succeeded.
        self.compensation_failures: List[CompensationFailure] = []

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

    def _restore_state(self) -> None:
        if self.state is not None and self.state.is_in_transaction:
            try:
                self.state.rollback()
                logger.info("[SAGA ROLLBACK] GuardedState proxy restored to baseline.")
            except Exception as e:
                logger.error(f"[SAGA ROLLBACK] Error restoring GuardedState: {e}")

    def _record_failure(self, action: CompensatingAction, error: BaseException) -> None:
        failure = CompensationFailure(self.saga_id, action.description, error)
        self.compensation_failures.append(failure)
        logger.critical(
            f"[SAGA COMPENSATE ERROR] Compensation '{action.description}' failed: {error}",
            exc_info=error,
        )
        if self._on_compensation_failure is not None:
            try:
                self._on_compensation_failure(failure)
            except Exception:
                logger.exception("[SAGA COMPENSATE ERROR] on_compensation_failure hook raised")

    def raise_if_compensation_failed(self, original: Optional[BaseException] = None) -> None:
        """Raise CompensationError if any undo failed. ``original`` becomes its cause."""
        if not self.compensation_failures:
            return
        err = CompensationError(self.saga_id, self.compensation_failures, original)
        if original is not None:
            raise err from original
        raise err

    def rollback(self, reason: Optional[str] = None) -> List[Any]:
        """Roll back the saga transaction.

        1. Restores GuardedState baseline snapshot if active.
        2. Executes compensating actions in reverse order (LIFO). A failing undo does not
           stop the remaining ones; it is recorded in ``compensation_failures`` and
           reported through ``on_compensation_failure``.

        Returns:
            List of results returned by compensating functions.
        """
        if self._is_rolled_back:
            logger.warning(f"[SAGA ROLLBACK] Saga '{self.saga_id}' was already rolled back.")
            return []

        logger.error(f"[SAGA ROLLBACK] Rolling back saga '{self.saga_id}'. Reason: {reason or 'Not specified'}")

        # Restore state proxy snapshot first if active
        self._restore_state()

        compensation_results = []
        while self._compensation_stack:
            action = self._compensation_stack.pop()
            try:
                compensation_results.append(action.execute())
            except Exception as e:
                self._record_failure(action, e)

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

        self._restore_state()

        compensation_results = []
        while self._compensation_stack:
            action = self._compensation_stack.pop()
            try:
                compensation_results.append(await action.aexecute())
            except Exception as e:
                self._record_failure(action, e)

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


# The active saga is tracked per execution context (thread / asyncio task), NOT in a
# module-level global, so overlapping sagas can never attach an undo to each other.
_active_saga: ContextVar[Optional[SagaCoordinator]] = ContextVar("stateguard_active_saga", default=None)


def get_active_saga() -> Optional[SagaCoordinator]:
    """Get the SagaCoordinator active in the current thread / asyncio task, if any."""
    return _active_saga.get()


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
    """Factory and context manager for creating and running Saga transactions.

    Args:
        state: GuardedState (or dict) the saga protects.
        registry: InvariantRegistry to check at the end of the block. Defaults to ``guard``.
        saga_id: Identifier used in logs and errors.
        mode: 'block' or 'warn'; overrides each invariant's own mode.
        raise_on_compensation_failure: If True (default) a rollback in which any undo
            failed raises CompensationError (chained to the original error) so the
            failure cannot go unnoticed. Set False to only record it in
            ``coordinator.compensation_failures`` / the hook.
        on_compensation_failure: Called with a CompensationFailure for each failed undo
            (e.g. push it to a retry queue or the audit log).
    """

    def __init__(
        self,
        state: Optional[Union[GuardedState, Dict[str, Any]]] = None,
        registry: Optional[InvariantRegistry] = None,
        saga_id: Optional[str] = None,
        mode: str = "block",
        raise_on_compensation_failure: bool = True,
        on_compensation_failure: Optional[Callable[[CompensationFailure], None]] = None,
    ) -> None:
        self.guarded_state = state if isinstance(state, GuardedState) else GuardedState(state or {})
        self.registry = registry if registry is not None else guard
        self.saga_id = saga_id or "saga"
        self.mode = mode
        self.raise_on_compensation_failure = raise_on_compensation_failure
        self.coordinator = SagaCoordinator(
            state=self.guarded_state,
            registry=self.registry,
            saga_id=self.saga_id,
            on_compensation_failure=on_compensation_failure,
        )
        self._token: Optional[Token] = None
        self._previous: Optional[SagaCoordinator] = None

    def _activate(self) -> None:
        self._previous = _active_saga.get()
        self._token = _active_saga.set(self.coordinator)
        self.guarded_state.begin()

    def _deactivate(self) -> None:
        token, self._token = self._token, None
        if token is None:
            return
        try:
            _active_saga.reset(token)
        except ValueError:  # exited from a different context than it was entered in
            _active_saga.set(self._previous)

    def _surface_compensation_failures(self, original: Optional[BaseException]) -> None:
        if self.raise_on_compensation_failure:
            self.coordinator.raise_if_compensation_failed(original)

    def __enter__(self) -> SagaCoordinator:
        self._activate()
        return self.coordinator

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        try:
            if self.coordinator.is_active:
                if exc_type is not None:
                    self.coordinator.rollback(reason=str(exc_val))
                else:
                    try:
                        self.registry.check(self.guarded_state, mode_override=self.mode)
                    except InvariantViolation as e:
                        self.coordinator.rollback(reason=f"Invariant Violation: {e.message}")
                        self._surface_compensation_failures(e)
                        raise
                    self.coordinator.commit()
                    return False
            # Rolled back just now, or earlier by a failing step: report undo failures.
            self._surface_compensation_failures(exc_val)
        finally:
            self._deactivate()
        return False

    async def __aenter__(self) -> SagaCoordinator:
        self._activate()
        return self.coordinator

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        try:
            if self.coordinator.is_active:
                if exc_type is not None:
                    await self.coordinator.arollback(reason=str(exc_val))
                else:
                    try:
                        self.registry.check(self.guarded_state, mode_override=self.mode)
                    except InvariantViolation as e:
                        await self.coordinator.arollback(reason=f"Invariant Violation: {e.message}")
                        self._surface_compensation_failures(e)
                        raise
                    self.coordinator.commit()
                    return False
            self._surface_compensation_failures(exc_val)
        finally:
            self._deactivate()
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

        See ``_build_compensation_call`` for how ``compensate`` is called.
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
                        comp_args, comp_kwargs = _build_compensation_call(compensate, fn, args, kwargs, result)
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
    """Global step decorator that binds to the saga active in the current context.

    ``compensate`` is called on rollback with the step's arguments (as many as it
    accepts) and, if it declares a parameter named ``result``, the step's return value::

        def cancel_voucher(state, result):      # or (result), or (state), or ()
            tally.cancel(result["voucher_id"])

        @saga_step(compensate=cancel_voucher)
        def post_order(state):
            return {"voucher_id": ...}
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        step_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            coord = get_active_saga()
            if coord is not None:
                coord.record_step(step_name)
            elif compensate is not None:
                logger.warning(
                    f"[SAGA] Step '{step_name}' ran outside any active Saga in this thread/task, "
                    "so its compensation was NOT registered. Enter 'with Saga(...)' in the same "
                    "context (worker threads need contextvars.copy_context())."
                )

            try:
                result = fn(*args, **kwargs)
                if compensate is not None and coord is not None:
                    comp_args, comp_kwargs = _build_compensation_call(compensate, fn, args, kwargs, result)
                    coord.add_compensation(compensate, *comp_args, description=f"Undo {step_name}", **comp_kwargs)
                return result
            except Exception as e:
                if coord is not None:
                    coord.rollback(reason=f"Error in step '{step_name}': {str(e)}")
                raise

        return wrapper

    return decorator
