#!/usr/bin/env python3
"""Run an external ARB edit2ripple pilot against frozen base-commit corpora."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import repository
from experiments.reactive_repository_state import (
    hybrid_ripple,
    impact_distribution,
    lexical_ripple,
    mapped_graph_ripple,
    reachable_file_distances,
    static_graph_ripple,
)
from project_registry import ProjectRegistry

HUNK_HEADER = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+\d+(?:,\d+)?\s+@@", re.MULTILINE)


def _safe_path(value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path


def materialize_snapshot(chunks_path: Path, destination: Path) -> int:
    """Rebuild the benchmark's frozen repository from its canonical file rows."""
    files = 0
    for line in chunks_path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") != "file":
            continue
        relative = _safe_path(str(row.get("path", "")))
        if relative is None:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(row.get("text", "")), encoding="utf-8")
        files += 1
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "add", "-f", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=GBrain Benchmark",
            "-c",
            "user.email=benchmark@example.invalid",
            "commit",
            "-qm",
            "frozen ARB snapshot",
        ],
        check=True,
    )
    return files


def _query(sample: dict) -> str:
    query = sample.get("query") or {}
    anchors = " ".join(sample.get("gold", {}).get("given_files", []))
    return (
        f"{query.get('intent', '')}\nAnchor file: {anchors}\n"
        f"{query.get('anchor_diff', '')}"
    ).strip()


def _seed_ranges(sample: dict) -> dict[str, list[tuple[int, int]]]:
    query = sample.get("query") or {}
    anchor = str(query.get("anchor_file", ""))
    ranges = []
    for start_text, count_text in HUNK_HEADER.findall(str(query.get("anchor_diff", ""))):
        start = int(start_text)
        count = int(count_text or "1")
        ranges.append((start, start + max(1, count) - 1))
    return {anchor: ranges} if anchor and ranges else {}


def _metrics(ranked: list[str], gold: set[str]) -> dict:
    positions = [index + 1 for index, path in enumerate(ranked) if path in gold]
    return {
        "recall": len(set(ranked) & gold) / len(gold) if gold else 1.0,
        "reciprocal_rank": 1.0 / min(positions) if positions else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--details", type=Path)
    args = parser.parse_args()

    samples = [
        json.loads(line) for line in args.samples.read_text("utf-8").splitlines() if line.strip()
    ]
    if args.repo:
        allowed = set(args.repo)
        samples = [sample for sample in samples if sample.get("repo") in allowed]
    if args.limit is not None:
        samples = samples[: args.limit]

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sample in samples:
        grouped[(sample["repo"], sample["base_commit"])].append(sample)

    methods = ("lexical", "static", "map", "reactive", "hybrid")
    details = []
    with tempfile.TemporaryDirectory(prefix="gbrain-arb-") as temporary:
        root = Path(temporary)
        repository.DB_PATH = root / "index.sqlite3"
        repository.REPOS = ProjectRegistry(root / "projects.json")
        for snapshot_number, ((repo_name, commit), snapshot_samples) in enumerate(
            sorted(grouped.items()), start=1
        ):
            corpus_name = repo_name.replace("/", "__")
            chunks_path = args.corpus / corpus_name / f"{commit}.chunks.jsonl"
            if not chunks_path.exists():
                raise FileNotFoundError(f"missing ARB corpus snapshot: {chunks_path}")
            snapshot_root = root / "repos" / f"snapshot-{snapshot_number}"
            snapshot_root.mkdir(parents=True)
            file_count = materialize_snapshot(chunks_path, snapshot_root)
            project = f"arb-{snapshot_number}"
            repository.REPOS.add(project, snapshot_root, f"{repo_name}@{commit}")
            status = repository.index_project(project)
            print(
                f"[{snapshot_number}/{len(grouped)}] {repo_name}@{commit[:10]} "
                f"files={file_count} edges={status['structure']['edges']}",
                file=sys.stderr,
            )
            for sample in snapshot_samples:
                seeds = list(sample["gold"]["given_files"])
                gold = set(sample["gold"]["files"])
                question = _query(sample)
                seed_ranges = _seed_ranges(sample)
                reachable = reachable_file_distances(project, seeds, max_hops=5)
                rankings = {
                    "lexical": lexical_ripple(project, seeds, args.k, question),
                    "static": static_graph_ripple(
                        project, seeds, args.k, question, seed_ranges
                    ),
                    "map": mapped_graph_ripple(project, seeds, question, args.k),
                    "reactive": [
                        item["path"]
                        for item in impact_distribution(
                            project,
                            seeds,
                            limit=args.k,
                            delta_text=question,
                            seed_ranges=seed_ranges,
                        )["candidates"]
                    ],
                    "hybrid": hybrid_ripple(
                        project,
                        seeds,
                        question,
                        args.k,
                        seed_ranges=seed_ranges,
                    ),
                }
                details.append(
                    {
                        "id": sample["id"],
                        "repo": repo_name,
                        "base_commit": commit,
                        "seed_paths": seeds,
                        "gold_paths": sorted(gold),
                        "seed_ranges": seed_ranges,
                        "diagnostics": {
                            "reachable_gold_paths": {
                                path: reachable[path] for path in sorted(gold) if path in reachable
                            },
                            "all_gold_structurally_reachable": gold.issubset(reachable),
                        },
                        "methods": {
                            method: {
                                "ranked_paths": rankings[method],
                                **_metrics(rankings[method], gold),
                            }
                            for method in methods
                        },
                    }
                )

    count = len(details)
    def stratum(method: str, rows: list[dict]) -> dict:
        return {
            "samples": len(rows),
            "recall_at_k": sum(item["methods"][method]["recall"] for item in rows)
            / len(rows)
            if rows
            else 0.0,
            "mrr": sum(item["methods"][method]["reciprocal_rank"] for item in rows)
            / len(rows)
            if rows
            else 0.0,
        }

    lexical_misses = [
        item for item in details if item["methods"]["lexical"]["recall"] == 0.0
    ]
    structurally_reachable = [
        item for item in details if item["diagnostics"]["reachable_gold_paths"]
    ]
    relational_opportunities = [
        item
        for item in lexical_misses
        if item["diagnostics"]["reachable_gold_paths"]
    ]
    summary = {
        "benchmark": "Agent Retrieval Bench v2_edit2ripple",
        "scope": {
            "samples": count,
            "repositories": sorted({item["repo"] for item in details}),
            "k": args.k,
            "note": "Graph extraction currently supports TypeScript/JavaScript and Python.",
        },
        "methods": {
            method: {
                "recall_at_k": sum(item["methods"][method]["recall"] for item in details)
                / count
                if count
                else 0.0,
                "mrr": sum(
                    item["methods"][method]["reciprocal_rank"] for item in details
                )
                / count
                if count
                else 0.0,
            }
            for method in methods
        },
        "strata": {
            "lexical_miss": {
                method: stratum(method, lexical_misses) for method in methods
            },
            "structurally_reachable": {
                method: stratum(method, structurally_reachable) for method in methods
            },
            "relational_opportunity": {
                method: stratum(method, relational_opportunities) for method in methods
            },
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
