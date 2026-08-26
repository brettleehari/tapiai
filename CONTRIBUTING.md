# Contributing to tapi-overlay

Thanks for your interest in improving tapi-overlay. This project harvests a
TAPI 2.5 (TR-547 NBI) controller into a coding-agent-ready datasource, and we
welcome contributions that broaden model coverage, harden the harvester, or
improve how the datasource is consumed.

## Ground rules

- **Northbound only.** This project speaks the TAPI NBI (RESTCONF + JSON). It
  does not touch NETCONF/XML, device YANG, or any southbound concern.
- **Spec-driven catalog.** Endpoint paths are generated from the vendored ONMI
  OpenAPI specs — do not hand-edit `tapi_overlay/endpoints_generated.py`. Change
  `tools/gen_catalog.py` or refresh the specs (see `spec/PROVENANCE.md`) and
  regenerate.
- **Zero runtime dependencies.** The package uses only the Python standard
  library. Keep it that way; dev-only tooling belongs under the `dev` extra.

## Developer Certificate of Origin (DCO)

All commits must be signed off, certifying you wrote or have the right to submit
the change under the project license (see https://developercertificate.org):

```bash
git commit -s -m "your message"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line. Commits without a
sign-off will be asked to amend.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the checks

```bash
make test        # or: python -m pytest -q
make lint        # ruff (if installed)
make catalog     # regenerate the endpoint catalog from the vendored OAS
make build       # build sdist + wheel
```

The tests spin up an in-process mock TR-547 controller over real TCP and run the
full harvest → normalize → write → graph pipeline, plus streaming and agent
wiring — no external controller required.

## Pull requests

1. Open an issue describing the change first for anything non-trivial.
2. Keep PRs focused; add or update tests for behavior changes.
3. Ensure `make test` passes and new public functions have docstrings.
4. Sign off your commits (DCO).
5. Note user-facing changes in `CHANGELOG.md` under "Unreleased".

## Adding TAPI model coverage

Most coverage flows automatically from the OpenAPI specs. If you add support for
a new list or a special normalization:

- Prefer extending `tools/gen_catalog.py` so the catalog stays generated.
- Add a normalizer path in `tapi_overlay/normalizer.py` if the shape is special
  (as topology and SIPs are today).
- Add a mock fixture in `tests/sample_data.py` and assertions in
  `tests/test_end_to_end.py`.

## Reporting security issues

Please follow `SECURITY.md` rather than opening a public issue.
