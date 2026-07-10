"""Taxonomy INDUCTION — build the feature label-space once, from clustered concern facets.

This replaces open-set feature naming (every batch free to invent names → drift, near-dups,
vague buckets) with the battle-tested two-phase shape (TnT-LLM / Clio): first *induce* a fixed
taxonomy from the data, then *classify* everything against it by ID (see classify.py).

Induction: embed each concern's FACET (label + one-sentence summary + top paths — richer than
the label alone, so generic labels like "Options UI" still separate), cluster with the shared
CPM-Leiden core, then have the LLM name each proto-cluster CONTRASTIVELY — it sees samples
from the nearest *other* clusters and must pick a name that distinguishes this one. Each
feature gets a DEFINITION (one sentence + includes/excludes) — the definition, not a global
prompt heuristic, is where specificity lives. A single reconcile pass merges near-duplicates
at taxonomy level (O(features), not O(concerns)).

Induction runs ONCE per knowledge base: if a taxonomy already exists the stage is a no-op and
incremental runs go straight to classification. Human review of the result is an optional
seam (taxonomy CLI), never a gate.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from ..storage import now_iso

# Contrastive cluster naming. Abstract guidance only — concrete example nouns get parroted
# onto unrelated clusters by small models, so none appear here.
NAME_SYS = (
    "You are reconstructing the functional FEATURE taxonomy of a software project from its "
    "change history. You get ONE cluster of related code changes (each: label — summary "
    "| files) plus CONTRAST samples that belong to OTHER, neighbouring clusters, plus "
    "GLOSSARY CANDIDATES — entities from the project's own vocabulary (derived from its "
    "file names and documentation) whose evidence overlaps this cluster. Name the single "
    "concrete feature/capability this cluster implements and define it.\n"
    "Rules: if the cluster IS one of the glossary candidates — or is the user-interface, "
    "frontend, or tooling FACET of one — use THE CANDIDATE'S name: a feature owns its UI "
    "and its tools; a window/screen/panel for an entity is that entity, never a separate "
    "'X window' feature. Only a generic reusable building block serving MANY entities "
    "stays its own component, named specifically. Otherwise: the name is a specific noun "
    "phrase (<=4 words) a maintainer would recognise as a feature of THIS project — never "
    "a generic bucket, never a lone mechanism word; when changes serve a feature THROUGH a "
    "generic mechanism, name the feature, not the mechanism. The name must distinguish "
    "this cluster from the CONTRAST samples. The definition states what the feature is and "
    "gives includes/excludes criteria sharp enough that a classifier can decide borderline "
    "changes.\n"
    'Respond with ONE JSON object: {"name":"...","definition":"<one sentence>. '
    'Includes: ... Excludes: ..."}'
)

RECONCILE_SYS = (
    "You are reviewing a machine-generated feature taxonomy of ONE software project: a "
    "numbered list of feature names with definitions. Fix exactly two defect types:\n"
    "1. NEAR-DUPLICATES — entries that are the same feature under different names. Merge "
    "them (keep the best-named entry as canonical). Distinct-but-similar features (e.g. two "
    "features that share a noun but have different includes/excludes) stay SEPARATE.\n"
    "2. VAGUE names — an entry whose name is a generic bucket that could apply to any "
    "software project. Rename it to the concrete capability its definition describes.\n"
    'Respond with ONE JSON object: {"merges":[[keep_id, dup_id, ...], ...], '
    '"renames":{"<id>":"New Name"}} — empty lists/objects if nothing to fix.'
)


def facet_text(label: str, summary: str | None, files: list[str]) -> str:
    """The embedding/classification facet of one concern: what it does + where it lives."""
    paths = ", ".join(files[:3])
    parts = [label or ""]
    if summary and summary.strip() and summary.strip().lower() != (label or "").strip().lower():
        parts.append(summary.strip())
    txt = ". ".join(p for p in parts if p)
    return f"{txt} (files: {paths})" if paths else txt


def load_facets(conn) -> tuple[list[int], dict[int, str], dict[int, dict]]:
    """All labelled concerns → (ids, facet text, raw row info). Vendored-drop/remainder
    concerns (origin='import-misc') exist for completeness but are never clustered or
    classified — their labels are containers, not capabilities."""
    ids, texts, info = [], {}, {}
    for r in conn.execute(
            "SELECT id, label, summary, files, commit_hash FROM concerns "
            "WHERE label IS NOT NULL AND label != '' "
            "AND (origin IS NULL OR origin != 'import-misc') ORDER BY id"):
        files = json.loads(r["files"] or "[]")
        ids.append(r["id"])
        texts[r["id"]] = facet_text(r["label"], r["summary"], files)
        info[r["id"]] = {"label": r["label"], "summary": r["summary"] or "", "files": files,
                         "commit": r["commit_hash"]}
    return ids, texts, info


def embed_facets(conn, provider, ids, texts, log=print) -> dict[int, np.ndarray]:
    """Embed concern facets (cached per concern in the embeddings table, type 'facet')."""
    model = provider.embed_cfg["model"]
    have = {int(r["target_id"]) for r in conn.execute(
        "SELECT target_id FROM embeddings WHERE target_type='facet' AND model=?", (model,))}
    todo = [cid for cid in ids if cid not in have]
    if todo:
        log(f"  embedding {len(todo)} concern facets ...")
        # chunked write-as-you-go: embedding thousands of facets on a local backend can die
        # mid-way (measured); losing at most one chunk makes the re-run a cheap resume
        for i in range(0, len(todo), 256):
            part = todo[i:i + 256]
            vecs = provider.embed([texts[cid] for cid in part])
            dim = int(vecs.shape[1])
            for cid, v in zip(part, vecs):
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (target_type,target_id,model,dim,vector) "
                    "VALUES ('facet',?,?,?,?)",
                    (str(cid), model, dim, np.asarray(v, dtype=np.float32).tobytes()))
            conn.commit()
            if i and i % 2048 == 0:
                log(f"    {i}/{len(todo)} facets embedded")
    out = {}
    for r in conn.execute(
            "SELECT target_id, vector FROM embeddings WHERE target_type='facet' AND model=?",
            (model,)):
        cid = int(r["target_id"])
        if cid in texts:
            v = np.frombuffer(r["vector"], dtype=np.float32)
            out[cid] = v / (np.linalg.norm(v) + 1e-9)
    return out


def taxonomy_exists(conn) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM domains WHERE definition IS NOT NULL "
        "AND status IN ('named','provisional','confirmed')").fetchone()[0] > 0


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "feature"


def _ctx_line(info: dict) -> str:
    fs = ", ".join(f.rsplit("/", 1)[-1] for f in info["files"][:5])
    s = info["summary"][:110]
    return f"{info['label']}" + (f" — {s}" if s else "") + (f" |files: {fs}" if fs else "")


def cluster_stems(member_infos: list[dict], census: set[str] | None = None,
                  top: int = 8) -> list[str]:
    """Dominant path stems of a group of concerns — the aggregate evidence of WHAT the
    group is about. A stem counts once per concern; kept if it covers >=30% of members."""
    from .ground import path_stems
    cnt = Counter()
    for info in member_infos:
        seen = set()
        for f in info["files"]:
            seen |= path_stems(f)
        for s in seen:
            cnt[s] += 1
    n = max(1, len(member_infos))
    keep = [s for s, c in cnt.most_common(40) if c >= max(2, 0.3 * n)]
    if census:
        keep.sort(key=lambda s: (s not in census,))   # census-known stems first
    return keep[:top]


def glossary_matches(conn, stems: list[str], top: int = 3) -> list[dict]:
    """Glossary entities whose evidence stems overlap the given stems, best first."""
    sset = set(stems)
    out = []
    for r in conn.execute("SELECT id, name, definition, stems FROM glossary"):
        est = set(json.loads(r["stems"] or "[]"))
        ov = len(est & sset)
        if ov:
            out.append({"id": r["id"], "name": r["name"],
                        "definition": r["definition"] or "", "overlap": ov,
                        "stems": sorted(est & sset)})
    out.sort(key=lambda e: -e["overlap"])
    return out[:top]


def induce(conn, provider, cfg: dict, git_head: str | None, rev_range: str,
           log=print, force: bool = False) -> dict:
    """Build the feature taxonomy (domains with definitions). No concern assignment here —
    classify.py assigns every concern uniformly against the frozen taxonomy."""
    cat = cfg.get("catalog", {})
    if taxonomy_exists(conn) and not force:
        n = conn.execute("SELECT COUNT(*) FROM domains WHERE definition IS NOT NULL "
                         "AND status IN ('named','provisional','confirmed')").fetchone()[0]
        log(f"  taxonomy exists ({n} features) — skipping induction (incremental mode)")
        return {"induced": 0, "existing": n}

    ids, texts, info = load_facets(conn)
    if len(ids) < 4:
        log("  not enough concerns to induce a taxonomy (run untangle first)")
        return {"induced": 0}
    vec = embed_facets(conn, provider, ids, texts, log)
    ids = [cid for cid in ids if cid in vec]
    Mn = np.vstack([vec[cid] for cid in ids])
    cfiles = {r["id"]: r["files"] for r in conn.execute("SELECT id, files FROM concerns")}

    from ..catalog.catalog import cluster_concerns
    min_cluster = int(cat.get("induce_min_cluster", 3))
    membership, res, mod = cluster_concerns(ids, Mn, cfiles, cat, log, min_cluster)
    clusters: dict[int, list[int]] = defaultdict(list)   # comm -> node positions
    for node, comm in enumerate(membership):
        clusters[comm].append(node)
    proto = {c: nodes for c, nodes in clusters.items() if len(nodes) >= min_cluster}
    log(f"  {len(proto)} proto-clusters (>= {min_cluster} concerns) of {len(clusters)} total; "
        f"tail concerns are handled by classification")

    # Representatives (nearest centroid + 2 far members for spread) and contrastive
    # neighbours (labels from the 2 nearest OTHER clusters), all precomputed in the main
    # thread — the SQLite conn is not shared with workers.
    cents = {c: Mn[nodes].mean(0) / (np.linalg.norm(Mn[nodes].mean(0)) + 1e-9)
             for c, nodes in proto.items()}
    order = sorted(proto)
    centM = np.vstack([cents[c] for c in order])
    simC = centM @ centM.T
    census = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=0")}
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    jobs = []
    for pi, c in enumerate(order):
        nodes = proto[c]
        d = Mn[nodes] @ cents[c]
        by_near = [nodes[i] for i in np.argsort(-d)]
        reps = by_near[:8] + ([by_near[-1]] if len(by_near) > 9 else [])
        rep_lines = [_ctx_line(info[ids[nd]]) for nd in reps]
        contrast_lines = []
        for qi in np.argsort(-simC[pi])[1:3]:
            cn = order[int(qi)]
            near = [proto[cn][i] for i in np.argsort(-(Mn[proto[cn]] @ cents[cn]))[:3]]
            contrast_lines += [info[ids[nd]]["label"] for nd in near]
        # repo-level grounding: the cluster's dominant path stems + matching glossary entities
        stems = [s for s in cluster_stems([info[ids[nd]] for nd in nodes], census)
                 if s not in god]
        gloss = glossary_matches(conn, stems)
        jobs.append((c, rep_lines, contrast_lines, len(nodes), stems, gloss))

    workers = int(cfg.get("untangle", {}).get("workers", 6))
    log(f"  naming {len(jobs)} clusters contrastively ({workers} workers) ...")

    def name_one(job):
        c, reps, contrast, size, stems, gloss = job
        gblock = "\n".join(f"- {g['name']} — {g['definition'][:120]} "
                           f"(shared evidence: {', '.join(g['stems'][:4])})" for g in gloss)
        from ..scope import load_charter as _lc
        _cb = _lc()
        _cb = ("OWNER CHARTER (project knowledge - follow where relevant):\n" + _cb + "\n\n") if _cb else ""
        user = (_cb + "CLUSTER CHANGES:\n" + "\n".join(f"- {l}" for l in reps)
                + (f"\n\nDOMINANT FILE STEMS: {', '.join(stems)}" if stems else "")
                + "\n\nGLOSSARY CANDIDATES:\n" + (gblock or "(none)")
                + "\n\nCONTRAST (samples from OTHER nearby clusters — do NOT cover these):\n"
                + "\n".join(f"- {l}" for l in contrast)
                + '\n\nName and define the feature. JSON: {"name":"...","definition":"..."}')
        try:
            out = provider.chat(NAME_SYS, user, want_json=True, cache_extra=f"tax-name:{c}")
        except Exception:  # noqa: BLE001
            return c, None
        if not isinstance(out, dict) or not (out.get("name") or "").strip():
            return c, None
        name = str(out["name"]).strip()[:60]
        # if the model adopted a glossary entity, inherit its evidence stems too
        adopted = next((g for g in gloss if g["name"].lower() == name.lower()), None)
        allstems = list(dict.fromkeys(stems + (adopted["stems"] if adopted else [])))
        return c, {"name": name, "definition": str(out.get("definition") or "").strip()[:500],
                   "size": size, "stems": allstems}

    named: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(name_one, j) for j in jobs]):
            c, res_ = fut.result()
            if res_:
                named[c] = res_
    log(f"  {len(named)} clusters named")

    # --- reconcile at taxonomy level: merge near-dups, rename vague names (one call) ---
    order2 = sorted(named)
    listing = "\n".join(f"{i}: {named[c]['name']} — {named[c]['definition'][:160]}"
                        for i, c in enumerate(order2))
    merged_into: dict[int, int] = {}
    try:
        from ..scope import load_charter as _lc2
        _cb2 = _lc2()
        _cb2 = ("OWNER CHARTER:\n" + _cb2 + "\n\n") if _cb2 else ""
        out = provider.chat(RECONCILE_SYS, _cb2 + f"TAXONOMY:\n{listing}", want_json=True, large=True,
                            cache_extra=f"tax-reconcile:{len(order2)}")
    except Exception:  # noqa: BLE001
        out = {}
    if isinstance(out, dict):
        for grp in (out.get("merges") or []):
            try:
                grp = [int(x) for x in grp]
            except (TypeError, ValueError):
                continue
            valid = [x for x in grp if 0 <= x < len(order2)]
            if len(valid) > 1:
                keep = valid[0]
                for dup in valid[1:]:
                    merged_into[dup] = keep
        for k, v in (out.get("renames") or {}).items():
            try:
                i = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(order2) and str(v).strip():
                named[order2[i]]["name"] = str(v).strip()[:60]
    n_merged = len(merged_into)

    # --- write the taxonomy (respecting tombstones; concerns assigned by classify) ---
    tomb = {r["name"].lower() for r in conn.execute("SELECT name FROM taxonomy_tombstones")}
    doomed = "status IN ('candidate','named','provisional') AND locked=0"
    conn.execute(f"UPDATE concerns SET domain_id=NULL, assign_source=NULL, assign_conf=NULL "
                 f"WHERE domain_id IN (SELECT id FROM domains WHERE {doomed})")
    conn.execute(f"DELETE FROM domains WHERE {doomed}")
    run_id = conn.execute(
        "INSERT INTO discovery_runs (algorithm, params, git_head, rev_range, n_files, n_domains, "
        "modularity, created_at) VALUES ('taxonomy-induce',?,?,?,?,?,?,?)",
        (json.dumps({"resolution": res, "min_cluster": min_cluster}), git_head, rev_range,
         len(ids), len(named) - n_merged, mod, now_iso())).lastrowid

    # Same-name clusters MERGE into one feature (this is how a UI-facet cluster that
    # adopted its glossary entity's name folds into the feature): union their stems.
    seen: dict[str, int] = {}
    n_written = n_tomb = n_folded = 0
    for i, c in enumerate(order2):
        if i in merged_into:
            continue
        f = named[c]
        key = f["name"].lower()
        if key in tomb:
            n_tomb += 1
            continue
        if key in seen:
            did = seen[key]
            row = conn.execute("SELECT stems FROM domains WHERE id=?", (did,)).fetchone()
            merged_stems = list(dict.fromkeys(json.loads(row["stems"] or "[]") + f.get("stems", [])))
            conn.execute("UPDATE domains SET stems=?, named_from=named_from+? WHERE id=?",
                         (json.dumps(merged_stems[:12]), f["size"], did))
            n_folded += 1
            continue
        seen[key] = conn.execute(
            "INSERT INTO domains (discovery_run_id, name, slug, definition, stems, named_from, "
            "classification, status, created_by) VALUES (?,?,?,?,?,?, 'feature','named','auto')",
            (run_id, f["name"], _slug(f["name"]), f["definition"],
             json.dumps(f.get("stems", [])[:12]), f["size"])).lastrowid
        n_written += 1
    conn.commit()
    log(f"  taxonomy: {n_written} features induced ({n_merged} near-dups merged, "
        f"{n_folded} facet-clusters folded by glossary name, {n_tomb} tombstoned suppressed)")
    return {"induced": n_written, "merged": n_merged, "folded": n_folded, "run_id": run_id}
