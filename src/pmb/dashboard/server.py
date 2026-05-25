"""
Stdlib HTTP server exposing PMB internals to a local web UI.

Endpoints:
  GET  /                     → dashboard HTML
  GET  /static/<file>        → static assets
  GET  /api/stats            → workspace + memory stats
  GET  /api/events?limit=50  → recent events (paginated)
  GET  /api/entities?limit=30 → top entities by mentions
  GET  /api/arcs             → narrative arcs
  GET  /api/event/<ulid>     → one event detail (+ reflections, facts, edges)
  POST /api/recall           → run recall (body: {"query": "...", "top_k": 10})
  POST /api/pin/<ulid>       → pin event
  POST /api/unpin/<ulid>     → unpin
  POST /api/archive/<ulid>   → archive
  POST /api/feedback         → log feedback (body: {ulid, verdict})

Binds to 127.0.0.1 only (no remote access by default).
"""

from __future__ import annotations

import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs


log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def make_handler(engine):
    """Factory that closes over the engine instance."""

    class _Handler(BaseHTTPRequestHandler):
        # ------------------------------------------------------------------
        # Output helpers
        # ------------------------------------------------------------------

        def _send_json(self, payload: dict | list, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self.send_error(404)
                return
            ctype, _ = mimetypes.guess_type(str(path))
            ctype = ctype or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            # Quiet — don't spam stdout per request
            log.debug(fmt, *args)

        # ------------------------------------------------------------------
        # Routing
        # ------------------------------------------------------------------

        def do_GET(self):
            parsed = urlparse(self.path)
            route = parsed.path
            qs = parse_qs(parsed.query or "")

            try:
                if route == "/" or route == "/index.html":
                    self._send_static(STATIC_DIR / "index.html")
                    return
                if route.startswith("/static/"):
                    rel = route[len("/static/"):]
                    self._send_static(STATIC_DIR / rel)
                    return
                if route == "/api/stats":
                    self._send_json(self._handle_stats())
                    return
                if route == "/api/events":
                    limit = int((qs.get("limit") or ["50"])[0])
                    self._send_json(self._handle_events(limit))
                    return
                if route == "/api/entities":
                    limit = int((qs.get("limit") or ["30"])[0])
                    kind = (qs.get("kind") or [None])[0]
                    self._send_json(self._handle_entities(limit, kind))
                    return
                if route == "/api/arcs":
                    self._send_json(self._handle_arcs())
                    return
                if route == "/api/graph":
                    limit = int((qs.get("limit") or ["80"])[0])
                    self._send_json(self._handle_graph(limit))
                    return
                if route.startswith("/api/event/"):
                    ulid = route[len("/api/event/"):]
                    self._send_json(self._handle_event_detail(ulid))
                    return
                if route == "/api/dedup_candidates":
                    limit = int((qs.get("limit") or ["100"])[0])
                    self._send_json(self._handle_dedup_candidates(limit))
                    return
                if route == "/api/perf":
                    hours = float((qs.get("hours") or ["24"])[0])
                    self._send_json(self._handle_perf(hours))
                    return
                self.send_error(404)
            except Exception as e:
                log.exception("GET %s failed", route)
                self._send_json({"error": str(e)}, status=500)

        def do_POST(self):
            parsed = urlparse(self.path)
            route = parsed.path
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                payload = {}
            try:
                if route == "/api/recall":
                    self._send_json(self._handle_recall(payload))
                    return
                if route.startswith("/api/pin/"):
                    self._send_json(self._handle_pin(route[len("/api/pin/"):], pin=True))
                    return
                if route.startswith("/api/unpin/"):
                    self._send_json(self._handle_pin(route[len("/api/unpin/"):], pin=False))
                    return
                if route.startswith("/api/archive/"):
                    self._send_json(self._handle_archive(route[len("/api/archive/"):]))
                    return
                if route == "/api/feedback":
                    self._send_json(self._handle_feedback(payload))
                    return
                if route == "/api/dedup_merge":
                    self._send_json(self._handle_dedup_merge(payload))
                    return
                if route == "/api/dedup_keep_both":
                    self._send_json(self._handle_dedup_keep(payload))
                    return
                if route == "/api/dedup_sweep":
                    self._send_json(self._handle_dedup_sweep(payload))
                    return
                self.send_error(404)
            except Exception as e:
                log.exception("POST %s failed", route)
                self._send_json({"error": str(e)}, status=500)

        # ------------------------------------------------------------------
        # Handlers
        # ------------------------------------------------------------------

        def _handle_stats(self) -> dict:
            stats = engine.stats() if hasattr(engine, "stats") else {}
            graph_stats = engine.graph_stats() if hasattr(engine, "graph_stats") else {}
            return {"workspace": stats, "graph": graph_stats}

        def _handle_events(self, limit: int) -> list[dict]:
            evs = engine.events.list_active(engine.workspace.id, limit=limit)
            return [
                {
                    "ulid": e.ulid,
                    "event_type": e.event_type,
                    "content": (e.content or "")[:500],
                    "timestamp": e.timestamp,
                    "importance": e.importance,
                    "tier": e.tier,
                    "access_count": e.access_count,
                    "metadata": e.metadata,
                }
                for e in evs
            ]

        def _handle_entities(self, limit: int, kind: Optional[str]) -> list[dict]:
            ents = engine.graph.top_entities(
                engine.workspace.id, kind=kind, limit=limit,
            )
            return [e.to_dict() for e in ents]

        def _handle_arcs(self) -> list[dict]:
            try:
                return engine.list_arcs(limit=100)
            except Exception:
                return []

        # Improvement JJ: MCP perf stats
        def _handle_perf(self, hours: float) -> dict:
            try:
                from pmb.mcp.perf import get_perf_stats
                return get_perf_stats(
                    db_path=engine.workspace.db_path,
                    workspace_id=engine.workspace.id,
                    hours=hours,
                )
            except Exception as e:
                log.exception("perf stats failed")
                return {"error": str(e)}

        # Improvement U: dedup endpoints
        def _handle_dedup_candidates(self, limit: int) -> list[dict]:
            try:
                return engine.dedupe_list_pending(limit=limit)
            except Exception as e:
                log.exception("dedup candidates failed")
                return [{"error": str(e)}]

        def _handle_dedup_merge(self, payload: dict) -> dict:
            """User clicked 'merge'. Archive `new_ulid`, point at `canonical`."""
            import sqlite3, time as _t, json as _j
            new_ulid = payload.get("new_ulid")
            canonical = payload.get("canonical_ulid")
            pending_id = payload.get("pending_id")
            if not new_ulid or not canonical:
                return {"error": "missing ulids"}
            try:
                from pmb.reasoning.dedup import _archive_with_pointer, mark_verdict
                _archive_with_pointer(engine.workspace.db_path, new_ulid, canonical)
                if pending_id:
                    mark_verdict(engine.workspace.db_path, int(pending_id), "merge")
                return {"ok": True, "merged": new_ulid, "into": canonical}
            except Exception as e:
                log.exception("dedup merge failed")
                return {"error": str(e)}

        def _handle_dedup_keep(self, payload: dict) -> dict:
            """User clicked 'keep both'. Just mark the pair resolved."""
            pending_id = payload.get("pending_id")
            if not pending_id:
                return {"error": "missing pending_id"}
            try:
                from pmb.reasoning.dedup import mark_verdict
                mark_verdict(engine.workspace.db_path, int(pending_id), "keep_both")
                return {"ok": True}
            except Exception as e:
                return {"error": str(e)}

        def _handle_dedup_sweep(self, payload: dict) -> dict:
            """Trigger a full workspace sweep (background-ish — blocks request)."""
            threshold = float(payload.get("threshold", 0.92))
            try:
                return engine.dedupe_sweep(threshold=threshold)
            except Exception as e:
                log.exception("dedup sweep failed")
                return {"error": str(e)}

        def _handle_graph(self, limit: int) -> dict:
            """Return nodes + edges for visualization.
            Top-N entities by mentions; only edges between included nodes."""
            import sqlite3
            ws = engine.workspace.id
            with sqlite3.connect(engine.workspace.db_path) as conn:
                conn.row_factory = sqlite3.Row
                ent_rows = conn.execute(
                    "SELECT id, kind, name, n_mentions FROM graph_entities "
                    "WHERE workspace_id = ? ORDER BY n_mentions DESC LIMIT ?",
                    (ws, limit),
                ).fetchall()
                included = {r["id"] for r in ent_rows}
                if not included:
                    return {"nodes": [], "edges": []}
                placeholders = ",".join("?" * len(included))
                edge_rows = conn.execute(
                    f"SELECT entity_a, entity_b, weight FROM graph_edges "
                    f"WHERE workspace_id = ? AND entity_a IN ({placeholders}) "
                    f"AND entity_b IN ({placeholders}) "
                    f"ORDER BY weight DESC LIMIT 500",
                    (ws, *included, *included),
                ).fetchall()
            nodes = [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "name": r["name"],
                    "mentions": r["n_mentions"],
                }
                for r in ent_rows
            ]
            edges = [
                {
                    "source": r["entity_a"],
                    "target": r["entity_b"],
                    "weight": r["weight"],
                    # All graph edges in PMB are entity co-occurrence within
                    # the same event. There is no verb / typed relation yet —
                    # we expose this explicitly so the UI can label correctly.
                    "kind": "co_occurrence",
                }
                for r in edge_rows
            ]
            return {"nodes": nodes, "edges": edges}

        def _handle_event_detail(self, ulid: str) -> dict:
            ev = engine.events.get_by_ulid(ulid)
            if ev is None:
                return {"error": "event not found"}
            # Linked entities
            import sqlite3
            with sqlite3.connect(engine.workspace.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT e.kind, e.name, e.n_mentions
                    FROM graph_event_entities ee
                    JOIN graph_entities e ON e.id = ee.entity_id
                    WHERE ee.event_ulid = ?
                    """,
                    (ulid,),
                ).fetchall()
                entities = [
                    {"kind": r["kind"], "name": r["name"], "mentions": r["n_mentions"]}
                    for r in rows
                ]
                # Reflections / fact_atoms pointing to this event
                derived = conn.execute(
                    """
                    SELECT ulid, event_type, content
                    FROM events
                    WHERE workspace_id = ?
                      AND json_extract(metadata_json, '$.source_ulid') = ?
                      AND archived_at IS NULL
                    """,
                    (engine.workspace.id, ulid),
                ).fetchall()
                derived_list = [
                    {"ulid": r["ulid"], "event_type": r["event_type"],
                     "content": (r["content"] or "")[:300]}
                    for r in derived
                ]
                # Edges in/out
                edges_out = conn.execute(
                    "SELECT target_ulid, edge_type, confidence FROM event_edges "
                    "WHERE source_ulid = ?",
                    (ulid,),
                ).fetchall()
                edges_in = conn.execute(
                    "SELECT source_ulid, edge_type, confidence FROM event_edges "
                    "WHERE target_ulid = ?",
                    (ulid,),
                ).fetchall()
            return {
                "ulid": ev.ulid,
                "event_type": ev.event_type,
                "content": ev.content,
                "timestamp": ev.timestamp,
                "importance": ev.importance,
                "tier": ev.tier,
                "access_count": ev.access_count,
                "metadata": ev.metadata,
                "entities": entities,
                "derived": derived_list,
                "edges_out": [
                    {"target": r["target_ulid"], "type": r["edge_type"],
                     "confidence": r["confidence"]}
                    for r in edges_out
                ],
                "edges_in": [
                    {"source": r["source_ulid"], "type": r["edge_type"],
                     "confidence": r["confidence"]}
                    for r in edges_in
                ],
            }

        def _handle_recall(self, payload: dict) -> dict:
            query = payload.get("query") or ""
            top_k = int(payload.get("top_k") or 10)
            rerank = bool(payload.get("rerank") or False)
            if not query.strip():
                return {"error": "empty query"}
            pack = engine.recall(query, top_k=top_k, rerank=rerank)
            return pack.to_dict()

        def _handle_pin(self, ulid: str, pin: bool) -> dict:
            try:
                if pin:
                    engine.pin(ulid)
                else:
                    engine.unpin(ulid)
                return {"ok": True, "ulid": ulid, "pin": pin}
            except Exception as e:
                return {"error": str(e)}

        def _handle_archive(self, ulid: str) -> dict:
            try:
                engine.events.archive(ulid)
                engine.recall_cache.bump_generation()
                return {"ok": True, "ulid": ulid}
            except Exception as e:
                return {"error": str(e)}

        def _handle_feedback(self, payload: dict) -> dict:
            try:
                ulid = payload.get("ulid")
                verdict = payload.get("verdict", "good")
                if not ulid:
                    return {"error": "ulid required"}
                from pmb.health.feedback import record_feedback
                record_feedback(engine, ulid=ulid, verdict=verdict)
                return {"ok": True}
            except Exception as e:
                return {"error": str(e)}

    return _Handler


def run_dashboard(engine, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Blocking — runs until KeyboardInterrupt."""
    handler = make_handler(engine)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"PMB dashboard at http://{host}:{port}")
    print(f"Workspace: {engine.workspace.name} ({engine.workspace.id})")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
