"""Question-scoped repository maps and diff-aware ripple audits."""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import graph_index
import repository

RELATION_WEIGHTS = {
    "calls_rpc": 24.0,
    "reads_table": 22.0,
    "writes_table": 24.0,
    "accesses_table": 20.0,
    "invokes_edge_function": 22.0,
    "overrides": 18.0,
    "extends": 16.0,
    "calls": 14.0,
    "imports": 9.0,
    "calls_sql": 22.0,
}
RESOURCE_RELATIONS = {
    "calls_rpc",
    "calls_sql",
    "reads_table",
    "writes_table",
    "accesses_table",
    "invokes_edge_function",
}
AUTHORITIES = {"code", "test", "schema_history", "generated", "config"}
SQL_GENERIC_TERMS = {
    "configuration",
    "get",
    "kpi",
    "public",
    "set",
    "station",
    "summary",
    "update",
}


def _snapshot(project: str) -> tuple[Path, dict[str, Any]]:
    status = repository.project_status(project)
    if not status["structural_current"]:
        raise RuntimeError(
            "The registered working tree is newer than the index; run refresh_repository."
        )
    return repository.require_registered_project(project), status


def _source_authority(db: Any, project: str, path: str) -> str:
    row = db.execute(
        "SELECT authority FROM source_authority WHERE project=? AND path=?",
        (project, path),
    ).fetchone()
    return row[0] if row else "unknown"


def _edge_fact(row: Any, direction: str) -> dict[str, Any]:
    return {
        "direction": direction,
        "source": row["source_path"],
        "source_symbol": row["source_qualified_name"] or row["source_name"],
        "relation": row["relation"],
        "target": row["target_name"],
        "target_path": row["target_path"],
        "target_symbol": row["target_qualified_name"],
        "line": row["line"],
        "resolution": row["resolution_confidence"],
    }


def _resource_family(relation: str) -> str:
    if relation.endswith("_table"):
        return "table"
    if relation in {"calls_rpc", "calls_sql"}:
        return "callable"
    return "edge_function"


def _shareable_resource(row: Any) -> bool:
    relation = row["relation"]
    keys = row.keys()
    target_key = "target_name" if "target_name" in keys else "target"
    resolution_key = "resolution_confidence" if "resolution_confidence" in keys else "resolution"
    target = str(row[target_key] or "")
    if relation.endswith("_table"):
        return target.startswith("public.")
    if relation == "calls_sql":
        return target.startswith("public.") and row[resolution_key] == "active_sql"
    if relation == "calls_rpc":
        return target.startswith("public.")
    return relation == "invokes_edge_function" and bool(target)


def _resource_matches_question(target: str, question_terms: set[str]) -> bool:
    target_terms = _question_term_family(target)
    return bool(target_terms & question_terms)


def _question_term_family(question: str) -> set[str]:
    result = set(repository.lexical_tokens(question)) | set(repository.query_terms(question))
    for term in tuple(result):
        result.add(term)
        if term.endswith("ing") and len(term) > 6:
            result.add(term[:-3])
        if term.endswith("al") and len(term) > 6:
            result.add(term[:-2])
        if term.endswith("s") and len(term) > 4:
            result.add(term[:-1])
    return result


def _sql_callable_matches_question(target: str, question_terms: set[str]) -> bool:
    overlap = _question_term_family(target) & question_terms
    return bool(overlap - SQL_GENERIC_TERMS) and len(overlap) >= 2


def _matching_active_sql_functions(
    db: Any,
    project: str,
    question_terms: set[str],
) -> set[str]:
    matches: set[str] = set()
    rows = db.execute(
        """SELECT s.name,s.qualified_name FROM symbols s
           JOIN source_authority a ON a.project=s.project AND a.path=s.path
           WHERE s.project=? AND s.kind='function' AND s.active=1
             AND a.authority='schema_history'""",
        (project,),
    )
    for row in rows:
        symbol_name = row["qualified_name"] or row["name"]
        overlap = _question_term_family(symbol_name) & question_terms
        meaningful = overlap - SQL_GENERIC_TERMS
        if meaningful and len(overlap) >= 2:
            matches.add(symbol_name)
    return matches


def _expand_graph(
    db: Any,
    project: str,
    seeds: list[str],
    max_depth: int = 2,
    question: str = "",
    seed_ranges: dict[str, list[tuple[int, int]]] | None = None,
) -> tuple[dict[str, float], dict[str, list[dict]], list[dict]]:
    scores = {path: 100.0 - rank for rank, path in enumerate(seeds)}
    reasons: dict[str, list[dict]] = defaultdict(list)
    facts: list[dict] = []
    seen_facts: set[tuple] = set()
    frontier = deque((path, 0) for path in seeds)
    expanded: set[tuple[str, int]] = set()
    resources: set[tuple[str, str]] = set()
    question_terms = _question_term_family(question)
    resources.update(
        ("callable", name) for name in _matching_active_sql_functions(db, project, question_terms)
    )
    affinity_cache: dict[str, int] = {}
    seed_targets_cache: dict[str, set[str]] = {}

    def path_affinity(path: str) -> int:
        if not question_terms:
            return 0
        if path not in affinity_cache:
            row = db.execute(
                "SELECT lower(group_concat(content, '\n')) FROM chunks WHERE project=? AND path=?",
                (project, path),
            ).fetchone()
            tokens = repository.lexical_tokens(f"{path}\n{str(row[0] or '') if row else ''}")
            affinity_cache[path] = sum(term in tokens for term in question_terms)
        return affinity_cache[path]

    def seed_targets(path: str) -> set[str]:
        if path in seed_targets_cache:
            return seed_targets_cache[path]
        names: set[str] = set()
        for start, end in (seed_ranges or {}).get(path, []):
            rows = db.execute(
                """SELECT name,qualified_name FROM symbols
                   WHERE project=? AND path=? AND start_line<=? AND end_line>=?""",
                (project, path, end, start),
            ).fetchall()
            for item in rows:
                names.update((item["name"], item["qualified_name"]))
        seed_targets_cache[path] = names
        return names

    def add_fact(row: Any, direction: str, depth: int) -> None:
        key = (
            row["source_path"],
            row["relation"],
            row["target_name"],
            row["target_path"],
            row["line"],
            direction,
        )
        if key in seen_facts:
            return
        seen_facts.add(key)
        fact = _edge_fact(row, direction) | {"depth": depth}
        facts.append(fact)

    while frontier:
        path, depth = frontier.popleft()
        if (path, depth) in expanded or depth >= max_depth:
            continue
        expanded.add((path, depth))
        outgoing = db.execute(
            """SELECT * FROM edges WHERE project=? AND source_path=?
               ORDER BY line,relation,target_name""",
            (project, path),
        ).fetchall()
        incoming = db.execute(
            """SELECT * FROM edges WHERE project=? AND target_path=?
               ORDER BY source_path,line,relation""",
            (project, path),
        ).fetchall()
        for direction, rows in (("outgoing", outgoing), ("incoming", incoming)):
            for row in rows:
                if (
                    direction == "outgoing"
                    and depth == 0
                    and seed_ranges
                    and path in seed_ranges
                    and not any(start <= row["line"] <= end for start, end in seed_ranges[path])
                ):
                    continue
                if direction == "incoming" and depth == 0 and seed_ranges:
                    targets = seed_targets(path)
                    edge_target = row["target_qualified_name"] or row["target_name"]
                    if targets and edge_target not in targets and row["target_name"] not in targets:
                        continue
                relation = row["relation"]
                if relation not in RELATION_WEIGHTS:
                    continue
                source_authority = _source_authority(db, project, path)
                if (
                    question_terms
                    and direction == "incoming"
                    and depth == 0
                    and source_authority == "schema_history"
                    and relation in {"calls_rpc", "calls_sql"}
                    and not _sql_callable_matches_question(row["target_name"], question_terms)
                ):
                    continue
                add_fact(row, direction, depth + 1)
                if relation in RESOURCE_RELATIONS and _shareable_resource(row):
                    source_authority = _source_authority(db, project, row["source_path"])
                    resource_matches = _resource_matches_question(
                        row["target_name"], question_terms
                    )
                    if source_authority == "schema_history" and relation in {
                        "calls_rpc",
                        "calls_sql",
                    }:
                        resource_matches = _sql_callable_matches_question(
                            row["target_name"], question_terms
                        )
                    if (
                        not question_terms
                        or source_authority != "schema_history"
                        or resource_matches
                    ):
                        resources.add((_resource_family(relation), row["target_name"]))
                neighbor = row["target_path"] if direction == "outgoing" else row["source_path"]
                if not neighbor or neighbor == path:
                    continue
                authority = _source_authority(db, project, neighbor)
                if authority not in AUTHORITIES:
                    continue
                if (
                    question_terms
                    and direction == "outgoing"
                    and relation in {"imports", "calls"}
                    and path_affinity(neighbor) == 0
                    and not _resource_matches_question(row["target_name"], question_terms)
                ):
                    continue
                if (
                    question_terms
                    and relation == "calls_sql"
                    and source_authority == "schema_history"
                    and authority == "schema_history"
                    and not _sql_callable_matches_question(row["target_name"], question_terms)
                ):
                    continue
                base = 80.0 if depth == 0 else 62.0
                increment = RELATION_WEIGHTS[relation] / (2 * (depth + 1))
                relevance = min(path_affinity(neighbor) * 3.0, 15.0)
                scores[neighbor] = max(scores.get(neighbor, 0.0), base + increment + relevance)
                reason = {
                    "kind": "graph_edge",
                    "from": path,
                    "relation": relation,
                    "direction": direction,
                    "depth": depth + 1,
                    "resolution": row["resolution_confidence"],
                }
                if reason not in reasons[neighbor]:
                    reasons[neighbor].append(reason)
                if relation not in {"imports"}:
                    frontier.append((neighbor, depth + 1))

    for family, target in sorted(resources):
        relations = (
            ("reads_table", "writes_table", "accesses_table")
            if family == "table"
            else ("calls_rpc", "calls_sql")
            if family == "callable"
            else ("invokes_edge_function",)
        )
        placeholders = ",".join("?" for _ in relations)
        rows = db.execute(
            f"""SELECT * FROM edges WHERE project=?
                  AND relation IN ({placeholders}) AND target_name=?
                  ORDER BY source_path,line LIMIT 120""",
            (project, *relations, target),
        ).fetchall()
        degree_penalty = min(
            max(0.0, math.log2(max(1, len(rows)) / 8.0)) * 6.0,
            30.0,
        )
        for row in rows:
            path = row["source_path"]
            if _source_authority(db, project, path) not in AUTHORITIES:
                continue
            relevance = min(path_affinity(path) * 3.0, 15.0)
            scores[path] = max(scores.get(path, 0.0), 88.0 + relevance - 1.5 * degree_penalty)
            reason = {
                "kind": "shared_resource",
                "resource_family": family,
                "resource": target,
                "consumer_count": len(rows),
            }
            if reason not in reasons[path]:
                reasons[path].append(reason)
            add_fact(row, "shared_resource", 1)
    return scores, reasons, facts


def _symbol_anchors(db: Any, project: str, hits: list[repository.Hit]) -> list[dict]:
    anchors = []
    unique_hits = []
    seen_paths = set()
    for hit in hits:
        if hit.path in seen_paths:
            continue
        seen_paths.add(hit.path)
        unique_hits.append(hit)
        if len(unique_hits) == 8:
            break
    for hit in unique_hits:
        symbols = db.execute(
            """SELECT kind,name,qualified_name,start_line,end_line,active
               FROM symbols WHERE project=? AND path=?
                 AND start_line<=? AND end_line>=?
               ORDER BY active DESC,
                 (min(end_line,?)-max(start_line,?)) ASC,
                 start_line LIMIT 4""",
            (
                project,
                hit.path,
                hit.end_line,
                hit.start_line,
                hit.end_line,
                hit.start_line,
            ),
        ).fetchall()
        anchors.append(
            {
                "path": hit.path,
                "citation": hit.citation,
                "authority": hit.authority,
                "retrieval_score": hit.score,
                "symbols": [dict(row) for row in symbols],
                "excerpt": hit.content[:4_000],
            }
        )
    return anchors


def _sql_lineage(db: Any, project: str, facts: list[dict], question: str) -> dict[str, list[dict]]:
    question_terms = _question_term_family(question)
    names = {
        fact["target"].split(".")[-1]
        for fact in facts
        if fact["relation"] == "calls_rpc"
        or (
            fact["relation"] == "calls_sql"
            and _sql_callable_matches_question(fact["target"], question_terms)
        )
    }
    names.update(
        name.split(".")[-1] for name in _matching_active_sql_functions(db, project, question_terms)
    )
    result: dict[str, list[dict]] = {}
    for name in sorted(names):
        rows = db.execute(
            """SELECT path,kind,name,qualified_name,start_line,end_line,active
               FROM symbols WHERE project=? AND kind='function'
                 AND (lower(name)=lower(?) OR lower(qualified_name)=lower(?))
               ORDER BY path DESC,start_line DESC""",
            (project, name, f"public.{name}"),
        ).fetchall()
        if rows:
            result[name] = [dict(row) for row in rows]
    return result


def map_code_context(
    project: str,
    question: str,
    max_files: int = 16,
    semantic_mode: str = "fast",
) -> dict[str, Any]:
    """Return a compact map; never claim that graph adjacency proves behavior."""
    _repo, snapshot = _snapshot(project)
    hits, mode_used, fallback = repository.retrieve(
        question,
        project,
        max(30, max_files * 3),
        semantic_mode,
    )
    seeds = []
    for hit in hits:
        if hit.path not in seeds:
            seeds.append(hit.path)
        if len(seeds) == 8:
            break
    seed_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for hit in hits:
        if hit.path in seeds and not seed_ranges[hit.path]:
            seed_ranges[hit.path].append((hit.start_line, hit.end_line))
    db = repository.connect()
    scores, reasons, facts = _expand_graph(
        db,
        project,
        seeds,
        max_depth=1,
        question=question,
        seed_ranges=seed_ranges,
    )
    for rank, path in enumerate(seeds):
        scores[path] = max(scores.get(path, 0.0), 120.0 - rank)
        reasons[path].append({"kind": "retrieval_anchor", "rank": rank + 1})

    def keep_path(path: str) -> bool:
        if path in seeds or scores[path] > 90.0:
            return True
        return any(
            reason["kind"] == "shared_resource"
            and reason["consumer_count"] <= 12
            and _resource_matches_question(reason["resource"], _question_term_family(question))
            for reason in reasons[path]
        )

    ranked_paths = [
        path
        for path in sorted(
            scores,
            key=lambda path: (
                -scores[path],
                0 if _source_authority(db, project, path) == "code" else 1,
                path,
            ),
        )
        if keep_path(path)
    ][: max(1, min(max_files, 30))]
    files = [
        {
            "path": path,
            "authority": _source_authority(db, project, path),
            "score": round(scores[path], 3),
            "reasons": reasons[path][:6],
        }
        for path in ranked_paths
    ]
    selected = set(ranked_paths)
    question_terms = _question_term_family(question)
    selected_facts = [
        fact
        for fact in facts
        if fact["source"] in selected or fact.get("target_path") in selected
        if fact["relation"] != "calls" or fact.get("target_path") != fact["source"]
        if fact["relation"] not in {"calls", "imports"} or fact.get("target_path") in selected
        if fact["relation"] not in RESOURCE_RELATIONS or _shareable_resource(fact)
        if fact["resolution"] != "unresolved"
        or _resource_matches_question(fact["target"], question_terms)
    ]
    deduplicated_facts: dict[tuple, dict] = {}
    for fact in selected_facts:
        key = (
            fact["source"],
            fact["relation"],
            fact["target"],
            fact.get("target_path"),
            fact["resolution"],
        )
        if key not in deduplicated_facts:
            deduplicated_facts[key] = fact | {
                "lines": [fact["line"]],
                "occurrences": 1,
            }
        else:
            grouped = deduplicated_facts[key]
            if fact["line"] not in grouped["lines"]:
                grouped["occurrences"] += 1
                grouped["lines"].append(fact["line"])
                grouped["lines"].sort()
            grouped["line"] = grouped["lines"][0]
            grouped["depth"] = min(grouped["depth"], fact["depth"])
    selected_facts = list(deduplicated_facts.values())
    selected_facts.sort(
        key=lambda fact: (
            -max(
                scores.get(fact["source"], 0.0),
                scores.get(fact.get("target_path") or "", 0.0),
            ),
            fact["depth"],
            fact["source"],
            fact["line"],
        )
    )
    selected_facts = selected_facts[:40]
    unresolved = [
        fact
        for fact in selected_facts
        if fact["resolution"] in {"unresolved", "external_or_unresolved"}
    ]
    result = {
        "project": project,
        "question": question,
        "snapshot": {
            "commit": snapshot["commit"],
            "generation_id": snapshot["generation_id"],
            "working_tree_dirty": snapshot["working_tree_dirty"],
        },
        "retrieval": {
            "mode": mode_used,
            "fallback": fallback,
            "anchors": _symbol_anchors(db, project, hits),
        },
        "map": {
            "files": files,
            "relationships": selected_facts,
            "sql_lineage": _sql_lineage(db, project, facts, question),
            "unresolved_relationships": unresolved[:20],
        },
        "limits": {
            "file_level_map_is_not_runtime_proof": True,
            "unresolved_edges": len(unresolved),
            "agent_should_open_decisive_files": True,
            "tests_still_required": True,
        },
    }
    return result


def inspect_symbol(
    project: str,
    name: str,
    path: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    _repo, snapshot = _snapshot(project)
    raw = graph_index.related(
        repository.connect(),
        project,
        name,
        max(1, min(limit, 50)),
        path,
    )
    return {
        "project": project,
        "snapshot": {
            "commit": snapshot["commit"],
            "generation_id": snapshot["generation_id"],
        },
        "query": name,
        "path_filter": path,
        "definitions": [
            {
                key: item.get(key)
                for key in (
                    "path",
                    "kind",
                    "name",
                    "qualified_name",
                    "start_line",
                    "end_line",
                    "signature",
                    "active",
                    "authority",
                )
            }
            for item in raw["symbols"]
        ],
        "suggestions": raw["suggestions"],
        "relationships": [
            {
                key: item.get(key)
                for key in (
                    "source_path",
                    "source_name",
                    "source_qualified_name",
                    "relation",
                    "target_name",
                    "target_path",
                    "target_qualified_name",
                    "line",
                    "resolution_confidence",
                )
            }
            for item in raw["edges"]
        ],
        "guidance": "Open definitions and callers before asserting runtime behavior.",
    }


def _changed_ranges(
    repo: Path, base_ref: str
) -> tuple[list[str], dict[str, list[tuple[int, int]]]]:
    diff = repository.git(repo, "diff", "--unified=0", base_ref, "--", check=False)
    changed: list[str] = []
    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current_path = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            changed.append(current_path)
        elif current_path and line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                length = int(match.group(2) or "1")
                ranges[current_path].append((start, max(start, start + length - 1)))
    for line in repository.git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        check=False,
    ).splitlines():
        if line:
            changed.append(line)
            ranges[line].append((1, 1_000_000_000))
    return list(dict.fromkeys(changed)), ranges


def _changed_symbols(
    db: Any,
    project: str,
    changed_files: list[str],
    ranges: dict[str, list[tuple[int, int]]],
) -> list[dict]:
    symbols = []
    for path in changed_files:
        for row in db.execute(
            """SELECT path,kind,name,qualified_name,start_line,end_line,active
               FROM symbols WHERE project=? AND path=? ORDER BY start_line""",
            (project, path),
        ):
            path_ranges = ranges.get(path) or [(1, 1_000_000_000)]
            if any(
                row["start_line"] <= end and row["end_line"] >= start for start, end in path_ranges
            ):
                symbols.append(dict(row))
    return symbols


def _duplicate_candidates(
    db: Any,
    project: str,
    changed: list[dict],
    changed_files: set[str],
) -> list[dict]:
    candidates = []
    for symbol in changed:
        if symbol["kind"] not in {"function", "method", "class", "component", "hook"}:
            continue
        rows = db.execute(
            """SELECT path,kind,name,qualified_name,start_line,end_line
               FROM symbols WHERE project=? AND lower(name)=lower(?) AND active=1
                 AND path<>? ORDER BY path,start_line LIMIT 12""",
            (project, symbol["name"], symbol["path"]),
        ).fetchall()
        for row in rows:
            if row["path"] in changed_files:
                continue
            candidates.append(
                {
                    "changed_symbol": symbol,
                    "existing_symbol": dict(row),
                    "reason": "same_symbol_name",
                    "proven_duplicate": False,
                }
            )
    return candidates[:30]


def audit_code_change(
    project: str,
    base_ref: str = "HEAD",
    question: str = "",
    max_candidates: int = 30,
) -> dict[str, Any]:
    """Map likely ripple effects around a current diff without judging correctness."""
    repo, snapshot = _snapshot(project)
    changed_files, ranges = _changed_ranges(repo, base_ref)
    changed_set = set(changed_files)
    db = repository.connect()
    changed_symbols = _changed_symbols(db, project, changed_files, ranges)
    _scores, reasons, facts = _expand_graph(
        db,
        project,
        changed_files,
        max_depth=2,
        question=question,
        seed_ranges=ranges,
    )
    ripple_scores: dict[str, float] = defaultdict(float)
    for fact in facts:
        for path in (fact["source"], fact.get("target_path")):
            if not path or path in changed_set:
                continue
            ripple_scores[path] += RELATION_WEIGHTS.get(fact["relation"], 5.0)
    ripple = [
        {
            "path": path,
            "authority": _source_authority(db, project, path),
            "score": round(score, 3),
            "reasons": reasons.get(path, [])[:6],
            "changed": False,
        }
        for path, score in sorted(ripple_scores.items(), key=lambda item: (-item[1], item[0]))[
            : max(1, min(max_candidates, 60))
        ]
    ]
    tests = [item for item in ripple if item["authority"] == "test"]
    production_without_test = (
        bool(changed_files)
        and any(_source_authority(db, project, path) == "code" for path in changed_files)
        and not any(_source_authority(db, project, path) == "test" for path in changed_files)
        and not tests
    )
    result = {
        "project": project,
        "base_ref": base_ref,
        "question": question or None,
        "snapshot": {
            "commit": snapshot["commit"],
            "generation_id": snapshot["generation_id"],
            "working_tree_dirty": snapshot["working_tree_dirty"],
        },
        "diff": {
            "changed_files": changed_files,
            "changed_ranges": {key: value for key, value in ranges.items()},
            "changed_symbols": changed_symbols,
        },
        "review_map": {
            "ripple_candidates_not_in_diff": ripple,
            "related_tests_not_in_diff": tests,
            "relationship_evidence": facts[:200],
            "possible_duplicate_abstractions": _duplicate_candidates(
                db,
                project,
                changed_symbols,
                changed_set,
            ),
            "test_gap_candidate": production_without_test,
        },
        "limits": {
            "candidate_is_not_proven_regression": True,
            "same_name_is_not_proven_duplication": True,
            "deleted_symbols_require_base_snapshot_support": True,
            "direct_diff_and_tests_remain_source_of_truth": True,
        },
    }
    if question:
        result["question_map"] = map_code_context(
            project,
            question,
            min(max_candidates, 20),
            "fast",
        )["map"]
    return result
