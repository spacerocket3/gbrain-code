"""Dual-snapshot repository twin experiment.

The production cartographer indexes one repository state at a time.  This
module keeps two normalized structural snapshots, computes a signed delta over
their union, and propagates that delta across relationships that existed before
or after the change.

The result is review evidence.  It is not an executable simulation and it does
not prove that a candidate file is broken.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cartographer
import repository

SymbolIdentity = tuple[str, str, str, int]
EdgeIdentity = tuple[str, str, str, str, str, str]


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json(raw: str) -> str:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        value = raw or ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_slice(repo: Path, path: str, start_line: int, end_line: int) -> str:
    try:
        lines = (repo / path).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, start_line - 1)
    end = min(len(lines), max(start_line, end_line))
    return "\n".join(lines[start:end])


def _shape_digest(source: str, symbol_name: str) -> str:
    """Hash a symbol body after erasing its declared name and whitespace.

    Exact shape equality is useful rename evidence.  It deliberately does not
    attempt fuzzy matching, because a plausible rename is not proof of symbol
    identity.
    """
    if not source.strip():
        return ""
    without_name = re.sub(rf"\b{re.escape(symbol_name)}\b", "<symbol>", source)
    normalized = re.sub(r"\s+", " ", without_name).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


@dataclass(frozen=True)
class FileState:
    path: str
    authority: str
    reason: str
    content_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "authority": self.authority,
            "reason": self.reason,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True)
class SymbolState:
    identity: SymbolIdentity
    path: str
    kind: str
    name: str
    qualified_name: str
    occurrence: int
    start_line: int
    end_line: int
    signature: str
    active: bool
    metadata: str
    body_digest: str
    shape_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": list(self.identity),
            "path": self.path,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "occurrence": self.occurrence,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "active": self.active,
            "metadata": json.loads(self.metadata),
            "body_digest": self.body_digest,
            "shape_digest": self.shape_digest,
        }

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "active": self.active,
            "metadata": self.metadata,
            "body_digest": self.body_digest,
        }


@dataclass(frozen=True)
class EdgeState:
    identity: EdgeIdentity
    source_path: str
    source_symbol: str
    relation: str
    target_name: str
    target_path: str
    target_symbol: str
    lines: tuple[int, ...]
    resolutions: tuple[str, ...]
    metadata: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": list(self.identity),
            "source_path": self.source_path,
            "source_symbol": self.source_symbol,
            "relation": self.relation,
            "target_name": self.target_name,
            "target_path": self.target_path or None,
            "target_symbol": self.target_symbol or None,
            "lines": list(self.lines),
            "resolutions": list(self.resolutions),
            "metadata": [json.loads(item) for item in self.metadata],
        }

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "resolutions": self.resolutions,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class RepositorySnapshot:
    project: str
    commit: str
    generation_id: str
    extractor_version: str
    graph_digest: str
    snapshot_digest: str
    files: tuple[FileState, ...]
    symbols: tuple[SymbolState, ...]
    edges: tuple[EdgeState, ...]

    def as_dict(self, *, include_entities: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format_version": 1,
            "project": self.project,
            "commit": self.commit,
            "generation_id": self.generation_id,
            "extractor_version": self.extractor_version,
            "graph_digest": self.graph_digest,
            "snapshot_digest": self.snapshot_digest,
            "counts": {
                "files": len(self.files),
                "symbols": len(self.symbols),
                "edges": len(self.edges),
            },
        }
        if include_entities:
            payload.update(
                {
                    "files": [item.as_dict() for item in self.files],
                    "symbols": [item.as_dict() for item in self.symbols],
                    "edges": [item.as_dict() for item in self.edges],
                }
            )
        return payload


def _snapshot_state_payload(
    files: tuple[FileState, ...],
    symbols: tuple[SymbolState, ...],
    edges: tuple[EdgeState, ...],
) -> dict[str, Any]:
    return {
        "files": [item.as_dict() for item in files],
        "symbols": [item.as_dict() for item in symbols],
        "edges": [item.as_dict() for item in edges],
    }


@dataclass(frozen=True)
class EntityChange:
    entity: str
    change: str
    sign: int
    identity: tuple[Any, ...]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    changed_fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "change": self.change,
            "sign": self.sign,
            "identity": list(self.identity),
            "before": self.before,
            "after": self.after,
            "changed_fields": list(self.changed_fields),
        }


@dataclass(frozen=True)
class IdentityCandidate:
    change: str
    before_identity: SymbolIdentity
    after_identity: SymbolIdentity
    evidence: str
    confidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "before_identity": list(self.before_identity),
            "after_identity": list(self.after_identity),
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class RepositoryTwinDelta:
    base: RepositorySnapshot
    changed: RepositorySnapshot
    file_changes: tuple[EntityChange, ...]
    symbol_changes: tuple[EntityChange, ...]
    edge_changes: tuple[EntityChange, ...]
    identity_candidates: tuple[IdentityCandidate, ...]
    delta_digest: str

    @property
    def changed_paths(self) -> tuple[str, ...]:
        # File content is the observed intervention.  Edge resolution can change
        # in an untouched caller merely because its target disappeared; treating
        # that derived change as another seed would leak the answer into the
        # propagation stage.
        paths = {str(change.identity[0]) for change in self.file_changes if change.identity}
        if not paths:
            paths = {
                str(change.identity[0])
                for change in (*self.symbol_changes, *self.edge_changes)
                if change.identity
            }
        return tuple(sorted(paths))

    def as_dict(self) -> dict[str, Any]:
        return {
            "base": self.base.as_dict(include_entities=False),
            "changed": self.changed.as_dict(include_entities=False),
            "delta_digest": self.delta_digest,
            "changed_paths": list(self.changed_paths),
            "file_changes": [item.as_dict() for item in self.file_changes],
            "symbol_changes": [item.as_dict() for item in self.symbol_changes],
            "edge_changes": [item.as_dict() for item in self.edge_changes],
            "identity_candidates": [item.as_dict() for item in self.identity_candidates],
            "limits": {
                "identity_candidates_are_not_proven_renames": True,
                "structural_delta_is_not_runtime_behavior": True,
            },
        }


def capture_snapshot(project: str) -> RepositorySnapshot:
    """Capture a normalized immutable view of a current structural index."""
    status = repository.project_status(project)
    if not status["structural_current"]:
        raise RuntimeError(
            f"Project {project!r} is newer than its structural index; refresh before capture."
        )
    repo = repository.require_registered_project(project)
    db = repository.connect()
    snapshot_row = db.execute(
        "SELECT * FROM structure_snapshots WHERE project=?",
        (project,),
    ).fetchone()
    if not snapshot_row:
        raise RuntimeError(f"Project {project!r} has no structural snapshot")

    files: list[FileState] = []
    for row in db.execute(
        """SELECT path,authority,reason FROM source_authority
           WHERE project=? ORDER BY path""",
        (project,),
    ):
        absolute = repo / str(row["path"])
        content = absolute.read_bytes() if absolute.is_file() else b""
        files.append(
            FileState(
                path=str(row["path"]),
                authority=str(row["authority"]),
                reason=str(row["reason"]),
                content_digest=hashlib.sha256(content).hexdigest(),
            )
        )

    symbol_rows = db.execute(
        """SELECT path,kind,name,qualified_name,start_line,end_line,signature,
                  active,metadata
           FROM symbols WHERE project=?
           ORDER BY path,kind,qualified_name,start_line,end_line,id""",
        (project,),
    ).fetchall()
    occurrences: dict[tuple[str, str, str], int] = {}
    symbols: list[SymbolState] = []
    for row in symbol_rows:
        path = str(row["path"])
        kind = str(row["kind"])
        name = str(row["name"])
        qualified_name = str(row["qualified_name"])
        partial_identity = (path, kind, qualified_name)
        occurrence = occurrences.get(partial_identity, 0)
        occurrences[partial_identity] = occurrence + 1
        source = _source_slice(
            repo,
            path,
            int(row["start_line"]),
            int(row["end_line"]),
        )
        symbols.append(
            SymbolState(
                identity=(*partial_identity, occurrence),
                path=path,
                kind=kind,
                name=name,
                qualified_name=qualified_name,
                occurrence=occurrence,
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                signature=str(row["signature"] or ""),
                active=bool(row["active"]),
                metadata=_canonical_json(str(row["metadata"] or "{}")),
                body_digest=hashlib.sha256(source.encode()).hexdigest() if source else "",
                shape_digest=_shape_digest(source, name),
            )
        )

    grouped_edges: dict[EdgeIdentity, dict[str, set[Any]]] = {}
    edge_rows = db.execute(
        """SELECT source_path,source_name,source_qualified_name,relation,target_name,
                  target_path,target_qualified_name,line,resolution_confidence,metadata
           FROM edges WHERE project=?
           ORDER BY source_path,source_qualified_name,relation,target_name,line,id""",
        (project,),
    ).fetchall()
    for row in edge_rows:
        identity: EdgeIdentity = (
            str(row["source_path"]),
            str(row["source_qualified_name"] or row["source_name"] or ""),
            str(row["relation"]),
            str(row["target_name"] or ""),
            str(row["target_path"] or ""),
            str(row["target_qualified_name"] or ""),
        )
        grouped = grouped_edges.setdefault(
            identity,
            {"lines": set(), "resolutions": set(), "metadata": set()},
        )
        grouped["lines"].add(int(row["line"] or 1))
        grouped["resolutions"].add(str(row["resolution_confidence"] or "unresolved"))
        grouped["metadata"].add(_canonical_json(str(row["metadata"] or "{}")))

    edges = tuple(
        EdgeState(
            identity=identity,
            source_path=identity[0],
            source_symbol=identity[1],
            relation=identity[2],
            target_name=identity[3],
            target_path=identity[4],
            target_symbol=identity[5],
            lines=tuple(sorted(values["lines"])),
            resolutions=tuple(sorted(values["resolutions"])),
            metadata=tuple(sorted(values["metadata"])),
        )
        for identity, values in sorted(grouped_edges.items())
    )
    files_tuple = tuple(files)
    symbols_tuple = tuple(symbols)
    normalized = _snapshot_state_payload(files_tuple, symbols_tuple, edges)
    captured = RepositorySnapshot(
        project=project,
        commit=str(status["commit"]),
        generation_id=str(status["generation_id"]),
        extractor_version=str(snapshot_row["extractor_version"]),
        graph_digest=str(snapshot_row["graph_digest"]),
        snapshot_digest=_digest(normalized),
        files=files_tuple,
        symbols=symbols_tuple,
        edges=edges,
    )
    final_status = repository.project_status(project)
    if (
        not final_status["structural_current"]
        or final_status["commit"] != status["commit"]
        or final_status["generation_id"] != status["generation_id"]
    ):
        raise RuntimeError(f"Project {project!r} changed while its twin snapshot was captured")
    return captured


def snapshot_from_dict(payload: dict[str, Any]) -> RepositorySnapshot:
    """Restore a portable snapshot and verify its content-addressed identity."""
    if payload.get("format_version") != 1:
        raise ValueError("Unsupported Repository Twin snapshot format")
    try:
        files = tuple(
            FileState(
                path=str(item["path"]),
                authority=str(item["authority"]),
                reason=str(item["reason"]),
                content_digest=str(item["content_digest"]),
            )
            for item in payload["files"]
        )
        symbols = tuple(
            SymbolState(
                identity=tuple(item["identity"]),
                path=str(item["path"]),
                kind=str(item["kind"]),
                name=str(item["name"]),
                qualified_name=str(item["qualified_name"]),
                occurrence=int(item["occurrence"]),
                start_line=int(item["start_line"]),
                end_line=int(item["end_line"]),
                signature=str(item["signature"]),
                active=bool(item["active"]),
                metadata=_canonical_json(json.dumps(item["metadata"], ensure_ascii=False)),
                body_digest=str(item["body_digest"]),
                shape_digest=str(item["shape_digest"]),
            )
            for item in payload["symbols"]
        )
        edges = tuple(
            EdgeState(
                identity=tuple(item["identity"]),
                source_path=str(item["source_path"]),
                source_symbol=str(item["source_symbol"]),
                relation=str(item["relation"]),
                target_name=str(item["target_name"]),
                target_path=str(item["target_path"] or ""),
                target_symbol=str(item["target_symbol"] or ""),
                lines=tuple(int(line) for line in item["lines"]),
                resolutions=tuple(str(value) for value in item["resolutions"]),
                metadata=tuple(
                    _canonical_json(json.dumps(value, ensure_ascii=False))
                    for value in item["metadata"]
                ),
            )
            for item in payload["edges"]
        )
        snapshot = RepositorySnapshot(
            project=str(payload["project"]),
            commit=str(payload["commit"]),
            generation_id=str(payload["generation_id"]),
            extractor_version=str(payload["extractor_version"]),
            graph_digest=str(payload["graph_digest"]),
            snapshot_digest=str(payload["snapshot_digest"]),
            files=files,
            symbols=symbols,
            edges=edges,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid Repository Twin snapshot payload") from exc
    actual_digest = _digest(_snapshot_state_payload(files, symbols, edges))
    if actual_digest != snapshot.snapshot_digest:
        raise ValueError("Repository Twin snapshot digest does not match its entities")
    expected_counts = payload.get("counts")
    if (
        expected_counts is not None
        and expected_counts != snapshot.as_dict(include_entities=False)["counts"]
    ):
        raise ValueError("Repository Twin snapshot counts do not match its entities")
    return snapshot


def save_snapshot(snapshot: RepositorySnapshot, path: Path) -> None:
    """Persist a portable T0/T1 artifact without embedding full source bodies."""
    path.write_text(
        f"{json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


def load_snapshot(path: Path) -> RepositorySnapshot:
    """Load and verify a snapshot previously written by :func:`save_snapshot`."""
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load Repository Twin snapshot: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Repository Twin snapshot must be a JSON object")
    return snapshot_from_dict(payload)


def _changed_fields(
    before: dict[str, Any],
    after: dict[str, Any],
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(field for field in fields if before.get(field) != after.get(field))


def _entity_changes(
    entity: str,
    before: dict[tuple[Any, ...], Any],
    after: dict[tuple[Any, ...], Any],
    semantic_fields: tuple[str, ...],
    citation_fields: tuple[str, ...] = (),
) -> tuple[EntityChange, ...]:
    changes: list[EntityChange] = []
    for identity in sorted(before.keys() - after.keys()):
        changes.append(
            EntityChange(entity, "removed", -1, identity, before[identity].as_dict(), None)
        )
    for identity in sorted(after.keys() - before.keys()):
        changes.append(
            EntityChange(entity, "added", 1, identity, None, after[identity].as_dict())
        )
    for identity in sorted(before.keys() & after.keys()):
        old = before[identity].as_dict()
        new = after[identity].as_dict()
        semantic = _changed_fields(old, new, semantic_fields)
        citations = _changed_fields(old, new, citation_fields)
        if semantic:
            changes.append(
                EntityChange(entity, "modified", 0, identity, old, new, semantic + citations)
            )
        elif citations:
            changes.append(
                EntityChange(entity, "relocated", 0, identity, old, new, citations)
            )
    return tuple(changes)


def _identity_candidates(
    removed: list[SymbolState],
    added: list[SymbolState],
) -> tuple[IdentityCandidate, ...]:
    removed_by_shape: dict[tuple[str, str], list[SymbolState]] = {}
    added_by_shape: dict[tuple[str, str], list[SymbolState]] = {}
    for item in removed:
        if item.shape_digest:
            removed_by_shape.setdefault((item.kind, item.shape_digest), []).append(item)
    for item in added:
        if item.shape_digest:
            added_by_shape.setdefault((item.kind, item.shape_digest), []).append(item)

    candidates: list[IdentityCandidate] = []
    for key in sorted(removed_by_shape.keys() & added_by_shape.keys()):
        old_items = removed_by_shape[key]
        new_items = added_by_shape[key]
        if len(old_items) != 1 or len(new_items) != 1:
            continue
        old = old_items[0]
        new = new_items[0]
        if old.path == new.path and old.name != new.name:
            change = "rename_candidate"
        elif old.path != new.path and old.name == new.name:
            change = "move_candidate"
        else:
            change = "identity_replacement_candidate"
        candidates.append(
            IdentityCandidate(
                change=change,
                before_identity=old.identity,
                after_identity=new.identity,
                evidence="Exact symbol shape after erasing the declared name and whitespace.",
                confidence="conservative_exact_shape",
            )
        )
    return tuple(candidates)


def compare_snapshots(
    base: RepositorySnapshot,
    changed: RepositorySnapshot,
) -> RepositoryTwinDelta:
    """Compute a signed structural delta without discarding deleted evidence."""
    if base.extractor_version != changed.extractor_version:
        raise ValueError("Repository Twin snapshots must use the same structural extractor")
    base_files = {(item.path,): item for item in base.files}
    changed_files = {(item.path,): item for item in changed.files}
    file_changes = _entity_changes(
        "file",
        base_files,
        changed_files,
        ("authority", "reason", "content_digest"),
    )
    base_symbols = {item.identity: item for item in base.symbols}
    changed_symbols = {item.identity: item for item in changed.symbols}
    symbol_changes = _entity_changes(
        "symbol",
        base_symbols,
        changed_symbols,
        ("signature", "active", "metadata", "body_digest"),
        ("start_line", "end_line"),
    )
    base_edges = {item.identity: item for item in base.edges}
    changed_edges = {item.identity: item for item in changed.edges}
    edge_changes = _entity_changes(
        "edge",
        base_edges,
        changed_edges,
        ("resolutions", "metadata"),
        ("lines",),
    )
    removed = [base_symbols[change.identity] for change in symbol_changes if change.sign < 0]
    added = [changed_symbols[change.identity] for change in symbol_changes if change.sign > 0]
    identity_candidates = _identity_candidates(removed, added)
    delta_payload = {
        "base": base.snapshot_digest,
        "changed": changed.snapshot_digest,
        "files": [item.as_dict() for item in file_changes],
        "symbols": [item.as_dict() for item in symbol_changes],
        "edges": [item.as_dict() for item in edge_changes],
        "identity_candidates": [item.as_dict() for item in identity_candidates],
    }
    return RepositoryTwinDelta(
        base=base,
        changed=changed,
        file_changes=file_changes,
        symbol_changes=symbol_changes,
        edge_changes=edge_changes,
        identity_candidates=identity_candidates,
        delta_digest=_digest(delta_payload),
    )


@dataclass(frozen=True)
class TwinTransition:
    source: str
    target: str
    relation: str
    direction: str
    structural_status: str
    evidence_side: str
    evidence_path: str
    lines: tuple[int, ...]
    weight: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "direction": self.direction,
            "structural_status": self.structural_status,
            "evidence_side": self.evidence_side,
            "evidence_path": self.evidence_path,
            "lines": list(self.lines),
            "weight": round(self.weight, 6),
        }


def _shareable_resource(edge: EdgeState) -> bool:
    if edge.relation.endswith("_table"):
        return edge.target_name.startswith("public.")
    if edge.relation in {"calls_rpc", "calls_sql"}:
        return edge.target_name.startswith("public.")
    return edge.relation == "invokes_edge_function" and bool(edge.target_name)


def _resource_node(edge: EdgeState) -> str:
    if edge.relation.endswith("_table"):
        family = "table"
    elif edge.relation in {"calls_rpc", "calls_sql"}:
        family = "callable"
    else:
        family = "edge-function"
    return f"@resource:{family}:{edge.target_name.casefold()}"


def _union_graph(
    base: RepositorySnapshot,
    changed: RepositorySnapshot,
) -> dict[str, list[TwinTransition]]:
    base_edges = {item.identity: item for item in base.edges}
    changed_edges = {item.identity: item for item in changed.edges}
    maximum = max(cartographer.RELATION_WEIGHTS.values())
    graph: dict[str, list[TwinTransition]] = {}

    def add(
        source: str,
        target: str,
        edge: EdgeState,
        direction: str,
        status: str,
        side: str,
        direction_factor: float,
    ) -> None:
        if not source or not target or source == target:
            return
        relation_weight = cartographer.RELATION_WEIGHTS.get(edge.relation, 5.0) / maximum
        status_factor = {
            "removed": 1.0,
            "added": 0.98,
            "modified": 1.0,
            "stable": 0.72,
        }[status]
        transition = TwinTransition(
            source=source,
            target=target,
            relation=edge.relation,
            direction=direction,
            structural_status=status,
            evidence_side=side,
            evidence_path=edge.source_path,
            lines=edge.lines,
            weight=min(1.0, relation_weight * status_factor * direction_factor),
        )
        graph.setdefault(source, []).append(transition)

    for identity in sorted(base_edges.keys() | changed_edges.keys()):
        old = base_edges.get(identity)
        new = changed_edges.get(identity)
        if old and new:
            status = "modified" if old.semantic_payload() != new.semantic_payload() else "stable"
            side = "both"
            edge = new
        elif old:
            status = "removed"
            side = "base"
            edge = old
        else:
            status = "added"
            side = "changed"
            assert new is not None
            edge = new

        if edge.target_path:
            add(
                edge.source_path,
                edge.target_path,
                edge,
                "toward_dependency",
                status,
                side,
                0.72,
            )
            add(
                edge.target_path,
                edge.source_path,
                edge,
                "toward_dependent",
                status,
                side,
                1.0,
            )
        if edge.relation in cartographer.RESOURCE_RELATIONS and _shareable_resource(edge):
            resource = _resource_node(edge)
            add(
                edge.source_path,
                resource,
                edge,
                "toward_resource",
                status,
                side,
                0.9,
            )
            add(
                resource,
                edge.source_path,
                edge,
                "toward_consumer",
                status,
                side,
                1.0,
            )
    for source in graph:
        graph[source].sort(
            key=lambda item: (
                -item.weight,
                item.target,
                item.relation,
                item.structural_status,
            )
        )
    return graph


def _propagate(
    base: RepositorySnapshot,
    changed: RepositorySnapshot,
    seed_paths: tuple[str, ...],
    *,
    limit: int,
    max_hops: int,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least one")
    if max_hops < 1:
        raise ValueError("max_hops must be at least one")
    graph = _union_graph(base, changed)
    base_edge_ids = {item.identity for item in base.edges}
    changed_edge_ids = {item.identity for item in changed.edges}
    seeds = tuple(dict.fromkeys(seed_paths))
    if not seeds:
        raise ValueError("at least one changed path is required")
    best: dict[str, float] = {path: 1.0 for path in seeds}
    paths: dict[str, tuple[TwinTransition, ...]] = {path: () for path in seeds}
    heap: list[tuple[float, int, str]] = [(-1.0, 0, path) for path in seeds]
    heapq.heapify(heap)
    while heap:
        negative_score, hops, node = heapq.heappop(heap)
        score = -negative_score
        if score + 1e-15 < best.get(node, 0.0) or hops >= max_hops:
            continue
        for transition in graph.get(node, []):
            candidate_score = score * 0.78 * transition.weight
            if candidate_score <= best.get(transition.target, 0.0) + 1e-15:
                continue
            best[transition.target] = candidate_score
            paths[transition.target] = (*paths[node], transition)
            heapq.heappush(heap, (-candidate_score, hops + 1, transition.target))

    seed_set = set(seeds)
    base_files = {item.path: item for item in base.files}
    changed_files = {item.path: item for item in changed.files}
    ranked = [
        (path, score)
        for path, score in best.items()
        if path not in seed_set and not path.startswith("@resource:")
    ]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    candidates = []
    for path, score in ranked[: max(1, min(limit, 100))]:
        state = changed_files.get(path) or base_files.get(path)
        candidates.append(
            {
                "path": path,
                "score": round(score, 12),
                "authority": state.authority if state else "unknown",
                "exists_in_base": path in base_files,
                "exists_in_changed": path in changed_files,
                "strongest_path": [item.as_dict() for item in paths[path]],
            }
        )
    return {
        "seed_paths": list(seeds),
        "candidates": candidates,
        "algorithm": {
            "name": "max_product_union_graph",
            "hop_decay": 0.78,
            "max_hops": max_hops,
            "uses_removed_edges": bool(base_edge_ids - changed_edge_ids),
        },
        "limits": {
            "candidate_is_not_a_required_edit": True,
            "graph_path_is_not_runtime_proof": True,
            "only_extracted_static_relations_are_propagated": True,
        },
    }


def impact_from_twin(
    delta: RepositoryTwinDelta,
    *,
    limit: int = 20,
    max_hops: int = 4,
) -> dict[str, Any]:
    """Propagate a signed delta over the union of T0 and T1."""
    result = _propagate(
        delta.base,
        delta.changed,
        delta.changed_paths,
        limit=limit,
        max_hops=max_hops,
    )
    result.update(
        {
            "base_snapshot": delta.base.snapshot_digest,
            "changed_snapshot": delta.changed.snapshot_digest,
            "delta_digest": delta.delta_digest,
        }
    )
    return result


def impact_from_snapshot(
    snapshot: RepositorySnapshot,
    seed_paths: list[str] | tuple[str, ...],
    *,
    limit: int = 20,
    max_hops: int = 4,
) -> dict[str, Any]:
    """Current-only control using the same propagator and weights."""
    return _propagate(
        snapshot,
        snapshot,
        tuple(seed_paths),
        limit=limit,
        max_hops=max_hops,
    )


def render_twin_packet(
    delta: RepositoryTwinDelta,
    impact: dict[str, Any],
    *,
    max_changes: int = 12,
    max_candidates: int = 8,
) -> str:
    """Render a bounded post-change packet for a human or coding agent."""
    lines = [
        "REPOSITORY TWIN STRUCTURAL DELTA",
        "Static review evidence; not proof of a regression or required edit.",
        f"T0: {delta.base.commit} / {delta.base.snapshot_digest[:12]}",
        f"T1: {delta.changed.commit} / {delta.changed.snapshot_digest[:12]}",
        f"Changed paths: {', '.join(delta.changed_paths)}",
        "",
        "SIGNED STRUCTURAL CHANGES",
    ]
    changes = (*delta.symbol_changes, *delta.edge_changes)
    for change in changes[: max(1, min(max_changes, 50))]:
        marker = "+" if change.sign > 0 else "-" if change.sign < 0 else "~"
        lines.append(
            f"  {marker} {change.entity} {change.change}: "
            f"{' :: '.join(str(part) for part in change.identity)}"
        )
    if len(changes) > max_changes:
        lines.append(f"  ... {len(changes) - max_changes} additional structural changes")
    if delta.identity_candidates:
        lines.extend(["", "CONSERVATIVE IDENTITY CANDIDATES"])
        for item in delta.identity_candidates:
            lines.append(
                f"  ? {item.change}: {item.before_identity[2]} -> {item.after_identity[2]} "
                f"({item.confidence})"
            )
    lines.extend(["", "UNION-GRAPH REVIEW CANDIDATES"])
    for index, candidate in enumerate(impact["candidates"][:max_candidates], start=1):
        statuses = " -> ".join(
            f"{step['relation']}[{step['structural_status']}:{step['evidence_side']}]"
            for step in candidate["strongest_path"]
        )
        lines.append(
            f"  {index}. {candidate['path']} (score={candidate['score']:.6g}) via {statuses}"
        )
    if not impact["candidates"]:
        lines.append("  No extracted relationship reached another file.")
    lines.extend(
        [
            "",
            "Required action: inspect the cited paths and tests; reject unsupported candidates.",
        ]
    )
    return "\n".join(lines)
