"""Classify each domain's lifecycle against the analysed ref (overlay) tip.

Code-grounded, not message-based:
  - most files gone at the tip           -> removed (removed_at = last activity)
  - gone, but their basenames moved       -> merged  (files relocated into other code)
  - present but long inactive             -> dormant (the 'deprecated' signal)
  - otherwise                             -> active

The tip is a single ref — gitchronicle analyses one branch overlay at a time and
never meshes branches. Tags are only time-pins used by the --as-of query filter.
"""

from __future__ import annotations

from datetime import datetime

from ..extract.git_ingest import run_git


def _epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def lifecycle(conn, repo: str, tip: str = "HEAD", cfg: dict | None = None, log=print) -> dict:
    lc = (cfg or {}).get("lifecycle", {})
    dormancy = float(lc.get("dormancy_days", 120)) * 86400.0
    removed_thr = float(lc.get("removed_threshold", 0.25))
    merged_thr = float(lc.get("merged_threshold", 0.5))

    alive = set(run_git(repo, ["ls-tree", "-r", "--name-only", tip], check=False).splitlines())
    alive_basenames = {p.rsplit("/", 1)[-1] for p in alive}
    tip_e = _epoch(run_git(repo, ["show", "-s", "--format=%aI", tip], check=False).strip())

    counts = {"active": 0, "dormant": 0, "merged": 0, "removed": 0}
    for d in conn.execute("SELECT id, last_seen FROM domains").fetchall():
        files = [r["path"] for r in conn.execute(
            "SELECT path FROM domain_files WHERE domain_id=?", (d["id"],))]
        if not files:
            continue
        present = sum(1 for f in files if f in alive)
        frac_present = present / len(files)
        last_e = _epoch(d["last_seen"])

        if frac_present < removed_thr:
            gone = [f for f in files if f not in alive]
            moved = sum(1 for f in gone if f.rsplit("/", 1)[-1] in alive_basenames)
            state = "merged" if gone and moved / len(gone) >= merged_thr else "removed"
            removed_at = d["last_seen"]
        elif last_e and tip_e and (tip_e - last_e) > dormancy:
            state, removed_at = "dormant", None
        else:
            state, removed_at = "active", None

        conn.execute("UPDATE domains SET lifecycle=?, removed_at=? WHERE id=?",
                     (state, removed_at, d["id"]))
        counts[state] += 1
    conn.commit()
    log("  lifecycle: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return counts
