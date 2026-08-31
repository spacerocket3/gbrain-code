"""Experimental impact propagation over GBrain's typed repository graph.

This module deliberately lives outside the production MCP surface.  It turns a
set of changed files into a probability distribution over possible ripple
files.  The distribution is navigation evidence, not a prediction that a file
must change.
"""

from __future__ import annotations

import heapq
import json
import math
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cartographer
import repository

DIRECT_RELATIONS = frozenset(cartographer.RELATION_WEIGHTS)
RESOURCE_RELATIONS = frozenset(cartographer.RESOURCE_RELATIONS)
DEFINITION_KINDS = ("function", "view", "policy", "trigger", "type")
CONFIDENCE_WEIGHTS = {
    "active_sql": 1.0,
    "exact_path": 1.0,
    "import_resolved": 0.95,
    "literal_via_wrapper": 0.95,
    "same_file": 0.9,
    "lexical": 0.72,
    "unresolved": 0.45,
}
AUTHORITY_WEIGHTS = {
    "code": 1.0,
    "schema_history": 1.0,
    "test": 0.9,
    "config": 0.82,
    "generated": 0.62,
    "active": 0.55,
    "historical": 0.18,
    "unknown": 0.45,
}
MAX_RELATION_WEIGHT = max(cartographer.RELATION_WEIGHTS.values())
GRAPH_VARIANTS = frozenset({"topology", "typed", "resources", "temporal"})


@dataclass(frozen=True)
class Transition:
    source: str
    target: str
    weight: float
    relation: str
    direction: str
    evidence_target: str
    evidence_path: str
    lines: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "weight": round(self.weight, 6),
            "relation": self.relation,
            "direction": self.direction,
            "evidence_target": self.evidence_target,
            "evidence_path": self.evidence_path,
            "lines": list(self.lines),
        }


def _is_resource(node: str) -> bool:
    return node.startswith("@resource:")


def _resource_node(relation: str, target: str) -> str:
    if relation.endswith("_table"):
        family = "table"
    elif relation in {"calls_rpc", "calls_sql"}:
        family = "callable"
    else:
        family = "edge-function"
    return f"@resource:{family}:{target.casefold()}"


def _authority(db: Any, project: str, path: str) -> str:
    row = db.execute(
        "SELECT authority FROM source_authority WHERE project=? AND path=?",
        (project, path),
    ).fetchone()
    return str(row[0]) if row else "unknown"


def _temporal_activity(db: Any, project: str, path: str) -> float:
    """Discount superseded SQL without erasing still-canonical table definitions."""
    if _authority(db, project, path) != "schema_history":
        return 1.0
    placeholders = ",".join("?" for _ in DEFINITION_KINDS)
    row = db.execute(
        f"""SELECT count(*) AS total,coalesce(sum(active),0) AS active
            FROM symbols WHERE project=? AND path=? AND kind IN ({placeholders})""",
        (project, path, *DEFINITION_KINDS),
    ).fetchone()
    total = int(row["total"] or 0)
    if not total:
        return 0.8
    active_ratio = float(row["active"] or 0) / total
    defines_table = db.execute(
        """SELECT 1 FROM symbols
           WHERE project=? AND path=? AND kind='table' LIMIT 1""",
        (project, path),
    ).fetchone()
    # A migration can contain a superseded function and still be the canonical
    # definition of a table.  Temporal lineage should demote the obsolete
    # callable without making the whole file practically unreachable.
    return max(0.8 if defines_table else 0.12, active_ratio)


def _node_factor(db: Any, project: str, node: str) -> float:
    if _is_resource(node):
        return 1.0
    authority = _authority(db, project, node)
    return AUTHORITY_WEIGHTS.get(authority, AUTHORITY_WEIGHTS["unknown"]) * _temporal_activity(
        db, project, node
    )


def _shareable_resource(relation: str, target: str) -> bool:
    if relation.endswith("_table"):
        return target.startswith("public.")
    if relation in {"calls_rpc", "calls_sql"}:
        return target.startswith("public.")
    return relation == "invokes_edge_function" and bool(target)


def build_transition_graph(
    project: str,
    variant: str = "temporal",
) -> dict[str, list[Transition]]:
    """Build a bounded file/resource transition graph from the current snapshot."""
    if variant not in GRAPH_VARIANTS:
        raise ValueError(f"variant must be one of: {', '.join(sorted(GRAPH_VARIANTS))}")
    repository.project_status(project)  # validates registration before opening the index
    db = repository.connect()
    typed = variant != "topology"
    include_resources = variant in {"resources", "temporal"}
    include_temporal_state = variant == "temporal"
    rows = db.execute(
        """SELECT source_path,relation,target_name,target_path,resolution_confidence,line
           FROM edges WHERE project=? ORDER BY source_path,relation,target_name,line""",
        (project,),
    ).fetchall()
    accumulated: dict[
        tuple[str, str, str, str, str, str], tuple[float, int, set[int]]
    ] = {}

    def record(
        source: str,
        target: str,
        relation: str,
        direction: str,
        evidence_target: str,
        evidence_path: str,
        weight: float,
        line: int,
    ) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, relation, direction, evidence_target, evidence_path)
        previous, count, lines = accumulated.get(key, (0.0, 0, set()))
        accumulated[key] = (max(previous, weight), count + 1, {*lines, line})

    for row in rows:
        relation = str(row["relation"])
        if relation not in DIRECT_RELATIONS:
            continue
        source = str(row["source_path"])
        target_name = str(row["target_name"] or "")
        target_path = str(row["target_path"] or "")
        line = int(row["line"] or 1)
        relation_weight = (
            cartographer.RELATION_WEIGHTS[relation] / MAX_RELATION_WEIGHT if typed else 1.0
        )
        confidence = (
            CONFIDENCE_WEIGHTS.get(
                str(row["resolution_confidence"]), CONFIDENCE_WEIGHTS["unresolved"]
            )
            if typed
            else 1.0
        )
        base = relation_weight * confidence

        def factor(path: str) -> float:
            return _node_factor(db, project, path) if include_temporal_state else 1.0

        if target_path:
            record(
                source,
                target_path,
                relation,
                "toward_dependency",
                target_name,
                source,
                base * (0.72 if typed else 1.0) * factor(target_path),
                line,
            )
            record(
                target_path,
                source,
                relation,
                "toward_dependent",
                target_name,
                source,
                base * factor(source),
                line,
            )

        if (
            include_resources
            and relation in RESOURCE_RELATIONS
            and _shareable_resource(relation, target_name)
        ):
            resource = _resource_node(relation, target_name)
            record(
                source,
                resource,
                relation,
                "toward_resource",
                target_name,
                source,
                base * 0.9,
                line,
            )
            record(
                resource,
                source,
                relation,
                "toward_consumer",
                target_name,
                source,
                base * factor(source),
                line,
            )

    graph: dict[str, list[Transition]] = defaultdict(list)
    for key, (weight, count, lines) in accumulated.items():
        source, target, relation, direction, evidence_target, evidence_path = key
        # Repeated references are evidence, but should not grow linearly with line count.
        adjusted = weight * (1.0 + 0.08 * math.log1p(max(0, count - 1)))
        graph[source].append(
            Transition(
                source,
                target,
                adjusted,
                relation,
                direction,
                evidence_target,
                evidence_path,
                tuple(sorted(lines)),
            )
        )
    for source in graph:
        graph[source].sort(key=lambda item: (-item.weight, item.target, item.relation))
    return dict(graph)


def _normalised(
    graph: dict[str, list[Transition]],
    *,
    seeds: set[str] | None = None,
    focus_terms: set[str] | None = None,
    seed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    strict_delta_edges: bool = False,
) -> dict[str, list[tuple[Transition, float]]]:
    result: dict[str, list[tuple[Transition, float]]] = {}
    for source, transitions in graph.items():
        weighted = []
        for item in transitions:
            weight = item.weight
            ranges = (seed_ranges or {}).get(source, [])
            if (
                seeds
                and source in seeds
                and ranges
                and item.direction in {"toward_dependency", "toward_resource"}
            ):
                touches_delta = any(
                    start <= line <= end
                    for line in item.lines
                    for start, end in ranges
                )
                if strict_delta_edges and not touches_delta:
                    weight = 0.0
                else:
                    weight *= 2.5 if touches_delta else 0.2
            if seeds and focus_terms and source in seeds:
                evidence_terms = repository.lexical_tokens(
                    f"{item.evidence_target} {item.target}"
                )
                overlap = len(evidence_terms & focus_terms)
                if overlap:
                    weight *= 1.0 + min(2.5, 0.75 * overlap)
            weighted.append((item, weight))
        total = sum(weight for _item, weight in weighted)
        if total > 0:
            result[source] = [(item, weight / total) for item, weight in weighted]
    return result


def _strongest_path(
    transitions: dict[str, list[tuple[Transition, float]]],
    seeds: set[str],
    target: str,
    max_hops: int,
) -> list[dict[str, Any]]:
    heap: list[tuple[float, int, str, tuple[str, ...], tuple[Transition, ...]]] = []
    for seed in sorted(seeds):
        heapq.heappush(heap, (0.0, 0, seed, (seed,), ()))
    best: dict[tuple[str, int], float] = {}
    while heap:
        cost, hops, node, visited, path = heapq.heappop(heap)
        if node == target and path:
            return [item.as_dict() for item in path]
        if hops >= max_hops:
            continue
        state = (node, hops)
        if cost > best.get(state, math.inf):
            continue
        best[state] = cost
        for transition, probability in transitions.get(node, []):
            if transition.target in visited or probability <= 0:
                continue
            next_cost = cost - math.log(probability)
            next_state = (transition.target, hops + 1)
            if next_cost >= best.get(next_state, math.inf):
                continue
            best[next_state] = next_cost
            heapq.heappush(
                heap,
                (
                    next_cost,
                    hops + 1,
                    transition.target,
                    (*visited, transition.target),
                    (*path, transition),
                ),
            )
    return []


def impact_distribution(
    project: str,
    seed_paths: list[str],
    *,
    limit: int = 20,
    restart: float = 0.28,
    max_iterations: int = 80,
    tolerance: float = 1e-10,
    explanation_hops: int = 5,
    delta_text: str = "",
    graph_variant: str = "temporal",
    seed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    strict_delta_edges: bool = False,
) -> dict[str, Any]:
    """Propagate an edit seed into an explainable candidate impact distribution."""
    if not 0 < restart < 1:
        raise ValueError("restart must be between zero and one")
    repo = repository.require_registered_project(project)
    status = repository.project_status(project)
    if not status["structural_current"]:
        raise RuntimeError("The working tree is newer than the index; refresh before propagation.")
    seeds = list(dict.fromkeys(seed_paths))
    if not seeds:
        raise ValueError("at least one seed path is required")
    missing = [path for path in seeds if not (repo / Path(path)).is_file()]
    if missing:
        raise ValueError(f"seed paths do not exist in the registered repository: {missing}")

    graph = build_transition_graph(project, graph_variant)
    seed_set = set(seeds)
    focus_terms = repository.lexical_tokens(delta_text) if delta_text else set()
    transitions = _normalised(
        graph,
        seeds=seed_set,
        focus_terms=focus_terms,
        seed_ranges=seed_ranges,
        strict_delta_edges=strict_delta_edges,
    )
    seed_mass = 1.0 / len(seeds)
    initial = {path: seed_mass for path in seeds}
    state = dict(initial)
    iterations = 0
    residual = math.inf
    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        updated: dict[str, float] = defaultdict(float)
        for seed, mass in initial.items():
            updated[seed] += restart * mass
        for source, mass in state.items():
            outgoing = transitions.get(source)
            if not outgoing:
                for seed, seed_share in initial.items():
                    updated[seed] += (1.0 - restart) * mass * seed_share
                continue
            for transition, probability in outgoing:
                updated[transition.target] += (1.0 - restart) * mass * probability
        nodes = set(state) | set(updated)
        residual = sum(abs(updated.get(node, 0.0) - state.get(node, 0.0)) for node in nodes)
        state = dict(updated)
        if residual <= tolerance:
            break

    ranked = [
        (node, score)
        for node, score in state.items()
        if node not in seed_set and not _is_resource(node)
    ]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    db = repository.connect()
    candidates = []
    for path, score in ranked[: max(1, min(limit, 100))]:
        candidates.append(
            {
                "path": path,
                "score": round(score, 12),
                "authority": _authority(db, project, path),
                "temporal_activity": round(_temporal_activity(db, project, path), 6),
                "strongest_path": _strongest_path(
                    transitions, seed_set, path, explanation_hops
                ),
            }
        )
    return {
        "project": project,
        "snapshot": {
            "commit": status["commit"],
            "generation_id": status["generation_id"],
        },
        "seeds": seeds,
        "algorithm": {
            "name": "personalized-pagerank",
            "graph_variant": graph_variant,
            "restart": restart,
            "iterations": iterations,
            "residual": residual,
            "candidate_meaning": "navigation evidence, not a required-change prediction",
            "delta_terms_used": len(focus_terms),
            "delta_ranges_used": sum(len(items) for items in (seed_ranges or {}).values()),
            "strict_delta_edges": strict_delta_edges,
        },
        "state": {
            "nodes": len({*graph, *(item.target for values in graph.values() for item in values)}),
            "transitions": sum(len(items) for items in graph.values()),
            "probability_mass": round(sum(state.values()), 12),
            "file_probability_mass": round(
                sum(score for node, score in state.items() if not _is_resource(node)), 12
            ),
        },
        "candidates": candidates,
    }


def _typescript_local_flows(
    repo: Path,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve bounded, file-local TS value paths for already-ranked candidates."""
    if not requests:
        return {"files": {}}
    script = Path(__file__).with_name("ts_local_impact.mjs")
    result = subprocess.run(
        ["node", str(script), str(repo)],
        input=json.dumps({"files": requests}),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _candidate_anchors(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    path = candidate["path"]
    anchors: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for step in reversed(candidate.get("strongest_path", [])):
        if step.get("evidence_path") != path:
            continue
        for line in step.get("lines", []):
            key = (int(line), str(step.get("evidence_target") or ""), str(step["relation"]))
            if key in seen:
                continue
            seen.add(key)
            anchors.append(
                {
                    "line": key[0],
                    "target": key[1],
                    "relation": key[2],
                    "direction": step["direction"],
                }
            )
        if anchors:
            break
    return anchors


def _flow_priority(flow: dict[str, Any]) -> float:
    sink = flow["chain"][-1]
    field = str(sink["label"]).rsplit(".", 1)[-1].casefold()
    context = flow.get("sink_context", {})
    score = 0.0
    if field == "value":
        score += 6.0
    elif field in {"label", "description", "detail", "statuslabel"}:
        score += 3.0
    elif field in {"tone", "color", "class", "classname"}:
        score -= 2.0
    explanatory = [
        value
        for key, value in context.items()
        if key.casefold() in {"detail", "description", "label", "title", "statuslabel"}
    ]
    score += 2.0 * sum(not str(value).startswith("{") for value in explanatory)
    score += min(2.0, max(0, len(flow["chain"]) - 2) * 0.4)
    return score


def impact_obligations(
    project: str,
    seed_paths: list[str],
    *,
    limit: int = 10,
    obligation_limit: int = 12,
    delta_text: str = "",
    seed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    graph_variant: str = "temporal",
) -> dict[str, Any]:
    """Translate ranked ripple paths into compact, unresolved review obligations.

    The translator deliberately stops before semantic judgment.  It can show
    that an observed relationship reaches a rendered value and its explanatory
    fields; source inspection or tests must still decide whether a contradiction
    exists.
    """
    if obligation_limit < 1:
        raise ValueError("obligation_limit must be at least one")
    repo = repository.require_registered_project(project)
    broad_distribution = impact_distribution(
        project,
        seed_paths,
        limit=limit,
        delta_text=delta_text,
        seed_ranges=seed_ranges,
        graph_variant=graph_variant,
    )
    distribution = (
        impact_distribution(
            project,
            seed_paths,
            limit=limit,
            delta_text=delta_text,
            seed_ranges=seed_ranges,
            graph_variant=graph_variant,
            strict_delta_edges=True,
        )
        if seed_ranges
        else broad_distribution
    )
    requests = []
    for candidate in distribution["candidates"]:
        path = candidate["path"]
        anchors = _candidate_anchors(candidate)
        if anchors and Path(path).suffix in {".ts", ".tsx", ".mts", ".cts"}:
            requests.append({"path": path, "anchors": anchors})
    local = _typescript_local_flows(repo, requests)

    obligations = []
    unresolved_candidates = []
    for candidate in distribution["candidates"]:
        path = candidate["path"]
        analysis = local.get("files", {}).get(path, {})
        flows = analysis.get("flows", [])
        if not flows:
            unresolved_candidates.append(
                {
                    "path": path,
                    "score": candidate["score"],
                    "reason": "No bounded local value path reached a supported review sink.",
                    "strongest_path": candidate["strongest_path"],
                }
            )
            continue
        if len(obligations) >= obligation_limit:
            unresolved_candidates.append(
                {
                    "path": path,
                    "score": candidate["score"],
                    "reason": "A supported sink was omitted by the obligation budget.",
                    "strongest_path": candidate["strongest_path"],
                }
            )
            continue
        for flow in sorted(flows, key=lambda item: (-_flow_priority(item), len(item["chain"])))[:1]:
            context = flow.get("sink_context", {})
            has_explanation = any(
                key in context for key in ("detail", "description", "label", "title")
            )
            obligations.append(
                {
                    "status": "unresolved_review_obligation",
                    "candidate_path": path,
                    "candidate_score": candidate["score"],
                    "entry_relationship": flow["anchor"],
                    "repository_path": candidate["strongest_path"],
                    "local_value_path": flow["chain"],
                    "sink_context": context,
                    "invariant": (
                        "A rendered value and its adjacent explanatory fields should remain "
                        "semantically consistent with every upstream contributor."
                        if has_explanation
                        else "Every changed upstream contract reaching a rendered value should "
                        "remain compatible with the consumer's assumptions."
                    ),
                    "review_question": (
                        "Do the adjacent explanatory fields describe every semantic contributor "
                        "shown in this value path?"
                        if has_explanation
                        else "Did the changed upstream contract alter an assumption at this sink?"
                    ),
                    "evidence_level": "static_navigation_and_local_forward_slice",
                    "proven_contradiction": False,
                    "presentation_priority": round(_flow_priority(flow), 3),
                }
            )

    obligation_paths = {item["candidate_path"] for item in obligations}
    core_paths = {item["path"] for item in distribution["candidates"]}
    ambient_halo = [
        {
            "path": item["path"],
            "score": item["score"],
            "reason": "Related by the broad graph but not retained in the strict delta core.",
            "strongest_path": item["strongest_path"],
        }
        for item in broad_distribution["candidates"]
        if item["path"] not in core_paths
    ]
    return {
        "project": project,
        "snapshot": broad_distribution["snapshot"],
        "delta": {
            "seed_paths": broad_distribution["seeds"],
            "seed_ranges": seed_ranges or {},
            "terms_used": distribution["algorithm"]["delta_terms_used"],
        },
        "impact_field": {
            "algorithm": distribution["algorithm"],
            "state": distribution["state"],
            "broad_algorithm": broad_distribution["algorithm"],
            "broad_state": broad_distribution["state"],
            "ranked_candidates": [
                {
                    "path": item["path"],
                    "score": item["score"],
                    "in_delta_core": item["path"] in core_paths,
                    "translation_status": (
                        "obligation"
                        if item["path"] in obligation_paths
                        else "untranslated"
                    ),
                }
                for item in broad_distribution["candidates"]
            ],
            "delta_core_candidates": [
                {"path": item["path"], "score": item["score"]}
                for item in distribution["candidates"]
            ],
            "obligations": obligations,
            "ranked_candidates_without_supported_sink": unresolved_candidates,
            "ambient_relational_halo": ambient_halo,
        },
        "limits": {
            "paths_are_not_runtime_proof": True,
            "obligations_require_source_or_test_verification": True,
            "typescript_slice_is_file_local_and_best_effort": True,
            "delta_core_membership_is_not_causal_proof": True,
            "no_model_generated_conclusions": True,
        },
    }


def render_impact_packet(
    result: dict[str, Any],
    max_obligations: int = 4,
    max_untranslated: int = 4,
    max_halo: int = 3,
    max_ranked: int = 8,
) -> str:
    """Render the smallest human/model-facing packet that preserves the evidence chain."""
    obligations = sorted(
        result["impact_field"]["obligations"],
        key=lambda item: (-item["candidate_score"], -item["presentation_priority"]),
    )[: max(1, min(max_obligations, 12))]
    lines = [
        "DELTA-CONDITIONED IMPACT FIELD",
        "These are unresolved review obligations, not proven regressions.",
        f"Snapshot: {result['snapshot']['commit']} / {result['snapshot']['generation_id']}",
        f"Seeds: {', '.join(result['delta']['seed_paths'])}",
    ]
    ranked = result["impact_field"].get("ranked_candidates", [])[
        : max(0, min(max_ranked, 20))
    ]
    if ranked:
        lines.extend(["", "BROAD RANKING (preserved)"])
        for index, item in enumerate(ranked, start=1):
            labels = ["delta-core" if item["in_delta_core"] else "ambient-halo"]
            if item["translation_status"] == "obligation":
                labels.append("translated-obligation")
            lines.append(
                f"  {index}. {item['path']} (score={item['score']:.6g}; "
                f"{', '.join(labels)})"
            )
    for index, item in enumerate(obligations, start=1):
        entry = item["entry_relationship"]
        lines.extend(
            [
                "",
                f"OBLIGATION {index}: {item['candidate_path']}",
                (
                    f"Entry: {entry['relation']} {entry['target']} at "
                    f"{item['candidate_path']}:{entry['line']}"
                ),
                "Observed value path:",
            ]
        )
        for node in item["local_value_path"]:
            lines.append(
                f"  {item['candidate_path']}:{node['line']} {node['label']}"
                f" <- {node['expression']}"
            )
        context = item.get("sink_context", {})
        if context:
            lines.append("Adjacent sink fields:")
            for key, value in context.items():
                lines.append(f"  {key}: {value}")
        lines.extend(
            [
                f"Invariant to verify: {item['invariant']}",
                f"Question: {item['review_question']}",
                "Required action: inspect these exact lines and either correct a concrete "
                "contradiction or reject the obligation with source-based evidence.",
            ]
        )
    if not obligations:
        lines.extend(["", "No supported local value path reached a review sink."])
    unresolved = result["impact_field"]["ranked_candidates_without_supported_sink"][
        : max(0, min(max_untranslated, 12))
    ]
    if unresolved:
        lines.extend(
            [
                "",
                "UNTRANSLATED RIPPLE CANDIDATES",
                "The graph related these files to the delta, but no supported local sink was "
                "proven. Inspect selectively; do not infer a required change.",
            ]
        )
        for item in unresolved:
            lines.append(f"  {item['path']} (score={item['score']:.6g})")
            for step in item.get("strongest_path", []):
                evidence_lines = ",".join(str(line) for line in step.get("lines", [])) or "?"
                lines.append(
                    f"    {step['source']} -> {step['target']} via {step['relation']} "
                    f"{step['evidence_target']} "
                    f"[{step.get('evidence_path', '?')}:{evidence_lines}]"
                )
    halo = result["impact_field"].get("ambient_relational_halo", [])
    halo = halo[: max(0, min(max_halo, 12))]
    if halo:
        lines.extend(
            [
                "",
                "AMBIENT RELATIONAL HALO",
                "These relationships are real graph evidence but were not retained by the "
                "strict delta-core ranking. Use only as secondary navigation.",
            ]
        )
        for item in halo:
            lines.append(f"  {item['path']} (score={item['score']:.6g})")
            for step in item.get("strongest_path", []):
                evidence_lines = ",".join(str(line) for line in step.get("lines", [])) or "?"
                lines.append(
                    f"    {step['source']} -> {step['target']} via {step['relation']} "
                    f"{step['evidence_target']} "
                    f"[{step.get('evidence_path', '?')}:{evidence_lines}]"
                )
    return "\n".join(lines)


def impact_field_for_diff(
    project: str,
    *,
    base_ref: str = "HEAD",
    question: str = "",
    limit: int = 10,
    obligation_limit: int = 12,
) -> dict[str, Any]:
    """Build an experimental Impact Field from the repository's current diff."""
    repo, _status = cartographer._snapshot(project)
    changed_files, ranges = cartographer._changed_ranges(repo, base_ref)
    if not changed_files:
        raise ValueError(f"no changed files found relative to {base_ref}")
    return impact_obligations(
        project,
        changed_files,
        limit=limit,
        obligation_limit=obligation_limit,
        delta_text=question,
        seed_ranges=ranges,
    )


def reachable_file_distances(
    project: str,
    seed_paths: list[str],
    *,
    max_hops: int = 5,
    graph_variant: str = "temporal",
) -> dict[str, int]:
    """Return file nodes reachable in the chosen representation, independent of rank."""
    graph = build_transition_graph(project, graph_variant)
    distances = {path: 0 for path in seed_paths}
    frontier = list(dict.fromkeys(seed_paths))
    for distance in range(1, max_hops + 1):
        following = []
        for source in frontier:
            for transition in graph.get(source, []):
                if transition.target in distances:
                    continue
                distances[transition.target] = distance
                following.append(transition.target)
        frontier = following
        if not frontier:
            break
    return {
        path: distance
        for path, distance in distances.items()
        if path not in seed_paths and not _is_resource(path)
    }


def lexical_ripple(
    project: str,
    seed_paths: list[str],
    limit: int = 20,
    question: str = "",
) -> list[str]:
    """A deliberately simple identifier baseline for edit-to-ripple evaluation."""
    db = repository.connect()
    terms: list[str] = []
    for path in seed_paths:
        terms.extend(Path(path).stem.replace("-", "_").split("_"))
        rows = db.execute(
            """SELECT name,qualified_name FROM symbols
               WHERE project=? AND path=? ORDER BY start_line LIMIT 30""",
            (project, path),
        ).fetchall()
        for row in rows:
            terms.extend((str(row["name"]), str(row["qualified_name"])))
    query = question or " ".join(dict.fromkeys(term for term in terms if term))
    seed_set = set(seed_paths)
    hits = repository.keyword_search(query, project, limit * 5)
    return [path for path in dict.fromkeys(hit.path for hit in hits) if path not in seed_set][
        :limit
    ]


def static_graph_ripple(
    project: str,
    seed_paths: list[str],
    limit: int = 20,
    question: str = "",
    seed_ranges: dict[str, list[tuple[int, int]]] | None = None,
) -> list[str]:
    """Current two-hop GBrain expansion, used as the strongest non-reactive baseline."""
    status = repository.project_status(project)
    if not status["structural_current"]:
        raise RuntimeError("The working tree is newer than the index; refresh before evaluation.")
    db = repository.connect()
    scores, _reasons, _facts = cartographer._expand_graph(
        db,
        project,
        seed_paths,
        max_depth=2,
        question=question,
        seed_ranges=seed_ranges,
    )
    seed_set = set(seed_paths)
    return [
        path
        for path, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if path not in seed_set
    ][:limit]


def mapped_graph_ripple(
    project: str,
    seed_paths: list[str],
    question: str,
    limit: int = 20,
) -> list[str]:
    """The current question-scoped GBrain map, used as a production baseline."""
    payload = cartographer.map_code_context(
        project,
        question,
        min(60, limit + len(seed_paths) + 10),
        "fast",
    )
    seed_set = set(seed_paths)
    return [
        item["path"]
        for item in payload["map"]["files"]
        if item["path"] not in seed_set
    ][:limit]


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    weights: list[float] | None = None,
    rank_constant: int = 60,
    limit: int = 20,
) -> list[str]:
    """Fuse independent rankings without treating either signal as ground truth."""
    if not rankings:
        return []
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights and rankings must have the same length")
    scores: dict[str, float] = defaultdict(float)
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, path in enumerate(ranking, start=1):
            scores[path] += weight / (rank_constant + rank)
    return [
        path
        for path, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
            :limit
        ]
    ]


def hybrid_ripple(
    project: str,
    seed_paths: list[str],
    question: str,
    limit: int = 20,
    reactive_weight: float = 0.5,
    seed_ranges: dict[str, list[tuple[int, int]]] | None = None,
) -> list[str]:
    """Lexical ranking re-ordered by a deliberately weaker reactive signal."""
    lexical = lexical_ripple(project, seed_paths, limit, question)
    reactive = [
        item["path"]
        for item in impact_distribution(
            project,
            seed_paths,
            limit=limit,
            delta_text=question,
            seed_ranges=seed_ranges,
        )["candidates"]
    ]
    return reciprocal_rank_fusion(
        [lexical, reactive],
        weights=[1.0, reactive_weight],
        limit=limit,
    )
