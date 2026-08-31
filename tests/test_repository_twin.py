from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import repository
from experiments.repository_twin import (
    capture_snapshot,
    compare_snapshots,
    impact_from_snapshot,
    impact_from_twin,
    load_snapshot,
    render_twin_packet,
    save_snapshot,
)
from project_registry import ProjectRegistry


class RepositoryTwinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        base_files = {
            "src/helper.ts": (
                "export function normalizeOrder(value: string) {\n"
                "  return value.trim();\n"
                "}\n"
            ),
            "src/service.ts": (
                "import {normalizeOrder} from './helper';\n"
                "export function saveOrder(value: string) {\n"
                "  return normalizeOrder(value);\n"
                "}\n"
            ),
            "tests/service.test.ts": (
                "import {saveOrder} from '../src/service';\n"
                "test('normalizes an order', () => expect(saveOrder(' x ')).toBe('x'));\n"
            ),
        }
        changed_files = {
            "src/service.ts": base_files["src/service.ts"],
            "tests/service.test.ts": base_files["tests/service.test.ts"],
        }
        self.base_repo = self._make_repo("base", base_files)
        self.changed_repo = self._make_repo("changed", changed_files)
        self.registry = ProjectRegistry(self.root / "projects.json")
        self.registry.add("twin-base", self.base_repo, "base state")
        self.registry.add("twin-changed", self.changed_repo, "changed state")
        self.database = self.root / "index.sqlite3"
        self.patches = (
            patch.object(repository, "REPOS", self.registry),
            patch.object(repository, "DB_PATH", self.database),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        repository.index_project("twin-base")
        repository.index_project("twin-changed")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_repo(self, name: str, files: dict[str, str]) -> Path:
        root = self.root / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=GBrain Tests",
                "-c",
                "user.email=gbrain@example.invalid",
                "commit",
                "-qm",
                name,
            ],
            check=True,
        )
        return root

    def test_twin_preserves_removed_file_symbol_and_relationship(self) -> None:
        base = capture_snapshot("twin-base")
        changed = capture_snapshot("twin-changed")
        delta = compare_snapshots(base, changed)

        self.assertNotEqual(base.snapshot_digest, changed.snapshot_digest)
        self.assertIn("src/helper.ts", delta.changed_paths)
        self.assertNotIn("src/service.ts", delta.changed_paths)
        self.assertTrue(
            any(
                item.entity == "file"
                and item.change == "removed"
                and item.identity == ("src/helper.ts",)
                for item in delta.file_changes
            )
        )
        self.assertTrue(
            any(
                item.change == "removed"
                and item.before
                and item.before["name"] == "normalizeOrder"
                for item in delta.symbol_changes
            )
        )
        removed_edges = [item for item in delta.edge_changes if item.change == "removed"]
        self.assertTrue(
            any(
                item.before
                and item.before["source_path"] == "src/service.ts"
                and item.before["target_path"] == "src/helper.ts"
                for item in removed_edges
            )
        )

    def test_union_twin_reaches_old_dependents_that_current_state_cannot(self) -> None:
        base = capture_snapshot("twin-base")
        changed = capture_snapshot("twin-changed")
        delta = compare_snapshots(base, changed)
        twin = impact_from_twin(delta, limit=20)
        current_only = impact_from_snapshot(changed, delta.changed_paths, limit=20)

        twin_paths = {item["path"] for item in twin["candidates"]}
        current_paths = {item["path"] for item in current_only["candidates"]}
        self.assertIn("src/service.ts", twin_paths)
        self.assertIn("tests/service.test.ts", twin_paths)
        self.assertNotIn("src/service.ts", current_paths)
        service = next(item for item in twin["candidates"] if item["path"] == "src/service.ts")
        self.assertTrue(
            any(
                step["structural_status"] == "removed"
                and step["evidence_side"] == "base"
                for step in service["strongest_path"]
            )
        )

    def test_snapshot_delta_and_packet_are_deterministic_and_serializable(self) -> None:
        base = capture_snapshot("twin-base")
        artifact = self.root / "t0.json"
        save_snapshot(base, artifact)
        self.assertEqual(load_snapshot(artifact), base)
        first = compare_snapshots(base, capture_snapshot("twin-changed"))
        second = compare_snapshots(
            capture_snapshot("twin-base"),
            capture_snapshot("twin-changed"),
        )
        self.assertEqual(first.as_dict(), second.as_dict())
        impact = impact_from_twin(first)
        packet = render_twin_packet(first, impact)
        self.assertIn("REPOSITORY TWIN STRUCTURAL DELTA", packet)
        self.assertIn("calls[removed:base]", packet)
        self.assertIn("src/service.ts", packet)
        json.dumps({"delta": first.as_dict(), "impact": impact})

        tampered = json.loads(artifact.read_text("utf-8"))
        tampered["files"][0]["content_digest"] = "0" * 64
        artifact.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            load_snapshot(artifact)

    def test_exact_shape_links_a_conservative_rename_candidate(self) -> None:
        renamed_base = self._make_repo(
            "rename-base",
            {"src/math.ts": "export function total(value: number) { return value + 1; }\n"},
        )
        renamed_changed = self._make_repo(
            "rename-changed",
            {"src/math.ts": "export function subtotal(value: number) { return value + 1; }\n"},
        )
        self.registry.add("rename-base", renamed_base)
        self.registry.add("rename-changed", renamed_changed)
        repository.index_project("rename-base")
        repository.index_project("rename-changed")
        delta = compare_snapshots(
            capture_snapshot("rename-base"),
            capture_snapshot("rename-changed"),
        )
        candidate = next(
            item for item in delta.identity_candidates if item.change == "rename_candidate"
        )
        self.assertIn("total", candidate.before_identity[2])
        self.assertIn("subtotal", candidate.after_identity[2])
        self.assertEqual(candidate.confidence, "conservative_exact_shape")

    def test_persisted_t0_survives_reindexing_the_same_checkout_as_t1(self) -> None:
        artifact = self.root / "persisted-t0.json"
        save_snapshot(capture_snapshot("twin-base"), artifact)
        (self.base_repo / "src" / "helper.ts").unlink()
        repository.index_project("twin-base", force=True)

        delta = compare_snapshots(load_snapshot(artifact), capture_snapshot("twin-base"))
        impact = impact_from_twin(delta)
        self.assertEqual(delta.changed_paths, ("src/helper.ts",))
        self.assertIn("src/service.ts", {item["path"] for item in impact["candidates"]})


class RepositoryTwinLabTests(unittest.TestCase):
    def test_public_evaluation_split_is_reproducible(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "benchmarks" / "evaluate_repository_twin.py"),
                "--split",
                "evaluation",
            ],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root)},
            text=True,
            capture_output=True,
            check=True,
        )
        summary = json.loads(result.stdout)["summary"]
        self.assertEqual(summary["cases"], 3)
        self.assertEqual(summary["delta_recall"], 1.0)
        self.assertEqual(summary["twin_impact_recall_at_10"], 1.0)
        self.assertEqual(summary["current_only_impact_recall_at_10"], 0.5)


if __name__ == "__main__":
    unittest.main()
