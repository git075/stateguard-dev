# Contributing to StateGuard

First off, thank you for taking the time to contribute! 🎉

## Quick Development Setup

1. Fork the repo and clone it:
   ```bash
   git clone https://github.com/edgento/stateguard.git
   cd stateguard
   ```

2. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # On Windows: .venv\Scripts\activate
   ```

3. Install in editable mode with dev dependencies:
   ```bash
   pip install -e ".[dev,langgraph]"
   ```

4. Run the test suite:
   ```bash
   pytest
   ```

If all tests pass, you're ready to go!

## Making a Change

1. Create a branch: `git checkout -b my-feature`
2. Make your change
3. Run tests: `pytest`
4. Run formatting: `ruff format .`
5. Open a Pull Request — we aim to review within 24 hours

## What We're Looking For

- New stdlib rules (see `src/stateguard/stdlib/rules.py` for the pattern)
- Integration examples (see `examples/` folder)
- Documentation improvements
- Bug fixes with regression tests

Not sure where to start? Check the `good first issue` label on GitHub Issues.
Need help? Open a Discussion or DM us on Twitter @edgento.

## Code Style

- We use `ruff` for formatting and linting
- All public functions must have docstrings
- All new rules must have at least 1 test in `tests/`
