# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 7 - Topology graph builder.

Builds a compact node/edge graph from normalized topology entities so a coding
agent can answer reachability, degree, and shortest-path questions without
re-parsing the full TAPI tree. Pure Python, no dependencies.

Edges come from TAPI links, which reference node-edge-points (NEPs). We resolve
each referenced NEP back to its owning node to get node-to-node adjacency.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

from .normalizer import get_list, strip_prefix


def _nep_refs(link: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return (node_uuid, nep_uuid) pairs a link connects.

    Leaf keys are resolved prefix-tolerantly: real controllers may emit
    ``tapi-topology:node-uuid`` while others emit a bare ``node-uuid``.
    """
    refs = []
    for ref in get_list(link, "tapi-topology:node-edge-point"):
        stripped = {strip_prefix(k): v for k, v in ref.items()}
        node = stripped.get("node-uuid") or stripped.get("node_uuid")
        nep = stripped.get("node-edge-point-uuid") or stripped.get(
            "node-edge-point_uuid"
        )
        if node and nep:
            refs.append((str(node), str(nep)))
    return refs


def build_graph(
    nodes: List[Dict[str, Any]], links: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Construct a serializable graph document.

    Structure::

        {
          "nodes": [{"id", "name", "topology", "degree"}],
          "edges": [{"id", "name", "a", "z", "endpoints":[nep,...]}],
          "adjacency": {node_id: [neighbor_id, ...]},
        }
    """
    node_index: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        nid = n.get("_id") or str(n.get("uuid", ""))
        node_index[nid] = {
            "id": nid,
            "name": n.get("_name", ""),
            "topology": n.get("_topology", ""),
            "degree": 0,
        }

    edges: List[Dict[str, Any]] = []
    adjacency: Dict[str, set] = defaultdict(set)
    # Degree counts link incidences (so parallel links between the same pair
    # each add to degree), separate from the neighbor set used for BFS.
    incidence: Dict[str, int] = defaultdict(int)

    for link in links:
        refs = _nep_refs(link)
        endpoint_nodes = [node for node, _ in refs]
        lid = link.get("_id") or str(link.get("uuid", ""))
        edge = {
            "id": lid,
            "name": link.get("_name", ""),
            "endpoints": [{"node": node, "nep": nep} for node, nep in refs],
            "a": endpoint_nodes[0] if endpoint_nodes else None,
            "z": endpoint_nodes[1] if len(endpoint_nodes) > 1 else None,
        }
        edges.append(edge)
        for node in endpoint_nodes:
            incidence[node] += 1
        # Connect every pair of distinct nodes the link touches.
        for i in range(len(endpoint_nodes)):
            for j in range(i + 1, len(endpoint_nodes)):
                a, b = endpoint_nodes[i], endpoint_nodes[j]
                if a != b:
                    adjacency[a].add(b)
                    adjacency[b].add(a)

    # A node referenced only by a link (never in the node list) still belongs in
    # the graph, or edges/adjacency would reference ids absent from "nodes".
    for nid in list(adjacency) + list(incidence):
        if nid not in node_index:
            node_index[nid] = {
                "id": nid, "name": "", "topology": "", "degree": 0, "external": True,
            }

    for nid, count in incidence.items():
        node_index[nid]["degree"] = count

    return {
        "nodes": list(node_index.values()),
        "edges": edges,
        "adjacency": {k: sorted(v) for k, v in adjacency.items()},
        "stats": {
            "node_count": len(node_index),
            "edge_count": len(edges),
            "isolated_nodes": [
                nid for nid in node_index if nid not in adjacency
            ],
        },
    }


def shortest_path(graph: Dict[str, Any], src: str, dst: str) -> Optional[List[str]]:
    """BFS shortest path over the adjacency map; ``None`` if unreachable."""
    adj = graph.get("adjacency", {})
    known = src in adj or any(n["id"] == src for n in graph.get("nodes", []))
    if src == dst:
        return [src] if known else None
    if src not in adj:
        return None
    seen = {src}
    queue: deque = deque([[src]])
    while queue:
        path = queue.popleft()
        for nxt in adj.get(path[-1], []):
            if nxt == dst:
                return path + [nxt]
            if nxt not in seen:
                seen.add(nxt)
                queue.append(path + [nxt])
    return None
