#!/usr/bin/env python3
"""Evaluate dual-snapshot mechanics on the controlled Repository Twin lab."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import repository
from experiments.repository_twin import (
    EntityChange,
    IdentityCandidate,
    capture_snapshot,
    compare_snapshots,
    impact_from_snapshot,
    impact_from_twin,
)
from project_registry import ProjectRegistry

ROOT = Path(__file__).resolve().parent / "repository_twin_lab"
DEFAULT_MANIFEST = ROOT / "manifest.json"


def _initialize_repo(source: Path, destination: Path, message: str) -> None:
    shutil.copytree(source, destination)
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=GBrain Lab",
            "-c",
            "user.email=gbrain-lab@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )


def _change_view(change: EntityChange) -> dict[str, Any]:
    result: dict[str, Any] = {"change": change.change, "sign": change.sign}
    if change.before:
        result.update(change.before)
    if change.after:
        result.update(change.after)
    return result


def _identity_view(candidate: IdentityCandidate) -> dict[str, Any]:
    return {
        "change": candidate.change,
        "before_name": candidate.before_identity[2],
        "after_name": candidate.after_identity[2],
        "confidence": candidate.confidence,
    }


def _matches(observed: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(observed.get(key) == value for key, value in expected.items())


def _score_expectations(
    expected: list[dict[str, Any]],
    observed: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for expectation in expected:
        match = next((item for item in observed if _matches(item, expectation)), None)
        rows.append({"expected": expectation, "matched": match is not None, "observed": match})
    matched = sum(1 for item in rows if item["matched"])
    return {"expected": len(rows), "matched": matched, "details": rows}


def _path_recall(expected: list[str], ranked: list[str]) -> dict[str, Any]:
    hits = [path for path in expected if path in ranked]
    return {
        "expected": len(expected),
        "matched": len(hits),
        "recall": round(len(hits) / len(expected), 4) if expected else 1.0,
        "hits": hits,
        "misses": [path for path in expected if path not in ranked],
    }


def evaluate_case(case: dict[str, Any], fixture_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"gbrain-twin-{case['id']}-") as temporary:
        root = Path(temporary)
        fixture = (fixture_root / case["fixture"]).resolve()
        if fixture_root.resolve() not in fixture.parents:
            raise ValueError(f"Fixture escapes lab root: {fixture}")
        base_root = root / "base"
        changed_root = root / "changed"
        _initialize_repo(fixture / "base", base_root, f"{case['id']} T0")
        _initialize_repo(fixture / "changed", changed_root, f"{case['id']} T1")

        registry = ProjectRegistry(root / "projects.json")
        registry.add("base", base_root, f"{case['id']} base")
        registry.add("changed", changed_root, f"{case['id']} changed")
        with ExitStack() as stack:
            stack.enter_context(patch.object(repository, "REPOS", registry))
            stack.enter_context(patch.object(repository, "DB_PATH", root / "index.sqlite3"))
            repository.index_project("base")
            repository.index_project("changed")
            base = capture_snapshot("base")
            changed = capture_snapshot("changed")
            delta = compare_snapshots(base, changed)
            twin = impact_from_twin(delta, limit=10)
            current = impact_from_snapshot(changed, delta.changed_paths, limit=10)

        expectations = case.get("expectations", {})
        categories = {
            "file_changes": _score_expectations(
                expectations.get("file_changes", []),
                [_change_view(item) for item in delta.file_changes],
            ),
            "symbol_changes": _score_expectations(
                expectations.get("symbol_changes", []),
                [_change_view(item) for item in delta.symbol_changes],
            ),
            "edge_changes": _score_expectations(
                expectations.get("edge_changes", []),
                [_change_view(item) for item in delta.edge_changes],
            ),
            "identity_candidates": _score_expectations(
                expectations.get("identity_candidates", []),
                [_identity_view(item) for item in delta.identity_candidates],
            ),
        }
        twin_paths = [item["path"] for item in twin["candidates"]]
        current_paths = [item["path"] for item in current["candidates"]]
        impact = _path_recall(expectations.get("impact_paths", []), twin_paths)
        current_impact = _path_recall(expectations.get("impact_paths", []), current_paths)
        expected_current_misses = expectations.get("current_only_miss_paths", [])
        current_misses = [path for path in expected_current_misses if path not in current_paths]
        category_expected = sum(item["expected"] for item in categories.values())
        category_matched = sum(item["matched"] for item in categories.values())
        return {
            "id": case["id"],
            "split": case["split"],
            "snapshots": {
                "base": base.as_dict(include_entities=False),
                "changed": changed.as_dict(include_entities=False),
            },
            "delta_digest": delta.delta_digest,
            "changed_paths": list(delta.changed_paths),
            "delta": {
                "expected": category_expected,
                "matched": category_matched,
                "recall": (
                    round(category_matched / category_expected, 4)
                    if category_expected
                    else 1.0
                ),
                "categories": categories,
            },
            "impact": impact,
            "current_only_impact": current_impact,
            "current_only_miss_check": {
                "expected": expected_current_misses,
                "confirmed": current_misses,
                "passed": len(current_misses) == len(expected_current_misses),
            },
            "rankings": {"twin": twin_paths, "current_only": current_paths},
            "limits": {
                "fixture_mechanics_are_not_agent_patch_quality": True,
                "evaluation_split_is_public_and_not_secret": True,
            },
        }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    delta_expected = sum(item["delta"]["expected"] for item in results)
    delta_matched = sum(item["delta"]["matched"] for item in results)
    impact_expected = sum(item["impact"]["expected"] for item in results)
    impact_matched = sum(item["impact"]["matched"] for item in results)
    current_matched = sum(item["current_only_impact"]["matched"] for item in results)
    return {
        "cases": len(results),
        "delta_recall": round(delta_matched / delta_expected, 4) if delta_expected else 1.0,
        "twin_impact_recall_at_10": (
            round(impact_matched / impact_expected, 4) if impact_expected else 1.0
        ),
        "current_only_impact_recall_at_10": (
            round(current_matched / impact_expected, 4) if impact_expected else 1.0
        ),
        "confirmed_current_only_miss_cases": sum(
            bool(item["current_only_miss_check"]["expected"])
            and item["current_only_miss_check"]["passed"]
            for item in results
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--split", choices=("development", "evaluation"))
    parser.add_argument("--details", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text("utf-8"))
    selected = [
        item
        for item in payload["cases"]
        if (not args.cases or item["id"] in args.cases)
        and (not args.split or item["split"] == args.split)
    ]
    if not selected:
        raise SystemExit("No Repository Twin cases matched the requested filters")
    results = [evaluate_case(item, args.manifest.parent) for item in selected]
    output = {
        "protocol": "repository-twin-lab-v1",
        "summary": summarize(results),
        "cases": results,
        "claims": {
            "proves_dual_snapshot_mechanics": True,
            "proves_agent_improvement": False,
            "proves_runtime_causality": False,
        },
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.details:
        args.details.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
