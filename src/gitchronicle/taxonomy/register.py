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


def _worktree_units(repo: str, scope,
                    only: set | None = None) -> tuple[dict[str, list[str]], list[str]]:
    """Scoped worktree -> module units by global stem family (dir-split when huge).
    `only` restricts carving to a subset (v0.2: the authored, anchor-unclaimed residue)."""
    from ..untangle.untangle import _stem_families
    files = [p for p in run_git(repo, ["ls-files"]).splitlines()
             if p.strip() and scope(p) and (only is None or p in only)]
    # code files only: dominant extensions of the scoped tree (data/assets excluded)
    extc = Counter(p.rsplit(".", 1)[-1].lower() for p in files if "." in p)
    code_exts = {e for e, n in extc.most_common(14)
                 if e not in ("png", "jpg", "dds", "tga", "wav", "mp3", "bin", "dat",
                              "gif", "bmp", "ico", "ttf", "sub", "gr2", "mse", "msa")}
    files = [p for p in files if "." in p and p.rsplit(".", 1)[-1].lower() in code_exts]
    # families are COMPONENT-SCOPED: a stem family must never straddle two components
    # (Tools/WorldEditor vs Tools/SoundArranger), or the unit belongs to neither and
    # folds into neither — 'ActorInstanceAccessor' living in two tools at once.
    bycomp: dict[str, list[str]] = defaultdict(list)
    for f in files:
        parts = f.split("/")
        bycomp["/".join(parts[:2]) if len(parts) > 2 else parts[0]].append(f)
    fams: dict[str, list[str]] = {}
    leftover: list[str] = []
    for comp in sorted(bycomp):
        cf, cl = _stem_families(bycomp[comp])
        for st, fs in cf.items():
            fams[st if st not in fams else f"{st} [{comp.rsplit('/', 1)[-1]}]"] = fs
        leftover += cl
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


_LICENSE_RE = re.compile(r"(?i)^(licen[cs]e|copying|copyright|notice)(\.|$)")

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
    cand = [(i, m) for i, m in enumerate(final)
            if not m.get("ack") and not m.get("anchor") and not m.get("inherited")]
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
    fails = 0
    for i in range(0, len(pairs), 10):
        chunk = pairs[i:i + 10]
        listing = "\n".join(
            f"[{j}] {cand[a][1]['name']} — {(cand[a][1]['definition'] or '')[:110]}\n"
            f"    VS {cand[b][1]['name']} — {(cand[b][1]['definition'] or '')[:110]}"
            for j, (_, a, b) in enumerate(chunk))
        out = None
        for attempt in range(2):
            try:
                out = provider.chat(
                    "Each numbered item shows TWO catalog entries from ONE software "
                    "project. Answer which numbers describe THE SAME feature (one "
                    "capability under two names — e.g. a design-doc name vs the code's "
                    "name). Different features that merely interact or share a subsystem "
                    "are NOT the same. "
                    'Respond JSON: {"same":[<numbers>]}',
                    listing, want_json=True,
                    cache_extra=f"reg-alias:{i}:{attempt}")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt:
                    fails += 1
                    log(f"  alias chunk {i} failed twice ({exc})")
        for j in (out.get("same") or []) if isinstance(out, dict) else []:
            try:
                j = int(j)
            except (TypeError, ValueError):
                continue
            if 0 <= j < len(chunk):
                same.append((chunk[j][1], chunk[j][2]))
    if not same:
        log(f"  alias pass: {len(pairs)} candidates, none confirmed"
            + (f" ({fails} chunks FAILED)" if fails else ""))
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
        vn = sum(m.get("_vn", len(m["files"]) if m.get("vendored") else 0)
                 for m in members)
        base = {**base,
                "name": keep_key(base["name"], keys),
                "files": list(dict.fromkeys(f for m in members for f in m["files"])),
                "stems": set().union(*(m["stems"] for m in members)),
                "vendored": vn * 2 > sum(len(m["files"]) for m in members),
                "doc_only": all(m.get("doc_only") for m in members)}
        merged_out.append(base)
        absorbed.update(id(m) for m in members)
    kept = [m for m in final if id(m) not in absorbed]
    log(f"  alias pass: {len(pairs)} candidates, {len(same)} confirmed, "
        f"{len(absorbed) - len(merged_out)} entries absorbed")
    return kept + merged_out



_IMPORT_LINE_RE = re.compile(r"""\s*(#\s*include|#\s*import|import\s|from\s|require\s*\(|
                                  \s*using\s|@import)""", re.X)
_PATHLIT_RE = re.compile(r"""["'`]([\w./-]{4,120})["'`]""")
_GENERATED_RE = re.compile(r"(?i)do not edit|@generated|autogenerated|automatically generated")


def _data_dirs(repo: str, code_files: list[str], all_files: set, reader) -> dict[str, int]:
    """Directories that CODE names in string literals are DATA of that code — the only
    generic way to tell markup-that-is-code (uchtml layouts) from markup-that-is-content
    (the in-game wiki). Every language reaches its data by path; nothing else does.
    Returns dir -> number of distinct code files that name it."""
    dirs = {f.rsplit("/", 1)[0] for f in all_files if "/" in f}
    # code names data RELATIVELY ("locale/universal/wiki/p/..."), not from the repo
    # root — index every directory by its path suffixes so a literal can find it
    by_suffix: dict[str, set] = defaultdict(set)
    for d in dirs:
        seg = d.split("/")
        for k in range(2, min(len(seg), 5) + 1):
            by_suffix["/".join(seg[-k:])].add(d)
    hits: dict[str, set] = defaultdict(set)
    for cf in code_files:
        text = reader.read("HEAD", cf, limit=20000)
        if not text:
            continue
        # include/import lines quote paths too, but that is the CODE graph, not data
        text = "\n".join(ln for ln in text.splitlines()
                         if not _IMPORT_LINE_RE.match(ln))
        own = cf.rsplit("/", 1)[0]
        for m in _PATHLIT_RE.finditer(text):
            lit = m.group(1).strip("/")
            if "/" not in lit:
                continue
            seg = [x for x in lit.split("/") if x not in (".", "..")]
            # longest suffix of the literal that IS a directory of this repo
            for k in range(min(len(seg), 5), 1, -1):
                cand = by_suffix.get("/".join(seg[-k:]) if False else "/".join(seg[:k]))
                cand = cand or by_suffix.get("/".join(seg[-k:]))
                if cand and len(cand) == 1:
                    d = next(iter(cand))
                    if d != own:            # a file naming its own directory proves nothing
                        hits[d].add(cf)
                    break
    return {d: len(v) for d, v in hits.items()}


def _keep_key(name: str, keys: set) -> str:
    """A coined identifier (uchtml, luna) must survive every rename — it is the
    owner's own word for the feature and the KB's most searchable handle."""
    ks = sorted(k for k in keys if len(k) >= 5 and " " not in k)
    if not ks or any(k.lower() in name.lower() for k in ks):
        return name
    return f"{name} ({ks[0]})"[:70]


_CLUSTER_CAP = 120      # a consolidated feature never exceeds this (anti-blob)
_SUBTREE_MAX = 800      # self-contained subtree collapses to one entry up to this
_LEAF_MAX = 2500        # hard ceiling on any collapsed subtree
_DOC_EXTS = {"md", "markdown", "txt", "rst", "adoc"}
# "source" = a file that participates in an import/include graph. Not a taxonomy of
# file types: it is the set of extensions whose files this repo's import regexes read.
_SRC_EXTS = {"c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx", "py", "pyw", "lua",
             "cs", "js", "mjs", "ts", "tsx", "jsx", "java", "go", "rs", "rb", "php"}
_QUIET_SHARE = 0.01     # a subtree touched by <= 1% of the project's commits was never
                        # the SITE of development: it arrived as a unit (a tool, an
                        # imported library) and is ONE artifact. Measured on void-queue:
                        # tools 1 commit; Client/UserInterface 141; Server/game 452 —
                        # feature homes are hammered continuously, tools are not.
_FANIN_HUB = 8          # units referenced by this many others are infrastructure, not fragments


def _home_dir(files: list[str]) -> str:
    """Deepest common directory of a unit's files."""
    parts = [f.split("/")[:-1] for f in files]
    if not parts:
        return ""
    pre = parts[0]
    for q in parts[1:]:
        n = 0
        while n < min(len(pre), len(q)) and pre[n] == q[n]:
            n += 1
        pre = pre[:n]
        if not pre:
            break
    return "/".join(pre)


def _consolidate_units(conn, repo: str, entries: list[dict], provider, keep_key,
                       log) -> list[dict]:
    """Merge peek-labelled units the IMPORT GRAPH proves are one system. Stem kinship
    carves structure; use defines features — an abstract-renderer header and its
    renderers, or a tool's subtree, are one feature however the basenames family.
    Locality (common ancestor dir) + size caps keep this from ever building blobs.

    The graph is built over ALL units (a tool's own files reference the SDK headers it
    bundles — drop those and its cohesion vanishes), but a cluster MATERIALISES per
    class: a tool that bundles an SDK sample tree yields its own feature plus one
    third-party entry, never one blob that buries the owner's tool in the shelf."""
    idx = [e for e in entries if len(e["files"]) <= 300]
    pos = {id(e): i for i, e in enumerate(idx)}
    own: dict[str, int] = {}
    by_base: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(idx):
        for f in e["files"]:
            own.setdefault(f, i)
            base = f.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if base in ("__init__", "index", "mod") and "/" in f:
                base = f.rsplit("/", 2)[-2].lower()
            by_base[base].append(i)

    from .imports import extract_import_refs
    reader = BatchReader(repo)
    refs_of: dict[tuple, set] = defaultdict(set)      # (src_unit, dst_unit) -> src files
    try:
        for i, e in enumerate(idx):
            if len(e["files"]) > _CLUSTER_CAP:
                continue                      # big dir-units: members, not edge sources
            comp_i = "/".join(e["files"][0].split("/")[:2])
            for f in sorted(e["files"])[:60]:
                text = reader.read("HEAD", f, limit=20000)
                if not text:
                    continue
                for ref in extract_import_refs(text):
                    owners = sorted(set(by_base.get(ref, [])))
                    if len(owners) > 1:
                        # a basename can exist in several components; resolution is
                        # LOCAL (an #include in a tool means the tool's header, not a
                        # same-named engine header) — without this, every reference
                        # inside a component with colliding names is silently dropped
                        local = [o for o in owners
                                 if "/".join(idx[o]["files"][0].split("/")[:2]) == comp_i]
                        owners = local
                    if len(owners) == 1 and owners[0] != i:
                        refs_of[(i, owners[0])].add(f)
    finally:
        reader.close()

    # 0) the pkg/dir/family generators can carve the SAME files -> one unit, not three
    sig: dict[tuple, int] = {}
    dup_pairs = []
    for i, e in enumerate(idx):
        k = tuple(sorted(e["files"]))
        if k in sig:
            dup_pairs.append((sig[k], i))
        else:
            sig[k] = i

    homes = [_home_dir(e["files"]) for e in idx]
    fan_in = Counter(b for (_, b) in refs_of)

    # how many distinct commits ever touched each unit (the density evidence)
    total = conn.execute("SELECT COUNT(*) FROM commits WHERE is_merge=0").fetchone()[0]
    quiet_max = max(3, int(total * _QUIET_SHARE))
    owner_of: dict[str, int] = {}
    for i, e in enumerate(idx):
        for f in e["files"]:
            owner_of.setdefault(f, i)
    commits_of: dict[int, set] = defaultdict(set)
    for r in conn.execute("SELECT path, commit_hash FROM commit_files"):
        i = owner_of.get(r["path"])
        if i is not None:
            commits_of[i].add(r["commit_hash"])

    def _ancestor_ok(a: int, b: int) -> bool:
        ha, hb = homes[a], homes[b]
        if not ha or not hb:
            return False
        share = _home_dir([ha + "/x", hb + "/x"])
        return share.count("/") >= 1                  # depth >= 2 (e.g. Client/EterLibrary)

    root = list(range(len(idx)))

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    size = [len(e["files"]) for e in idx]

    def union(a, b) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb or size[ra] + size[rb] > _CLUSTER_CAP:
            return False
        root[ra] = rb
        size[rb] += size[ra]
        return True

    n_dup = 0
    for a, b in dup_pairs:
        n_dup += union(a, b)

    # 1) mutual references: interface and implementation cite each other
    mutual = sorted((min(len(refs_of[(a, b)]) + len(refs_of[(b, a)]), 99), a, b)
                    for (a, b) in refs_of if a < b and (b, a) in refs_of)
    n_mut = 0
    for _, a, b in sorted(mutual, reverse=True):
        if fan_in[a] <= _FANIN_HUB and fan_in[b] <= _FANIN_HUB and _ancestor_ok(a, b):
            n_mut += union(a, b)

    # 2) a small fragment folds into the system that uses it — but ONLY when that
    #    system is the sole user. A fragment used by exactly one cluster belongs to it
    #    (a base class its siblings derive from); a small unit used by MANY clusters is
    #    shared infrastructure — a framework — and must stand on its own. (Luna's
    #    package root is one file imported by 25 UI modules: absorbing it into whichever
    #    module happened to reference it first erased the framework from the register.)
    users_of: dict[int, set] = defaultdict(set)
    for (a, b), fs in refs_of.items():
        if len(fs) >= 2:
            users_of[b].add(a)
    n_dir = 0
    for b, users in sorted(users_of.items()):
        if len(idx[b]["files"]) > 10:
            continue
        homes_of_users = {find(u) for u in users}
        if len(homes_of_users) != 1:
            continue                      # used by several systems: shared, keep it
        a = sorted(users)[0]
        if homes[a] == homes[b] or _ancestor_ok(a, b):
            n_dir += union(b, a)

    # 3) a self-contained subtree is ONE tool/feature however many stem families live
    #    inside it: cohesion is MEASURED (references stay internal), so the cluster cap
    #    does not apply — but genuinely multi-feature trees (a 600-file client library)
    #    stay split via the size sanity bound and the deepest-first walk
    anc: dict[str, set[int]] = defaultdict(set)
    for i, h in enumerate(homes):
        parts = h.split("/")
        for d in range(2, len(parts) + 1):
            anc["/".join(parts[:d])].add(i)
    n_sub = 0
    collapsed: dict[str, int] = {}
    for d in sorted(anc, key=lambda x: (-x.count("/"), x)):
        members = sorted(anc[d])
        roots = sorted({find(m) for m in members})
        if len(roots) < 2:
            continue
        mem = set(members)
        internal = external = 0
        for (a, b), fs in refs_of.items():
            if a in mem:
                if b in mem:
                    internal += len(fs)
                else:
                    external += len(fs)
        # inbound = references INTO this subtree from outside it. A subtree nothing
        # else imports from is a LEAF PRODUCT (a standalone tool): it may collapse
        # whole, however large. A subtree others depend on hosts shared features
        # (Client/UserInterface) and must stay decomposed.
        total = sum(size[r] for r in roots)
        if total > _LEAF_MAX:
            continue
        # A subtree is ONE product when it is internally coherent AND the project
        # barely touched it: development density, not naming, tells a tool (arrived
        # whole, never iterated) from a feature home (Server/game: 452 commits of
        # affect, battle, guild...). Naming cannot: raw peek labels inside a tool are
        # as diverse as anywhere else.
        ratio = internal / max(1, internal + external)
        touched = len({h for i in members for h in commits_of.get(i, ())})
        if internal >= 2 and ratio >= 0.5 and touched <= quiet_max:
            base = roots[0]
            for r in roots[1:]:
                ra, rb = find(base), find(r)
                if ra != rb:
                    root[ra] = rb
                    size[rb] += size[ra]
                    base = rb
                    n_sub += 1
            collapsed[d] = find(base)

    # 4) mop-up: global stem families leak a tool's files across the tree by generic
    #    basenames ('version', 'shaders'); a unit majority-inside a collapsed subtree
    #    belongs to it
    n_mop = 0
    for d in sorted(collapsed, key=lambda x: (-x.count("/"), x)):
        r = find(collapsed[d])
        for i, e in enumerate(idx):
            if find(i) == r:
                continue
            share = sum(1 for f in e["files"] if f.startswith(d + "/")) / len(e["files"])
            if share >= 0.6:
                ra, rb = find(i), r
                if ra != rb and size[ra] + size[rb] <= _SUBTREE_MAX:
                    root[ra] = rb
                    size[rb] += size[ra]
                    n_mop += 1

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(idx)):
        clusters[find(i)].append(i)
    merged_ids = set()
    out: list[dict] = []
    renamed = []
    for r, members in sorted(clusters.items()):
        if len(members) < 2:
            continue
        allms = [idx[i] for i in members]
        for vend in (False, True):
            ms = [m for m in allms if bool(m.get("vendored")) is vend]
            if not ms:
                continue
            merged_ids.update(id(m) for m in ms)
            if len(ms) == 1:
                out.append(ms[0])          # lone member of its class: unchanged
                continue
            base = max(ms, key=lambda m: (len(m["files"]), m["name"]))
            keys = {m["key"] for m in ms if m.get("key")}
            e = {**base,
                 "name": keep_key(base["name"], keys),
                 "files": sorted(dict.fromkeys(f for m in ms for f in m["files"])),
                 "stems": set().union(*(m["stems"] for m in ms)),
                 "tier": max(m["tier"] for m in ms),
                 "vendored": vend,
                 "_ckeys": keys,
                 "_members": [m["name"] for m in ms][:8]}
            out.append(e)
            if len(ms) >= 3 and not vend:
                renamed.append(e)
    # name genuinely multi-part clusters at feature altitude (one cheap batch)
    for i in range(0, len(renamed), 12):
        chunk = renamed[i:i + 12]
        listing = "\n".join(f"[{j}] parts: {'; '.join(e['_members'])}\n"
                             f"    definition: {(e['definition'] or '')[:120]}"
                             for j, e in enumerate(chunk))
        try:
            got = provider.chat(
                "Each numbered item lists code modules that the import graph proves form "
                "ONE feature/system of a software project. Name that system at feature "
                "altitude (what a maintainer calls it; keep coined identifiers verbatim) "
                "and define it in one sentence. "
                'Respond JSON: {"clusters":{"<n>":{"name":"...","definition":"..."}}}',
                listing, want_json=True, cache_extra=f"reg-cluster:{i}")
            cl = got.get("clusters", {}) if isinstance(got, dict) else {}
            for j, e in enumerate(chunk):
                r2 = cl.get(str(j)) or {}
                if r2.get("name"):
                    e["name"] = keep_key(str(r2["name"]).strip()[:70],
                                         e.get("_ckeys") or set())
                if r2.get("definition"):
                    e["definition"] = str(r2["definition"]).strip()[:400]
        except Exception:  # noqa: BLE001
            continue
    kept = [e for e in entries if id(e) not in merged_ids]
    log(f"  consolidation: {n_dup} dup + {n_mut} mutual + {n_dir} fragment + "
        f"{n_sub} subtree + {n_mop} mop-up "
        f"-> {len(entries)} units => {len(kept) + len(out)} entries")
    return kept + out


def build_register(conn, provider, repo: str, cfg: dict, log=print,
                   force: bool = False) -> dict:
    md = cfg.get("scope", {}).get("file", "gitchronicle.md")
    scope = Scope.load(md)
    have = conn.execute("SELECT COUNT(*) FROM domains "
                        "WHERE status IN ('named','confirmed')").fetchone()[0]
    if have and not force:
        log(f"  register exists ({have} features) — use --force to rebuild")
        return {"register": have, "skipped": True}

    # ---- v0.2: delta first (inherited never becomes a feature), anchors second
    # (frameworks claim cross-component territory by name), residue carves locally
    from .anchors import claim_territory, discover_anchors
    from .delta import file_authorship
    scoped = [p for p in run_git(repo, ["ls-files"]).splitlines()
              if p.strip() and scope(p)]
    klass = file_authorship(conn, repo, scoped, log=log)
    authored = [f for f in scoped if klass[f] == "authored"]
    inherited = [f for f in scoped if klass[f] == "inherited"]
    anchors = discover_anchors(repo, authored, inherited, log=log)
    claims = claim_territory(anchors, authored)
    claimed = {f for fs in claims.values() for f in fs}
    log(f"  anchors claim {len(claimed)} authored files across "
        f"{len(claims)} identities")

    units, leftover = _worktree_units(repo, scope,
                                      only=set(authored) - claimed)
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
                                    "files": fs, "stems": stems})
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

    # DATA and GENERATED units are not features.
    #  - data: a unit whose files live under a directory that CODE names in string
    #    literals is the data that code consumes (the in-game wiki: 262 html/json files
    #    reached by path from the wiki renderer). Markup that is CODE (uchtml layouts)
    #    is composed via imports and never appears this way.
    #  - generated: files that say so ("DO NOT EDIT", "@generated") — protobuf stubs and
    #    template caches are not features anyone wrote.
    treader = BatchReader(repo)
    try:
        code_files = [f for e in entries for f in e["files"][:6]
                      if f.rsplit(".", 1)[-1].lower() in _SRC_EXTS]
        allf = {f for e in entries for f in e["files"]}
        ddirs = _data_dirs(repo, code_files[:2500], allf, treader)
        n_data = n_gen = 0
        for e in entries:
            fs = e["files"]
            named = sum(1 for f in fs
                        if any(f.startswith(d + "/") for d in ddirs))
            if named * 2 > len(fs) and not any(
                    f.rsplit(".", 1)[-1].lower() in _SRC_EXTS for f in fs):
                e["data"] = True          # majority of files are named-by-code data
                n_data += 1
                continue
            # an entry is generated when MOST of it is — one protobuf stub in an
            # otherwise hand-written feature must not condemn the feature
            probe = sorted(fs)[:5]
            gen = sum(1 for f in probe
                      if _GENERATED_RE.search(treader.read("HEAD", f, limit=400) or ""))
            if gen * 2 > len(probe):
                e["generated"] = True
                n_gen += 1
    finally:
        treader.close()
    log(f"  data/generated: {len(ddirs)} code-named data dirs -> {n_data} data units, "
        f"{n_gen} generated units")

    # vendored code is marked by its own LICENSE/COPYING file: a subtree that ships
    # one is a third-party library, whatever its files' headers say. Copyright headers
    # are NOT evidence — a derived codebase carries them everywhere, and confirming
    # them with an LLM shelved 91 first-party features (Clipboard Manager, Game Command
    # Interpreter...) as third-party. License files are unambiguous and free.
    lic_roots = sorted({f.rsplit("/", 1)[0] for f in run_git(repo, ["ls-files"]).splitlines()
                        if _LICENSE_RE.search(f.rsplit("/", 1)[-1]) and "/" in f})
    n_vend = 0
    for e in entries:
        if any(any(f.startswith(root + "/") for root in lic_roots) for f in e["files"][:20]):
            e["vendored"] = True
            n_vend += 1
    log(f"  vendored: {len(lic_roots)} licensed subtrees -> {n_vend} third-party units")

    # import-graph consolidation: stem carving fragments real systems (an abstract
    # renderer vs its pipeline, a tool subtree); use-evidence reunites them
    entries = _consolidate_units(conn, repo, entries, provider, _keep_key, log)

    # ---- anchor entries: the owner's frameworks, tier 5, cross-component territory.
    # Peeked for a definition like any unit; protected from every merge/dedupe below
    # (docs may merge INTO them — Doc/luna belongs to the luna anchor).
    from ..untangle.untangle import _representative as _rep2
    areader = BatchReader(repo)
    try:
        aitems = sorted(claims.items())
        for i in range(0, len(aitems), _PEEK_BATCH):
            part = aitems[i:i + _PEEK_BATCH]
            blocks = []
            for j, (key, fs) in enumerate(part):
                ents = [f for f in fs
                        if f.rsplit("/", 1)[-1].rsplit(".", 1)[0] in ("__init__", "index")]
                rep = min(ents, key=lambda f: f.count("/")) if ents else                     _rep2(repo, "HEAD", key, fs, reader=areader)
                head = areader.read("HEAD", rep, limit=_CODE_HEAD * 3)
                blocks.append(f"[{j}] framework '{key}' ({rep.rsplit('/', 1)[-1]}, "
                              f"{len(fs)} files across components):\n{head}")
            try:
                got = provider.chat(REGISTER_PEEK_SYS, "\n\n".join(blocks),
                                    want_json=True, cache_extra=f"reg-anchor:{i}")
            except Exception:  # noqa: BLE001
                got = {}
            labels = got.get("labels", {}) if isinstance(got, dict) else {}
            for j, (key, fs) in enumerate(part):
                r = labels.get(str(j)) or {}
                name = str((r.get("name") if isinstance(r, dict) else "") or key).strip()[:70]
                if name.lower() == "inconclusive":
                    name = key
                stems = set()
                for f in fs[:20]:
                    stems |= path_stems(f)
                stems.add(key)
                if key.lower() not in name.lower():
                    name = f"{name} ({key})"[:70]
                entries.append({"name": name, "tier": 5, "key": key, "anchor": True,
                                "definition": str(r.get("definition") or "").strip()[:400],
                                "files": fs, "stems": stems})
    finally:
        areader.close()

    # ---- inherited baseline: catalogued, attributable, never features
    bycomp2: dict[str, list[str]] = defaultdict(list)
    for f in inherited:
        parts = f.split("/")
        bycomp2["/".join(parts[:2]) if len(parts) > 2 else parts[0]].append(f)
    for comp, fs in sorted(bycomp2.items()):
        if len(fs) >= 3:
            entries.append({"name": f"{comp} (inherited baseline)"[:70], "tier": 1,
                            "inherited": True, "doc_only": False,
                            "definition": "code inherited from the upstream base; "
                                          "maintenance only, not the owner's feature",
                            "files": sorted(fs)[:400],
                            "stems": set()})

    # docs are EVIDENCE about features, not features. A doc only becomes a register
    # entry in its own right when it genuinely DEFINES a system (design doc); how-tos
    # and references merge into the code feature they corroborate (code wins naming);
    # meta/navigation pages and uncorroborated leftovers shelve as classification='doc'.
    # An LLM triage on title+excerpt decides the kind and coins the canonical
    # (code-language) feature name — doc titles in any language stay searchable in the
    # definition text.
    code_stems = set().union(*(e["stems"] for e in entries)) if entries else set()
    docdirs: dict[str, list[dict]] = defaultdict(list)
    singles: list[dict] = []
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
            singles.append(d)
    finally:
        dreader.close()
    for i in range(0, len(singles), 15):
        chunk = singles[i:i + 15]
        listing = "\n".join(
            f"[{j}] title: {d['title'][:70]}\n    excerpt: "
            f"{d['excerpt'].replace(chr(10), ' ')[:150]}"
            for j, d in enumerate(chunk))
        got = {}
        try:
            got = provider.chat(
                "Classify each documentation page of ONE software project. kind: "
                "'design' = defines/specifies a system or feature; 'howto' = procedure "
                "or guide about existing functionality; 'meta' = index/navigation/"
                "process page. Also give name: the concise English feature-altitude "
                "name of the system the page is about (keep the project's coined "
                'identifiers verbatim). Respond JSON: {"docs":{"<n>":{"kind":"design|'
                'howto|meta","name":"..."}}}',
                listing, want_json=True, cache_extra=f"reg-doctriage:{i}")
        except Exception:  # noqa: BLE001
            got = {}
        kinds = got.get("docs", {}) if isinstance(got, dict) else {}
        for j, d in enumerate(chunk):
            k = kinds.get(str(j)) or {}
            kind = str(k.get("kind") or "howto").lower()
            name = str(k.get("name") or d["title"]).strip()[:70] or d["title"][:70]
            corro = bool(d["idents"] or (path_stems(d["path"]) & code_stems))
            # design docs stand as feature candidates; how-tos merge into code (tier
            # below the code peek so code names win) or shelve; meta always shelves
            entries.append({
                "name": name,
                "tier": 4 if (kind == "design" and d["idents"]) else 2,
                "doc_kind": kind,
                # ONLY a design doc defines a feature. How-tos and meta pages are
                # sources ABOUT features: they merge into the code they corroborate
                # (code wins the name, doc joins the territory) or shelve as docs.
                "doc_only": kind != "design" or not corro,
                "definition": (f"[{d['title']}] " + d["excerpt"].replace("\n", " "))[:400],
                "files": [d["path"]],
                "stems": (path_stems(d["path"]) | d["idents"]) if kind != "meta"
                         else path_stems(d["path"])})
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
    def _comp(f: str) -> str:
        parts = f.split("/")
        return "/".join(parts[:2]) if len(parts) > 2 else parts[0]

    # code entities seed and absorb; DOCS never serve as homes — a design doc with
    # generic corroborated identifiers seeded a 400-file eight-component blob. A doc
    # merges into its single BEST code home and donates its tier-4 naming rights there.
    is_doc = lambda d: bool(d.get("doc_kind") or d.get("doc_only"))
    merged: list[dict] = []
    for e in sorted(entries, key=lambda d: (is_doc(d), -d["tier"])):
        nest = _norm_stems(e["stems"] - god)
        ecomp = {_comp(f) for f in e["files"][:40]}
        home = None
        best = 0.0
        for m in merged:
            # component sets are FROZEN at seed time: any accumulating field re-opens
            # the accretion door (docs are exempt — they cross components by nature)
            if not is_doc(e) and not (ecomp & m["_comp"]):
                continue
            if is_doc(m):
                continue
            small = min(len(nest), len(m["_seed"])) or 1
            ov = nest & m["_seed"]
            if e.get("key") and m.get("key") and e["key"] != m["key"]:
                continue
            if (len(ov) >= 2 or any(" " in s for s in ov)) and len(ov) * 2 >= small:
                score = len(ov) / small
                if is_doc(e):
                    if score > best:      # a doc attaches to its BEST home, not the first
                        best, home = score, m
                else:
                    home = m
                    break
        if home is None:
            merged.append({**e, "_norm": nest, "_seed": frozenset(nest),
                           "_comp": ecomp,
                           "_vn": len(e["files"]) if e.get("vendored") else 0,
                           "_keys": {e["key"]} if e.get("key") else set()})
        else:
            home["files"] = list(dict.fromkeys(home["files"] + e["files"]))
            home["_norm"] |= nest
            home["stems"] |= e["stems"]
            if e.get("key"):
                home["_keys"].add(e["key"])
            if e.get("vendored"):
                home["_vn"] = home.get("_vn", 0) + len(e["files"])
            if e["tier"] > home["tier"]:
                home.update(name=e["name"], definition=e["definition"], tier=e["tier"])
            home["name"] = _keep_key(home["name"], home["_keys"])
    for m in merged:
        # vendored only when third-party files are the MAJORITY — a first-party
        # feature absorbing two generated templates must not get shelved
        m["vendored"] = m.get("_vn", 0) * 2 > len(m["files"])
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
                vn = sum(m.get("_vn", len(m["files"]) if m.get("vendored") else 0)
                         for m in members)
                base = {**base, "vendored": vn * 2 > sum(len(m["files"]) for m in members)}
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

    # containment merge: two entries whose TERRITORIES largely coincide are one feature,
    # whatever their stems say (SphereLibrary was catalogued twice — an 11-file entry and
    # a 5-file subset of it, the twin left commit-less). Boilerplate shared by many
    # entries (StdAfx, CMakeLists) cannot fuse anything: it is excluded from the overlap.
    fcount: Counter = Counter()
    for e in final:
        for f in e["files"]:
            fcount[f] += 1
    boiler = {f for f, n in fcount.items() if n > 4}
    root = list(range(len(final)))

    def _find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    inv: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(final):
        if e.get("anchor") or e.get("inherited") or e.get("ack"):
            continue
        for f in e["files"]:
            if f not in boiler:
                inv[f].append(i)
    ov: Counter = Counter()
    for f, owners in inv.items():
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                ov[(owners[i], owners[j])] += 1
    n_cont = 0
    for (a, b), n in sorted(ov.items(), key=lambda kv: -kv[1]):
        sa = len([f for f in final[a]["files"] if f not in boiler]) or 1
        sb = len([f for f in final[b]["files"] if f not in boiler]) or 1
        if n >= 2 and n / min(sa, sb) >= 0.7:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                root[ra] = rb
                n_cont += 1
    if n_cont:
        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(len(final)):
            groups[_find(i)].append(i)
        collapsed = []
        for members in groups.values():
            ms = [final[i] for i in members]
            if len(ms) == 1:
                collapsed.append(ms[0])
                continue
            base = max(ms, key=lambda m: (m["tier"], len(m["files"])))
            collapsed.append({**base,
                              "files": list(dict.fromkeys(f for m in ms for f in m["files"])),
                              "stems": set().union(*(m["stems"] for m in ms))})
        final = collapsed
    log(f"  containment merge: {n_cont} overlapping territories absorbed "
        f"-> {len(final)} entries")

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
        # a FEATURE has code. A design doc that never merged with a code territory
        # documents something the register could not locate — catalogue it as a doc,
        # never as the feature itself (Luna Bridge owned 6 markdown files and no code).
        if not e.get("ack") and all(f.rsplit(".", 1)[-1].lower() in _DOC_EXTS
                                    for f in e["files"]):
            e["doc_only"] = True
        did = conn.execute(
            "INSERT INTO domains (discovery_run_id, name, slug, definition, stems, "
            "named_from, classification, status, created_by) "
            "VALUES (?,?,?,?,?,?,?,'named','auto')",
            (run_id, e["name"], _slug(e["name"]), e["definition"],
             json.dumps(sorted(e["stems"] - god)[:12]), len(e["files"]),
             "inherited" if e.get("inherited")
             else "vendored" if e.get("vendored")
             else "generated" if e.get("generated")
             else "content" if e.get("data")
             else "core" if e.get("anchor")
             else ("doc-only" if e.get("doc_only") else "feature"))).lastrowid
        conn.executemany("INSERT OR REPLACE INTO domain_files (domain_id, path, weight, source) "
                         "VALUES (?,?,1.0,'register')", [(did, f) for f in e["files"][:400]])
        n += 1
    conn.commit()
    log(f"  register: {n} features written")
    return {"register": n, "units": len(units)}
