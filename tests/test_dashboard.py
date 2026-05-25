"""Tests for the web dashboard server (Improvement I)."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pmb.core.engine import Engine
from pmb.dashboard.server import make_handler, run_dashboard


@pytest.fixture
def tmp_pmb_home():
    import gc, shutil, time as _t
    tmp = tempfile.mkdtemp()
    home = Path(tmp) / "pmb_home"
    os.environ["PMB_HOME"] = str(home)
    try:
        yield home
    finally:
        os.environ.pop("PMB_HOME", None)
        gc.collect()
        for _ in range(3):
            try:
                shutil.rmtree(tmp, ignore_errors=False)
                break
            except (OSError, PermissionError):
                _t.sleep(0.2)
                gc.collect()
        else:
            shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def tmp_workspace_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def running_dashboard(tmp_pmb_home, tmp_workspace_dir):
    """Start a dashboard server in a background thread; yield (engine, port)."""
    eng = Engine(cwd=tmp_workspace_dir, pmb_home=tmp_pmb_home)
    eng.record_fact("Caroline researched adoption agencies")
    eng.record_fact("Melanie does pottery")

    from http.server import ThreadingHTTPServer
    handler = make_handler(eng)
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Give it a moment
    time.sleep(0.1)
    yield (eng, port)
    server.shutdown()
    server.server_close()


# ----------------------------------------------------------------------
# Static + API
# ----------------------------------------------------------------------

def _get_json(port, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(port, path: str, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_dashboard_serves_html(running_dashboard):
    _, port = running_dashboard
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        body = resp.read().decode("utf-8")
    assert "PMB Dashboard" in body
    assert "<html" in body


def test_dashboard_api_stats(running_dashboard):
    eng, port = running_dashboard
    data = _get_json(port, "/api/stats")
    assert "workspace" in data
    assert "graph" in data


def test_dashboard_api_events(running_dashboard):
    eng, port = running_dashboard
    events = _get_json(port, "/api/events?limit=10")
    assert isinstance(events, list)
    assert len(events) >= 2
    assert "ulid" in events[0]
    assert "content" in events[0]


def test_dashboard_api_entities(running_dashboard):
    eng, port = running_dashboard
    ents = _get_json(port, "/api/entities?limit=20")
    assert isinstance(ents, list)


def test_dashboard_api_recall(running_dashboard):
    eng, port = running_dashboard
    res = _post_json(port, "/api/recall",
                     {"query": "adoption agencies", "top_k": 3})
    assert "results" in res
    assert "elapsed_ms" in res


def test_dashboard_event_detail(running_dashboard):
    eng, port = running_dashboard
    events = _get_json(port, "/api/events?limit=1")
    ulid = events[0]["ulid"]
    detail = _get_json(port, f"/api/event/{ulid}")
    assert "entities" in detail
    assert detail["ulid"] == ulid


def test_dashboard_404_on_unknown_route(running_dashboard):
    _, port = running_dashboard
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get_json(port, "/nonsense")
    assert exc.value.code == 404
