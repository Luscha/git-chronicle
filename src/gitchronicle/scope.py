"""SCOPE — the owner-reviewed boundary of what the pipeline analyses.

Every catastrophic result in validation traced to one missing question: which parts of
this repository are the product? A vendored Boost became fifty features; a bundled web
app became six hundred families. No runtime heuristic decides that reliably — so none
does. The boundary lives in a persistent, reviewable artifact (the ``## Scope`` section
of ``gitchronicle.md``): the tool DRAFTS it from evidence, a human confirms it once,
and the pipeline filters through it exclusively.

The draft is pondered from BOTH commit history and the current worktree, because repos
get reorganized: a root that died in a folder-reorg (``dev/`` 2016-2019) must still be
scoped for the history that lived there. Per top-level root (and second-level roots of
big trees) the analysis records: lifetime (first/last commit touching it), file count,
change activity, later-touch ratio, extension profile vs the repo's dominant one, and
ecosystem markers (LICENSE/package.json/etc. under a non-root subtree = a foreign drop).
Heuristic suggestions (include/exclude) are just that — suggestions in the draft.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .extract.git_ingest import run_git

MD_FILE = "gitchronicle.md"

_MARKERS = {"license", "license.txt", "license.md", "license_1_0.txt", "copying",
            "package.json", "cargo.toml", "setup.py", "pom.xml", "gemfile"}

# draft-time ADVISORS only — never consulted at runtime (the scope map is the runtime)
_SUSPECT_SEGMENTS = {
    "node_modules", "vendor", "vendored", "third_party", "thirdparty", "extern",
    "externals", "external", "deps", "dist", "target", "__pycache__", "generated",
    "locale", "locales", "i18n", "translations",
}


def _roots_analysis(repo: str, max_roots: int = 60) -> list[dict]:
    """Per-root evidence table from full history + worktree."""
    # one pass: every commit's date + files (name-only; cheap compared to numstat)
    out = run_git(repo, ["log", "--all", "--format=@%H %ad", "--date=short", "--name-only",
                         "--no-renames"])
    touches: dict[str, int] = Counter()
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    files_of: dict[str, set] = defaultdict(set)
    exts_of: dict[str, Counter] = defaultdict(Counter)
    date = ""
    for ln in out.splitlines():
        if ln.startswith("@"):
            date = ln.split()[-1]
            continue
        p = ln.strip()
        if not p:
            continue
        parts = p.split("/")
        root = parts[0] if len(parts) > 1 else "(root)"
        # big trees get second-level resolution so a foreign subtree is visible
        if len(parts) > 2:
            sub = f"{parts[0]}/{parts[1]}"
        else:
            sub = root
        for key in {root, sub}:
            touches[key] += 1
            files_of[key].add(p)
            if "." in parts[-1]:
                exts_of[key][parts[-1].rsplit(".", 1)[-1].lower()] += 1
            if key not in first or date < first[key]:
                first[key] = date
            if key not in last or date > last[key]:
                last[key] = date

    repo_exts = Counter()
    for c in exts_of.values():
        repo_exts.update(c)
    dominant = {e for e, _ in repo_exts.most_common(10)}

    worktree = set(run_git(repo, ["ls-files"]).splitlines())
    wt_roots = {p.split("/")[0] for p in worktree if p.strip()}
    wt_markers: dict[str, bool] = {}
    for p in worktree:
        parts = p.lower().split("/")
        if len(parts) >= 2 and parts[-1] in _MARKERS:
            wt_markers["/".join(p.split("/")[:-1])] = True

    rows = []
    majors = sorted(touches, key=lambda r: -touches[r])[:max_roots]
    for key in majors:
        exts = exts_of[key]
        total = sum(exts.values()) or 1
        overlap = sum(n for e, n in exts.items() if e in dominant) / total
        segs = set(key.lower().split("/"))
        # a marker signals a foreign drop only at the subtree's own level — a LICENSE
        # buried five directories deep must not condemn a first-party root
        marker = any(m == key or (m.startswith(key + "/")
                                  and m.count("/") <= key.count("/") + 1)
                     for m in wt_markers)
        alive = key.split("/")[0] in wt_roots
        suspect = bool(segs & _SUSPECT_SEGMENTS) or (overlap < 0.3 and len(files_of[key]) > 40) \
            or (marker and key.count("/") >= 1)
        rows.append({
            "root": key, "files": len(files_of[key]), "touches": touches[key],
            "first": first.get(key, ""), "last": last.get(key, ""),
            "alive": alive, "ext_overlap": round(overlap, 2),
            "top_exts": [e for e, _ in exts.most_common(4)],
            "marker": marker, "suggest": "exclude" if suspect else "include",
        })
    return rows


def draft_scope(repo: str, md_path: str | Path = MD_FILE) -> str:
    """Generate (or refresh) the ## Scope section draft. Returns the section text."""
    rows = _roots_analysis(repo)
    lines = ["## Scope", "",
             "<!-- The pipeline analyses ONLY paths matching an `include:` glob and no",
             "     `exclude:` glob. Review the drafted verdicts — the evidence for each is",
             "     in the comment. Scope edits re-run analysis for affected commits. -->", ""]
    for r in sorted(rows, key=lambda r: (r["suggest"] != "exclude", -r["touches"])):
        ev = (f"{r['files']} files, {r['touches']} touches, {r['first']}→{r['last']}"
              f"{'' if r['alive'] else ' (gone from worktree)'}, "
              f"exts {','.join(r['top_exts'])} (repo-overlap {r['ext_overlap']})"
              + (", ecosystem markers" if r["marker"] else ""))
        verdict = "exclude" if r["suggest"] == "exclude" else "include"
        lines.append(f"- {verdict}: {r['root']}/**    <!-- {ev} -->")
    lines.append("")
    section = "\n".join(lines)

    p = Path(md_path)
    if p.exists():
        text = p.read_text(encoding="utf-8")
        if "## Scope" in text:
            text = re.sub(r"## Scope.*?(?=\n## |\Z)", section, text, flags=re.S)
        else:
            text = section + "\n" + text
    else:
        text = "# gitchronicle\n\n" + section
    p.write_text(text, encoding="utf-8")
    return section


class Scope:
    """Parsed runtime filter. The ONLY authority on what the pipeline sees.
    Verdicts: include (analysed), exclude (invisible), acknowledge (owned sub-product:
    catalogued as ONE feature, its internals never analysed)."""

    def __init__(self, includes: list[str], excludes: list[str],
                 acknowledges: list[str] | None = None):
        self.includes = includes or ["**"]
        self.excludes = excludes
        self.acknowledges = acknowledges or []

    @classmethod
    def load(cls, md_path: str | Path = MD_FILE) -> "Scope":
        p = Path(md_path)
        inc, exc, ack = [], [], []
        if p.exists():
            m = re.search(r"## Scope.*?(?=\n## |\Z)", p.read_text(encoding="utf-8"), re.S)
            for ln in (m.group(0) if m else "").splitlines():
                ln = re.sub(r"<!--.*?-->", "", ln).strip()
                mm = re.match(r"-\s*(include|exclude|acknowledge):\s*(\S+)", ln)
                if mm:
                    {"include": inc, "exclude": exc,
                     "acknowledge": ack}[mm.group(1)].append(mm.group(2))
        return cls(inc, exc, ack)

    def __call__(self, path: str) -> bool:
        """True when the path is IN scope (acknowledged subtrees are NOT analysed)."""
        if any(fnmatch.fnmatch(path, g) for g in self.excludes):
            return False
        if any(fnmatch.fnmatch(path, g) for g in self.acknowledges):
            return False
        return any(fnmatch.fnmatch(path, g) for g in self.includes)

    def exists(self) -> bool:
        return bool(self.excludes) or self.includes != ["**"]


def scope_or_default(md_path: str | Path = MD_FILE, log=None):
    """The runtime path filter: the reviewed scope map when present, else the legacy
    draft-advisor heuristics with a loud pointer to `gitchronicle init`."""
    s = Scope.load(md_path)
    if s.exists():
        return s
    from .enrich.signals import is_vendored
    if log:
        log("  no reviewed scope map — falling back to built-in heuristics "
            "(run `gitchronicle init` to draft and review one)")
    return lambda path: not is_vendored(path)


CHARTER_HEADER = """## Charter

<!-- Owner knowledge the repository cannot express — plain sentences, consumed ONLY by
     the feature-naming stages (never per-commit analysis, never assignment picks).
     Statements that work:
       - "<X> and <Y> are one feature"
       - "<X> and <Y> are distinct features, never merge them"
       - "UI windows/screens belong to the feature they serve"
       - one or two lines describing what this project IS -->

(describe the project here)
"""


def draft_charter(conn, md_path: str | Path = MD_FILE) -> str:
    """Charter skeleton seeded from the census (generic — no docs, no repo assumptions):
    related vocabulary groups are surfaced as commented questions the owner can turn into
    rulings. Requires a census (init builds a touch-weighted one pre-untangle)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT stem, n_concerns FROM stem_census WHERE is_god=0 "
                          "ORDER BY n_concerns DESC LIMIT 400"):
        groups[r["stem"].split()[0]].append(r["stem"])
    questions = [(tok, v) for tok, v in groups.items() if len(v) >= 3][:15]
    section = CHARTER_HEADER
    if questions:
        section += ("\n<!-- the census found related vocabulary that may need a ruling —\n"
                    "     one sentence each turns a guess into a rule: -->\n")
        for tok, variants in questions:
            section += f"<!-- {tok}: {', '.join(variants[:5])} -->\n"
    p = Path(md_path)
    text = p.read_text(encoding="utf-8") if p.exists() else "# gitchronicle\n\n"
    if "## Charter" in text:
        text = re.sub(r"## Charter.*?(?=\n## |\Z)", section, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + section
    p.write_text(text, encoding="utf-8")
    return section


def load_charter(md_path: str | Path = MD_FILE) -> str:
    """The ## Charter section body (owner taste, injected at label-space calls only)."""
    p = Path(md_path)
    if not p.exists():
        return ""
    m = re.search(r"## Charter\s*\n(.*?)(?=\n## |\Z)", p.read_text(encoding="utf-8"), re.S)
    body = (m.group(1) if m else "").strip()
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    if body == "(describe the project here)":   # untouched skeleton = no charter
        return ""
    return body[:2500]
