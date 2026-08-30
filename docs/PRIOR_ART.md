# Prior art and design boundaries

GBrain Code is built from known ideas. This document records the closest work so
future descriptions do not overstate novelty.

## Aider repository map

Aider builds a concise map of repository symbols and dependencies, ranks graph
nodes and fits the result into a token budget. It is the closest mature example
of a repository cartographer embedded in a coding workflow.

- Documentation: https://aider.chat/docs/repomap.html
- Source: https://github.com/Aider-AI/aider
- License: Apache-2.0

GBrain Code differs experimentally by emphasizing database migration lineage and
a post-diff ripple audit. No Aider source code is copied here.

## RepoCoder

RepoCoder studies iterative retrieval and generation for repository-level code
completion, demonstrating that relevant context is often scattered across files.

- Paper: https://arxiv.org/abs/2303.12570
- Source: https://github.com/microsoft/CodeT/tree/main/RepoCoder

GBrain Code is generation-independent and targets engineering task context rather
than fill-in-the-middle completion.

## RepoFormer

RepoFormer studies selective retrieval and whether repository context is useful
for a particular completion. Its central lesson is that always retrieving can be
costly or harmful.

- Paper: https://arxiv.org/abs/2403.10059
- Source: https://github.com/amazon-science/Repoformer
- License: Apache-2.0

GBrain Code adopts the design principle of selective use. No RepoFormer source
code or model weights are included.

## DraCo

DraCo uses dataflow to retrieve cross-file context and demonstrates the limits of
plain text/import similarity for repository-level completion.

- Paper: https://arxiv.org/abs/2405.19782

GBrain currently records static calls/imports/resources, not a full interprocedural
dataflow graph.

## RepoGraph

RepoGraph represents repositories as code graphs and retrieves structural
subgraphs to improve software-engineering agents.

- Paper: https://arxiv.org/abs/2410.14684
- Source: https://github.com/ozyyshr/RepoGraph
- License: Apache-2.0

GBrain's graph extractor descends from the earlier private Project Brain
prototype owned by this repository's author. No RepoGraph implementation is
copied.

## Agent Retrieval Bench

Agent Retrieval Bench evaluates context acquisition using workflow-defined gold
files. Its `edit2ripple` task is especially aligned with post-diff cartography.

- Repository: https://github.com/eyuansu62/agent-retrieval-bench
- Dataset: https://huggingface.co/datasets/eyuansu71/agent_retrieval_bench
- Paper: https://arxiv.org/abs/2607.24882
- Code license: MIT; dataset terms are documented separately upstream.

The benchmark is not vendored here. The local evaluation adapter accepts a
normalized JSONL representation so downloaded releases remain separately
licensed artifacts.

## Provenance of this implementation

The initial indexing, TS/JS extraction, SQL lineage and optional embedding code
were extracted and cleaned from Nicolas Cenci's private Project Brain prototype.
The public MCP surface, Python extractor, evidence contract and diff audit were
created for GBrain Code. External projects informed design and evaluation only.
