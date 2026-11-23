# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""Leaf 10 - CLI / orchestrator.

Entry point that wires the leaves together:

    tapi-overlay harvest \\
        --endpoint https://controller:8443 \\
        --username admin --password secret \\
        --out ./network-datasource [--insecure] [--restconf-root /restconf/data]

Credentials can also come from env vars (TAPI_ENDPOINT / TAPI_USERNAME /
TAPI_PASSWORD / TAPI_TOKEN) so they never have to appear in shell history.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import os
import sys

from .client import TapiClient
from .crawler import Crawler
from .datasource import DataSource
from .endpoints import Catalog
from .graph import shortest_path
from .http import HttpClient, HttpError


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _build_client(args: argparse.Namespace) -> TapiClient:
    endpoint = args.endpoint or os.environ.get("TAPI_ENDPOINT")
    if not endpoint:
        print("error: --endpoint (or TAPI_ENDPOINT) is required", file=sys.stderr)
        raise SystemExit(2)

    username = args.username or os.environ.get("TAPI_USERNAME")
    password = args.password or os.environ.get("TAPI_PASSWORD")
    # Flags/env creds take precedence; only fall back to a token when no
    # username was supplied, so --username never gets silently overridden.
    token = args.token
    if not token and not username:
        token = os.environ.get("TAPI_TOKEN")

    if not token and username and password is None:
        # Prompt interactively rather than putting a password on the CLI.
        if sys.stdin.isatty():
            password = getpass.getpass(f"Password for {username}@{endpoint}: ")
        else:
            print(
                "error: password required for --username; pass --password, set "
                "TAPI_PASSWORD, or run interactively",
                file=sys.stderr,
            )
            raise SystemExit(2)

    http = HttpClient(
        base_url=endpoint,
        username=username,
        password=password,
        token=token,
        verify_tls=not args.insecure,
        timeout=args.timeout,
        max_retries=args.retries,
    )
    catalog = Catalog(restconf_root=args.restconf_root)
    return TapiClient(http, catalog)


def cmd_probe(args: argparse.Namespace) -> int:
    client = _build_client(args)
    ok, note = client.probe()
    print(("OK: " if ok else "FAIL: ") + note)
    return 0 if ok else 2


def cmd_harvest(args: argparse.Namespace) -> int:
    client = _build_client(args)
    ds = DataSource(args.out)
    crawler = Crawler(client, ds, now_iso=_now_iso())
    print(f"Harvesting {client.http.base_url} -> {ds.out}")
    report = crawler.run()
    if not report.ok:
        print(f"FAILED: {report.probe_note}", file=sys.stderr)
        for w in report.warnings:
            print(f"  - {w}", file=sys.stderr)
        return 2
    total = sum(report.entity_counts.values())
    print(f"Done. {total} entities across {len(report.entity_counts)} kinds.")
    for kind in sorted(report.entity_counts):
        print(f"  {kind}: {report.entity_counts[kind]}")
    if report.warnings:
        print("Warnings:")
        for w in report.warnings:
            print(f"  - {w}")
    print(f"Datasource ready at: {ds.out}")
    print(f"  {report.schema_files} schema map(s) in schema/.")
    print("  Start with README.md, SCHEMA.md, and QUERIES.md.")
    return 0


def cmd_streams(args: argparse.Namespace) -> int:
    from . import streaming

    client = _build_client(args)
    streams = streaming.discover_streams(client)
    if not streams:
        print("No streams advertised by the controller (tapi-streaming:stream-context).")
        return 1
    print(f"{len(streams)} stream(s):")
    for s in streams:
        from .normalizer import entity_name
        name = entity_name(s) or s.get("uuid", "?")
        addr = s.get("stream-address") or s.get("connection-address") or s.get("url") or ""
        print(f"  {name}  {addr}")
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    from . import streaming

    client = _build_client(args)
    ds = DataSource(args.out)
    if not os.path.exists(os.path.join(ds.out, "MANIFEST.json")):
        print("warning: no prior harvest found; streaming updates individual "
              "entities but a full 'harvest' should be run first.", file=sys.stderr)
    max_records = None if args.follow else args.max_records
    print(f"Ingesting stream into {ds.out} "
          f"({'follow' if args.follow else f'up to {args.max_records} records'})...")
    report = streaming.ingest(
        client, ds, stream_name=args.stream,
        max_records=max_records, max_seconds=args.max_seconds,
    )
    print(f"records={report.records_seen} applied={report.applied} "
          f"deleted={report.deleted} skipped={report.skipped}")
    for w in report.warnings[:10]:
        print(f"  - {w}")
    return 0 if report.records_seen or not report.warnings else 1


def cmd_agent(args: argparse.Namespace) -> int:
    from . import agent

    out = args.out
    manifest = os.path.join(out, "MANIFEST.json")
    # If there is no datasource yet and TAPI connection args are provided,
    # harvest first - the "API key + endpoint -> answer" one-shot flow.
    if not os.path.exists(manifest):
        if args.endpoint or os.environ.get("TAPI_ENDPOINT"):
            print("No datasource yet; harvesting first...")
            rc = cmd_harvest(args)
            if rc != 0:
                return rc
        else:
            print(f"error: no datasource at {out}. Run 'harvest' first, or pass "
                  f"--endpoint/--username to harvest now.", file=sys.stderr)
            return 2

    model = args.model or os.environ.get("AIDER_MODEL")
    if not agent.aider_path():
        print("Aider is not installed. Install it with:\n"
              "  pip install aider-chat\n"
              "Then re-run this command. See https://aider.chat", file=sys.stderr)
        # Still prepare the repo so it is ready the moment Aider is installed.
        prep = agent.prepare(out, model)
        print(f"Datasource prepared for Aider (git={'init' if prep.git_initialized else 'existing'}, "
              f"assets: {', '.join(prep.assets_written)}).")
        return 3

    try:
        return agent.run(
            out, model=model, api_key=args.api_key, ask=args.ask,
            yes=args.yes, extra=args.aider_arg or None,
        )
    except agent.AiderNotInstalled as exc:
        print(str(exc), file=sys.stderr)
        return 3


def cmd_path(args: argparse.Namespace) -> int:
    import json

    graph_file = os.path.join(args.out, "index", "topology-graph.json")
    if not os.path.exists(graph_file):
        print(f"error: no topology graph at {graph_file}; run harvest first", file=sys.stderr)
        return 2
    with open(graph_file, encoding="utf-8") as fh:
        graph = json.load(fh)
    path = shortest_path(graph, args.src, args.dst)
    if path is None:
        print(f"No path from {args.src} to {args.dst} (or unknown node).")
        return 1
    print(" -> ".join(path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tapi-overlay",
        description="Harvest a TAPI 2.5 (TR-547 NBI) controller into a coding-agent datasource.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_conn(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--endpoint", help="Controller base URL, e.g. https://ctrl:8443")
        sp.add_argument("--username")
        sp.add_argument("--password")
        sp.add_argument("--token", help="Bearer token (overrides basic auth)")
        sp.add_argument("--insecure", action="store_true", help="Skip TLS verification (lab/self-signed)")
        sp.add_argument("--restconf-root", default="/restconf/data")
        sp.add_argument("--timeout", type=float, default=60.0)
        sp.add_argument("--retries", type=int, default=3)

    sp_probe = sub.add_parser("probe", help="Check connectivity + auth only")
    add_conn(sp_probe)
    sp_probe.set_defaults(func=cmd_probe)

    sp_harvest = sub.add_parser("harvest", help="Fetch everything into a datasource")
    add_conn(sp_harvest)
    sp_harvest.add_argument("--out", default="./network-datasource", help="Output directory")
    sp_harvest.set_defaults(func=cmd_harvest)

    sp_streams = sub.add_parser("streams", help="List available TR-548 streams")
    add_conn(sp_streams)
    sp_streams.set_defaults(func=cmd_streams)

    sp_stream = sub.add_parser("stream", help="Incrementally sync from a TR-548 stream")
    add_conn(sp_stream)
    sp_stream.add_argument("--out", default="./network-datasource")
    sp_stream.add_argument("--stream", help="Stream name (default: first advertised)")
    sp_stream.add_argument("--max-records", type=int, default=100)
    sp_stream.add_argument("--max-seconds", type=float, default=None)
    sp_stream.add_argument("--follow", action="store_true", help="Follow until the server closes")
    sp_stream.set_defaults(func=cmd_stream)

    sp_agent = sub.add_parser(
        "agent",
        help="Drive the Aider coding agent over the datasource to answer questions",
    )
    add_conn(sp_agent)  # optional TAPI creds, so it can harvest-first if needed
    sp_agent.add_argument("--out", default="./network-datasource",
                          help="Datasource directory (harvested if absent and --endpoint given)")
    sp_agent.add_argument("--model", help="LLM for Aider (e.g. sonnet, anthropic/claude-...)")
    sp_agent.add_argument("--api-key", help="Aider API key as provider=key (e.g. anthropic=sk-...)")
    sp_agent.add_argument("--ask", help="One-shot question (ask mode, no edits); omit for interactive chat")
    sp_agent.add_argument("--yes", action="store_true", help="Auto-confirm Aider prompts")
    sp_agent.add_argument("--aider-arg", action="append",
                          help="Extra raw argument to pass through to aider (repeatable)")
    sp_agent.set_defaults(func=cmd_agent)

    sp_path = sub.add_parser("path", help="Shortest path between two node uuids from a harvested datasource")
    sp_path.add_argument("--out", default="./network-datasource")
    sp_path.add_argument("src")
    sp_path.add_argument("dst")
    sp_path.set_defaults(func=cmd_path)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except HttpError as exc:
        msg = f"error: {exc}"
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            msg += "\nhint: for a self-signed lab controller, add --insecure"
        print(msg, file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
