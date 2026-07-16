"""ANCHORS — the owner's frameworks, discovered first and allowed to claim
cross-component territory by NAME-CARRYING evidence.

Bottom-up carving with locality guards can never assemble a cross-cutting framework
(LUNA lives in six components); v0.2 inverts: coined identities with global evidence
are seated FIRST and claim every authored file that carries their name, then the
residue carves locally under the v0.1 guards.

Anchor evidence (all deterministic, authored-delta only):
  - embedded-module registrations: Py_InitModule("luna", ...) coins an importable name
  - coined directories recurring across >= 2 components (luna/ under Client-Files,
    Server/game/src, Server/db/src ...)
  - Doc/<name>/ documentation trees
  - coined file prefixes recurring across >= 2 components (forge_*.­cpp + forge.sys.*.lua)

Claim rule (the guard IS the rule): an anchor owns an authored file iff the anchor
token appears as a path segment, a filename token, or the file registers/declares the
anchor's name. No semantic matching, no LLM.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..extract.git_ingest import BatchReader

_EMBED_RE = re.compile(
    r'(?:Py_InitModule[34]?|PyImport_AppendInittab|PyModule_Create2?)\s*\(\s*"(\w+)"')
_MIN_TOKEN = 4
_MIN_COMPONENTS = 2
_MAX_ANCHORS = 64


def _comp(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def _tokens(name: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= _MIN_TOKEN}


def discover_anchors(repo: str, authored: list[str], inherited: list[str],
                     log=print) -> dict[str, dict]:
    """token -> {evidence: [...], files: []} for coined framework identities."""
    cand: dict[str, set] = defaultdict(set)      # token -> evidence kinds
    tok_comps: dict[str, set] = defaultdict(set)

    # 1. embedded-module registrations in authored C/C++
    reader = BatchReader(repo)
    try:
        for f in authored:
            if f.rsplit(".", 1)[-1].lower() in ("c", "cc", "cpp", "cxx"):
                text = reader.read("HEAD", f, limit=60000) or ""
                for m in _EMBED_RE.finditer(text):
                    t = m.group(1).lower()
                    if len(t) >= _MIN_TOKEN:
                        cand[t].add("registration")
                        tok_comps[t].add(_comp(f))
    finally:
        reader.close()

    # 2. coined directory names recurring across components
    dir_comps: dict[str, set] = defaultdict(set)
    for f in authored:
        parts = f.lower().split("/")
        for seg in parts[1:-1]:
            for t in _tokens(seg):
                dir_comps[t].add(_comp(f))
    for t, comps in dir_comps.items():
        if len(comps) >= _MIN_COMPONENTS:
            cand[t].add("dirs")
            tok_comps[t] |= comps

    # 3. Doc/<name>/ trees (the repo names its own systems)
    for f in authored:
        parts = f.split("/")
        if len(parts) >= 3 and parts[0].lower() in ("doc", "docs"):
            for t in _tokens(parts[1]):
                cand[t].add("doctree")
                tok_comps[t].add(_comp(f))

    # 4. imported packages: a name that authored code imports from many files is a
    #    coined framework even if it lives in one directory and registers nothing in
    #    C (uchtml: pure python, `import uchtml` everywhere)
    from .imports import extract_import_refs
    imp_by_name: dict[str, int] = Counter()
    reader = BatchReader(repo)
    try:
        for f in authored:
            if f.rsplit(".", 1)[-1].lower() in ("py", "pyw", "lua"):
                text = reader.read("HEAD", f, limit=20000) or ""
                for ref in set(extract_import_refs(text)):
                    if len(ref) >= _MIN_TOKEN:
                        imp_by_name[ref] += 1
                        tok_comps[ref].add(_comp(f))
    finally:
        reader.close()
    for t, n in imp_by_name.items():
        if n >= 5:
            cand[t].add("imports")

    # 5. coined filename prefixes recurring across components
    pref_comps: dict[str, set] = defaultdict(set)
    for f in authored:
        base = f.rsplit("/", 1)[-1].lower()
        m = re.match(r"([a-z]{4,}?)[_.]", base)
        if m:
            pref_comps[m.group(1)].add(_comp(f))
    for t, comps in pref_comps.items():
        if len(comps) >= _MIN_COMPONENTS:
            cand[t].add("fileprefix")
            tok_comps[t] |= comps

    # candidacy needs >= 2 evidence kinds or a registration...
    anchors: dict[str, dict] = {}
    for t, ev in cand.items():
        if "registration" in ev or len(ev) >= 2:
            anchors[t] = {"evidence": sorted(ev), "components": sorted(tok_comps[t])}
    # ...but the decisive test is COINAGE: the owner's identities cannot exist in the
    # inherited vanilla corpus. 'quest', 'guild', 'server' pervade vanilla — structure,
    # not identity; 'luna', 'uchtml', 'forge' appear only in the authored delta.
    vanfiles = Counter()
    for f in inherited:
        d = f.rsplit("/", 1)[0]
        for t in _tokens(f.rsplit("/", 1)[-1]) | {x for seg in d.lower().split("/")
                                                  for x in _tokens(seg)}:
            vanfiles[t] += 1
    # a few vanilla files carrying the token is coincidence (a luna_park monster);
    # many is structure ('game', 'quest', 'guild') — counted in FILES because vanilla
    # Metin2 is flat (thousands of files in a handful of dirs)
    anchors = {t: a for t, a in anchors.items() if vanfiles.get(t, 0) <= 20}
    top = sorted(anchors, key=lambda t: (-len(anchors[t]["evidence"]),
                                         -len(anchors[t]["components"]), t))[:_MAX_ANCHORS]
    anchors = {t: anchors[t] for t in top}
    log(f"  anchors: {len(anchors)} coined identities "
        f"({sum(1 for a in anchors.values() if 'registration' in a['evidence'])} registered)")
    return anchors


def claim_territory(anchors: dict[str, dict], authored: list[str]) -> dict[str, list[str]]:
    """Each anchor claims authored files that CARRY ITS NAME. Compound tokens count
    (libluna, PythonLunaModule carry 'luna'). A file inside the framework's DIRECTORY
    belongs to it over a filename match (luna/forge_game.cpp is luna's before forge's);
    ties break by match strength, then token length, then lexicographic."""
    def _match(t: str, toks: set) -> bool:
        # singular/plural morphology: 'augments' claims uiAugmentChoice
        base = t[:-1] if t.endswith("s") else t
        return any(t in x or base in x for x in toks)

    claims: dict[str, list[str]] = defaultdict(list)
    for f in authored:
        low = f.lower()
        segs = low.split("/")
        dtoks = set()
        for seg in segs[:-1]:
            dtoks |= _tokens(seg)
        # the EXTENSION is the language, never the owner: eve_manager.forge is an eve
        # system written IN forge, not part of the forge framework
        fname = segs[-1].rsplit(".", 1)[0]
        ftoks = _tokens(fname)
        best = None
        for t in anchors:
            if t in dtoks:
                score = (4, len(t))
            elif _match(t, dtoks):
                score = (3, len(t))
            elif t in ftoks:
                score = (2, len(t))
            elif _match(t, ftoks):
                score = (1, len(t))
            else:
                continue
            if best is None or score > best[0] or (score == best[0] and t < best[1]):
                best = (score, t)
        if best:
            claims[best[1]].append(f)
    return {t: sorted(fs) for t, fs in claims.items() if len(fs) >= 2}
