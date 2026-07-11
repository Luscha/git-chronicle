"""REGISTER — the feature catalog, derived from the CURRENT worktree.

The inversion at the heart of v0.1: feature identity comes from what the code IS today
(the direction every working tool in this space uses), and history is attribution +
narrative against it. No induction from change clusters, no novelty guessing.

Pipeline: scoped worktree files -> module units (global stem families, the 88%-graded
machinery) -> code-peek label per unit (representative = stem-carrying, header-preferred,
include-ranked; read at HEAD via BatchReader) -> tier-4 doc entities where the repo
documents itself -> deterministic territory merge + one chunked LLM dedupe -> the
register, written as `domains` rows (status 'named') with stems + seeded territories.

Acknowledged scope subtrees (owned products that must not be decomposed, e.g. a bundled
backoffice app) become ONE register entry each, defined from a single peek of their most
declarative root file.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..extract.git_ingest import BatchReader, run_git
from ..scope import Scope
from ..storage import now_iso
from .ground import _norm_stems, harvest_docs, path_stems
from .induce import _slug

REGISTER_PEEK_SYS = (
    "You identify the FEATURES and SUBSYSTEMS of ONE software project from its current "
    "code. For each numbered MODULE (a family of files that live together; you get the "
    "head of its most declarative file) name the feature/subsystem/library it constitutes "
    "and give a one-sentence concrete definition derived from the CODE (declarations, "
    "tokens, includes) — never from file names alone. Name at feature altitude: what a "
    "maintainer calls the capability, not the file. If the code is inconclusive, use the "
    'name "inconclusive". '
    'Respond with ONE JSON object: {"labels":{"<n>":{"name":"...","definition":"..."}}}'
)

_CODE_HEAD = 1100
_UNIT_MIN = 2          # smallest stem family that forms a unit
_UNIT_MAX = 80         # bigger families get split by directory within the family
_PEEK_BATCH = 5


def _worktree_units(repo: str, scope) -> tuple[dict[str, list[str]], list[str]]:
    """Scoped worktree -> module units by global stem family (dir-split when huge)."""
    from ..untangle.untangle import _stem_families
    files = [p for p in run_git(repo, ["ls-files"]).splitlines()
             if p.strip() and scope(p)]
    # code files only: dominant extensions of the scoped tree (data/assets excluded)
    extc = Counter(p.rsplit(".", 1)[-1].lower() for p in files if "." in p)
    code_exts = {e for e, n in extc.most_common(14)
                 if e not in ("png", "jpg", "dds", "tga", "wav", "mp3", "bin", "dat",
                              "gif", "bmp", "ico", "ttf", "sub", "gr2", "mse", "msa")}
    files = [p for p in files if "." in p and p.rsplit(".", 1)[-1].lower() in code_exts]
    fams, leftover = _stem_families(files)
    units: dict[str, list[str]] = {}
    for s, fs in fams.items():
        if len(fs) <= _UNIT_MAX:
            units[s] = fs
            continue
        bydir: dict[str, list[str]] = defaultdict(list)
        for f in fs:
            bydir[f.rsplit("/", 1)[0]].append(f)
        for d, dfs in bydir.items():
            units[f"{s} ({d.rsplit('/', 1)[-1]})"] = dfs
    return units, leftover


def build_register(conn, provider, repo: str, cfg: dict, log=print,
                   force: bool = False) -> dict:
    md = cfg.get("scope", {}).get("file", "gitchronicle.md")
    scope = Scope.load(md)
    have = conn.execute("SELECT COUNT(*) FROM domains "
                        "WHERE status IN ('named','confirmed')").fetchone()[0]
    if have and not force:
        log(f"  register exists ({have} features) — use --force to rebuild")
        return {"register": have, "skipped": True}

    units, leftover = _worktree_units(repo, scope)
    log(f"  worktree: {len(units)} module units ({len(leftover)} un-familied files)")

    # peek-label every unit at HEAD
    from ..untangle.untangle import _representative
    cblock = ""
    reader = BatchReader(repo)
    entries: list[dict] = []
    try:
        items = sorted(units.items())
        for i in range(0, len(items), _PEEK_BATCH):
            part = items[i:i + _PEEK_BATCH]
            blocks = []
            for j, (s, fs) in enumerate(part):
                rep = _representative(repo, "HEAD", s.split(" (")[0], fs, reader=reader)
                head = reader.read("HEAD", rep, limit=_CODE_HEAD)
                blocks.append(f"[{j}] module '{s}' ({rep.rsplit('/', 1)[-1]}, "
                              f"{len(fs)} files):\n{head}")
            try:
                got = provider.chat(REGISTER_PEEK_SYS, cblock + "\n\n".join(blocks),
                                    want_json=True, cache_extra=f"reg:{i}")
            except Exception:  # noqa: BLE001
                got = {}
            labels = got.get("labels", {}) if isinstance(got, dict) else {}
            for j, (s, fs) in enumerate(part):
                r = labels.get(str(j)) or {}
                name = str((r.get("name") if isinstance(r, dict) else "") or "").strip()[:70]
                if name and name.lower() != "inconclusive":
                    stems = set()
                    for f in fs[:10]:
                        stems |= path_stems(f)
                    entries.append({"name": name, "tier": 3,
                                    "definition": str(r.get("definition") or "").strip()[:400],
                                    "files": fs, "stems": stems})
            if i and i % 200 == 0:
                log(f"    {i}/{len(items)} units peeked")
    finally:
        reader.close()
    log(f"  {len(entries)} units labelled")

    # docs (scoped) are the highest evidence tier — the repo describing itself
    for d in harvest_docs(repo):
        if not scope(d["path"]):
            continue
        title = d["title"].strip()
        if not (6 <= len(title) <= 60) or title.lower().endswith((".md", ".txt")):
            continue
        entries.append({"name": title[:70], "tier": 4,
                        "definition": d["excerpt"].replace("\n", " ")[:400],
                        "files": [d["path"]],
                        "stems": path_stems(d["path"])})

    # acknowledged subtrees: one entry each, never decomposed
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    for glob_ in getattr(scope, "acknowledges", []):
        root = glob_.rstrip("/*")
        entries.append({"name": root.rsplit("/", 1)[-1], "tier": 4,
                        "definition": f"acknowledged sub-product under {root}/ "
                                      "(catalogued, not decomposed)",
                        "files": [root], "stems": path_stems(root + "/x"), "ack": root})

    # deterministic territory merge (tier wins on conflict), then chunked LLM dedupe
    merged: list[dict] = []
    for e in sorted(entries, key=lambda d: -d["tier"]):
        nest = _norm_stems(e["stems"] - god)
        home = None
        for m in merged:
            small = min(len(nest), len(m["_norm"])) or 1
            ov = nest & m["_norm"]
            if (len(ov) >= 2 or any(" " in s for s in ov)) and len(ov) * 2 >= small:
                home = m
                break
        if home is None:
            merged.append({**e, "_norm": nest})
        else:
            home["files"] = list(dict.fromkeys(home["files"] + e["files"]))
            home["_norm"] |= nest
            home["stems"] |= e["stems"]
            if e["tier"] > home["tier"]:
                home.update(name=e["name"], definition=e["definition"], tier=e["tier"])
    log(f"  {len(merged)} entries after territory merge")

    merged.sort(key=lambda m: (sorted(m["stems"] - god)[:1] or ["~"])[0])
    final: list[dict] = []
    for i in range(0, len(merged), 120):
        part = merged[i:i + 120]
        listing = "\n".join(f"{j}: {m['name']} — {m['definition'][:90]}"
                            for j, m in enumerate(part))
        try:
            out = provider.chat(
                "Deduplicate this feature register of ONE software project: merge entries "
                "that are the same feature under different names (keep the name a "
                "maintainer would use and the most concrete definition). DO NOT drop "
                "entries; every input appears exactly once. "
                'Respond JSON: {"entities":[{"i":[<merged input numbers>],"name":"...",'
                '"definition":"..."}]}',
                cblock + listing, want_json=True, large=True, cache_extra=f"reg-dedupe:{i}")
            ents = [e for e in (out.get("entities") or []) if isinstance(e, dict)
                    and e.get("i")] if isinstance(out, dict) else []
        except Exception:  # noqa: BLE001
            ents = []
        if ents and sum(len(e["i"]) for e in ents) >= len(part) * 0.8:
            for e in ents:
                members = [part[int(x)] for x in e["i"] if 0 <= int(x) < len(part)]
                if not members:
                    continue
                base = max(members, key=lambda m: m["tier"])
                final.append({**base,
                              "name": str(e.get("name") or base["name"]).strip()[:70],
                              "definition": str(e.get("definition")
                                                or base["definition"]).strip()[:400],
                              "files": list(dict.fromkeys(
                                  f for m in members for f in m["files"])),
                              "stems": set().union(*(m["stems"] for m in members))})
        else:
            final.extend(part)

    # write the register
    doomed = "status IN ('candidate','named','provisional') AND locked=0"
    conn.execute(f"UPDATE concerns SET domain_id=NULL, assign_source=NULL WHERE domain_id IN "
                 f"(SELECT id FROM domains WHERE {doomed})")
    conn.execute(f"DELETE FROM domains WHERE {doomed}")
    run_id = conn.execute(
        "INSERT INTO discovery_runs (algorithm, params, created_at) "
        "VALUES ('register-worktree','{}',?)", (now_iso(),)).lastrowid
    seen = set()
    n = 0
    for e in final:
        key = e["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        did = conn.execute(
            "INSERT INTO domains (discovery_run_id, name, slug, definition, stems, "
            "named_from, classification, status, created_by) "
            "VALUES (?,?,?,?,?,?, 'feature','named','auto')",
            (run_id, e["name"], _slug(e["name"]), e["definition"],
             json.dumps(sorted(e["stems"] - god)[:12]), len(e["files"]))).lastrowid
        conn.executemany("INSERT OR REPLACE INTO domain_files (domain_id, path, weight) "
                         "VALUES (?,?,1.0)", [(did, f) for f in e["files"][:400]])
        n += 1
    conn.commit()
    log(f"  register: {n} features written")
    return {"register": n, "units": len(units)}
