"""Tiny HTTP server that serves static files AND accepts POST /save
with JSON {filename, png_base64} to write PNG screenshots to disk.

Run: python _capture_server.py <port>
"""
import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0].lstrip("/")
        if not path:
            path = "screenshots.html"
        f = ROOT / path
        if not f.exists() or not f.is_file():
            self.send_response(404); self._cors(); self.end_headers(); return
        self.send_response(200); self._cors()
        ct = "text/html" if f.suffix == ".html" else (
            "image/png" if f.suffix == ".png" else "text/plain")
        self.send_header("Content-Type", ct + "; charset=utf-8" if "text" in ct else ct)
        body = f.read_bytes()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/save":
            self.send_response(404); self._cors(); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8"))
            fn = data["filename"]
            b64 = data["png_base64"]
            if b64.startswith("data:image/png;base64,"):
                b64 = b64[len("data:image/png;base64,"):]
            out = ROOT / fn
            out.write_bytes(base64.b64decode(b64))
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True, "path": str(out), "bytes": out.stat().st_size
            }).encode("utf-8"))
            print(f"[saved] {fn} {out.stat().st_size} bytes", flush=True)
        except Exception as e:
            self.send_response(500); self._cors(); self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

    def log_message(self, *args): pass  # quiet


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    print(f"capture server on http://localhost:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
