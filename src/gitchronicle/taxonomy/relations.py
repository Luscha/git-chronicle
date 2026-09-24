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

from ..extract.git_ingest import BatchReader
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


def build_relations(conn, repo: str, log=print, ledger=None,
                    max_files: int = _MAX_FILES_PER_FEATURE) -> dict:
    feats = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM domains WHERE status IN ('named','confirmed','provisional')")}
    if not feats:
        log("  no register — run `gitchronicle register` first")
        return {"edges": 0}

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
    by_base: dict[str, list[int]] = defaultdict(list)
    for path, did in owner.items():
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
        by_base[base].append(did)

    # read each feature's top territory files once; collect cross-feature references
    per_feat_files: dict[int, list[str]] = defaultdict(list)
    for path, did in owner.items():
        if len(per_feat_files[did]) < max_files:
            per_feat_files[did].append(path)
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
            for ref in extract_import_refs(text):
                if ref in embed_owner:
                    target = embed_owner[ref]     # None = ambiguous registration
                    strong = True                 # coined module name: one import suffices
                else:
                    # DISTINCT owners: char_traits.hpp and char_traits.cpp are two files
                    # and one entry, and counting files made every C/C++ header/source pair
                    # look ambiguous — 499 import targets in this repository alone
                    owners = set(by_base.get(ref, []))
                    target = next(iter(owners)) if len(owners) == 1 else None
                    strong = False
                if target is not None and target != did:
                    edge_files[(did, target)].add(f)
                    if strong:
                        strong_edges.add((did, target))

    conn.execute("DELETE FROM domain_edges WHERE status != 'confirmed' AND locked = 0")
    n = 0
    used_by: Counter = Counter()
    inferred: set = set()
    for (a, b), files in edge_files.items():
        if len(files) < _MIN_EDGE_FILES and (a, b) not in strong_edges:
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
        ids = {name: did for did, name in feats.items()}
        for src, dst in ledger.relations():
            a, b = ids.get(src), ids.get(dst)
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
    log(f"  {n} uses-edges ({stated} stated, the rest from imports); hubs: " + (", ".join(f"{h} (used by {c})" for h, c in hubs[:6])
                                       or "none"))
    return {"edges": n, "hubs": hubs[:10]}
