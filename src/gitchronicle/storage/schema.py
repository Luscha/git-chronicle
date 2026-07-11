"""SQLite schema + connection helpers. This DB is the single source of truth.

Design notes:
- Tables are plural to avoid the SQLite ``COMMIT`` keyword clash.
- Embeddings are stored as float32 BLOBs (numpy ``tobytes``); at slice scale we do
  kNN in numpy. ``sqlite-vec`` is a later drop-in optimisation, not a dependency now.
- ``pipeline_stage_status`` gives every stage cheap idempotency/resumability.
- ``locked``/``status`` columns let a human confirm data that later auto-runs must
  never clobber.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commits (
    hash            TEXT PRIMARY KEY,
    author_name     TEXT,
    author_email    TEXT,
    committer_name  TEXT,
    committer_email TEXT,
    authored_at     TEXT,
    committed_at    TEXT,
    subject         TEXT,
    body            TEXT,
    is_merge        INTEGER DEFAULT 0,
    parent_hashes   TEXT,            -- JSON array
    insertions      INTEGER DEFAULT 0,
    deletions       INTEGER DEFAULT 0,
    files_changed   INTEGER DEFAULT 0,
    lang            TEXT,
    is_conventional INTEGER DEFAULT 0,
    cc_type         TEXT,
    cc_scope        TEXT,
    kind            TEXT,            -- normalised work-kind: feat|fix|refactor|chore|docs|other
    component       TEXT             -- primary (non-noise) component
);

CREATE TABLE IF NOT EXISTS components (
    id       INTEGER PRIMARY KEY,
    key      TEXT UNIQUE NOT NULL,
    name     TEXT,
    is_noise INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS commit_files (
    id           INTEGER PRIMARY KEY,
    commit_hash  TEXT NOT NULL REFERENCES commits(hash) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    change_type  TEXT,
    insertions   INTEGER,
    deletions    INTEGER,
    is_binary    INTEGER DEFAULT 0,
    component_id INTEGER REFERENCES components(id)
);
CREATE INDEX IF NOT EXISTS idx_commit_files_hash ON commit_files(commit_hash);
CREATE INDEX IF NOT EXISTS idx_commit_files_path ON commit_files(path);

CREATE TABLE IF NOT EXISTS commit_branches (
    commit_hash TEXT NOT NULL REFERENCES commits(hash) ON DELETE CASCADE,
    branch      TEXT NOT NULL,
    PRIMARY KEY (commit_hash, branch)
);
CREATE INDEX IF NOT EXISTS idx_commit_branches_hash ON commit_branches(commit_hash);

CREATE TABLE IF NOT EXISTS commit_embeddings (
    commit_hash TEXT NOT NULL REFERENCES commits(hash) ON DELETE CASCADE,
    kind        TEXT NOT NULL,      -- e.g. 'combined'
    model       TEXT NOT NULL,
    dim         INTEGER,
    vector      BLOB,               -- float32 numpy bytes
    PRIMARY KEY (commit_hash, kind, model)
);

CREATE TABLE IF NOT EXISTS eras (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE,
    tag_ref     TEXT,
    time_start  TEXT,
    time_end    TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id             INTEGER PRIMARY KEY,
    algorithm      TEXT,
    params         TEXT,            -- JSON
    signals_config TEXT,            -- JSON
    git_head       TEXT,
    rev_range      TEXT,
    n_files        INTEGER,
    n_domains      INTEGER,
    modularity     REAL,
    created_at     TEXT
);

-- A CONCERN is one semantic purpose within a commit, untangled from its diff (the
-- message is untrusted). It is the atomic unit we cluster into domains — NOT the file
-- (god-files span many concerns) nor the commit (commits are tangled).
CREATE TABLE IF NOT EXISTS concerns (
    id          INTEGER PRIMARY KEY,
    commit_hash TEXT NOT NULL REFERENCES commits(hash) ON DELETE CASCADE,
    label       TEXT,          -- plain capability phrase read from the diff
    summary     TEXT,          -- one-sentence description of the change (the embedding facet)
    files       TEXT,          -- JSON array: the commit's files belonging to this concern
    kind        TEXT,          -- the commit's work-kind
    domain_id   INTEGER REFERENCES domains(id),  -- assigned by classification
    assign_source TEXT,        -- how domain_id was set: fast|llm|novelty|propose|audit|human
    assign_conf   REAL,        -- classification confidence (cosine / margin)
    origin      TEXT           -- NULL = diff/msg untangle; import = peek-labelled family from
                               -- a bulk commit; import-misc = vendored drop / remainder (never
                               -- evidence, never injected into the glossary)
);
CREATE INDEX IF NOT EXISTS idx_concerns_commit ON concerns(commit_hash);
CREATE INDEX IF NOT EXISTS idx_concerns_domain ON concerns(domain_id);

-- An AREA is the top level of the hierarchy: a broad part of the system (e.g. "Combat",
-- "Anti-Cheat", "Client UI") grouping several fine domains. Large histories have hundreds of
-- domains; areas make them navigable. Areas are clusters of domains (a second Leiden pass).
CREATE TABLE IF NOT EXISTS areas (
    id               INTEGER PRIMARY KEY,
    discovery_run_id INTEGER REFERENCES discovery_runs(id),
    slug             TEXT,
    name             TEXT,
    classification   TEXT,
    tags             TEXT,          -- JSON array
    status           TEXT DEFAULT 'candidate',
    confidence       REAL,
    n_domains        INTEGER,
    n_commits        INTEGER,
    first_seen       TEXT,
    last_seen        TEXT,
    created_by       TEXT DEFAULT 'auto'
);

-- A domain is a coherent PIECE OF THE SYSTEM (e.g. "query planner", "augments").
-- All work of every kind accretes to it; work-kind lives on the commit, not here.
CREATE TABLE IF NOT EXISTS domains (
    id               INTEGER PRIMARY KEY,
    discovery_run_id INTEGER REFERENCES discovery_runs(id),
    area_id          INTEGER REFERENCES areas(id),   -- parent area (hierarchy)
    slug             TEXT,
    name             TEXT,
    summary          TEXT,          -- what the domain is (distilled from the chronicle; opt-in)
    definition       TEXT,          -- taxonomy definition: one sentence + includes/excludes criteria
    stems            TEXT,          -- JSON array: path stems that evidence this feature (v3 ground)
    named_from       INTEGER,       -- how many concerns the name was drafted from (rename-on-accretion)
    classification   TEXT,          -- core|feature|subsystem|data|ui|infra|tooling|docs
    fan_in           INTEGER DEFAULT 0,  -- how many other domains depend on this one
    tags             TEXT,          -- JSON array
    status           TEXT DEFAULT 'candidate',  -- candidate|named|provisional|confirmed|rejected
    confidence       REAL,
    lifecycle        TEXT DEFAULT 'active',     -- active|dormant|merged|removed
    removed_at       TEXT,                      -- when the domain's files were deleted
    first_seen       TEXT,
    last_seen        TEXT,
    n_commits        INTEGER,
    n_files          INTEGER,
    created_by       TEXT DEFAULT 'auto',
    locked           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS domain_aliases (
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    alias     TEXT NOT NULL,
    PRIMARY KEY (domain_id, alias)
);

-- The coupled files/code that define the domain.
CREATE TABLE IF NOT EXISTS domain_files (
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    path      TEXT NOT NULL,
    weight    REAL,                  -- centrality of this file to the domain
    source    TEXT NOT NULL DEFAULT 'history',  -- 'register' = worktree territory (authoritative), 'history' = derived from attributed commits
    PRIMARY KEY (domain_id, path)
);
CREATE INDEX IF NOT EXISTS idx_domain_files_domain ON domain_files(domain_id);

-- Many-to-many: one commit can contribute to several domains (multi-topic commits).
CREATE TABLE IF NOT EXISTS commit_domains (
    commit_hash  TEXT NOT NULL REFERENCES commits(hash) ON DELETE CASCADE,
    domain_id    INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    weight       REAL,               -- share of the commit's files in this domain
    kind         TEXT,               -- the commit's work-kind (feat|fix|refactor|...)
    contribution TEXT,               -- optional: what this commit did for the domain
    source       TEXT,               -- files|message|semantic
    PRIMARY KEY (commit_hash, domain_id)
);
CREATE INDEX IF NOT EXISTS idx_commit_domains_domain ON commit_domains(domain_id);
CREATE INDEX IF NOT EXISTS idx_commit_domains_commit ON commit_domains(commit_hash);

CREATE TABLE IF NOT EXISTS domain_edges (
    id         INTEGER PRIMARY KEY,
    src_domain INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    dst_domain INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    type       TEXT,                 -- depends_on|relates_to|evolved_from
    weight     REAL,
    why        TEXT,
    confidence REAL,
    status     TEXT DEFAULT 'candidate',
    locked     INTEGER DEFAULT 0
);

-- The narrative chronicle (opt-in): commit-anchored story chapters per domain and repo.
CREATE TABLE IF NOT EXISTS evolution_chapters (
    id            INTEGER PRIMARY KEY,
    target_type   TEXT NOT NULL,     -- 'domain' | 'repo'
    target_id     TEXT,              -- domain id (as text) or NULL for repo
    seq           INTEGER,           -- order within the target's timeline
    period_start  TEXT,
    period_end    TEXT,
    title         TEXT,
    narrative     TEXT,
    commit_hashes TEXT,              -- JSON array of anchoring commit hashes
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_chapters_target ON evolution_chapters(target_type, target_id);

-- Semantic index: embeddings for any entity (domains, later commits). kNN in numpy.
CREATE TABLE IF NOT EXISTS embeddings (
    target_type TEXT NOT NULL,      -- 'domain' | 'commit' | ...
    target_id   TEXT NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER,
    vector      BLOB,
    PRIMARY KEY (target_type, target_id, model)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,    -- sha256(provider|model|kind|payload)
    provider   TEXT,
    model      TEXT,
    kind       TEXT,
    response   TEXT,
    tokens_in  INTEGER,
    tokens_out INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_stage_status (
    stage      TEXT NOT NULL,
    item_key   TEXT NOT NULL,
    input_hash TEXT,
    status     TEXT DEFAULT 'done',
    updated_at TEXT,
    PRIMARY KEY (stage, item_key)
);

-- GROUND: repo-level aggregate evidence (the layer per-commit reading can never see).
-- Stems are name tokens from file paths, weighted by how much history touches them; the
-- glossary is the big model's draft of the repo's own feature vocabulary, distilled from
-- the census + in-repo docs. Induction and classification consume both.
CREATE TABLE IF NOT EXISTS stem_census (
    stem         TEXT PRIMARY KEY,
    n_files      INTEGER,          -- distinct files carrying the stem
    n_concerns   INTEGER,          -- concerns touching those files (the activity weight)
    ubiquity     REAL,             -- share of ALL files carrying it (high = generic word)
    is_god       INTEGER DEFAULT 0,-- hub signature: dir-token (huge ubiquity) or its files
                                   -- are god-files (high median concerns-per-file) — such a
                                   -- stem legitimately spans features and must never anchor one
    sample_paths TEXT              -- JSON array
);

CREATE TABLE IF NOT EXISTS glossary (
    id         INTEGER PRIMARY KEY,
    name       TEXT,
    definition TEXT,
    stems      TEXT,               -- JSON array: census stems that evidence this entity
    evidence   TEXT,               -- JSON: {"docs": [...], "paths": [...]}
    source     TEXT,               -- census|doc|peek|import
    tier       INTEGER DEFAULT 2,  -- evidence strength: 4=doc 3=code-peek 2=diff-labels 1=name.
                                   -- Territory conflicts (entities claiming the same stems with
                                   -- incompatible definitions) resolve to the higher tier.
    status     TEXT DEFAULT 'candidate'
);

-- Rejected feature names. Fed to propose-new as negative examples so a rejected candidate
-- is never re-proposed run after run (review must not become whack-a-mole).
CREATE TABLE IF NOT EXISTS taxonomy_tombstones (
    name       TEXT PRIMARY KEY COLLATE NOCASE,
    reason     TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY,
    target_type TEXT,               -- 'feature'|'feature_commit'|'edge'...
    target_id   TEXT,
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT,
    author      TEXT,
    kind        TEXT,               -- 'correction'|'confirmation'|'note'
    created_at  TEXT
);
"""


def now_iso() -> str:
    """UTC timestamp, ISO-8601, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the DB with sane pragmas for a resumable single-writer pipeline."""
    # check_same_thread=False + the provider's db_lock let concurrent untangle workers
    # share one connection safely (all access is serialised through the lock / main thread).
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Lightweight migrations for pre-existing DBs (CREATE TABLE IF NOT EXISTS won't add columns).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(domains)")}
    if "area_id" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN area_id INTEGER REFERENCES areas(id)")
    if "definition" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN definition TEXT")
    if "stems" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN stems TEXT")
    if "named_from" not in cols:
        conn.execute("ALTER TABLE domains ADD COLUMN named_from INTEGER")
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(concerns)")}
    for name, decl in (("summary", "TEXT"), ("assign_source", "TEXT"), ("assign_conf", "REAL"),
                       ("origin", "TEXT")):
        if name not in ccols:
            conn.execute(f"ALTER TABLE concerns ADD COLUMN {name} {decl}")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(stem_census)")}
    if scols and "is_god" not in scols:
        conn.execute("ALTER TABLE stem_census ADD COLUMN is_god INTEGER DEFAULT 0")
    gcols = {r[1] for r in conn.execute("PRAGMA table_info(glossary)")}
    if gcols and "tier" not in gcols:
        conn.execute("ALTER TABLE glossary ADD COLUMN tier INTEGER DEFAULT 2")
    conn.commit()


def stage_done(conn: sqlite3.Connection, stage: str, item_key: str,
               input_hash: str | None = None) -> bool:
    """True if this stage already processed item_key with the same input_hash."""
    row = conn.execute(
        "SELECT input_hash FROM pipeline_stage_status WHERE stage=? AND item_key=?",
        (stage, item_key),
    ).fetchone()
    if row is None:
        return False
    if input_hash is None:
        return True
    return row["input_hash"] == input_hash


def mark_stage(conn: sqlite3.Connection, stage: str, item_key: str,
               input_hash: str | None = None, status: str = "done") -> None:
    conn.execute(
        "INSERT INTO pipeline_stage_status (stage, item_key, input_hash, status, updated_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(stage, item_key) DO UPDATE SET "
        "input_hash=excluded.input_hash, status=excluded.status, updated_at=excluded.updated_at",
        (stage, item_key, input_hash, status, now_iso()),
    )
