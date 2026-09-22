# 🛡️ StateGuard

[![PyPI version](https://badge.fury.io/py/stateguard-core.svg)](https://badge.fury.io/py/stateguard-core)
[![Python Versions](https://img.shields.io/pypi/pyversions/stateguard-core.svg)](https://pypi.org/project/stateguard-core/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://static.pepy.tech/badge/stateguard-core/month)](https://pepy.tech/project/stateguard-core)
[![Discord](https://img.shields.io/discord/1234567890?label=discord&color=5865F2)](https://discord.gg/stateguard)

**Transactional State Management for Multi-Agent AI Systems.**

StateGuard prevents hallucinated side-effects, enforces business invariants, and provides rock-solid state snapshots for frameworks like LangGraph, CrewAI, and AutoGen.

## The Problem

Your LLM agent runs inside a `with` block. It reads state, calls APIs, modifies numbers.
But what happens when it hallucinates a negative budget and saves it? Nothing stops it.
LangGraph checkpoints it. CrewAI passes it forward. The next agent trusts it.

## The Fix (30 seconds)

```bash
pip install stateguard-core
```

```python
from stateguard import GuardedState, Saga, guard
from stateguard.stdlib.rules import require_bounds

state = GuardedState({"budget": 50000})
guard.add(require_bounds("budget", min_val=0))   # Rule: budget can never go negative

with Saga(state) as tx:
    state["budget"] = -5000   # Agent hallucinates
# ← InvariantViolation raised. State restored to 50000. Side effects undone.
```

## ✨ Features

- 📸 **Snapshot & Rollback**: Time-travel debugging for agent states.
- 🛡️ **Business Invariants**: Prevent agents from violating critical constraints.
- 🔄 **Saga & Compensation**: Handle side-effect failures gracefully.
- 🔒 **Namespace Isolation**: Keep multi-agent states from leaking.
- 📊 **Drift Detection**: Catch hallucination drift statistically (standalone; you call it).
- 🗄️ **SQLite Audit Store**: Append-only event log with a CLI (you log events explicitly).

📖 **Full documentation:** [docs/API.md](./docs/API.md)

## 🔌 Integrations

- **LangGraph** — `guarded_node` (per-node guard) and `StateGuardCheckpointer` (wraps your saver). See [docs/API.md](./docs/API.md#5-langgraph) and [examples/langgraph](./examples/langgraph/README.md).
- **CrewAI / AutoGen** — no dedicated adapters yet. `GuardedState` and `Saga` are plain Python, so you can wrap any agent step; see [examples/crewai](./examples/crewai/README.md) and [examples/autogen](./examples/autogen/README.md).

> Invariants are checked when a transaction ends, i.e. *after* your code has run. Side effects
> need a compensating action (`@saga_step(compensate=...)`) to be undone.

## 💬 What Developers Are Saying

> *"StateGuard saved us weeks of chasing silent state corruption bugs in our LangGraph agent pipeline."*
> — Early Adopter

## 🤝 Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.
