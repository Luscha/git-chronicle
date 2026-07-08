"""Per-commit enrichment with no LLM cost: language guess, conventional-commit
parse, and path->component taxonomy (down-weighting vendored/translation dirs)."""

from __future__ import annotations

import re
from collections import Counter

_CC_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s")

# Small stopword sets; language is a best-effort signal, not a hard dependency.
_IT_WORDS = {
    "di", "che", "non", "per", "con", "una", "del", "della", "come", "più",
    "sono", "alla", "dei", "delle", "nel", "nella", "gli", "questo", "anche",
    "aggiunto", "aggiunta", "rimozione", "rimosso", "sistemato", "aggiustato",
    "aggiustamento", "modificato", "migliorato", "correzione", "nuovo", "nuova",
    "gestione", "manca", "mancante", "prima", "adesso", "ora", "senza", "sulla",
}
_EN_WORDS = {
    "the", "and", "for", "with", "added", "remove", "removed", "update",
    "updated", "better", "implement", "implemented", "make", "made", "support",
    "when", "into", "from", "this", "that", "should", "before", "after", "now",
    "missing", "management", "handling",
}
_WORD_RE = re.compile(r"[a-zàèéìòù]+", re.IGNORECASE)


def guess_lang(text: str) -> str:
    toks = [t.lower() for t in _WORD_RE.findall(text or "")]
    it = sum(1 for t in toks if t in _IT_WORDS)
    en = sum(1 for t in toks if t in _EN_WORDS)
    if it == 0 and en == 0:
        return "unknown"
    return "it" if it > en else "en"


def parse_conventional(subject: str) -> tuple[bool, str | None, str | None]:
    m = _CC_RE.match(subject or "")
    if not m:
        return False, None, None
    return True, m.group("type").lower(), (m.group("scope") or None)


_KIND_MAP = {
    "feat": "feat", "feature": "feat",
    "fix": "fix", "bugfix": "fix", "hotfix": "fix", "bug": "fix",
    "refactor": "refactor", "refactoring": "refactor", "perf": "refactor", "performance": "refactor",
    "chore": "chore", "build": "chore", "ci": "chore", "test": "chore", "tests": "chore",
    "style": "chore", "deps": "chore", "bump": "chore",
    "docs": "docs", "doc": "docs",
}


def work_kind(cc_type: str | None, subject: str) -> str:
    """Best-effort work-kind for a commit. Untrusted: from cc-type if present, else
    a light subject heuristic, else 'other'. Kind lives on the commit, not the domain."""
    if cc_type and cc_type in _KIND_MAP:
        return _KIND_MAP[cc_type]
    s = (subject or "").lower()
    if any(w in s for w in ("fix", "bug", "crash", "hotfix", "segfault", "error")):
        return "fix"
    if any(w in s for w in ("refactor", "cleanup", "rework", "optimi")):
        return "refactor"
    if any(w in s for w in ("implement", "introduce", "feat", "add ", "new ")):
        return "feat"
    if any(w in s for w in ("readme", "wiki", "docs", "documentation")):
        return "docs"
    if any(w in s for w in ("bump", "version", "chore", "merge")):
        return "chore"
    return "other"


def component_key(path: str) -> str:
    """Top-level path segment; the coarse 'where in the tree' bucket."""
    if "/" not in path:
        return "(root)"
    return path.split("/", 1)[0]


# Generic vendored/generated markers — apply to ANY repo/language, no per-repo config.
_VENDORED_SEGMENTS = {
    "node_modules", "vendor", "vendored", "third_party", "thirdparty", "dist", "build",
    "target", "out", "__pycache__", "generated", "coverage", ".venv", "venv", ".git",
    "locale", "locales", "po", "i18n", "translations",
}
_VENDORED_SUFFIX = (".min.js", ".min.css", ".lock", ".map", "_pb2.py", ".pb.go")


def is_vendored(path: str) -> bool:
    """True for generated/vendored/translation files that carry no design signal."""
    p = path.lower()
    if set(p.split("/")) & _VENDORED_SEGMENTS:
        return True
    return p.endswith(_VENDORED_SUFFIX)


def enrich(conn, log=print) -> dict:
    """Populate components, commit_files.component_id, and commit language/cc/component."""
    # 1. Build the component table from all distinct top-level path segments.
    keys = {component_key(r["path"])
            for r in conn.execute("SELECT DISTINCT path FROM commit_files")}
    for k in sorted(keys):
        conn.execute(
            "INSERT OR IGNORE INTO components (key, name, is_noise) VALUES (?,?,?)",
            (k, k, int(is_vendored(k + "/x"))),
        )
    conn.commit()
    comp_id = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM components")}
    comp_noise = {r["key"]: bool(r["is_noise"]) for r in conn.execute("SELECT key, is_noise FROM components")}

    # 2. Assign component_id to each file row (only where unset -> resumable).
    updates = []
    for r in conn.execute("SELECT id, path FROM commit_files WHERE component_id IS NULL"):
        updates.append((comp_id[component_key(r["path"])], r["id"]))
    conn.executemany("UPDATE commit_files SET component_id=? WHERE id=?", updates)
    conn.commit()

    # 3. Per-commit language, conventional-commit fields, and primary component.
    rows = conn.execute(
        "SELECT hash, subject, body FROM commits WHERE lang IS NULL"
    ).fetchall()
    n = 0
    for row in rows:
        lang = guess_lang(f"{row['subject']} {row['body'] or ''}")
        is_cc, cc_type, cc_scope = parse_conventional(row["subject"])
        kind = work_kind(cc_type, row["subject"])
        paths = [fr["path"] for fr in conn.execute(
            "SELECT path FROM commit_files WHERE commit_hash=?", (row["hash"],))]
        primary = _primary_component(paths, comp_noise)
        conn.execute(
            "UPDATE commits SET lang=?, is_conventional=?, cc_type=?, cc_scope=?, kind=?, "
            "component=? WHERE hash=?",
            (lang, int(is_cc), cc_type, cc_scope, kind, primary, row["hash"]),
        )
        n += 1
    conn.commit()
    log(f"  components: {len(keys)} ({sum(comp_noise.values())} noise); enriched {n} commits")
    return {"components": len(keys), "enriched": n}


def _primary_component(paths: list[str], comp_noise: dict[str, bool]) -> str | None:
    if not paths:
        return None
    counts = Counter(component_key(p) for p in paths)
    non_noise = Counter({k: v for k, v in counts.items() if not comp_noise.get(k, False)})
    chosen = non_noise or counts
    return chosen.most_common(1)[0][0]
