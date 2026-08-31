"""Executable simulation scheduling for a Repository Twin impact field.

This module does not generate tests or claim that a graph edge is a defect. It
executes a frozen scenario corpus against T0 and T1, identifies reproducible
regressions, and measures whether an impact ranking finds them sooner than an
equal-budget general schedule.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _safe_relative_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Scenario path must stay inside the repository: {value!r}")
    return path.as_posix()


@dataclass(frozen=True)
class ExecutableScenario:
    id: str
    path: str
    command: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutableScenario:
        scenario_id = str(payload.get("id", "")).strip()
        if not scenario_id:
            raise ValueError("Scenario id cannot be empty")
        command = tuple(str(part) for part in payload.get("command", ()))
        if not command or any(not part for part in command):
            raise ValueError(f"Scenario {scenario_id!r} must define a non-empty command")
        return cls(
            id=scenario_id,
            path=_safe_relative_path(str(payload.get("path", ""))),
            command=command,
        )


@dataclass(frozen=True)
class ScenarioRun:
    scenario_id: str
    path: str
    passed: bool
    returncode: int | None
    timed_out: bool
    stdout_tail: str
    stderr_tail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "path": self.path,
            "passed": self.passed,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


@dataclass(frozen=True)
class DifferentialOutcome:
    scenario: ExecutableScenario
    base: ScenarioRun
    changed: ScenarioRun

    @property
    def introduced_failure(self) -> bool:
        return self.base.passed and not self.changed.passed

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": {
                "id": self.scenario.id,
                "path": self.scenario.path,
                "command": list(self.scenario.command),
            },
            "base": self.base.as_dict(),
            "changed": self.changed.as_dict(),
            "introduced_failure": self.introduced_failure,
        }


def _tail(value: str | bytes | None, limit: int = 2000) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return (value or "")[-limit:]


def execute_scenario(
    repository_root: Path,
    scenario: ExecutableScenario,
    *,
    timeout_seconds: float = 10.0,
) -> ScenarioRun:
    """Execute one trusted benchmark scenario with bounded captured output."""
    root = repository_root.resolve()
    focus = (root / scenario.path).resolve()
    if root != focus and root not in focus.parents:
        raise ValueError(f"Scenario escapes repository root: {scenario.path}")
    if not focus.is_file():
        raise ValueError(f"Scenario focus path does not exist: {scenario.path}")
    try:
        result = subprocess.run(
            list(scenario.command),
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ScenarioRun(
            scenario_id=scenario.id,
            path=scenario.path,
            passed=False,
            returncode=None,
            timed_out=True,
            stdout_tail=_tail(exc.stdout or ""),
            stderr_tail=_tail(exc.stderr or ""),
        )
    return ScenarioRun(
        scenario_id=scenario.id,
        path=scenario.path,
        passed=result.returncode == 0,
        returncode=result.returncode,
        timed_out=False,
        stdout_tail=_tail(result.stdout),
        stderr_tail=_tail(result.stderr),
    )


def execute_differential_scenarios(
    base_root: Path,
    changed_root: Path,
    scenarios: tuple[ExecutableScenario, ...],
    *,
    timeout_seconds: float = 10.0,
) -> tuple[DifferentialOutcome, ...]:
    """Run every scenario once on T0 and T1 to freeze executable truth."""
    if len({item.id for item in scenarios}) != len(scenarios):
        raise ValueError("Scenario ids must be unique")
    return tuple(
        DifferentialOutcome(
            scenario=scenario,
            base=execute_scenario(base_root, scenario, timeout_seconds=timeout_seconds),
            changed=execute_scenario(changed_root, scenario, timeout_seconds=timeout_seconds),
        )
        for scenario in scenarios
    )


def _seeded_key(seed: int, scenario_id: str) -> str:
    return hashlib.sha256(f"{seed}:{scenario_id}".encode()).hexdigest()


def _impact_scores(impact: dict[str, Any] | None) -> dict[str, float]:
    if impact is None:
        return {}
    return {str(item["path"]): float(item["score"]) for item in impact["candidates"]}


def select_scenarios(
    scenarios: tuple[ExecutableScenario, ...],
    *,
    budget: int,
    seed: int,
    impact: dict[str, Any] | None = None,
) -> tuple[ExecutableScenario, ...]:
    """Select an equal-budget schedule without consulting scenario outcomes."""
    if budget < 1 or budget > len(scenarios):
        raise ValueError("budget must be between one and the scenario count")
    scores = _impact_scores(impact)
    ranked = sorted(
        scenarios,
        key=lambda item: (-scores.get(item.path, 0.0), _seeded_key(seed, item.id)),
    )
    return tuple(ranked[:budget])


def evaluate_schedule(
    outcomes: tuple[DifferentialOutcome, ...],
    *,
    budget: int,
    trials: int,
    impact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay schedules over frozen outcomes without re-running the programs."""
    if trials < 1:
        raise ValueError("trials must be at least one")
    scenarios = tuple(item.scenario for item in outcomes)
    by_id = {item.scenario.id: item for item in outcomes}
    detections = 0
    checks_spent = 0
    introduced_selected = 0
    selection_counts = {item.id: 0 for item in scenarios}
    first_schedule: list[str] = []
    for seed in range(trials):
        selected = select_scenarios(scenarios, budget=budget, seed=seed, impact=impact)
        if seed == 0:
            first_schedule = [item.id for item in selected]
        found_at: int | None = None
        for position, scenario in enumerate(selected, start=1):
            selection_counts[scenario.id] += 1
            if by_id[scenario.id].introduced_failure:
                introduced_selected += 1
                if found_at is None:
                    found_at = position
        if found_at is not None:
            detections += 1
            checks_spent += found_at
        else:
            checks_spent += budget
    total_checks = trials * budget
    return {
        "budget": budget,
        "trials": trials,
        "detection_rate": round(detections / trials, 6),
        "mean_checks_until_detection_or_budget": round(checks_spent / trials, 6),
        "introduced_failure_yield": round(introduced_selected / total_checks, 6),
        "first_schedule": first_schedule,
        "selection_counts": selection_counts,
    }


def compare_schedulers(
    outcomes: tuple[DifferentialOutcome, ...],
    *,
    budget: int,
    trials: int,
    current_impact: dict[str, Any],
    twin_impact: dict[str, Any],
) -> dict[str, Any]:
    """Compare general, current-only and Twin-directed equal-budget schedules."""
    return {
        "general": evaluate_schedule(outcomes, budget=budget, trials=trials),
        "current_only": evaluate_schedule(
            outcomes,
            budget=budget,
            trials=trials,
            impact=current_impact,
        ),
        "twin_mesh": evaluate_schedule(
            outcomes,
            budget=budget,
            trials=trials,
            impact=twin_impact,
        ),
    }
