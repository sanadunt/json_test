"""Serve the dashboard (static HTML) plus data/dashboard.json on localhost."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config

DASHBOARD_DIR = Path(__file__).parent / "dashboard"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, data_dir: Path, **kwargs):
        self.data_dir = data_dir
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            name = Path(clean[len("/data/"):]).name  # no traversal
            return str(self.data_dir / name)
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter
        if "dashboard.json" not in (args[0] if args else ""):
            super().log_message(fmt, *args)


def serve(cfg: Config, port: int = 8000) -> None:
    handler = partial(Handler, data_dir=cfg.data_dir.resolve())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"dashboard: http://127.0.0.1:{port}/  (data from {cfg.data_dir.resolve()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
