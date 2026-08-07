#!/usr/bin/env python3
"""Local MCQ grid annotator — click columns/rows on a gold page, save JSON.

Usage:
  .venv/bin/python scripts/annotate_mcq_grid.py
  # open http://127.0.0.1:8765

Workflow:
  1. Pick a gold PNG (or any page image).
  2. Mode COLS — click centers of A, B, C, D, E on one clear row (5 clicks).
  3. Mode ROWS — click the TOP of each fill bubble for Q1..Q20 (20 clicks),
     or click Q1 then Q20 and use Fit rows to interpolate.
  4. On the next page, MOVE — drag (or arrow-nudge) the kept grid into place.
  5. RESIZE — drag E / S / SE handles (anchored at Q1/A) to stretch col/row pitch.
  6. Adjust cell_width / bubble_height if needed; preview updates live.
  7. Save → writes storage/annotations/<id>_grid.json (template-ready).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
GOLD_DEFAULT = ROOT / "tests/fixtures/gold/seamo_2025_format_b"
OUT_DEFAULT = ROOT / "storage/annotations"
HOST = "127.0.0.1"
PORT = 8765

# Static assets live next to this script so the HTML file can be edited safely.
ASSET_DIR = Path(__file__).resolve().parent / "annotate_mcq_grid_assets"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[annot] {args[0] if args else fmt}")

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, code: int, data: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _safe_path(self, rel: str) -> Path:
        raw = unquote(rel).lstrip("/")
        path = (ROOT / raw).resolve()
        if not str(path).startswith(str(ROOT.resolve())):
            raise ValueError("path escapes repo root")
        return path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            html = (ASSET_DIR / "index.html").read_bytes()
            self._bytes(200, html, "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._bytes(200, (ASSET_DIR / "app.js").read_bytes(), "application/javascript")
            return
        if parsed.path == "/app.css":
            self._bytes(200, (ASSET_DIR / "app.css").read_bytes(), "text/css")
            return
        if parsed.path == "/api/list":
            images = []
            gold = Path(self.server.gold_root)  # type: ignore[attr-defined]
            if gold.exists():
                for p in sorted(gold.rglob("page_*.png")):
                    images.append(str(p.relative_to(ROOT)))
            self._json(200, {"images": images})
            return
        if parsed.path == "/api/image":
            qs = parse_qs(parsed.query)
            rel = (qs.get("path") or [""])[0]
            try:
                path = self._safe_path(rel)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            if not path.is_file():
                self._json(404, {"error": f"not found: {rel}"})
                return
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._bytes(200, data, ctype)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/save":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode())
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return
        out_dir = Path(self.server.out_dir)  # type: ignore[attr-defined]
        out_dir.mkdir(parents=True, exist_ok=True)
        src = str(payload.get("source_image") or "page")
        stem = Path(src).stem
        paper = Path(src).parent.name
        out = out_dir / f"{paper}_{stem}_grid.json"
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self._json(200, {"ok": True, "path": str(out.relative_to(ROOT))})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default=str(GOLD_DEFAULT), help="Gold root to list")
    parser.add_argument("--out", default=str(OUT_DEFAULT), help="Annotation output dir")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    if not (ASSET_DIR / "index.html").is_file():
        raise SystemExit(f"Missing UI assets in {ASSET_DIR}")

    server = ThreadingHTTPServer((HOST, args.port), Handler)
    server.gold_root = args.gold  # type: ignore[attr-defined]
    server.out_dir = args.out  # type: ignore[attr-defined]

    url = f"http://{HOST}:{args.port}/"
    print(f"MCQ grid annotator → {url}")
    print(f"Gold list from: {args.gold}")
    print(f"Saves to:       {args.out}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
