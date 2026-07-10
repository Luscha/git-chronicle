"""Extract commits, file churn, branch membership and tags/eras from a repo.

Backend: the system ``git`` CLI via subprocess (portable, and more robust than
bundling a libgit2 build). Kept behind this module so it is swappable.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path

from ..storage import mark_stage, now_iso

# ASCII separators unlikely to appear in commit metadata.
US = "\x1f"  # unit separator  -> between fields
RS = "\x1e"  # record separator -> end of the format line, before numstat
CM = "\x01__C__\x01"  # start-of-commit marker

_FIELDS = ["%H", "%P", "%an", "%ae", "%cn", "%ce", "%aI", "%cI", "%s", "%b"]


class BatchReader:
    """Persistent ``git cat-file --batch`` process: thousands of blob reads without one
    subprocess spawn each (a 2,000-file import's family analysis would otherwise fork
    ~15k times). Not thread-safe — create one per worker and close it."""

    def __init__(self, repo: str | Path):
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, rev: str, path: str, limit: int | None = None) -> str:
        try:
            self._proc.stdin.write(f"{rev}:{path}\n".encode())
            self._proc.stdin.flush()
            header = self._proc.stdout.readline().decode("utf-8", "replace")
            parts = header.split()
            if len(parts) != 3 or parts[1] != "blob":
                return ""
            size = int(parts[2])
            data = self._proc.stdout.read(size + 1)[:size]   # always consume trailing NL
            text = data.decode("utf-8", "replace")
            return text[:limit] if limit else text
        except (BrokenPipeError, ValueError, OSError):
            return ""

    def close(self) -> None:
        try:
            self._proc.stdin.close()
            self._proc.terminate()
        except OSError:
            pass


def run_git(repo: str | Path, args: list[str], check: bool = True) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{res.stderr.strip()}")
    return res.stdout


def rev_list(repo: str | Path, rev_range: str) -> list[str]:
    """All commit hashes reachable in a rev-range (newest first), merges included. Anchored at the
    default branch, this is its FULL history — every merged-PR commit is an ancestor and included;
    only never-merged feature branches are naturally excluded. The range may hold several
    whitespace-separated revs (e.g. 'A..B ^C' to exclude a huge merged branch)."""
    out = run_git(repo, ["rev-list", *rev_range.split()])
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _parse_numstat(block: str) -> tuple[list[dict], int, int]:
    files: list[dict] = []
    ins_tot = del_tot = 0
    for line in block.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        is_bin = a == "-" or d == "-"
        ai = 0 if a == "-" else int(a)
        di = 0 if d == "-" else int(d)
        ins_tot += ai
        del_tot += di
        files.append({"path": path, "insertions": ai, "deletions": di, "is_binary": int(is_bin)})
    return files, ins_tot, del_tot


def extract_commits(repo: str | Path, rev_range: str) -> list[dict]:
    """Parse commit metadata + per-file churn in a single ``git log`` pass."""
    fmt = CM + US.join(_FIELDS) + RS
    out = run_git(repo, ["log", "--no-renames", "--numstat", f"--format={fmt}", *rev_range.split()])
    commits: list[dict] = []
    for chunk in out.split(CM):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition(RS)
        fields = head.split(US)
        if len(fields) < len(_FIELDS):
            continue
        h, parents, an, ae, cn, ce, ai, ci, subj = fields[:9]
        body = US.join(fields[9:])  # body is last; tolerate stray separators
        files, ins_tot, del_tot = _parse_numstat(rest)
        parent_list = parents.split() if parents.strip() else []
        commits.append({
            "hash": h.strip(),
            "parent_hashes": parent_list,
            "author_name": an, "author_email": ae,
            "committer_name": cn, "committer_email": ce,
            "authored_at": ai, "committed_at": ci,
            "subject": subj, "body": body,
            "is_merge": int(len(parent_list) > 1),
            "insertions": ins_tot, "deletions": del_tot,
            "files_changed": len(files),
            "files": files,
        })
    return commits


def _norm_branch(name: str) -> str | None:
    name = name.strip()
    if not name or name.endswith("/HEAD"):
        return None
    if name.startswith("origin/"):
        name = name[len("origin/"):]
    return name or None


def _resolve_trunk(repo: str | Path) -> str:
    """The default branch, e.g. 'origin/master' — the mainline to measure against."""
    ref = run_git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"], check=False).strip()
    if ref.startswith("refs/remotes/"):
        return ref[len("refs/remotes/"):]
    for cand in ("origin/master", "master", "origin/main", "main"):
        if run_git(repo, ["rev-parse", "--verify", "--quiet", cand], check=False).strip():
            return cand
    return "master"


def _pick_ref(repo: str | Path, short: str) -> str | None:
    for cand in (short, f"origin/{short}"):
        if run_git(repo, ["rev-parse", "--verify", "--quiet", cand], check=False).strip():
            return cand
    return None


def branch_map(repo: str | Path, hashes: set[str], since_iso: str | None) -> dict[str, list[str]]:
    """Map each commit hash -> the feature branches it is *distinctive* to.

    Uses ``trunk..branch`` (commits ahead of the mainline), NOT the branch's whole
    ancestry — otherwise every commit looks like it's on every branch. Branch names
    here double as feature labels (dev/avatar-war, feat/ccc), so this is the strongest
    clustering signal once made discriminative.
    """
    trunk = _resolve_trunk(repo)
    trunk_short = trunk.split("/", 1)[-1] if "/" in trunk else trunk
    refs = run_git(repo, ["for-each-ref", "--format=%(refname:short)",
                          "refs/heads", "refs/remotes"]).splitlines()
    branches: list[str] = []
    seen = set()
    for r in refs:
        nb = _norm_branch(r)
        if nb and nb not in seen and nb != trunk_short:
            seen.add(nb)
            branches.append(nb)

    result: dict[str, list[str]] = defaultdict(list)
    for b in branches:
        ref = _pick_ref(repo, b)
        if ref is None:
            continue
        args = ["rev-list"]
        if since_iso:
            args.append(f"--since={since_iso}")
        args.append(f"{trunk}..{ref}")
        for ln in run_git(repo, args, check=False).splitlines():
            hh = ln.strip()
            if hh in hashes:
                result[hh].append(b)
    return result


def extract_eras(repo: str | Path) -> list[dict]:
    """Tags -> chronological eras (each era spans until the next tag)."""
    out = run_git(repo, ["for-each-ref", "--sort=creatordate",
                         f"--format=%(refname:short){US}%(creatordate:iso-strict)",
                         "refs/tags"])
    eras: list[dict] = []
    for ln in out.splitlines():
        if US not in ln:
            continue
        name, date = ln.split(US, 1)
        eras.append({"name": name.strip(), "tag_ref": name.strip(),
                     "time_start": date.strip(), "time_end": None})
    for i in range(len(eras) - 1):
        eras[i]["time_end"] = eras[i + 1]["time_start"]
    return eras


def ingest(conn, repo: str | Path, rev_range: str, log=print) -> dict:
    """Extract + persist commits/files/branches/eras. Resumable: skips known hashes."""
    commits = extract_commits(repo, rev_range)
    if not commits:
        log("  no commits in range")
        return {"commits": 0, "new": 0}

    existing = {r["hash"] for r in conn.execute("SELECT hash FROM commits")}
    new = [c for c in commits if c["hash"] not in existing]
    log(f"  {len(commits)} commits in range, {len(new)} new to ingest")

    for c in new:
        conn.execute(
            "INSERT OR IGNORE INTO commits "
            "(hash, author_name, author_email, committer_name, committer_email, "
            " authored_at, committed_at, subject, body, is_merge, parent_hashes, "
            " insertions, deletions, files_changed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c["hash"], c["author_name"], c["author_email"], c["committer_name"],
             c["committer_email"], c["authored_at"], c["committed_at"], c["subject"],
             c["body"], c["is_merge"], json.dumps(c["parent_hashes"]),
             c["insertions"], c["deletions"], c["files_changed"]),
        )
        if c["files"]:
            conn.executemany(
                "INSERT INTO commit_files (commit_hash, path, insertions, deletions, is_binary) "
                "VALUES (?,?,?,?,?)",
                [(c["hash"], f["path"], f["insertions"], f["deletions"], f["is_binary"])
                 for f in c["files"]],
            )
        mark_stage(conn, "extract", c["hash"])
    conn.commit()

    # Branch attribution over the whole range (idempotent), bounded by earliest date.
    hashes = {c["hash"] for c in commits}
    since = min((c["authored_at"] for c in commits if c["authored_at"]), default=None)
    since_date = since.split("T")[0] if since else None
    bmap = branch_map(repo, hashes, since_date)
    for hh, brs in bmap.items():
        conn.executemany(
            "INSERT OR IGNORE INTO commit_branches (commit_hash, branch) VALUES (?,?)",
            [(hh, b) for b in brs],
        )

    # Eras from tags.
    for e in extract_eras(repo):
        conn.execute(
            "INSERT OR IGNORE INTO eras (name, tag_ref, time_start, time_end) VALUES (?,?,?,?)",
            (e["name"], e["tag_ref"], e["time_start"], e["time_end"]),
        )
    conn.commit()

    n_branch_links = sum(len(v) for v in bmap.values())
    log(f"  branch links: {n_branch_links}; tags/eras: "
        f"{conn.execute('SELECT COUNT(*) FROM eras').fetchone()[0]}")
    return {"commits": len(commits), "new": len(new), "branch_links": n_branch_links}
