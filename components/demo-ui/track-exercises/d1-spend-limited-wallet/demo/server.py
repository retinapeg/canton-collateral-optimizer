#!/usr/bin/env python3
"""Dependency-free loopback dashboard for the spend-limited wallet demo."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from typing import Any
import webbrowser

from session import ACTION_PRESETS, DashboardSession
from wallet_client import DEFAULT_CLI_PATH, DemoClientError, FIXTURE_WARNING, WalletCli


STATIC_ROOT = Path(__file__).resolve().parent / "static"
TARGET_LABELS = {
    "devnet": "LIVE DEVNET",
    "sandbox": "LOCAL CANTON SANDBOX",
    "fixture": "OFFLINE FIXTURE",
}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class DemoHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        session: DashboardSession,
        mode_label: str,
    ) -> None:
        super().__init__(address, DemoRequestHandler)
        self.demo_session = session
        self.mode_label = mode_label
        self.session_lock = threading.Lock()


class DemoRequestHandler(BaseHTTPRequestHandler):
    server: DemoHttpServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path == "/api/bootstrap":
            self._bootstrap()
            return
        static = STATIC_FILES.get(path)
        if static is None:
            self._json_response(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "mode": self.server.mode_label, "error": "Not found"},
            )
            return
        filename, content_type = static
        try:
            body = (STATIC_ROOT / filename).read_bytes()
        except OSError:
            self._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "mode": self.server.mode_label,
                    "error": "Dashboard static asset is missing.",
                },
            )
            return
        if filename == "index.html":
            rendered = body.decode("utf-8").replace(
                "__MODE_LABEL__", _html_text(self.server.mode_label)
            )
            rendered = rendered.replace(
                "__FIXTURE_WARNING__",
                _html_text(FIXTURE_WARNING)
                if self.server.mode_label == "OFFLINE FIXTURE"
                else "",
            )
            body = rendered.encode("utf-8")
        self._send(HTTPStatus.OK, body, content_type)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.split("?", 1)[0] != "/api/action":
            self._json_response(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "mode": self.server.mode_label, "error": "Not found"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 1 or length > 4096:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "mode": self.server.mode_label,
                    "error": "Action body must be a small JSON object.",
                },
            )
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "mode": self.server.mode_label,
                    "error": "Action body was not valid UTF-8 JSON.",
                },
            )
            return
        action = payload.get("action") if isinstance(payload, dict) else None
        if action not in ACTION_PRESETS and action != "refresh":
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "mode": self.server.mode_label,
                    "error": "Unknown dashboard action.",
                },
            )
            return
        try:
            with self.server.session_lock:
                view = self.server.demo_session.execute_action(str(action))
            self._json_response(
                HTTPStatus.OK,
                {"ok": True, "mode": view["mode"], "view": view},
            )
        except DemoClientError as error:
            with self.server.session_lock:
                stale_view = self.server.demo_session.error_view()
            payload = error.as_dict()
            payload["staleView"] = stale_view
            self._json_response(HTTPStatus.SERVICE_UNAVAILABLE, payload)
        except Exception:
            self._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "mode": self.server.mode_label,
                    "error": {
                        "title": "DASHBOARD ERROR / NO LEDGER RESULT",
                        "code": "DASHBOARD_ERROR",
                        "message": "The dashboard encountered an unexpected local error.",
                        "remedy": "Use the P0 terminal operator and inspect the dashboard logs.",
                        "outcome": "NO_LEDGER_RESULT",
                    },
                },
            )

    def _bootstrap(self) -> None:
        try:
            with self.server.session_lock:
                view = self.server.demo_session.preflight_view()
            self._json_response(
                HTTPStatus.OK,
                {"ok": True, "mode": view["mode"], "view": view},
            )
        except DemoClientError as error:
            self._json_response(HTTPStatus.SERVICE_UNAVAILABLE, error.as_dict())

    def _json_response(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *arguments: object) -> None:
        del format_string, arguments


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local wallet demo dashboard.")
    parser.add_argument(
        "--target",
        choices=tuple(TARGET_LABELS),
        default=os.environ.get("WALLET_DEMO_TARGET", "sandbox"),
        help="Expected target; verifies but does not select the CLI ledger environment.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI_PATH)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--open", action="store_true", help="Open the dashboard in a browser.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    mode_label = TARGET_LABELS[arguments.target]
    cli = WalletCli(
        expected_mode=mode_label,
        cli_path=arguments.cli,
        timeout_seconds=arguments.timeout,
    )
    server = DemoHttpServer(
        (arguments.host, arguments.port), DashboardSession(cli), mode_label
    )
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print("SPEND-LIMITED AI WALLET DASHBOARD")
    print(f"MODE: {mode_label}")
    if mode_label == "OFFLINE FIXTURE":
        print(FIXTURE_WARNING)
    print(f"Open {url}")
    print("The P0 terminal operator remains demo/run_demo.py.")
    if arguments.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


def _html_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


if __name__ == "__main__":
    raise SystemExit(main())

