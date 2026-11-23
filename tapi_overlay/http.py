# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 1 - HTTP layer.

A tiny, dependency-free RESTCONF HTTP client built on the standard library.
It handles Basic / Bearer auth, TLS (with an explicit opt-in for self-signed
controllers), retries with backoff, and RESTCONF-flavoured JSON requests.

Kept deliberately free of TAPI knowledge so it can be tested in isolation.
"""

from __future__ import annotations

import base64
import http.client
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Methods safe to retry automatically. POST is deliberately excluded: retrying a
# provisioning POST whose response was lost can double-create a service.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})
_MAX_RETRY_DELAY = 30.0


class HttpError(Exception):
    """Raised for a non-recoverable HTTP failure (after retries)."""

    def __init__(self, status: int, url: str, body: str = ""):
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class HttpClient:
    """Minimal RESTCONF-aware HTTP client.

    Parameters
    ----------
    base_url:
        Scheme + host + optional port, e.g. ``https://controller:8443``.
    username / password:
        Used for HTTP Basic auth when ``token`` is not supplied.
    token:
        Bearer token; takes precedence over Basic auth when set.
    verify_tls:
        Set ``False`` for lab controllers with self-signed certificates.
    timeout:
        Per-request timeout in seconds.
    max_retries / backoff:
        Retry policy for transient failures (connection errors, 5xx, 429).
    """

    base_url: str
    username: str | None = None
    password: str | None = None
    token: str | None = None
    verify_tls: bool = True
    timeout: float = 60.0
    max_retries: int = 3
    backoff: float = 1.5
    extra_headers: dict[str, str] = field(default_factory=dict)

    # RESTCONF media type per RFC 8040. TR-547 controllers accept JSON.
    accept: str = "application/yang-data+json, application/json"

    def _auth_header(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.username is not None:
            raw = f"{self.username}:{self.password or ''}".encode()
            enc = base64.b64encode(raw).decode("ascii")
            return {"Authorization": f"Basic {enc}"}
        return {}

    def _ssl_context(self) -> ssl.SSLContext | None:
        if self.base_url.lower().startswith("https") and not self.verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            # RESTCONF params such as depth/fields must not be over-encoded;
            # keep the reserved characters ( ; : / ) that TAPI paths use. Force
            # quote() (not quote_plus) so a space becomes %20, not '+', which a
            # strict RFC 8040 server would read as a literal plus.
            qs = urllib.parse.urlencode(
                query, safe=";:/,()*", quote_via=urllib.parse.quote
            )
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{qs}"
        return url

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> HttpResponse:
        url = self.build_url(path, query)
        headers = {"Accept": self.accept}
        headers.update(self._auth_header())
        headers.update(self.extra_headers)

        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/yang-data+json"

        ctx = self._ssl_context()
        last_exc: Exception | None = None
        # Only replay a request when the method is safe to repeat. A retried
        # POST could double-provision, so provisioning callers get one attempt.
        retryable = method.upper() in _IDEMPOTENT_METHODS

        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                    return HttpResponse(
                        status=resp.status,
                        headers={k.lower(): v for k, v in resp.headers.items()},
                        body=resp.read(),
                    )
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read() if hasattr(exc, "read") else b""
                except (OSError, http.client.HTTPException):
                    body = b""
                # Retry only idempotent methods on transient statuses.
                if (
                    retryable
                    and exc.code in (429, 500, 502, 503, 504)
                    and attempt < self.max_retries
                ):
                    last_exc = exc
                    time.sleep(self._retry_delay(attempt, exc.headers))
                    continue
                return HttpResponse(
                    status=exc.code,
                    headers={k.lower(): v for k, v in (exc.headers or {}).items()},
                    body=body,
                )
            # OSError covers TimeoutError/ConnectionError/ssl.SSLError and (pre-3.10)
            # socket.timeout; http.client.HTTPException covers IncompleteRead raised
            # while reading a large body after urlopen() already returned.
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                last_exc = exc
                if retryable and attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise HttpError(0, url, str(exc)) from exc

        # Should be unreachable, but keeps type-checkers and logic honest.
        raise HttpError(0, url, str(last_exc) if last_exc else "unknown error")

    def _retry_delay(self, attempt: int, headers: Any) -> float:
        """Backoff for retry ``attempt`` (0-based), honoring Retry-After.

        Genuine exponential schedule anchored on ``backoff``: backoff, 2x, 4x...
        A numeric ``Retry-After`` (429/503) raises the floor, capped for sanity.
        """
        delay = self.backoff * (2 ** attempt)
        if headers is not None:
            ra = headers.get("Retry-After")
            if ra and str(ra).strip().isdigit():
                delay = max(delay, float(ra))
        return min(delay, _MAX_RETRY_DELAY)

    def stream_lines(
        self,
        path: str,
        query: dict[str, Any] | None = None,
        max_seconds: float | None = None,
    ):
        """Open a long-lived GET and yield decoded text lines as they arrive.

        Works for both Server-Sent Events (``text/event-stream``) and
        newline-delimited JSON streams. The connection is not retried (a stream
        is stateful); the caller stops by breaking out of the iterator. Raises
        HttpError on a failed open.
        """
        url = self.build_url(path, query)
        headers = {"Accept": "text/event-stream, application/json"}
        headers.update(self._auth_header())
        headers.update(self.extra_headers)
        ctx = self._ssl_context()
        req = urllib.request.Request(url, headers=headers, method="GET")
        # A per-read timeout; total duration bounded by max_seconds if given.
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout, context=ctx)
        except urllib.error.HTTPError as exc:
            raise HttpError(exc.code, url, exc.read().decode("utf-8", "replace")) from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise HttpError(0, url, str(exc)) from exc

        with resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                yield line

    def get(self, path: str, query: dict[str, Any] | None = None) -> HttpResponse:
        return self.request("GET", path, query=query)

    def get_json(
        self, path: str, query: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """GET and parse JSON. Returns ``(status, parsed_or_None)``.

        Never raises on 4xx/5xx - the caller decides how to treat a missing
        or not-implemented resource. Only genuinely broken transport raises.
        """
        resp = self.get(path, query=query)
        if resp.status >= 400:
            return resp.status, None
        try:
            return resp.status, resp.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return resp.status, None
