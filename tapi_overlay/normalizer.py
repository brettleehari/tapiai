# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 5 - Normalizer.

Turns raw TAPI JSON (whole context or a single subtree) into flat lists of
per-entity records. TAPI/RESTCONF keys are module-prefixed and sometimes
wrapped by the resource name, so every accessor tolerates both prefixed and
bare keys and unwraps single-key envelopes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .endpoints import COLLECTIONS, TOPOLOGY_CHILD_LISTS, CollectionSpec


def strip_prefix(key: str) -> str:
    """``tapi-topology:node`` -> ``node``."""
    return key.split(":", 1)[1] if ":" in key else key


def get_list(container: Any, list_name: str) -> List[Dict[str, Any]]:
    """Return the list stored under ``list_name`` in ``container``.

    Matches both the module-prefixed key (``tapi-topology:node``) and the bare
    key (``node``). Returns ``[]`` when absent, and wraps a lone dict in a list
    (some controllers emit a single-member list as an object).
    """
    if not isinstance(container, dict):
        return []
    bare = strip_prefix(list_name)
    for key, value in container.items():
        if key == list_name or strip_prefix(key) == bare:
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
            if isinstance(value, dict):
                return [value]
    return []


def unwrap(data: Any, resource_key: str) -> Any:
    """Unwrap a ``{"<resource_key>": <body>}`` envelope if present."""
    if isinstance(data, dict):
        bare = strip_prefix(resource_key)
        for key, value in data.items():
            if key == resource_key or strip_prefix(key) == bare:
                return value
    return data


def entity_name(entity: Dict[str, Any]) -> str:
    """Best-effort human name from the TAPI ``name`` list.

    TAPI names are ``[{value-name, value}]`` pairs; prefer a NAME-ish entry.
    """
    names = get_list(entity, "name")
    if not names:
        return ""
    preferred = (
        "TOPOLOGY_NAME", "NODE_NAME", "LINK_NAME", "SERVICE_NAME",
        "CONNECTIVITY_SERVICE_NAME", "CONNECTION_NAME", "SIP_NAME", "NEP_NAME",
        "LOCAL_NAME", "NAME", "USER_LABEL",
    )
    for pref in preferred:
        for n in names:
            if str(n.get("value-name", "")).upper() == pref:
                return str(n.get("value", ""))
    # Fall back to any *NAME value-name before blindly taking the first entry,
    # which on real controllers is often a non-name id (INVENTORY_ID, PLUG_ID).
    for n in names:
        if str(n.get("value-name", "")).upper().endswith("NAME"):
            return str(n.get("value", ""))
    return str(names[0].get("value", ""))


def normalize_collection(
    parent_body: Any, spec: CollectionSpec
) -> List[Dict[str, Any]]:
    """Extract members of one collection from a subtree body."""
    # Tolerate both a bare subtree body and a whole-context envelope.
    body = unwrap(unwrap(parent_body, "context"), spec.parent_key_resource())
    return get_list(body, spec.list_name)


def _record(entity: Dict[str, Any], id_field: str) -> Dict[str, Any]:
    ident = entity.get(id_field) or entity.get("uuid") or ""
    rec: Dict[str, Any] = {
        "_id": str(ident),
        "_name": entity_name(entity),
        **entity,
    }
    if not ident:
        # No uuid: synthesize a stable content-derived id so uuid-less entities
        # do not collide on the empty string. Flag it so callers can tell.
        digest = hashlib.sha1(
            json.dumps(entity, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]
        rec["_id"] = f"anon-{digest}"
        rec["_id_synthetic"] = True
    return rec


def to_record(entity: Dict[str, Any], id_field: str = "uuid") -> Dict[str, Any]:
    """Public wrapper around the per-entity record builder."""
    return _record(entity, id_field)


def node_edge_points(node: Dict[str, Any], topology_id: str) -> List[Dict[str, Any]]:
    """Build NEP records for one node, tagged with their owning node/topology."""
    out = []
    for nep in get_list(node, "tapi-topology:owned-node-edge-point"):
        nrec = _record(nep, "uuid")
        nrec["_topology"] = topology_id
        nrec["_node"] = str(node.get("uuid", ""))
        out.append(nrec)
    return out


def normalize_topologies(topology_context_body: Any) -> Dict[str, Any]:
    """Split the topology-context into topologies, nodes, links, NEPs.

    Returns a dict keyed by entity_kind -> list of records. Nodes and links are
    tagged with their parent topology id so cross-references survive flattening.
    """
    body = unwrap(unwrap(topology_context_body, "context"), "topology-context")
    topologies = get_list(body, "tapi-topology:topology")

    out: Dict[str, List[Dict[str, Any]]] = {
        "topology": [],
        "node": [],
        "link": [],
        "node-edge-point": [],
    }
    for topo in topologies:
        topo_id = str(topo.get("uuid", ""))
        out["topology"].append(_record(topo, "uuid"))
        for node in get_list(topo, TOPOLOGY_CHILD_LISTS["nodes"][0]):
            rec = _record(node, "uuid")
            rec["_topology"] = topo_id
            out["node"].append(rec)
            for nep in get_list(node, "tapi-topology:owned-node-edge-point"):
                nrec = _record(nep, "uuid")
                nrec["_topology"] = topo_id
                nrec["_node"] = str(node.get("uuid", ""))
                out["node-edge-point"].append(nrec)
        for link in get_list(topo, TOPOLOGY_CHILD_LISTS["links"][0]):
            rec = _record(link, "uuid")
            rec["_topology"] = topo_id
            out["link"].append(rec)
    return out


def normalize_sips(sip_body: Any) -> List[Dict[str, Any]]:
    # SIPs may arrive as a bare list, wrapped under the resource key, or nested
    # inside a whole-context envelope.
    ctx = unwrap(sip_body, "context")
    body = unwrap(ctx, "service-interface-point")
    if isinstance(body, list):
        items = [v for v in body if isinstance(v, dict)]
    else:
        items = get_list(ctx, "tapi-common:service-interface-point")
    return [_record(s, "uuid") for s in items]


def normalize_generic(parent_body: Any, spec: CollectionSpec) -> List[Dict[str, Any]]:
    members = normalize_collection(parent_body, spec)
    return [_record(m, spec.id_field) for m in members]


# Attach a helper to CollectionSpec without importing normalizer there.
def _parent_key_resource(self: CollectionSpec) -> str:
    """Resource key used to unwrap the parent subtree envelope.

    ``unwrap`` matches prefix-tolerantly (bare name vs ``module:name``), so the
    local container name works for every module without a per-module mapping.
    """
    return self.parent_key


CollectionSpec.parent_key_resource = _parent_key_resource  # type: ignore[attr-defined]


def collection_specs_by_parent(parent_key: str) -> List[CollectionSpec]:
    return [c for c in COLLECTIONS if c.parent_key == parent_key]
