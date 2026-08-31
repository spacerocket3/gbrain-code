#!/usr/bin/env python3
"""Compare lexical, static-map and reactive edit-to-ripple ranking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.reactive_repository_state import (
    hybrid_ripple,
    impact_distribution,
    lexical_ripple,
    mapped_graph_ripple,
    static_graph_ripple,
)


def ranked_paths(sample: dict, method: str, k: int) -> list[str]:
    project = sample["project"]
    seeds = sample["seed_paths"]
    question = sample.get("question", "")
    if method == "lexical":
        return lexical_ripple(project, seeds, k, question)
    if method == "static":
        return static_graph_ripple(project, seeds, k, question)
    if method == "map":
        return mapped_graph_ripple(project, seeds, question, k)
    if method == "hybrid":
        return hybrid_ripple(project, seeds, question, k)
    if method.startswith("ppr-"):
        variant = method.removeprefix("ppr-")
        return [
            item["path"]
            for item in impact_distribution(
                project,
                seeds,
                limit=k,
                delta_text=question,
                graph_variant=variant,
            )["candidates"]
        ]
    return [
        item["path"]
        for item in impact_distribution(project, seeds, limit=k, delta_text=question)[
            "candidates"
        ]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--method",
        choices=(
            "lexical",
            "static",
            "map",
            "ppr-topology",
            "ppr-typed",
            "ppr-resources",
            "reactive",
            "hybrid",
        ),
        required=True,
    )
    parser.add_argument("--k", type=int, default=20)
    args = parser.parse_args()
    samples = [
        json.loads(line) for line in args.dataset.read_text("utf-8").splitlines() if line.strip()
    ]
    details = []
    for sample in samples:
        ranked = ranked_paths(sample, args.method, args.k)
        gold = set(sample["gold_paths"])
        positions = [index + 1 for index, path in enumerate(ranked) if path in gold]
        details.append(
            {
                "id": sample.get("id"),
                "ranked_paths": ranked,
                "gold_paths": sorted(gold),
                "recall": len(set(ranked) & gold) / len(gold) if gold else 1.0,
                "reciprocal_rank": 1.0 / min(positions) if positions else 0.0,
            }
        )
    count = len(details)
    print(
        json.dumps(
            {
                "method": args.method,
                "k": args.k,
                "samples": count,
                "recall_at_k": sum(item["recall"] for item in details) / count if count else 0.0,
                "mrr": sum(item["reciprocal_rank"] for item in details) / count
                if count
                else 0.0,
                "details": details,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
