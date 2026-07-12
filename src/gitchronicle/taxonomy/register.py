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
from .facets import _slug

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
    # cohesive small directories are modules in their own right (a python package like
    # luna/ is one framework even when its files family by inner basenames): add an
    # umbrella unit per compact directory subtree
    bydir: dict[str, list[str]] = defaultdict(list)
    pkgroots: set = set()
    for f in files:
        parts = f.split("/")
        if len(parts) >= 3:
            bydir["/".join(parts[:-1])].append(f)
        if parts[-1] == "__init__.py" and len(parts) >= 3:
            pkgroots.add("/".join(parts[:-1]))
    # a package unit owns its DIRECT children only; subpackages (own __init__) self-
    # register. Claiming the whole subtree lets generated member trees (protobuf stubs
    # et al) outnumber the entry module and redefine the package's identity by stems.
    for root in sorted(pkgroots):
        dfs = [f for f in files
               if f.startswith(root + "/") and "/" not in f[len(root) + 1:]]
        if 1 <= len(dfs) <= 60:
            units[f"pkg:{root.rsplit('/', 1)[-1]}"] = dfs
    for d, dfs in bydir.items():
        base = d.rsplit("/", 1)[-1]
        if 3 <= len(dfs) <= 40 and base.lower() not in ("src", "include", "lib"):
            units[f"dir:{base}"] = dfs
    # significant singletons: a lone file with a specific stem is still a module
    from .ground import path_stems as _ps
    kept_leftover = []
    for f in leftover:
        st = sorted(_ps(f), key=lambda x: (-len(x), x))
        if st and len(f.rsplit("/", 1)[-1]) >= 8:
            units[f"solo:{st[0]}"] = [f]
        else:
            kept_leftover.append(f)
    leftover = kept_leftover
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


_VENDOR_MARK_RE = re.compile(
    r"(?i)copyright|licen[cs]e|SPDX|auto-?generated|generated by|do not edit|"
    r"all rights reserved")

_DOC_IDENT_RE = re.compile(
    r"`([A-Za-z_][\w./:]{3,60})`"                       # code spans
    r"|\b([a-z]+(?:_[a-z0-9]+)+)\b"                     # snake_case
    r"|\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")        # CamelCase


def _doc_idents(text: str, code_stems: set) -> set:
    """Identifiers a doc's CONTENT uses that exist in the code — the corroboration
    bridge that lets 'Player Virtualization' (doc name) meet uiAvatarBuilder (code)."""
    cands: set[str] = set()
    for m in _DOC_IDENT_RE.finditer(text or ""):
        if m.group(1):
            for part in re.split(r"[./:]", m.group(1)):
                if len(part) >= 4:
                    cands.add(part.lower())
                    cands.add(part.lower().replace("_", " "))
        else:
            tok = m.group(2) or m.group(3)
            cands.add(tok.lower().replace("_", ""))
            cands.add(re.sub(r"[_]+", " ", tok.lower()).strip())
            words = re.findall(r"[A-Z][a-z0-9]+", tok)
            if len(words) >= 2:
                cands.add(" ".join(w.lower() for w in words))
    return {c for c in cands if len(c) >= 4 and c in code_stems}


def _alias_pass(provider, final: list[dict], keep_key, log) -> list[dict]:
    import numpy as np
    cand = [(i, m) for i, m in enumerate(final) if not m.get("ack")]
    if len(cand) < 2:
        return final
    texts = [f"{m['name']}. {(m['definition'] or '')[:200]}" for _, m in cand]
    V = provider.embed(texts)
    V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    S = V @ V.T
    np.fill_diagonal(S, 0)
    pairs = []
    for a in range(len(cand)):
        b = int(np.argmax(S[a]))
        if a < b and S[a, b] >= 0.85:
            pairs.append((float(S[a, b]), a, b))
    pairs = sorted(pairs, reverse=True)[:120]
    if not pairs:
        log("  alias pass: no candidate pairs")
        return final
    same: list[tuple[int, int]] = []
    for i in range(0, len(pairs), 20):
        chunk = pairs[i:i + 20]
        listing = "\n".join(
            f"[{j}] {cand[a][1]['name']} — {(cand[a][1]['definition'] or '')[:110]}\n"
            f"    VS {cand[b][1]['name']} — {(cand[b][1]['definition'] or '')[:110]}"
            for j, (_, a, b) in enumerate(chunk))
        try:
            out = provider.chat(
                "Each numbered item shows TWO catalog entries from ONE software project. "
                "Answer which numbers describe THE SAME feature (one capability under two "
                "names — e.g. a design-doc name vs the code's name). Different features "
                "that merely interact or share a subsystem are NOT the same. "
                'Respond JSON: {"same":[<numbers>]}',
                listing, want_json=True, cache_extra=f"reg-alias:{i}")
            for j in (out.get("same") or []) if isinstance(out, dict) else []:
                j = int(j)
                if 0 <= j < len(chunk):
                    same.append((chunk[j][1], chunk[j][2]))
        except Exception:  # noqa: BLE001
            continue
    if not same:
        log(f"  alias pass: {len(pairs)} candidates, none confirmed")
        return final
    root = list(range(len(cand)))

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for a, b in same:
        root[find(a)] = find(b)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(cand)):
        groups[find(i)].append(i)
    merged_out, absorbed = [], set()
    for g in groups.values():
        if len(g) < 2:
            continue
        members = [cand[i][1] for i in g]
        base = max(members, key=lambda m: (m["tier"], len(m["files"])))
        keys = set().union(*(m.get("_keys") or set() for m in members))
        base = {**base,
                "name": keep_key(base["name"], keys),
                "files": list(dict.fromkeys(f for m in members for f in m["files"])),
                "stems": set().union(*(m["stems"] for m in members)),
                "doc_only": all(m.get("doc_only") for m in members)}
        merged_out.append(base)
        absorbed.update(id(m) for m in members)
    kept = [m for m in final if id(m) not in absorbed]
    log(f"  alias pass: {len(pairs)} candidates, {len(same)} confirmed, "
        f"{len(absorbed) - len(merged_out)} entries absorbed")
    return kept + merged_out


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
                key = s.split(":", 1)[-1].split(" (")[0]
                rep = None
                if s.startswith("pkg:"):
                    # a package's identity lives in its root entry module, not in whichever
                    # (often generated) member the include ranking likes best
                    ents = [f for f in fs
                            if f.rsplit("/", 1)[-1].rsplit(".", 1)[0] in ("__init__", "index")]
                    if ents:
                        rep = min(ents, key=lambda f: f.count("/"))
                        if len((reader.read("HEAD", rep, limit=200) or "").strip()) < 60:
                            rep = None      # empty entry file says nothing
                if rep is None:
                    rep = _representative(repo, "HEAD", key, fs, reader=reader)
                depth = _CODE_HEAD * 3 if s.startswith(("solo:", "pkg:")) else _CODE_HEAD
                head = reader.read("HEAD", rep, limit=depth)
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
                stems = set()
                for f in fs[:10]:
                    stems |= path_stems(f)
                if name and name.lower() != "inconclusive":
                    # a coined unit identifier (uchtml, luna) is the owner's own name for
                    # the thing — it must survive generic relabeling, in stems and name
                    key = s.split(":", 1)[-1].split(" (")[0]
                    stems.add(key)
                    if len(key) >= 5 and " " not in key and key.lower() not in name.lower():
                        name = f"{name} ({key})"[:70]
                    entries.append({"name": name, "tier": 3, "key": key,
                                    "definition": str(r.get("definition") or "").strip()[:400],
                                    "files": fs, "stems": stems, "_head": (head or "")[:400]})
                else:
                    # files must never vanish: a tier-1 stub named from the stem keeps the
                    # territory covered; review or later evidence can upgrade it
                    stub = s.split(":", 1)[-1].split(" (")[0]
                    entries.append({"name": stub[:70], "tier": 1, "key": stub,
                                    "definition": "", "files": fs, "stems": stems})
            if i and i % 200 == 0:
                log(f"    {i}/{len(items)} units peeked")
    finally:
        reader.close()
    log(f"  {len(entries)} units labelled")

    # vendored code: a license/copyright/generated header is evidence the unit is
    # third-party — an LLM confirm on that evidence (never a name wordlist) decides.
    # Vendored entities stay catalogued but are shelved out of the KB's main view.
    marks = []
    for e in entries:
        m = _VENDOR_MARK_RE.search(e.get("_head", ""))
        if m:
            line = next((ln.strip() for ln in e["_head"].splitlines()
                         if m.group(0) in ln), "")[:120]
            marks.append((e, line))
    n_vend = 0
    for i in range(0, len(marks), 15):
        chunk = marks[i:i + 15]
        listing = "\n".join(f"[{j}] {e['name']} — {(e['definition'] or '')[:90]}\n"
                             f"    header: {line}"
                             for j, (e, line) in enumerate(chunk))
        try:
            out = provider.chat(
                "These modules of ONE software project carry license/copyright/generated "
                "headers. Judge from the header line and definition which are THIRD-PARTY "
                "(vendored libraries, bundled SDKs, generated bindings of external tools) "
                "rather than the project's own code. "
                'Respond JSON: {"vendored":[<numbers>]}',
                listing, want_json=True, cache_extra=f"reg-vendor:{i}")
            for j in (out.get("vendored") or []) if isinstance(out, dict) else []:
                j = int(j)
                if 0 <= j < len(chunk):
                    chunk[j][0]["vendored"] = True
                    n_vend += 1
        except Exception:  # noqa: BLE001
            continue
    log(f"  vendor pass: {len(marks)} header-marked units, {n_vend} confirmed third-party")

    # docs (scoped) are the highest evidence tier — but only when the CODE corroborates
    # them. Identifiers harvested from doc content bridge naming gaps (a doc says
    # 'Player Virtualization', the code says uiAvatarBuilder). A doc with zero
    # corroboration is doc-only: outdated or aspirational — catalogued and flagged,
    # but it claims no code territory and never outranks code naming (tier 2).
    code_stems = set().union(*(e["stems"] for e in entries)) if entries else set()
    docdirs: dict[str, list[dict]] = defaultdict(list)
    dreader = BatchReader(repo)
    try:
        for d in harvest_docs(repo, scope=scope):
            d["idents"] = _doc_idents(dreader.read("HEAD", d["path"], limit=8000),
                                      code_stems)
            parts = d["path"].split("/")
            if len(parts) >= 3 and parts[0].lower() in ("doc", "docs"):
                docdirs[parts[1]].append(d)
            title = d["title"].strip()
            if not (4 <= len(title) <= 60) or title.lower().endswith((".md", ".txt")):
                continue
            corro = bool(d["idents"] or (path_stems(d["path"]) & code_stems))
            entries.append({"name": title[:70], "tier": 4 if d["idents"] else (3 if corro else 2),
                            "doc_only": not corro,
                            "definition": d["excerpt"].replace("\n", " ")[:400],
                            "files": [d["path"]],
                            "stems": path_stems(d["path"]) | d["idents"]})
    finally:
        dreader.close()
    # Doc/<name>/ subtrees document one system by that name — the strongest naming signal
    for sub, docs in docdirs.items():
        if len(docs) >= 2:
            idents = set().union(*(x["idents"] for x in docs))
            entries.append({"name": sub[:70], "tier": 4 if idents else 2,
                            "doc_only": not idents,
                            "definition": ("documented system: "
                                           + "; ".join(x["title"] for x in docs[:5]))[:400],
                            "files": [x["path"] for x in docs],
                            "stems": set().union(*(path_stems(x["path"]) for x in docs))
                                     | idents})

    # acknowledged subtrees: one entry each, never decomposed
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    for glob_ in getattr(scope, "acknowledges", []):
        root = glob_.rstrip("/*")
        entries.append({"name": root.rsplit("/", 1)[-1], "tier": 4,
                        "definition": f"acknowledged sub-product under {root}/ "
                                      "(catalogued, not decomposed)",
                        "files": [root], "stems": path_stems(root + "/x"), "ack": root})

    # deterministic territory merge (tier wins on conflict), then chunked LLM dedupe
    def _keep_key(name: str, keys: set) -> str:
        """A coined identifier (uchtml, luna) must survive every rename — it is the
        owner's own word for the feature and the KB's most searchable handle."""
        ks = sorted(k for k in keys if len(k) >= 5 and " " not in k)
        if not ks or any(k.lower() in name.lower() for k in ks):
            return name
        return f"{name} ({ks[0]})"[:70]

    merged: list[dict] = []
    for e in sorted(entries, key=lambda d: -d["tier"]):
        nest = _norm_stems(e["stems"] - god)
        home = None
        for m in merged:
            # match against the SEED's stems, never the accumulated union — otherwise
            # every absorption widens the net and unrelated units chain into one blob
            small = min(len(nest), len(m["_seed"])) or 1
            ov = nest & m["_seed"]
            # different coined identifiers = different features, whatever the overlap
            if e.get("key") and m.get("key") and e["key"] != m["key"]:
                continue
            if (len(ov) >= 2 or any(" " in s for s in ov)) and len(ov) * 2 >= small:
                home = m
                break
        if home is None:
            merged.append({**e, "_norm": nest, "_seed": frozenset(nest),
                           "_keys": {e["key"]} if e.get("key") else set()})
        else:
            home["files"] = list(dict.fromkeys(home["files"] + e["files"]))
            home["_norm"] |= nest
            home["stems"] |= e["stems"]
            if e.get("key"):
                home["_keys"].add(e["key"])
            if e["tier"] > home["tier"]:
                home.update(name=e["name"], definition=e["definition"], tier=e["tier"])
            home["name"] = _keep_key(home["name"], home["_keys"])
    log(f"  {len(merged)} entries after territory merge")

    protected = [m for m in merged if m["tier"] >= 4]
    merged = [m for m in merged if m["tier"] < 4]
    merged.sort(key=lambda m: (sorted(m["stems"] - god)[:1] or ["~"])[0])
    final: list[dict] = list(protected)
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
                mkeys = set().union(*(m.get("_keys") or set() for m in members))
                final.append({**base,
                              "name": _keep_key(
                                  str(e.get("name") or base["name"]).strip()[:70], mkeys),
                              "definition": str(e.get("definition")
                                                or base["definition"]).strip()[:400],
                              "files": list(dict.fromkeys(
                                  f for m in members for f in m["files"])),
                              "stems": set().union(*(m["stems"] for m in members))})
        else:
            final.extend(part)

    # global ALIAS pass — chunked dedupe can only merge within a chunk; same-feature-
    # different-name splits (a doc's 'Player Virtualization' vs the code's avatar
    # entity) need a register-wide sweep: embed name+definition, shortlist high-cosine
    # pairs, one LLM confirm batch decides which are genuinely the same feature.
    try:
        final = _alias_pass(provider, final, _keep_key, log)
    except Exception as exc:  # noqa: BLE001
        log(f"  alias pass skipped ({exc})")

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
            "VALUES (?,?,?,?,?,?,?,'named','auto')",
            (run_id, e["name"], _slug(e["name"]), e["definition"],
             json.dumps(sorted(e["stems"] - god)[:12]), len(e["files"]),
             "vendored" if e.get("vendored")
             else ("doc-only" if e.get("doc_only") else "feature"))).lastrowid
        conn.executemany("INSERT OR REPLACE INTO domain_files (domain_id, path, weight, source) "
                         "VALUES (?,?,1.0,'register')", [(did, f) for f in e["files"][:400]])
        n += 1
    conn.commit()
    log(f"  register: {n} features written")
    return {"register": n, "units": len(units)}
