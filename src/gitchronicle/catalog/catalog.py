"""Cluster CONCERNS into domains — the semantic core of the pipeline.

We embed each concern's label (a capability phrase read from the diff) and cluster the
embeddings with Leiden. We deliberately DO NOT use file co-change: files are god-nodes
(char.cpp serves 13 concerns) and would blob unrelated work. A domain is a cluster of
semantically-related concerns; its files and commits are DERIVED (many-to-many), so a
god-file legitimately belongs to many domains.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict

import igraph as ig
import numpy as np

from ..storage import now_iso


def _ari(a, b) -> float:
    """Adjusted Rand Index between two labelings — agreement corrected for chance."""
    n = len(a)
    if n < 2:
        return 1.0
    nab = Counter(zip(a, b))
    na, nb = Counter(a), Counter(b)
    comb = lambda x: x * (x - 1) / 2  # noqa: E731
    sij = sum(comb(v) for v in nab.values())
    sa = sum(comb(v) for v in na.values())
    sb = sum(comb(v) for v in nb.values())
    tot = comb(n)
    exp = sa * sb / tot if tot else 0.0
    mx = (sa + sb) / 2
    return (sij - exp) / (mx - exp) if mx != exp else 1.0


def _choose_partition(g, sweep, min_size, log):
    """CPM Leiden (resolution-limit-free) at the coarsest STABLE resolution. No target, no knobs.

    Plain modularity has a resolution limit: it under-resolves large dense regions, merging
    several real pieces into one blob while small pieces stay separate. CPM clusters at a uniform
    density scale instead. We pick the CPM resolution data-drivenly by partition STABILITY —
    the agreement (ARI) of each partition with its neighbours in the sweep — and take the first
    (coarsest) interior local maximum: the coarsest partition that is robust to the exact
    resolution. This yields uniform granularity: no blob, no swarm of singletons.
    """
    gammas = list(sweep)
    parts = {gm: g.community_leiden(objective_function="CPM", weights="weight",
                                    resolution=gm, n_iterations=5) for gm in gammas}
    stab = {}
    for i, gm in enumerate(gammas):
        nb = []
        if i > 0:
            nb.append(_ari(parts[gm].membership, parts[gammas[i - 1]].membership))
        if i < len(gammas) - 1:
            nb.append(_ari(parts[gm].membership, parts[gammas[i + 1]].membership))
        stab[gm] = sum(nb) / len(nb) if nb else 0.0
        eff = sum(1 for s in Counter(parts[gm].membership).values() if s >= min_size)
        log(f"    gamma={gm}: domains(>= {min_size} concerns)={eff}, stability={stab[gm]:.3f}")
    pick = None
    for i in range(1, len(gammas) - 1):   # first interior local max = coarsest robust scale
        gm = gammas[i]
        if stab[gm] >= stab[gammas[i - 1]] and stab[gm] >= stab[gammas[i + 1]]:
            pick = gm
            break
    if pick is None:                      # monotonic curve: fall back to most stable interior
        pick = max(gammas[1:-1] or gammas, key=lambda gm: stab[gm])
    part = parts[pick]
    mod = g.modularity(part.membership, weights="weight")
    log(f"    -> gamma={pick} (stability {stab[pick]:.3f}, modularity {mod:.3f})")
    return pick, part, mod


def _embed_concerns(conn, provider, log):
    model = provider.embed_cfg["model"]
    rows = conn.execute("SELECT id, label FROM concerns WHERE label IS NOT NULL AND label != ''").fetchall()
    have = {r["target_id"] for r in conn.execute(
        "SELECT target_id FROM embeddings WHERE target_type='concern' AND model=?", (model,))}
    todo = [(r["id"], r["label"]) for r in rows if str(r["id"]) not in have]
    if todo:
        log(f"  embedding {len(todo)} concern labels ...")
        vecs = provider.embed([l for _, l in todo])
        dim = int(vecs.shape[1])
        for (cid, _), v in zip(todo, vecs):
            conn.execute("INSERT OR REPLACE INTO embeddings (target_type,target_id,model,dim,vector) "
                         "VALUES ('concern',?,?,?,?)", (str(cid), model, dim,
                                                        np.asarray(v, dtype=np.float32).tobytes()))
        conn.commit()
    vec = {int(r["target_id"]): np.frombuffer(r["vector"], dtype=np.float32)
           for r in conn.execute("SELECT target_id, vector FROM embeddings WHERE target_type='concern' AND model=?",
                                 (model,))}
    return vec


def catalog(conn, provider, cfg: dict, rev_range: str, git_head: str | None = None, log=print) -> dict:
    cat = cfg.get("catalog", {})
    if cat.get("method", "leiden") == "semantic":
        from .semantic import reconstruct
        return reconstruct(conn, provider, cfg, git_head, rev_range, log)
    knn = int(cat.get("knn", 10))
    min_size = int(cat.get("min_domain_concerns", 2))
    # Seed igraph's RNG so Leiden is reproducible run-to-run (deterministic pipeline).
    random.seed(int(cat.get("seed", 20240607)))
    ig.set_random_number_generator(random)

    vec = _embed_concerns(conn, provider, log)
    cfiles = {r["id"]: r["files"] for r in conn.execute("SELECT id, files FROM concerns")}
    ids = [r["id"] for r in conn.execute("SELECT id FROM concerns ORDER BY id") if r["id"] in vec]
    n = len(ids)
    if n < 2:
        log("  not enough concerns to cluster (run untangle first)")
        return {"domains": 0}

    M = np.vstack([vec[i] for i in ids])
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    S = Mn @ Mn.T

    # (1) Semantic label edges: kNN on concern-label embeddings, baseline-rescaled.
    k = min(knn + 1, n)
    pairs = set()
    for i in range(n):
        for j in np.argpartition(-S[i], k - 1)[:k]:
            j = int(j)
            if j != i:
                pairs.add((i, j) if i < j else (j, i))
    coslist = [float(S[i, j]) for i, j in pairs]
    base = float(np.percentile(coslist, 60)) if coslist else 0.5   # bge-m3 has a high baseline
    span = max(1e-6, 1.0 - base)
    wmap: dict[tuple, float] = {}
    for i, j in pairs:
        w = max(0.0, (float(S[i, j]) - base) / span)
        if w > 0:
            wmap[(i, j)] = w

    # (2) Rare-file affinity: concerns that touch the same FEATURE-SPECIFIC file are related
    # even when their labels are generic ("Options UI", "Interface"). We weight each shared
    # file by 1/df so hubs count for little, and hard-exclude god-files (df above the 95th
    # percentile) so a client/server god-file can never re-blob unrelated concerns.
    idx = {cid: p for p, cid in enumerate(ids)}
    file_concerns: dict[str, list[int]] = defaultdict(list)
    for cid in ids:
        for f in json.loads(cfiles.get(cid) or "[]"):
            file_concerns[f].append(idx[cid])
    dfvals = np.array([len(v) for v in file_concerns.values()]) if file_concerns else np.array([1])
    df_cap = int(min(40, max(6, np.percentile(dfvals, 95))))
    fw = float(cat.get("file_affinity_weight", 0.6))
    aff: dict[tuple, float] = defaultdict(float)
    skipped_hubs = 0
    for cs in file_concerns.values():
        dfc = len(cs)
        if dfc < 2:
            continue
        if dfc > df_cap:            # god-file — excluded (would re-create blobs)
            skipped_hubs += 1
            continue
        contrib = 1.0 / dfc
        cs = sorted(set(cs))
        for a in range(len(cs)):
            for b in range(a + 1, len(cs)):
                aff[(cs[a], cs[b])] += contrib
    for pair, a in aff.items():
        wmap[pair] = wmap.get(pair, 0.0) + fw * min(1.0, a)

    edges = list(wmap.keys())
    weights = [wmap[e] for e in edges]
    g = ig.Graph(n=n)
    g.add_edges(edges)
    g.es["weight"] = weights
    log(f"  {n} concerns, {len(edges)} edges "
        f"(label baseline={base:.2f}; +{len(aff)} rare-file affinity pairs, "
        f"df_cap={df_cap}, {skipped_hubs} god-files excluded)")

    res, part, mod = _choose_partition(
        g, list(cat.get("cpm_gamma_sweep", [0.008, 0.012, 0.016, 0.02, 0.025, 0.03, 0.04, 0.05, 0.07, 0.09])),
        min_size, log)
    membership = part.membership
    clusters = defaultdict(list)
    for node, comm in enumerate(membership):
        clusters[comm].append(ids[node])
    kept = {c: cs for c, cs in clusters.items() if len(cs) >= min_size}

    # Release concern assignments before dropping the domains they reference (FK), then
    # re-cluster from scratch. Confirmed/locked domains and their concerns are left intact.
    doomed = "status IN ('candidate','named') AND locked=0"
    conn.execute(f"UPDATE concerns SET domain_id=NULL WHERE domain_id IN "
                 f"(SELECT id FROM domains WHERE {doomed})")
    conn.execute(f"DELETE FROM domains WHERE {doomed}")
    run_id = conn.execute(
        "INSERT INTO discovery_runs (algorithm, params, git_head, rev_range, n_files, n_domains, "
        "modularity, created_at) VALUES ('leiden-concerns',?,?,?,?,?,?,?)",
        (json.dumps({"resolution": res, "knn": knn}), git_head, rev_range, n, len(kept), mod, now_iso()),
    ).lastrowid

    labels = {r["id"]: (r["label"] or "") for r in conn.execute("SELECT id, label FROM concerns")}
    domain_nodes = []   # (domain_id, [node positions]) — for the level-2 area clustering
    for cs in kept.values():
        temp = _temp_label([labels[i] for i in cs])
        did = conn.execute("INSERT INTO domains (discovery_run_id, slug, name, status, created_by) "
                           "VALUES (?,?,?,?,?)", (run_id, temp, temp, "candidate", "auto")).lastrowid
        conn.executemany("UPDATE concerns SET domain_id=? WHERE id=?", [(did, cid) for cid in cs])
        domain_nodes.append((did, [idx[cid] for cid in cs]))
    conn.commit()
    _derive_files(conn)
    conn.commit()

    if cat.get("consolidate"):
        domain_nodes = _consolidate_domains(conn, domain_nodes, Mn, cat, log)
        conn.commit()

    n_areas = _build_areas(conn, run_id, domain_nodes, Mn, ids, cfiles, cat, log)
    log(f"  {len(domain_nodes)} domains in {n_areas} areas (modularity {mod:.3f})")
    return {"domains": len(domain_nodes), "areas": n_areas, "concerns": n,
            "modularity": mod, "run_id": run_id}


def _consolidate_domains(conn, domain_nodes, Mn, cat, log):
    """Step 3: merge near-DUPLICATE domains (same feature split into several) by domain-centroid
    cosine. Deterministic, no LLM. This is the post-processing that cleans the fragmentation the
    area hierarchy doesn't — file affinity can't merge these because their labels genuinely differ."""
    thr = float(cat.get("consolidate_min", 0.90))
    D = len(domain_nodes)
    if D < 2:
        return domain_nodes
    cent = np.vstack([Mn[nodes].mean(0) for _, nodes in domain_nodes])
    cent = cent / (np.linalg.norm(cent, axis=1, keepdims=True) + 1e-9)
    Sc = cent @ cent.T
    parent = list(range(D))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(D):
        for j in range(i + 1, D):
            if Sc[i, j] >= thr:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    groups = defaultdict(list)
    for i in range(D):
        groups[find(i)].append(i)
    merged = sum(len(g) - 1 for g in groups.values())
    if not merged:
        log(f"  consolidate: no near-duplicate domains (cosine >= {thr})")
        return domain_nodes
    new_nodes = []
    for members in groups.values():
        members.sort(key=lambda m: -len(domain_nodes[m][1]))   # canonical = biggest
        canon = domain_nodes[members[0]][0]
        all_nodes = list(domain_nodes[members[0]][1])
        for m in members[1:]:
            did = domain_nodes[m][0]
            conn.execute("UPDATE concerns SET domain_id=? WHERE domain_id=?", (canon, did))
            conn.execute("DELETE FROM domains WHERE id=?", (did,))
            all_nodes += domain_nodes[m][1]
        new_nodes.append((canon, all_nodes))
    conn.commit()
    _derive_files(conn)
    log(f"  consolidate: merged {merged} near-duplicate domains ({D} -> {len(new_nodes)})")
    return new_nodes


def _spherical_kmeans(X, k, seed, iters=50):
    """Deterministic cosine k-means (X rows are unit vectors). Returns integer labels."""
    rng = np.random.RandomState(seed)
    n = X.shape[0]
    # k-means++ seeding on cosine distance
    chosen = [int(rng.randint(n))]
    nearest = 1.0 - X @ X[chosen[0]]
    for _ in range(k - 1):
        d = np.clip(nearest, 1e-12, None)
        chosen.append(int(rng.choice(n, p=d / d.sum())))
        nearest = np.minimum(nearest, 1.0 - X @ X[chosen[-1]])
    C = X[chosen].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        new = (X @ C.T).argmax(1)
        if np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            m = X[labels == j]
            if len(m):
                v = m.mean(0)
                C[j] = v / (np.linalg.norm(v) + 1e-9)
    return labels


def _build_areas(conn, run_id, domain_nodes, Mn, ids, cfiles, cat, log) -> int:
    """Level-2 hierarchy: group the fine DOMAINS into a bounded, navigable set of AREAS by
    balanced semantic clustering (cosine k-means on domain centroids). Graph clustering collapses
    the dense engine core into one mega-area; k-means gives balanced topical groups instead — the
    right basis for a top-level navigation layer. k scales ~sqrt(#domains), capped."""
    conn.execute("DELETE FROM areas WHERE status IN ('candidate','named')")
    conn.execute("UPDATE domains SET area_id=NULL")
    D = len(domain_nodes)
    cent = np.vstack([Mn[nodes].mean(0) for _, nodes in domain_nodes])
    cent = cent / (np.linalg.norm(cent, axis=1, keepdims=True) + 1e-9)
    k = min(int(cat.get("max_areas", 60)), max(1, int(round(np.sqrt(D) * 1.6))))
    if D <= k:
        labels = np.arange(D)
        k = D
    else:
        labels = _spherical_kmeans(cent, k, int(cat.get("seed", 20240607)))
    log(f"    grouping {D} domains into {len(set(labels))} areas (k-means)")

    groups = defaultdict(list)
    for di, am in enumerate(labels):
        groups[int(am)].append(di)
    area_of = {}
    for am, dis in groups.items():
        aid = conn.execute("INSERT INTO areas (discovery_run_id, name, slug, status, created_by) "
                           "VALUES (?,?,?,?,?)", (run_id, f"area {am}", f"area-{am}", "candidate", "auto")).lastrowid
        for di in dis:
            area_of[domain_nodes[di][0]] = aid
    conn.executemany("UPDATE domains SET area_id=? WHERE id=?",
                     [(aid, did) for did, aid in area_of.items()])
    conn.execute("UPDATE areas SET n_domains=(SELECT COUNT(*) FROM domains WHERE area_id=areas.id) "
                 "WHERE status='candidate'")
    conn.commit()
    return len(groups)


def _temp_label(concern_labels):
    words = Counter()
    for lab in concern_labels:
        for w in lab.lower().replace("-", " ").split():
            if len(w) > 3:
                words[w] += 1
    return " ".join(w for w, _ in words.most_common(3)) or "domain"


import math
import re as _re

# non-source build/debug/binary artifacts — never a feature's characteristic file
_ARTIFACT_RE = _re.compile(
    r"\.(exe|dll|pyc|pyo|pyd|zip|7z|rar|gz|dmp|dump|png|jpg|jpeg|gif|bmp|dds|tga|ico|"
    r"wav|mp3|ogg|ttf|otf|bin|lib|obj|pdb|so|a|o|class|jar)$", _re.I)


def _derive_files(conn):
    """A domain's files are the union of its concerns' (untangled) file subsets — but weighted by
    SPECIFICITY, not raw count. A file touched by many domains (a god-class like char.cpp, or a
    generic mechanism file) carries little feature signal, so we down-weight it by inverse
    domain-frequency (idf); a file unique to one domain is characteristic and keeps full weight.
    Non-source artifacts (binaries, .pyc, images) are dropped entirely."""
    raw = defaultdict(Counter)
    for r in conn.execute("SELECT domain_id, files FROM concerns WHERE domain_id IS NOT NULL"):
        for f in json.loads(r["files"] or "[]"):
            if _ARTIFACT_RE.search(f):
                continue
            raw[r["domain_id"]][f] += 1
    # document frequency: number of distinct domains each file appears in
    df = Counter()
    for files in raw.values():
        for f in files:
            df[f] += 1
    n_dom = max(1, len(raw))
    conn.execute("DELETE FROM domain_files")
    for did, files in raw.items():
        rows = []
        for f, k in files.items():
            idf = math.log((n_dom + 1) / (df[f] + 1)) + 1e-3
            rows.append((did, f, float(k) * idf))
        conn.executemany("INSERT OR REPLACE INTO domain_files (domain_id, path, weight) VALUES (?,?,?)", rows)
        conn.execute("UPDATE domains SET n_files=? WHERE id=?", (len(files), did))
