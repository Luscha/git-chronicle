"""TIERS — which entries are foundations, which are frameworks, which are features.

The question this answers is the one the whole tool exists for: *of everything I built,
what did the rest get built ON?* Three attempts produced a flat alphabetical list in which
the scripting bridge that every system registers against sat between two quest scripts.

A tier is a flat ATTRIBUTE, never a hierarchy. Filing entries into a tree would force a
single parent on exactly the things that have none -- luna's territory spans eleven
components, and any tree puts it under one of them and hides it from the other ten. So
entries stay peers and carry a tier, and the graph layers on that.

The tier is read off evidence already computed, in this order:

  foundation  many other entries import it, across several components, over years. The
              conjunction matters: fan-in alone promotes a wiki toolchain used 27 times
              inside one component; a long life alone promotes every quest file that was
              never deleted.
  framework   used by others, or owning a directory tree that recurs across components
              (the anchors signal: a thing with its own tree is a thing you built ON).
  content     the proto/locale shipment stream, already shelved upstream.
  tooling     lives in the build/dev tree rather than the product.
  feature     everything else, which is most of it and should be.

Every verdict is a default. `tier "X" = framework` in the ledger outranks it permanently,
and `tier_from='ledger'` keeps later auto-runs from arguing.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from .territory import name_tokens

_FOUNDATION_FANIN = 5      # imported by this many other entries ...
_FOUNDATION_COMPS = 3      # ... from this many components ...
_FOUNDATION_YEARS = 4      # ... for this long. All three, or it is not a foundation.
_FRAMEWORK_FANIN = 2
_FRAMEWORK_COMPS = 2       # a directory tree recurring across this many components
_TOOL_ROOTS = ("tools", "build", "scripts", "devops", ".devops", "dev")


def _identity(row) -> tuple[str, str | None]:
    """(name, coined stem) for an entry, tolerating a missing row."""
    if not row:
        return "", None
    name, stems = row
    parsed = json.loads(stems or "[]")
    return name or "", (parsed[0] if parsed else None)


def _years(born: str | None, last: str | None) -> float:
    try:
        return (int((last or "")[:4]) - int((born or "")[:4]))
    except ValueError:
        return 0.0


def assign_tiers(conn, ledger=None, log=print) -> dict:
    """Write domains.tier. Returns a census. Deterministic, no LLM, no git."""
    fan: Counter = Counter()
    fan_comps: dict[int, set] = defaultdict(set)
    terr: dict[int, set] = defaultdict(set)
    for r in conn.execute("SELECT domain_id, path FROM domain_files"):
        terr[r["domain_id"]].add(r["path"])
    for r in conn.execute("SELECT src_domain, dst_domain FROM domain_edges"):
        fan[r["dst_domain"]] += 1
        for p in terr.get(r["src_domain"], ()):
            fan_comps[r["dst_domain"]].add(p.split("/", 1)[0])

    # A framework has a tree of ITS OWN, recurring across components: luna/ under
    # Server, Client-Files and the repo root. The segment must carry the entry's own
    # identifier — counting any shared segment promotes everything, because 'ue' and
    # 'quest' appear under every component in this corpus.
    names = {r["id"]: (r["name"], r["stems"]) for r in
             conn.execute("SELECT id, name, stems FROM domains")}
    own_tree: dict[int, int] = {}
    for did, paths in terr.items():
        ident = name_tokens(*_identity(names.get(did)))
        if not ident:
            own_tree[did] = 0
            continue
        segs: dict[str, set] = defaultdict(set)
        for p in paths:
            parts = p.split("/")
            for s in parts[1:-1]:
                if s.lower() in ident:
                    segs[s.lower()].add(parts[0])
        own_tree[did] = max((len(v) for v in segs.values()), default=0)

    forced = (ledger.tiers() if ledger is not None else {}) or {}
    census: Counter = Counter()
    rows = conn.execute(
        "SELECT id, name, classification, born_at, last_seen FROM domains").fetchall()
    for r in rows:
        did = r["id"]
        if r["name"] in forced:
            tier, src = forced[r["name"]], "ledger"
        else:
            tier, src = _auto(r, fan[did], len(fan_comps.get(did, ())),
                              own_tree.get(did, 0), terr.get(did, set())), "auto"
        conn.execute("UPDATE domains SET tier=?, tier_from=? WHERE id=?", (tier, src, did))
        census[tier] += 1
    conn.commit()

    log("  tiers: " + ", ".join(f"{t} {n}" for t, n in census.most_common()))
    return dict(census)


def _auto(row, fanin: int, fan_comps: int, tree_comps: int, files: set) -> str:
    cls = row["classification"] or ""
    if cls == "content":
        return "content"
    if cls == "inherited":
        return "content"
    if files and all(p.split("/", 1)[0].lower() in _TOOL_ROOTS for p in files):
        return "tooling"
    if (fanin >= _FOUNDATION_FANIN and fan_comps >= _FOUNDATION_COMPS
            and _years(row["born_at"], row["last_seen"]) >= _FOUNDATION_YEARS):
        return "foundation"
    if fanin >= _FRAMEWORK_FANIN or tree_comps >= _FRAMEWORK_COMPS:
        return "framework"
    return "feature"
