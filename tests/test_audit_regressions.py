"""Regression tests for the problems found when auditing the source against the docs.

Each test is written so it also runs (and fails) on the pre-fix code, except where a
new API is required, in which case the import happens inside the test.
"""

import asyncio
import threading

import pytest

from stateguard import GuardedState, InvariantRegistry, InvariantViolation, Saga, saga_step


def _failing_registry() -> InvariantRegistry:
    reg = InvariantRegistry()
    reg.register(name="always_fail")(lambda s: False)
    return reg


# ── Invariant semantics ───────────────────────────────────────────────────────


def test_raise_style_invariant_passes_valid_state_and_reports_its_own_message():
    reg = InvariantRegistry()

    @reg.invariant(name="no_negative_balance", raises=True)
    def no_negative_balance(state):
        if state["balance"] < 0:
            raise ValueError("Balance cannot go negative")

    assert reg.check({"balance": 5})[0].is_valid
    with pytest.raises(InvariantViolation) as exc:
        reg.check({"balance": -1})
    assert "Balance cannot go negative" in exc.value.message


def test_raise_style_invariant_still_fails_on_explicit_false():
    reg = InvariantRegistry()
    reg.register(name="explicit_false", raises=True)(lambda s: False)
    with pytest.raises(InvariantViolation):
        reg.check({})


def test_bool_style_invariant_returning_none_fails_closed_with_a_helpful_message():
    reg = InvariantRegistry()

    @reg.invariant(name="forgot_return")
    def forgot_return(state):
        state["balance"] >= 0  # noqa: B018  (missing `return` on purpose)

    with pytest.raises(InvariantViolation) as exc:
        reg.check({"balance": 5})
    assert "returned None" in exc.value.message
    assert "raises=True" in exc.value.message


# ── saga_step compensation arguments ─────────────────────────────────────────

_RESULT = {"id": "V1"}


def _make_undo(shape, calls):
    if shape == "result_only":
        def undo(result):
            calls.append(result)
    elif shape == "state_result":
        def undo(state, result):
            calls.append(result)
    elif shape == "result_then_state":
        def undo(result, state):
            calls.append(result)
    elif shape == "kwonly_result":
        def undo(state, *, result):
            calls.append(result)
    elif shape == "state_only":
        def undo(state):
            calls.append("called")
    else:
        def undo():
            calls.append("called")
    return undo


@pytest.mark.parametrize(
    "shape,expected",
    [
        ("result_only", _RESULT),
        ("state_result", _RESULT),
        ("result_then_state", _RESULT),
        ("kwonly_result", _RESULT),
        ("state_only", "called"),
        ("no_args", "called"),
    ],
)
def test_saga_step_compensation_accepts_any_reasonable_signature(shape, expected):
    calls = []

    @saga_step(compensate=_make_undo(shape, calls))
    def post(state):
        return dict(_RESULT)

    with pytest.raises(InvariantViolation):
        with Saga(GuardedState({"x": 1}), registry=_failing_registry()) as tx:
            post(tx.state)

    assert calls == [expected]


def test_saga_step_passes_named_step_kwargs_to_the_compensation():
    seen = []

    def undo(order_id, result):
        seen.append((order_id, result))

    @saga_step(compensate=undo)
    def post(state, order_id=None):
        return "voucher-9"

    with pytest.raises(InvariantViolation):
        with Saga(GuardedState({}), registry=_failing_registry()) as tx:
            post(tx.state, order_id="O-7")

    assert seen == [("O-7", "voucher-9")]


def test_compensation_with_differently_named_parameter_falls_back_to_position():
    seen = []

    @saga_step(compensate=lambda s: seen.append(type(s).__name__))   # `s`, not `state`
    def post(state):
        return "voucher"

    with pytest.raises(InvariantViolation):
        with Saga(GuardedState({"x": 1}), registry=_failing_registry()) as tx:
            post(tx.state)
    assert seen == ["GuardedState"]   # the undo received the step's first positional argument


# ── Overlapping sagas must not share an active-saga slot ─────────────────────


def test_overlapping_sagas_in_threads_each_keep_their_own_undo():
    undone = []

    @saga_step(compensate=lambda state: undone.append("A"))
    def post_a(state):
        return "voucher-A"

    a_entered, b_entered = threading.Event(), threading.Event()
    a_posted, b_done = threading.Event(), threading.Event()

    def wait(event):
        assert event.wait(5), "test deadlocked"

    def request_a():
        try:
            with Saga(GuardedState({}), registry=InvariantRegistry(), saga_id="A") as tx:
                a_entered.set()
                wait(b_entered)          # B is now inside its own saga
                post_a(tx.state)         # A's side effect happens while B's saga is open
                a_posted.set()
                wait(b_done)             # B has committed
                raise RuntimeError("A fails after its side effect")
        except RuntimeError:
            pass

    def request_b():
        wait(a_entered)
        with Saga(GuardedState({}), registry=InvariantRegistry(), saga_id="B"):
            b_entered.set()
            wait(a_posted)
        b_done.set()

    threads = [threading.Thread(target=request_a), threading.Thread(target=request_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert undone == ["A"]


async def test_overlapping_async_sagas_each_keep_their_own_undo():
    undone = []

    @saga_step(compensate=lambda state: undone.append("A"))
    def post_a(state):
        return "voucher-A"

    a_entered, b_entered, a_posted, b_done = (asyncio.Event() for _ in range(4))

    async def request_a():
        try:
            async with Saga(GuardedState({}), registry=InvariantRegistry(), saga_id="A") as tx:
                a_entered.set()
                await b_entered.wait()
                post_a(tx.state)
                a_posted.set()
                await b_done.wait()
                raise RuntimeError("A fails after its side effect")
        except RuntimeError:
            pass

    async def request_b():
        await a_entered.wait()
        async with Saga(GuardedState({}), registry=InvariantRegistry(), saga_id="B"):
            b_entered.set()
            await a_posted.wait()
        b_done.set()

    await asyncio.wait_for(asyncio.gather(request_a(), request_b()), 10)
    assert undone == ["A"]


# ── Compensation failures must surface ───────────────────────────────────────


def test_failed_compensation_raises_compensation_error_and_remaining_undos_still_run():
    from stateguard import CompensationError

    ran, hook_calls = [], []

    def good_undo(state):
        ran.append("good")

    def bad_undo(state):
        raise RuntimeError("tally unreachable")

    @saga_step(compensate=good_undo)
    def step_one(state):
        pass

    @saga_step(compensate=bad_undo)
    def step_two(state):
        pass

    with pytest.raises(CompensationError) as exc:
        with Saga(
            GuardedState({}),
            registry=InvariantRegistry(),
            saga_id="orders-1",
            on_compensation_failure=hook_calls.append,
        ) as tx:
            step_one(tx.state)
            step_two(tx.state)
            raise ValueError("boom")

    err = exc.value
    assert ran == ["good"]                       # LIFO: bad ran first, good still ran
    assert isinstance(err.original, ValueError) and err.__cause__ is err.original
    assert [f.description for f in err.failures] == ["Undo step_two"]
    assert hook_calls == err.failures


def test_compensation_failure_can_be_recorded_without_raising():
    def bad_undo(state):
        raise RuntimeError("nope")

    @saga_step(compensate=bad_undo)
    def step(state):
        pass

    saga = Saga(GuardedState({}), registry=InvariantRegistry(), raise_on_compensation_failure=False)
    with pytest.raises(ValueError):              # the ORIGINAL error propagates
        with saga as tx:
            step(tx.state)
            raise ValueError("boom")
    assert len(saga.coordinator.compensation_failures) == 1


def test_async_compensation_in_a_sync_rollback_is_reported_not_silently_dropped():
    from stateguard import CompensationError

    async def async_undo(state):
        pass

    @saga_step(compensate=async_undo)
    def step(state):
        pass

    with pytest.raises(CompensationError) as exc:
        with Saga(GuardedState({}), registry=InvariantRegistry()) as tx:
            step(tx.state)
            raise ValueError("boom")
    assert "async" in str(exc.value.failures[0].error).lower()


async def test_async_saga_awaits_async_compensations():
    undone = []

    async def async_undo(state):
        undone.append("undone")

    @saga_step(compensate=async_undo)
    def step(state):
        pass

    with pytest.raises(ValueError):
        async with Saga(GuardedState({}), registry=InvariantRegistry()) as tx:
            step(tx.state)
            raise ValueError("boom")
    assert undone == ["undone"]


def test_guarded_node_surfaces_failed_compensations():
    from stateguard import CompensationError, SagaCoordinator
    from stateguard.integrations.langgraph import guarded_node

    coord = SagaCoordinator(GuardedState({}))

    def bad_undo():
        raise RuntimeError("nope")

    coord.add_compensation(bad_undo)

    @guarded_node(registry=InvariantRegistry(), saga_coordinator=coord)
    def node(state):
        raise ValueError("node failed")

    with pytest.raises(CompensationError) as exc:
        node({"x": 1})
    assert isinstance(exc.value.original, ValueError)


# ── LangGraph integration against a REAL graph and saver ─────────────────────


def _graph(checkpointer, bad_node):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        balance: float
        note: str

    g = StateGraph(S)
    g.add_node("good", lambda s: {"balance": 100.0, "note": "ok"})
    g.add_node("bad", bad_node)
    g.add_edge(START, "good")
    g.add_edge("good", "bad")
    g.add_edge("bad", END)
    return g.compile(checkpointer=checkpointer)


def _balance_registry() -> InvariantRegistry:
    reg = InvariantRegistry()
    reg.register(name="no_negative_balance")(lambda s: s.get("balance", 0) >= 0)
    return reg


def test_checkpointer_works_on_a_clean_run_with_a_real_memory_saver():
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer

    app = _graph(
        StateGuardCheckpointer(MemorySaver(), registry=_balance_registry()),
        lambda s: {"balance": 90.0, "note": "fine"},
    )
    out = app.invoke({"balance": 0.0, "note": ""}, {"configurable": {"thread_id": "clean"}})
    assert out == {"balance": 90.0, "note": "fine"}


def test_checkpointer_never_saves_a_violating_checkpoint():
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer

    backend = MemorySaver()
    app = _graph(
        StateGuardCheckpointer(backend, registry=_balance_registry()),
        lambda s: {"balance": -50.0, "note": "hallucinated"},
    )
    cfg = {"configurable": {"thread_id": "bad"}}
    with pytest.raises(InvariantViolation):
        app.invoke({"balance": 0.0, "note": ""}, cfg)

    # Inspect the RAW checkpoints in the backend (not get_state, which also shows pending writes).
    saved = list(backend.list(cfg))
    assert saved, "expected the valid earlier checkpoints to have been saved"
    assert all(c.checkpoint["channel_values"].get("balance", 0) >= 0 for c in saved)


def test_strict_rules_do_not_block_a_clean_run_because_of_the_empty_input_checkpoint():
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer
    from stateguard.stdlib.rules import require_bounds, require_keys

    reg = InvariantRegistry()
    reg.add(require_keys(["balance"]))
    reg.add(require_bounds("balance", min_val=0))
    good = lambda s: {"balance": 90.0, "note": "fine"}   # noqa: E731

    app = _graph(StateGuardCheckpointer(MemorySaver(), registry=reg), good)
    out = app.invoke({"balance": 1.0, "note": ""}, {"configurable": {"thread_id": "strict"}})
    assert out["balance"] == 90.0

    bad_app = _graph(
        StateGuardCheckpointer(MemorySaver(), registry=reg),
        lambda s: {"balance": -1.0, "note": "bad"},
    )
    with pytest.raises(InvariantViolation):
        bad_app.invoke({"balance": 1.0, "note": ""}, {"configurable": {"thread_id": "strict2"}})


def test_guarded_node_leaves_no_bad_state_behind_in_a_real_graph():
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from stateguard.integrations.langgraph import guarded_node

    reg = _balance_registry()
    bad = guarded_node(registry=reg)(lambda s: {"balance": -50.0, "note": "hallucinated"})
    app = _graph(MemorySaver(), bad)
    cfg = {"configurable": {"thread_id": "gn"}}
    with pytest.raises(InvariantViolation):
        app.invoke({"balance": 0.0, "note": ""}, cfg)
    assert app.get_state(cfg).values == {"balance": 100.0, "note": "ok"}


def test_langgraph_names_are_importable_from_the_package_root():
    pytest.importorskip("langgraph")
    import stateguard
    from stateguard.integrations.langgraph import guarded_node
    from stateguard.integrations.langgraph_checkpointer import StateGuardCheckpointer

    assert stateguard.StateGuardCheckpointer is StateGuardCheckpointer
    assert stateguard.guarded_node is guarded_node


# ── Pre-built compensations must not hide their own failures ─────────────────


def test_webhook_rollback_raises_when_the_request_fails(monkeypatch):
    import urllib.request

    from stateguard.stdlib.compensations import webhook_rollback

    def boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(OSError):
        webhook_rollback("http://example.invalid/undo", {"order": 1})()


def test_delete_file_raises_when_the_file_cannot_be_removed(monkeypatch, tmp_path):
    import os

    from stateguard.stdlib.compensations import delete_file

    target = tmp_path / "artifact.txt"
    target.write_text("x")

    def deny(path):
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "remove", deny)
    with pytest.raises(PermissionError):
        delete_file(str(target))()
