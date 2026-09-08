# Contributing to StateGuard

Thank you for your interest in contributing to StateGuard! We welcome issues, pull requests, and feedback.

## Local Development Setup

To set up your local development environment:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/edgento/stateguard.git
   cd stateguard
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install the package in editable mode with development dependencies:**
   ```bash
   pip install -e ".[dev,langgraph]"
   ```

## Running Tests

StateGuard uses `pytest` for testing. We require 100% test passing before merging PRs.

To run the test suite:
```bash
PYTHONPATH=src pytest tests/
```

To run with coverage:
```bash
PYTHONPATH=src pytest --cov=stateguard tests/
```

## Creating Invariants
If you are contributing a new standard library invariant (e.g. to `src/stateguard/stdlib/rules.py`), please ensure:
1. It is fully typed.
2. It is documented with a clear docstring.
3. It has a corresponding test in `tests/test_stdlib_rules.py`.

## Pull Request Process
1. Fork the repo and create your branch from `main`.
2. Write tests for your changes.
3. Ensure all tests pass.
4. Open a PR with a clear title and description.
