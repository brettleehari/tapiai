# Governance

tapi-overlay uses a lightweight maintainer model, intended to fold cleanly into
the ONMI / Linux Foundation TAPI project's governance on donation.

## Roles

- **Contributors** open issues and pull requests. Anyone can be a contributor.
- **Maintainers** (see `MAINTAINERS.md`) review and merge changes, cut releases,
  and steward the roadmap.

## Decision making

- Routine changes: lazy consensus — a PR may merge after at least one maintainer
  approval and passing CI, if no other maintainer objects.
- Significant changes (new subsystems, dependency policy, breaking CLI changes):
  require agreement of a majority of maintainers, tracked in the issue/PR.

## Contribution terms

All contributions are made under the Apache License, Version 2.0, and must carry
a DCO sign-off (`git commit -s`); see `CONTRIBUTING.md`.

## Releases

Releases follow [Semantic Versioning](https://semver.org). The `CHANGELOG.md`
"Unreleased" section is finalized, tagged `vX.Y.Z`, and a build is published.

## Donation to ONMI / LF

This project is structured to be donated to the LF ONMI Transport API project.
On acceptance, governance transfers to that project's Technical Steering
Committee and this document is superseded by the project's charter.
