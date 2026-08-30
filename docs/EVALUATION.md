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
