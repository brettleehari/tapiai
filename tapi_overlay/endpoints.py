# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 3 - TAPI 2.5 / TR-547 NBI endpoint catalog.

The catalog is generated from the vendored ONMI TAPI OpenAPI specs by
``tools/gen_catalog.py`` (see ``endpoints_generated.py``) so the paths and list
keys come straight from the spec rather than being hand-maintained. This module
adapts that generated data into the runtime ``Endpoint`` / ``CollectionSpec``
shapes and adds the small amount of behaviour the crawler needs.

Only the northbound TAPI interface is modelled: RESTCONF + JSON. No NETCONF/XML.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .endpoints_generated import GENERATED_COLLECTIONS, GENERATED_SUBTREES

# The root TAPI resource. All augmentations hang beneath it.
CONTEXT = "tapi-common:context"

# Handled by dedicated normalizers, so excluded from the generic collection loop.
_SPECIAL_KINDS = {"service-interface-point", "topology"}


@dataclass(frozen=True)
class Endpoint:
    """One harvestable RESTCONF resource (a GET target)."""

    key: str
    path: str            # relative to the context resource
    module: str
    description: str
    optional: bool = True


@dataclass(frozen=True)
class CollectionSpec:
    """A uuid-keyed list to split into per-entity files.

    ``parent_key`` is the :class:`Endpoint` key whose fetched body contains the
    list. ``member_path`` (relative to the context) drills into one member.
    """

    key: str
    parent_key: str
    list_name: str
    id_field: str
    entity_kind: str
    member_path: str


def _build_subtrees() -> list[Endpoint]:
    subs: list[Endpoint] = [
        # SIPs are a base-context list; fetch explicitly and normalize specially.
        Endpoint(
            key="service-interface-points",
            path="service-interface-point",
            module="tapi-common",
            description="Service Interface Points (SIPs) - customer-facing edges.",
            optional=False,
        )
    ]
    for g in GENERATED_SUBTREES:
        subs.append(
            Endpoint(
                key=g.key, path=g.path, module=g.module,
                description=g.description, optional=g.optional,
            )
        )
    # Base-context uuid-lists other than SIP (e.g. profile) get their own GET.
    for c in GENERATED_COLLECTIONS:
        if c.parent_key == "__context__" and c.entity_kind not in _SPECIAL_KINDS:
            subs.append(
                Endpoint(
                    key=c.entity_kind + "s",
                    path=c.member_path.replace("={id}", ""),
                    module="tapi-common",
                    description=f"Base-context {c.entity_kind} list.",
                    optional=True,
                )
            )
    return subs


def _build_collections() -> list[CollectionSpec]:
    cols: list[CollectionSpec] = []
    for c in GENERATED_COLLECTIONS:
        if c.entity_kind in _SPECIAL_KINDS:
            continue
        # parent_key is the Endpoint key whose body holds the list: for a
        # base-context list that is the synthesized "<kind>s" endpoint; for a
        # sub-list it is the container subtree's own key.
        parent = c.entity_kind + "s" if c.parent_key == "__context__" else c.parent_key
        cols.append(
            CollectionSpec(
                key=c.key, parent_key=parent, list_name=c.list_name,
                id_field=c.id_field, entity_kind=c.entity_kind,
                member_path=c.member_path,
            )
        )
    return cols


CONTEXT_SUBTREES: list[Endpoint] = _build_subtrees()
COLLECTIONS: list[CollectionSpec] = _build_collections()

# Per-topology child lists (handled specially: they live below each topology).
TOPOLOGY_CHILD_LISTS = {
    "nodes": ("tapi-topology:node", "node"),
    "links": ("tapi-topology:link", "link"),
}


@dataclass
class Catalog:
    """Bundles the catalog with the controller's RESTCONF rooting."""

    restconf_root: str = "/restconf/data"
    subtrees: list[Endpoint] = field(default_factory=lambda: list(CONTEXT_SUBTREES))
    collections: list[CollectionSpec] = field(default_factory=lambda: list(COLLECTIONS))

    def context_path(self) -> str:
        return f"{self.restconf_root.rstrip('/')}/{CONTEXT}"

    def subtree_path(self, ep: Endpoint) -> str:
        return f"{self.context_path()}/{ep.path}"
