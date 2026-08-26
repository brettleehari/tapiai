# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 6 - Datasource writer.

Persists everything the crawler harvested into a git-friendly folder tree that a
coding agent can navigate like a repo:

    <out>/
      MANIFEST.json          what was fetched, when, from where, with counts
      raw/                   verbatim TAPI responses (source of truth)
      entities/<kind>/*.json one file per entity, stable filenames
      index/                 summary tables (CSV + JSON) and the topology graph
      README.md / QUERIES.md  written by the docs leaf

Filenames are deterministic (uuid-based) so re-running produces a clean diff.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(value: str, fallback: str = "unknown") -> str:
    value = (value or "").strip()
    if not value:
        return fallback
    slug = _SAFE.sub("-", value).strip("-")
    return slug[:120] or fallback


def _cell(value: Any) -> Any:
    """Render a CSV cell: scalars as-is, anything else as canonical JSON
    (not Python repr, which produces unparseable pseudo-JSON)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


class DataSource:
    def __init__(self, out_dir: str):
        self.out = os.path.abspath(out_dir)
        self.raw_dir = os.path.join(self.out, "raw")
        self.entities_dir = os.path.join(self.out, "entities")
        self.index_dir = os.path.join(self.out, "index")
        for d in (self.out, self.raw_dir, self.entities_dir, self.index_dir):
            os.makedirs(d, exist_ok=True)

    # -- raw responses ---------------------------------------------------
    def write_raw(self, key: str, data: Any) -> str:
        path = os.path.join(self.raw_dir, f"{safe_filename(key)}.json")
        _write_json(path, data)
        return os.path.relpath(path, self.out)

    # -- entities --------------------------------------------------------
    def write_entities(self, kind: str, records: Iterable[Dict[str, Any]]) -> int:
        kind_dir = os.path.join(self.entities_dir, safe_filename(kind))
        os.makedirs(kind_dir, exist_ok=True)
        # Sort by id so filenames (and any collision suffixes) are independent of
        # controller iteration order -> stable, reviewable diffs across re-runs.
        materialized = sorted(
            records, key=lambda r: str(r.get("_id") or r.get("uuid") or "")
        )
        seen: Dict[str, int] = {}
        written: set = set()
        for i, rec in enumerate(materialized):
            ident = rec.get("_id") or rec.get("uuid") or f"item-{i}"
            base = safe_filename(str(ident), fallback=f"item-{i}")
            if base in seen:
                seen[base] += 1
                base = f"{base}__{seen[base]}"
            else:
                seen[base] = 0
            fname = f"{base}.json"
            _write_json(os.path.join(kind_dir, fname), rec)
            written.add(fname)
        # Drop entity files from a previous run that no longer exist upstream,
        # so the datasource reflects the controller's current state.
        for existing in os.listdir(kind_dir):
            if existing.endswith(".json") and existing not in written:
                os.remove(os.path.join(kind_dir, existing))
        return len(materialized)

    # -- indexes ---------------------------------------------------------
    def write_index_json(self, name: str, obj: Any) -> str:
        path = os.path.join(self.index_dir, f"{safe_filename(name)}.json")
        _write_json(path, obj)
        return os.path.relpath(path, self.out)

    def write_index_csv(
        self, name: str, rows: List[Dict[str, Any]], columns: List[str]
    ) -> str:
        path = os.path.join(self.index_dir, f"{safe_filename(name)}.csv")
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _cell(row.get(c, "")) for c in columns})
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(buf.getvalue())
        return os.path.relpath(path, self.out)

    # -- manifest --------------------------------------------------------
    def write_manifest(self, manifest: Dict[str, Any]) -> str:
        path = os.path.join(self.out, "MANIFEST.json")
        _write_json(path, manifest)
        return os.path.relpath(path, self.out)

    def write_text(self, name: str, text: str) -> str:
        path = os.path.join(self.out, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return os.path.relpath(path, self.out)
