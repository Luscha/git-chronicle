"""RELATIONS — built-on/uses edges between register features, from static imports.

The original product idea ("avatar uses luna under the hood — it registers its handlers
via luna") resurrected on evidence that works: feature A's territory files import/include
files owned by feature B's territory => A *uses* B. Deterministic, local, no LLM — the
co-change guessing that produced tautologies is gone for good.

A feature that many others use is a framework hub — exactly the "major framework on top
of which I created many other systems" narrative anchor the chronicle needs.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..extract.git_ingest import BatchReader, run_git
from .imports import extract_import_refs

# embedded-interpreter module registration: the C/C++ side coins the importable name
# (CPython convention; e.g. Py_InitModule("luna", ...) makes `import luna` bind here)
_EMBED_RE = re.compile(
    r'(?:Py_InitModule[34]?|PyImport_AppendInittab|PyModule_Create2?)\s*\(\s*"(\w+)"')

_MAX_FILES_PER_FEATURE = 400   # territory files read per feature (by weight); at 80 a
                               # framework's clients sat below the cut and its edges vanished
_MIN_EDGE_FILES = 2            # distinct importing files needed to assert an edge
_HUB_MIN_FANIN = 5             # used by >=5 other features => framework hub
_SWEEP_FILES = 150             # a commit touching more files than this is a sweep, not
                               # development of any one of them

# A reference can only mean a file its own syntax could be naming. `#include "config.h"`
# in char_affect.cpp was resolving to ccc/frontend/public/config.js, and 85 of this
# repository's 227 import edges were that same collision. Stated as an EXCLUSION rather
# than a whitelist: an unknown extension (.fx, .forge, .inc) resolves as it always did,
# and a Python import may still reach a native extension module.
_WEB = {"js", "mjs", "cjs", "ts", "tsx", "jsx", "vue", "svelte"}
_PY = {"py", "pyw", "pyx"}
_NATIVE = {"c", "cc", "cpp", "cxx", "c++", "h", "hh", "hpp", "hxx", "h++", "inl", "ipp",
           "m", "mm"}
_CANNOT_NAME = {"c": _WEB | _PY | {"lua"}, "py": _WEB | {"lua"},
                "req": _PY, "es": _NATIVE | _PY | {"lua"},
                "rs": _WEB | _PY | _NATIVE | {"lua"}}


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _index(conn, repo: str, max_files: int) -> dict:
    """Who owns which file, and which files a reference could be naming.

    Shared by the edge builder and by the evidence behind one edge, so what the studio
    shows as the reason for a link is the same resolution that created it.
    """
    feats = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM domains WHERE status IN ('named','confirmed','provisional')")}
    if not feats:
        return {"feats": feats}

    # file -> owning feature: register territory (worktree truth) beats history-derived
    # weight; files claimed by many features are ambiguous glue and own nothing
    # Compiled output is never what a dependency points at: os.pyc made every script in
    # the repository look built on the tool that vendored a Python installation. git's own
    # numstat already says which files are binary.
    binary = {r[0] for r in conn.execute(
        "SELECT DISTINCT path FROM commit_files WHERE is_binary = 1")}
    # ... and it must be a file somebody here actually wrote a line of. A vendored language
    # runtime is imported by every script in the repository, which made the tool that
    # bundled it look like the thing the project is built on. Sweeps and deletions do not
    # count: only a normal commit that added lines to that file.
    sizes: Counter = Counter()
    for (h,) in conn.execute("SELECT commit_hash FROM commit_files"):
        sizes[h] += 1
    edited = {r[0] for r in conn.execute(
        "SELECT path, commit_hash FROM commit_files WHERE insertions > 0")
        if sizes[r[1]] <= _SWEEP_FILES}
    claims: dict[str, list] = defaultdict(list)
    inherited: set = set()
    for r in conn.execute("SELECT domain_id, path, weight, source, authored FROM domain_files "
                          "ORDER BY source='register' DESC, weight DESC"):
        if r["domain_id"] in feats:
            claims[r["path"]].append(
                (1 if r["source"] == "register" else 0, r["weight"] or 0, r["domain_id"]))
            if r["authored"] == 0:
                inherited.add(r["path"])
    owner: dict[str, int] = {}
    for path, cs in claims.items():
        reg = [c for c in cs if c[0] == 1]
        if len(reg) == 1:
            owner[path] = reg[0][2]
        elif not reg and len(cs) <= 3:
            owner[path] = max(cs)[2]
    # a target that no longer exists cannot be what today's code includes: a deleted
    # sys.* absorbed 67 `import sys` lines and spread edges across the whole catalogue
    alive = set(run_git(repo, ["ls-files"], check=False).splitlines()) if repo else None
    by_base: dict[str, list[tuple]] = defaultdict(list)
    for path, did in owner.items():
        if alive is not None and path not in alive:
            continue
        # An import target nobody here wrote is not a dependency on anyone's feature. A tool
        # that vendors a language's standard library otherwise looks like the thing every
        # script in the repository is built on — 17 such edges here, and 13 of them made it
        # the second-biggest hub. Provenance decides this, not a list of vendor names.
        if path in inherited or path in binary or path not in edited:
            continue
        # declaration shims (.pyi/.d.ts) re-declare a module someone else implements —
        # never the import target's real owner
        if path.endswith((".pyi", ".d.ts")):
            continue
        parts = path.rsplit("/", 2)
        fname = parts[-1].lower()
        base = fname.rsplit(".", 1)[0]
        # a package's entry MODULE answers to the package name: `import luna` ->
        # luna/__init__.py (code only — Doc/luna/index.md must not shadow it)
        if (base in ("__init__", "index", "mod") and len(parts) >= 2
                and fname.rsplit(".", 1)[-1]
                in ("py", "pyw", "js", "mjs", "cjs", "ts", "jsx", "tsx", "rs", "go")):
            base = parts[-2].lower()
        by_base[base].append((did, path))

    # read each feature's top territory files once
    per_feat_files: dict[int, list[str]] = defaultdict(list)
    for path, did in owner.items():
        if len(per_feat_files[did]) < max_files:
            per_feat_files[did].append(path)
    return {"feats": feats, "owner": owner, "by_base": by_base, "files": per_feat_files}


def _resolve(ref: str, kind: str, by_base: dict) -> list[tuple]:
    """The (entry, file) pairs a reference could be naming, its own syntax respected."""
    return [(d, p) for d, p in by_base.get(ref, ())
            if _ext(p) not in _CANNOT_NAME.get(kind, ())]


def build_relations(conn, repo: str, log=print, ledger=None,
                    max_files: int = _MAX_FILES_PER_FEATURE) -> dict:
    idx = _index(conn, repo, max_files)
    feats = idx["feats"]
    if not feats:
        log("  no register — run `gitchronicle register` first")
        return {"edges": 0}
    by_base, per_feat_files = idx["by_base"], idx["files"]

    reader = BatchReader(repo)
    texts: dict[str, str] = {}
    embed_owner: dict[str, int | None] = {}
    try:
        for did, files in per_feat_files.items():
            for f in files:
                text = reader.read("HEAD", f, limit=60000)
                if not text:
                    continue
                texts[f] = text
                for m in _EMBED_RE.finditer(text):
                    name = m.group(1).lower()
                    # two features registering the same module name = ambiguous, drop it
                    embed_owner[name] = did if embed_owner.get(name, did) == did else None
    finally:
        reader.close()

    edge_files: dict[tuple, set] = defaultdict(set)
    strong_edges: set[tuple] = set()
    for did, files in per_feat_files.items():
        for f in files:
            text = texts.get(f)
            if not text:
                continue
            for ref, kind in extract_import_refs(text, with_kind=True):
                if ref in embed_owner:
                    target = embed_owner[ref]     # None = ambiguous registration
                    strong = True                 # coined module name: one import suffices
                else:
                    # DISTINCT owners: char_traits.hpp and char_traits.cpp are two files
                    # and one entry, and counting files made every C/C++ header/source pair
                    # look ambiguous — 499 import targets in this repository alone
                    owners = {d for d, _ in _resolve(ref, kind, by_base)}
                    target = next(iter(owners)) if len(owners) == 1 else None
                    strong = False
                if target is not None and target != did:
                    edge_files[(did, target)].add(f)
                    if strong:
                        strong_edges.add((did, target))

    # Relations the owner has judged and rejected. `not-uses` used to stop the studio
    # PROPOSING a link and did nothing to one already inferred, so a wrong edge could be
    # dismissed and still stand in the graph — the one verdict the ledger could not carry.
    ids = {name: did for did, name in feats.items()}
    refused = {(ids[a], ids[b]) for a, b in
               (ledger.rejected_relations() if ledger is not None else [])
               if a in ids and b in ids}

    conn.execute("DELETE FROM domain_edges WHERE status != 'confirmed' AND locked = 0")
    n = vetoed = 0
    used_by: Counter = Counter()
    inferred: set = set()
    for (a, b), files in edge_files.items():
        if len(files) < _MIN_EDGE_FILES and (a, b) not in strong_edges:
            continue
        if (a, b) in refused:
            vetoed += 1
            continue
        conn.execute(
            "INSERT INTO domain_edges (src_domain, dst_domain, type, weight, why, status) "
            "VALUES (?,?,'uses',?,?, 'named')",
            (a, b, float(len(files)),
             f"{len(files)} files in '{feats[a]}' import '{feats[b]}' territory"))
        inferred.add((a, b))
        used_by[b] += 1
        n += 1

    # Relations the owner states. Imports show code calling code; content rendered by a
    # framework, or a quest driven by one, references nothing an extractor can read, so
    # the statement IS the evidence and it outranks anything inferred.
    stated = 0
    if ledger is not None:
        for src, dst in ledger.relations():
            a, b = ids.get(src), ids.get(dst)
            if a is not None and (a, b) in refused:
                log(f"    '{src}' uses '{dst}' and not-uses it too — the refusal wins; "
                    f"delete one of the two lines")
                continue
            if a is None or b is None:
                log(f"    '{src}' uses '{dst}': " +
                    ("no entry called " + ("'" + src + "'" if a is None else "'" + dst + "'")))
                continue
            if (a, b) in inferred:
                conn.execute("UPDATE domain_edges SET why = why || ', and stated in the plan' "
                             "WHERE src_domain=? AND dst_domain=?", (a, b))
                continue
            conn.execute(
                "INSERT INTO domain_edges (src_domain, dst_domain, type, weight, why, status) "
                "VALUES (?,?,'uses',1.0,'stated in gitchronicle.plan','named')", (a, b))
            used_by[b] += 1
            stated += 1
            n += 1

    # framework hubs: heavily-used features carry the 'built-on' narrative
    hubs = []
    for did, cnt in used_by.most_common():
        if cnt >= _HUB_MIN_FANIN:
            # hub promotion never overwrites a shelf class: inherited code is imported
            # by everything — that makes it a dependency, not one of the owner's frameworks
            conn.execute("UPDATE domains SET classification='core', fan_in=? WHERE id=? "
                         "AND classification IN ('feature','core')", (cnt, did))
            conn.execute("UPDATE domains SET fan_in=? WHERE id=?", (cnt, did))
            hubs.append((feats[did], cnt))
    conn.commit()
    log(f"  {n} uses-edges ({stated} stated, {vetoed} refused by the plan, "
        f"the rest from imports); hubs: " + (", ".join(f"{h} (used by {c})" for h, c in hubs[:6])
                                       or "none"))
    return {"edges": n, "hubs": hubs[:10]}


def edge_evidence(conn, repo: str, src: str, dst: str, limit: int = 40,
                  max_files: int = _MAX_FILES_PER_FEATURE) -> list[dict]:
    """The references that put an edge there: which file names which file.

    A link the owner cannot interrogate is a link the owner cannot judge — and one of the
    first ones looked at was wrong.
    """
    idx = _index(conn, repo, max_files)
    ids = {n: d for d, n in idx.get("feats", {}).items()}
    if src not in ids or dst not in ids:
        return []
    a, b = ids[src], ids[dst]
    out, reader = [], BatchReader(repo)
    try:
        for f in idx["files"].get(a, []):
            text = reader.read("HEAD", f, limit=60000)
            if not text:
                continue
            for ref, kind in extract_import_refs(text, with_kind=True):
                hits = _resolve(ref, kind, idx["by_base"])
                if {d for d, _ in hits} != {b}:
                    continue
                for _, p in hits[:2]:
                    out.append({"file": f, "ref": ref, "target": p})
                if len(out) >= limit:
                    return out
    finally:
        reader.close()
    return out
