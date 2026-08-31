# Reactive Repository State experiment

**Status:** research branch, August 2026. The implementation is not exposed as
an MCP tool and the name is provisional.

## Question

A repository map can retrieve relationships that an agent might otherwise find
after several searches. If it ultimately returns the same source text, its main
benefit may be lower search cost rather than greater reasoning capacity.

This experiment asks a narrower question:

> Can an observed edit be propagated through the repository's typed and
> temporal relationships to rank likely ripple files better than lexical search
> or bounded graph traversal?

The experiment does not claim that a repository is a quantum system. A
probability distribution over candidate impacts is a useful mathematical object;
"superposition" is only an analogy for unresolved alternatives.

## State and update

Let the indexed repository state be:

\[
R_t=(V_t,E_t,A_t,C_t)
\]

where:

- \(V_t\) contains files and virtual shared-resource nodes;
- \(E_t\) contains typed imports, calls, inheritance, RPCs and table access;
- \(A_t\) records authority and active/superseded SQL definitions;
- \(C_t\) records extractor resolution confidence.

An observed change \(\Delta\) contains seed files, diff line ranges and exact
terms from the change signal. It changes the transition weights used for the
query, not the persistent source index.

The current implementation performs personalized PageRank:

\[
x_{k+1}=\alpha x_0+(1-\alpha)P(R_t,\Delta)^\top x_k
\]

with restart \(\alpha=0.28\). The resulting score is an **impact candidate
distribution**, not a probability that a file is broken or must be edited.

## Representation variants

The experiment keeps the propagation algorithm fixed and changes the graph:

1. `topology`: resolved file edges with equal relation weights;
2. `typed`: relation and resolution-confidence weights;
3. `resources`: typed edges plus virtual RPC/table/edge-function resources;
4. `temporal`: resources plus source authority and active/superseded SQL weight.

Every candidate includes its strongest bounded evidence path. Repeated call
sites receive a logarithmic rather than linear boost. The diff's old-line hunks
boost outgoing relationships that actually touch the changed region.

The code lives under `experiments/` and deliberately remains outside the five
public MCP operations.

## Rejected local-slice side experiment

This section records a bounded TypeScript value-flow experiment retained for
reproducibility. Its results did not justify treating semantic translation as a
general GBrain layer, and it is not part of the Repository Twin publication
claim or proposed product surface.

File ranking alone leaves the model responsible for rediscovering why a
candidate matters. The experimental `impact_obligations` layer therefore
composes three bounded representations:

1. a broad relational distribution that retains difficult navigation evidence;
2. a stricter delta core that blocks dependency edges not touched by the seed
   diff;
3. a best-effort, file-local TypeScript forward slice from the observed call to
   a supported review sink such as a rendered JSX value.

For example, the translator can preserve a path shaped like:

```text
changed helper
-> caller result
-> derived set
-> returned diagnostic field
-> aggregate count
-> rendered value and adjacent explanatory fields
```

The result is an unresolved **review obligation**, containing exact files,
lines, expressions, sink context, an invariant to verify and a focused review
question. It never labels the path a regression. The packet distinguishes
translated obligations, untranslated delta-core candidates and an ambient
relational halo. Halo paths remain available for secondary navigation but are
not presented as delta-supported impact. `render_impact_packet` produces this
bounded form for a subsequent model pass, while `impact_field_for_diff` starts
the experiment from the current Git diff.

The local slice is deliberately limited. Its TypeScript binding resolution is
lexical and file-local; it is not a type checker, complete program-dependence
graph or runtime simulator.

### Visible retrospective translation pilot

The translator was built after inspecting one TankFlow failure case, so the
result below is a development observation and is exposed to overfitting.

The original post-diff graph audit ranked `ShiftPlan.tsx`, but a frontier agent
rejected it after an 88,142-token audit. The new packet exposed this exact
observed chain in under one thousand text tokens:

```text
contractWarnings
-> contractIds
-> shiftDiagnostics.contractIds
-> plannerStats.conflictCount
-> PlannerStatCard.value
detail: "Überschneidung / Abwesenheit"
```

A fresh agent using the same model accepted the inconsistency, added the missing
contract-rule category to the label, rejected an unrelated payroll-form path,
and used 22,766 model tokens. The historical target commit contained the same
semantic correction with different wording.

This demonstrates that the translator can preserve a decisive path on its
development example. It does **not** demonstrate held-out patch improvement,
lower total cost, general causal precision or benchmark leadership.

### Pre-development-history diagnostic

To check whether the visible pilot had merely shaped an over-specialized
translator, the history evaluator was run on ten TankFlow commits preceding the
development example. One modified production file from each parent snapshot was
the seed and the remaining pre-existing modified files were noisy co-change
labels. At ten returned candidates:

| Representation | Recall@10 | MRR |
| --- | ---: | ---: |
| Lexical retrieval | 0.510 | 0.298 |
| Question-scoped map | 0.576 | 0.407 |
| Reactive file distribution | 0.513 | 0.406 |
| Strict delta core | 0.470 | 0.402 |
| Lexical + graph fusion | 0.510 | 0.425 |

The strict core traded recall for stronger delta evidence. The rendered packet
therefore preserves the broad ordering and annotates, rather than automatically
promoting, the core and halo. The local TypeScript translator emitted six
candidate-file obligations across two of ten samples, none of which was also
present in the historical commit. Seventy-five of 81 core candidates had no
supported local sink. Co-change is not causal ground truth, so this diagnostic
does not establish precision.

An earlier version incorrectly treated unchanged Git hunk context as part of
the delta and promoted `WeeklyTimeline.tsx` through the shared
`getEmployeeColor` helper after an `EmployeePanel.tsx` layout edit. Direct diff
inspection showed that relationship was not causal. The evaluator now requests
zero-context hunks, every transition records the file owning its evidence line,
and post-diff propagation blocks untouched dependency edges at the seed. The
development example still reaches its direct consumers under this stricter
rule.

This is a useful negative result: the JSX value-flow slice is not the Impact
Field itself and did not support a general translation strategy. Naively
promoting translated or strict-core paths produced inconsistent ranking changes
across the two history cohorts and was rejected. On this small pre-development
cohort the question-scoped map had higher recall. The code remains as an
auditable failed branch of the research, not as the next required layer.

## Initial results

### Public ARB subset

The reproducible external pilot uses the frozen base snapshots from Agent
Retrieval Bench `v2_edit2ripple`, restricted to the 14 samples in three
repositories for which GBrain currently has structural extractors: Vite,
Transformers and Diffusers.

At five returned files:

| Method | Recall@5 | MRR |
| --- | ---: | ---: |
| Lexical | 0.310 | 0.131 |
| Current GBrain question map | 0.381 | 0.167 |
| Reactive propagation | 0.095 | 0.029 |
| Fixed lexical/reactive fusion | 0.310 | 0.185 |

The fusion weight was explored while these samples were visible, so its MRR is
diagnostic and must not be treated as held-out evidence.

Nine samples had no lexical gold in the top five. Five of those had at least one
gold file reachable in the extracted graph. On that five-sample relational
opportunity stratum, both the current map and reactive propagation recovered one
sample (Recall@5 0.20); lexical and fixed fusion recovered none.

Adding exact diff ranges did not improve the ARB aggregate. The current
representation lacks many benchmark relationships involving fixtures,
manifests, test conventions and newly coordinated files. This is a negative
result for the present propagator, not a reason to discard the benchmark.

### Private retrospective cross-layer pilot

A second diagnostic used 12 multi-file commits from TankFlow ending at revision
`c8c539d`. Each repository graph was rebuilt from the commit parent, then one
existing production file was used as the anchor and the other modified,
pre-existing files as noisy co-change gold. Zero-context Git hunks provide the
exact old-line delta ranges. This avoids future-code leakage but does **not**
make co-change equivalent to required impact.

At five returned files:

| Method | Recall@5 | MRR |
| --- | ---: | ---: |
| Lexical | 0.304 | 0.378 |
| Current two-hop graph | 0.401 | 0.558 |
| Topology PageRank | 0.531 | 0.833 |
| Typed PageRank | 0.545 | 0.833 |
| + shared resources | 0.545 | 0.917 |
| + temporal/authority state | 0.545 | 0.917 |

In five cases lexical retrieval found no gold in its first five results while a
gold file was graph-reachable. The temporal variant reached Recall@5 0.617 and
MRR 0.800 on that stratum. On nine cross-layer samples it reached Recall@5 0.634
and MRR 1.000, versus lexical 0.183 and 0.281. Here, cross-layer means a coarse
boundary such as application-to-test or application-to-database; `.ts` and
`.tsx` alone are deliberately treated as the same application layer.

This signal is strong but not independently reproducible because the repository
is private, the sample count is small and co-change labels are noisy.

## Interpretation

The first experiments support four limited statements:

- iterative propagation can extract more useful ordering from GBrain's graph
  than its current bounded expansion on one relation-heavy repository;
- shared-resource and temporal/authority state can change that ordering;
- the effect is not universal: the current GBrain map substantially beats the
  reactive propagator on the public ARB subset;
- a fixed fusion rule is wrong when one evidence channel has no useful signal.

They do not establish a new scientific category, benchmark leadership, improved
patch correctness or a physical/quantum interpretation.

## Falsification and next tests

The hypothesis should be rejected or narrowed if the effect disappears when:

1. evaluated on larger public relation-heavy datasets;
2. compared against unweighted PageRank, shortest paths, RepoMap and learned
   impact-analysis baselines;
3. labels are manually audited for causal necessity rather than co-change;
4. relation weights are selected on one repository and frozen on unseen repos;
5. final agents receive equal context budgets and hidden tests judge patches.

Before product integration, the graph needs explicit config/manifest, test-to-
fixture and convention edges, a held-out routing policy for lexical versus
relational evidence, and a public temporal-migration benchmark. Until then,
GBrain's normal cartography remains the production path.
