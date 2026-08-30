#!/usr/bin/env python3
"""Evaluate lexical anchors or GBrain maps against normalized file-level gold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cartographer
import repository


def ranked_paths(sample: dict, method: str, k: int) -> list[str]:
    if method == "lexical":
        hits = repository.keyword_search(sample["question"], sample["project"], k * 4)
        return list(dict.fromkeys(hit.path for hit in hits))[:k]
    result = cartographer.map_code_context(
        sample["project"],
        sample["question"],
        k,
        "fast",
    )
    return [item["path"] for item in result["map"]["files"]][:k]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--method", choices=("lexical", "map"), required=True)
    parser.add_argument("--k", type=int, default=20)
    args = parser.parse_args()
    samples = [
        json.loads(line) for line in args.dataset.read_text("utf-8").splitlines() if line.strip()
    ]
    reciprocal_ranks = []
    recalls = []
    all_gold = []
    details = []
    for sample in samples:
        ranked = ranked_paths(sample, args.method, args.k)
        gold = set(sample["gold_paths"])
        hits = [index + 1 for index, path in enumerate(ranked) if path in gold]
        recall = len(set(ranked) & gold) / len(gold) if gold else 1.0
        reciprocal_rank = 1.0 / min(hits) if hits else 0.0
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        all_gold.append(gold.issubset(ranked))
        details.append(
            {
                "id": sample.get("id"),
                "ranked_paths": ranked,
                "gold_paths": sorted(gold),
                "recall": recall,
                "reciprocal_rank": reciprocal_rank,
            }
        )
    count = len(samples)
    print(
        json.dumps(
            {
                "method": args.method,
                "k": args.k,
                "samples": count,
                "recall_at_k": sum(recalls) / count if count else 0.0,
                "mrr": sum(reciprocal_ranks) / count if count else 0.0,
                "all_gold_recall": sum(all_gold) / count if count else 0.0,
                "details": details,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
