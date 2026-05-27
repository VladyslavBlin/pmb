"""Helper: read base64 chunks from stdin and write a PNG file.

Usage:
    python _save_png.py <output_path>
Reads base64 string (no data: prefix) from stdin and decodes to PNG.
"""
import base64
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("usage: _save_png.py <output>", file=sys.stderr)
    sys.exit(1)

out = Path(sys.argv[1])
data = sys.stdin.read().strip()
if data.startswith("data:image/png;base64,"):
    data = data[len("data:image/png;base64,"):]
out.write_bytes(base64.b64decode(data))
print(f"wrote {out} ({out.stat().st_size} bytes)")
