# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-25

### Added
- TAPI 2.5 (TR-547 NBI) harvester: `probe` and `harvest` commands that pull the
  full RESTCONF context into a git-friendly datasource (`raw/`, `entities/`,
  `index/`, `schema/`, plus README/QUERIES/SCHEMA guides and a MANIFEST).
- Endpoint catalog **generated from the ONMI TAPI OpenAPI specs**
  (`tools/gen_catalog.py`), covering all TAPI modules the controller implements:
  topology, connectivity, path-computation, OAM, fault-management, equipment,
  virtual-network, notifications, streaming, and technology-specific layers
  (photonic-media, digital-otn, ethernet, dsr).
- Topology graph (`index/topology-graph.json`) and a `path` command for
  reachability / shortest-path queries.
- Schema bundle: TAPI YANG tree maps shipped into every datasource under
  `schema/` with a `SCHEMA.md` index.
- TR-548 streaming ingestion (`streams`, `stream`) for incremental datasource
  refresh (SSE / NDJSON records, per-entity apply and delete).
- Aider coding-agent integration (`agent`): prepares the datasource as a git
  repo with an analyst guide and config, then launches Aider with a supplied
  model / API key to answer network questions.
- Zero-dependency RESTCONF client (stdlib only) with safe-method-only retries,
  `Retry-After` handling, `depth`/`fields` filtering, and robust error handling.
- In-process mock TR-547 controller and an end-to-end test suite.

[Unreleased]: https://github.com/brettleehari/tapiai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/brettleehari/tapiai/releases/tag/v0.1.0
