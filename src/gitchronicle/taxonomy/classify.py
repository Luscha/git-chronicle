"""Closed-set CLASSIFICATION of concerns against the frozen taxonomy — by ID.

Every concern is assigned through the same cascade, cheapest sufficient signal first
(the untangle routing philosophy, applied to classification):

1. **fast path** — cosine of the concern facet vs feature-definition embeddings; a clear
   top-1 (margin over top-2 + absolute floor) is assigned with no LLM call.
2. **LLM pick-by-ID** — ambiguous concerns get a shortlist of top-k candidate features
   (definitions shown once per batch); the model returns a feature ID from that shortlist
   or 0 (= none fits). An ID can't be misspelled, paraphrased or vague; anything outside
   the shortlist is retried once solo, then treated as 0.
3. **propose-new** — the 0s accumulate and are grouped into NEW features in one batched
   pass (tombstoned names are never re-proposed). New features enter as status
   'provisional': used immediately, flagged in outputs, pending the optional review seam.
   With frozen=True this step is skipped and the 0s stay explicitly unassigned.
4. **audit** — per-feature embedding outliers (low cosine to the member centroid) are
   re-classified; targeted repair instead of global re-passes.

Provisional features that keep attracting concerns across runs auto-confirm, so a fully
unattended install converges without a human ever stepping in.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from ..storage import now_iso
from .facets import _slug, cluster_stems, embed_facets, load_facets



CLASSIFY_SYS = (
    "You classify code changes into a software project's existing FEATURES. The FEATURES "
    "section lists candidate features as 'id: name — definition'. Each CHANGE (label — "
    "summary |files) has its own candidate id list. For each change pick the SINGLE "
    "best-fitting feature id FROM ITS OWN candidates, judging by what the change does and "
    "each feature's includes/excludes — a feature spans many files and directories. A change "
    "that merely USES a generic mechanism belongs to the feature it serves, not the "
    "mechanism. If no candidate genuinely fits, answer 0 — never force a bad fit: a weak "
    "thematic resemblance (same genre, same subsystem vocabulary, same broad area) is NOT a "
    "fit. Answering 0 is correct and expected for work on capabilities not yet in the list.\n"
    'Respond with ONE JSON object: {"picks":{"<change number>": <feature id or 0>, ...}}'
)

PROPOSE_SYS = (
    "These code changes (label — summary |files) did NOT fit any existing feature of the "
    "project. Group them into NEW, specific features: each a concrete noun phrase (<=4 "
    "words) a maintainer would recognise — never a generic bucket that could apply to any "
    "project — with a definition (one sentence + Includes/Excludes). Reuse of an EXISTING "
    "feature name means the change actually belongs there — allowed. NEVER use a REJECTED "
    "name. Changes that are trivial one-offs may stay ungrouped (omit them).\n"
    'Respond with ONE JSON object: {"features":[{"name":"...","definition":"...",'
    '"changes":[<change numbers>]}]}'
)

NOVELTY_SYS = (
    "A group of code changes in one software project matched NO existing feature, but they "
    "cohere: they share file-name stems and change semantics. Decide what FEATURE this group "
    "implements and define it. Name the capability at feature altitude (what a maintainer "
    "would call the system), NOT the specific change in front of you — a duration tweak to a "
    "boss system is the boss system. Use the project's own vocabulary: the shared stems and "
    "any glossary candidates given. Never use a REJECTED name. If the group is genuinely "
    "incoherent, return an empty name.\n"
    'Respond with ONE JSON object: {"name":"...","definition":"<one sentence>. '
    'Includes: ... Excludes: ..."}'
)

RENAME_SYS = (
    "A machine-proposed feature of one software project was named when it had very few "
    "changes; it has since accumulated more. Re-name it from ALL its member changes at "
    "feature altitude — what a maintainer calls the whole capability, not the founding "
    "change's detail. Keep the project's vocabulary (shared stems given). If the current "
    "name is already right, return it unchanged.\n"
    'Respond with ONE JSON object: {"name":"...","definition":"..."}'
)


def taxonomy_rows(conn) -> list:
    return conn.execute(
        "SELECT id, name, definition, stems, status, locked FROM domains "
        "WHERE status IN ('named','provisional','confirmed') ORDER BY id").fetchall()


def concern_stems(info: dict) -> set:
    from .ground import path_stems
    st = set()
    for f in info["files"]:
        st |= path_stems(f)
    return st


def embed_definitions(conn, provider, rows) -> tuple[list[int], np.ndarray]:
    """Embed 'name — definition' per feature. Rebuilt per run (cheap, O(features))."""
    model = provider.embed_cfg["model"]
    conn.execute("DELETE FROM embeddings WHERE target_type='taxdef'")
    ids = [r["id"] for r in rows]
    texts = [f"{r['name']} — {r['definition'] or ''}" for r in rows]
    vecs = provider.embed(texts)
    dim = int(vecs.shape[1])
    for did, v in zip(ids, vecs):
        conn.execute("INSERT OR REPLACE INTO embeddings (target_type,target_id,model,dim,vector) "
                     "VALUES ('taxdef',?,?,?,?)",
                     (str(did), model, dim, np.asarray(v, dtype=np.float32).tobytes()))
    conn.commit()
    M = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    return ids, M


def _ctx(info: dict) -> str:
    fs = ", ".join(f.rsplit("/", 1)[-1] for f in info["files"][:5])
    s = (info["summary"] or "")[:110]
    return f"{info['label']}" + (f" — {s}" if s else "") + (f" |files: {fs}" if fs else "")


def _llm_pick(provider, batch, feat_line, cache_tag):
    """One batched pick-by-ID call. batch = [(concern_id, ctx, shortlist_ids)].
    Returns {concern_id: picked_id_or_0_or_None(invalid)}."""
    union = sorted({fid for _, _, sl in batch for fid in sl})
    feats = "\n".join(feat_line[fid] for fid in union)
    changes = "\n".join(
        f"[{j}] {ctx}\n    candidates: {', '.join(str(x) for x in sl)} or 0"
        for j, (_, ctx, sl) in enumerate(batch))
    user = f"FEATURES:\n{feats}\n\nCHANGES:\n{changes}"
    try:
        out = provider.chat(CLASSIFY_SYS, user, want_json=True, cache_extra=cache_tag)
    except Exception:  # noqa: BLE001
        return {cid: None for cid, _, _ in batch}
    picks = out.get("picks", {}) if isinstance(out, dict) else {}
    res = {}
    for j, (cid, _, sl) in enumerate(batch):
        raw = picks.get(str(j), picks.get(j))
        try:
            p = int(raw)
        except (TypeError, ValueError):
            res[cid] = None
            continue
        res[cid] = p if (p == 0 or p in sl) else None   # outside shortlist = invalid
    return res


def _births(conn, repo: str) -> dict[int, str]:
    """Feature birth = first git appearance (day precision) of any register-territory
    file, FOLLOWED THROUGH RENAMES — repo restructures (dev/X -> X) must not reset
    births or every old feature would look newborn and lose its early history.
    Temporal grounding: semantic matching alone happily attributes 2016 work to a
    2023 framework; a feature cannot own commits older than its own code."""
    from ..extract.git_ingest import run_git
    first: dict[str, str] = {}
    parent: dict[str, tuple[str, str]] = {}     # new path -> (old path, date), earliest wins
    date = ""
    out = run_git(repo, ["-c", "diff.renameLimit=100000", "log", "--all",
                         "--find-renames", "--diff-filter=AR", "--name-status",
                         "--date=format:%Y-%m-%d", "--format=%x01%ad"])
    for ln in out.splitlines():
        if ln.startswith("\x01"):
            date = ln[1:].strip()
            continue
        if not ln or not date or "\t" not in ln:
            continue
        if ln[0] == "A":
            p2 = ln.split("\t", 1)[1].strip()
            if p2 not in first or date < first[p2]:
                first[p2] = date
        elif ln[0] == "R":
            parts = ln.split("\t")
            if len(parts) == 3:
                old2, new2 = parts[1].strip(), parts[2].strip()
                if new2 not in parent or date < parent[new2][1]:
                    parent[new2] = (old2, date)

    memo: dict[str, str] = {}

    def birth(p2: str, depth: int = 0) -> str:
        if p2 in memo:
            return memo[p2]
        best = first.get(p2, "")
        if depth < 32 and p2 in parent:
            old2, d = parent[p2]
            memo[p2] = best or d                # cycle guard before recursing
            b = birth(old2, depth + 1) or d
            if not best or b < best:
                best = b
        memo[p2] = best
        return best

    births: dict[int, str] = {}
    for r in conn.execute("SELECT domain_id, path FROM domain_files WHERE source='register'"):
        d = birth(r["path"])
        if d and (r["domain_id"] not in births or d < births[r["domain_id"]]):
            births[r["domain_id"]] = d
    conn.executemany("UPDATE domains SET born_at=? WHERE id=?",
                     [(b, did) for did, b in births.items()])
    conn.commit()
    return births


def classify(conn, provider, cfg: dict, git_head: str | None = None, rev_range: str = "",
             frozen: bool = False, force: bool = False, log=print) -> dict:
    cat = cfg.get("catalog", {})
    k = int(cat.get("shortlist_k", 8))
    batch_n = int(cat.get("classify_batch", 12))
    fast_margin = float(cat.get("fast_margin", 0.05))
    fast_floor = float(cat.get("fast_floor", 0.62))
    workers = int(cfg.get("untangle", {}).get("workers", 6))

    rows = taxonomy_rows(conn)
    if not rows:
        log("  no taxonomy — run induction first")
        return {"classified": 0}
    run_id = conn.execute(
        "INSERT INTO discovery_runs (algorithm, params, git_head, rev_range, created_at) "
        "VALUES ('taxonomy-classify','{}',?,?,?)", (git_head, rev_range, now_iso())).lastrowid
    if force:
        conn.execute("UPDATE concerns SET domain_id=NULL, assign_source=NULL, assign_conf=NULL "
                     "WHERE assign_source != 'human' OR assign_source IS NULL")
        conn.commit()

    ids_all, texts, info = load_facets(conn)
    vec = embed_facets(conn, provider, ids_all, texts, log)
    todo = [r["id"] for r in conn.execute(
        "SELECT id FROM concerns WHERE domain_id IS NULL AND label IS NOT NULL AND (origin IS NULL OR origin != 'import-misc') ORDER BY id")
        if r["id"] in vec]

    # --- stage 0: TERRITORY EVIDENCE — a concern whose files sit dominantly in one
    # feature's register territory belongs there; file facts outrank semantics and are
    # never second-guessed by the audit.
    active = {r["id"] for r in rows}
    terr_own: dict[str, list[int]] = defaultdict(list)
    for r in conn.execute("SELECT domain_id, path FROM domain_files WHERE source='register'"):
        if r["domain_id"] in active:
            terr_own[r["path"]].append(r["domain_id"])
    n_terr = 0
    remaining = []
    for cid in todo:
        hits = Counter()
        for f in info[cid]["files"]:
            for did in terr_own.get(f, []):
                hits[did] += 1
        top2 = hits.most_common(2)
        if top2 and (len(top2) == 1 or top2[0][1] >= 2 * top2[1][1]):
            conn.execute("UPDATE concerns SET domain_id=?, assign_source='territory', "
                         "assign_conf=1.0 WHERE id=?", (top2[0][0], cid))
            n_terr += 1
        else:
            remaining.append(cid)
    conn.commit()
    todo = remaining
    log(f"  territory evidence: {n_terr} assigned by register-file overlap")

    # temporal grounding for every semantic stage below
    births = _births(conn, cfg.get("repo", {}).get("path", "."))
    cdates = {r[0]: (r[1] or "")[:10] for r in
              conn.execute("SELECT hash, authored_at FROM commits")}
    fids, F = embed_definitions(conn, provider, rows)
    feat_line = {r["id"]: f"{r['id']}: {r['name']} — {(r['definition'] or '')[:200]}" for r in rows}
    names = {r["id"]: r["name"] for r in rows}
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    feat_stems = {r["id"]: set(json.loads(r["stems"] or "[]")) - god for r in rows}
    stem_boost = float(cat.get("stem_boost", 0.12))
    log(f"  classifying {len(todo)} concerns against {len(fids)} features "
        f"(k={k}, fast: margin>={fast_margin}, floor>={fast_floor}, stem_boost={stem_boost})")
    if not todo:
        _auto_confirm(conn, run_id, cat, log)
        _post(conn, provider, log)
        return {"classified": 0, "features": len(fids), "unassigned": 0, "proposed": []}

    # --- stage 1: shortlist + fast path on BOOSTED score (cosine + stem-overlap evidence).
    # A concern touching uiAvatarBuilder.py carries aggregate path evidence for the feature
    # owning the 'avatar builder' stem — embeddings alone can't see that.
    C = np.vstack([vec[cid] for cid in todo])
    S = C @ F.T                                     # concerns x features (raw cosine)
    fpos_all = {fid: i for i, fid in enumerate(fids)}
    shortlists: dict[int, list[int]] = {}
    ambiguous: list[tuple] = []                     # (cid, ctx, shortlist)
    n_fast = 0
    margins = []
    barr = np.array([births.get(fid, "") for fid in fids])
    for i, cid in enumerate(todo):
        cstems = concern_stems(info[cid])
        score = S[i].copy()
        for fid, fst in feat_stems.items():
            if fst and (fst & cstems):
                score[fpos_all[fid]] += stem_boost
        cd = cdates.get(info[cid]["commit"], "")
        if cd:
            score[barr > cd] = -9.0     # feature born after this commit: not a candidate
        srt = np.argsort(-score)
        top = [fids[int(j)] for j in srt[:k] if score[int(j)] > -8.0]
        shortlists[cid] = top
        t1, t2 = float(score[srt[0]]), float(score[srt[1]]) if len(fids) > 1 else 0.0
        margins.append(t1 - t2)
        if t1 >= fast_floor and (t1 - t2) >= fast_margin:
            conn.execute("UPDATE concerns SET domain_id=?, assign_source='fast', assign_conf=? "
                         "WHERE id=?", (top[0], round(float(S[i, srt[0]]), 4), cid))
            n_fast += 1
        else:
            ambiguous.append((cid, _ctx(info[cid]), top))
    conn.commit()
    log(f"  fast path: {n_fast}/{len(todo)} assigned by embedding+stems "
        f"(median top1-top2 margin {np.median(margins):.3f}); {len(ambiguous)} onward")

    # --- stage 1b: BATCH NOVELTY — novelty is an aggregate signal no per-concern call can
    # see. Cluster the still-ambiguous facets; a coherent cluster whose stems no existing
    # feature owns becomes ONE proposed feature (instead of N forced fits keeping the
    # NONE-rate deceptively low).
    if not frozen and len(ambiguous) >= int(cat.get("novelty_min", 4)):
        # novelty exists to catch REAL features the register missed (removed ones like
        # CCC). Orphans made of inherited/vendor files are not features — vanilla
        # maintenance routes to the baseline, and boost headers must never mint
        # 'Date and Time Handling System' again.
        from .delta import file_authorship
        allf = sorted({f for cid, _, _ in ambiguous for f in info[cid]["files"]})
        aklass = file_authorship(conn, cfg.get("repo", {}).get("path", "."), allf,
                                 log=lambda *a: None)
        base_row = conn.execute("SELECT id FROM domains WHERE classification='inherited' "
                                "AND status IN ('named','provisional') LIMIT 1").fetchone()
        mintable, routed = [], 0
        for cid, ctx, top in ambiguous:
            fs = info[cid]["files"]
            auth = sum(1 for f in fs if aklass.get(f) == "authored")
            if fs and auth * 2 <= len(fs):
                if base_row:
                    conn.execute("UPDATE concerns SET domain_id=?, assign_source='baseline' "
                                 "WHERE id=?", (base_row["id"], cid))
                    routed += 1
                continue
            mintable.append((cid, ctx, top))
        conn.commit()
        if routed:
            log(f"  novelty gate: {routed} inherited/vendor orphans -> baseline; "
                f"{len(mintable)} authored orphans may mint")
        ambiguous = mintable
        created, ambiguous = _batch_novelty(conn, provider, cfg, ambiguous, vec, info,
                                            feat_stems, names, run_id, log)
        if created:
            # refresh taxonomy state so the per-concern stage sees the new features
            rows = taxonomy_rows(conn)
            fids, F = embed_definitions(conn, provider, rows)
            feat_line = {r["id"]: f"{r['id']}: {r['name']} — {(r['definition'] or '')[:200]}"
                         for r in rows}
            names = {r["id"]: r["name"] for r in rows}
            feat_stems = {r["id"]: set(json.loads(r["stems"] or "[]")) for r in rows}
            fpos_all = {fid: i for i, fid in enumerate(fids)}
            barr = np.array([births.get(fid, "") for fid in fids])
            for pos, (cid, ctx, _) in enumerate(list(ambiguous)):
                cstems = concern_stems(info[cid])
                score = (vec[cid] @ F.T).copy()
                for fid, fst in feat_stems.items():
                    if fst and (fst & cstems):
                        score[fpos_all[fid]] += stem_boost
                cd = cdates.get(info[cid]["commit"], "")
                if cd:
                    score[barr > cd] = -9.0   # same birth mask as stage 1 — this rebuild
                                              # silently replaced masked shortlists once
                ambiguous[pos] = (cid, ctx, [fids[int(j)] for j in np.argsort(-score)[:k]
                                             if score[int(j)] > -8.0])
                # shortlists feeds the invalid-pick retry: keep it masked too
                shortlists[cid] = ambiguous[pos][2]

    # --- stage 2: batched LLM pick-by-ID (parallel) ---
    batches = [ambiguous[i:i + batch_n] for i in range(0, len(ambiguous), batch_n)]
    picked: dict[int, int | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_llm_pick, provider, b, feat_line,
                          f"tax-cls:{b[0][0]}:{len(fids)}"): b for b in batches}
        done = 0
        for fut in as_completed(futs):
            picked.update(fut.result())
            done += 1
            if done % 20 == 0 or done == len(batches):
                log(f"    {done}/{len(batches)} batches")
    # invalid picks: one solo retry, then treated as none-fits
    retry = [(cid, _ctx(info[cid]), shortlists[cid]) for cid, p in picked.items() if p is None]
    for cid, ctx, sl in retry:
        p = _llm_pick(provider, [(cid, ctx, sl)], feat_line, f"tax-cls-retry:{cid}").get(cid)
        picked[cid] = p if p is not None else 0
    n_llm = 0
    nones: list[int] = []
    for cid, p in picked.items():
        if p:
            conf = float(vec[cid] @ F[fids.index(p)])
            conn.execute("UPDATE concerns SET domain_id=?, assign_source='llm', assign_conf=? "
                         "WHERE id=?", (p, round(conf, 4), cid))
            n_llm += 1
        else:
            nones.append(cid)
    conn.commit()
    log(f"  LLM: {n_llm} assigned, {len(retry)} retried, {len(nones)} fit no feature")

    # --- stage 3: audit — re-check per-feature embedding outliers. Runs BEFORE propose so
    # a forced fit (LLM shoehorned a novel change into a weakly-related feature; validated
    # signature: within-feature outlier + low home cosine + the auditor answers 0) is FREED
    # and joins the NONE queue, where propose can give it an honest new home.
    n_audit, freed = _audit(conn, provider, vec, info, cat, workers, log, births=births, cdates=cdates)
    nones += freed

    # --- stage 4: NONEs stay honestly unattributed (dead-feature candidates live in the
    # batch-novelty pool; singleton minting is retired) ---
    proposed: list[dict] = []
    unassigned = conn.execute(
        "SELECT COUNT(*) FROM concerns WHERE domain_id IS NULL AND label IS NOT NULL AND (origin IS NULL OR origin != 'import-misc')").fetchone()[0]

    conn.execute(
        "UPDATE discovery_runs SET params=?, n_domains=? WHERE id=?",
        (json.dumps({"k": k, "fast": n_fast, "llm": n_llm, "proposed": len(proposed),
                     "frozen": frozen}), len(fids), run_id))
    _auto_confirm(conn, run_id, cat, log)
    _post(conn, provider, log)
    return {"classified": n_fast + n_llm, "fast": n_fast, "llm": n_llm,
            "proposed": [p["name"] for p in proposed], "unassigned": unassigned,
            "audited": n_audit, "features": len(fids), "run_id": run_id}


def _batch_novelty(conn, provider, cfg, ambiguous, vec, info, feat_stems, names,
                   run_id, log) -> tuple[list[dict], list[tuple]]:
    """Cluster ambiguous facets; coherent clusters whose stems no existing feature owns
    become proposed features EN MASSE. Returns (created, remaining_ambiguous)."""
    from ..catalog.catalog import cluster_concerns
    cat = cfg.get("catalog", {})
    novelty_min = int(cat.get("novelty_min", 4))
    cids = [cid for cid, _, _ in ambiguous]
    Mn = np.vstack([vec[c] for c in cids])
    cfiles = {c: json.dumps(info[c]["files"]) for c in cids}
    membership, _, _ = cluster_concerns(cids, Mn, cfiles, cat, log=lambda *_: None,
                                        min_size=novelty_min)
    groups: dict[int, list[int]] = defaultdict(list)
    for node, comm in enumerate(membership):
        groups[comm].append(cids[node])
    census = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=0")}
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    tomb = {r["name"].lower() for r in conn.execute("SELECT name FROM taxonomy_tombstones")}
    owned = set().union(*feat_stems.values()) if feat_stems else set()
    nameset = {n.lower() for n in names.values()}
    created: list[dict] = []
    absorbed: set[int] = set()
    # existing feature-definition embeddings: a novelty cluster must be semantically NEW,
    # not just carry one unclaimed stem variant of a feature that already exists
    defrows = conn.execute("SELECT vector FROM embeddings WHERE target_type='taxdef'").fetchall()
    Fdef = (np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in defrows])
            if defrows else None)
    if Fdef is not None:
        Fdef = Fdef / (np.linalg.norm(Fdef, axis=1, keepdims=True) + 1e-9)
    for comm, members in groups.items():
        if len(members) < novelty_min:
            continue
        stems = [s for s in cluster_stems([info[c] for c in members], census) if s not in god]
        fresh = [s for s in stems if s not in owned]
        if not stems or len(fresh) * 2 < len(stems):   # majority of vocabulary already owned
            continue
        if Fdef is not None:
            cent = np.mean([vec[c] for c in members], axis=0)
            cent /= (np.linalg.norm(cent) + 1e-9)
            if float((Fdef @ cent).max()) >= 0.80:     # semantically an existing feature
                continue
        user = ("CHANGES:\n" + "\n".join(f"- {_ctx(info[c])}" for c in members[:14])
                + f"\n\nSHARED STEMS: {', '.join(stems)}"
                + ("\n\nREJECTED names (NEVER use): " + ", ".join(sorted(tomb)) if tomb else ""))
        try:
            out = provider.chat(NOVELTY_SYS, user, want_json=True, large=True,
                                cache_extra=f"tax-novelty:{run_id}:{comm}")
        except Exception:  # noqa: BLE001
            continue
        name = str((out.get("name") if isinstance(out, dict) else "") or "").strip()[:60]
        if not name or name.lower() in tomb:
            continue
        if name.lower() in nameset:         # resolved to an existing feature after all
            continue
        did = conn.execute(
            "INSERT INTO domains (discovery_run_id, name, slug, definition, stems, named_from, "
            "classification, status, created_by) VALUES (?,?,?,?,?,?, 'feature','provisional','auto')",
            (run_id, name, _slug(name), str(out.get("definition") or "").strip()[:500],
             json.dumps(stems[:12]), len(members))).lastrowid
        conn.executemany("UPDATE concerns SET domain_id=?, assign_source='novelty' WHERE id=?",
                         [(did, c) for c in members])
        absorbed.update(members)
        nameset.add(name.lower())
        created.append({"name": name, "id": did, "n": len(members)})
    conn.commit()
    if created:
        log("  batch-novelty: " + ", ".join(f"{c['name']} ({c['n']})" for c in created))
    return created, [a for a in ambiguous if a[0] not in absorbed]


def _audit(conn, provider, vec, info, cat, workers, log,
           births=None, cdates=None) -> tuple[int, list[int]]:
    """Targeted repair: within each feature, members whose facet is a cosine outlier vs the
    member centroid get re-checked (no fast path). Moves only into SETTLED features and only
    on an embedding improvement; when the auditor answers 0 for a weakly-anchored outlier the
    concern is FREED (returned) so propose-new can give it an honest home. Cap keeps it O(small)."""
    z_cut = float(cat.get("audit_z", -2.0))
    members = defaultdict(list)
    for r in conn.execute("SELECT id, domain_id FROM concerns WHERE domain_id IS NOT NULL "
                          "AND assign_source IN ('fast','llm','propose')"):
        if r["id"] in vec:
            members[r["domain_id"]].append(r["id"])
    suspects = []
    for did, cids in members.items():
        if len(cids) < 6:
            continue
        M = np.vstack([vec[c] for c in cids])
        cent = M.mean(0)
        cent /= (np.linalg.norm(cent) + 1e-9)
        d = M @ cent
        mu, sd = float(d.mean()), float(d.std())
        if sd < 1e-6:
            continue
        for c, dv in zip(cids, d):
            if (dv - mu) / sd < z_cut:
                suspects.append((c, did))
    if not suspects:
        log("  audit: no outliers")
        return 0, []
    rows = taxonomy_rows(conn)
    feat_line = {r["id"]: f"{r['id']}: {r['name']} — {(r['definition'] or '')[:200]}" for r in rows}
    fids, F = embed_definitions(conn, provider, rows)
    fpos = {fid: i for i, fid in enumerate(fids)}
    # Repair only against the SETTLED taxonomy: an outlier must never be pulled into a
    # freshly-minted provisional feature (validated failure mode: tiny provisional features
    # attract unrelated outliers).
    settled = {r["id"] for r in rows if r["status"] in ("named", "confirmed")}
    move_margin = float(cat.get("audit_move_margin", 0.03))
    jobs = []
    births, cdates = births or {}, cdates or {}
    for c, did in suspects:
        sims = vec[c] @ F.T
        cd = cdates.get(info[c]["commit"], "")
        cand = [fids[int(j)] for j in np.argsort(-sims)
                if fids[int(j)] in settled
                and not (cd and births.get(fids[int(j)], "") > cd)][:8]
        if did not in cand:
            cand = cand[:7] + [did]                 # current home always an option
        jobs.append((c, _ctx(info[c]), cand, did))
    batches = [jobs[i:i + 12] for i in range(0, len(jobs), 12)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_llm_pick, provider, [(c, ctx, sl) for c, ctx, sl, _ in b],
                          feat_line, f"tax-audit:{b[0][0]}") for b in batches]
        results = {}
        for fut in as_completed(futs):
            results.update(fut.result())
    unassign_floor = float(cat.get("audit_unassign_floor", 0.55))
    moved = 0
    freed: list[int] = []
    for c, _, _, did in jobs:
        p = results.get(c)
        if did not in fpos:
            continue
        cos_old = float(vec[c] @ F[fpos[did]])
        if p == 0 and cos_old < unassign_floor:
            # the auditor says nothing fits AND the current home is weak — free it for propose
            conn.execute("UPDATE concerns SET domain_id=NULL, assign_source=NULL, "
                         "assign_conf=NULL WHERE id=?", (c,))
            freed.append(c)
            continue
        if not p or p == did or p not in fpos:
            continue
        # a move must be an embedding IMPROVEMENT too — LLM pick alone may be a worse home
        cos_new = float(vec[c] @ F[fpos[p]])
        if cos_new >= cos_old + move_margin:
            conn.execute("UPDATE concerns SET domain_id=?, assign_source='audit', "
                         "assign_conf=? WHERE id=?", (p, round(cos_new, 4), c))
            moved += 1
    conn.commit()
    log(f"  audit: {len(suspects)} outliers re-checked, {moved} moved, {len(freed)} freed")
    return moved, freed


def _auto_confirm(conn, run_id: int, cat: dict, log=print) -> None:
    """Provisional features created at least N classify runs ago that kept enough concerns
    get promoted — unattended installs converge without a human ever reviewing."""
    min_runs = int(cat.get("autoconfirm_runs", 2))
    min_concerns = int(cat.get("autoconfirm_concerns", 5))
    prior = [r["id"] for r in conn.execute(
        "SELECT id FROM discovery_runs WHERE algorithm='taxonomy-classify' AND id < ? "
        "ORDER BY id DESC LIMIT ?", (run_id, min_runs - 1))]
    if len(prior) < min_runs - 1:
        return
    cutoff = min(prior)   # provisional features from runs at/before this one have "survived"
    promoted = conn.execute(
        "UPDATE domains SET status='named' WHERE status='provisional' "
        "AND discovery_run_id <= ? AND id IN ("
        "  SELECT domain_id FROM concerns WHERE domain_id IS NOT NULL "
        "  GROUP BY domain_id HAVING COUNT(*) >= ?)",
        (cutoff, min_concerns)).rowcount
    if promoted:
        log(f"  auto-confirmed {promoted} provisional features "
            f"(survived {min_runs} runs with >= {min_concerns} concerns)")


def _post(conn, provider, log) -> None:
    """Derived state after (re)assignment: per-domain files + areas."""
    from ..catalog.catalog import _derive_files
    _derive_files(conn)
    conn.commit()


def rollups(conn) -> None:
    """Domain + area commit rollups (run AFTER attribute has populated commit_domains)."""
    conn.execute(
        "UPDATE domains SET "
        "n_commits=(SELECT COUNT(DISTINCT commit_hash) FROM commit_domains WHERE domain_id=domains.id), "
        "first_seen=(SELECT MIN(c.authored_at) FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=domains.id), "
        "last_seen=(SELECT MAX(c.authored_at) FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=domains.id)")
    conn.commit()


def build_taxonomy(conn, provider, cfg: dict, git_head: str | None, rev_range: str,
                   log=print) -> dict:
    """Catalog entry point (v0.1): the register (from the worktree) is the label space;
    history is classified against it. Census refreshes locally each run (god stems)."""
    from .ground import build_census
    from .register import build_register
    cat = cfg.get("catalog", {})
    build_census(conn, top_n=int(cat.get("census_top", 800)))
    r1 = build_register(conn, provider, cfg.get("repo", {}).get("path", "."), cfg, log,
                        force=bool(cat.get("reregister")))
    r2 = classify(conn, provider, cfg, git_head, rev_range,
                  frozen=bool(cat.get("frozen")), force=bool(cat.get("reclassify")), log=log)
    # history refines the identity card (text-only) once attribution exists
    from .refine import promote_evidenced_docs, refine_descriptions
    promote_evidenced_docs(conn, log=log)
    refine_descriptions(conn, provider, log=log)
    n_feat = conn.execute("SELECT COUNT(*) FROM domains "
                          "WHERE status IN ('named','provisional','confirmed')").fetchone()[0]
    log(f"  {n_feat} features")
    return {"domains": n_feat, **{k: v for k, v in r2.items() if k != "features"},
            "register": r1.get("register", 0)}
