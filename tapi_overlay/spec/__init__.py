# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Vendored ONMI TAPI specs (OpenAPI + YANG tree maps).

These are copied verbatim from the ONMI TAPI repo (develop branch):
https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI

- ``oas/*.yaml``  : OpenAPI specs, the source for the generated endpoint catalog.
- ``tree/*.tree`` : pyang tree renderings of each YANG module - compact,
  human/agent-readable schema maps shipped into the datasource so a coding agent
  can reason against the model, not just instances.
"""

from __future__ import annotations

import os
from typing import Dict, List

SPEC_DIR = os.path.dirname(os.path.abspath(__file__))
OAS_DIR = os.path.join(SPEC_DIR, "oas")
TREE_DIR = os.path.join(SPEC_DIR, "tree")


def list_tree_files() -> List[str]:
    if not os.path.isdir(TREE_DIR):
        return []
    return sorted(f for f in os.listdir(TREE_DIR) if f.endswith(".tree"))


def read_tree(filename: str) -> str:
    with open(os.path.join(TREE_DIR, filename), encoding="utf-8") as fh:
        return fh.read()


def all_trees() -> Dict[str, str]:
    return {f: read_tree(f) for f in list_tree_files()}


def list_oas_files() -> List[str]:
    if not os.path.isdir(OAS_DIR):
        return []
    return sorted(f for f in os.listdir(OAS_DIR) if f.endswith(".yaml"))
