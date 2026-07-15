"""IDENTITY-CARD REFINEMENT — history gets a voice on what a feature is CALLED.

The peek names a feature from one file head: mechanics, not purpose ('User Interface
Utilities' for the Sound Arranger tool). The feature's attributed commits state intent
in the maintainer's own vocabulary — already untangled and normalized in the concerns
table, so this pass mines nothing new.

Strictly text-only: territory, membership, classification and coined identifiers are
untouchable (v2's rename-on-accretion died of identity drift; with territory frozen
before history speaks, that failure is structurally impossible). Old names are kept as
searchable aliases.
"""

from __future__ import annotations

import re

REFINE_SYS = (
    "You refine the catalog entries of ONE software project's feature register. For each "
    "numbered feature you get its current code-derived name and definition, sample file "
    "names, and its CHANGE HISTORY (what the maintainer's commits actually did to it). "
    "Rewrite the name at feature altitude — the term the maintainer's own history uses, "
    "keeping coined identifiers verbatim — and one concrete sentence of definition; add "
    "evolution context the history proves (e.g. 'migrated affects from C++ to YAML'). "
    "If the current name is already what the history calls it, return it unchanged. "
    'Respond JSON: {"features":{"<n>":{"name":"...","definition":"..."}}}'
)

_KEY_SUFFIX_RE = re.compile(r"\(([A-Za-z0-9_]{4,})\)\s*$")


def refine_descriptions(conn, provider, log=print, min_commits: int = 3,
                        batch: int = 10) -> dict:
    rows = conn.execute(
        "SELECT id, name, definition FROM domains "
        "WHERE status IN ('named','provisional') AND locked=0 "
        "AND classification IN ('feature','core') ORDER BY id").fetchall()
    feats = []
    for r in rows:
        cons = conn.execute(
            "SELECT label, summary FROM concerns WHERE domain_id=? AND label IS NOT NULL "
            "AND (origin IS NULL OR origin NOT IN ('import','import-misc')) "
            "ORDER BY id LIMIT 12", (r["id"],)).fetchall()
        if len(cons) < min_commits:
            continue
        files = [x["path"].rsplit("/", 1)[-1] for x in conn.execute(
            "SELECT path FROM domain_files WHERE domain_id=? AND source='register' "
            "ORDER BY path LIMIT 5", (r["id"],))]
        feats.append((r, cons, files))
    log(f"  refining {len(feats)} features with >= {min_commits} attributed commits ...")
    renamed = redefined = 0
    for i in range(0, len(feats), batch):
        chunk = feats[i:i + batch]
        listing = "\n".join(
            f"[{j}] name: {r['name']}\n"
            f"    def: {(r['definition'] or '')[:110]}\n"
            f"    files: {', '.join(files)}\n"
            f"    history: " + "; ".join(
                (c["label"] or "")[:70] + (f" ({(c['summary'] or '')[:60]})"
                                           if c["summary"] else "")
                for c in cons[:8])[:600]
            for j, (r, cons, files) in enumerate(chunk))
        try:
            out = provider.chat(REFINE_SYS, listing, want_json=True,
                                cache_extra=f"refine:{i}")
        except Exception as exc:  # noqa: BLE001
            log(f"    refine batch {i} failed ({exc})")
            continue
        got = out.get("features", {}) if isinstance(out, dict) else {}
        for j, (r, cons, files) in enumerate(chunk):
            g = got.get(str(j)) or {}
            name = str(g.get("name") or "").strip()[:70]
            dfn = str(g.get("definition") or "").strip()[:400]
            if not name:
                continue
            # a coined identifier on the old name survives every rename
            m = _KEY_SUFFIX_RE.search(r["name"])
            if m and m.group(1).lower() not in name.lower():
                name = f"{name} ({m.group(1)})"[:70]
            if name != r["name"]:
                conn.execute("INSERT OR IGNORE INTO domain_aliases (domain_id, alias) "
                             "VALUES (?,?)", (r["id"], r["name"]))
                conn.execute("UPDATE domains SET name=? WHERE id=?", (name, r["id"]))
                renamed += 1
            if dfn and dfn != (r["definition"] or ""):
                conn.execute("UPDATE domains SET definition=? WHERE id=?", (dfn, r["id"]))
                redefined += 1
    conn.commit()
    log(f"  identity refinement: {renamed} renamed, {redefined} redefined "
        f"(old names kept as aliases)")
    return {"renamed": renamed, "redefined": redefined}
