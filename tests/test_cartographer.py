from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cartographer
import mcp_server
import repository
from benchmarks.evaluate_git_history_ripple import architectural_layer, diff_ranges
from experiments.reactive_repository_state import (
    Transition,
    _candidate_anchors,
    _normalised,
    _typescript_local_flows,
    hybrid_ripple,
    impact_distribution,
    impact_field_for_diff,
    impact_obligations,
    lexical_ripple,
    mapped_graph_ripple,
    reachable_file_distances,
    reciprocal_rank_fusion,
    render_impact_packet,
    static_graph_ripple,
)
from project_registry import ProjectRegistry


class CartographerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for directory in ("src", "tests", "migrations", "pythonpkg"):
            (self.repo / directory).mkdir()
        (self.repo / "src" / "api.ts").write_text(
            "const callRpc = (client, name, args) => client.rpc(name as never, args);\n"
            "const fromDynamic = (client, table) => client.from(table as never);\n"
            "export async function saveOrder(client, order) {\n"
            "  await callRpc(client, 'save_order', {order});\n"
            "  return fromDynamic(client, 'orders').update(order);\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "list.ts").write_text(
            "export function listOrders(client) {\n"
            "  return client.from('orders').select('*');\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "rules.ts").write_text(
            "export function buildWarnings(rows) {\n"
            "  return rows.filter(Boolean);\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "planner.tsx").write_text(
            "import {useMemo} from 'react';\n"
            "import {buildWarnings} from './rules';\n"
            "export function Planner({rows}) {\n"
            "  const diagnostics = useMemo(() => {\n"
            "    const warnings = buildWarnings(rows);\n"
            "    const contractIds = new Set(warnings.map((row) => row.id));\n"
            "    return {contractIds};\n"
            "  }, [rows]);\n"
            "  const stats = useMemo(() => ({\n"
            "    conflictCount: diagnostics.contractIds.size,\n"
            "  }), [diagnostics]);\n"
            "  return <Stat value={stats.conflictCount} detail=\"Overlap / Absence\" />;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "colors.ts").write_text(
            "export function getColor(id) { return id ? 'blue' : 'gray'; }\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "panel.tsx").write_text(
            "import {getColor} from './colors';\n"
            "export function Panel({id}) {\n"
            "  const color = getColor(id);\n"
            "  return <div className={`wide ${color}`} />;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "timeline.tsx").write_text(
            "import {getColor} from './colors';\n"
            "export function Timeline({id}) {\n"
            "  const color = getColor(id);\n"
            "  return <Stat value={color} detail=\"Employee color\" />;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "src" / "mapped.tsx").write_text(
            "export function Mapped({rows}) {\n"
            "  const mapped = rows.map((row) => ({id: row.id}));\n"
            "  return <Stat value={mapped.id} />;\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repo / "tests" / "api.test.ts").write_text(
            "import {saveOrder} from '../src/api';\n"
            "test('saves order', () => saveOrder(client, order));\n",
            encoding="utf-8",
        )
        (self.repo / "migrations" / "001_orders.sql").write_text(
            "create table public.orders(id uuid primary key);\n"
            "create function public.save_order(order jsonb) returns void as $$\n"
            "begin insert into public.orders values ((order->>'id')::uuid); end;\n"
            "$$ language plpgsql;\n",
            encoding="utf-8",
        )
        (self.repo / "migrations" / "002_orders.sql").write_text(
            "create or replace function public.save_order(order jsonb) returns void as $$\n"
            "begin update public.orders set id=id; end;\n"
            "$$ language plpgsql;\n",
            encoding="utf-8",
        )
        (self.repo / "pythonpkg" / "helper.py").write_text(
            "def normalize_order(value):\n    return value.strip()\n",
            encoding="utf-8",
        )
        (self.repo / "pythonpkg" / "service.py").write_text(
            "from pythonpkg.helper import normalize_order\n\n"
            "def process_order(value):\n    return normalize_order(value)\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=GBrain Tests",
                "-c",
                "user.email=gbrain@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.registry = ProjectRegistry(self.root / "projects.json")
        self.registry.add("sample", self.repo, "test fixture")
        self.database = self.root / "index.sqlite3"
        self.patches = (
            patch.object(repository, "REPOS", self.registry),
            patch.object(repository, "DB_PATH", self.database),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        repository.index_project("sample")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_indexes_typescript_python_and_sql_lineage(self) -> None:
        db = repository.connect()
        python_call = db.execute(
            """SELECT target_path,resolution_confidence FROM edges
               WHERE project='sample' AND source_path='pythonpkg/service.py'
                 AND relation='calls' AND target_name='normalize_order'"""
        ).fetchone()
        self.assertEqual(python_call["target_path"], "pythonpkg/helper.py")
        definitions = db.execute(
            """SELECT path,active FROM symbols WHERE project='sample'
               AND kind='function' AND name='public.save_order' ORDER BY path"""
        ).fetchall()
        self.assertEqual(
            [(row["path"], row["active"]) for row in definitions],
            [
                ("migrations/001_orders.sql", 0),
                ("migrations/002_orders.sql", 1),
            ],
        )
        wrapped_rpc = db.execute(
            """SELECT target_name,resolution_confidence FROM edges
               WHERE project='sample' AND source_path='src/api.ts'
                 AND relation='calls_rpc'"""
        ).fetchone()
        self.assertEqual(
            dict(wrapped_rpc),
            {
                "target_name": "public.save_order",
                "resolution_confidence": "active_sql",
            },
        )
        wrapped_table = db.execute(
            """SELECT target_name,resolution_confidence FROM edges
               WHERE project='sample' AND source_path='src/api.ts'
                 AND relation='writes_table'"""
        ).fetchone()
        self.assertEqual(
            dict(wrapped_table),
            {
                "target_name": "public.orders",
                "resolution_confidence": "literal_via_wrapper",
            },
        )

    def test_map_finds_shared_resource_consumers_and_tests(self) -> None:
        result = cartographer.map_code_context(
            "sample",
            "change save_order persistence and its regression tests",
            16,
            "fast",
        )
        paths = {item["path"] for item in result["map"]["files"]}
        self.assertIn("src/api.ts", paths)
        self.assertIn("src/list.ts", paths)
        self.assertIn("tests/api.test.ts", paths)
        self.assertTrue(result["map"]["sql_lineage"]["save_order"])
        self.assertEqual(result["retrieval"]["mode"], "lexical+graph")

    def test_inspect_symbol_marks_active_and_superseded_sql(self) -> None:
        result = cartographer.inspect_symbol("sample", "save_order")
        active = {item["path"]: item["active"] for item in result["definitions"]}
        self.assertEqual(active["migrations/001_orders.sql"], 0)
        self.assertEqual(active["migrations/002_orders.sql"], 1)
        self.assertTrue(
            any(
                item["source_path"] == "src/api.ts" and item["relation"] == "calls_rpc"
                for item in result["relationships"]
            )
        )

    def test_audit_reports_ripple_outside_diff(self) -> None:
        path = self.repo / "src" / "api.ts"
        path.write_text(
            path.read_text("utf-8").replace("return fromDynamic", "return await fromDynamic"),
            encoding="utf-8",
        )
        repository.index_project("sample", force=True)
        result = cartographer.audit_code_change("sample")
        ripple = {item["path"] for item in result["review_map"]["ripple_candidates_not_in_diff"]}
        self.assertEqual(result["diff"]["changed_files"], ["src/api.ts"])
        self.assertIn("tests/api.test.ts", ripple)
        self.assertIn("src/list.ts", ripple)

    def test_map_fails_closed_when_index_is_stale(self) -> None:
        (self.repo / "src" / "api.ts").write_text("export const changed = true;\n")
        with self.assertRaisesRegex(RuntimeError, "refresh_repository"):
            cartographer.map_code_context("sample", "save order")

    def test_mcp_surface_contains_only_cartography_operations(self) -> None:
        names = [tool["name"] for tool in mcp_server.TOOLS]
        self.assertEqual(
            names,
            [
                "gbrain_status",
                "map_code_context",
                "inspect_symbol",
                "audit_code_change",
                "refresh_repository",
            ],
        )
        self.assertFalse(any("nemotron" in name or "gemini" in name for name in names))
        result = mcp_server.call_tool(
            "map_code_context",
            {"project": "sample", "question": "save order persistence"},
        )
        self.assertEqual(result["retrieval"]["mode"], "lexical+graph")

    def test_map_is_json_serializable(self) -> None:
        payload = cartographer.map_code_context("sample", "save_order and orders", 12, "fast")
        self.assertIn('"relationships"', json.dumps(payload))

    def test_reactive_state_propagates_typed_temporal_impact(self) -> None:
        result = impact_distribution("sample", ["src/api.ts"], limit=20)
        candidates = {item["path"]: item for item in result["candidates"]}
        self.assertAlmostEqual(result["state"]["probability_mass"], 1.0, places=9)
        self.assertIn("src/list.ts", candidates)
        self.assertIn("tests/api.test.ts", candidates)
        self.assertIn("migrations/002_orders.sql", candidates)
        self.assertIn("migrations/001_orders.sql", candidates)
        self.assertGreater(
            candidates["migrations/002_orders.sql"]["score"],
            candidates["migrations/001_orders.sql"]["score"],
        )
        self.assertEqual(candidates["migrations/001_orders.sql"]["temporal_activity"], 0.8)
        self.assertTrue(candidates["src/list.ts"]["strongest_path"])
        self.assertTrue(
            any(
                step["evidence_target"] == "public.orders"
                for step in candidates["src/list.ts"]["strongest_path"]
            )
        )
        self.assertTrue(
            all(step["evidence_path"] for step in candidates["src/list.ts"]["strongest_path"])
        )

    def test_reactive_state_is_deterministic_and_kept_out_of_mcp(self) -> None:
        first = impact_distribution("sample", ["src/api.ts"], limit=20)
        second = impact_distribution("sample", ["src/api.ts"], limit=20)
        self.assertEqual(first, second)
        self.assertNotIn("reactive", {tool["name"] for tool in mcp_server.TOOLS})

    def test_reactive_graph_ablation_variants_and_reachability(self) -> None:
        for variant in ("topology", "typed", "resources", "temporal"):
            result = impact_distribution(
                "sample", ["src/api.ts"], limit=10, graph_variant=variant
            )
            self.assertEqual(result["algorithm"]["graph_variant"], variant)
        reachable = reachable_file_distances("sample", ["src/api.ts"], max_hops=5)
        self.assertIn("src/list.ts", reachable)
        self.assertIn("tests/api.test.ts", reachable)
        with self.assertRaisesRegex(ValueError, "variant must be"):
            impact_distribution("sample", ["src/api.ts"], graph_variant="quantum")

    def test_reactive_state_accepts_diff_scoped_seed_ranges(self) -> None:
        result = impact_distribution(
            "sample",
            ["src/api.ts"],
            limit=10,
            seed_ranges={"src/api.ts": [(3, 5)]},
        )
        self.assertEqual(result["algorithm"]["delta_ranges_used"], 1)
        path = next(
            item["strongest_path"]
            for item in result["candidates"]
            if item["path"] == "src/list.ts"
        )
        self.assertTrue(any(step["lines"] for step in path))

    def test_strict_delta_edges_block_untouched_dependency_fanout(self) -> None:
        transition = Transition(
            source="src/changed.tsx",
            target="src/shared.ts",
            weight=1.0,
            relation="calls",
            direction="toward_dependency",
            evidence_target="sharedHelper",
            evidence_path="src/changed.tsx",
            lines=(20,),
        )
        graph = {"src/changed.tsx": [transition]}
        loose = _normalised(
            graph,
            seeds={"src/changed.tsx"},
            seed_ranges={"src/changed.tsx": [(4, 6)]},
        )
        strict = _normalised(
            graph,
            seeds={"src/changed.tsx"},
            seed_ranges={"src/changed.tsx": [(4, 6)]},
            strict_delta_edges=True,
        )
        self.assertIn("src/changed.tsx", loose)
        self.assertNotIn("src/changed.tsx", strict)

    def test_impact_field_translates_a_ranked_file_into_a_local_value_path(self) -> None:
        result = impact_obligations(
            "sample",
            ["src/rules.ts"],
            limit=10,
            delta_text="change buildWarnings contract conflict semantics",
        )
        obligation = next(
            item
            for item in result["impact_field"]["obligations"]
            if item["candidate_path"] == "src/planner.tsx"
            and item["local_value_path"][-1]["label"] == "Stat.value"
        )
        labels = [item["label"] for item in obligation["local_value_path"]]
        self.assertEqual(
            labels,
            [
                "warnings",
                "contractIds",
                "diagnostics.contractIds",
                "stats.conflictCount",
                "Stat.value",
            ],
        )
        self.assertEqual(obligation["sink_context"]["detail"], "Overlap / Absence")
        self.assertFalse(obligation["proven_contradiction"])
        self.assertTrue(result["limits"]["obligations_require_source_or_test_verification"])
        packet = render_impact_packet(result, max_obligations=1)
        self.assertIn("warnings <- buildWarnings(rows)", packet)
        self.assertIn("stats.conflictCount", packet)
        self.assertIn("detail: Overlap / Absence", packet)
        self.assertIn("not proven regressions", packet)

        broad = impact_obligations("sample", ["src/api.ts"], limit=10)
        broad_packet = render_impact_packet(broad, max_obligations=1, max_untranslated=2)
        self.assertIn("BROAD RANKING (preserved)", broad_packet)
        self.assertIn("UNTRANSLATED RIPPLE CANDIDATES", broad_packet)
        self.assertIn("inspect selectively", broad_packet.casefold())

    def test_impact_field_only_anchors_lines_observed_in_the_candidate(self) -> None:
        candidate = {
            "path": "src/dependency.tsx",
            "strongest_path": [
                {
                    "source": "src/caller.tsx",
                    "target": "src/dependency.tsx",
                    "relation": "calls",
                    "direction": "toward_dependency",
                    "evidence_target": "renderValue",
                    "evidence_path": "src/caller.tsx",
                    "lines": [12],
                }
            ],
        }
        self.assertEqual(_candidate_anchors(candidate), [])

    def test_local_slice_does_not_promote_arbitrary_callback_objects(self) -> None:
        result = _typescript_local_flows(
            self.repo,
            [
                {
                    "path": "src/mapped.tsx",
                    "anchors": [{"line": 2, "target": "map", "relation": "calls"}],
                }
            ],
        )
        labels = result["files"]["src/mapped.tsx"]["flows"][0]["chain"]
        self.assertEqual([item["label"] for item in labels], ["mapped", "Stat.value"])

    def test_impact_field_can_start_from_the_current_diff(self) -> None:
        path = self.repo / "src" / "rules.ts"
        path.write_text(
            path.read_text("utf-8").replace("filter(Boolean)", "filter((row) => Boolean(row))"),
            encoding="utf-8",
        )
        repository.index_project("sample", force=True)
        result = impact_field_for_diff(
            "sample",
            question="change buildWarnings contract conflict semantics",
        )
        self.assertEqual(result["delta"]["seed_paths"], ["src/rules.ts"])
        self.assertTrue(
            any(
                item["candidate_path"] == "src/planner.tsx"
                for item in result["impact_field"]["obligations"]
            )
        )

    def test_impact_field_keeps_untouched_shared_dependencies_in_the_halo(self) -> None:
        path = self.repo / "src" / "panel.tsx"
        path.write_text(
            path.read_text("utf-8").replace("wide", "compact"),
            encoding="utf-8",
        )
        repository.index_project("sample", force=True)
        result = impact_field_for_diff("sample", question="make the panel compact")
        ranked = {
            item["path"]: item for item in result["impact_field"]["ranked_candidates"]
        }
        self.assertIn("src/timeline.tsx", ranked)
        self.assertFalse(ranked["src/timeline.tsx"]["in_delta_core"])
        self.assertIn(
            "src/timeline.tsx",
            {
                item["path"]
                for item in result["impact_field"]["ambient_relational_halo"]
            },
        )
        self.assertNotIn(
            "src/timeline.tsx",
            {item["candidate_path"] for item in result["impact_field"]["obligations"]},
        )
        packet = render_impact_packet(result, max_obligations=1, max_untranslated=1)
        self.assertIn("AMBIENT RELATIONAL HALO", packet)

    def test_ripple_baselines_return_ranked_paths(self) -> None:
        lexical = lexical_ripple("sample", ["src/api.ts"], 10)
        static = static_graph_ripple("sample", ["src/api.ts"], 10)
        reactive = [
            item["path"]
            for item in impact_distribution("sample", ["src/api.ts"], limit=10)["candidates"]
        ]
        mapped = mapped_graph_ripple(
            "sample", ["src/api.ts"], "save_order persistence", 10
        )
        hybrid = hybrid_ripple("sample", ["src/api.ts"], "save_order persistence", 10)
        self.assertNotIn("src/api.ts", lexical)
        self.assertIn("tests/api.test.ts", static)
        self.assertTrue(mapped)
        self.assertNotIn("src/api.ts", mapped)
        self.assertIn("src/list.ts", reactive)
        self.assertTrue(hybrid)

    def test_rank_fusion_is_deterministic_and_validates_weights(self) -> None:
        self.assertEqual(
            reciprocal_rank_fusion(
                [["a", "b"], ["b", "c"]], weights=[1.0, 0.5], limit=3
            ),
            ["b", "a", "c"],
        )
        with self.assertRaisesRegex(ValueError, "same length"):
            reciprocal_rank_fusion([["a"]], weights=[1.0, 0.5])

    def test_history_layers_are_architectural_not_extension_based(self) -> None:
        self.assertEqual(architectural_layer("src/page.ts"), "application")
        self.assertEqual(architectural_layer("src/page.tsx"), "application")
        self.assertEqual(architectural_layer("src/page.test.tsx"), "test")
        self.assertEqual(architectural_layer("migrations/001.sql"), "database")

    def test_history_diff_ranges_use_only_reported_hunk_lines(self) -> None:
        patch = "@@ -57,1 +57,1 @@\n-old\n+new\n@@ -71,2 +71,2 @@\n-a\n-b\n+c\n+d\n"
        self.assertEqual(diff_ranges("src/view.tsx", patch), {"src/view.tsx": [(57, 57), (71, 72)]})


if __name__ == "__main__":
    unittest.main()
