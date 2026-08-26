# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 13 - Aider coding-agent integration.

Turns the harvested datasource into something the Aider OSS coding agent can
drive: a git repo plus an analyst guide and config tuned for answering network
questions. You supply a model + API key; the overlay harvests the network into
the repo; Aider reads the repo and answers.

    tapi-overlay agent --out ./network-datasource --model sonnet \\
        --ask "which connectivity-services are down and what nodes do they cross?"

Aider (https://aider.chat) is a separate tool: `pip install aider-chat`. This
module shells out to it; it never vendors it. Everything except the actual
Aider subprocess is exercised by the tests, so the wiring is verifiable without
Aider installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

# Files that make up the navigation layer - always in the agent's context.
_ALWAYS_READ = ["CONVENTIONS.md", "README.md", "QUERIES.md", "SCHEMA.md"]

CONVENTIONS = """# How to answer questions about this network

You are a **network analyst** working over a TAPI 2.5 (TR-547 NBI) snapshot that
was auto-harvested from an optical SDN controller. This repo is DATA, not an app
to build. Do not invent facts - every answer must trace to a file here.

## Read order (cheap -> expensive)
1. `QUERIES.md` - maps common questions to the exact files that answer them.
2. `index/*.csv` - compact summary tables. Answer counts/filters/state questions
   from these FIRST (e.g. `index/nodes.csv`, `index/active-conditions.csv`).
3. `index/topology-graph.json` - nodes, edges, adjacency for reachability/paths.
4. `entities/<kind>/<uuid>.json` - full per-object detail. Open these only when a
   summary table lacks the field you need.
5. `schema/*.tree` - the TAPI data model: what each field means.
6. `raw/*.json` - verbatim controller responses; the tie-breaking source of truth.

## Rules
- Prefer indexes and the graph over opening many entity files (token cost).
- Every entity file carries `_id` (uuid) and `_name` (resolved label); topology
  children also carry `_topology`/`_node`.
- State semantics: `operational-state` ENABLED/DISABLED (real health),
  `administrative-state` UNLOCKED/LOCKED (intended), `lifecycle-state` INSTALLED/PLANNED.
- Alarms are in `entities/active-condition/` with severity in
  `index/active-conditions.csv`.
- Cite the file path(s) you used in your answer.
- This is northbound TAPI only (RESTCONF/JSON). There is no device/NETCONF data here.
"""

AIDERIGNORE = """# Keep the large verbatim dumps out of the repo map to save tokens.
# They remain on disk as the source of truth; open them explicitly when needed.
raw/
"""


def _aider_conf(model: Optional[str]) -> str:
    lines = [
        "# Managed by tapi-overlay. Tuned for read/analysis over a data repo.",
        "auto-commits: false",
        "gitignore: false",
        "dark-mode: true",
        "read:",
    ]
    lines += [f"  - {f}" for f in _ALWAYS_READ]
    if model:
        lines.insert(1, f"model: {model}")
    return "\n".join(lines) + "\n"


@dataclass
class AgentPrep:
    git_initialized: bool
    assets_written: List[str]


def aider_path() -> Optional[str]:
    """Return the aider executable path, or None if not installed."""
    return shutil.which("aider")


def _run_git(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def ensure_git_repo(path: str) -> bool:
    """Make ``path`` a git repo with an initial commit if it is not one.

    Aider needs git for its repo map and undo. Returns True if we initialized it.
    """
    if os.path.isdir(os.path.join(path, ".git")):
        return False
    _run_git(["init", "-q"], cwd=path)
    # Ensure a committer identity exists locally so the initial commit succeeds
    # even on a machine with no global git config.
    if not _run_git(["config", "user.email"], cwd=path).stdout.strip():
        _run_git(["config", "user.email", "overlay@tapi.local"], cwd=path)
        _run_git(["config", "user.name", "tapi-overlay"], cwd=path)
    _run_git(["add", "-A"], cwd=path)
    _run_git(["commit", "-q", "-m", "Initial TAPI datasource snapshot"], cwd=path)
    return True


def write_agent_assets(path: str, model: Optional[str]) -> List[str]:
    """Write CONVENTIONS.md, .aiderignore, and .aider.conf.yml. Returns names."""
    written = []
    for name, content in (
        ("CONVENTIONS.md", CONVENTIONS),
        (".aiderignore", AIDERIGNORE),
        (".aider.conf.yml", _aider_conf(model)),
    ):
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(name)
    return written


def prepare(path: str, model: Optional[str]) -> AgentPrep:
    if not os.path.isdir(path):
        raise FileNotFoundError(f"datasource not found: {path}")
    assets = write_agent_assets(path, model)
    git_init = ensure_git_repo(path)
    return AgentPrep(git_initialized=git_init, assets_written=assets)


def build_aider_args(
    model: Optional[str],
    api_key: Optional[str],
    ask: Optional[str],
    yes: bool,
    extra: Optional[List[str]] = None,
) -> List[str]:
    """Construct the aider CLI arguments (executable prepended by the caller)."""
    args: List[str] = []
    if model:
        args += ["--model", model]
    if api_key:
        # Aider expects provider=key (e.g. anthropic=sk-...). A bare key is passed
        # through as-is; standard env vars (ANTHROPIC_API_KEY, ...) also work.
        args += ["--api-key", api_key]
    if ask is not None:
        # One-shot: run in ask mode (no edits) then exit.
        args += ["--message", f"/ask {ask}", "--no-auto-commits"]
    if yes:
        args += ["--yes-always"]
    if extra:
        args += extra
    return args


def run(
    path: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    ask: Optional[str] = None,
    yes: bool = False,
    extra: Optional[List[str]] = None,
) -> int:
    """Prepare the repo and launch Aider against it.

    Interactive when ``ask`` is None (inherits the terminal); one-shot otherwise.
    Returns Aider's exit code, or 127 if Aider is not installed.
    """
    exe = aider_path()
    if not exe:
        raise AiderNotInstalled()
    prepare(path, model)
    args = [exe, *build_aider_args(model, api_key, ask, yes, extra)]
    # Run with cwd = datasource so aider detects the repo root correctly.
    proc = subprocess.run(args, cwd=path, check=False)
    return proc.returncode


class AiderNotInstalled(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Aider is not installed. Install it with:  pip install aider-chat\n"
            "Then re-run the 'agent' command. See https://aider.chat for setup."
        )
