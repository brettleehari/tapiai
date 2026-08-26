# Vendored TAPI spec provenance

The files under `oas/` and `tree/` are vendored **unmodified** from the ONMI
Transport API (TAPI) SDK so that the overlay is self-contained and its endpoint
catalog is reproducible offline.

| Field | Value |
| --- | --- |
| Upstream repo | https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI |
| Branch | `develop` |
| Retrieved | 2026-08-25 |
| License | Apache License, Version 2.0 |
| Paths taken | `OAS/*.yaml`, `YANG/*.tree` |

## Contents

- `oas/*.yaml` — OpenAPI specifications, one per TAPI module. Consumed by
  `tools/gen_catalog.py` to generate `tapi_overlay/endpoints_generated.py`.
- `tree/*.tree` — pyang tree renderings of the YANG modules. Shipped into every
  produced datasource under `schema/` so a consuming agent has the data model.

## Refreshing

To update to a newer upstream revision:

```bash
BASE=https://raw.githubusercontent.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/develop
for m in tapi-common tapi-connectivity tapi-topology tapi-path-computation \
         tapi-notification tapi-oam tapi-equipment tapi-virtual-network \
         tapi-streaming tapi-gnmi-streaming tapi-fm tapi-digital-otn \
         tapi-photonic-media tapi-dsr tapi-eth; do
  curl -sSL -o tapi_overlay/spec/oas/$m.yaml  "$BASE/OAS/$m.yaml"
  curl -sSL -o tapi_overlay/spec/tree/$m.tree "$BASE/YANG/$m.tree"
done
python tools/gen_catalog.py     # regenerate the catalog
python -m pytest                # confirm nothing broke
```

Then update the **Retrieved** date above and note the change in `CHANGELOG.md`.
