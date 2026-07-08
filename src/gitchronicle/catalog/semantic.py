"""LLM-semantic reconstruction of functional domains (the validated alternative to Leiden
clustering). Instead of clustering concern-label embeddings, we let the model reconstruct the
FEATURE set the way a maintainer would: walk the concerns keeping a growing feature list and, with
each change in front of it (its untangled label + its files + its commit message), decide "an
existing feature, or a new one?". Three specificity rules keep it honest:

  1. FEATURE beats MECHANISM — a change implemented via a generic mechanism (Effects, Logging,
     Rendering, Packets, UI...) belongs to the FEATURE it serves, not the mechanism (unless the
     change is about the mechanism itself). "lobby slow queue affect" -> Matchmaking, not Effects.
  2. LABEL is authoritative — the concern label is the per-change, untangled description; the commit
     message is per-commit and may describe SIBLING changes, so a topic that appears only in the
     message never overrides the label. Multi-topic commit subjects are dropped outright.
  3. No vague buckets — System/UI/Combat/Gameplay/Core/Misc/Player Management are forbidden; a
     de-vague pass re-assigns anything that slipped into one to its concrete feature.

Domains and areas come out already NAMED (the assignment IS the name), so `discover` is skipped for
this method."""
from __future__ import annotations

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..storage import now_iso
from .catalog import _derive_files

VAGUE = {"system", "ui", "combat", "gameplay", "core", "misc", "general", "player management",
         "networking", "interface", "client", "server", "game", "other", "unassigned",
         "utilities", "utility", "helpers", "helper", "miscellaneous", "common", "commons",
         "features", "functionality", "management", "configuration", "settings"}

_SPECIFICITY = (
    "SPECIFICITY RULE: a specific FEATURE beats a generic MECHANISM. Effects/Affects, Logging, "
    "Audit, Rendering, Networking, Packets, Input, Database, UI are generic mechanisms that many "
    "features merely USE. If a change is a specific feature implemented via a mechanism, assign it "
    "to the FEATURE, not the mechanism: 'lobby slow queue affect'->Matchmaking; 'guild war "
    "logging'->Guild War; 'skill damage packet'->Skills. Assign to the mechanism domain ONLY when "
    "the change is about the MECHANISM ITSELF (the affect framework, the logger, the renderer). "
)

ASSIGN_SYS = (
    "You group code changes into the FUNCTIONAL FEATURES of a software project (e.g. Skills, Skill "
    "Tree, Potions, Death Penalty, Guild War, Auto Hunt, Trade, Login, Ranking, Inventory, Shop, "
    "Scoreboard, Augments, Matchmaking, Quests, Dungeons, Anti-Cheat, Logging, Audit, Database...).\n"
    "- NEVER use vague buckets or grab-bags: System, UI, Combat, Gameplay, Core, Misc, Utilities, "
    "Miscellaneous, Helpers, Management, Player Management. Infrastructure work goes to a SPECIFIC "
    "infra feature (Build & Config, Dev Tools, Documentation, Localization), never 'Utilities'.\n"
    "- Distinct similar features stay SEPARATE: Skill Tree != Skills; Auto Hunt != Auto Pickup.\n"
    "- Fold sub-variants up: 'guild war scoring'->Guild War; 'skill tree persistence'->Skill Tree.\n"
    + _SPECIFICITY +
    "The LABEL is the authoritative description of THIS change; the commit message is per-commit and "
    "may also mention OTHER unrelated changes bundled in the same commit — use it only to confirm the "
    "label, NEVER let a topic that appears only in the message override the label. "
    "Each change shows: label |the files it touched |the commit message. USE the files+message to "
    "understand WHICH feature it serves, but assign by FUNCTIONALITY — a feature spans many files; "
    "never group by folder. Reuse an existing feature EXACT name if it fits, else name a NEW specific "
    'feature. Respond JSON {"assignments":{"0":"Feature"}} keyed by [n].'
)

REASSIGN_SYS = (
    "Assign each change to the SINGLE most specific functional feature. Prefer one from the FEATURE "
    "LIST; else name a NEW specific feature. NEVER answer with a vague bucket — always the concrete "
    "feature. " + _SPECIFICITY + 'Respond JSON {"assignments":{"0":"Feature"}} keyed by [n].'
)

AREA_SYS = (
    "Group these software features into ~8-14 broad, meaningful AREAS (e.g. 'Combat', 'Items & "
    "Economy', 'Anti-Cheat & Security', 'Social & Guild', 'Client & UX', 'Infrastructure & Tools', "
    "'Progression', 'PvP & Arena'). Every feature in exactly one area. "
    'Respond JSON: {"areas":{"Area Name":["feature", ...]}}.'
)


def _multitopic(s: str) -> bool:
    """A bullet-list subject or several conventional-commit prefixes = the commit bundles many
    unrelated changes; its whole subject describes SIBLING concerns, so it poisons assignment."""
    return s.count(" - ") >= 2 or len(
        re.findall(r"\b(feat|fix|refactor|chore|docs|perf|test|add|remove)\b", s.lower())) >= 3


def _ctx_fn(labels, cfiles, subj_of):
    def ctx(c):
        fs = ", ".join(f.split("/")[-1] for f in cfiles.get(c, [])[:6])
        s = subj_of.get(c, "")
        msg = "(multi-topic commit; ignore)" if _multitopic(s) else s[:90]
        return f"{labels[c]}  |files: {fs}  |msg: {msg}"
    return ctx


def reconstruct(conn, provider, cfg: dict, git_head: str | None, rev_range: str, log=print) -> dict:
    """Form named domains + named areas from concerns via LLM reconstruction. Leaves confirmed/locked
    domains untouched; reconstructs everything else from scratch."""
    cat = cfg.get("catalog", {})
    batch_n = int(cat.get("assign_batch", 40))
    workers = int(cfg.get("untangle", {}).get("workers", 4))

    # concerns to reconstruct: those not already committed to a confirmed/locked domain
    protected = set(r["id"] for r in conn.execute(
        "SELECT c.id FROM concerns c JOIN domains d ON d.id=c.domain_id "
        "WHERE d.status='confirmed' OR d.locked=1"))
    rows = [r for r in conn.execute(
        "SELECT id, label, files, commit_hash FROM concerns WHERE label IS NOT NULL ORDER BY id")
        if r["id"] not in protected]
    ids = [r["id"] for r in rows]
    if len(ids) < 2:
        log("  not enough concerns to reconstruct (run untangle first)")
        return {"domains": 0}
    labels = {r["id"]: r["label"] for r in rows}
    cfiles = {r["id"]: json.loads(r["files"] or "[]") for r in rows}
    chash = {r["id"]: r["commit_hash"] for r in rows}
    subj = {r["hash"]: (r["subject"] or "") for r in conn.execute("SELECT hash, subject FROM commits")}
    subj_of = {c: subj.get(chash.get(c), "") for c in ids}
    ctx = _ctx_fn(labels, cfiles, subj_of)

    # --- pass 1: growing-list assignment (sequential — each batch sees the accumulated feature set)
    features: list[str] = []
    featset: dict[str, str] = {}
    dom: dict[int, str] = {}
    for i in range(0, len(ids), batch_n):
        batch = ids[i:i + batch_n]
        fl = "\n".join(f"- {f}" for f in features) or "(none yet)"
        user = f"EXISTING FEATURES:\n{fl}\n\nCHANGES:\n" + "\n".join(
            f"[{j}] {ctx(c)}" for j, c in enumerate(batch))
        out = provider.chat(ASSIGN_SYS, user, want_json=True, cache_extra=f"sem-grow:{i}:{len(features)}")
        a = out.get("assignments", {}) if isinstance(out, dict) else {}
        for j, c in enumerate(batch):
            f = str(a.get(str(j)) or a.get(j) or "Unassigned").strip()
            if f.lower() not in featset:
                featset[f.lower()] = f
                features.append(f)
            dom[c] = featset[f.lower()]
    log(f"  pass 1: {len(set(dom.values()))} features from growing-list assignment")

    # --- pass 2: de-vague — re-assign concerns that slipped into a catch-all bucket
    specific = [f for f in features if f.lower() not in VAGUE]
    vague_ids = [c for c in ids if dom[c].lower() in VAGUE]
    if vague_ids:
        flist = "\n".join(f"- {f}" for f in specific)

        def reassign(i):
            batch = vague_ids[i:i + 30]
            user = f"FEATURE LIST:\n{flist}\n\nCHANGES:\n" + "\n".join(
                f"[{j}] {ctx(c)}" for j, c in enumerate(batch))
            out = provider.chat(REASSIGN_SYS, user, want_json=True, cache_extra=f"sem-devague:{i}")
            a = out.get("assignments", {}) if isinstance(out, dict) else {}
            return {c: str(a.get(str(j)) or a.get(j) or dom[c]).strip() for j, c in enumerate(batch)}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(reassign, i) for i in range(0, len(vague_ids), 30)]):
                for c, ff in fut.result().items():
                    if ff.lower() not in VAGUE:
                        dom[c] = ff
        log(f"  pass 2: de-vagued {len(vague_ids)} concerns -> "
            f"{sum(1 for c in ids if dom[c].lower() in VAGUE)} still vague")

    # --- write named domains ---
    conn.execute("UPDATE concerns SET domain_id=NULL WHERE id IN (%s)" %
                 ",".join("?" * len(ids)), ids)
    conn.execute("DELETE FROM domains WHERE status IN ('candidate','named') AND locked=0")
    conn.execute("DELETE FROM areas WHERE status IN ('candidate','named')")
    conn.commit()
    run_id = conn.execute(
        "INSERT INTO discovery_runs (algorithm, params, git_head, rev_range, n_domains, created_at) "
        "VALUES ('llm-semantic',?,?,?,?,?)",
        (json.dumps({"assign_batch": batch_n}), git_head, rev_range,
         len(set(dom.values())), now_iso())).lastrowid
    feat_dom: dict[str, int] = {}
    for feat in sorted(set(dom.values())):
        feat_dom[feat] = conn.execute(
            "INSERT INTO domains (discovery_run_id, name, slug, classification, status, created_by) "
            "VALUES (?,?,?, 'feature','named','auto')",
            (run_id, feat, re.sub(r"[^a-z0-9]+", "-", feat.lower()).strip("-")[:60] or "feature")).lastrowid
    conn.executemany("UPDATE concerns SET domain_id=? WHERE id=?",
                     [(feat_dom[dom[c]], c) for c in ids])
    conn.commit()

    # --- semantic areas ---
    feats = sorted(set(dom.values()))
    n_areas = 0
    if feats:
        out = provider.chat(AREA_SYS, "Features:\n" + "\n".join(f"- {f}" for f in feats),
                            want_json=True, cache_extra=f"sem-areas:{len(feats)}")
        amap = out.get("areas", {}) if isinstance(out, dict) else {}
        fl2 = {f.lower(): f for f in feats}
        for aname, afeats in amap.items():
            aid = conn.execute(
                "INSERT INTO areas (discovery_run_id, name, slug, status, created_by) "
                "VALUES (?,?,?, 'named','auto')",
                (run_id, str(aname)[:80], re.sub(r"[^a-z0-9]+", "-", str(aname).lower()).strip("-")[:60])).lastrowid
            n_areas += 1
            for af in (afeats or []):
                f = fl2.get(str(af).lower())
                if f:
                    conn.execute("UPDATE domains SET area_id=? WHERE id=?", (aid, feat_dom[f]))
        orphan = [r[0] for r in conn.execute("SELECT id FROM domains WHERE area_id IS NULL")]
        if orphan:
            aid = conn.execute(
                "INSERT INTO areas (discovery_run_id, name, slug, status) VALUES (?, 'Other','other','named')",
                (run_id,)).lastrowid
            n_areas += 1
            conn.executemany("UPDATE domains SET area_id=? WHERE id=?", [(aid, d) for d in orphan])
    conn.commit()

    _derive_files(conn)
    conn.commit()
    log(f"  {len(feat_dom)} domains in {n_areas} areas (llm-semantic)")
    return {"domains": len(feat_dom), "areas": n_areas, "concerns": len(ids), "run_id": run_id}


def rollups(conn) -> None:
    """Compute domain + area commit rollups (run AFTER attribute has populated commit_domains)."""
    conn.execute(
        "UPDATE domains SET "
        "n_commits=(SELECT COUNT(DISTINCT commit_hash) FROM commit_domains WHERE domain_id=domains.id), "
        "first_seen=(SELECT MIN(c.authored_at) FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=domains.id), "
        "last_seen=(SELECT MAX(c.authored_at) FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=domains.id)")
    conn.execute(
        "UPDATE areas SET "
        "n_domains=(SELECT COUNT(*) FROM domains WHERE area_id=areas.id), "
        "n_commits=(SELECT COUNT(DISTINCT cd.commit_hash) FROM commit_domains cd "
        "JOIN domains d ON d.id=cd.domain_id WHERE d.area_id=areas.id)")
    conn.commit()
