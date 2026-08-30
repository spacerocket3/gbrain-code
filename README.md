# GBrain Code

Selective, diff-aware repository cartography for coding agents.

GBrain Code answers one narrow question:

> What code is related to this engineering task even when the relevant files do
> not use the same words?

It does **not** answer the engineering question, edit files, run an agent loop,
or replace direct inspection and tests. It returns a versioned evidence map that
an agent can verify with normal repository tools.

```text
task
  -> question-scoped repository map
  -> agent opens decisive files
  -> agent edits and tests
  -> diff-aware ripple audit
  -> agent reviews omitted consumers, tests and duplicate candidates
```

## Why

Model weights contain broad programming knowledge. They do not contain the
current, private relationships unique to a changing repository: a React query
calling an RPC patched by a later migration, a second consumer of the same
table, or a test reached only through an imported callback.

Text search remains excellent for exact strings. GBrain Code complements it by
following explicit structural edges and shared resources across files and
layers.

## Public surface

The MCP server intentionally exposes only five tools:

- `gbrain_status`: verify snapshot freshness.
- `map_code_context`: retrieve anchors and expand their structural neighborhood.
- `inspect_symbol`: inspect definitions, callers, callees and SQL lineage.
- `audit_code_change`: map ripple candidates around the current Git diff.
- `refresh_repository`: update the local structural/text index.

There are no model consultants, answer generators, chat memories, or autonomous
editing tools.

## Current structural coverage

- TypeScript and JavaScript: modules, imports, definitions, calls, inheritance,
  overrides, Supabase RPC/table access (including small local wrappers such as
  `callRpc(name, args)`) and edge-function invocation.
- Python: modules, imports, functions, classes, methods, calls and inheritance
  when statically resolvable with `ast`.
- SQL: definitions, table access, function calls and ordered migration lineage.
- Other textual languages: bounded lexical and optional semantic retrieval;
  structural extraction remains future work.

Every map identifies its Git commit, working-tree generation and unresolved
edges. An index that does not match the registered working tree fails closed.
Repeated call sites are grouped by relationship and returned with line lists so
the map spends its context budget on distinct evidence instead of duplication.

## Install from source

Requirements: Python 3.11+, Git, Node.js and npm.

```bash
git clone https://github.com/spacerocket3/gbrain-code
cd gbrain-code
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm ci
```

Optional semantic retrieval:

```bash
.venv/bin/pip install -e '.[semantic]'
```

The default `fast` mode is lexical search plus structural graph traversal and
never starts a model. Embeddings and code reranking are explicit experimental
options through `auto` or `code`.

## Register and index a repository

Registration is an explicit local authorization boundary. The MCP server cannot
register arbitrary paths.

```bash
.venv/bin/gbrain-code project add my-repo /absolute/path/to/my-repo
.venv/bin/gbrain-code index my-repo

# Optional semantic index
.venv/bin/gbrain-code embed my-repo
```

Runtime state is ignored by Git and defaults to:

- registry: `data/projects.json`
- SQLite evidence index: `data/index.sqlite3`
- model cache: `~/.cache/gbrain-code/models`

Override these with `GBRAIN_PROJECTS_FILE`, `GBRAIN_DB` and
`GBRAIN_MODEL_CACHE`.

## Query locally

```bash
.venv/bin/gbrain-code map my-repo \
  "change reservation retries without breaking duplicate protection"

.venv/bin/gbrain-code inspect my-repo update_reservation

# After editing, refresh before auditing the working-tree diff
.venv/bin/gbrain-code index my-repo --force
.venv/bin/gbrain-code audit my-repo --question \
  "change reservation retries without breaking duplicate protection"
```

## MCP registration

```bash
codex mcp add gbrain-code -- \
  /absolute/path/to/gbrain-code/.venv/bin/python \
  /absolute/path/to/gbrain-code/mcp_server.py
```

GBrain Code is intentionally opt-in. A repository or agent policy should decide
when a task is large enough to justify cartography.

## Evidence contract

- A graph edge means the extractor observed a static relationship.
- An unresolved edge is retained and labelled, not silently promoted.
- A ripple candidate means “inspect this,” not “this is broken.”
- A same-name symbol is not proof of duplicate code.
- `active=0` means a repeated SQL definition was superseded by a later migration.
- Direct source inspection, Git history and executable tests remain authoritative.

## Research and evaluation

- [Position paper](docs/WHITEPAPER.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Prior art and boundaries](docs/PRIOR_ART.md)
- [Evaluation protocol](docs/EVALUATION.md)

The repository includes a small file-retrieval evaluator. It is not presented as
a scientific result; it exists so claims can be tested instead of repeated.

## Status

Experimental alpha. The core behavior is tested, but no claim of superiority
over repository maps, embeddings, or agent exploration is made until controlled
evaluation is published.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
