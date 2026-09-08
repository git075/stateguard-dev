<div align="center">
  <h1>🛡️ StateGuard</h1>
  <p><strong>The Transactional Firewall for AI Agents</strong></p>

  <p>
    <a href="https://pypi.org/project/stateguard/"><img src="https://img.shields.io/pypi/v/stateguard.svg?style=flat-square" alt="PyPI version" /></a>
    <a href="https://github.com/edgento/stateguard/actions"><img src="https://img.shields.io/github/actions/workflow/status/edgento/stateguard/tests.yml?branch=main&style=flat-square" alt="Build Status" /></a>
    <a href="https://codecov.io/gh/edgento/stateguard"><img src="https://img.shields.io/codecov/c/github/edgento/stateguard?style=flat-square" alt="Coverage" /></a>
    <a href="https://pypi.org/project/stateguard/"><img src="https://img.shields.io/pypi/pyversions/stateguard.svg?style=flat-square" alt="Python Versions" /></a>
    <a href="https://github.com/edgento/stateguard/blob/main/LICENSE"><img src="https://img.shields.io/github/license/edgento/stateguard.svg?style=flat-square" alt="License" /></a>
  </p>
  <p>
    <em>Stop LLM hallucinations from permanently corrupting your agent's memory.</em>
  </p>
</div>

---

## ⚡ Why StateGuard?

When building multi-agent systems with **LangGraph**, **CrewAI**, or **AutoGen**, you quickly realize a dangerous truth: **LLMs are non-deterministic.** 

If an agent hallucinates mid-workflow and outputs malformed JSON, deletes critical context, or tries to transfer a negative bank balance, native checkpointers will happily save that corrupted data forever. 

**StateGuard** is a drop-in plugin that treats your agent's memory like a secure database:
1. **Protects** state with strict Invariants (like TypeScript for memory).
2. **Blocks** corrupt data from saving using ACID-like Transactions.
3. **Undoes** external side-effects (like Stripe charges) using the Saga Pattern.
4. **Self-Corrects** via automated LLM Retry loops.

---

## 🚀 Quick Start (Time-to-Magic: 60s)

Install StateGuard:
```bash
pip install stateguard
```

Wrap your existing LangGraph nodes. You don't need to rewrite your agents!

```python
from stateguard import guard, Saga
from stateguard.proxy import GuardedState

# 1. Define strict invariants for your memory
@guard.invariant(name="no_negative_balance")
def check_balance(state):
    return state.get("balance", 0) >= 0

def my_agent_node(state_dict):
    # 2. Wrap state in a Transactional Proxy
    state = GuardedState(state_dict)
    
    # 3. Open a Saga Transaction
    with Saga(state) as tx:
        # 🚨 Agent hallucinates and tries to deduct $500 from a $100 account!
        state["balance"] -= 500
        
    # > [BLOCKED: InvariantViolation - no_negative_balance]
    # > [STATE: Safely rolled back to original values!]
    
    return dict(state)
```

---

## 💎 Core Features

### 1. Invariant Enforcement (TypeScript for Memory)
Define what a "healthy" state looks like. StateGuard evaluates invariants automatically before any transaction commits. If the LLM breaks the rules, the transaction is blocked.
*Includes built-in stdlib rules for JSON schema validation, max step limits, and data drift detection.*

### 2. Database-like Transactions
Use `begin()`, `commit()`, and `rollback()` on your Python dictionaries. Either the entire multi-step agent reasoning process succeeds, or it safely rolls back to the starting point.

### 3. Saga Compensations (The "Undo" Button)
If your agent calls an external API (like booking a flight) but fails in the next step (like failing to book the hotel), you need to undo the flight!
```python
from stateguard.saga import saga_step

@saga_step(compensate=cancel_flight_api)
def book_flight(state):
    return {"flight_id": "fl_123"} # Passed automatically to cancel_flight_api!
```

### 4. LLM Self-Correction (Retry Loop)
Don't instantly fail when an LLM hallucinates. Use the `@retry_on_violation` decorator to catch the invariant error, pass it back to the LLM, and let the agent fix its own mistake!

---

## 🔌 Drop-In Integrations

StateGuard is completely framework agnostic, but provides first-class, drop-in support for the leading orchestrators:

- **[LangGraph Checkpointer](https://stateguard.dev/docs/langgraph)** - Replace your `MemorySaver` with `StateGuardCheckpointer` in 1 line of code.
- **[CrewAI Memory](https://stateguard.dev/docs/crewai)** 
- **[Vanilla Python / AutoGen](https://stateguard.dev/docs/python)**

---

## 📚 Documentation

View the full API Reference, Cookbooks, and integration guides at:
**[https://stateguard.dev/docs](https://stateguard.dev/docs)**

---

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details on how to run tests, submit PRs, and suggest new invariants.

## 📄 License

StateGuard is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
