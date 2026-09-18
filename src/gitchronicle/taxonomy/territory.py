"""TERRITORY — which files an entry owns, from two kinds of evidence plus the ledger.

Measured, not assumed (P0.5, on the 35,813-file worktree of a 12-year fork):

  concern-derived alone   median  6 files, 15 of 185 entries with NO territory
  name-claimed alone      median  5 files, 63 with none  (only entries whose names
                                                          appear in paths get anything)
  UNION                   median 13 files,  6 with none, largest entry 331

so the two are complementary and the union is the configuration to ship. Battle Pass goes
from 3 files to 17 -- the ground truth is the 15 files carrying its name across protobuf,
client UI, quest scripts and tooling -- while Luna stays one bounded entry at 287.

The name-claim comes from ``anchors.claim_territory``, with one inversion that matters.
anchors scores a DIRECTORY match above a FILENAME match, which is right for the job it was
written for: a framework owns its own tree, so luna/forge_game.cpp is Luna's rather than
forge's. Applied to every entry that rule lets containers eat features --
luna/protobuf/ue/battlepass_pb2.py went to *protobuf* and battlepass_manager.py to
*universalcore*. Inverting it takes Battle Pass from 5 of its 15 files to 15 of 15. The
directory still decides when no filename carries an identity.

What neither rule can settle is left to the ledger on purpose. Server/game/src/luna/
bind_arena.cpp is Luna's binding FOR Arena; bind_raid.cpp and event_bus_db.cpp are the
same shape, and Luna's tree holds 113 core files beside 115 per-feature protobuf schemas.
No heuristic decides that correctly for everyone, so the default ships and one ledger line
overrides it.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .anchors import _tokens as _path_tokens
from .ground import _STOP

_MIN_TOKEN = 4          # anchors' own floor: shorter tokens are not identities

# Structural vocabulary: words that describe the SHAPE of a thing, never which thing it
# is. Every codebase is full of tabs, panels and tables, so a claim on one of these reaches
# across the whole repo — 'tabs' pulled iptables.rules, Lua's ltable.c and the backoffice's
# DataTable.vue into a wiki-builder entry. ground._STOP covers the same idea for
# clustering; these are the container nouns that only matter once entries claim territory.
_GENERIC = {
    "tab", "tabs", "table", "tables", "panel", "panels", "dialog", "dialogs",
    "widget", "widgets", "page", "pages", "button", "buttons", "view", "views",
    "component", "components", "element", "elements", "container", "containers",
    "wrapper", "layout", "screen", "screens", "entry", "entries", "record", "records",
    "value", "values", "field", "fields", "name", "names", "text", "label", "labels",
}


def name_tokens(name: str, seed: str | None = None) -> set[str]:
    """The identifiers an entry claims on: the content words of its name plus its coined
    stem. Generic words go — 'Battlepass System' must claim on 'battlepass', not on
    'system', or every *System* entry would fight over the same files."""
    toks = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= _MIN_TOKEN}
    toks -= _STOP | _GENERIC
    if seed and len(seed) >= _MIN_TOKEN and seed not in _GENERIC:
        toks.add(seed)
    return toks


def claim_by_name(tokens: set[str], files: list[str]) -> dict[str, list[str]]:
    """Assign each file to the ONE token that names it most specifically.

    Filename beats directory (see the module docstring); exact beats morphological;
    longer tokens beat shorter, and ties break lexicographically so the result does not
    depend on iteration order.
    """
    def _fuzzy(t: str, toks: set) -> bool:
        """Morphology and packaging affixes only — never bare substring containment.

        Substring matching was tried and is actively harmful: 'tabs' reaches 'tab', which
        is *inside* iptables, ltable and DataTable, so one UI entry claimed the firewall
        rules, Lua's hash table and the backoffice. A claim token must BE the path token,
        its singular/plural, or that token minus a packaging prefix (libluna -> luna).
        """
        base = t[:-1] if t.endswith("s") else t      # 'augments' claims uiAugmentChoice
        for x in toks:
            if x in (t, base) or x in (t + "s", base + "s"):
                return True
            for pre in ("lib", "py", "ui"):          # packaging, not identity
                if x.startswith(pre) and x[len(pre):] in (t, base):
                    return True
        return False

    claims: dict[str, list[str]] = defaultdict(list)
    for f in files:
        segs = f.lower().split("/")
        dtoks: set = set()
        for seg in segs[:-1]:
            dtoks |= _path_tokens(seg)
        # the EXTENSION is the language, never the owner: eve_manager.forge is an eve
        # system written IN forge, not part of the forge framework
        ftoks = _path_tokens(segs[-1].rsplit(".", 1)[0])
        best = None
        for t in tokens:
            if t in ftoks:
                score = (4, len(t))
            elif _fuzzy(t, ftoks):
                score = (3, len(t))
            elif t in dtoks:
                score = (2, len(t))
            elif _fuzzy(t, dtoks):
                score = (1, len(t))
            else:
                continue
            if best is None or score > best[0] or (score == best[0] and t < best[1]):
                best = (score, t)
        if best:
            claims[best[1]].append(f)
    return {t: sorted(fs) for t, fs in claims.items()}


def build_territory(entries: dict[int, dict], concern_territory: dict[int, set],
                    authored: list[str], ledger=None, worktree=None,
                    log=print) -> tuple[dict, dict, dict]:
    """entry id -> owned files. ``entries`` carries at least ``name`` and ``seed``.

    Name-claiming draws only on ``authored`` files, because it is a heuristic and a
    heuristic should stay inside the fork's own code. Explicit ledger rules draw on the
    whole ``worktree``: a written claim is a decision, and the authorship pass is not
    entitled to overrule it — it marked wiki_manager's own app.py and main.py inherited,
    which would have silently cut 27 files out of a tool the owner had named.

    Returns (territory, report, ledger-declared entries).
    """
    owners: dict[str, set] = defaultdict(set)
    for eid, e in entries.items():
        for t in name_tokens(e["name"], e.get("seed")):
            owners[t].add(eid)

    claims = claim_by_name(set(owners), authored)
    named: dict[int, set] = defaultdict(set)
    contested = 0
    for t, files in claims.items():
        if len(owners[t]) != 1:
            # a token several entries answer to identifies none of them: 'proto' is
            # claimed by five entries here, 'wiki' by five more
            contested += len(files)
            continue
        named[next(iter(owners[t]))] |= set(files)

    terr = {eid: set(concern_territory.get(eid, ())) | named.get(eid, set())
            for eid in entries}
    rep = {"concern": sum(len(v) for v in concern_territory.values()),
           "named": sum(len(v) for v in named.values()),
           "contested_token": contested,
           "union": sum(len(v) for v in terr.values())}

    declared: dict[str, set] = {}
    if ledger is not None and ledger.exists():
        by_name = {e["name"]: eid for eid, e in entries.items()}
        cat = {e["name"]: terr[eid] for eid, e in entries.items()}
        placed = {f for v in terr.values() for f in v}
        pool = worktree if worktree is not None else authored
        cat[""] = {f for f in pool if f not in placed}
        cat, lrep = ledger.apply(cat)
        terr = {by_name[n]: fs for n, fs in cat.items() if n in by_name}
        # An entry the ledger DECLARES but the assembly never proposed is the whole point
        # of being able to write one: a 400-file tool the pipeline smeared across fifteen
        # game features has no cluster to attach to, so it needs a row of its own.
        declared = {n: fs for n, fs in cat.items() if n and n not in by_name}
        rep["ledger"] = lrep
        rep["declared"] = len(declared)

    log(f"  territory: {rep['concern']} concern-derived + {rep['named']} name-claimed "
        f"-> {sum(len(v) for v in terr.values())} owned "
        f"({contested} files on tokens shared by several entries)"
        + (f"; {len(declared)} entries declared by the ledger" if declared else ""))
    return terr, rep, declared
