"""AUTHORED DELTA — the first partition of a fork's worktree is inherited vs authored.

void-queue derives from Metin2: much of HEAD arrived in import/sync/reorg blob commits
and was never the owner's work. v0.1 catalogued it all as feature material and produced
god features from vanilla code ('Guild Diplomacy and War System' = inherited
PythonGuild bindings).

klass — CREATION PROVENANCE with rename chains (the dev/ -> root restructure must not
launder history). Touch counts are NOT authorship (PythonGuild.cpp: 25 maintenance
touches, never the owner's feature; god-files collect hundreds). The owner's features
ADD files.

  authored  — the file's rename-chain ROOT was added by a work commit
  inherited — it arrived via the founding import or an import-majority mega drop

Mega drops (> _BLOB_FILES files) are judged by their untangled content: majority
import-origin concerns = a drop (locale syncs, vendor updates); majority work = the
owner's own mega feature commit (CCC: 1,857 files). Files born INSIDE an import drop
are rescued when either (a) a work concern lists them, or (b) their directory became a
DEVELOPMENT SITE right after birth (>= _DEV_SITE_MIN later work commits touch it) —
uchtml was born inside a 98%-import wiki drop and then actively developed; a locale
drop never is.
"""

from __future__ import annotations

import json
from collections import defaultdict

from ..extract.git_ingest import run_git

_BLOB_FILES = 150
_DEV_SITE_MIN = 3


def history_scan(repo: str):
    """One pass over --all: per path, the (date, commit) that first ADDED it, plus the
    rename parent map. Deterministic; ~seconds on 6.5k commits."""
    first: dict[str, tuple[str, str]] = {}
    parent: dict[str, tuple[str, str]] = {}
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
    big = [r[0] for r in conn.execute(
        "SELECT commit_hash FROM commit_files GROUP BY commit_hash "
        "HAVING COUNT(*) > ?", (_BLOB_FILES,))]
    blob_hashes: set = set()
    work_at: dict[str, set] = {}
    for h in big:
        work: set = set()
        tot = imp = 0
        for r in conn.execute("SELECT files, origin FROM concerns WHERE commit_hash=?", (h,)):
            tot += 1
            if (r["origin"] or "") in ("import", "import-misc"):
                imp += 1
            else:
                work.update(json.loads(r["files"] or "[]"))
        if tot and imp / tot < 0.5:
            continue                        # majority-work mega drop = the owner's work
        blob_hashes.add(h)
        if work:
            work_at[h] = work
    founding = conn.execute(
        "SELECT hash FROM commits WHERE is_merge=0 ORDER BY authored_at LIMIT 1").fetchone()
    if founding:
        blob_hashes.add(founding[0])
        work_at.pop(founding[0], None)      # the founding import births nothing

    # development-site evidence: which directories the owner's WORK commits touched
    dir_work: dict[str, set] = defaultdict(set)
    for r in conn.execute("SELECT path, commit_hash FROM commit_files"):
        if r[1] not in blob_hashes:
            dir_work[r[0].rsplit("/", 1)[0]].add(r[1])

    first, parent = history_scan(repo)
    fh = founding[0] if founding else ""
    out: dict[str, str] = {}
    n_auth = 0
    for f in files:
        root = _aliases(f, parent)[-1]
        added_by = (first.get(root) or ("", ""))[1]
        if added_by and added_by not in blob_hashes:
            klass = "authored"
        elif added_by and root in work_at.get(added_by, ()):
            klass = "authored"              # a work concern inside an import drop
        elif added_by and added_by != fh \
                and len(dir_work.get(root.rsplit("/", 1)[0], ())) >= _DEV_SITE_MIN:
            klass = "authored"              # born in a drop, then actively developed
        else:
            klass = "inherited"
        out[f] = klass
        n_auth += klass == "authored"
    log(f"  delta: {n_auth}/{len(files)} files authored, "
        f"{len(files) - n_auth} inherited ({len(blob_hashes)} blob commits)")
    return out
