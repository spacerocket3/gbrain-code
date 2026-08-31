#!/usr/bin/env python3
"""Retrospective co-change pilot using only each commit's parent snapshot.

This is a diagnostic, not a correctness benchmark: co-changed files can be
incidental.  Its purpose is to compare graph formulations on repositories with
cross-layer and migration relationships that generic retrieval suites may not
contain.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import repository
from experiments.reactive_repository_state import (
    hybrid_ripple,
    impact_distribution,
    impact_obligations,
    lexical_ripple,
    mapped_graph_ripple,
    reachable_file_distances,
    static_graph_ripple,
)
from project_registry import ProjectRegistry

SUPPORTED_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".py", ".sql"})
TEST_MARKERS = ("/test/", "/tests/", "__tests__", ".test.", ".spec.", "_test.")
METHODS = (
    "lexical",
    "static",
    "map",
    "ppr_topology",
    "ppr_typed",
    "ppr_resources",
    "reactive",
    "delta_field",
    "hybrid",
)
HUNK_HEADER = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+\d+(?:,\d+)?\s+@@", re.MULTILINE)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def is_test(path: str) -> bool:
    lower = f"/{path.casefold()}"
    return any(marker in lower for marker in TEST_MARKERS)


def architectural_layer(path: str) -> str:
    """Classify coarse layers without treating .ts and .tsx as different ones."""
    if is_test(path):
        return "test"
    suffix = Path(path).suffix.casefold()
    if suffix == ".sql":
        return "database"
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        return "application"
    return suffix or "unknown"


def changed_supported_files(repo: Path, commit: str) -> list[str]:
    rows = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", commit)
    result = []
    for row in rows.splitlines():
        fields = row.split("\t")
        if len(fields) != 2 or fields[0] != "M":
            continue
        path = fields[1]
        if Path(path).suffix.casefold() in SUPPORTED_SUFFIXES:
            result.append(path)
    return result


def metrics(ranked: list[str], gold: set[str]) -> dict:
    positions = [index + 1 for index, path in enumerate(ranked) if path in gold]
    return {
        "recall": len(set(ranked) & gold) / len(gold) if gold else 1.0,
        "reciprocal_rank": 1.0 / min(positions) if positions else 0.0,
    }


def diff_ranges(path: str, patch: str) -> dict[str, list[tuple[int, int]]]:
    ranges = []
    for start_text, count_text in HUNK_HEADER.findall(patch):
        start = int(start_text)
        count = int(count_text or "1")
        ranges.append((start, start + max(1, count) - 1))
    return {path: ranges} if ranges else {}


def aggregate(rows: list[dict]) -> dict:
    return {
        method: {
            "samples": len(rows),
            "recall_at_k": sum(row["methods"][method]["recall"] for row in rows)
            / len(rows)
            if rows
            else 0.0,
            "mrr": sum(row["methods"][method]["reciprocal_rank"] for row in rows)
            / len(rows)
            if rows
            else 0.0,
        }
        for method in METHODS
    }


def aggregate_translation(rows: list[dict]) -> dict:
    emitted = 0
    matched = 0
    candidates = 0
    samples_with_obligations = 0
    gold_files = 0
    for row in rows:
        paths = {
            item["candidate_path"]
            for item in row["diagnostics"]["impact_field"]["obligations"]
        }
        emitted += len(paths)
        matched += len(paths & set(row["gold_paths"]))
        candidates += len(row["methods"]["delta_field"]["ranked_paths"])
        samples_with_obligations += bool(paths)
        gold_files += len(row["gold_paths"])
    return {
        "samples": len(rows),
        "samples_with_obligations": samples_with_obligations,
        "delta_core_candidates_examined": candidates,
        "candidate_files_translated": emitted,
        "co_changed_files_translated": matched,
        "gold_files": gold_files,
        "warning": "Co-change is not proof that an obligation is causally correct.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--rev", default="HEAD")
    parser.add_argument("--max-commits", type=int, default=30)
    parser.add_argument("--max-samples", type=int, default=12)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--details", type=Path)
    args = parser.parse_args()

    source_repo = args.repository.resolve()
    commits = git(
        source_repo,
        "rev-list",
        "--first-parent",
        f"--max-count={args.max_commits}",
        args.rev,
    ).splitlines()
    candidates = []
    for commit in commits:
        try:
            parent = git(source_repo, "rev-parse", f"{commit}^")
        except RuntimeError:
            continue
        changed = changed_supported_files(source_repo, commit)
        if not 2 <= len(changed) <= 8:
            continue
        anchors = [path for path in changed if not is_test(path)]
        if not anchors:
            continue
        anchor = anchors[0]
        gold = [path for path in changed if path != anchor]
        candidates.append((commit, parent, anchor, gold))
        if len(candidates) >= args.max_samples:
            break

    details = []
    with tempfile.TemporaryDirectory(prefix="gbrain-history-") as temporary:
        root = Path(temporary)
        repository.DB_PATH = root / "index.sqlite3"
        repository.REPOS = ProjectRegistry(root / "projects.json")
        for number, (commit, parent, anchor, gold_paths) in enumerate(candidates, start=1):
            snapshot = root / "repos" / f"snapshot-{number}"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--shared",
                    "--no-checkout",
                    str(source_repo),
                    str(snapshot),
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(snapshot), "checkout", "-q", "--detach", parent],
                check=True,
            )
            if not (snapshot / anchor).is_file():
                continue
            gold = {path for path in gold_paths if (snapshot / path).is_file()}
            if not gold:
                continue
            project = f"history-{number}"
            repository.REPOS.add(project, snapshot, f"{source_repo.name}@{parent}")
            status = repository.index_project(project)
            message = git(source_repo, "show", "-s", "--format=%B", commit)
            anchor_diff = git(
                source_repo,
                "diff",
                "--unified=0",
                parent,
                commit,
                "--",
                anchor,
            )
            question = f"{message}\nAnchor file: {anchor}\n{anchor_diff[:16000]}"
            seeds = [anchor]
            seed_ranges = diff_ranges(anchor, anchor_diff)
            reachable = reachable_file_distances(project, seeds, max_hops=5)

            ppr_rankings = {}
            for variant in ("topology", "typed", "resources", "temporal"):
                ppr_rankings[variant] = [
                    item["path"]
                    for item in impact_distribution(
                        project,
                        seeds,
                        limit=args.k,
                        delta_text=question,
                        graph_variant=variant,
                        seed_ranges=seed_ranges,
                    )["candidates"]
                ]
            field = impact_obligations(
                project,
                seeds,
                limit=args.k,
                obligation_limit=args.k,
                delta_text=question,
                seed_ranges=seed_ranges,
            )["impact_field"]
            field_paths = [item["path"] for item in field["delta_core_candidates"]]
            rankings = {
                "lexical": lexical_ripple(project, seeds, args.k, question),
                "static": static_graph_ripple(
                    project, seeds, args.k, question, seed_ranges
                ),
                "map": mapped_graph_ripple(project, seeds, question, args.k),
                "ppr_topology": ppr_rankings["topology"],
                "ppr_typed": ppr_rankings["typed"],
                "ppr_resources": ppr_rankings["resources"],
                "reactive": ppr_rankings["temporal"],
                "delta_field": field_paths,
                "hybrid": hybrid_ripple(
                    project,
                    seeds,
                    question,
                    args.k,
                    seed_ranges=seed_ranges,
                ),
            }
            anchor_layer = architectural_layer(anchor)
            gold_layers = {architectural_layer(path) for path in gold}
            cross_layer = any(layer != anchor_layer for layer in gold_layers)
            details.append(
                {
                    "commit": commit,
                    "parent": parent,
                    "subject": message.splitlines()[0] if message else "",
                    "seed_paths": seeds,
                    "gold_paths": sorted(gold),
                    "seed_ranges": seed_ranges,
                    "cross_layer": cross_layer,
                    "layers": {
                        "anchor": anchor_layer,
                        "gold": sorted(gold_layers),
                    },
                    "diagnostics": {
                        "reachable_gold_paths": {
                            path: reachable[path] for path in sorted(gold) if path in reachable
                        },
                        "impact_field": {
                            "obligations": [
                                {
                                    "candidate_path": item["candidate_path"],
                                    "entry_relationship": item["entry_relationship"],
                                    "local_value_path": item["local_value_path"],
                                    "sink_context": item["sink_context"],
                                }
                                for item in field["obligations"]
                            ],
                            "candidates_without_supported_sink": [
                                item["path"]
                                for item in field[
                                    "ranked_candidates_without_supported_sink"
                                ]
                            ],
                        },
                    },
                    "methods": {
                        method: {
                            "ranked_paths": rankings[method],
                            **metrics(rankings[method], gold),
                        }
                        for method in METHODS
                    },
                }
            )
            print(
                f"[{number}/{len(candidates)}] {commit[:10]} files={status['files']} "
                f"edges={status['structure']['edges']} gold={len(gold)}",
                file=sys.stderr,
            )

    lexical_miss = [row for row in details if row["methods"]["lexical"]["recall"] == 0]
    reachable = [row for row in details if row["diagnostics"]["reachable_gold_paths"]]
    relational_opportunity = [
        row for row in lexical_miss if row["diagnostics"]["reachable_gold_paths"]
    ]
    cross_layer = [row for row in details if row["cross_layer"]]
    summary = {
        "benchmark": "retrospective parent-snapshot co-change pilot",
        "repository": str(source_repo),
        "scope": {
            "samples": len(details),
            "k": args.k,
            "warning": "Co-change is noisy evidence and does not prove required impact.",
        },
        "all": aggregate(details),
        "translation_diagnostic": aggregate_translation(details),
        "strata": {
            "lexical_miss": aggregate(lexical_miss),
            "structurally_reachable": aggregate(reachable),
            "relational_opportunity": aggregate(relational_opportunity),
            "cross_layer": aggregate(cross_layer),
        },
    }
    if args.details:
        args.details.parent.mkdir(parents=True, exist_ok=True)
        args.details.write_text(
            json.dumps({**summary, "details": details}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary["details_path"] = str(args.details)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
