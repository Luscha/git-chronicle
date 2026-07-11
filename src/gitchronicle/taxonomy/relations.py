"""RELATIONS — built-on/uses edges between register features, from static imports.

The original product idea ("avatar uses luna under the hood — it registers its handlers
via luna") resurrected on evidence that works: feature A's territory files import/include
files owned by feature B's territory => A *uses* B. Deterministic, local, no LLM — the
co-change guessing that produced tautologies is gone for good.

A feature that many others use is a framework hub — exactly the "major framework on top
of which I created many other systems" narrative anchor the chronicle needs.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from ..extract.git_ingest import BatchReader
from .imports import extract_import_refs

_MAX_FILES_PER_FEATURE = 80    # territory files read per feature (by weight)
_MIN_EDGE_FILES = 2            # distinct importing files needed to assert an edge
_HUB_MIN_FANIN = 5             # used by >=5 other features => framework hub


def build_relations(conn, repo: str, log=print) -> dict:
    feats = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM domains WHERE status IN ('named','confirmed','provisional')")}
    if not feats:
        log("  no register — run `gitchronicle register` first")
        return {"edges": 0}

    # file -> owning feature: register territory (worktree truth) beats history-derived
    # weight; files claimed by many features are ambiguous glue and own nothing
    claims: dict[str, list] = defaultdict(list)
    for r in conn.execute("SELECT domain_id, path, weight, source FROM domain_files"):
        if r["domain_id"] in feats:
            claims[r["path"]].append(
                (1 if r["source"] == "register" else 0, r["weight"] or 0, r["domain_id"]))
    owner: dict[str, int] = {}
    for path, cs in claims.items():
        reg = [c for c in cs if c[0] == 1]
        if len(reg) == 1:
            owner[path] = reg[0][2]
        elif not reg and len(cs) <= 3:
            owner[path] = max(cs)[2]
    by_base: dict[str, list[int]] = defaultdict(list)
    for path, did in owner.items():
        base = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        by_base[base].append(did)

    # read each feature's top territory files once; collect cross-feature references
    per_feat_files: dict[int, list[str]] = defaultdict(list)
    for path, did in owner.items():
        if len(per_feat_files[did]) < _MAX_FILES_PER_FEATURE:
            per_feat_files[did].append(path)
    reader = BatchReader(repo)
    edge_files: dict[tuple, set] = defaultdict(set)
    try:
        for did, files in per_feat_files.items():
            for f in files:
                text = reader.read("HEAD", f, limit=15000)
                if not text:
                    continue
                for ref in extract_import_refs(text):
                    owners = by_base.get(ref, [])
                    if len(owners) == 1 and owners[0] != did:
                        edge_files[(did, owners[0])].add(f)
    finally:
        reader.close()

    conn.execute("DELETE FROM domain_edges WHERE status != 'confirmed' AND locked = 0")
    n = 0
    used_by: Counter = Counter()
    for (a, b), files in edge_files.items():
        if len(files) < _MIN_EDGE_FILES:
            continue
        conn.execute(
            "INSERT INTO domain_edges (src_domain, dst_domain, type, weight, why, status) "
            "VALUES (?,?,'uses',?,?, 'named')",
            (a, b, float(len(files)),
             f"{len(files)} files in '{feats[a]}' import '{feats[b]}' territory"))
        used_by[b] += 1
        n += 1

    # framework hubs: heavily-used features carry the 'built-on' narrative
    hubs = []
    for did, cnt in used_by.most_common():
        if cnt >= _HUB_MIN_FANIN:
            conn.execute("UPDATE domains SET classification='core', fan_in=? WHERE id=?",
                         (cnt, did))
            hubs.append((feats[did], cnt))
    conn.commit()
    log(f"  {n} uses-edges; hubs: " + (", ".join(f"{h} (used by {c})" for h, c in hubs[:6])
                                       or "none"))
    return {"edges": n, "hubs": hubs[:10]}
