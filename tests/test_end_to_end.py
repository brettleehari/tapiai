# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The tapi-overlay Authors
"""End-to-end + unit tests for the overlay, driven against the mock controller.

Run:  python -m pytest tests/ -q     (or)     python -m tests.test_end_to_end
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tapi_overlay.client import TapiClient
from tapi_overlay.crawler import Crawler
from tapi_overlay.datasource import DataSource
from tapi_overlay.endpoints import Catalog
from tapi_overlay.graph import build_graph, shortest_path
from tapi_overlay.http import HttpClient
from tapi_overlay import normalizer

from tests.mock_tapi import MockController
from tests import sample_data as S


class NormalizerTests(unittest.TestCase):
    def test_topology_split(self):
        topo = normalizer.normalize_topologies(S.TOPOLOGY_CONTEXT)
        self.assertEqual(len(topo["topology"]), 1)
        self.assertEqual(len(topo["node"]), 3)
        self.assertEqual(len(topo["link"]), 2)
        # 2 + 2 + 1 NEPs across the three nodes.
        self.assertEqual(len(topo["node-edge-point"]), 5)
        self.assertEqual(topo["node"][0]["_name"], "ROADM-A")
        self.assertEqual(topo["node"][0]["_topology"], "topo-0001")

    def test_sip_normalize(self):
        sips = normalizer.normalize_sips(S.SIPS)
        self.assertEqual(len(sips), 2)
        self.assertEqual(sips[0]["_id"], "sip-1")

    def test_prefix_tolerance(self):
        # bare key should match prefixed lookups
        got = normalizer.get_list({"node": [{"uuid": "x"}]}, "tapi-topology:node")
        self.assertEqual(len(got), 1)


class GraphTests(unittest.TestCase):
    def setUp(self):
        topo = normalizer.normalize_topologies(S.TOPOLOGY_CONTEXT)
        self.graph = build_graph(topo["node"], topo["link"])

    def test_counts_and_degree(self):
        self.assertEqual(self.graph["stats"]["node_count"], 3)
        self.assertEqual(self.graph["stats"]["edge_count"], 2)
        degrees = {n["id"]: n["degree"] for n in self.graph["nodes"]}
        self.assertEqual(degrees["node-bbbb"], 2)  # B is the middle node
        self.assertEqual(degrees["node-aaaa"], 1)

    def test_shortest_path(self):
        path = shortest_path(self.graph, "node-aaaa", "node-cccc")
        self.assertEqual(path, ["node-aaaa", "node-bbbb", "node-cccc"])

    def test_unreachable(self):
        self.assertIsNone(shortest_path(self.graph, "node-aaaa", "ghost"))


class HardeningRegressionTests(unittest.TestCase):
    """Locks in the fixes from the multi-agent leaf review."""

    def test_whole_context_envelope_unwraps(self):
        # A controller that returns the whole context (not a bare subtree).
        whole = {"tapi-common:context": S.TOPOLOGY_CONTEXT}
        topo = normalizer.normalize_topologies(whole)
        self.assertEqual(len(topo["node"]), 3)  # would be 0 before the fix

    def test_prefixed_nep_refs(self):
        link = {"uuid": "l1", "tapi-topology:node-edge-point": [
            {"tapi-topology:node-uuid": "n1", "tapi-topology:node-edge-point-uuid": "e1"},
            {"tapi-topology:node-uuid": "n2", "tapi-topology:node-edge-point-uuid": "e2"},
        ]}
        g = build_graph(
            [{"_id": "n1"}, {"_id": "n2"}], [normalizer.to_record(link)]
        )
        self.assertEqual(g["stats"]["edge_count"], 1)
        self.assertEqual(g["adjacency"]["n1"], ["n2"])

    def test_parallel_links_increase_degree(self):
        nodes = [{"_id": "n1"}, {"_id": "n2"}]
        def link(uid):
            return normalizer.to_record({"uuid": uid, "tapi-topology:node-edge-point": [
                {"node-uuid": "n1", "node-edge-point-uuid": f"{uid}-a"},
                {"node-uuid": "n2", "node-edge-point-uuid": f"{uid}-b"},
            ]})
        g = build_graph(nodes, [link("la"), link("lb")])
        degrees = {n["id"]: n["degree"] for n in g["nodes"]}
        self.assertEqual(degrees["n1"], 2)  # two parallel links, not collapsed to 1

    def test_self_path_on_known_node(self):
        g = build_graph([{"_id": "solo"}], [])
        self.assertEqual(shortest_path(g, "solo", "solo"), ["solo"])
        self.assertIsNone(shortest_path(g, "ghost", "ghost"))

    def test_post_is_not_retried(self):
        # Count how many times the transport actually hits the wire for a POST
        # that always 503s: exactly one attempt (no retries for non-idempotent).
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        hits = {"n": 0}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    self.rfile.read(length)   # drain body to avoid RST
                hits["n"] += 1
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            http = HttpClient(
                base_url=f"http://127.0.0.1:{srv.server_address[1]}",
                max_retries=3, backoff=0.01,
            )
            resp = http.request("POST", "/x", body={"a": 1})
            self.assertEqual(resp.status, 503)
            self.assertEqual(hits["n"], 1)  # would be 4 if POST were retried
        finally:
            srv.shutdown()
            srv.server_close()

    def test_uuidless_entities_get_stable_ids(self):
        recs = [normalizer.to_record({"name": [{"value-name": "NAME", "value": "x"}]}),
                normalizer.to_record({"name": [{"value-name": "NAME", "value": "y"}]})]
        ids = {r["_id"] for r in recs}
        self.assertEqual(len(ids), 2)  # distinct synthetic ids, no empty-string collision
        self.assertTrue(all(r["_id"].startswith("anon-") for r in recs))


class EndToEndTests(unittest.TestCase):
    def test_full_harvest_against_mock(self):
        with MockController() as mock, tempfile.TemporaryDirectory() as out:
            http = HttpClient(base_url=mock.base_url, username="admin", password="admin")
            client = TapiClient(http, Catalog())
            ds = DataSource(out)
            report = Crawler(client, ds, now_iso="2026-08-25T00:00:00+00:00").run()

            self.assertTrue(report.ok, report.warnings)
            self.assertEqual(report.entity_counts.get("node"), 3)
            self.assertEqual(report.entity_counts.get("link"), 2)
            self.assertEqual(report.entity_counts.get("service-interface-point"), 2)
            self.assertEqual(report.entity_counts.get("connectivity-service"), 1)
            self.assertEqual(report.entity_counts.get("device"), 1)

            # Datasource files exist and are valid JSON.
            self.assertTrue(os.path.exists(os.path.join(out, "MANIFEST.json")))
            self.assertTrue(os.path.exists(os.path.join(out, "README.md")))
            self.assertTrue(os.path.exists(os.path.join(out, "QUERIES.md")))
            node_file = os.path.join(out, "entities", "node", "node-aaaa.json")
            with open(node_file) as fh:
                node = json.load(fh)
            self.assertEqual(node["_name"], "ROADM-A")

            graph_file = os.path.join(out, "index", "topology-graph.json")
            with open(graph_file) as fh:
                graph = json.load(fh)
            self.assertEqual(
                shortest_path(graph, "node-aaaa", "node-cccc"),
                ["node-aaaa", "node-bbbb", "node-cccc"],
            )

            # Optional not-implemented contexts are recorded, not fatal.
            self.assertIn("path-computation-context", report.subtrees)
            self.assertFalse(report.subtrees["path-computation-context"]["ok"])

    def test_auth_failure_is_reported(self):
        with MockController() as mock, tempfile.TemporaryDirectory() as out:
            http = HttpClient(base_url=mock.base_url, username="admin", password="wrong")
            client = TapiClient(http, Catalog())
            ds = DataSource(out)
            report = Crawler(client, ds, now_iso="2026-08-25T00:00:00+00:00").run()
            self.assertFalse(report.ok)
            self.assertIn("401", report.probe_note)


class CatalogGenerationTests(unittest.TestCase):
    def test_generated_catalog_covers_all_modules(self):
        from tapi_overlay.endpoints import CONTEXT_SUBTREES, COLLECTIONS
        keys = {e.key for e in CONTEXT_SUBTREES}
        # Modules the review corrected + FM/streaming discovered from OAS.
        self.assertIn("virtual-network-context", keys)
        self.assertIn("stream-context", keys)
        self.assertIn("fault-management-context", keys)
        kinds = {c.entity_kind for c in COLLECTIONS}
        for expected in ("connectivity-service", "connection", "path",
                         "device", "virtual-nw-service", "active-condition"):
            self.assertIn(expected, kinds)


class StreamingTests(unittest.TestCase):
    def test_parse_ndjson_and_sse(self):
        from tapi_overlay import streaming
        ndjson = ['{"notification-type":"OBJECT_CREATION","target-object-type":"NODE",'
                  '"target-object-identifier":"n1"}']
        recs = list(streaming.parse_records(ndjson))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].object_type, "NODE")
        self.assertFalse(recs[0].is_deletion)

        sse = ['data: {"notification-type":"OBJECT_DELETION",',
               'data: "target-object-type":"LINK","target-object-identifier":"l9"}', '']
        recs = list(streaming.parse_records(sse))
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0].is_deletion)
        self.assertEqual(recs[0].object_id, "l9")

    def test_apply_deletion_removes_entity_file(self):
        from tapi_overlay import streaming
        with tempfile.TemporaryDirectory() as out:
            ds = DataSource(out)
            ds.write_entities("node", [{"_id": "n1", "uuid": "n1"}])
            target = os.path.join(out, "entities", "node", "n1.json")
            self.assertTrue(os.path.exists(target))

            rec = streaming.StreamRecord({
                "notification-type": "OBJECT_DELETION",
                "target-object-type": "NODE", "target-object-identifier": "n1",
            })
            report = streaming.StreamReport()
            # client not needed for a deletion.
            streaming.apply_record(rec, client=None, ds=ds, report=report)
            self.assertFalse(os.path.exists(target))
            self.assertEqual(report.deleted, 1)

    def test_apply_change_refetches_and_writes(self):
        from tapi_overlay import streaming
        from tapi_overlay.client import FetchResult
        from tapi_overlay.endpoints import Catalog

        class FakeClient:
            catalog = Catalog()
            def fetch_member(self, spec, oid):
                return FetchResult(
                    key="x", path="/p", status=200, ok=True,
                    data={spec.list_name: [{"uuid": oid,
                          "name": [{"value-name": "NAME", "value": "refreshed"}]}]},
                )

        with tempfile.TemporaryDirectory() as out:
            ds = DataSource(out)
            rec = streaming.StreamRecord({
                "notification-type": "ATTRIBUTE_VALUE_CHANGE",
                "target-object-type": "CONNECTIVITY_SERVICE",
                "target-object-identifier": "cs-9",
            })
            report = streaming.StreamReport()
            streaming.apply_record(rec, client=FakeClient(), ds=ds, report=report)
            f = os.path.join(out, "entities", "connectivity-service", "cs-9.json")
            self.assertTrue(os.path.exists(f))
            with open(f) as fh:
                self.assertEqual(json.load(fh)["_name"], "refreshed")
            self.assertEqual(report.applied, 1)


class AgentIntegrationTests(unittest.TestCase):
    def test_build_aider_args_oneshot(self):
        from tapi_overlay import agent
        args = agent.build_aider_args(
            model="sonnet", api_key="anthropic=sk-x", ask="how many nodes?",
            yes=True, extra=["--map-tokens", "2048"],
        )
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertIn("--api-key", args)
        # Ask mode is expressed as a /ask message with no auto-commits.
        self.assertIn("--message", args)
        self.assertEqual(args[args.index("--message") + 1], "/ask how many nodes?")
        self.assertIn("--no-auto-commits", args)
        self.assertIn("--yes-always", args)
        self.assertIn("--map-tokens", args)

    def test_build_aider_args_interactive(self):
        from tapi_overlay import agent
        args = agent.build_aider_args(model=None, api_key=None, ask=None, yes=False)
        self.assertNotIn("--message", args)
        self.assertNotIn("--model", args)

    def test_prepare_creates_repo_and_assets(self):
        from tapi_overlay import agent
        with tempfile.TemporaryDirectory() as out:
            # Minimal datasource shape.
            with open(os.path.join(out, "README.md"), "w") as fh:
                fh.write("# ds\n")
            prep = agent.prepare(out, model="sonnet")
            self.assertTrue(prep.git_initialized)
            self.assertTrue(os.path.isdir(os.path.join(out, ".git")))
            for name in ("CONVENTIONS.md", ".aiderignore", ".aider.conf.yml"):
                self.assertTrue(os.path.exists(os.path.join(out, name)), name)
            with open(os.path.join(out, ".aider.conf.yml")) as fh:
                conf = fh.read()
            self.assertIn("model: sonnet", conf)
            self.assertIn("auto-commits: false", conf)
            # Second prepare is idempotent: git already there, no re-init.
            prep2 = agent.prepare(out, model="sonnet")
            self.assertFalse(prep2.git_initialized)

    def test_prepare_missing_datasource_raises(self):
        from tapi_overlay import agent
        with self.assertRaises(FileNotFoundError):
            agent.prepare("/nonexistent/datasource/path", model=None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
