"""AUTHORED DELTA — the first partition of a fork's worktree is inherited vs authored.

void-queue derives from Metin2: a large share of HEAD arrived in import/reorg blob
commits and was never meaningfully touched again. That code is the OWNER'S BASELINE,
not their features — v0.1 catalogued it as feature material and produced god features
from vanilla code ('Guild Diplomacy and War System' = inherited PythonGuild bindings).

Evidence, all from git:
  - blob commits: the founding import + any commit touching > _BLOB_FILES files
    (imports, vendor drops, reorgs — Hindle et al.: large commits are not feature work)
  - a file's authored touches = distinct non-blob commits that changed it, counted
    through its rename chain (the dev/ -> root restructure must not launder history)

klass — CREATION PROVENANCE ONLY. Touch counts are not authorship: PythonGuild.cpp
collected 25 maintenance touches and guild_war.cpp 44 without ever being the owner's
feature, while god-files (char.cpp, 569) accumulate touches from every feature passing
through. The owner's features ADD files; edits to inherited files are maintenance whose
story attributes elsewhere.
  authored  — the file's rename-chain ROOT was ADDED by a non-blob commit
  inherited — it arrived in a blob (founding import, vendor drop, reorg)
"""

from __future__ import annotations

from ..extract.git_ingest import run_git

_BLOB_FILES = 200


def history_scan(repo: str):
    """One pass over --all: per path, the (date, commit) that first ADDED it, plus the
    rename parent map. Deterministic; ~seconds on 6.5k commits."""
    first: dict[str, tuple[str, str]] = {}      # path -> (date, adding commit)
    parent: dict[str, tuple[str, str]] = {}     # new -> (old, date)
    date = commit = ""
    out = run_git(repo, ["-c", "diff.renameLimit=100000", "log", "--all",
                         "--find-renames", "--diff-filter=AR", "--name-status",
                         "--date=format:%Y-%m-%d", "--format=%x01%H %ad"])
    for ln in out.splitlines():
        if ln.startswith("\x01"):
            commit, _, date = ln[1:].strip().partition(" ")
            continue
        if not ln or not date or "\t" not in ln:
            continue
        if ln[0] == "A":
            p = ln.split("\t", 1)[1].strip()
            if p not in first or date < first[p][0]:
                first[p] = (date, commit)
        elif ln[0] == "R":
            parts = ln.split("\t")
            if len(parts) == 3:
                o, n = parts[1].strip(), parts[2].strip()
                if n not in parent or date < parent[n][1]:
                    parent[n] = (o, date)
    return first, parent


def _aliases(path: str, parent: dict) -> list[str]:
    """The path plus every historical name it had (rename chain, cycle-guarded)."""
    out, seen = [path], {path}
    p = path
    for _ in range(32):
        if p not in parent:
            break
        p = parent[p][0]
        if p in seen:
            break
        seen.add(p)
        out.append(p)
    return out


def file_authorship(conn, repo: str, files: list[str], log=print) -> dict[str, str]:
    """path -> 'authored' | 'inherited' for the given worktree files."""
    blob_hashes = {r[0] for r in conn.execute(
        "SELECT commit_hash FROM commit_files GROUP BY commit_hash "
        "HAVING COUNT(*) > ?", (_BLOB_FILES,))}
    founding = conn.execute(
        "SELECT hash FROM commits WHERE is_merge=0 ORDER BY authored_at LIMIT 1").fetchone()
    if founding:
        blob_hashes.add(founding[0])

    first, parent = history_scan(repo)
    out: dict[str, str] = {}
    n_auth = 0
    for f in files:
        root = _aliases(f, parent)[-1]
        added_by = (first.get(root) or ("", ""))[1]
        out[f] = "authored" if added_by and added_by not in blob_hashes else "inherited"
        n_auth += out[f] == "authored"
    log(f"  delta: {n_auth}/{len(files)} files authored, "
        f"{len(files) - n_auth} inherited ({len(blob_hashes)} blob commits excluded)")
    return out
