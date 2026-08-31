# Repository Twin Lab

**Status:** controlled mechanics experiment, August 2026. It is not exposed as
an MCP tool and does not modify the production cartographer.

## The missing state

A graph built only after an edit cannot represent a relationship that the edit
deleted. That matters for exactly the class of mistake a post-change review is
supposed to find: removed exports, disconnected callers, superseded RPCs and
contracts whose old consumers still encode assumptions.

The Repository Twin keeps two normalized structural states:

\[
T_0=(F_0,V_0,E_0,A_0)
\]

\[
T_1=(F_1,V_1,E_1,A_1)
\]

where `F` is file state, `V` is extracted symbols, `E` is typed relationships
and `A` is source authority. The signed delta is computed over their union:

\[
\Delta T=(F^+,F^-,F^\sim,V^+,V^-,V^\sim,E^+,E^-,E^\sim)
\]

Deleted nodes and edges remain as tombstone evidence from `T0`. Added evidence
comes from `T1`. Stable relationships from both states let the change propagate
toward callers, tests and shared resources.

This is the concrete version of the “mathematical mesh” idea. It does not assign
an arbitrary number to every character and it is not a second executable copy
of the program. The nodes and edges come from parsers and repository structure;
the numbers only rank unresolved review candidates.

## Pipeline

```text
base repository ──index──> T0 ─┐
                               ├──> signed structural delta
changed repository ─index──> T1 ┘              │
                                               v
                                  union graph with tombstones
                                               │
                                               v
                                  bounded review candidates
```

`experiments/repository_twin.py` implements:

- immutable, content-addressed file/symbol/edge snapshots;
- portable JSON snapshot persistence with digest verification, so `T0` can be
  frozen before the same checkout is edited and reindexed as `T1`;
- added, removed, modified and citation-relocated entities;
- conservative exact-shape rename/move candidates;
- a union graph that records whether evidence comes from `base`, `changed` or
  `both`;
- max-product bounded propagation with an exact strongest path;
- a compact agent-facing packet that labels every result as review evidence.

The observed intervention is the set of files whose content changed. Derived
resolution changes in untouched callers are not silently promoted to seeds.
That avoids leaking the expected ripple into the experiment.

A persisted artifact contains structural identities, citations, signatures,
bounded extractor metadata and content hashes, but not full source bodies. It
still reveals repository structure and must be treated as private unless its
owner explicitly decides otherwise:

```python
from pathlib import Path
from experiments.repository_twin import capture_snapshot, load_snapshot, save_snapshot

t0 = capture_snapshot("my-project")
save_snapshot(t0, Path("/tmp/my-project-t0.json"))

# Edit and refresh the registered repository, then capture T1.
t0 = load_snapshot(Path("/tmp/my-project-t0.json"))
t1 = capture_snapshot("my-project")
```

## Controlled microrepository

The public lab under `benchmarks/repository_twin_lab/` contains five small
cases. Each case has an isolated `base` and `changed` repository. The evaluator
creates temporary Git repositories, indexes each side independently and never
places the expectation manifest inside either repository.

```bash
PYTHONPATH=. .venv/bin/python benchmarks/evaluate_repository_twin.py

# Run only the public evaluation split
PYTHONPATH=. .venv/bin/python benchmarks/evaluate_repository_twin.py \
  --split evaluation
```

The first frozen run produced:

| Metric | Result |
| --- | ---: |
| Signed delta expectation recall | 1.00 |
| Twin impact recall@10 | 1.00 |
| Changed-state-only impact recall@10 | 0.50 |
| Cases where a planned current-only miss was confirmed | 2/2 |

The two differentiating cases are deliberately simple:

1. an exported TypeScript helper is deleted while an unchanged caller still
   imports it;
2. a SQL RPC definition is deleted while an unchanged frontend caller still
   invokes it.

The changed state alone has no resolved path back from the missing definition.
The Twin retains the removed `T0` edge, reaches the stale caller and then its
test. Contract additions and ordinary modifications remain visible in both
representations, as expected.

## What this result proves

It proves a narrow representational property:

> A dual snapshot can mechanically recover removed structural relationships
> that are absent from a graph of the changed repository alone.

It does **not** prove:

- better patches from a coding agent;
- runtime causality;
- general precision on large repositories;
- superiority over compiler errors, tests, `rg` or direct inspection;
- a secret or independent held-out result.

The `evaluation` split is public and frozen for regression testing. It must not
be presented as unseen benchmark evidence.

## Exploratory agent pilot

We also ran one deliberately small agent pilot to test whether the packet can
change a review decision. This is a development observation, not benchmark
evidence.

Two fresh instances of the same frontier coding model received the same small
JavaScript repository and task. The task added a `contract` warning to an
existing planner that already handled overlap and absence warnings. Both first
passes implemented the new behavior and passed the visible tests. Both also
changed the planner's explanation from `Overlap / Absence` to
`Overlap / Absence / Contract` unconditionally, including plans with no
contract warning.

The second-pass conditions were:

- **A:** a generic adversarial review request;
- **B:** the same request plus a 296-word Repository Twin packet generated from
  its frozen `T0` and edited `T1` states.

The packet identified the stable downstream consumers in `diagnostics.js` and
`publication.js` and required the agent to inspect rather than blindly edit
them. It did not state the expected fix or reveal the hidden assertions.

Before either agent ran, we froze two kinds of checks outside their working
trees: new contract behavior and the original planner assertions. After the
review:

| Condition | Frozen tests | Result |
| --- | ---: | --- |
| Generic review | 9/11 | Failed two original-behavior assertions |
| Generic review + Twin packet | 11/11 | Preserved old output and added contract output conditionally |

This is encouraging because the differentiating failures exercised a real
instruction from the task: preserve existing behavior. It is still only
`n=1`, on a synthetic repository, with nondeterministic agents. The packet did
not explicitly point to the conditional label, so the result cannot separate
the value of structural evidence from the value of simply prompting a second
inspection. Repeated randomized runs and frozen historical bugs are required
before making a causal patch-quality claim.

## Why the lab comes before a hard unresolved bug

An unresolved issue has no trustworthy answer key. A convincing story can look
like progress even when the representation is wrong. The microrepository first
tests whether the mechanism observes additions, removals and lost relationships
exactly. The next evidence levels are:

1. frozen historical public bugs with the fix hidden from the agent;
2. the same model/runtime/prompt on base versus base plus Twin packet;
3. hidden tests and human review of correctness, churn, tokens and time;
4. only then, prospective unresolved tasks.

## Current limitations

- Structural extraction currently covers TypeScript/JavaScript, Python and SQL.
- Symbol identity uses path, kind, qualified name and occurrence. Complex
  overload movement can still produce add/remove noise.
- Rename candidates require exact normalized body shape and remain explicitly
  unproven.
- Propagation weights are hand-set navigation weights, not learned causal
  probabilities.
- A candidate means “inspect this file,” never “edit this file.”
- Dynamic dispatch, generated behavior, reflection, configuration conventions
  and runtime dataflow can remain invisible.

## Publication boundary

The public artifact stops at the evidence boundary: signed changes, identity
candidates, union-graph paths, citations and bounded rankings. A human or coding
agent must inspect the cited source and use compilers, tests or runtime evidence
to accept or reject each candidate.

Automatic semantic translation, patch generation, model-specific memory and
agent-runtime integration are deliberately excluded. The next scientific step
is independent reproduction on more frozen repositories and historical bugs,
not another layer that converts uncertain graph evidence into confident prose.

The first executable scheduling step is documented separately in
[`EXECUTABLE_IMPACT_SIMULATION_LAB.md`](EXECUTABLE_IMPACT_SIMULATION_LAB.md).
