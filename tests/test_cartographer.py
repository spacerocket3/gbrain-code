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


if __name__ == "__main__":
    unittest.main()
