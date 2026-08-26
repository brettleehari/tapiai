# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 11 - Schema bundle writer (P2).

Copies the vendored YANG tree maps into ``<out>/schema/`` and writes a
``SCHEMA.md`` index. This gives the coding agent that consumes the datasource
the actual TAPI information model to reason against - so it understands what a
node, NEP, connection, or connectivity-service *is*, not just the instance JSON.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from . import spec

if TYPE_CHECKING:  # pragma: no cover
    from .datasource import DataSource

# Short human labels per module so the index reads well.
_MODULE_LABELS = {
    "tapi-common": "Common types: context, SIPs, names, capacities, profiles.",
    "tapi-topology": "Topology: topologies, nodes, links, node-edge-points.",
    "tapi-connectivity": "Connectivity: connectivity-services, connections, CEPs.",
    "tapi-path-computation": "Path computation: path-comp-services, paths, path-sets.",
    "tapi-notification": "Notifications, subscriptions, event records.",
    "tapi-oam": "OAM: MEGs, MEPs/MIPs, OAM jobs and PM data.",
    "tapi-equipment": "Physical inventory: devices, holders, equipment, spans.",
    "tapi-virtual-network": "Virtual network services.",
    "tapi-streaming": "Streaming (TR-548): streams, monitors, stream types.",
    "tapi-gnmi-streaming": "gNMI streaming binding.",
    "tapi-fm": "Fault management: alarms, current/history conditions, TCAs.",
    "tapi-digital-otn": "L1 OTN technology specifics (ODU/OTU, tributary slots).",
    "tapi-photonic-media": "L0 photonic/WDM technology specifics (media channels).",
    "tapi-dsr": "Digital Signal Rate client layer specifics.",
    "tapi-eth": "L2 Carrier Ethernet technology specifics.",
}


def _index(trees) -> str:
    lines = [
        "# TAPI Schema Maps",
        "",
        "pyang tree renderings of the TAPI 2.5 YANG modules (from ONMI TAPI,",
        "`develop`). Use these to understand the shape of any entity in",
        "`entities/`: each `.tree` shows containers, lists, leafs, and types.",
        "",
        "| Module | What it models | Tree file |",
        "| --- | --- | --- |",
    ]
    for fname in trees:
        module = fname[:-5]
        label = _MODULE_LABELS.get(module, "")
        lines.append(f"| `{module}` | {label} | `schema/{fname}` |")
    lines += [
        "",
        "## How to use with the instance data",
        "- An entity file at `entities/node/<uuid>.json` conforms to the `node`",
        "  list in `schema/tapi-topology.tree`.",
        "- Technology detail (wavelengths, ODU slots, ethernet) is carried as",
        "  augmentations inside the same instance JSON; see the technology-specific",
        "  `.tree` files (`tapi-photonic-media`, `tapi-digital-otn`, `tapi-eth`).",
        "- The full OpenAPI specs the endpoint paths came from are not shipped here",
        "  to keep the datasource small; they live in the overlay's `spec/oas/`.",
        "",
    ]
    return "\n".join(lines)


def write_schema_bundle(ds: "DataSource") -> int:
    """Write tree maps under <out>/schema/ and a SCHEMA.md index. Returns count."""
    trees = spec.list_tree_files()
    if not trees:
        return 0
    schema_dir = os.path.join(ds.out, "schema")
    os.makedirs(schema_dir, exist_ok=True)
    for fname in trees:
        with open(os.path.join(schema_dir, fname), "w", encoding="utf-8") as fh:
            fh.write(spec.read_tree(fname))
    ds.write_text("SCHEMA.md", _index(trees))
    return len(trees)
