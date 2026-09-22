"""Executes every ```python block in docs/API.md so the documentation cannot drift from the code.

Blocks whose first line is ``# skip-test`` are illustrations and are not run.
Blocks that import langgraph are skipped when langgraph is not installed.
"""

import re
import sys
import types
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parent.parent / "docs" / "API.md"
FENCE = re.compile(r"^```python[^\n]*\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def _blocks():
    text = DOC.read_text(encoding="utf-8")
    out = []
    for match in FENCE.finditer(text):
        code = match.group(1)
        if code.lstrip().startswith("# skip-test"):
            continue
        line = text.count("\n", 0, match.start()) + 1
        out.append(pytest.param(code, id=f"API.md:{line}"))
    return out


@pytest.mark.parametrize("code", _blocks())
def test_doc_snippet_runs(code, tmp_path, monkeypatch):
    if "langgraph" in code:
        pytest.importorskip("langgraph")
    monkeypatch.chdir(tmp_path)              # snippets may create files (e.g. audit.db)
    mod = types.ModuleType("__docs__")
    monkeypatch.setitem(sys.modules, "__docs__", mod)
    exec(compile(code, "<docs/API.md>", "exec"), mod.__dict__)


def test_the_docs_actually_contain_runnable_snippets():
    assert len(_blocks()) >= 14
