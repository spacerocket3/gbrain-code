# Executable Impact Simulation Lab

**Status:** public scheduling-mechanics experiment, August 2026. It does not
call a model, use a GPU or modify the production MCP surface.

## Question

Given the same proposed change, frozen executable scenarios and execution
budget, can the Repository Twin impact mesh prioritize a newly introduced
failure better than a general schedule?

The experiment separates three responsibilities:

```text
T0 and T1 snapshots -> impact ranking -> scenario scheduling -> executable truth
```

The graph never declares a defect. A scenario counts only when it passes on
`T0` and fails on `T1`.

## Frozen case

`benchmarks/impact_simulation_lab/` contains one small public JavaScript case.
`T0` has a planner connected to a contract normalizer. `T1` deletes the
normalizer while leaving its caller and test unchanged. Five unrelated tests
remain green and the planner scenario becomes the single reproducible
regression.

The manifest declares six executable scenarios. Each scenario is run once on
both snapshots to freeze its differential outcome. The evaluator then replays
2,000 deterministic schedule orderings; it does not execute 2,000 fresh
programs or pretend those orderings are new simulations.

Three schedulers receive the same budget of two scenarios:

1. **General:** seeded uniform ordering, with no repository ranking.
2. **Current-only:** the same ordering, promoted by paths reachable from `T1`.
3. **Twin mesh:** the same ordering, promoted by paths reachable through the
   union of `T0` and `T1`, including tombstone edges.

Neither directed scheduler can inspect scenario outcomes while ranking.

```bash
PYTHONPATH=. .venv/bin/python benchmarks/evaluate_impact_simulation.py
```

## First frozen result

| Scheduler | Detection rate | Mean checks to detection or budget | Regression yield per check |
| --- | ---: | ---: | ---: |
| General | 0.351 | 1.82 | 0.1755 |
| Current-only | 0.351 | 1.82 | 0.1755 |
| Twin mesh | 1.000 | 1.00 | 0.5000 |

The current-only graph reached no candidate from the deleted path. The Twin
retained the removed dependency, reached `src/planner.js`, then reached
`test/planner.test.js`. Consequently the failing planner scenario was first in
every Twin schedule. The general rate is close to the one-third probability of
choosing the one failing scenario in two draws from six.

## What this result proves

It proves another narrow mechanics property:

> A removed structural relationship can be used to spend a fixed executable
> check budget on a relevant regression scenario that a current-only graph can
> no longer reach.

This is stronger than returning a plausible file: the selected scenario
actually passes before the change and fails after it.

## What it does not prove

- The fixture is synthetic, public and designed around a removed relationship.
- The general scheduler is uniform random, not a compiler, test-impact tool or
  frontier coding agent.
- Only six unique scenarios are executed against each snapshot. The 2,000
  trials replay scheduling orders over their frozen outcomes.
- No model generates new scenarios and no accelerator is required.
- A graph-directed generator could produce low-quality tests even if it chooses
  the right subsystem.
- One case cannot establish general accuracy, runtime savings or novelty over
  test prioritization and change-impact-analysis literature.

## Publication boundary

This artifact ends at equal-budget scheduling over frozen executable scenarios.
Model-generated tests, accelerator experiments and agent loops are outside its
scope. The next necessary test is replication on more frozen cases—preferably
historical public bugs—with the same rule that only executable before/after
differences count as detected regressions.
