from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from experiments.impact_simulation import (
    DifferentialOutcome,
    ExecutableScenario,
    ScenarioRun,
    evaluate_schedule,
    select_scenarios,
)


def _run(scenario: ExecutableScenario, *, passed: bool) -> ScenarioRun:
    return ScenarioRun(
        scenario_id=scenario.id,
        path=scenario.path,
        passed=passed,
        returncode=0 if passed else 1,
        timed_out=False,
        stdout_tail="",
        stderr_tail="",
    )


class ImpactSimulationTests(unittest.TestCase):
    def test_scenario_paths_cannot_escape_the_repository(self) -> None:
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            ExecutableScenario.from_dict(
                {"id": "escape", "path": "../outside.test.js", "command": ["node"]}
            )

    def test_impact_schedule_prioritizes_a_scenario_without_reading_its_outcome(self) -> None:
        scenarios = tuple(
            ExecutableScenario(id=name, path=f"test/{name}.test.js", command=("node", name))
            for name in ("accounts", "planner", "reports")
        )
        impact = {
            "candidates": [
                {"path": "test/planner.test.js", "score": 0.42},
                {"path": "src/planner.js", "score": 0.7},
            ]
        }

        selected = select_scenarios(scenarios, budget=1, seed=7, impact=impact)

        self.assertEqual([item.id for item in selected], ["planner"])

    def test_equal_budget_replay_measures_reproducible_introduced_failures(self) -> None:
        scenarios = tuple(
            ExecutableScenario(id=name, path=f"test/{name}.test.js", command=("node", name))
            for name in ("accounts", "planner", "reports")
        )
        outcomes = tuple(
            DifferentialOutcome(
                scenario=item,
                base=_run(item, passed=True),
                changed=_run(item, passed=item.id != "planner"),
            )
            for item in scenarios
        )
        impact = {"candidates": [{"path": "test/planner.test.js", "score": 0.42}]}

        result = evaluate_schedule(outcomes, budget=1, trials=50, impact=impact)

        self.assertEqual(result["detection_rate"], 1.0)
        self.assertEqual(result["introduced_failure_yield"], 1.0)
        self.assertEqual(result["mean_checks_until_detection_or_budget"], 1.0)


class ImpactSimulationLabTests(unittest.TestCase):
    def test_public_campaign_is_reproducible(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "benchmarks" / "evaluate_impact_simulation.py"),
            ],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root)},
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        case = payload["cases"][0]

        self.assertTrue(case["validity"]["matches_frozen_expectation"])
        self.assertEqual(case["schedulers"]["general"]["detection_rate"], 0.351)
        self.assertEqual(case["schedulers"]["current_only"]["detection_rate"], 0.351)
        self.assertEqual(case["schedulers"]["twin_mesh"]["detection_rate"], 1.0)
        self.assertEqual(
            case["schedulers"]["twin_mesh"]["first_schedule"][0],
            "planner-normalization",
        )


if __name__ == "__main__":
    unittest.main()
