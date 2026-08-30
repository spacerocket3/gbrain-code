#!/usr/bin/env python3
"""Versioned local repository index used by the GBrain cartographer."""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import subprocess
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import vector_search
from graph_index import (
    ensure_schema as ensure_graph_schema,
)
from graph_index import (
    extractor_fingerprint,
    index_structure,
)
from project_registry import ProjectRegistry

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("GBRAIN_DB", ROOT / "data" / "index.sqlite3"))
PROJECTS_FILE = Path(os.environ.get("GBRAIN_PROJECTS_FILE", ROOT / "data" / "projects.json"))
REPOS = ProjectRegistry(PROJECTS_FILE)

TEXT_EXTENSIONS = {
    ".bash",
    ".c",
    ".cc",
    ".cjs",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".cts",
    ".dart",
    ".env.example",
    ".ex",
    ".exs",
    ".fs",
    ".fsx",
    ".go",
    ".gradle",
    ".graphql",
    ".groovy",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".lua",
    ".md",
    ".mjs",
    ".mts",
    ".php",
    ".pl",
    ".proto",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
SKIP_NAMES = {"package-lock.json", "bun.lock", "bun.lockb", "deno.lock"}
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
SECRET_RE = re.compile(r"(^|/)(\.env($|\.)|.*\.(pem|key|p12|pfx)$)", re.I)
WORD_RE = re.compile(r"[\w][\w.-]*", re.UNICODE)
STOPWORDS = {
    "a",
    "al",
    "and",
    "como",
    "con",
    "de",
    "del",
    "donde",
    "el",
    "en",
    "es",
    "esta",
    "este",
    "for",
    "how",
    "la",
    "las",
    "lo",
    "los",
    "of",
    "o",
    "para",
    "por",
    "que",
    "se",
    "the",
    "to",
    "un",
    "una",
    "y",
}
QUERY_NOISE = {
    "affected",
    "all",
    "automatic",
    "automatically",
    "compatibility",
    "complete",
    "code",
    "concrete",
    "date",
    "existing",
    "find",
    "identify",
    "implementation",
    "implement",
    "including",
    "manual",
    "relevant",
    "repository",
    "reversible",
    "selection",
    "trace",
    "where",
    "with",
}
AUTHORITY_WEIGHTS = {
    "code": 1.0,
    "test": 0.90,
    "schema_history": 1.0,
    "config": 0.80,
    "generated": 0.65,
}


@dataclass
class Hit:
    project: str
    path: str
    start_line: int
    end_line: int
    commit: str
    score: float
    content: str
    authority: str = "unknown"

    @property
    def citation(self) -> str:
        return f"{self.project}:{self.path}:{self.start_line}-{self.end_line}"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS repos (
            project TEXT PRIMARY KEY,
            root TEXT NOT NULL,
            commit_hash TEXT NOT NULL,
            content_digest TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            commit_hash TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            content TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_project_idx ON chunks(project);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            path, content, content='chunks', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2 tokenchars ''_-'''
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid,path,content)
            VALUES(new.id,new.path,new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts,rowid,path,content)
            VALUES('delete',old.id,old.path,old.content);
        END;
        """
    )
    ensure_graph_schema(db)
    vector_search.ensure_schema(db)
    return db


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def require_registered_project(project: str) -> Path:
    if project not in REPOS:
        available = ", ".join(REPOS) or "none"
        raise ValueError(f"Unknown project {project!r}; registered projects: {available}")
    return REPOS[project]


def working_tree_files(repo: Path) -> Iterable[Path]:
    listed = git(repo, "ls-files", "--cached", "--others", "--exclude-standard")
    for relative in listed.splitlines():
        path = Path(relative)
        suffix = "".join(path.suffixes[-2:]) if path.name.endswith(".env.example") else path.suffix
        absolute = repo / path
        if (
            not relative
            or path.name in SKIP_NAMES
            or any(part in SKIP_PARTS for part in path.parts)
            or SECRET_RE.search(relative)
            or suffix.lower() not in TEXT_EXTENSIONS
            or not absolute.is_file()
            or absolute.stat().st_size > 1_500_000
        ):
            continue
        yield path


def repository_content_digest(repo: Path) -> str:
    state = hashlib.sha256()
    for path in sorted(working_tree_files(repo), key=str):
        raw_path = str(path).encode("utf-8", "surrogateescape")
        raw = (repo / path).read_bytes()
        state.update(len(raw_path).to_bytes(8, "big"))
        state.update(raw_path)
        state.update(len(raw).to_bytes(8, "big"))
        state.update(raw)
    return state.hexdigest()


def chunks_for(
    text: str,
    size: int = 80,
    overlap: int = 15,
) -> Iterable[tuple[int, int, str]]:
    lines = text.splitlines()
    step = size - overlap
    for offset in range(0, len(lines), step):
        block = lines[offset : offset + size]
        if not block:
            break
        yield offset + 1, offset + len(block), "\n".join(block)
        if offset + size >= len(lines):
            break


def _publish_chunks(
    db: sqlite3.Connection,
    project: str,
    commit: str,
    generation_id: str,
    desired: list[tuple[str, int, int, str]],
) -> None:
    existing: dict[tuple[str, int, int, str], list[int]] = {}
    for row in db.execute(
        "SELECT id,path,start_line,end_line,content FROM chunks WHERE project=?",
        (project,),
    ):
        key = (row["path"], row["start_line"], row["end_line"], row["content"])
        existing.setdefault(key, []).append(row["id"])
    for path, start, end, content in desired:
        key = (path, start, end, content)
        candidates = existing.get(key)
        if candidates:
            chunk_id = candidates.pop()
            db.execute(
                "UPDATE chunks SET commit_hash=?,generation_id=? WHERE id=?",
                (commit, generation_id, chunk_id),
            )
        else:
            db.execute(
                "INSERT INTO chunks(project,path,start_line,end_line,commit_hash,"
                "generation_id,content) VALUES(?,?,?,?,?,?,?)",
                (project, path, start, end, commit, generation_id, content),
            )
    stale = [chunk_id for ids in existing.values() for chunk_id in ids]
    db.executemany("DELETE FROM chunks WHERE id=?", ((item,) for item in stale))


def semantic_status(db: sqlite3.Connection, project: str) -> dict:
    chunks = db.execute("SELECT count(*) FROM chunks WHERE project=?", (project,)).fetchone()[0]
    embedded = db.execute(
        """SELECT count(*) FROM embeddings e JOIN chunks c ON c.id=e.chunk_id
           WHERE c.project=? AND e.model=?""",
        (project, vector_search.EMBED_MODEL),
    ).fetchone()[0]
    return {
        "chunks": chunks,
        "embedded_chunks": embedded,
        "semantic_coverage": round(embedded / chunks, 4) if chunks else 1.0,
        "pending_embeddings": max(0, chunks - embedded),
    }


def index_project(project: str, force: bool = False) -> dict:
    repo = require_registered_project(project)
    commit = git(repo, "rev-parse", "HEAD")
    digest = repository_content_digest(repo)
    extractor = extractor_fingerprint()
    generation_id = hashlib.sha256(
        f"{project}\0{commit}\0{digest}\0{extractor}".encode()
    ).hexdigest()
    dirty = bool(git(repo, "status", "--porcelain", "--untracked-files=all"))
    db = connect()
    previous = db.execute("SELECT * FROM repos WHERE project=?", (project,)).fetchone()
    if (
        previous
        and previous["commit_hash"] == commit
        and previous["content_digest"] == digest
        and previous["extractor_version"] == extractor
        and not force
    ):
        return {
            "project": project,
            "commit": commit,
            "generation_id": generation_id,
            "working_tree_dirty": dirty,
            "structural_current": True,
            "status": "current",
            **semantic_status(db, project),
        }

    desired: list[tuple[str, int, int, str]] = []
    files = 0
    for relative in working_tree_files(repo):
        try:
            text = (repo / relative).read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files += 1
        desired.extend(
            (str(relative), start, end, content) for start, end, content in chunks_for(text)
        )
    with db:
        _publish_chunks(db, project, commit, generation_id, desired)
        structure = index_structure(
            db,
            project,
            repo,
            commit,
            content_digest=digest,
            generation_id=generation_id,
            manage_transaction=False,
        )
        db.execute(
            """INSERT INTO repos(
                   project,root,commit_hash,content_digest,extractor_version,generation_id
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(project) DO UPDATE SET
                 root=excluded.root,
                 commit_hash=excluded.commit_hash,
                 content_digest=excluded.content_digest,
                 extractor_version=excluded.extractor_version,
                 generation_id=excluded.generation_id,
                 indexed_at=CURRENT_TIMESTAMP""",
            (project, str(repo), commit, digest, extractor, generation_id),
        )
        if git(repo, "rev-parse", "HEAD") != commit:
            raise RuntimeError("Repository commit changed during indexing")
        if repository_content_digest(repo) != digest:
            raise RuntimeError("Repository contents changed during indexing")
    return {
        "project": project,
        "commit": commit,
        "generation_id": generation_id,
        "files": files,
        "chunks": len(desired),
        "working_tree_dirty": dirty,
        "structural_current": True,
        "structure": structure,
        "status": "indexed",
        **semantic_status(db, project),
    }


def query_terms(question: str, limit: int = 48) -> list[str]:
    normalized = unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode().lower()
    words = []
    for raw in WORD_RE.findall(normalized):
        word = raw.replace('"', "")
        if len(word) <= 1 or word in STOPWORDS or word in QUERY_NOISE:
            continue
        words.append(word)
        if word.endswith("ing") and len(word) > 6:
            words.append(word[:-3])
        if word.endswith("al") and len(word) > 6:
            words.append(word[:-2])
        if word.endswith("s") and len(word) > 4:
            words.append(word[:-1])
    words = list(dict.fromkeys(words))
    if len(words) <= limit:
        return words
    half = limit // 2
    return list(dict.fromkeys(words[:half] + words[-(limit - half) :]))


def lexical_tokens(value: str) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return {
        part
        for token in re.findall(r"[a-zA-Z0-9_-]+", separated.casefold())
        for part in re.split(r"[_-]+", token)
        if part
    }


def keyword_search(question: str, project: str, limit: int = 40) -> list[Hit]:
    require_registered_project(project)
    terms = query_terms(question)
    if not terms:
        return []
    db = connect()
    query = " OR ".join(f'"{term}"' for term in terms)
    rows = db.execute(
        """SELECT c.project,c.path,c.start_line,c.end_line,c.commit_hash,c.content,
                  a.authority,bm25(chunks_fts,2.5,1.0) AS rank
           FROM chunks_fts
           JOIN chunks c ON c.id=chunks_fts.rowid
           JOIN source_authority a ON a.project=c.project AND a.path=c.path
           WHERE chunks_fts MATCH ? AND c.project=?
             AND a.authority IN ('code','test','schema_history','generated','config')
           ORDER BY rank LIMIT ?""",
        (query, project, max(1, min(limit, 100))),
    ).fetchall()
    hits = [
        Hit(
            row["project"],
            row["path"],
            row["start_line"],
            row["end_line"],
            row["commit_hash"],
            round(-row["rank"], 6),
            row["content"],
            row["authority"],
        )
        for row in rows
    ]
    total_files = db.execute(
        "SELECT count(DISTINCT path) FROM chunks WHERE project=?", (project,)
    ).fetchone()[0]
    document_frequency = {}
    for term in terms:
        document_frequency[term] = db.execute(
            """SELECT count(DISTINCT c.path)
               FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
               WHERE chunks_fts MATCH ? AND c.project=?""",
            (f'"{term}"', project),
        ).fetchone()[0]

    term_groups: list[list[str]] = []
    for term in sorted(terms, key=len, reverse=True):
        group = next(
            (
                item
                for item in term_groups
                if any(term.startswith(other) or other.startswith(term) for other in item)
            ),
            None,
        )
        if group is None:
            term_groups.append([term])
        else:
            group.append(term)

    def lexical_value(hit: Hit) -> float:
        tokens = lexical_tokens(f"{hit.path}\n{hit.content}")
        specificity = sum(
            max(
                (
                    math.log((total_files + 1) / (document_frequency[term] + 1)) + 1.0
                    for term in group
                    if term in tokens
                ),
                default=0.0,
            )
            for group in term_groups
        )
        return specificity + hit.score * 0.001

    return sorted(hits, key=lexical_value, reverse=True)


def retrieve(
    question: str,
    project: str,
    limit: int = 40,
    semantic_mode: str = "auto",
) -> tuple[list[Hit], str, str | None]:
    if semantic_mode not in {"auto", "fast", "code"}:
        raise ValueError("semantic_mode must be auto, fast, or code")
    candidates = max(20, min(80, limit * 3))
    lexical = keyword_search(question, project, candidates)
    db = connect()
    semantic = semantic_status(db, project)
    use_semantic = semantic_mode != "fast" and semantic["embedded_chunks"] > 0
    ranked: dict[str, tuple[Hit, float]] = {}
    for rank, hit in enumerate(lexical, 1):
        ranked[hit.citation] = (hit, 1.0 / (60 + rank))
    if use_semantic:
        for rank, row in enumerate(
            vector_search.semantic_rows(db, question, project, candidates), 1
        ):
            hit = Hit(
                row["project"],
                row["path"],
                row["start_line"],
                row["end_line"],
                row["commit_hash"],
                row["semantic_score"],
                row["content"],
            )
            authority = db.execute(
                "SELECT authority FROM source_authority WHERE project=? AND path=?",
                (project, hit.path),
            ).fetchone()
            hit.authority = authority[0] if authority else "unknown"
            previous = ranked.get(hit.citation)
            score = (previous[1] if previous else 0.0) + 0.9 / (60 + rank)
            ranked[hit.citation] = (previous[0] if previous else hit, score)
    weighted = [
        (hit, score * AUTHORITY_WEIGHTS.get(hit.authority, 0.5)) for hit, score in ranked.values()
    ]
    first_stage = [hit for hit, _ in sorted(weighted, key=lambda item: item[1], reverse=True)][
        :candidates
    ]
    mode_used = "hybrid" if use_semantic else "lexical+graph"
    fallback = None
    should_rerank = semantic_mode == "code" or (
        semantic_mode == "auto"
        and vector_search.code_reranker_cached()
        and len(query_terms(question)) >= 8
    )
    if should_rerank and first_stage:
        try:
            documents = [f"File: {hit.path}\n{hit.content}" for hit in first_stage]
            scores = vector_search.code_rerank_scores(db, question, documents)
            ordering = sorted(range(len(first_stage)), key=scores.__getitem__, reverse=True)
            first_stage = [first_stage[index] for index in ordering]
            mode_used = "code-reranked"
        except RuntimeError as exc:
            if semantic_mode == "code":
                raise
            fallback = str(exc)
    return first_stage[:limit], mode_used, fallback


def embed_project(project: str, batch_size: int = 8) -> dict:
    require_registered_project(project)
    result = vector_search.index_embeddings(connect(), project, batch_size)
    return {**result, **semantic_status(connect(), project)}


def register_project(name: str, root: str, description: str = "") -> dict:
    return REPOS.add(name, Path(root), description)


def unregister_project(name: str) -> dict:
    require_registered_project(name)
    db = connect()
    with db:
        for table in (
            "edges",
            "symbols",
            "source_authority",
            "structure_snapshots",
            "chunks",
            "repos",
        ):
            db.execute(f"DELETE FROM {table} WHERE project=?", (name,))
    registration = REPOS.remove(name)
    return {"project": name, **registration, "checkout_deleted": False}


def project_status(project: str) -> dict:
    repo = require_registered_project(project)
    db = connect()
    row = db.execute("SELECT * FROM repos WHERE project=?", (project,)).fetchone()
    commit = git(repo, "rev-parse", "HEAD")
    digest = repository_content_digest(repo)
    current = bool(
        row
        and row["commit_hash"] == commit
        and row["content_digest"] == digest
        and row["extractor_version"] == extractor_fingerprint()
    )
    return {
        "project": project,
        "root": str(repo),
        "description": REPOS.note(project),
        "commit": commit,
        "generation_id": row["generation_id"] if row else None,
        "structural_current": current,
        "working_tree_dirty": bool(git(repo, "status", "--porcelain", "--untracked-files=all")),
        "symbols": db.execute(
            "SELECT count(*) FROM symbols WHERE project=?", (project,)
        ).fetchone()[0],
        "edges": db.execute("SELECT count(*) FROM edges WHERE project=?", (project,)).fetchone()[0],
        **semantic_status(db, project),
    }


def status() -> dict:
    return {
        "database": str(DB_PATH),
        "projects": [project_status(name) for name in REPOS],
        "semantic": {
            "embedding_model": vector_search.EMBED_MODEL,
            "code_reranker_model": vector_search.CODE_EMBED_MODEL,
            "code_reranker_backend": vector_search.code_reranker_backend(),
            "code_reranker_cached": vector_search.code_reranker_cached(),
        },
    }
