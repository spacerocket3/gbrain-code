# Evaluation protocol

## Claims policy

The current tests prove mechanics, not superiority. Do not publish “better than
rg,” “reduces hallucinations,” or “improves patch quality” without controlled
results.

## Retrieval evaluation

Use frozen repository snapshots and JSONL rows shaped as:

```json
{"id":"sample-1","project":"repo-snapshot","question":"...","gold_paths":["src/a.ts"]}
```

Run the included evaluator:

```bash
python benchmarks/evaluate.py samples.jsonl --method lexical --k 20
python benchmarks/evaluate.py samples.jsonl --method map --k 20
```

Reported metrics:

- Recall@k;
- mean reciprocal rank;
- exact all-gold recall;
- returned-file count.

For Agent Retrieval Bench, download the releases using its own CLI and transform
each row into the normalized shape while registering the corresponding frozen
checkout. Keep ARB corpora outside this Git repository.

## Agentic A/B evaluation

Use the same model, runtime, prompt, repository snapshot, timeout and permissions.
Compare:

1. normal agent exploration;
2. normal agent plus pre-change map;
3. normal agent plus pre-change map and post-diff audit.

Record:

- test outcome and hidden regression tests;
- files opened and changed;
- unnecessary file churn;
- direct-search/tool calls;
- input/output tokens;
- wall-clock time;
- human review findings;
- whether map candidates were actually decisive.

Randomize run order and isolate working trees. A map that finds more files but
reduces patch correctness is a failure, not an improvement.

### Local-slice side experiment

The repository retains an early TypeScript value-flow slice so its negative
result remains reproducible. It must not be presented as a general semantic
translator or as part of the Repository Twin claim. Any future revisit must
hold file localization constant, pre-register its context budget and judge the
result through hidden tests and source-based human review.

## Target workflows

- `edit2ripple`: other files affected by an anchored change;
- `trace2code`: implementation behind a failure trace;
- `code2test`: tests relevant to a change;
- active/superseded migration definitions;
- frontend/backend/database/cache relationships;
- duplicate-abstraction review after a diff;
- no-gold/abstention tasks.

## Current evidence

The included fixture tests demonstrate that the prototype can:

- connect TypeScript callers to RPCs and tables;
- preserve literal RPC/table names through small local wrappers;
- connect Python imports and calls;
- distinguish repeated SQL definitions by migration order;
- find read consumers from a changed write consumer through a shared table;
- find a related test outside the diff;
- fail closed on a stale working-tree index.

These are unit-level capabilities, not benchmark results.

## Reactive impact experiment

`experiments/reactive_repository_state.py` compares four representations while
holding personalized PageRank constant:

- unweighted topology;
- typed relations;
- typed relations plus shared resources;
- shared resources plus temporal/authority state.

It accepts exact old-line diff ranges so an edit delta can focus outgoing
relationships. It remains research code and is not an MCP operation.

Use the normalized ripple evaluator for already registered snapshots:

```bash
PYTHONPATH=. python benchmarks/evaluate_ripple.py ripple.jsonl \
  --method ppr-topology --k 5
PYTHONPATH=. python benchmarks/evaluate_ripple.py ripple.jsonl \
  --method reactive --k 5
```

Normalized rows contain `project`, `seed_paths`, optional `question` and
`gold_paths`.

### Agent Retrieval Bench adapter

Keep ARB data outside this repository. After downloading its
`v2_edit2ripple` release, run:

```bash
PYTHONPATH=. python benchmarks/evaluate_arb_edit2ripple.py \
  /external/arb/benchmark/v2_edit2ripple/samples.jsonl \
  /external/arb/corpus/v2_edit2ripple \
  --repo vitejs/vite \
  --repo huggingface/transformers \
  --repo huggingface/diffusers \
  --k 5 \
  --details /tmp/gbrain-arb-details.json
```

The adapter reconstructs every repository from its frozen base-commit file
rows, so fixed code cannot leak into the index.

### Retrospective history diagnostic

For a local Git repository:

```bash
PYTHONPATH=. python benchmarks/evaluate_git_history_ripple.py /path/to/repo \
  --max-commits 60 --max-samples 12 --k 5 \
  --details /tmp/gbrain-history-details.json
```

This rebuilds each graph from the commit parent and compares lexical, bounded
graph, question map, PageRank ablations and fusion. Co-change is noisy evidence,
so these results are diagnostics rather than causal impact ground truth.

Both adapters report these strata separately:

- lexical misses;
- structurally reachable gold;
- relational opportunity: lexical miss plus graph reachability;
- cross-layer commits in the history diagnostic, using coarse application,
  test, database and Python layers rather than file-extension differences.

Do not report only a favorable stratum. Publish the aggregate and every stratum
together to avoid moving the evaluation target after seeing results.

## Dual-snapshot Repository Twin mechanics

The controlled lab isolates a prerequisite that current-state retrieval cannot
measure: whether relationships removed by an edit remain available to a
post-change audit.

```bash
PYTHONPATH=. python benchmarks/evaluate_repository_twin.py
PYTHONPATH=. python benchmarks/evaluate_repository_twin.py --split evaluation
```

It scores exact signed-delta expectations and impact-path recall for:

1. the union of independently indexed `T0` and `T1` snapshots;
2. a changed-state-only control using the same propagation algorithm.

These fixtures prove mechanics only. Before making an agent-quality claim,
freeze historical public bugs, hide their fixes from the agent, randomize the
condition order and score hidden tests, human review, diff churn, tokens and
wall time as specified above. The public `evaluation` split is a reproducible
regression set, not secret held-out evidence.
