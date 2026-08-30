# Architecture

## Data flow

```text
explicit repository registry
          |
          v
working-tree snapshot -- Git commit + content digest + extractor fingerprint
          |
          +--> bounded text chunks --> SQLite FTS5
          |
          +--> TS/JS compiler -----> symbols + edges
          +--> Python ast ----------> symbols + edges
          +--> SQL extractor -------> symbols + edges + ordered lineage
          |
          +--> optional embeddings (separate, incremental)

question --> anchor retrieval --> bounded graph expansion --> evidence map
Git diff -------------------------> bounded graph expansion --> ripple audit
```

## Storage

SQLite stores:

- repository generations;
- chunks and FTS index;
- source authority;
- symbols;
- directed edges with resolution confidence;
- optional embedding vectors.

Unchanged chunks retain identifiers so optional embeddings remain incremental.
The registry and index live in GBrain's own state directory rather than in each
target repository. Generated state is Git-ignored and excluded by the file
walker, so even indexing GBrain itself cannot recursively ingest its SQLite
index or model cache.

## Evidence classes

- `code`: current executable source.
- `test`: executable behavior evidence.
- `schema_history`: ordered migrations and database definitions.
- `config`: executable manifests and build configuration.
- `generated`: useful for contracts but lower authority.
- documentation is indexed as source metadata but does not dominate code maps.

## Ranking

First-stage anchors use FTS and, when explicitly prepared, semantic embeddings.
The code-aware reranker is optional and bounded. Graph expansion uses relation
weights, depth and source authority. Shared database resources are expanded
across read/write relation variants so a writer can discover readers without a
direct import. High-degree resources receive a fan-out penalty; a ubiquitous
table should not crowd out a specific RPC. Question terms gate expansion from
large migration files, and repeated call sites are grouped into one relation
with multiple cited lines.

The TypeScript pass recognizes direct Supabase calls and deliberately small
same-file wrappers whose literal argument can be propagated to `.rpc()` or
`.from()`. This is bounded alias resolution, not general interprocedural
analysis.

## Snapshot invariant

Every map and audit verifies:

```text
indexed commit == current commit
indexed content digest == current indexable bytes
indexed extractor fingerprint == current extractor
```

If any comparison fails, the operation refuses to return a map.

## Deliberate exclusions

- no answer generation;
- no local or remote chat model;
- no autonomous loop;
- no editing;
- no conversation memory;
- no claim that static edges capture runtime behavior;
- no automatic refresh hidden inside a read-only query.
