# StateGuard documentation

> Applies to `stateguard-core` 0.1.x. Every ```python ``` block on this page is executed by
> `tests/test_docs_snippets.py`, so the examples cannot drift away from the code again.
> Blocks that start with `# skip-test` are illustrations only.

## 1. What it does, and the one rule to remember

StateGuard gives an agent's state (a dictionary) database-style transactions:

1. take a snapshot of the state,
2. let your agent code change it,
3. check your business rules ("invariants"),
4. keep the change if the rules pass, otherwise restore the snapshot.

If the agent also did something in the outside world (charged a card, created a voucher),
you register a **compensating action** (an "undo") that runs when the transaction is rolled back.

**The one rule: rules are checked _after_ your code has run.** StateGuard cannot stop a function
from calling an API. It stops the resulting state from being kept, and it undoes the side effects
you registered an undo for. So keep the code that touches the outside world in as few steps as
possible, and give every one of those steps an undo.

## 2. Install

```bash
pip install stateguard-core              # core (Python 3.9+)
pip install stateguard-core langgraph    # if you use the LangGraph integration
```

## 3. Sixty-second example

```python
from stateguard import GuardedState, InvariantRegistry, InvariantViolation, Saga

rules = InvariantRegistry()

@rules.invariant(name="no_negative_balance")
def no_negative_balance(state):
    return state["balance"] >= 0

state = GuardedState({"balance": 100})
try:
    with Saga(state, registry=rules, saga_id="charge-1"):
        state["balance"] -= 500          # the agent hallucinates
except InvariantViolation as err:
    print("blocked by", err.rule_name)

assert state["balance"] == 100            # restored to the snapshot
```

What happened inside: `Saga.__enter__` took a deep copy of the state. When the `with` block ended,
the registry checked every rule. One failed, so the copy was put back and `InvariantViolation`
was raised.

## 4. Core concepts

### 4.1 `GuardedState`

A dict-like wrapper (item and attribute access) with `begin()`, `commit()` and `rollback()`.
`begin()` pushes a **deep copy** onto a stack; `rollback()` restores it; `commit()` discards it.
Transactions nest.

```python
from stateguard import GuardedState

s = GuardedState({"balance": 100, "items": []})
s.begin()
s["balance"] -= 30
s["items"].append("a")
s.rollback()                              # back to the snapshot
assert s.to_dict() == {"balance": 100, "items": []}

s.begin()
s["balance"] = 50
s.commit()
assert s.balance == 50                    # attribute access works too
```

Because snapshots are deep copies, values must be copyable, and the cost of a transaction grows
with the size of the state. Keep large blobs (documents, embeddings) outside the guarded state.

### 4.2 Invariants

An invariant is a function of the state. There are two styles; pick one per rule.

| Style | Register with | Passes when | Fails when |
|---|---|---|---|
| Boolean (default) | `@rules.invariant(name=...)` | returns `True` | returns `False`, **returns `None`**, or raises |
| Assertion | `@rules.invariant(name=..., raises=True)` | returns without raising | raises (its message becomes the failure message) or returns `False` |

Returning `None` fails on purpose in the boolean style: a forgotten `return` must never silently
pass a safety rule. The error message tells you what to fix.

```python
from stateguard import InvariantRegistry, InvariantViolation

rules = InvariantRegistry()

@rules.invariant(name="non_negative")                  # style 1: return True / False
def non_negative(state):
    return state["balance"] >= 0

@rules.invariant(name="valid_status", raises=True)     # style 2: raise to fail
def valid_status(state):
    if state["status"] not in {"idle", "processing", "done"}:
        raise ValueError(f"invalid status {state['status']!r}")

rules.check({"balance": 5, "status": "idle"})          # passes silently
try:
    rules.check({"balance": 5, "status": "weird"})
except InvariantViolation as err:
    assert "invalid status" in err.message
```

* `mode="block"` (default) raises `InvariantViolation`; `mode="warn"` only logs.
  `Saga(..., mode=...)` overrides the mode of every rule; its default `"block"` therefore
  turns `warn` rules into blocking ones. Pass `mode="warn"` to the `Saga` for a dry run.
* `stateguard.guard` is one global registry shared by the whole process. For anything with more
  than one rule set (per client, per workflow) create your own `InvariantRegistry()` and pass it
  as `registry=`.

### 4.3 Sagas and compensations

`with Saga(state, registry=rules):` is the transaction. Inside it, decorate every step that
changes the outside world with `@saga_step(compensate=undo_function)`. If the block fails (an
exception, or a rule failing at the end), StateGuard first restores the state, then runs the
undo functions of the steps that had completed, **newest first**.

```python
from stateguard import GuardedState, InvariantRegistry, InvariantViolation, Saga, saga_step

books = []                                # stand-in for an external system

def cancel_order(result):                 # `result` = what the step returned
    books.remove(result["voucher_id"])

@saga_step(compensate=cancel_order)
def post_order(state):
    books.append("V-1")
    return {"voucher_id": "V-1"}

rules = InvariantRegistry()
rules.register(name="total_matches")(lambda s: s["total"] == sum(s["lines"]))

state = GuardedState({"lines": [10, 20], "total": 30})
try:
    with Saga(state, registry=rules, saga_id="order-1") as tx:
        post_order(tx.state)              # side effect happens now
        state["total"] = 999              # a later, bad edit
except InvariantViolation:
    pass

assert books == []                        # the voucher was cancelled
assert state["total"] == 30
```

**How the undo function is called.** Declare only what it needs:

* If every parameter of the undo function (other than `result`) has the same name as a parameter
  of the step, values are matched **by name**. `undo(order_id, result)` receives the step's
  `order_id` and never its `state`.
* Otherwise the step's positional arguments fill the undo function's parameters in order.
  Arguments it cannot accept are dropped instead of raising at rollback time.
* If it has a parameter called `result`, it receives the step's return value.

**Undo functions run after the state has been restored.** Anything the step wrote into `state`
is already gone when the undo runs, so take identifiers from `result`, not from `state`:

```python
from stateguard import GuardedState, InvariantRegistry, InvariantViolation, Saga, saga_step

seen = {}

def undo(state, result):
    seen["from_state"] = state.get("voucher_id")     # already rolled back -> None
    seen["from_result"] = result                     # reliable

@saga_step(compensate=undo)
def post(state):
    state["voucher_id"] = "V-1"
    return "V-1"

always_fail = InvariantRegistry()
always_fail.register(name="always_fail")(lambda s: False)
try:
    with Saga(GuardedState({}), registry=always_fail) as tx:
        post(tx.state)
except InvariantViolation:
    pass
assert seen == {"from_state": None, "from_result": "V-1"}
```

#### When an undo itself fails

A failing undo does not stop the remaining ones. Afterwards `Saga` raises `CompensationError`
(chained to the error that caused the rollback) so a half-undone workflow can never look like a
clean rollback. `err.failures` lists what still needs manual or queued follow-up, and
`on_compensation_failure` is called once per failure (for a retry queue, an alert, the audit log).

```python
from stateguard import CompensationError, GuardedState, InvariantRegistry, Saga, saga_step

def flaky_undo(result):
    raise ConnectionError("accounting system is not reachable")

@saga_step(compensate=flaky_undo)
def post_order(state):
    return {"voucher_id": "V-9"}

failed = []
try:
    with Saga(
        GuardedState({}),
        registry=InvariantRegistry(),
        saga_id="order-9",
        on_compensation_failure=failed.append,
    ) as tx:
        post_order(tx.state)
        raise ValueError("something else went wrong")
except CompensationError as err:
    assert isinstance(err.original, ValueError)
    assert err.failures[0].description == "Undo post_order"
assert len(failed) == 1
```

Pass `raise_on_compensation_failure=False` to only record failures
(`saga.coordinator.compensation_failures`) and let the original error propagate.
The pre-built `stateguard.stdlib.compensations` undos re-raise their own errors for the same reason.
Write undo functions so they are safe to run twice (idempotent): StateGuard runs each undo once,
so retries are your queue's job.

#### Async

`async with Saga(...)` awaits async undo functions. An async undo inside a plain `with Saga(...)`
cannot be awaited, so it is reported as a failed compensation instead of being silently skipped.

```python
import asyncio
from stateguard import GuardedState, InvariantRegistry, Saga, saga_step

undone = []

async def cancel(result):
    undone.append(result)

@saga_step(compensate=cancel)
def post(state):
    return "V-1"

async def main():
    try:
        async with Saga(GuardedState({}), registry=InvariantRegistry()) as tx:
            post(tx.state)
            raise RuntimeError("boom")
    except RuntimeError:
        pass

asyncio.run(main())
assert undone == ["V-1"]
```

#### Retrying a model that made a mistake

`retry_on_violation(max_retries)` re-runs the decorated function when it raises
`InvariantViolation`. Put it around the function that contains the `with Saga(...)` block, so every
failed attempt is rolled back before the next one. It re-runs your function; it does **not**
pass the violation message to the model. If you want the model to see the error, catch
`InvariantViolation` yourself and add `err.message` to the next prompt.

```python
from stateguard import GuardedState, InvariantRegistry, Saga
from stateguard.saga import retry_on_violation

rules = InvariantRegistry()
rules.register(name="positive")(lambda s: s["amount"] > 0)
attempts = []

@retry_on_violation(max_retries=3)
def propose(state_dict):
    attempts.append(1)
    amount = -5 if len(attempts) < 3 else 5      # a model that corrects itself on try 3
    with Saga(GuardedState(state_dict), registry=rules) as tx:
        tx.state["amount"] = amount
    return tx.state.to_dict()

assert propose({"amount": 1})["amount"] == 5 and len(attempts) == 3
```

#### Concurrency

The active saga is tracked per thread and per asyncio task (a `ContextVar`), so overlapping
requests never attach an undo to each other's saga. A `@saga_step` that runs with no active saga
in its own context registers nothing and logs a warning. That happens if you hand work to a plain
thread pool; submit it as `pool.submit(contextvars.copy_context().run, fn)` instead.

## 5. LangGraph

### 5.1 `guarded_node` (recommended)

Wraps one node: snapshot, run the node, check the rules **before LangGraph sees the output**, then
commit or roll back and raise. Because the node raises, LangGraph never records its output.

```python
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from stateguard import InvariantRegistry, InvariantViolation, guarded_node

class State(TypedDict):
    balance: float

rules = InvariantRegistry()
rules.register(name="no_negative_balance")(lambda s: s.get("balance", 0) >= 0)

@guarded_node(registry=rules)
def charge(state):
    return {"balance": state["balance"] - 500}     # the model over-charges

graph = StateGraph(State)
graph.add_node("charge", charge)
graph.add_edge(START, "charge")
graph.add_edge("charge", END)
app = graph.compile(checkpointer=MemorySaver())

cfg = {"configurable": {"thread_id": "t1"}}
try:
    app.invoke({"balance": 100.0}, cfg)
except InvariantViolation as err:
    print("blocked:", err.rule_name)

assert app.get_state(cfg).values == {"balance": 100.0}    # the bad update left no trace
```

To have undo functions run when a node fails, pass a coordinator:
`guarded_node(registry=rules, saga_coordinator=coord)`, and register undos with
`coord.add_compensation(fn, *args)`. Undo failures raise `CompensationError`, as in section 4.3.

### 5.2 `StateGuardCheckpointer`

A wrapper around any LangGraph saver. Unlike a plain saver, it **is not a drop-in with no
arguments**: give it the saver to wrap and your registry.

```python
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from stateguard import InvariantRegistry, InvariantViolation, StateGuardCheckpointer

class State(TypedDict):
    balance: float

rules = InvariantRegistry()
rules.register(name="no_negative_balance")(lambda s: s.get("balance", 0) >= 0)

graph = StateGraph(State)
graph.add_node("charge", lambda s: {"balance": s["balance"] - 500})
graph.add_edge(START, "charge")
graph.add_edge("charge", END)
app = graph.compile(checkpointer=StateGuardCheckpointer(MemorySaver(), registry=rules))

try:
    app.invoke({"balance": 100.0}, {"configurable": {"thread_id": "t2"}})
except InvariantViolation as err:
    print("blocked:", err.rule_name)
```

Exactly what it guarantees, and what it does not:

* **Guaranteed:** no checkpoint that breaks your rules is written to the wrapped saver, and the
  run stops with `InvariantViolation`.
* **Not guaranteed:** LangGraph stores a node's raw output as *pending writes* before it saves the
  next checkpoint, and the wrapper does not validate those. So `app.get_state(...)` can still
  display the rejected values, and resuming the thread raises the same violation again until you
  continue from an earlier checkpoint with LangGraph's time-travel/fork features.
* **Not done:** it never runs undo functions. Side effects inside a node need `Saga` or
  `guarded_node(saga_coordinator=...)`.
* **Empty input checkpoint:** LangGraph writes an "input" checkpoint before the input is applied.
  It is skipped by default (`skip_sources=("input",)`), otherwise any rule that needs a key would
  block every run. All later checkpoints are checked, so write rules against complete state.

Use `guarded_node` when you need the stronger guarantee; use the checkpointer when you want one
central rule set without touching individual nodes.

## 6. CrewAI, AutoGen and other frameworks

There are **no dedicated CrewAI or AutoGen adapters in 0.1**. `GuardedState`, `Saga` and
`InvariantRegistry` are plain Python, so wrap any agent step yourself, as the scripts in
`examples/` do. Adapters are on the roadmap, not shipped.

## 7. Standard library

Ready-made rules in `stateguard.stdlib.rules` (each returns an `Invariant` for `registry.add(...)`):

| Rule | Ensures |
|---|---|
| `require_keys(keys, exact=False)` | the keys are present (and, with `exact`, nothing else is) |
| `require_bounds(key, min_val=None, max_val=None)` | a number stays within inclusive bounds; a missing key fails |
| `require_enum(key, allowed_values)` | the value is one of the allowed values |
| `require_regex(key, pattern)` | a string matches the pattern |
| `require_length(key, ...)` | a string or list is within length bounds |
| `require_not_empty(key)` | a string or list is present and non-empty |
| `require_monotonic(key)` | a number never decreases |
| `valid_transition(key, allowed_graph)` | state-machine moves follow the allowed graph |
| `max_steps_breaker(key="step_count", max_steps=10)` | stops runaway loops |
| `require_schema(model)` | state validates against a Pydantic model (import from `stateguard.stdlib.rules`) |

```python
from stateguard import InvariantRegistry
from stateguard.stdlib.rules import max_steps_breaker, require_bounds, require_enum, require_keys

rules = InvariantRegistry()
rules.add(require_keys(["order_id", "amount"]))
rules.add(require_bounds("amount", min_val=0, max_val=100_000))
rules.add(require_enum("status", ["draft", "approved", "posted"]))
rules.add(max_steps_breaker("step_count", max_steps=10))

rules.check({"order_id": "O1", "amount": 50, "status": "draft", "step_count": 1})
```

Pre-built undos in `stateguard.stdlib.compensations`: `delete_file(path)`, `webhook_rollback(url, payload)`,
`log_warning(message)`. The first two re-raise their own errors so a failed undo is reported.

## 8. Drift detection (standalone)

`DriftDetector` flags changes that pass every hard rule but are statistically unusual for a field
(a rolling Z-score of recent changes). It is **not** wired into `Saga` or `guarded_node`: call
`observe()` to build a baseline and `check(before, after)` yourself.

```python
from stateguard import DriftDetector, DriftViolation

detector = DriftDetector().watch("balance", z_threshold=3.5, mode="block")
for value in [100, 102, 99, 101, 100, 103, 98, 100, 101, 99, 102, 100]:
    detector.observe("balance", value)

try:
    detector.check({"balance": 100}, {"balance": 50_000})
except DriftViolation as err:
    print("drift:", err)
else:
    raise AssertionError("expected a DriftViolation")
```

## 9. Namespace isolation

Give each agent a view of the shared state that can only touch keys with its own prefix, plus
explicitly shared keys.

```python
from stateguard import GuardedState, NamespaceViolation, create_agent_state

base = GuardedState({"task": "summarise", "researcher_notes": ""})
researcher = create_agent_state(base, "researcher", shared_keys=["task"])

researcher["researcher_notes"] = "found three sources"   # own prefix: allowed
assert researcher["task"] == "summarise"                # shared key: readable

try:
    researcher["writer_draft"] = "..."                  # someone else's key
except NamespaceViolation:
    pass
else:
    raise AssertionError("expected a NamespaceViolation")
```

## 10. Audit log and CLI

`AuditStore` is an append-only SQLite log. **Nothing writes to it automatically**: call
`log_*` yourself around your sagas (for example after catching `InvariantViolation` or
`CompensationError`).

```python
import asyncio
from stateguard import AuditStore

async def main():
    async with AuditStore("audit.db") as store:
        await store.log_saga_start("order-1")
        await store.log_block("order-1", "no_negative_balance", {"balance": -5}, "blocked")
        await store.log_rollback("order-1", "invariant violated")
        await store.log_saga_end("order-1", success=False)

asyncio.run(main())
```

```bash
stateguard sagas --db audit.db          # list sagas
stateguard inspect order-1 --db audit.db
stateguard log --last 20 --db audit.db
stateguard export --saga order-1 -o order-1.json --db audit.db
```

## 11. API reference

**`GuardedState(initial_state=None)`** — `begin()`, `commit()`, `rollback()`, `is_in_transaction`,
`transaction_depth`, `to_dict()`; dict and attribute access.

**`InvariantRegistry`** — `register(...)` / `invariant(name=None, mode="block", description=None,
error_message=None, raises=False)` (decorators), `add(invariant)`, `check(state, mode_override=None)`,
`clear()`, `len()`. `Invariant(fn, name, description, mode, error_message, raises)`.
`InvariantViolation(rule_name, message, state)`. Global registry: `guard`.

**`Saga(state=None, registry=None, saga_id=None, mode="block", raise_on_compensation_failure=True,
on_compensation_failure=None)`** — context manager (sync and async); `.step(compensate=, name=)`
decorator; `.coordinator`, `.guarded_state`.

**`SagaCoordinator`** — `add_compensation(fn, *args, description=None, **kwargs)`, `commit()`,
`rollback(reason)`, `arollback(reason)`, `compensation_failures`, `raise_if_compensation_failed(original)`,
`stack_depth`, `is_active`, `state`.

**`saga_step(compensate=None, name=None)`**, **`get_active_saga()`**,
**`CompensationError(saga_id, failures, original)`**, **`CompensationFailure(saga_id, description, error)`**,
**`stateguard.saga.retry_on_violation(max_retries=3)`**.

**LangGraph:** `guarded_node(registry=None, saga_coordinator=None, mode="block", node_name=None)`;
`StateGuardCheckpointer(backend, registry=None, skip_sources=("input",))`.

**Other:** `DriftDetector` / `DriftViolation`; `NamespacedState`, `create_agent_state`,
`NamespaceViolation`; `AuditStore`, `AuditEvent`, `EventType`; `stateguard.stdlib`.

## 12. Known limits (0.1, alpha)

* Rules run after your code; undo functions are how side effects are reversed, and only for steps
  you decorated.
* Undos run once each and their failures are reported, not retried. Make them idempotent.
* `StateGuardCheckpointer` blocks bad checkpoints, not bad pending writes (section 5.2).
* Drift detection and the audit log are separate tools you call explicitly.
* Snapshots are deep copies: cost grows with state size and values must be copyable.
* No persistence for `GuardedState` itself; persistence comes from your LangGraph saver.
