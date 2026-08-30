#!/usr/bin/env python3
"""Small stdio MCP surface for repository cartography."""

from __future__ import annotations

import json
import sys
from typing import Any

import cartographer
import repository

PROJECT = {
    "type": "string",
    "minLength": 1,
    "description": "A repository name registered explicitly with the local CLI.",
}
TOOLS = [
    {
        "name": "gbrain_status",
        "description": (
            "Report registered repositories and whether each index matches its working tree."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "map_code_context",
        "description": (
            "Build one compact, question-scoped map of relevant files, symbols, callers, "
            "callees, shared resources, tests, and SQL lineage. Relationships are navigation "
            "evidence, not proof; open decisive files before editing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "question": {"type": "string", "minLength": 2},
                "max_files": {"type": "integer", "minimum": 4, "maximum": 30, "default": 16},
                "semantic_mode": {
                    "type": "string",
                    "enum": ["auto", "fast", "code"],
                    "default": "fast",
                },
            },
            "required": ["project", "question"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "inspect_symbol",
        "description": (
            "Return exact definitions plus direct graph relationships for a named symbol. "
            "SQL definitions include active/superseded state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "name": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            },
            "required": ["project", "name"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "audit_code_change",
        "description": (
            "Inspect the current Git diff and report related consumers, tests, resources, "
            "and possible duplicate abstractions that are not in the diff. Candidates are "
            "review prompts, never automatic bug claims. Refresh the index after editing first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "base_ref": {"type": "string", "default": "HEAD"},
                "question": {"type": "string", "default": ""},
                "max_candidates": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 60,
                    "default": 30,
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "refresh_repository",
        "description": (
            "Refresh the local text and structural index from one explicitly registered "
            "working tree. Never edits the repository and never starts an embedding model."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": PROJECT},
            "required": ["project"],
            "additionalProperties": False,
        },
    },
]


def call_tool(name: str, arguments: dict[str, Any]) -> object:
    if name == "gbrain_status":
        return repository.status()
    project = arguments.get("project")
    if not isinstance(project, str):
        raise ValueError("project must be a registered repository name")
    repository.require_registered_project(project)
    if name == "map_code_context":
        return cartographer.map_code_context(
            project,
            arguments["question"],
            int(arguments.get("max_files", 16)),
            str(arguments.get("semantic_mode", "fast")),
        )
    if name == "inspect_symbol":
        return cartographer.inspect_symbol(
            project,
            arguments["name"],
            arguments.get("path"),
            int(arguments.get("limit", 20)),
        )
    if name == "audit_code_change":
        return cartographer.audit_code_change(
            project,
            str(arguments.get("base_ref", "HEAD")),
            str(arguments.get("question", "")),
            int(arguments.get("max_candidates", 30)),
        )
    if name == "refresh_repository":
        return repository.index_project(project, force=True)
    raise ValueError(f"Unknown tool: {name}")


def _tool_result(value: object, is_error: bool = False) -> dict:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _respond(message_id: object, result: object = None, error: dict | None = None) -> None:
    message = {"jsonrpc": "2.0", "id": message_id}
    message["error" if error else "result"] = error if error else result
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        protocol = message.get("params", {}).get("protocolVersion", "2025-06-18")
        _respond(
            message_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gbrain-code", "version": "0.1.0"},
            },
        )
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        _respond(message_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = message.get("params", {})
        try:
            value = call_tool(params.get("name", ""), params.get("arguments") or {})
            _respond(message_id, _tool_result(value))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            _respond(message_id, _tool_result(str(exc), is_error=True))
    else:
        _respond(message_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    for line in sys.stdin:
        try:
            handle(json.loads(line))
        except json.JSONDecodeError as exc:
            _respond(None, error={"code": -32700, "message": str(exc)})


if __name__ == "__main__":
    main()
