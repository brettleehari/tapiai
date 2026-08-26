# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 12 - TR-548 streaming ingestion (P4).

Keeps a harvested datasource fresh incrementally instead of re-polling the whole
context. Flow:

  1. Discover streams from tapi-streaming:stream-context (available-stream).
  2. Consume records from a stream - Server-Sent Events or newline-delimited
     JSON, or a pull-based record log as a fallback.
  3. Apply each record to the datasource: (re)fetch the changed object and
     rewrite its entity file, or delete it on an object-deletion record.

Each TAPI notification/record identifies a target object by type + uuid and a
change type, so the most reliable update is to re-read the object through the
normal RESTCONF path and overwrite its entity file - self-healing even if a
record is malformed or a batch is missed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import normalizer
from .client import TapiClient
from .datasource import DataSource, safe_filename
from .endpoints import CollectionSpec

# TAPI notification object-type -> (entity_kind folder, collection spec key).
# Used to map a change record to where its entity file lives and how to refetch.
_KIND_BY_OBJECT_TYPE = {
    "NODE": "node",
    "LINK": "link",
    "CONNECTION": "connection",
    "CONNECTIVITY_SERVICE": "connectivity-service",
    "SERVICE_INTERFACE_POINT": "service-interface-point",
    "NODE_EDGE_POINT": "node-edge-point",
    "TOPOLOGY": "topology",
    "PATH": "path",
    "PATH_COMP_SERVICE": "path-comp-service",
}

_DELETION_TYPES = {"OBJECT_DELETION", "NOTIFICATION_TYPE_OBJECT_DELETION", "DELETE"}


@dataclass
class StreamRecord:
    raw: Dict[str, Any]

    @property
    def notification_type(self) -> str:
        return str(self.raw.get("notification-type", self.raw.get("event-type", "")))

    @property
    def object_type(self) -> str:
        return str(self.raw.get("target-object-type", self.raw.get("object-type", "")))

    @property
    def object_id(self) -> str:
        return str(
            self.raw.get("target-object-identifier")
            or self.raw.get("target-object")
            or self.raw.get("uuid")
            or ""
        )

    @property
    def is_deletion(self) -> bool:
        t = self.notification_type.upper()
        return any(d in t for d in _DELETION_TYPES)


@dataclass
class StreamReport:
    records_seen: int = 0
    applied: int = 0
    deleted: int = 0
    skipped: int = 0
    warnings: List[str] = field(default_factory=list)


def discover_streams(client: TapiClient) -> List[Dict[str, Any]]:
    """Return available-stream descriptors from the stream-context, if any."""
    path = f"{client.catalog.context_path()}/tapi-streaming:stream-context"
    status, data = client.http.get_json(path)
    if status >= 400 or data is None:
        return []
    body = normalizer.unwrap(
        normalizer.unwrap(data, "context"), "tapi-streaming:stream-context"
    )
    return normalizer.get_list(body, "tapi-streaming:available-stream")


def parse_records(lines: Iterable[str]) -> Iterable[StreamRecord]:
    """Parse SSE or NDJSON lines into records.

    SSE frames carry payload on ``data:`` lines (possibly multi-line, ended by a
    blank line); NDJSON has one JSON object per line. Both are handled.
    """
    buffer: List[str] = []
    for line in lines:
        if line.startswith(":"):  # SSE comment / keep-alive
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
            continue
        if line == "":
            if buffer:
                yield from _emit("".join(buffer))
                buffer = []
            continue
        # Not SSE - treat the whole line as an NDJSON record.
        stripped = line.strip()
        if stripped:
            yield from _emit(stripped)
    if buffer:
        yield from _emit("".join(buffer))


def _emit(text: str) -> Iterable[StreamRecord]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return
    # A frame may hold a single record or a batch under a records/notification key.
    if isinstance(obj, dict):
        for key in ("record", "notification", "tapi-notification:notification"):
            if key in obj and isinstance(obj[key], dict):
                yield StreamRecord(obj[key])
                return
        batch = obj.get("records") or obj.get("notifications")
        if isinstance(batch, list):
            for item in batch:
                if isinstance(item, dict):
                    yield StreamRecord(item)
            return
        yield StreamRecord(obj)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield StreamRecord(item)


def _member_spec(kind: str, client: TapiClient) -> Optional[CollectionSpec]:
    for spec in client.catalog.collections:
        if spec.entity_kind == kind:
            return spec
    return None


def apply_record(
    record: StreamRecord, client: TapiClient, ds: DataSource, report: StreamReport
) -> None:
    """Apply one change record to the datasource."""
    kind = _KIND_BY_OBJECT_TYPE.get(record.object_type.upper())
    oid = record.object_id
    if not kind or not oid:
        report.skipped += 1
        return

    kind_dir = os.path.join(ds.entities_dir, safe_filename(kind))
    target = os.path.join(kind_dir, f"{safe_filename(oid)}.json")

    if record.is_deletion:
        if os.path.exists(target):
            os.remove(target)
            report.deleted += 1
        else:
            report.skipped += 1
        return

    # Creation or change: re-read the authoritative object and overwrite.
    fresh = _refetch_object(kind, oid, client)
    if fresh is None:
        report.warnings.append(f"could not re-fetch {kind} {oid}")
        report.skipped += 1
        return
    _write_one(ds, kind, fresh)
    report.applied += 1


def _write_one(ds: DataSource, kind: str, rec: Dict[str, Any]) -> None:
    # Single-file write that does NOT prune the directory (unlike write_entities,
    # which is a full-snapshot operation). Streaming touches one entity at a time.
    import json as _json

    kind_dir = os.path.join(ds.entities_dir, safe_filename(kind))
    os.makedirs(kind_dir, exist_ok=True)
    ident = rec.get("_id") or rec.get("uuid") or "unknown"
    path = os.path.join(kind_dir, f"{safe_filename(str(ident))}.json")
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(rec, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def _refetch_object(
    kind: str, oid: str, client: TapiClient
) -> Optional[Dict[str, Any]]:
    spec = _member_spec(kind, client)
    if spec is not None:
        res = client.fetch_member(spec, oid)
        if res.ok and res.data is not None:
            body = normalizer.unwrap(res.data, spec.list_name)
            entity = body[0] if isinstance(body, list) and body else body
            if isinstance(entity, dict):
                return normalizer.to_record(entity, spec.id_field)
    # Topology children (node/link) live below a topology; skip precise refetch
    # and let the next full harvest reconcile - signal by returning None.
    return None


def ingest(
    client: TapiClient,
    ds: DataSource,
    stream_name: Optional[str] = None,
    max_records: Optional[int] = 100,
    max_seconds: Optional[float] = None,
) -> StreamReport:
    """Consume a stream and apply records until a bound is hit.

    ``max_records`` (default 100) bounds a one-shot sync; pass ``None`` with
    ``max_seconds`` for time-bounded following, or both None to follow until the
    server closes the stream.
    """
    report = StreamReport()
    streams = discover_streams(client)
    stream_path = _resolve_stream_path(client, streams, stream_name, report)
    if stream_path is None:
        return report

    for line in client.http.stream_lines(stream_path, max_seconds=max_seconds):
        for record in parse_records([line]):
            report.records_seen += 1
            apply_record(record, client, ds, report)
            if max_records is not None and report.records_seen >= max_records:
                return report
    return report


def _resolve_stream_path(
    client: TapiClient,
    streams: List[Dict[str, Any]],
    stream_name: Optional[str],
    report: StreamReport,
) -> Optional[str]:
    if not streams:
        report.warnings.append("no available streams advertised by the controller")
        return None
    chosen = None
    if stream_name:
        for s in streams:
            if normalizer.entity_name(s) == stream_name or s.get("name") == stream_name:
                chosen = s
                break
        if chosen is None:
            report.warnings.append(f"stream '{stream_name}' not found")
            return None
    else:
        chosen = streams[0]
    # A stream descriptor exposes its retrieval endpoint; fall back to the
    # conventional stream resource path if the controller does not spell one out.
    url = (
        chosen.get("stream-address")
        or chosen.get("connection-address")
        or chosen.get("url")
    )
    if url:
        return str(url)
    name = normalizer.entity_name(chosen) or chosen.get("uuid", "")
    return (
        f"{client.catalog.context_path()}/tapi-streaming:stream-context/"
        f"stream={name}/records"
    )
