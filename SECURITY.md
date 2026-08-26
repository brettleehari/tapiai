# Security Policy

## Scope

tapi-overlay is a read-oriented harvester for the TAPI northbound interface. It
handles controller credentials and produces a datasource that may contain
sensitive network topology. Please treat both with care.

## Reporting a vulnerability

Do **not** open a public issue for security problems. Instead, email the
maintainers listed in `MAINTAINERS.md` with:

- a description of the issue and its impact,
- steps to reproduce, and
- any suggested remediation.

We aim to acknowledge reports within 5 business days and to provide a remediation
plan or timeline after triage.

## Handling credentials and output

- Credentials are read from flags, environment variables (`TAPI_USERNAME`,
  `TAPI_PASSWORD`, `TAPI_TOKEN`), or an interactive prompt. Prefer env vars or
  the prompt so secrets do not land in shell history.
- The tool never writes credentials into the datasource or manifest.
- TLS verification is on by default; `--insecure` disables it for lab
  controllers with self-signed certificates and should not be used in
  production.
- The produced datasource can reveal full network topology. Store and share it
  with the same controls you apply to network inventory data.
