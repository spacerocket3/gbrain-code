"""Structural symbols, dependencies, lineage, and source authority."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
import sqlite3
import subprocess
import sysconfig
from pathlib import Path

TS_EXTENSIONS = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
SQL_EXTENSIONS = {".sql"}
PYTHON_EXTENSIONS = {".py"}
EXTRACTOR_VERSION = "ts-compiler-python-ast-sql-v1"
SQL_DEFINITION_PATTERNS = [
    ("function", re.compile(r"(?is)\bcreate\s+(?:or\s+replace\s+)?function\s+([\w.]+)\s*\(")),
    ("table", re.compile(r"(?is)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([\w.]+)")),
    (
        "view",
        re.compile(r"(?is)\bcreate\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+([\w.]+)"),
    ),
    ("policy", re.compile(r'(?is)\bcreate\s+policy\s+(?:"([^"]+)"|([\w.]+))\s+on\s+([\w.]+)')),
    ("trigger", re.compile(r"(?is)\bcreate\s+(?:or\s+replace\s+)?trigger\s+([\w.]+)")),
    ("type", re.compile(r"(?is)\bcreate\s+type\s+([\w.]+)")),
]
SQL_CALL = re.compile(r"\b(public\.)?([a-zA-Z_][\w]*)\s*\(")
SQL_TABLE_REF = re.compile(r"(?is)\b(from|join|update|into|delete\s+from)\s+([\w.]+)")
RPC_CALL = re.compile(r"\.rpc\(\s*['\"]([^'\"]+)['\"]")
TABLE_CALL = re.compile(r"\.from\(\s*['\"]([^'\"]+)['\"]")
FUNCTION_INVOKE = re.compile(r"\.functions\.invoke\(\s*['\"]([^'\"]+)['\"]")
SQL_CALL_STOPWORDS = {
    "and",
    "any",
    "array",
    "as",
    "avg",
    "bool_or",
    "case",
    "check",
    "coalesce",
    "conflict",
    "count",
    "date_trunc",
    "exists",
    "extract",
    "filter",
    "from",
    "greatest",
    "if",
    "in",
    "json_agg",
    "json_build_object",
    "jsonb_agg",
    "jsonb_build_object",
    "key",
    "lateral",
    "least",
    "make_interval",
    "max",
    "min",
    "nullif",
    "numeric",
    "or",
    "over",
    "return",
    "round",
    "select",
    "sum",
    "table",
    "unique",
    "using",
    "values",
    "when",
    "where",
}
SQL_TABLE_STOPWORDS = {
    "anon",
    "lateral",
    "of",
    "on",
    "or",
    "public",
    "set",
    "to",
    "using",
}


def _ts_script_path() -> Path:
    local = Path(__file__).with_name("ts_structure.mjs")
    if local.exists():
        return local
    installed = Path(sysconfig.get_path("data")) / "share" / "gbrain-code" / "ts_structure.mjs"
    if installed.exists():
        return installed
    raise RuntimeError("ts_structure.mjs is missing from the GBrain Code installation")


def extractor_fingerprint() -> str:
    state = hashlib.sha256()
    for path in (Path(__file__), _ts_script_path()):
        raw = path.read_bytes()
        state.update(path.name.encode())
        state.update(len(raw).to_bytes(8, "big"))
        state.update(raw)
    return f"{EXTRACTOR_VERSION}:{state.hexdigest()[:20]}"


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_authority (
            project TEXT NOT NULL, path TEXT NOT NULL, authority TEXT NOT NULL,
            reason TEXT NOT NULL, commit_hash TEXT NOT NULL,
            PRIMARY KEY(project,path)
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, project TEXT NOT NULL, path TEXT NOT NULL,
            kind TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT NOT NULL,
            start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
            signature TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS symbols_lookup_idx
          ON symbols(project,name,kind,active);
        CREATE INDEX IF NOT EXISTS symbols_path_idx ON symbols(project,path);
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, project TEXT NOT NULL, source_path TEXT NOT NULL,
            source_name TEXT NOT NULL DEFAULT '', relation TEXT NOT NULL,
            target_name TEXT NOT NULL, target_path TEXT, line INTEGER NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS edges_target_idx
          ON edges(project,target_name,relation);
        CREATE INDEX IF NOT EXISTS edges_source_idx
          ON edges(project,source_path,relation);
        CREATE TABLE IF NOT EXISTS structure_snapshots (
            project TEXT PRIMARY KEY,
            commit_hash TEXT NOT NULL,
            graph_digest TEXT NOT NULL,
            content_digest TEXT NOT NULL DEFAULT '',
            extractor_version TEXT NOT NULL DEFAULT '',
            generation_id TEXT NOT NULL DEFAULT '',
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    symbol_columns = {row[1] for row in db.execute("PRAGMA table_info(symbols)")}
    if "commit_hash" not in symbol_columns:
        db.execute("ALTER TABLE symbols ADD COLUMN commit_hash TEXT NOT NULL DEFAULT ''")
    if "generation_id" not in symbol_columns:
        db.execute("ALTER TABLE symbols ADD COLUMN generation_id TEXT NOT NULL DEFAULT ''")
    edge_columns = {row[1] for row in db.execute("PRAGMA table_info(edges)")}
    for name, declaration in (
        ("source_qualified_name", "TEXT NOT NULL DEFAULT ''"),
        ("target_qualified_name", "TEXT"),
        ("resolution_confidence", "TEXT NOT NULL DEFAULT 'unresolved'"),
        ("commit_hash", "TEXT NOT NULL DEFAULT ''"),
        ("generation_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in edge_columns:
            db.execute(f"ALTER TABLE edges ADD COLUMN {name} {declaration}")
    authority_columns = {row[1] for row in db.execute("PRAGMA table_info(source_authority)")}
    if "generation_id" not in authority_columns:
        db.execute("ALTER TABLE source_authority ADD COLUMN generation_id TEXT NOT NULL DEFAULT ''")
    snapshot_columns = {row[1] for row in db.execute("PRAGMA table_info(structure_snapshots)")}
    for name in ("content_digest", "extractor_version", "generation_id"):
        if name not in snapshot_columns:
            db.execute(
                f"ALTER TABLE structure_snapshots ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
            )


def classify_source(path: str) -> tuple[str, str]:
    lower = path.lower()
    parts = tuple(part for part in lower.split("/") if part)
    name = parts[-1] if parts else lower
    if "/history/" in lower or lower.startswith("docs/history/"):
        return "historical", "File under historical documentation"
    if lower.endswith("src/integrations/supabase/types.ts"):
        return "generated", "Types generated from the Supabase schema"
    if lower.startswith("migrations/") or "/migrations/" in lower:
        return "schema_history", "SQL migration; only the latest same-name definition is active"
    if lower.endswith("agents.md"):
        return "canonical", "Explicit repository instructions"
    if lower.startswith("docs/architecture/") or lower.startswith("docs/reference/"):
        return "canonical", "Architecture or reference document"
    if lower.startswith("docs/"):
        return "active", "Active project documentation"
    if name in {
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "makefile",
    } or name.startswith(("tsconfig", "jest.config", "vitest.config", "vite.config")):
        return "config", "Executable configuration or project manifest"
    if name in {"readme.md", "contributing.md", "code_of_conduct.md"}:
        return "active", "Active documentation at the repository root"
    if name in {"changelog.md", "history.md", "changes.md"}:
        return "historical", "Repository change history"
    if name.endswith((".md", ".rst")) or (parts and parts[0] == ".github"):
        return "active", "Documentation or collaboration metadata"
    if any(part in {"test", "tests", "spec", "specs", "__tests__"} for part in parts[:-1]) or any(
        marker in name for marker in (".test.", ".spec.", "_test.", "_spec.")
    ):
        return "test", "Executable test that fixes expected behavior"
    return "code", "Current source code"


def add_symbol(
    db,
    project,
    path,
    kind,
    name,
    start,
    end,
    signature="",
    metadata=None,
    qualified_name=None,
    commit_hash="",
    generation_id="",
):
    if not name:
        return
    db.execute(
        """INSERT INTO symbols(
               project,path,kind,name,qualified_name,start_line,end_line,
               signature,metadata,commit_hash,generation_id
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            project,
            path,
            kind,
            name,
            qualified_name or name,
            start,
            end,
            signature[:500],
            json.dumps(metadata or {}, ensure_ascii=False),
            commit_hash,
            generation_id,
        ),
    )


def add_edge(
    db,
    project,
    path,
    source,
    relation,
    target,
    line,
    target_path=None,
    metadata=None,
    source_qualified_name="",
    target_qualified_name=None,
    confidence="unresolved",
    commit_hash="",
    generation_id="",
):
    if not target:
        return
    db.execute(
        """INSERT INTO edges(
               project,source_path,source_name,relation,target_name,target_path,
               line,metadata,source_qualified_name,target_qualified_name,
               resolution_confidence,commit_hash,generation_id
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            project,
            path,
            source,
            relation,
            target,
            target_path,
            line,
            json.dumps(metadata or {}, ensure_ascii=False),
            source_qualified_name or source,
            target_qualified_name,
            confidence,
            commit_hash,
            generation_id,
        ),
    )


def extract_ts_compiler(
    db: sqlite3.Connection,
    project: str,
    repo: Path,
    commit: str,
    generation_id: str = "",
) -> tuple[int, dict]:
    """Extract TS/JS structure in one stable pass using the TypeScript compiler."""
    script = _ts_script_path()
    result = subprocess.run(
        ["node", str(script), str(repo), project],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    for symbol in payload["symbols"]:
        add_symbol(
            db,
            project,
            symbol["path"],
            symbol["kind"],
            symbol["name"],
            symbol["start_line"],
            symbol["end_line"],
            symbol.get("signature", ""),
            symbol.get("metadata"),
            symbol.get("qualified_name"),
            commit,
            generation_id,
        )
    for edge in payload["edges"]:
        add_edge(
            db,
            project,
            edge["source_path"],
            edge.get("source_name", ""),
            edge["relation"],
            edge["target_name"],
            edge["line"],
            edge.get("target_path"),
            edge.get("metadata"),
            edge.get("source_qualified_name", ""),
            edge.get("target_qualified_name"),
            edge.get("resolution_confidence", "unresolved"),
            commit,
            generation_id,
        )
    touched = {item["path"] for item in payload["symbols"]}
    touched.update(item["source_path"] for item in payload["edges"])
    return len(touched), payload.get("coverage", {})


def _python_module_path(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_python_module(
    current: Path,
    module: str | None,
    level: int,
    module_paths: dict[str, str],
) -> str | None:
    current_parts = _python_module_path(current).split(".")
    package = current_parts[:-1]
    if level:
        keep = max(0, len(package) - level + 1)
        prefix = package[:keep]
    else:
        prefix = []
    name = ".".join([*prefix, *(module or "").split(".")]).strip(".")
    candidates = [name]
    if not level and name:
        candidates.extend(candidate for candidate in module_paths if candidate.endswith(f".{name}"))
    for candidate in candidates:
        if candidate in module_paths:
            return module_paths[candidate]
    return None


def extract_python(
    db: sqlite3.Connection,
    project: str,
    repo: Path,
    commit: str,
    generation_id: str = "",
) -> tuple[int, dict]:
    """Index Python definitions and statically resolvable imports/calls."""
    files = [path for path in subprocess_files(repo) if path.suffix in PYTHON_EXTENSIONS]
    module_paths = {_python_module_path(path): str(path) for path in files}
    parsed: dict[Path, ast.AST] = {}
    parse_errors: list[str] = []
    local_symbols: dict[str, dict[str, list[tuple[str, str]]]] = {}

    class SymbolVisitor(ast.NodeVisitor):
        def __init__(self, path: Path) -> None:
            self.path = path
            self.stack: list[str] = []
            self.symbols: dict[str, list[tuple[str, str]]] = {}

        def record(self, node: ast.AST, kind: str, name: str) -> None:
            qualified = ".".join([*self.stack, name])
            signature = name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = [item.arg for item in node.args.args]
                signature = f"{name}({', '.join(arguments)})"
            add_symbol(
                db,
                project,
                str(self.path),
                kind,
                name,
                int(getattr(node, "lineno", 1)),
                int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                signature,
                {},
                qualified,
                commit,
                generation_id,
            )
            self.symbols.setdefault(name, []).append((qualified, kind))

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.record(node, "class", node.name)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            kind = "method" if self.stack else "function"
            self.record(node, kind, node.name)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

    for path in files:
        try:
            tree = ast.parse((repo / path).read_text("utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            parse_errors.append(str(path))
            continue
        parsed[path] = tree
        add_symbol(
            db,
            project,
            str(path),
            "module",
            path.stem,
            1,
            max(1, len((repo / path).read_text("utf-8").splitlines())),
            str(path),
            {},
            str(path),
            commit,
            generation_id,
        )
        visitor = SymbolVisitor(path)
        visitor.visit(tree)
        local_symbols[str(path)] = visitor.symbols

    class EdgeVisitor(ast.NodeVisitor):
        def __init__(self, path: Path) -> None:
            self.path = path
            self.stack: list[str] = []
            self.imports: dict[str, tuple[str | None, str]] = {}

        def owner(self) -> tuple[str, str]:
            if not self.stack:
                return "<module>", str(self.path)
            return self.stack[-1], ".".join(self.stack)

        def edge(
            self,
            node: ast.AST,
            relation: str,
            target: str,
            target_path: str | None = None,
            target_qualified: str | None = None,
            confidence: str = "unresolved",
        ) -> None:
            source, qualified = self.owner()
            add_edge(
                db,
                project,
                str(self.path),
                source,
                relation,
                target,
                int(getattr(node, "lineno", 1)),
                target_path,
                {},
                qualified,
                target_qualified,
                confidence,
                commit,
                generation_id,
            )

        def visit_Import(self, node: ast.Import) -> None:
            for item in node.names:
                target_path = _resolve_python_module(
                    self.path,
                    item.name,
                    0,
                    module_paths,
                )
                alias = item.asname or item.name.split(".")[0]
                self.imports[alias] = (target_path, item.name)
                self.edge(
                    node,
                    "imports",
                    item.name,
                    target_path,
                    item.name,
                    "exact_path" if target_path else "external_or_unresolved",
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            target_path = _resolve_python_module(
                self.path,
                node.module,
                node.level,
                module_paths,
            )
            module = node.module or ""
            for item in node.names:
                alias = item.asname or item.name
                qualified = f"{module}.{item.name}".strip(".")
                self.imports[alias] = (target_path, qualified)
                self.edge(
                    node,
                    "imports",
                    qualified,
                    target_path,
                    qualified,
                    "exact_path" if target_path else "external_or_unresolved",
                )

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            for base in node.bases:
                name = (
                    base.id
                    if isinstance(base, ast.Name)
                    else base.attr
                    if isinstance(base, ast.Attribute)
                    else ""
                )
                if not name:
                    continue
                target_path, qualified = self.imports.get(name, (None, name))
                self.edge(
                    base,
                    "extends",
                    name,
                    target_path,
                    qualified,
                    "import_resolved" if target_path else "unresolved",
                )
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_Call(self, node: ast.Call) -> None:
            target = ""
            root = ""
            if isinstance(node.func, ast.Name):
                target = root = node.func.id
            elif isinstance(node.func, ast.Attribute):
                target = node.func.attr
                value = node.func.value
                while isinstance(value, ast.Attribute):
                    value = value.value
                if isinstance(value, ast.Name):
                    root = value.id
            if target:
                target_path = None
                target_qualified = target
                confidence = "unresolved"
                if root in self.imports:
                    target_path, imported = self.imports[root]
                    target_qualified = f"{imported}.{target}" if root != target else imported
                    confidence = "import_resolved" if target_path else confidence
                else:
                    candidates = local_symbols.get(str(self.path), {}).get(target, [])
                    if len(candidates) == 1:
                        target_path = str(self.path)
                        target_qualified = candidates[0][0]
                        confidence = "same_file"
                self.edge(
                    node,
                    "calls",
                    target,
                    target_path,
                    target_qualified,
                    confidence,
                )
            self.generic_visit(node)

    for path, tree in parsed.items():
        EdgeVisitor(path).visit(tree)
    return len(parsed), {
        "language": "python",
        "files": len(files),
        "parsed": len(parsed),
        "parse_errors": parse_errors,
    }


def _sql_definition_end(text: str, start: int, kind: str, fallback: int) -> int:
    """Find a SQL definition terminator without stopping at PL/pgSQL semicolons."""
    if kind == "function":
        opener = re.search(r"\$[A-Za-z_0-9]*\$", text[fallback:])
        if opener:
            delimiter = opener.group(0)
            body_start = fallback + opener.end()
            body_end = text.find(delimiter, body_start)
            if body_end >= 0:
                terminator = text.find(";", body_end + len(delimiter))
                return terminator + 1 if terminator >= 0 else body_end + len(delimiter)
    terminator = text.find(";", start)
    return terminator + 1 if terminator >= 0 else fallback


def extract_sql(
    db,
    project: str,
    path: Path,
    text: str,
    commit: str = "",
    generation_id: str = "",
) -> None:
    path_text = str(path)
    definitions: list[tuple[int, int, str]] = []
    for kind, pattern in SQL_DEFINITION_PATTERNS:
        for match in pattern.finditer(text):
            if kind == "policy":
                name = match.group(1) or match.group(2)
                metadata = {"table": match.group(3)}
            else:
                name = match.group(1)
                metadata = {}
            line = text.count("\n", 0, match.start()) + 1
            end = _sql_definition_end(text, match.start(), kind, match.end())
            end_line = text.count("\n", 0, end) + 1
            add_symbol(
                db,
                project,
                path_text,
                kind,
                name,
                line,
                end_line,
                text[match.start() : match.end()],
                metadata,
                qualified_name=name,
                commit_hash=commit,
                generation_id=generation_id,
            )
            definitions.append((match.start(), end, name))
    definitions.sort()

    def owner(offset: int) -> str:
        result = ""
        for start, end, name in definitions:
            if start > offset:
                break
            result = name if offset <= end else ""
        return result

    for match in SQL_CALL.finditer(text):
        name = ("public." if match.group(1) else "") + match.group(2)
        if match.group(2).lower() in SQL_CALL_STOPWORDS:
            continue
        prefix = text[max(0, match.start() - 40) : match.start()].lower()
        if "function" in prefix:
            continue
        add_edge(
            db,
            project,
            path_text,
            owner(match.start()),
            "calls_sql",
            name,
            text.count("\n", 0, match.start()) + 1,
            target_qualified_name=name,
            confidence="lexical",
            commit_hash=commit,
            generation_id=generation_id,
        )
    for match in SQL_TABLE_REF.finditer(text):
        prefix = text[max(0, match.start() - 30) : match.start()].lower()
        if match.group(1).lower() == "from" and "extract" in prefix:
            continue
        if match.group(2).lower() in SQL_TABLE_STOPWORDS:
            continue
        add_edge(
            db,
            project,
            path_text,
            owner(match.start()),
            "accesses_table",
            match.group(2),
            text.count("\n", 0, match.start()) + 1,
            metadata={"operation": match.group(1).lower()},
            target_qualified_name=match.group(2),
            confidence="lexical",
            commit_hash=commit,
            generation_id=generation_id,
        )


def graph_digest(db: sqlite3.Connection, project: str) -> str:
    edges = [
        dict(row)
        for row in db.execute(
            """SELECT source_path,source_name,source_qualified_name,relation,target_name,
                  target_path,target_qualified_name,line,resolution_confidence,metadata
           FROM edges WHERE project=?
           ORDER BY source_path,source_qualified_name,line,relation,target_name,
                    coalesce(target_path,''),coalesce(target_qualified_name,''),metadata,id""",
            (project,),
        )
    ]
    symbols = [
        dict(row)
        for row in db.execute(
            """SELECT path,kind,qualified_name,start_line,end_line,active,metadata
           FROM symbols WHERE project=?
           ORDER BY path,start_line,end_line,kind,qualified_name,id""",
            (project,),
        )
    ]
    payload = json.dumps(
        {"edges": edges, "symbols": symbols},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def mark_latest_sql_definitions(db: sqlite3.Connection, project: str) -> None:
    rows = db.execute(
        """SELECT s.id,s.name,s.path FROM symbols s
           JOIN source_authority a ON a.project=s.project AND a.path=s.path
           WHERE s.project=? AND s.kind IN ('function','view','policy','trigger','type')
             AND a.authority='schema_history'
           ORDER BY s.path DESC,s.id DESC""",
        (project,),
    ).fetchall()
    seen = set()
    for row in rows:
        key = row["name"].lower()
        active = 0 if key in seen else 1
        db.execute("UPDATE symbols SET active=? WHERE id=?", (active, row["id"]))
        seen.add(key)


def resolve_sql_targets(db: sqlite3.Connection, project: str) -> None:
    """Link RPC/SQL calls to the current active SQL definition when unambiguous."""
    active: dict[str, list[sqlite3.Row]] = {}
    for row in db.execute(
        """SELECT path,name,qualified_name FROM symbols
           WHERE project=? AND kind='function' AND active=1""",
        (project,),
    ):
        for key in {
            row["name"].casefold(),
            row["qualified_name"].casefold(),
            row["qualified_name"].split(".")[-1].casefold(),
        }:
            active.setdefault(key, []).append(row)
    for edge in db.execute(
        """SELECT id,target_name FROM edges WHERE project=?
           AND relation IN ('calls_rpc','calls_sql') AND target_path IS NULL""",
        (project,),
    ).fetchall():
        candidates = {row["path"]: row for row in active.get(edge["target_name"].casefold(), [])}
        if len(candidates) == 1:
            row = next(iter(candidates.values()))
            db.execute(
                "UPDATE edges SET target_path=?,target_qualified_name=?,"
                "resolution_confidence='active_sql' WHERE id=?",
                (row["path"], row["qualified_name"], edge["id"]),
            )


def index_structure(
    db: sqlite3.Connection,
    project: str,
    repo: Path,
    commit: str,
    *,
    content_digest: str = "",
    generation_id: str = "",
    manage_transaction: bool = True,
) -> dict:
    if manage_transaction:
        ensure_schema(db)
    files = 0
    result: tuple[dict, str] | None = None

    def publish() -> None:
        nonlocal files, result
        db.execute("DELETE FROM source_authority WHERE project=?", (project,))
        db.execute("DELETE FROM symbols WHERE project=?", (project,))
        db.execute("DELETE FROM edges WHERE project=?", (project,))
        tracked = subprocess_files(repo)
        files, ts_coverage = extract_ts_compiler(
            db,
            project,
            repo,
            commit,
            generation_id,
        )
        python_files, python_coverage = extract_python(
            db,
            project,
            repo,
            commit,
            generation_id,
        )
        files += python_files
        for path in tracked:
            authority, reason = classify_source(str(path))
            db.execute(
                "INSERT INTO source_authority("
                "project,path,authority,reason,commit_hash,generation_id"
                ") VALUES(?,?,?,?,?,?)",
                (project, str(path), authority, reason, commit, generation_id),
            )
            absolute = repo / path
            if path.suffix in SQL_EXTENSIONS:
                extract_sql(
                    db,
                    project,
                    path,
                    absolute.read_text("utf-8", errors="ignore"),
                    commit,
                    generation_id,
                )
                files += 1
        mark_latest_sql_definitions(db, project)
        resolve_sql_targets(db, project)
        digest = graph_digest(db, project)
        db.execute(
            """INSERT INTO structure_snapshots(
                   project,commit_hash,graph_digest,content_digest,
                   extractor_version,generation_id
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(project) DO UPDATE SET
                 commit_hash=excluded.commit_hash,
                 graph_digest=excluded.graph_digest,
                 content_digest=excluded.content_digest,
                 extractor_version=excluded.extractor_version,
                 generation_id=excluded.generation_id,
                 indexed_at=CURRENT_TIMESTAMP""",
            (project, commit, digest, content_digest, extractor_fingerprint(), generation_id),
        )
        result = ({"typescript": ts_coverage, "python": python_coverage}, digest)

    if manage_transaction:
        with db:
            publish()
    else:
        publish()
    assert result is not None
    ts_coverage, digest = result
    symbols = db.execute("SELECT count(*) FROM symbols WHERE project=?", (project,)).fetchone()[0]
    edges = db.execute("SELECT count(*) FROM edges WHERE project=?", (project,)).fetchone()[0]
    unresolved = db.execute(
        "SELECT count(*) FROM edges WHERE project=? AND resolution_confidence='unresolved'",
        (project,),
    ).fetchone()[0]
    return {
        "project": project,
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "unresolved_edges": unresolved,
        "graph_digest": digest,
        "extractor_version": extractor_fingerprint(),
        "extractor_coverage": ts_coverage,
    }


def subprocess_files(repo: Path) -> list[Path]:
    output = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard"],
        text=True,
    )
    return [Path(line) for line in output.splitlines() if line and (repo / line).is_file()]


def related(
    db: sqlite3.Connection,
    project: str,
    name: str,
    limit: int = 50,
    path: str | None = None,
) -> dict:
    ensure_schema(db)
    path_sql = " AND s.path=?" if path else ""
    symbol_params: list[object] = [project, f"%{name}%", f"%{name}%"]
    if path:
        symbol_params.append(path)
    symbol_params.extend([name, name, f"{name}%", limit])
    symbols = [
        dict(row)
        for row in db.execute(
            """SELECT s.*,a.authority,a.reason FROM symbols s
           LEFT JOIN source_authority a USING(project,path)
           WHERE s.project=? AND (
             lower(s.name) LIKE lower(?) OR lower(s.qualified_name) LIKE lower(?)
           )"""
            + path_sql
            + """
           ORDER BY
             CASE
               WHEN lower(s.qualified_name)=lower(?) THEN 0
               WHEN lower(s.name)=lower(?) THEN 1
               WHEN lower(s.qualified_name) LIKE lower(?) THEN 2
               ELSE 3
             END,
             s.active DESC,s.path,s.start_line
           LIMIT ?""",
            symbol_params,
        )
    ]
    suggestions = []
    if not symbols:
        needle = name.casefold()
        candidates = [
            dict(row)
            for row in db.execute(
                """SELECT s.*,a.authority,a.reason FROM symbols s
               LEFT JOIN source_authority a USING(project,path)
               WHERE s.project=? AND s.active=1"""
                + (" AND s.path=?" if path else "")
                + " ORDER BY s.path,s.start_line",
                ([project, path] if path else [project]),
            )
        ]
        scored = []
        for item in candidates:
            short = str(item.get("name") or "").casefold()
            qualified = str(item.get("qualified_name") or "").casefold()
            score = max(
                difflib.SequenceMatcher(None, needle, short).ratio(),
                difflib.SequenceMatcher(None, needle, qualified).ratio(),
            )
            if score >= 0.48:
                scored.append((score, item))
        suggestions = [
            item | {"match_score": round(score, 3)}
            for score, item in sorted(
                scored,
                key=lambda pair: (-pair[0], pair[1]["path"], pair[1]["start_line"]),
            )[:5]
        ]
    edge_path_sql = " AND (source_path=? OR target_path=?)" if path else ""
    edge_params: list[object] = [project, f"%{name}%", f"%{name}%"]
    if path:
        edge_params.extend([path, path])
    edge_params.extend([name, name, name, name, limit])
    edges = [
        dict(row)
        for row in db.execute(
            """SELECT * FROM edges WHERE project=?
           AND (lower(source_name) LIKE lower(?) OR lower(target_name) LIKE lower(?))
        """
            + edge_path_sql
            + """
           ORDER BY
             CASE
               WHEN lower(source_qualified_name)=lower(?) THEN 0
               WHEN lower(source_name)=lower(?) THEN 1
               WHEN lower(target_qualified_name)=lower(?) THEN 2
               WHEN lower(target_name)=lower(?) THEN 3
               ELSE 4
             END,
             CASE WHEN target_path IS NOT NULL THEN 0 ELSE 1 END,
             relation,source_path,line
           LIMIT ?""",
            edge_params,
        )
    ]
    return {
        "query": name,
        "path_filter": path,
        "symbols": symbols,
        "suggestions": suggestions,
        "edges": edges,
    }
