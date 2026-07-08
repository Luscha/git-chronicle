"""Project the hierarchical knowledge base into a self-contained HTML browser + JSON.

Large histories produce a 3-level hierarchy — AREAS (top-level sections) → DOMAINS (fine
pieces) → CONCERNS/commits. We emit a lightweight payload: areas and domains carry summaries
and references, and commit metadata is deduplicated into one shared map (a commit touched by
several domains is stored once). The viewer is a 3-pane browser (areas → domains → detail).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from ..storage import now_iso
from .template import HTML_TEMPLATE

_LIFECYCLE_COLOR = {"active": "#54A24B", "dormant": "#EECA3B",
                    "merged": "#4C78A8", "removed": "#E45756"}
_MAX_CONCERNS = 18
_MAX_FILES = 15
_MAX_COMMITS = 40


def _build_payload(conn) -> dict:
    # --- domains (lightweight) grouped by area ---
    drows = conn.execute(
        "SELECT id, area_id, name, slug, classification, tags, lifecycle, "
        "n_commits, n_files, first_seen, last_seen FROM domains WHERE status='named'"
    ).fetchall()

    concerns_by = defaultdict(list)
    for r in conn.execute(
        "SELECT domain_id, label, COUNT(*) n FROM concerns WHERE domain_id IS NOT NULL "
        "AND label IS NOT NULL GROUP BY domain_id, label ORDER BY n DESC"):
        lst = concerns_by[r["domain_id"]]
        if len(lst) < _MAX_CONCERNS:
            lst.append({"label": r["label"], "n": r["n"]})
    files_by = defaultdict(list)
    for r in conn.execute(
        "SELECT domain_id, path FROM domain_files ORDER BY weight DESC"):
        lst = files_by[r["domain_id"]]
        if len(lst) < _MAX_FILES:
            lst.append(r["path"])
    commits_by = defaultdict(list)
    for r in conn.execute(
        "SELECT cd.domain_id, cd.commit_hash, c.authored_at FROM commit_domains cd "
        "JOIN commits c ON c.hash=cd.commit_hash ORDER BY c.authored_at DESC"):
        lst = commits_by[r["domain_id"]]
        if len(lst) < _MAX_COMMITS:
            lst.append(r["commit_hash"][:10])

    domains = []
    ref_hashes = set()
    for d in drows:
        did = d["id"]
        chashes = commits_by.get(did, [])
        ref_hashes.update(chashes)
        lc = d["lifecycle"] or "active"
        domains.append({
            "id": did, "area_id": d["area_id"],
            "name": d["name"] or d["slug"] or f"domain {did}",
            "classification": d["classification"] or "feature",
            "tags": json.loads(d["tags"]) if d["tags"] else [],
            "lifecycle": lc, "color": _LIFECYCLE_COLOR.get(lc, "#9D755D"),
            "n_commits": d["n_commits"] or 0, "n_files": d["n_files"] or 0,
            "first_seen": (d["first_seen"] or "")[:10], "last_seen": (d["last_seen"] or "")[:10],
            "concerns": concerns_by.get(did, []), "files": files_by.get(did, []),
            "commits": chashes,
        })

    # --- shared, deduped commit metadata (referenced by hash) ---
    commits = {}
    for r in conn.execute(
        "SELECT hash, subject, body, authored_at, author_name, kind FROM commits WHERE is_merge=0"):
        h = r["hash"][:10]
        if h in ref_hashes:
            commits[h] = {"subject": (r["subject"] or "")[:600],
                          "body": (r["body"] or "").strip()[:1200],
                          "date": (r["authored_at"] or "")[:10],
                          "author": r["author_name"] or "", "kind": r["kind"] or ""}

    # --- areas (top level) ---
    dom_by_area = defaultdict(list)
    for dd in domains:
        dom_by_area[dd["area_id"]].append(dd)
    areas = []
    for a in conn.execute(
        "SELECT id, name, slug, classification, tags FROM areas WHERE status='named'"):
        ads = dom_by_area.get(a["id"], [])
        firsts = [x["first_seen"] for x in ads if x["first_seen"]]
        lasts = [x["last_seen"] for x in ads if x["last_seen"]]
        areas.append({
            "id": a["id"], "name": a["name"] or a["slug"] or f"area {a['id']}",
            "classification": a["classification"] or "", "tags": json.loads(a["tags"]) if a["tags"] else [],
            "n_domains": len(ads), "n_commits": sum(x["n_commits"] for x in ads),
            "n_concerns": sum(len(x["concerns"]) for x in ads),
            "first_seen": min(firsts) if firsts else "", "last_seen": max(lasts) if lasts else "",
        })
    areas.sort(key=lambda a: -a["n_commits"])

    return {
        "meta": {
            "generated_at": now_iso(),
            "n_areas": len(areas), "n_domains": len(domains),
            "n_commits": len(commits), "n_concerns": sum(len(d["concerns"]) for d in domains),
        },
        "areas": areas, "domains": domains, "commits": commits,
    }


def export_graph(conn, html_path: str | Path, json_path: str | Path, log=print) -> dict:
    payload = _build_payload(conn)
    if not payload["domains"]:
        log("  no domains to export (run catalog + discover first)")
        return {"domains": 0}
    Path(json_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    m = payload["meta"]
    html = (HTML_TEMPLATE
            .replace("__GRAPH_DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__N_AREAS__", str(m["n_areas"]))
            .replace("__N_DOMAINS__", str(m["n_domains"]))
            .replace("__N_COMMITS__", str(m["n_commits"]))
            .replace("__GENERATED__", m["generated_at"]))
    Path(html_path).write_text(html, encoding="utf-8")
    log(f"  wrote {html_path} ({m['n_areas']} areas, {m['n_domains']} domains)")
    log(f"  wrote {json_path}")
    return {"areas": m["n_areas"], "domains": m["n_domains"],
            "html": str(html_path), "json": str(json_path)}
