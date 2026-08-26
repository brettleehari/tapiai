# tapi-overlay

[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-blue)](.github/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](pyproject.toml)
[![Dependencies](https://img.shields.io/badge/runtime%20deps-none-brightgreen)](pyproject.toml)

Point it at a **TAPI 2.5 (TR-547 NBI)** controller with an endpoint + credentials.
It automatically harvests the *entire* TAPI context and lays it down as a
git-friendly datasource — a "network as a code repo" that a coding agent can read
to answer almost any question about the network.

Northbound only: **RESTCONF + JSON**. No NETCONF/XML — that is the controller's
southbound concern and out of scope.

```
Overlay / OSS / orchestrator   <-- this tool
      │  TAPI NBI = RESTCONF + JSON (TR-547)
      ▼
Optical SDN controller (e.g. Virtuora NC)
      │  SBI = NETCONF/XML  (out of scope)
      ▼
NEs: ROADMs / transponders
```

## Install

Standard library only — no dependencies. Python 3.9+.

```bash
cd tapiai
pip install -e .        # optional; or just run as a module
```

## Use

```bash
# 1. Check connectivity + auth
python -m tapi_overlay probe \
    --endpoint https://controller:8443 --username admin

# 2. Harvest the whole network into ./network-datasource
python -m tapi_overlay harvest \
    --endpoint https://controller:8443 --username admin \
    --out ./network-datasource --insecure    # --insecure for self-signed lab certs

# 3. Ask a graph question straight from the datasource
python -m tapi_overlay path --out ./network-datasource <node-uuid-a> <node-uuid-z>

# 4. Keep it fresh incrementally via TR-548 streaming (after a harvest)
python -m tapi_overlay streams --endpoint https://controller:8443 --username admin
python -m tapi_overlay stream  --endpoint https://controller:8443 --username admin \
    --out ./network-datasource --max-records 500   # or --follow
```

Credentials via env instead of flags (keeps them out of shell history):
`TAPI_ENDPOINT`, `TAPI_USERNAME`, `TAPI_PASSWORD`, `TAPI_TOKEN` (bearer).
Omit `--password` on an interactive terminal and you'll be prompted.

## What you get (the datasource)

```
network-datasource/
  MANIFEST.json          what was fetched, when, counts, warnings
  raw/                   verbatim controller responses (source of truth)
  entities/<kind>/*.json one file per topology / node / link / NEP / service /
                         connection / device / alarm / oam-job / ...
  index/                 nodes.csv, links.csv, active-conditions.csv, ...
                         + topology-graph.json
  schema/                TAPI YANG tree maps - the data model each entity obeys
  README.md SCHEMA.md QUERIES.md   written for the consuming coding agent
```

Hand the whole folder to a coding agent. `QUERIES.md` maps common questions
("is A reachable from Z?", "which services are down?", "what alarms are active?")
to the exact files that answer them, and `schema/` tells the agent what every
field means.

## Ask the network with a coding agent (Aider)

The datasource is a git repo, so the [Aider](https://aider.chat) OSS coding agent
can read it and answer questions using any LLM you point it at. The overlay is
the **harness** that harvests the network; Aider is the **agent** that answers.

```bash
pip install aider-chat            # one-time
export ANTHROPIC_API_KEY=sk-...   # or OPENAI_API_KEY, etc.

# One command: harvest (if needed) then answer, in ask mode (no edits)
python -m tapi_overlay agent \
    --endpoint https://controller:8443 --username admin \
    --out ./network-datasource --model sonnet \
    --ask "which connectivity-services are down, and what nodes do they cross?"

# Or drop into an interactive analyst chat over an existing datasource
python -m tapi_overlay agent --out ./network-datasource --model sonnet
```

What the `agent` command does:
1. Harvests first if `--out` has no datasource and `--endpoint` is given.
2. Makes the datasource a git repo (Aider needs git) with an initial commit.
3. Writes `CONVENTIONS.md` (a network-analyst guide: read indexes and the graph
   before opening entity files, cite sources, state semantics), `.aiderignore`
   (keeps the large `raw/` dumps out of the repo map to save tokens), and
   `.aider.conf.yml` (model + always-read docs, auto-commits off).
4. Launches Aider with your model/API key — one-shot (`--ask`) or interactive.

API key: pass `--api-key anthropic=sk-...` or just set the standard env var
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...), which Aider reads automatically.
If Aider isn't installed, the command still prepares the repo and tells you how
to install it (exit code 3).

## Coverage: generated from the spec

The endpoint catalog is **generated from the ONMI TAPI OpenAPI specs**
(`tools/gen_catalog.py` -> `endpoints_generated.py`), so paths and list keys come
straight from the spec rather than being hand-maintained. Coverage spans all
TAPI modules the controller implements:

- **Core**: topology, connectivity, path-computation, equipment inventory,
  notifications, virtual-network.
- **OAM**: MEGs, MEPs/MIPs, OAM jobs, PM data.
- **Fault management**: active alarm conditions (`entities/active-condition/`,
  `index/active-conditions.csv`).
- **Streaming (TR-548)**: stream discovery + incremental ingestion.
- **Technology layers**: photonic-media (L0/WDM), digital-otn (L1), ethernet
  (L2), dsr - carried as augmentations inside the entity JSON.

Refresh the catalog after a spec bump: `python tools/gen_catalog.py`.

## Architecture (spine + leaves)

Spine: `endpoint + credentials → auto-harvest → normalized datasource → agent answers`.

| Leaf | Module | Role |
| --- | --- | --- |
| 1 | `http.py` | zero-dep RESTCONF transport (auth, TLS, retries) |
| 2 | `client.py` | TAPI read semantics over the transport |
| 3 | `endpoints.py` | TAPI 2.5 / TR-547 endpoint catalog (data-driven) |
| 4 | `crawler.py` | harvest orchestrator, tolerant of not-implemented contexts |
| 5 | `normalizer.py` | context/subtrees → per-entity records |
| 6 | `datasource.py` | writes the repo layout, manifest, indexes |
| 7 | `graph.py` | topology graph + shortest path |
| 8 | `docs.py` | generates README/QUERIES for the consuming agent |
| 9 | `tests/mock_tapi.py` | mock TR-547 NBI controller for offline testing |
| 10 | `cli.py` | CLI / orchestrator |
| 11 | `schema.py` | ships TAPI YANG tree maps into the datasource |
| 12 | `streaming.py` | TR-548 stream discovery + incremental ingestion |
| 13 | `agent.py` | Aider coding-agent integration (repo prep + launch) |

Plus `spec/` (vendored ONMI TAPI OAS + YANG tree maps) and `tools/gen_catalog.py`
(the OAS → catalog generator).

## Test

```bash
python -m unittest tests.test_end_to_end -v
# or run the mock standalone:
python -m tests.mock_tapi        # serves admin/admin on an ephemeral port
```

The tests spin up the mock controller over real TCP and run the full
harvest → normalize → write → graph pipeline.

## Project

- **Architecture**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Contributing**: [`CONTRIBUTING.md`](CONTRIBUTING.md) (DCO sign-off required)
- **Governance**: [`GOVERNANCE.md`](GOVERNANCE.md) · **Maintainers**: [`MAINTAINERS.md`](MAINTAINERS.md)
- **Security policy**: [`SECURITY.md`](SECURITY.md)
- **Changelog**: [`CHANGELOG.md`](CHANGELOG.md)

## License and attribution

Licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE). This project vendors, unmodified, the TAPI OpenAPI and YANG
artifacts from the [ONMI TAPI SDK](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI)
(also Apache-2.0); provenance is recorded in
[`tapi_overlay/spec/PROVENANCE.md`](tapi_overlay/spec/PROVENANCE.md). It is
structured to be contributed to the LF ONMI Transport API project.
