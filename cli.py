#!/usr/bin/env python3
"""Command-line interface for GBrain Code."""

from __future__ import annotations

import argparse
import json

import cartographer
import repository


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    project = commands.add_parser("project", help="Manage explicitly registered repositories")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    add = project_commands.add_parser("add")
    add.add_argument("name")
    add.add_argument("path")
    add.add_argument("--description", default="")
    project_commands.add_parser("list")
    remove = project_commands.add_parser("remove")
    remove.add_argument("name")

    index = commands.add_parser("index", help="Refresh text and structural evidence")
    index.add_argument("project")
    index.add_argument("--force", action="store_true")
    embed = commands.add_parser("embed", help="Optionally create semantic embeddings")
    embed.add_argument("project")
    embed.add_argument("--batch-size", type=int, default=8)
    commands.add_parser("status")

    mapping = commands.add_parser("map", help="Build a question-scoped repository map")
    mapping.add_argument("project")
    mapping.add_argument("question")
    mapping.add_argument("--max-files", type=int, default=16)
    mapping.add_argument("--semantic-mode", choices=("auto", "fast", "code"), default="fast")

    inspect = commands.add_parser("inspect", help="Inspect one symbol and its relationships")
    inspect.add_argument("project")
    inspect.add_argument("name")
    inspect.add_argument("--path")
    inspect.add_argument("--limit", type=int, default=20)

    audit = commands.add_parser("audit", help="Map ripple effects around the current diff")
    audit.add_argument("project")
    audit.add_argument("--base-ref", default="HEAD")
    audit.add_argument("--question", default="")
    audit.add_argument("--max-candidates", type=int, default=30)
    args = parser.parse_args()

    if args.command == "project":
        if args.project_command == "add":
            result = repository.register_project(args.name, args.path, args.description)
        elif args.project_command == "remove":
            result = repository.unregister_project(args.name)
        else:
            result = [
                {"project": name, **entry} for name, entry in repository.REPOS.entries().items()
            ]
    elif args.command == "index":
        result = repository.index_project(args.project, args.force)
    elif args.command == "embed":
        result = repository.embed_project(args.project, args.batch_size)
    elif args.command == "status":
        result = repository.status()
    elif args.command == "map":
        result = cartographer.map_code_context(
            args.project,
            args.question,
            args.max_files,
            args.semantic_mode,
        )
    elif args.command == "inspect":
        result = cartographer.inspect_symbol(args.project, args.name, args.path, args.limit)
    else:
        result = cartographer.audit_code_change(
            args.project,
            args.base_ref,
            args.question,
            args.max_candidates,
        )
    _print(result)


if __name__ == "__main__":
    main()
