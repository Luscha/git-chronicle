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
from .ledger import Ledger
from .register import _SRC_EXTS
from .territory import build_territory


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
_GOLDEN_MIN = 20       # a probe smaller than this is noise, not a gate
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
    # identity containment is judged over FILE populations, never concern sets:
    # two stems are one family when they mark the same files (luna / luna forge),
    # not when they ride the same commits — a client feature rides its framework's
    # commits ('player virtual' concern-containment in forge measured 0.75 and was
    # wrongly absorbed; its file-containment is 0.08: different files, own family)
    fpop = {s: {f for f in stem_files[s] if auth[f] == "authored"} for s in seeds}
    order = sorted(seeds, key=lambda s: (-len(seeds[s]), s))
    pos = {s: k for k, s in enumerate(order)}
    sdsu = _DSU()
    for small in sorted(seeds, key=lambda s: (len(fpop[s]), s)):
        cs = fpop[small]
        if not cs:
            continue
        best, share = None, 0.0
        for big in order:
            if big == small or len(fpop[big]) <= len(cs):
                continue
            ov = len(cs & fpop[big]) / len(cs)
            if ov > share:
                best, share = big, ov
        if best is not None and share >= _SEED_CONTAIN:
            sdsu.union(pos[best], pos[small])
    fam_of: dict[str, str] = {s: order[sdsu.find(pos[s])] for s in order}

    # one concern, one cluster: dominant family wins. Votes are per-FILE stem hits:
    # a coined BASENAME BIGRAM is the file's own compound name and outranks all
    # (player_virtual_manager.cpp IS player-virtual wherever it lives); a stem
    # carried by the file's DIRECTORY outranks single basename tokens (v0.2's
    # claim rule: bind_arena.cpp inside luna/ is luna territory, and 'bind arena'
    # is no coined family — the 'arena' token must not steal the bridge).
    assign: dict[int, str] = {}
    for i, c in enumerate(work):
        votes: Counter = Counter()
        for f in c["files"]:
            if auth[f] != "authored":
                continue
            dstems = _seg_stems(f)
            btoks = _tokens(f.rsplit("/", 1)[-1].rsplit(".", 1)[0])
            bigrams = {f"{a} {b}" for a, b in zip(btoks, btoks[1:])}
            for s in _lineage_stems(f):
                if s in seeds:
                    votes[fam_of[s]] += 4 if s in bigrams else 3 if s in dstems else 1
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

    # The golden probes are a SAMPLE, and a small one: augments carries under a dozen
    # concerns, so a handful of newly untangled commits swings its share by forty points.
    # Probes below _GOLDEN_MIN are reported and not counted -- a gate that flips on 0.6%
    # more evidence measures noise, and this one blocked a scheduled rebuild doing it.
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
    scored = {n: v for n, v in golden.items() if v[1] >= _GOLDEN_MIN}
    n_gold = sum(share >= _GOLDEN_SHARE for share, _ in scored.values())

    log(f"  blob check: top cluster {big['concerns']} concerns / "
        f"{len(big['components'])} components -> {'KILL' if blob else 'ok'}")
    for name, (share, n) in golden.items():
        log(f"  golden {name}: {share:.0%} of its {n} concerns in one cluster"
            + ("" if n >= _GOLDEN_MIN else f"  (sample < {_GOLDEN_MIN}, not scored)"))
    verdict = (not blob) and n_gold >= min(2, len(scored))
    log(f"  VERDICT: {'PASS' if verdict else 'KILL'} "
        f"(blob={'yes' if blob else 'no'}, golden {n_gold}/{len(scored)} scored)")
    return {"clusters": len(clusters), "features": len(feats), "sizes": dict(sizes),
            "content": len(shelved), "blob": blob, "golden": golden, "pass": verdict}


# ---- emit: name the clusters, write the v0.3 register to a fresh DB -----------

_NAME_SYS = (
    'You name features of a software repository for its knowledge base.\n'
    'Given work-log labels (with counts) and file paths that all belong to ONE '
    'feature or system, return JSON: {"name": ..., "definition": ...}.\n'
    "Rules:\n"
    "- name: 2-5 words, Title Case. If a coined key is given, keep it verbatim.\n"
    "- definition: ONE sentence, present tense, saying what the system IS. Never "
    "use change words (added/fixed/updated/refactored/removed).\n"
    "- Derive only from the evidence given; do not invent scope.")


def _cluster_prompt(cl: dict, work: list[dict]) -> str:
    lab = "; ".join(f"{n}x {l}" for l, n in cl["labels"].most_common(8))
    deg: Counter = Counter()
    for i in cl["idxs"]:
        for f in work[i]["files"]:
            deg[f] += 1
    paths = "\n".join(f"  {f}" for f, _ in deg.most_common(12))
    parts = [f"active: {cl['born']}..{cl['last']}, {cl['commits']} commits, "
             f"{len(cl['files'])} files"]
    if cl["seed"]:
        parts.append(f"coined key: {cl['seed']}")
    if cl["dead"]:
        parts.append("NOTE: all files deleted from the repository (a removed feature)")
    if cl["content"]:
        parts.append("NOTE: this is a data/content stream, not program code")
    parts.append(f"work labels: {lab}")
    parts.append(f"representative paths:\n{paths}")
    return "\n".join(parts)


def emit_register(conn, repo: str, res: dict, out_db: str, provider, log=print) -> dict:
    from pathlib import Path

    from ..storage.schema import connect as db_connect, init_db

    import os

    src_path = conn.execute("PRAGMA database_list").fetchone()[2]
    # Build beside the target and swap at the end. Deleting it in place broke anything
    # holding it open — the studio serves this very file — and a run that failed halfway
    # left a 4KB stub where the knowledge base used to be.
    out_path = Path(out_db)
    tmp_path = out_path.with_suffix(out_path.suffix + ".building")
    for stale in (tmp_path, Path(str(tmp_path) + "-wal"), Path(str(tmp_path) + "-shm")):
        if stale.exists():
            stale.unlink()
    out = db_connect(str(tmp_path))
    init_db(out)
    out.execute("ATTACH ? AS s", (src_path,))
    out.execute("PRAGMA foreign_keys=OFF")   # bulk copy; source rows are consistent
    for t in ("commits", "commit_files", "commit_branches", "components", "eras"):
        out.execute(f"INSERT INTO {t} SELECT * FROM s.{t}")
    # concerns come over with the source run's domain assignments STRIPPED — this
    # register is the only authority on attribution in the emitted DB
    out.execute("INSERT INTO concerns (id, commit_hash, label, summary, files, kind, "
                "origin) SELECT id, commit_hash, label, summary, files, kind, origin "
                "FROM s.concerns")
    out.commit()
    out.execute("PRAGMA foreign_keys=ON")

    work, alive = res["work"], res["alive"]
    feats = [cl for cl in res["clusters"]
             if cl["concerns"] >= _MIN_CONCERNS and cl["commits"] >= _MIN_COMMITS]

    # territory is CONTESTED: a file belongs to the ONE cluster whose concerns
    # touch it most (ties own nothing), and substrate/glue files — barred as
    # clustering evidence — are barred as territory too. The naive union
    # (every file any member concern touched) graded 56% ownership: luna's 700
    # files swallowed interfacemodule.py and pvp_arena_manager.cpp wholesale.
    barred = set(res["substrate"]) | set(res["glue"])
    file_claims: dict[str, Counter] = defaultdict(Counter)
    for ci, cl in enumerate(feats):
        for i in cl["idxs"]:
            for f in work[i]["files"]:
                if f in cl["files"] and res["root_of"].get(f) not in barred:
                    file_claims[f][ci] += 1
    territory: dict[int, dict] = defaultdict(dict)
    for f, claims in file_claims.items():
        top = claims.most_common(2)
        if len(top) > 1 and top[0][1] == top[1][1]:
            continue                        # contested file: nobody's territory
        territory[top[0][0]][f] = top[0][1]

    log(f"  naming {len(feats)} clusters (LLM, cached)")
    n_llm = 0
    named: dict[int, tuple[str, str]] = {}
    for ci, cl in enumerate(feats):
        try:
            j = provider.chat(_NAME_SYS, _cluster_prompt(cl, work), want_json=True)
            name = str(j.get("name") or "").strip()[:80]
            definition = str(j.get("definition") or "").strip()[:400]
        except Exception as e:              # a failed name never blocks the register
            name, definition = "", f"(naming failed: {e})"
        if not name:
            name = (cl["seed"] or f"family {cl['idxs'][0]}").title()
        n_llm += 1
        named[ci] = (name, definition)

    # Territory needs the NAMES (an entry claims files carrying its own identifiers), so
    # it is settled after naming and before anything is written: concern-derived evidence
    # in union with the worktree name-claim, then the ledger's rules last so a human
    # verdict always outranks both. Measured: median territory 6 -> 13 files, entries
    # with none 15 -> 6.
    authored = sorted(f for f, v in res["auth"].items() if v == "authored" and alive(f))
    entries = {ci: {"name": named[ci][0], "seed": feats[ci]["seed"]} for ci in named}
    weights = {ci: territory.get(ci, {}) for ci in named}
    led = Ledger.load()
    worktree = [f for f in run_git(repo, ["ls-files"]).splitlines() if f.strip()]
    final, _, declared = build_territory(
        entries, {ci: set(w) for ci, w in weights.items()}, authored,
        ledger=led, worktree=worktree, log=log)
    tiers = led.tiers()
    notes = led.notes()

    retired = {s for s, _ in led.merges} | set(led.tombstones)
    for ci, cl in enumerate(feats):
        name, definition = named[ci]
        if name in retired:
            continue          # the ledger merged or tombstoned it; no row, no dossier
        klass = "content" if cl["content"] else "feature"
        stems = json.dumps(([cl["seed"]] if cl["seed"] else [])[:8])
        cur = out.execute(
            "INSERT INTO domains (name, definition, summary, stems, named_from, "
            "classification, status, lifecycle, removed_at, born_at, first_seen, "
            "last_seen, n_commits, n_files, created_by) "
            "VALUES (?,?,?,?,?,?,'named',?,?,?,?,?,?,?, 'lineage')",
            (name, definition, definition, stems, cl["concerns"], klass,
             "removed" if cl["dead"] else "active",
             cl["last"] if cl["dead"] else None,
             cl["born"], cl["born"], cl["last"], cl["commits"], len(cl["files"])))
        did = cur.lastrowid
        # weight is concern-degree where we have it; a name-claimed file has no concern
        # evidence behind it, so it sits at the bottom of the ordering rather than
        # pretending to a centrality nothing measured
        w = weights.get(ci, {})
        out.executemany(
            "INSERT OR IGNORE INTO domain_files (domain_id, path, weight, source) "
            "VALUES (?,?,?,?)",
            [(did, f, float(w.get(f, 0)), "register" if alive(f) else "history")
             for f in sorted(final.get(ci, ()), key=lambda p: (-w.get(p, 0), p))])
        cw: Counter = Counter()
        for i in cl["idxs"]:
            cw[work[i]["commit"]] += 1
        out.executemany(
            "INSERT OR IGNORE INTO commit_domains (commit_hash, domain_id, weight, "
            "source) VALUES (?,?,?,'lineage')",
            [(h, did, float(n)) for h, n in sorted(cw.items())])
        # each concern remembers its cluster: downstream stages (chronicle) narrate
        # from the per-domain slice of a commit, not its whole multi-feature subject
        out.executemany("UPDATE concerns SET domain_id=? WHERE id=?",
                        [(did, work[i]["id"]) for i in cl["idxs"]])

    # the inherited baseline: maintenance on vanilla files, catalogued per component
    comp_cons: dict[str, list[dict]] = defaultdict(list)
    for c in res["base"]:
        comp = Counter(f.split("/", 1)[0] for f in c["files"]).most_common(1)[0][0]
        comp_cons[comp].append(c)
    for comp, cs in sorted(comp_cons.items()):
        if len(cs) < _MIN_CONCERNS:
            continue
        cur = out.execute(
            "INSERT INTO domains (name, definition, classification, status, "
            "n_commits, created_by) VALUES (?,?, 'inherited', 'named', ?, 'lineage')",
            (f"Inherited baseline — {comp}",
             f"Maintenance and fixes on inherited (pre-fork) code under {comp}/.",
             len({c['commit'] for c in cs})))
        did = cur.lastrowid
        cw = Counter(c["commit"] for c in cs)
        out.executemany(
            "INSERT OR IGNORE INTO commit_domains (commit_hash, domain_id, weight, "
            "source) VALUES (?,?,?,'baseline')",
            [(h, did, float(n)) for h, n in sorted(cw.items())])
        out.executemany("UPDATE concerns SET domain_id=? WHERE id=?",
                        [(did, c["id"]) for c in cs])
    out.commit()

    # entries the ledger declares outright: they own territory and carry the human's own
    # words, but have no cluster, so their history comes from whoever touched their files
    for name, files in sorted(declared.items()):
        if not files:
            continue
        rows = out.execute(
            "SELECT MIN(c.authored_at), MAX(c.authored_at), COUNT(DISTINCT c.hash) "
            "FROM commit_files cf JOIN commits c ON c.hash = cf.commit_hash "
            f"WHERE cf.path IN ({','.join('?' * len(files))}) AND c.is_merge = 0",
            tuple(sorted(files))).fetchone()
        born, last, ncom = rows[0] or "", rows[1] or "", rows[2] or 0
        cur = out.execute(
            "INSERT INTO domains (name, definition, summary, classification, status, "
            "lifecycle, tier, tier_from, born_at, first_seen, last_seen, n_commits, "
            "n_files, created_by) VALUES (?,?,?,?, 'named', 'active', ?, 'ledger', "
            "?,?,?,?,?, 'ledger')",
            (name, notes.get(name, ""), notes.get(name, ""),
             tiers.get(name, "feature"), tiers.get(name), born[:10], born[:10], last[:10],
             ncom, len(files)))
        did = cur.lastrowid
        out.executemany(
            "INSERT OR IGNORE INTO domain_files (domain_id, path, weight, source) "
            "VALUES (?,?,0.0,'register')", [(did, f) for f in sorted(files)])
        out.executemany(
            "INSERT OR IGNORE INTO commit_domains (commit_hash, domain_id, weight, source) "
            "VALUES (?,?,1.0,'ledger')",
            [(r[0], did) for r in out.execute(
                "SELECT DISTINCT cf.commit_hash FROM commit_files cf JOIN commits c "
                f"ON c.hash = cf.commit_hash WHERE cf.path IN ({','.join('?' * len(files))}) "
                "AND c.is_merge = 0", tuple(sorted(files)))])
    out.commit()

    residue = sum(1 for cl in res["clusters"]
                  if cl["concerns"] < _MIN_CONCERNS or cl["commits"] < _MIN_COMMITS)
    n_dom = out.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
    n_att = out.execute("SELECT COUNT(DISTINCT commit_hash) FROM commit_domains").fetchone()[0]
    log(f"  {n_dom} domains ({n_llm} named), {n_att} distinct commits attributed; "
        f"{residue} residue micro-clusters left unattributed")
    out.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # fold the WAL in before the swap
    out.close()
    for suffix in ("-wal", "-shm"):
        stale = Path(str(tmp_path) + suffix)
        if stale.exists():
            stale.unlink()
    os.replace(tmp_path, out_path)                   # atomic: readers see old or new
    for suffix in ("-wal", "-shm"):
        stale = Path(str(out_path) + suffix)
        if stale.exists():
            stale.unlink()
    return {"domains": n_dom, "attributed": n_att, "residue": residue}
