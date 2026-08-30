# GBrain Code: temporal and diff-aware repository cartography for coding agents

**Status:** position paper and experimental system description, August 2026.
This is not a peer-reviewed performance claim.

## Abstract

Coding models possess broad knowledge of programming but encounter each private,
changing repository as external evidence. They can infer local architecture by
searching and reading, yet repository-specific relationships are often scattered
across files, languages, database migrations, tests and consumers. Missing one
relationship can produce a plausible patch followed by regression repair,
duplicated abstractions or unnecessary code growth.

GBrain Code explores a narrow intervention: construct a current, question-scoped
map of repository relationships before editing and a diff-scoped ripple map after
editing. The map is delivered as cited evidence to an otherwise normal coding
agent. The system does not generate answers or patches. Its purpose is to improve
context selection while preserving direct inspection and executable verification.

## 1. Problem

A model may know common software patterns while not knowing that, in one specific
repository:

- a frontend hook calls an RPC whose effective body was replaced by a later SQL
  migration;
- a second screen reads the same table but never imports the first screen;
- a test reaches a function through an alias or virtual dispatch;
- a new helper duplicates an existing abstraction under different vocabulary;
- a cache invalidation contract lives outside the edited module.

Exact search locates known strings. The harder problem is discovering which
strings, symbols and resources should have been searched for in the first place.

## 2. Thesis

The proposed division of labor is:

```text
GBrain Code  -> what is structurally related and worth opening?
rg/search    -> where does an exact identifier or behavior appear?
source read  -> what does the current implementation actually do?
tests        -> does the resulting system satisfy executable behavior?
```

Repository cartography should not replace exploration. It should reduce the
probability that exploration starts from an incomplete local map.

## 3. Design

### 3.1 Current working-tree snapshot

The index represents tracked plus untracked, non-ignored source files. A
generation is bound to the Git commit, a content digest and an extractor
fingerprint. Consequential queries fail closed if the working tree has changed
since indexing.

### 3.2 Multiple evidence channels

Candidate anchors may come from exact text search and optional embeddings. Graph
expansion then follows explicit relationships such as imports, calls, database
resources, RPCs, inheritance and tests. Search similarity is an anchor; graph
edges provide the relationship evidence.

### 3.3 Temporal SQL lineage

Applied migration repositories frequently contain several definitions of the
same database object. The newest ordered definition is marked active while older
definitions remain visible as lineage. This avoids treating every historical
body as simultaneously effective.

### 3.4 Pre-change map

`map_code_context` returns:

- compact source anchors;
- definitions overlapping those anchors;
- structurally related files and provenance for every expansion;
- shared resource consumers;
- SQL lineage;
- unresolved edges and explicit limitations.

### 3.5 Post-change map

`audit_code_change` starts from the current Git diff and reports:

- changed symbols;
- callers, callees and shared-resource consumers outside the diff;
- related tests outside the diff;
- same-name abstraction candidates;
- gaps that deserve review.

These are prompts for inspection, not automated bug verdicts.

## 4. What is and is not new

Repository maps, code graphs, iterative retrieval, dataflow-guided retrieval and
selective RAG are established research directions. GBrain Code does not claim to
invent them.

The experimental contribution is their product framing as a model-independent,
evidence-only loop around an arbitrary coding agent:

```text
selective map before edit -> normal agent work -> diff-aware map after edit
```

The strongest differentiators to evaluate are temporal migration lineage,
cross-layer shared-resource fan-out and post-diff ripple auditing. Novelty must be
earned through controlled comparison, not asserted from architecture alone.

## 5. Why selective use matters

Retrieval can distract as well as help. RepoFormer showed that repository context
does not improve every completion and motivated selective retrieval. GBrain Code
therefore remains opt-in, bounded and capable of operating without a heavy
semantic model.

The expected high-value tasks are cross-file changes, schema evolution,
permissions, caching, concurrency, compatibility and regression review. A local
one-line edit may not justify any cartography.

## 6. Falsifiable hypotheses

Compared with lexical search or an agent's default exploration, the combined
pre/post map should improve:

1. recall of files needed for the next coding step;
2. recall of related tests and consumers;
3. detection of active versus superseded database definitions;
4. detection of ripple files omitted from a patch;
5. context yield under fixed token budgets;
6. final patch correctness without increasing unnecessary file churn.

It may fail by anchoring on the wrong concept, following high-degree resources,
missing dynamic dispatch, over-reporting duplicate candidates, or supplying
context that distracts the agent. Those outcomes must be measured.

## 7. Evaluation

The primary external target is Agent Retrieval Bench because it evaluates the
files an agent needs next. Its `edit2ripple`, `trace2code`, `code2test` and
selective no-gold subsets closely match the cartography objective.

Retrieval metrics are necessary but insufficient. A second agentic evaluation
must compare test-passing patches, regressions, token use, elapsed time and file
churn under identical model/runtime conditions.

Required baselines:

- lexical search;
- BM25;
- embedding retrieval;
- Aider-style RepoMap;
- normal coding-agent exploration;
- GBrain map only;
- GBrain pre-map plus post-diff audit.

## 8. Safety and privacy

Repositories are registered explicitly and indexed locally. Secrets, private
keys, dependencies, build outputs, binaries and oversized files are excluded.
The MCP server cannot authorize arbitrary filesystem paths. A future hosted
version would require a separate threat model and is outside this prototype.

## 9. Conclusion

The model does not need another agent pretending to know the repository. It may
benefit from a better cartographer that shows where evidence is connected,
admits uncertainty and gets out of the way before implementation begins.
