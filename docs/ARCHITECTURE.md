# Architecture

tapi-overlay turns a live TAPI 2.5 controller into a **network-as-data**
snapshot that a coding agent can read. It touches only the TAPI northbound
interface (RESTCONF + JSON, per TR-547/548) — never NETCONF/XML or any device
model.

## Positioning

```
   operator: model API key + TAPI endpoint/creds
                    │
                    ▼
        ┌──────────────────────────┐
        │       tapi-overlay        │   OSS / orchestrator layer
        └──────────────────────────┘
                    │  TAPI NBI = RESTCONF + JSON (TR-547/548)   ← the only interface used
                    ▼
           Optical SDN controller (e.g. Virtuora NC)
                    │  SBI = NETCONF/XML   ← out of scope
                    ▼
           NEs: ROADMs / transponders
```

## Pipeline

```
 endpoint+creds ─▶ HTTP ─▶ Client ─▶ Crawler ─▶ Normalizer ─▶ DataSource ─▶ git repo
                (transport) (TAPI reads)  │       (JSON→entities)  (writes)       │
                                          ├─▶ Graph  (topology → adjacency)       │
                                          ├─▶ Schema (YANG tree maps)             ▼
                                          └─▶ Docs   (README/QUERIES/SCHEMA)   Aider agent
                                                                               (LLM answers)
   Catalog (what to fetch) ◀── generated offline from the ONMI OpenAPI specs
   Streaming (TR-548) ─▶ incremental updates ─▶ DataSource (keeps it fresh)
```

## Modules ("leaves")

| # | Module | Responsibility |
|---|--------|----------------|
| 1 | `http.py` | Zero-dep RESTCONF transport: auth, TLS, safe-method retries, streaming reads |
| 2 | `client.py` | TAPI read semantics: probe, subtree fetch, member drill-down, `fields`/`depth` |
| 3 | `endpoints.py` + `endpoints_generated.py` | The catalog of what to harvest — generated from the OpenAPI specs |
| 4 | `crawler.py` | Orchestrates the harvest; tolerant of not-implemented contexts; builds indexes |
| 5 | `normalizer.py` | Splits raw TAPI JSON into per-entity records (prefix/envelope tolerant) |
| 6 | `datasource.py` | Writes the repo layout, manifest, CSV indexes; deterministic + prunes stale |
| 7 | `graph.py` | Topology graph + BFS shortest path |
| 8 | `docs.py` | Generates README/QUERIES that route questions to files |
| 9 | `tests/mock_tapi.py` | Mock TR-547 controller for offline testing |
| 10 | `cli.py` | Orchestrator: `probe · harvest · streams · stream · agent · path` |
| 11 | `schema.py` | Ships the TAPI YANG tree maps into `schema/` + `SCHEMA.md` |
| 12 | `streaming.py` | TR-548 stream discovery + incremental record apply |
| 13 | `agent.py` | Aider integration: repo prep + config/guide + launch |

Supporting: `spec/` (vendored ONMI OpenAPI + YANG, see `spec/PROVENANCE.md`) and
`tools/gen_catalog.py` (the spec → catalog generator).

## The datasource

```
network-datasource/
  MANIFEST.json          what/when/counts/warnings
  raw/                   verbatim controller responses (ground truth)
  entities/<kind>/*.json one file per node/link/service/connection/alarm/...
  index/                 nodes.csv, active-conditions.csv, ... + topology-graph.json
  schema/                TAPI YANG tree maps (the data model)
  README · SCHEMA · QUERIES · CONVENTIONS   guides for the consuming agent
  .git / .aider.conf.yml  ready for Aider
```

## Design principles

1. **Spec-driven, not hand-coded.** The endpoint catalog is generated from the
   ONMI OpenAPI specs, so coverage tracks the standard and cannot drift into
   path bugs. Regenerate with `make catalog` after a spec bump.
2. **Ground truth + navigable layers.** `raw/` is the source of truth;
   `entities/` is per-object detail; `index/` + `topology-graph.json` are the
   cheap summary layer. This is what lets an LLM agent answer at scale — read
   indexes first, open entities only when needed.
3. **Resilient harvest.** Optional contexts that a controller has not
   implemented are recorded as warnings, never fatal; large node/link lists
   omitted from a context response are re-fetched directly.
4. **Zero runtime dependencies.** Standard library only, so the harvester runs
   anywhere Python does. Aider is an optional external tool for the agent step.
5. **Deterministic output.** Filenames are uuid-based and records are sorted, so
   a refresh yields a clean git diff of exactly what changed in the network.

## Extending coverage

Most coverage flows from the OpenAPI specs automatically. Special shapes
(topology's nodes/links, SIPs) have dedicated normalizers. See
`CONTRIBUTING.md` for the workflow.
