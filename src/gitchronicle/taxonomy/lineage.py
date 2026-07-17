"""LINEAGE — v0.3: the history-native register (seeded assembly of untangled concerns).

v0.1 clustered commits by embedding similarity and got change-prose clusters, not
features. v0.2 read identity off the worktree and used history only for narrative.
v0.3 tests the middle that the untangle assessment justified: untangled concerns carry
workpackage boundaries well (file-boundary axis 1.80/2 on a 95-commit stratified
hand-grade), so features are assembled from concerns with the FILE as the join key.
Attribution comes free: a cluster's commits ARE its concerns' commits. Dead features
emerge naturally: a cluster whose files are all gone at HEAD is a removed feature.

WHY NOT TRANSITIVE CLUSTERING (measured, twice): single-link union over shared files
percolates — the first full run fused 5,510 of 5,835 concerns into one blob spanning
21 components, and excluding substrate hubs only shaved it to 5,083. On a 12-year
monorepo everything genuinely interconnects (a feature ships items, items touch shops,
shops touch UI), so ANY transitive closure over broad evidence converges to the giant
component. Assembly must be seeded and merging proportional; transitivity is the bug.

The three layers (all deterministic, all history-only, no config, no LLM):
  seeds  — COINED STEMS (name families whose file population is >= _COIN_SHARE
           authored: the owner's own vocabulary — luna, battlepass, uchtml) with
           >= _SEED_MIN concerns. Seed families merge ONLY by proportional
           containment (>= _SEED_CONTAIN of the smaller inside the larger); a
           concern carrying several seeds is assigned once, to its dominant family.
  attach — unseeded concerns join the seeded cluster that OWNS the majority of
           their lineage roots (rename-chain identity, authored files only).
           One hop: attachment never creates edges between clusters.
  micro  — what remains forms root-shared components among ITSELF only; families
           with >= _MIN_CONCERNS concerns from >= _MIN_COMMITS commits become
           name-less candidate features, the rest is residue.

Exclusions, measured on this corpus:
  provenance — origin='import'* concerns are drops and peek descriptions of what
           files ARE, not work (5.6% of concerns); they never cluster.
  inherited — maintenance on vanilla files routes to the baseline (2,299 concerns
           here); a feature of the owner's cannot be assembled from inherited code.
  substrate — a root in >= _SUBSTRATE_DEG concerns is corpus-wide substrate
           (locale_game.txt 511, item_proto 380, interfacemodule.py 298; feature
           cores stay <= ~40, p99 = 49): it identifies no feature and never votes
           in attachment or micro-clustering.
  glue   — a high-degree root whose concerns share almost no other evidence
           (mean pairwise Jaccard < _GLUE_COHESION) evidences integration, not
           identity: forge_game.cpp carries 33 unrelated concern labels.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import combinations

from ..extract.git_ingest import run_git
from .delta import _aliases, file_authorship, history_scan
from .ground import _STOP, _tokens, path_stems
from .register import _SRC_EXTS


def _seg_stems(path: str) -> set:
    """Stems from EVERY directory segment — path_stems reads only the immediate
    parent, but luna's territory lives in Server/libluna, luna/protobuf and
    Client-Files/root/luna alike. The lib- packaging prefix is stripped
    (libluna -> luna); stopwords and short tokens never become identity."""
    out: set = set()
    for seg in path.split("/")[:-1]:
        toks = _tokens(seg)
        out |= {t for t in toks if t not in _STOP}
        out |= {t[3:] for t in toks if t.startswith("lib") and len(t) > 5}
        out |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return out


def _lineage_stems(path: str) -> set:
    return path_stems(path) | _seg_stems(path)

_COIN_MIN_FILES = 3    # a coined stem needs a family, not a filename
_COIN_SHARE = 0.90     # share of the stem's file population that must be authored
_SEED_MIN = 3          # concerns a stem needs to seed a cluster
_SEED_COHESION = 0.05  # a seed's concerns must share lineage BESIDES the stem —
                       # kills structural dir vocabulary ('prototypes', 'universal',
                       # 'uiscript' seeded 686/661/331-concern junk clusters: their
                       # concerns share only substrate, real families share roots)
_SEED_CONTAIN = 0.70   # smaller seed folds into larger at this concern containment
_GOD_STEM_SHARE = 0.05 # a stem in > this share of ALL work concerns is platform
                       # vocabulary, not a feature name — 'proto' spans the item
                       # system, the dump tooling and every content shipment (the
                       # family it seeded fused 1,161 concerns / 13 components);
                       # luna, the largest real framework, sits at 4.4%
_SUBSTRATE_DEG = 50    # a root in >= this many concerns is substrate (~p99, docstring)
_GLUE_MIN_DEG = 8      # roots below this degree are never glue-tested
_GLUE_COHESION = 0.05  # mean pairwise Jaccard of other evidence below this = glue
_MIN_CONCERNS = 3      # a feature-grade cluster needs at least this many concerns
_MIN_COMMITS = 2       # ... from at least this many commits
_CONTENT_SRC_SHARE = 0.2   # below this source-file share a cluster is content, not code

# pre-registered kill criteria (build/untangle_eval_protocol.md and the v0.3 plan)
_KILL_BLOB_CONCERNS = 600
_KILL_BLOB_COMPONENTS = 4
_GOLDEN = {"luna": "/luna/", "augments": "augment", "uchtml": "uchtml"}
_GOLDEN_SHARE = 0.70


class _DSU:
    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        p = self.p
        while p.setdefault(x, x) != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)   # min-root keeps runs reproducible


def _work_concerns(conn) -> list[dict]:
    """The clustering population: LLM-untangled concerns of non-merge work commits."""
    out = []
    for r in conn.execute(
            "SELECT c.id, c.commit_hash, c.label, c.files, co.authored_at "
            "FROM concerns c JOIN commits co ON co.hash = c.commit_hash "
            "WHERE c.origin IS NULL AND co.is_merge = 0 ORDER BY c.id"):
        files = json.loads(r["files"] or "[]")
        if files:
            out.append({"id": r["id"], "commit": r["commit_hash"],
                        "label": (r["label"] or "").strip(),
                        "files": files, "at": r["authored_at"] or ""})
    return out


def _root_exclusions(work: list[dict]) -> tuple[set, set]:
    """Substrate (degree) + glue (degree with no co-evidence) roots — never evidence."""
    root_cons: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(work):
        for r in c["roots"]:
            root_cons[r].append(i)
    substrate = {r for r, idxs in root_cons.items() if len(idxs) >= _SUBSTRATE_DEG}
    glue = set()
    for r, idxs in sorted(root_cons.items()):
        if r in substrate or len(idxs) < _GLUE_MIN_DEG:
            continue
        pairs = list(combinations(idxs[:12], 2))[:60]
        tot = 0.0
        for a, b in pairs:
            ea = set(work[a]["roots"]) - {r}
            eb = set(work[b]["roots"]) - {r}
            union = len(ea | eb)
            tot += (len(ea & eb) / union) if union else 0.0
        if pairs and tot / len(pairs) < _GLUE_COHESION:
            glue.add(r)
    return substrate, glue


def build_lineage(conn, repo: str, log=print) -> dict:
    cons = _work_concerns(conn)
    log(f"  {len(cons)} work concerns (import-origin filtered)")

    first, parent = history_scan(repo)
    allfiles = sorted({f for c in cons for f in c["files"]})
    # coinage must be judged against EVERY path history ever saw, not just the
    # work-touched ones — over the touched population alone, generic vocabulary
    # ('render', 'sql', 'make') looks owner-exclusive and seeds junk families
    corpus = sorted(set(first) | set(allfiles))
    auth = file_authorship(conn, repo, corpus, log=log)
    root_of = {f: _aliases(f, parent)[-1] for f in allfiles}

    # coined stems: the owner's own vocabulary — the stem's population across the
    # WHOLE historical corpus must be almost entirely authored
    stem_files: dict[str, set] = defaultdict(set)
    for f in corpus:
        for s in _lineage_stems(f):
            stem_files[s].add(f)
    coined = {s for s, fs in sorted(stem_files.items())
              if len(fs) >= _COIN_MIN_FILES
              and sum(auth[f] == "authored" for f in fs) / len(fs) >= _COIN_SHARE}

    work, base = [], []
    for c in cons:
        roots = sorted({root_of[f] for f in c["files"] if auth[f] == "authored"})
        if not roots:
            base.append(c)                  # pure maintenance on inherited files
            continue
        stems: set = set()
        for f in c["files"]:
            if auth[f] == "authored":
                stems |= _lineage_stems(f) & coined
        c["roots"], c["stems"] = roots, sorted(stems)
        work.append(c)
    log(f"  {len(work)} concerns carry authored lineage; {len(base)} -> baseline; "
        f"{len(coined)} coined stems")

    substrate, glue = _root_exclusions(work)
    log(f"  {len(substrate)} substrate roots + {len(glue)} glue roots excluded")

    # ---- seeds: coined-stem families, merged by proportional containment ------
    stem_cons: dict[str, set] = defaultdict(set)
    for i, c in enumerate(work):
        for s in c["stems"]:
            stem_cons[s].add(i)

    def _shares_lineage(idx_set: set) -> bool:
        """A real family's concerns share roots beyond the stem; structural dir
        vocabulary shares only substrate. Sampled, deterministic."""
        pairs = list(combinations(sorted(idx_set)[:12], 2))[:60]
        if not pairs:
            return True
        tot = 0.0
        for a, b in pairs:
            ea = {r for r in work[a]["roots"] if r not in substrate and r not in glue}
            eb = {r for r in work[b]["roots"] if r not in substrate and r not in glue}
            union = len(ea | eb)
            tot += (len(ea & eb) / union) if union else 0.0
        return tot / len(pairs) >= _SEED_COHESION

    def _seed_ok(s: str, idx_set: set) -> bool:
        """Identity vocabulary crosses component boundaries (luna: libluna +
        root/luna + luna/ + Doc/luna) — structural vocabulary never leaves its
        one dir (prototypes/, universal/, uiscript/). Single-component stems can
        still seed if their concerns tightly share lineage (a local feature);
        a big framework's sub-areas need not pairwise-share roots (measured:
        luna cohesion 0.02 — the gate alone would kill it)."""
        comps = {f.split("/", 1)[0] for f in stem_files[s] if auth[f] == "authored"}
        return len(comps) >= 2 or _shares_lineage(idx_set)

    god_deg = max(_SEED_MIN + 1, int(len(work) * _GOD_STEM_SHARE))
    seeds = {s: idxs for s, idxs in sorted(stem_cons.items())
             if _SEED_MIN <= len(idxs) <= god_deg and _seed_ok(s, idxs)}
    order = sorted(seeds, key=lambda s: (-len(seeds[s]), s))
    sdsu = _DSU()
    for small in reversed(order):                       # smallest first
        cs = seeds[small]
        best, share = None, 0.0
        for big in order:                               # largest first
            if big == small or len(seeds[big]) < len(cs):
                break
            ov = len(cs & seeds[big]) / len(cs)
            if ov > share:
                best, share = big, ov
        if best is not None and share >= _SEED_CONTAIN:
            sdsu.union(order.index(best), order.index(small))
    fam_of: dict[str, str] = {s: order[sdsu.find(order.index(s))] for s in order}

    # one concern, one cluster: dominant family wins. Votes are per-FILE stem hits,
    # and a stem carried by the file's DIRECTORY outranks one from its basename
    # (v0.2's claim rule): bind_arena.cpp inside luna/ is luna territory — the
    # 'arena' basename token must not steal the luna bridge for the arena feature.
    assign: dict[int, str] = {}
    for i, c in enumerate(work):
        votes: Counter = Counter()
        for f in c["files"]:
            if auth[f] != "authored":
                continue
            dstems = _seg_stems(f)
            for s in _lineage_stems(f):
                if s in seeds:
                    votes[fam_of[s]] += 3 if s in dstems else 1
        if votes:
            assign[i] = min(votes, key=lambda f: (-votes[f], len(seeds[f]), f))

    # ---- attach: majority lineage-root owner; one hop, no new edges ------------
    root_owner: dict[str, Counter] = defaultdict(Counter)
    for i, fam in sorted(assign.items()):
        for r in work[i]["roots"]:
            if r not in substrate and r not in glue:
                root_owner[r][fam] += 1
    attached = 0
    for i, c in enumerate(work):
        if i in assign:
            continue
        votes = Counter()
        for r in c["roots"]:
            own = root_owner.get(r)
            if own:
                top = own.most_common(2)
                if len(top) == 1 or top[0][1] > top[1][1]:
                    votes[top[0][0]] += 1
        if votes:
            top = votes.most_common(2)
            if len(top) == 1 or top[0][1] > top[1][1]:
                assign[i] = top[0][0]
                attached += 1
    log(f"  seeded {len(assign) - attached} concerns via {len(seeds)} seed stems "
        f"({len(set(fam_of.values()))} families), attached {attached} via roots")

    # ---- micro: leftovers cluster among themselves only ------------------------
    left = [i for i in range(len(work)) if i not in assign]
    mdsu = _DSU()
    left_roots: dict[str, list[int]] = defaultdict(list)
    for i in left:
        for r in work[i]["roots"]:
            if r not in substrate and r not in glue:
                left_roots[r].append(i)
    for r, idxs in sorted(left_roots.items()):
        for a, b in zip(idxs, idxs[1:]):
            mdsu.union(a, b)
    for i in left:
        assign[i] = f"micro:{mdsu.find(i)}"

    # ---- materialize clusters ---------------------------------------------------
    head = set(run_git(repo, ["ls-tree", "-r", "--name-only", "HEAD"]).splitlines())
    fwd: dict[str, tuple[str, str]] = {}
    for new, (old, date) in parent.items():
        if old not in fwd or date > fwd[old][1]:
            fwd[old] = (new, date)

    def _alive(path: str) -> bool:
        seen: set = set()
        while path not in head and path in fwd and path not in seen:
            seen.add(path)
            path = fwd[path][0]
        return path in head

    members: dict[str, list[int]] = defaultdict(list)
    for i, fam in sorted(assign.items()):
        members[fam].append(i)
    clusters = []
    for fam, idxs in sorted(members.items()):
        cs = [work[i] for i in idxs]
        files = sorted({f for c in cs for f in c["files"] if auth[f] == "authored"})
        src = sum("." in f and f.rsplit(".", 1)[-1].lower() in _SRC_EXTS for f in files)
        clusters.append({
            # the content stream (proto/locale shipments) is a shelf, not a feature —
            # same class v0.2 shelved; here derived from the source-extension share
            "content": bool(files) and src / len(files) < _CONTENT_SRC_SHARE,
            "seed": None if fam.startswith("micro:") else fam,
            "idxs": idxs, "concerns": len(cs),
            "commits": len({c["commit"] for c in cs}),
            "files": files,
            "components": sorted({f.split("/", 1)[0] for f in files}),
            "labels": Counter(c["label"].lower() for c in cs if c["label"]),
            "born": min((c["at"] for c in cs if c["at"]), default="")[:10],
            "last": max((c["at"] for c in cs if c["at"]), default="")[:10],
            "dead": not any(_alive(f) for f in files),
        })
    clusters.sort(key=lambda cl: (-cl["concerns"], cl["files"][0] if cl["files"] else ""))

    report = _validate(clusters, work, log)
    return {"work": work, "base": base, "clusters": clusters,
            "substrate": sorted(substrate), "glue": sorted(glue),
            "auth": auth, "root_of": root_of, "alive": _alive, "report": report}


def _validate(clusters: list[dict], work: list[dict], log=print) -> dict:
    """Structural gates, pre-registered before any LLM is spent. Kill: v0.3 rejected."""
    shelved = [cl for cl in clusters if cl["content"]]
    code = [cl for cl in clusters if not cl["content"]]
    feats = [cl for cl in code
             if cl["concerns"] >= _MIN_CONCERNS and cl["commits"] >= _MIN_COMMITS]
    if shelved:
        top = shelved[0]
        log(f"  {len(shelved)} content clusters shelved (top: "
            f"{top['seed'] or '(micro)'} {top['concerns']} concerns — the proto/"
            f"locale shipment stream, never presented as a feature)")
    sizes = Counter()
    for cl in clusters:
        sizes["1"] += cl["concerns"] == 1
        sizes["2"] += cl["concerns"] == 2
        sizes["3-9"] += 3 <= cl["concerns"] <= 9
        sizes["10-49"] += 10 <= cl["concerns"] <= 49
        sizes["50-599"] += 50 <= cl["concerns"] <= 599
        sizes["600+"] += cl["concerns"] >= _KILL_BLOB_CONCERNS
    log(f"  {len(clusters)} clusters -> {len(feats)} feature-grade "
        f"(>= {_MIN_CONCERNS} concerns, >= {_MIN_COMMITS} commits); sizes: {dict(sizes)}")
    log(f"  dead feature-grade clusters: {sum(cl['dead'] for cl in feats)}")
    log("  top clusters:")
    for cl in code[:12]:
        top = ", ".join(l for l, _ in cl["labels"].most_common(3))
        log(f"    {cl['seed'] or '(micro)':<22} {cl['concerns']:4} concerns "
            f"{cl['commits']:4} commits  {cl['born']}..{cl['last']}  [{top}]")

    # blob = MANY UNRELATED features fused: huge AND cross-cutting (conjunctive —
    # the golden gate itself demands that an assembled framework span components,
    # so component-spread alone cannot be the kill signal; both readings reported)
    big = code[0] if code else {"concerns": 0, "components": []}
    blob = (big["concerns"] > _KILL_BLOB_CONCERNS
            and len(big["components"]) > _KILL_BLOB_COMPONENTS)

    golden = {}
    for name, pat in _GOLDEN.items():
        hit_idx = {i for i, c in enumerate(work)
                   if sum(pat in f.lower() for f in c["files"]) * 2 > len(c["files"])}
        if not hit_idx:
            golden[name] = (0.0, 0)
            continue
        best = max(clusters, key=lambda cl: len(hit_idx & set(cl["idxs"])))
        share = len(hit_idx & set(best["idxs"])) / len(hit_idx)
        golden[name] = (round(share, 2), len(hit_idx))
    n_gold = sum(share >= _GOLDEN_SHARE for share, _ in golden.values())

    log(f"  blob check: top cluster {big['concerns']} concerns / "
        f"{len(big['components'])} components -> {'KILL' if blob else 'ok'}")
    for name, (share, n) in golden.items():
        log(f"  golden {name}: {share:.0%} of its {n} concerns in one cluster")
    verdict = (not blob) and n_gold >= 2
    log(f"  VERDICT: {'PASS' if verdict else 'KILL'} "
        f"(blob={'yes' if blob else 'no'}, golden {n_gold}/3)")
    return {"clusters": len(clusters), "features": len(feats), "sizes": dict(sizes),
            "content": len(shelved), "blob": blob, "golden": golden, "pass": verdict}
