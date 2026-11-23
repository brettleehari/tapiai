# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 2 - TAPI client.

Wraps the HTTP layer with TAPI/RESTCONF semantics: fetch the context, fetch a
named augmentation subtree, drill into a single list member, and probe the
controller. Returns plain parsed JSON so the rest of the pipeline stays simple.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any

from .endpoints import Catalog, CollectionSpec, Endpoint
from .http import HttpClient


def _enc(key: str) -> str:
    """Percent-encode a RESTCONF list-key value (RFC 8040 s3.5.3).

    ``safe=""`` so '/', ',', ':' inside the value are all escaped; only the
    value passes through, never the surrounding path.
    """
    return urllib.parse.quote(str(key), safe="")


def _looks_unimplemented(status: int, body: Any) -> bool:
    """True when a >=400 response means 'module not supported' rather than a
    genuine error. ODL-based controllers answer an unknown augmentation module
    with 400 + error-tag 'unknown-element'/'invalid-value', not 404."""
    if status in (404, 405, 501):
        return True
    if status == 400 and isinstance(body, dict):
        text = str(body).lower()
        return "unknown-element" in text or "unknown element" in text
    return False


@dataclass
class FetchResult:
    key: str
    path: str
    status: int
    data: Any
    ok: bool
    note: str = ""


class TapiClient:
    """High-level TAPI NBI reader."""

    def __init__(self, http: HttpClient, catalog: Catalog | None = None):
        self.http = http
        self.catalog = catalog or Catalog()

    def _get_with_depth_fallback(
        self, path: str, depth: int | None
    ) -> tuple[int, Any, str]:
        """GET ``path`` with an optional ``depth``; if the controller rejects
        depth with 400, retry once without it. Returns (status, data, note)."""
        if depth is not None:
            status, data = self.http.get_json(path, query={"depth": str(depth)})
            if status != 400:
                return status, data, ""
            # Controller likely lacks the RFC 8040 'depth' capability; retry plain.
        status, data = self.http.get_json(path)
        note = "depth unsupported; fetched without it" if depth is not None else ""
        return status, data, note

    # -- probing ---------------------------------------------------------
    def probe(self) -> tuple[bool, str]:
        """Confirm we can reach the controller and authenticate.

        Fetches the context with depth=1 (falling back to a plain GET if the
        controller does not support the depth query) so we do not pull the whole
        network just to check connectivity.
        """
        status, data, _ = self._get_with_depth_fallback(
            self.catalog.context_path(), depth=1
        )
        if status == 401:
            return False, "authentication failed (401) - check credentials"
        if status == 403:
            return False, "authorization failed (403) - user lacks TAPI access"
        if status == 404:
            return False, "context not found (404) - check RESTCONF root / TAPI support"
        if status >= 400 or data is None:
            return False, f"unexpected response (HTTP {status})"
        return True, "reachable and authenticated"

    # -- context ---------------------------------------------------------
    def fetch_context_shallow(self, depth: int = 2) -> FetchResult:
        path = self.catalog.context_path()
        status, data, note = self._get_with_depth_fallback(path, depth=depth)
        return FetchResult(
            key="context-shallow",
            path=path,
            status=status,
            data=data,
            ok=status < 400 and data is not None,
            note=note,
        )

    def fetch_subtree(
        self,
        ep: Endpoint,
        fields: str | None = None,
        depth: int | None = None,
    ) -> FetchResult:
        # TR-547 supports RFC 8040 'fields' (select a subset) and 'depth' (bound
        # nesting) to keep responses tractable on large contexts. Both optional.
        path = self.catalog.subtree_path(ep)
        query: dict[str, str] = {}
        if fields:
            query["fields"] = fields
        if depth is not None:
            query["depth"] = str(depth)
        resp = self.http.get(path, query=query or None)
        status = resp.status
        data = None
        if status < 400:
            try:
                data = resp.json()
            except (ValueError, UnicodeDecodeError):
                data = None
        # A speculative augmentation the controller does not implement may answer
        # 404/405/501 or (ODL) 400 unknown-element. Treat all as 'not implemented'
        # for optional endpoints rather than a hard error.
        err_body = None
        if status == 400:
            try:
                err_body = resp.json()
            except (ValueError, UnicodeDecodeError):
                err_body = resp.text
        if ep.optional and _looks_unimplemented(status, err_body):
            return FetchResult(
                key=ep.key, path=path, status=status, data=None, ok=False,
                note="not implemented by controller",
            )
        return FetchResult(
            key=ep.key,
            path=path,
            status=status,
            data=data,
            ok=status < 400 and data is not None,
            note="" if status < 400 else f"HTTP {status}",
        )

    def fetch_member(self, spec: CollectionSpec, member_id: str) -> FetchResult:
        rel = spec.member_path.format(id=_enc(member_id))
        path = f"{self.catalog.context_path()}/{rel}"
        status, data = self.http.get_json(path)
        return FetchResult(
            key=f"{spec.entity_kind}:{member_id}",
            path=path,
            status=status,
            data=data,
            ok=status < 400 and data is not None,
        )

    def fetch_topology_children(
        self, topology_id: str, list_node: str
    ) -> FetchResult:
        """Fetch nodes or links for one topology directly.

        Used when a topology response omits its (large) node/link lists.
        """
        rel = (
            f"tapi-topology:topology-context/topology={_enc(topology_id)}/{list_node}"
        )
        path = f"{self.catalog.context_path()}/{rel}"
        status, data = self.http.get_json(path)
        return FetchResult(
            key=f"{topology_id}:{list_node}",
            path=path,
            status=status,
            data=data,
            ok=status < 400 and data is not None,
        )
