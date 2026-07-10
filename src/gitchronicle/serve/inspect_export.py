"""Per-stage validation payload -> self-contained inspect.html (read-only GUI).

One tab per pipeline stage output: untangle concerns, ground census+glossary, the
taxonomy (features with definitions/stems/status), every assignment with provenance,
stem-coherence violations, the pending changeset, and areas. The GUI never writes —
it emits review-plan verb lines to the clipboard; the plan file stays the single
write path.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from ..storage import now_iso
from ..taxonomy.check import health, stem_coherence
from ..taxonomy.ops import pending_changeset
from .inspect_template import INSPECT_TEMPLATE

_MAX_ASSIGN = 20000
_MAX_CENSUS = 400


def _build_payload(conn) -> dict:
    h = health(conn)

    # --- untangle: concerns grouped per commit (newest first, capped) ---
    commits = {r["hash"]: {"hash": r["hash"][:10], "subject": (r["subject"] or "")[:120],
                           "date": (r["authored_at"] or "")[:10]}
               for r in conn.execute("SELECT hash, subject, authored_at FROM commits "
                                     "WHERE is_merge=0 ORDER BY authored_at DESC LIMIT 1200")}
    unt = defaultdict(list)
    for r in conn.execute("SELECT commit_hash, label, summary, files FROM concerns "
                          "WHERE label IS NOT NULL ORDER BY id"):
        if r["commit_hash"] in commits and len(unt[r["commit_hash"]]) < 8:
            unt[r["commit_hash"]].append({
                "label": r["label"], "summary": (r["summary"] or "")[:200],
                "files": [f.rsplit("/", 1)[-1] for f in json.loads(r["files"] or "[]")[:6]]})
    untangle = [{**commits[hh], "concerns": cc} for hh, cc in unt.items()]
    untangle.sort(key=lambda c: c["date"], reverse=True)

    # --- ground: census + glossary ---
    census = [{"stem": r["stem"], "files": r["n_files"], "activity": r["n_concerns"],
               "ubiquity": r["ubiquity"],
               "samples": [p.rsplit("/", 1)[-1] for p in json.loads(r["sample_paths"] or "[]")[:3]]}
              for r in conn.execute("SELECT * FROM stem_census ORDER BY n_concerns DESC "
                                    f"LIMIT {_MAX_CENSUS}")]
    glossary = [{"name": r["name"], "definition": r["definition"] or "",
                 "stems": json.loads(r["stems"] or "[]"),
                 "docs": (json.loads(r["evidence"] or "{}").get("docs") or []),
                 "source": r["source"] or ""}
                for r in conn.execute("SELECT * FROM glossary ORDER BY name")]

    # --- taxonomy features ---
    feats = []
    fnames = {}
    for r in conn.execute(
            "SELECT d.id, d.name, d.definition, d.stems, d.status, d.locked, d.named_from, "
            "a.name AS area, (SELECT COUNT(*) FROM concerns c WHERE c.domain_id=d.id) n "
            "FROM domains d LEFT JOIN areas a ON a.id=d.area_id "
            "WHERE d.status IN ('named','provisional','confirmed') ORDER BY n DESC"):
        fnames[r["id"]] = r["name"]
        feats.append({"id": r["id"], "name": r["name"], "definition": r["definition"] or "",
                      "stems": json.loads(r["stems"] or "[]"), "status": r["status"],
                      "locked": bool(r["locked"]), "named_from": r["named_from"],
                      "area": r["area"] or "", "n": r["n"]})

    # --- assignments (provenance for every concern) ---
    assigns = []
    for r in conn.execute(
            "SELECT c.id, c.commit_hash, c.label, c.summary, c.files, c.domain_id, "
            "c.assign_source, c.assign_conf FROM concerns c WHERE c.label IS NOT NULL "
            f"ORDER BY c.id DESC LIMIT {_MAX_ASSIGN}"):
        assigns.append({
            "id": r["id"], "commit": r["commit_hash"][:10], "label": r["label"],
            "summary": (r["summary"] or "")[:160],
            "files": [f.rsplit("/", 1)[-1] for f in json.loads(r["files"] or "[]")[:5]],
            "feature": fnames.get(r["domain_id"], "") or "(unassigned)",
            "fid": r["domain_id"], "source": r["assign_source"] or "",
            "conf": r["assign_conf"]})

    # --- coherence: worst stems + per-stem feature distribution ---
    coh = stem_coherence(conn)
    from ..taxonomy.ground import path_stems
    worst = {c["stem"] for c in coh[:40]}
    dist: dict[str, Counter] = defaultdict(Counter)
    for r in conn.execute("SELECT files, domain_id FROM concerns WHERE domain_id IS NOT NULL"):
        st = set()
        for f in json.loads(r["files"] or "[]"):
            st |= path_stems(f)
        for s in st & worst:
            dist[s][fnames.get(r["domain_id"], "?")] += 1
    coherence = [{**c, "dist": dict(dist.get(c["stem"], {}))} for c in coh[:80]]

    # --- areas + changeset ---
    areas = defaultdict(list)
    for f in feats:
        areas[f["area"] or "(none)"].append(f["name"])
    changeset = pending_changeset(conn)

    return {"meta": {"generated_at": now_iso(), **{k: h[k] for k in
                     ("features", "concerns", "unassigned", "incoherent_stems")}},
            "health": h, "untangle": untangle[:800], "census": census,
            "glossary": glossary, "features": feats, "assignments": assigns,
            "coherence": coherence, "areas": dict(areas), "changeset": changeset}


def export_inspect(conn, html_path: str | Path, log=print) -> dict:
    payload = _build_payload(conn)
    html = INSPECT_TEMPLATE.replace("__INSPECT_DATA__", json.dumps(payload, ensure_ascii=False))
    Path(html_path).write_text(html, encoding="utf-8")
    m = payload["meta"]
    log(f"  wrote {html_path} ({m['features']} features, {m['concerns']} concerns, "
        f"{len(payload['glossary'])} glossary entities)")
    return {"html": str(html_path), **m}
