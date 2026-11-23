"""Offline generator: OAS specs -> authoritative TAPI endpoint catalog.

Parses the vendored ONMI TAPI OpenAPI specs (tapi_overlay/spec/oas/*.yaml) and
emits tapi_overlay/endpoints_generated.py, which the runtime catalog loads. This
replaces hand-maintained endpoint paths with the real ones straight from the
spec, so the catalog is correct and complete across all TAPI modules.

We only need the RESTCONF *path* templates, so we extract path keys with a line
regex rather than pulling in a YAML parser (keeping the package zero-dependency).

Derivation from paths under /data/tapi-common:context:
  * subtree   = a container augmentation directly under the context
                (e.g. tapi-topology:topology-context)  -> depth-1, no '='
  * collection= a uuid-keyed list directly under the context or under one
                subtree (e.g. .../connectivity-context/connectivity-service={uuid})

Run:  python tools/gen_catalog.py
"""

from __future__ import annotations

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OAS_DIR = os.path.join(ROOT, "tapi_overlay", "spec", "oas")
OUT = os.path.join(ROOT, "tapi_overlay", "endpoints_generated.py")

CTX = "tapi-common:context"
PATH_RE = re.compile(r"^\s{2}(/data/" + re.escape(CTX) + r"\S*):\s*$")
KEY_RE = re.compile(r"^([\w:-]+)=\{([\w-]+)\}$")   # e.g. connectivity-service={uuid}

# Subtrees a TR-547 controller is expected to always expose.
REQUIRED = {"service-interface-point", "topology-context"}

# Human descriptions layered onto generated entries where we have them.
DESCRIPTIONS = {
    "service-interface-point": "Service Interface Points (SIPs) - customer-facing edges.",
    "topology-context": "Topology context: topologies + network-topology service.",
    "connectivity-context": "Connectivity services and their underlying connections.",
    "path-computation-context": "Path computation services and computed paths.",
    "notification-context": "Notifications and subscriptions.",
    "oam-context": "OAM maintenance entities and jobs.",
    "physical-context": "Physical/equipment inventory: devices, holders, equipment.",
    "virtual-network-context": "Virtual network services.",
    "stream-context": "Streaming (TR-548): available-stream / stream lists.",
    "stream-admin-context": "Streaming (TR-548): streaming admin config.",
}


def _paths(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = PATH_RE.match(line)
        if m:
            out.append(m.group(1)[len("/data/"):])   # strip '/data/'
    return out


def _segments(path: str) -> list[str]:
    return path.split("/")


def collect() -> tuple[dict[str, dict], dict[str, dict]]:
    subtrees: dict[str, dict] = {}
    collections: dict[str, dict] = {}

    for fname in sorted(os.listdir(OAS_DIR)):
        if not fname.endswith(".yaml"):
            continue
        module = fname[:-5]
        with open(os.path.join(OAS_DIR, fname), encoding="utf-8") as fh:
            paths = _paths(fh.read())

        for p in paths:
            segs = _segments(p)
            # segs[0] == CTX always.
            if len(segs) == 2:
                child = segs[1]
                km = KEY_RE.match(child)
                if km and km.group(2) == "uuid":
                    # Base-context uuid-list (e.g. service-interface-point, profile).
                    list_node, key = km.group(1), km.group(2)
                    local = list_node.split(":", 1)[-1]
                    collections.setdefault(local, {
                        "key": local + "s",
                        "parent_key": "__context__",
                        "list_name": _qualify(list_node, "tapi-common"),
                        "id_field": key,
                        "entity_kind": local,
                        "member_path": f"{list_node}={{id}}",
                    })
                elif ":" in child and "=" not in child:
                    # Module augmentation container directly under context.
                    local = child.split(":", 1)[-1]
                    subtrees.setdefault(local, {
                        "key": local,
                        "path": child,
                        "module": module,
                        "description": DESCRIPTIONS.get(local, f"{local} ({module})."),
                        "optional": local not in REQUIRED,
                    })
            elif len(segs) == 3:
                container, child = segs[1], segs[2]
                if ":" not in container or "=" in container:
                    continue
                km = KEY_RE.match(child)
                if not km or km.group(2) != "uuid":
                    continue
                sub_local = container.split(":", 1)[-1]
                list_node, key = km.group(1), km.group(2)
                local = list_node.split(":", 1)[-1]
                ck = f"{sub_local}:{local}"
                collections.setdefault(ck, {
                    "key": local + "s",
                    "parent_key": sub_local,
                    "list_name": _qualify(list_node, module),
                    "id_field": key,
                    "entity_kind": local,
                    "member_path": f"{container}/{list_node}={{id}}",
                })
    return subtrees, collections


def _qualify(list_node: str, module: str) -> str:
    return list_node if ":" in list_node else f"{module}:{list_node}"


def render(subtrees: dict[str, dict], collections: dict[str, dict]) -> str:
    def sub_line(s: dict) -> str:
        return (
            "    GenEndpoint(key={key!r}, path={path!r}, module={module!r}, "
            "description={description!r}, optional={optional!r}),"
        ).format(**s)

    def col_line(c: dict) -> str:
        return (
            "    GenCollection(key={key!r}, parent_key={parent_key!r}, "
            "list_name={list_name!r}, id_field={id_field!r}, "
            "entity_kind={entity_kind!r}, member_path={member_path!r}),"
        ).format(**c)

    subs = "\n".join(sub_line(s) for s in sorted(subtrees.values(), key=lambda x: x["key"]))
    cols = "\n".join(col_line(c) for c in sorted(collections.values(), key=lambda x: x["entity_kind"]))
    return f'''# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""AUTO-GENERATED by tools/gen_catalog.py from vendored TAPI OAS specs.
Do not edit by hand; re-run the generator to refresh. Covers all TAPI modules.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GenEndpoint:
    key: str
    path: str
    module: str
    description: str
    optional: bool


@dataclass(frozen=True)
class GenCollection:
    key: str
    parent_key: str      # "__context__" means directly under tapi-common:context
    list_name: str
    id_field: str
    entity_kind: str
    member_path: str


GENERATED_SUBTREES = [
{subs}
]

GENERATED_COLLECTIONS = [
{cols}
]
'''


def main() -> None:
    subtrees, collections = collect()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(render(subtrees, collections))
    print(f"Wrote {OUT}")
    print(f"  subtrees:    {len(subtrees)}")
    print(f"  collections: {len(collections)}")
    for s in sorted(subtrees):
        print(f"    subtree  {s}")
    for c in sorted({v['entity_kind'] for v in collections.values()}):
        print(f"    collect  {c}")


if __name__ == "__main__":
    main()
