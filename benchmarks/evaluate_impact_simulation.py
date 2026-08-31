#!/usr/bin/env python3
"""Compare general and graph-directed executable simulation schedules."""

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
from experiments.impact_simulation import (
    ExecutableScenario,
    compare_schedulers,
    execute_differential_scenarios,
)
from experiments.repository_twin import (
    capture_snapshot,
    compare_snapshots,
    impact_from_snapshot,
    impact_from_twin,
)
from project_registry import ProjectRegistry

ROOT = Path(__file__).resolve().parent / "impact_simulation_lab"
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
            "user.name=GBrain Simulation Lab",
            "-c",
            "user.email=gbrain-lab@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )


def evaluate_case(
    case: dict[str, Any],
    fixture_root: Path,
    *,
    budget: int,
    trials: int,
) -> dict[str, Any]:
    scenarios = tuple(ExecutableScenario.from_dict(item) for item in case["scenarios"])
    with tempfile.TemporaryDirectory(prefix=f"gbrain-simulation-{case['id']}-") as temporary:
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
            twin_impact = impact_from_twin(delta, limit=100)
            current_impact = impact_from_snapshot(changed, delta.changed_paths, limit=100)

        outcomes = execute_differential_scenarios(base_root, changed_root, scenarios)
        introduced = sorted(item.scenario.id for item in outcomes if item.introduced_failure)
        base_failures = sorted(item.scenario.id for item in outcomes if not item.base.passed)
        expected = sorted(str(item) for item in case.get("expected_introduced_failures", ()))
        return {
            "id": case["id"],
            "scenario_count": len(scenarios),
            "budget": budget,
            "trials": trials,
            "validity": {
                "base_failures": base_failures,
                "introduced_failures": introduced,
                "expected_introduced_failures": expected,
                "matches_frozen_expectation": not base_failures and introduced == expected,
            },
            "delta": {
                "changed_paths": list(delta.changed_paths),
                "uses_removed_edges": twin_impact["algorithm"]["uses_removed_edges"],
            },
            "ranked_paths": {
                "current_only": [item["path"] for item in current_impact["candidates"]],
                "twin_mesh": [item["path"] for item in twin_impact["candidates"]],
            },
            "schedulers": compare_schedulers(
                outcomes,
                budget=budget,
                trials=trials,
                current_impact=current_impact,
                twin_impact=twin_impact,
            ),
            "outcomes": [item.as_dict() for item in outcomes],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--budget", type=int)
    parser.add_argument("--trials", type=int)
    parser.add_argument("--details", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text("utf-8"))
    budget = args.budget if args.budget is not None else int(payload["budget"])
    trials = args.trials if args.trials is not None else int(payload["trials"])
    results = [
        evaluate_case(item, args.manifest.parent, budget=budget, trials=trials)
        for item in payload["cases"]
    ]
    if not all(item["validity"]["matches_frozen_expectation"] for item in results):
        raise SystemExit("Executable fixture does not match its frozen expectation")
    output = {
        "protocol": "executable-impact-simulation-v1",
        "budget": budget,
        "trials": trials,
        "cases": results,
        "claims": {
            "all_failures_are_executable_t1_regressions": True,
            "same_scenario_budget_per_scheduler": True,
            "proves_gpu_or_model_improvement": False,
            "public_fixture_is_not_held_out": True,
        },
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.details:
        args.details.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
