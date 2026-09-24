"""GROUND — the repo-level evidence layer (catalog v3, phase 0).

Per-commit reading is myopic: feature identity lives in AGGREGATES a single diff never
shows — file-name stems repeated across years (uiAvatarBuilder.py + avatar_builder.proto),
and the repo's own prose (README/Doc design documents that literally name and define its
systems). A human newcomer reads the file tree and Doc/ first; this stage does the same.

Local and O(unique paths): the STEM CENSUS (name stems weighted by activity, god-stem
flags) and the DOC HARVEST (in-repo prose at HEAD). Both feed the register and the
health checks; the retired glossary-drafting funnel lived here and is gone.
"""



from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..extract.git_ingest import run_git

# source-code extensions: what makes a cluster code rather than data
_SRC_EXTS = {"c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx", "py", "pyw", "lua",
             "cs", "js", "mjs", "ts", "tsx", "jsx", "java", "go", "rs", "rb", "php"}

# generic path words that can never evidence a feature by themselves (kept as unigram
# stopwords only — they still appear inside bigrams like "guild war")
_STOP = {
    "src", "inc", "include", "lib", "libs", "bin", "build", "common", "core", "base",
    "util", "utils", "utility", "helper", "helpers", "misc", "main", "test", "tests",
    "data", "config", "cfg", "conf", "settings", "manager", "system", "module", "type",
    "types", "def", "defs", "impl", "old", "new", "tmp", "temp", "root", "the", "and",
    "for", "with", "index", "info", "list", "item2", "file", "files",
    "client", "server", "game", "ui", "gui", "window", "python", "cpp", "header",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokens(name: str) -> list[str]:
    """File/dir name -> lowercase word tokens (camelCase, snake, kebab, digits split)."""
    name = _CAMEL.sub(" ", name)
    return [t for t in re.split(r"[^a-zA-Z]+", name.lower()) if len(t) >= 3]


def path_stems(path: str) -> set[str]:
    """The stems one path contributes: unigrams + adjacent bigrams from its basename,
    plus bigrams from its immediate parent dir (dirs carry feature names too)."""
    base = path.rsplit("/", 1)[-1]
    base = base.rsplit(".", 1)[0]
    toks = _tokens(base)
    stems = {t for t in toks if t not in _STOP}
    stems |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    parts = path.split("/")
    if len(parts) > 1:
        ptoks = _tokens(parts[-2])
        stems |= {f"{a} {b}" for a, b in zip(ptoks, ptoks[1:])}
        stems |= {t for t in ptoks if t not in _STOP}
    return stems


def build_census(conn, top_n: int = 800, min_concerns: int = 3, scope=None) -> int:
    """Stem census over every path the history touched, weighted by concern activity
    (falls back to touch counts pre-untangle, so `init` can seed the charter). Scope-
    filtered: out-of-scope vocabulary must never enter the evidence layer."""
    if scope is None:
        from ..scope import scope_or_default
        scope = scope_or_default()
    file_concerns: dict[str, int] = Counter()
    for r in conn.execute("SELECT files FROM concerns"):
        for f in json.loads(r["files"] or "[]"):
            file_concerns[f] += 1
    if not file_concerns:   # pre-untangle (init): weight by touch counts instead
        for r in conn.execute("SELECT path, COUNT(*) n FROM commit_files GROUP BY path"):
            if scope(r["path"]):
                file_concerns[r["path"]] = r["n"]
    all_files = {r["path"] for r in conn.execute("SELECT DISTINCT path FROM commit_files")
                 if scope(r["path"])}

    stem_files: dict[str, set] = defaultdict(set)
    stem_concerns: dict[str, int] = Counter()
    for f in all_files:
        for s in path_stems(f):
            stem_files[s].add(f)
            stem_concerns[s] += file_concerns.get(f, 0)

    n_all = max(1, len(all_files))
    # god-file threshold: 95th percentile of per-file concern counts (df)
    dfs = sorted(file_concerns.values())
    df_p95 = dfs[int(len(dfs) * 0.95)] if dfs else 999
    rows = []
    for s, files in stem_files.items():
        nc = stem_concerns[s]
        if nc < min_concerns:
            continue
        touched = sorted(file_concerns.get(f, 0) for f in files)
        med_df = touched[len(touched) // 2] if touched else 0
        # a stem is a HUB (never anchors a feature) when it's a directory-token spread over
        # a large share of the tree, or its files are god-files everything touches
        is_god = int(len(files) / n_all > 0.005 or med_df > max(3, df_p95))
        rows.append((s, len(files), nc, round(len(files) / n_all, 4), is_god,
                     json.dumps(sorted(files, key=lambda f: -file_concerns.get(f, 0))[:5])))
    rows.sort(key=lambda r: -r[2])
    rows = rows[:top_n]
    conn.execute("DELETE FROM stem_census")
    conn.executemany("INSERT INTO stem_census (stem, n_files, n_concerns, ubiquity, is_god, "
                     "sample_paths) VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


_DOC_RE = re.compile(r"\.(md|rst|txt)$", re.I)


def harvest_docs(repo: str, max_docs: int = 160, excerpt_lines: int = 30,
                 scope=None) -> list[dict]:
    """Titles + excerpts of in-repo prose at HEAD — the repo describing itself.
    Scope-filtered BEFORE the cap, so excluded trees can't starve real design docs."""
    out = []
    for path in run_git(repo, ["ls-files"]).splitlines():
        p = path.strip()
        if not _DOC_RE.search(p):
            continue
        if scope is not None and not scope(p):
            continue
        low = p.lower()
        if not (low.startswith(("doc/", "docs/", "wiki/")) or "readme" in low
                or "design" in low or "/doc/" in low or "/docs/" in low):
            continue
        try:
            text = (Path(repo) / p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [l for l in text.splitlines() if l.strip()][:excerpt_lines]
        title = next((l.lstrip("# ").strip() for l in lines if l.lstrip().startswith("#")),
                     p.rsplit("/", 1)[-1])
        out.append({"path": p, "title": title[:120], "excerpt": "\n".join(lines)[:1500]})
        if len(out) >= max_docs:
            break
    return out



_SYM_RE = re.compile(
    r"(?:class|struct|interface|trait)\s+([A-Z]\w{2,})|(?:def|function|fn|func)\s+([a-zA-Z_]\w{2,})"
    r"|\b([A-Z]\w{2,})::\w+\s*\(")


def _file_symbols(repo: str, path: str, cache: dict, max_syms: int = 5) -> list[str]:
    """Declared names in a file's head at HEAD — the code's own vocabulary for a stem."""
    if path in cache:
        return cache[path]
    text = run_git(repo, ["show", f"HEAD:{path}"], check=False)[:20000]
    syms: list[str] = []
    for m in _SYM_RE.finditer(text):
        s = next(g for g in m.groups() if g)
        if s not in syms:
            syms.append(s)
        if len(syms) >= max_syms:
            break
    cache[path] = syms
    return syms


_CENSUS_LABEL_MAX_FILES = 30   # a label spanning more files carries no per-stem signal


def _stem_change_labels(conn, wanted: set) -> dict[str, list[str]]:
    """stem -> up to 3 concern labels touching its files (what work there actually did).
    Import-origin concerns are BARRED as census evidence (measured: routing founding labels
    through evidence re-drafting destroys them — they enter the glossary directly instead),
    and so is any mega-concern whose label spans too many files to mean anything per-stem."""
    out: dict[str, list[str]] = defaultdict(list)
    fcache: dict[str, set] = {}
    for r in conn.execute(
            "SELECT label, files FROM concerns WHERE label IS NOT NULL AND origin IS NULL"):
        files = json.loads(r["files"] or "[]")
        if len(files) > _CENSUS_LABEL_MAX_FILES:
            continue
        for f in files:
            if f not in fcache:
                fcache[f] = path_stems(f) & wanted
            for s in fcache[f]:
                if len(out[s]) < 3 and r["label"] not in out[s]:
                    out[s].append(r["label"])
    return out


def _norm_stems(stems: set) -> set:
    """Comparison view of a stem set: adds affix-stripped variants so 'libpoly' and 'poly'
    overlap ('lib' prefixes are packaging convention, not identity)."""
    out = set(stems)
    for s in stems:
        if s.startswith("lib") and len(s) > 5:
            out.add(s[3:])
    return out


