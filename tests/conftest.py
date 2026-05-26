"""Shared pytest setup for PMB.

Two responsibilities:

1. **sys.path fallback.** When the package is installed editable
   (`pip install -e .`) every test can simply `from pmb.X import Y` and
   nothing here matters. But if someone runs pytest from a clean check-out
   where `src/` is not on the path yet, individual tests have historically
   patched `sys.path` themselves; conftest centralises that fallback so
   new tests do not need the boilerplate.

2. **Common fixtures.** A `tmp_workspace` fixture that returns an isolated
   tmp directory pair (PMB_HOME, workspace cwd), useful for any test that
   needs to instantiate an Engine without colliding with `~/.pmb/`.

Existing tests that still do `sys.path.insert(...)` themselves keep
working unchanged - the operation is idempotent.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path fallback - safe even if pmb is already installed editable
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_pmb_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated PMB_HOME for the test. Restored automatically.

    Use this whenever a test instantiates Engine and you don't want it
    writing to the developer's real `~/.pmb/`.
    """
    home = tmp_path / "pmb_home"
    home.mkdir()
    monkeypatch.setenv("PMB_HOME", str(home))
    return home


@pytest.fixture
def tmp_workspace_dir(tmp_path: Path) -> Path:
    """An empty directory to act as the workspace `cwd` for an Engine."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def isolated_engine(tmp_pmb_home: Path, tmp_workspace_dir: Path):
    """A fresh Engine that writes to a tmp dir and tears down after the test.

    Yields the engine. Calls `engine.close()` on teardown if the method exists.
    """
    # Lazy import so collecting this fixture doesn't load LanceDB.
    from pmb.core.engine import Engine

    eng = Engine(cwd=tmp_workspace_dir, pmb_home=tmp_pmb_home, rerank_model=None)
    try:
        yield eng
    finally:
        close = getattr(eng, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # We don't fail teardown because of a cleanup hiccup; the
                # tmp_path fixture deletes the directory anyway.
                pass
