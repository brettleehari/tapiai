# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Representative (small) TAPI 2.5 context used by the mock server and tests.

One topology with three nodes and two links, node-edge-points, two SIPs, one
connectivity-service + connection, and one inventory device. Shapes mirror the
TAPI information model closely enough to exercise the normalizer and graph.
"""

from __future__ import annotations


def _name(vn, v):
    return {"value-name": vn, "value": v}


NODE_A = {
    "uuid": "node-aaaa",
    "name": [_name("NODE_NAME", "ROADM-A")],
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "tapi-topology:owned-node-edge-point": [
        {"uuid": "nep-a1", "name": [_name("NAME", "A-degree-1")]},
        {"uuid": "nep-a2", "name": [_name("NAME", "A-degree-2")]},
    ],
}
NODE_B = {
    "uuid": "node-bbbb",
    "name": [_name("NODE_NAME", "ROADM-B")],
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "tapi-topology:owned-node-edge-point": [
        {"uuid": "nep-b1", "name": [_name("NAME", "B-degree-1")]},
        {"uuid": "nep-b2", "name": [_name("NAME", "B-degree-2")]},
    ],
}
NODE_C = {
    "uuid": "node-cccc",
    "name": [_name("NODE_NAME", "ROADM-C")],
    "administrative-state": "UNLOCKED",
    "operational-state": "DISABLED",
    "tapi-topology:owned-node-edge-point": [
        {"uuid": "nep-c1", "name": [_name("NAME", "C-degree-1")]},
    ],
}

LINK_AB = {
    "uuid": "link-ab",
    "name": [_name("LINK_NAME", "A<->B")],
    "operational-state": "ENABLED",
    "tapi-topology:node-edge-point": [
        {"node-uuid": "node-aaaa", "node-edge-point-uuid": "nep-a1"},
        {"node-uuid": "node-bbbb", "node-edge-point-uuid": "nep-b1"},
    ],
}
LINK_BC = {
    "uuid": "link-bc",
    "name": [_name("LINK_NAME", "B<->C")],
    "operational-state": "ENABLED",
    "tapi-topology:node-edge-point": [
        {"node-uuid": "node-bbbb", "node-edge-point-uuid": "nep-b2"},
        {"node-uuid": "node-cccc", "node-edge-point-uuid": "nep-c1"},
    ],
}

TOPOLOGY = {
    "uuid": "topo-0001",
    "name": [_name("TOPOLOGY_NAME", "otn-topology")],
    "tapi-topology:node": [NODE_A, NODE_B, NODE_C],
    "tapi-topology:link": [LINK_AB, LINK_BC],
}

TOPOLOGY_CONTEXT = {
    "tapi-topology:topology-context": {
        "nw-topology-service": {"uuid": "nwts-1"},
        "tapi-topology:topology": [TOPOLOGY],
    }
}

SIPS = {
    "tapi-common:service-interface-point": [
        {"uuid": "sip-1", "name": [_name("NAME", "client-1")],
         "administrative-state": "UNLOCKED", "operational-state": "ENABLED"},
        {"uuid": "sip-2", "name": [_name("NAME", "client-2")],
         "administrative-state": "UNLOCKED", "operational-state": "ENABLED"},
    ]
}

CONNECTIVITY_CONTEXT = {
    "tapi-connectivity:connectivity-context": {
        "tapi-connectivity:connectivity-service": [
            {"uuid": "cs-1", "name": [_name("NAME", "A-to-C-service")],
             "administrative-state": "UNLOCKED", "operational-state": "ENABLED",
             "end-point": [
                 {"local-id": "ep1", "service-interface-point": {"service-interface-point-uuid": "sip-1"}},
                 {"local-id": "ep2", "service-interface-point": {"service-interface-point-uuid": "sip-2"}},
             ],
             "connection": [{"connection-uuid": "conn-1"}]},
        ],
        "tapi-connectivity:connection": [
            {"uuid": "conn-1", "name": [_name("NAME", "conn-A-C")],
             "operational-state": "ENABLED"},
        ],
    }
}

PHYSICAL_CONTEXT = {
    "tapi-equipment:physical-context": {
        "tapi-equipment:device": [
            {"uuid": "dev-a", "name": [_name("NAME", "chassis-ROADM-A")]},
        ]
    }
}

CONTEXT_SHALLOW = {
    "tapi-common:context": {
        "uuid": "ctx-1",
        "name": [_name("NAME", "tapi-context")],
        "service-interface-point": [{"uuid": "sip-1"}, {"uuid": "sip-2"}],
    }
}
