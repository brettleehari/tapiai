# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 4 - Crawler / harvest orchestrator.

Drives the full pipeline: probe -> fetch every TAPI subtree -> write raw ->
normalize into entities -> build indexes and the topology graph -> write the
manifest and coding-agent docs. Designed to keep going when a controller has
not implemented an optional context, recording that fact rather than failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import docs, normalizer, schema
from .client import TapiClient
from .datasource import DataSource
from .endpoints import Catalog
from .graph import build_graph
from .http import HttpError


def _scalar(value: Any) -> str:
    """Flatten a possibly list/dict TAPI value to a short string for a CSV cell."""
    if isinstance(value, list):
        return ",".join(_scalar(v) for v in value)
    if isinstance(value, dict):
        # Common shapes: {"value-name","value"} or a single meaningful field.
        return str(value.get("value") or value.get("layer-protocol-name") or value)
    return "" if value is None else str(value)


@dataclass
class HarvestReport:
    endpoint: str
    started_at: str
    finished_at: str = ""
    ok: bool = False
    probe_note: str = ""
    subtrees: dict[str, dict[str, Any]] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    index_files: list[str] = field(default_factory=list)
    schema_files: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "tool": "tapi-overlay",
            "tapi_version": "2.5",
            "profile": "TR-547 NBI (RESTCONF + JSON)",
            "controller_endpoint": self.endpoint,
            "harvest_started_at": self.started_at,
            "harvest_finished_at": self.finished_at,
            "ok": self.ok,
            "probe": self.probe_note,
            "subtrees": self.subtrees,
            "entity_counts": self.entity_counts,
            "index_files": self.index_files,
            "schema_files": self.schema_files,
            "warnings": self.warnings,
        }


class Crawler:
    def __init__(
        self,
        client: TapiClient,
        datasource: DataSource,
        now_iso: str,
    ):
        self.client = client
        self.ds = datasource
        self.catalog: Catalog = client.catalog
        self.now = now_iso

    def run(self) -> HarvestReport:
        report = HarvestReport(
            endpoint=self.client.http.base_url, started_at=self.now
        )

        try:
            ok, note = self.client.probe()
        except HttpError as exc:
            ok, note = False, f"transport error: {exc}"
        report.probe_note = note
        if not ok:
            report.finished_at = self.now
            report.warnings.append(f"probe failed: {note}")
            # Still write a manifest so the failure is captured on disk.
            self.ds.write_manifest(report.to_manifest())
            return report

        # 1. Shallow context for top-level structure / SIPs presence.
        try:
            shallow = self.client.fetch_context_shallow(depth=3)
            if shallow.ok:
                raw_rel = self.ds.write_raw("context-shallow", shallow.data)
                report.subtrees["context-shallow"] = {
                    "path": shallow.path, "status": shallow.status,
                    "ok": True, "note": shallow.note, "raw": raw_rel,
                }
            else:
                report.warnings.append(
                    f"context-shallow: fetch failed (HTTP {shallow.status})"
                )
        except HttpError as exc:
            report.warnings.append(f"context-shallow: transport error: {exc}")

        # 2. Fetch every subtree, write raw, remember bodies for normalization.
        bodies: dict[str, Any] = {}
        for ep in self.catalog.subtrees:
            try:
                res = self.client.fetch_subtree(ep)
            except HttpError as exc:
                report.subtrees[ep.key] = {
                    "path": self.catalog.subtree_path(ep), "status": 0,
                    "ok": False, "note": f"transport error: {exc}",
                }
                report.warnings.append(f"{ep.key}: transport error: {exc}")
                continue
            entry = {
                "path": res.path,
                "status": res.status,
                "ok": res.ok,
                "note": res.note,
            }
            report.subtrees[ep.key] = entry
            if res.ok:
                raw_rel = self.ds.write_raw(ep.key, res.data)
                entry["raw"] = raw_rel
                bodies[ep.key] = res.data
            elif res.note == "not implemented by controller":
                report.warnings.append(f"{ep.key}: not implemented (HTTP {res.status})")
            else:
                report.warnings.append(f"{ep.key}: fetch failed (HTTP {res.status})")

        # 3. Normalize into entities.
        counts: dict[str, int] = {}
        graph_nodes: list[dict[str, Any]] = []
        graph_links: list[dict[str, Any]] = []

        # 3a. SIPs (customer-facing edges).
        if "service-interface-points" in bodies:
            sips = normalizer.normalize_sips(bodies["service-interface-points"])
            counts["service-interface-point"] = self.ds.write_entities(
                "service-interface-point", sips
            )
            rows = [
                {"id": s.get("_id", ""), "name": s.get("_name", ""),
                 "administrative_state": s.get("administrative-state", ""),
                 "operational_state": s.get("operational-state", "")}
                for s in sips
            ]
            report.index_files.append(self.ds.write_index_csv(
                "service-interface-points", rows,
                ["id", "name", "administrative_state", "operational_state"],
            ))

        # 3b. Topology (topologies, nodes, links, NEPs).
        if "topology-context" in bodies:
            topo = normalizer.normalize_topologies(bodies["topology-context"])
            self._refetch_missing_topology_children(
                bodies["topology-context"], topo, report
            )
            for kind, records in topo.items():
                if records:
                    counts[kind] = self.ds.write_entities(kind, records)
            graph_nodes = topo.get("node", [])
            graph_links = topo.get("link", [])
            self._index_topology(topo, report)

        # 3c. Generic collections (connectivity, path-comp, equipment, OAM, FM,
        # streaming, virtual-network, profiles, ...) - all driven by the catalog.
        for cspec in self.catalog.collections:
            if cspec.parent_key not in bodies:
                continue
            records = normalizer.normalize_generic(bodies[cspec.parent_key], cspec)
            if records:
                counts[cspec.entity_kind] = self.ds.write_entities(
                    cspec.entity_kind, records
                )
                self._index_collection(cspec, records, report)

        report.entity_counts = counts

        # 4. Topology graph for reachability / path questions.
        if graph_nodes:
            graph = build_graph(graph_nodes, graph_links)
            rel = self.ds.write_index_json("topology-graph", graph)
            report.index_files.append(rel)

        # 4b. Ship the TAPI schema maps so the consuming agent has the model.
        report.schema_files = schema.write_schema_bundle(self.ds)

        # 5. Manifest + coding-agent docs.
        report.finished_at = self.now
        # Success requires every non-optional subtree; optional gaps are warnings.
        report.ok = all(
            report.subtrees.get(ep.key, {}).get("ok")
            for ep in self.catalog.subtrees
            if not ep.optional
        )
        self.ds.write_manifest(report.to_manifest())
        docs.write_docs(self.ds, report)
        return report

    def _refetch_missing_topology_children(
        self, topology_body: Any, topo: dict[str, Any], report: HarvestReport
    ) -> None:
        """Re-fetch nodes/links directly for any topology that returned them
        empty (controllers often omit these large lists from the context GET)."""
        body = normalizer.unwrap(
            normalizer.unwrap(topology_body, "context"), "topology-context"
        )
        for topo_rec in normalizer.get_list(body, "tapi-topology:topology"):
            topo_id = str(topo_rec.get("uuid", ""))
            if not topo_id:
                continue
            for kind, (_list_node, _) in (
                ("node", ("node", None)), ("link", ("link", None))
            ):
                if normalizer.get_list(topo_rec, f"tapi-topology:{kind}"):
                    continue  # already present, nothing to backfill
                try:
                    res = self.client.fetch_topology_children(topo_id, kind)
                except HttpError as exc:
                    report.warnings.append(
                        f"topology {topo_id}: {kind} re-fetch transport error: {exc}"
                    )
                    continue
                if not res.ok:
                    continue
                self.ds.write_raw(f"topology-{topo_id}-{kind}", res.data)
                members = normalizer.get_list(res.data, f"tapi-topology:{kind}")
                for m in members:
                    rec = normalizer.to_record(m)
                    rec["_topology"] = topo_id
                    topo[kind].append(rec)
                    if kind == "node":
                        topo["node-edge-point"].extend(
                            normalizer.node_edge_points(m, topo_id)
                        )
                if members:
                    report.warnings.append(
                        f"topology {topo_id}: backfilled {len(members)} {kind}(s) "
                        "omitted from the context response"
                    )

    # -- index helpers ---------------------------------------------------
    def _index_collection(
        self, cspec, records: list[dict[str, Any]], report: HarvestReport
    ) -> None:
        """Write a summary CSV, choosing columns that suit the entity kind."""
        kind = cspec.entity_kind
        if kind == "active-condition":
            # Current fault-management alarms.
            rows = [
                {"id": r.get("_id", ""), "name": r.get("_name", ""),
                 "perceived_severity": r.get("perceived-severity", ""),
                 "probable_cause": _scalar(r.get("probable-cause", "")),
                 "alarm_condition_type": r.get("alarm-condition-type", ""),
                 "is_acknowledged": r.get("is-acknowledged", ""),
                 "raise_time": r.get("raise-time", "")}
                for r in records
            ]
            cols = ["id", "name", "perceived_severity", "probable_cause",
                    "alarm_condition_type", "is_acknowledged", "raise_time"]
        else:
            rows = [
                {"id": r.get("_id", ""), "name": r.get("_name", ""),
                 "administrative_state": r.get("administrative-state", ""),
                 "operational_state": r.get("operational-state", ""),
                 "lifecycle_state": r.get("lifecycle-state", ""),
                 "layer_protocol_name": _scalar(r.get("layer-protocol-name", ""))}
                for r in records
            ]
            cols = ["id", "name", "administrative_state", "operational_state",
                    "lifecycle_state", "layer_protocol_name"]
        report.index_files.append(self.ds.write_index_csv(cspec.key, rows, cols))

    def _index_topology(self, topo: dict[str, Any], report: HarvestReport) -> None:
        node_rows = [
            {"id": n.get("_id", ""), "name": n.get("_name", ""),
             "topology": n.get("_topology", ""),
             "administrative_state": n.get("administrative-state", ""),
             "operational_state": n.get("operational-state", "")}
            for n in topo.get("node", [])
        ]
        report.index_files.append(
            self.ds.write_index_csv(
                "nodes", node_rows,
                ["id", "name", "topology", "administrative_state", "operational_state"],
            )
        )
        link_rows = [
            {"id": link.get("_id", ""), "name": link.get("_name", ""),
             "topology": link.get("_topology", ""),
             "operational_state": link.get("operational-state", "")}
            for link in topo.get("link", [])
        ]
        report.index_files.append(
            self.ds.write_index_csv(
                "links", link_rows,
                ["id", "name", "topology", "operational_state"],
            )
        )
