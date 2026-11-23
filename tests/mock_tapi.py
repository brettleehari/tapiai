# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 9 - Mock TR-547 NBI controller.

A stdlib http.server that speaks just enough RESTCONF to exercise the whole
overlay end-to-end over real TCP: Basic auth, the context, the implemented
subtrees, and a genuine 404 for not-implemented optional contexts.

Run standalone:  python -m tests.mock_tapi  (serves on 127.0.0.1:8443-ish)
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import sample_data as S

ROOT = "/restconf/data/tapi-common:context"

# Path suffix -> response body. Anything not here 404s (not implemented).
ROUTES = {
    "/service-interface-point": S.SIPS,
    "/tapi-topology:topology-context": S.TOPOLOGY_CONTEXT,
    "/tapi-connectivity:connectivity-context": S.CONNECTIVITY_CONTEXT,
    "/tapi-equipment:physical-context": S.PHYSICAL_CONTEXT,
}

VALID_USER = "admin"
VALID_PASS = "admin"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test noise
        pass

    def _authed(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:]).decode("utf-8")
            user, _, pw = raw.partition(":")
        except Exception:
            return False
        return user == VALID_USER and pw == VALID_PASS

    def _send(self, status: int, body: dict | None) -> None:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/yang-data+json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self):
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]
        if path == ROOT or path == ROOT + "/":
            self._send(200, S.CONTEXT_SHALLOW)
            return
        if path.startswith(ROOT):
            suffix = path[len(ROOT):]
            if suffix in ROUTES:
                self._send(200, ROUTES[suffix])
                return
            # Simulate a controller that has not implemented this context.
            self._send(404, {"error": "not implemented"})
            return
        self._send(404, {"error": "not found"})


class MockController:
    """Context-manager wrapper that runs the mock on an ephemeral port."""

    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return f"http://{self.host}:{self.server.server_address[1]}"

    def __enter__(self) -> MockController:
        self.server = ThreadingHTTPServer((self.host, 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


if __name__ == "__main__":  # pragma: no cover
    with MockController() as m:
        print(f"Mock TAPI NBI at {m.base_url}{ROOT}")
        print("Basic auth: admin/admin. Ctrl-C to stop.")
        try:
            import time

            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
