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

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _uncommented(section: str) -> str:
    """Section text with HTML comments removed, including multi-line ones.

    Stripping per line only reaches comments that open and close on that line, so a
    commented-out BLOCK stayed fully live — a template meant to be inactive was parsed
    and silently applied. Both readers share this so neither can drift back.
    """
    return _COMMENT_RE.sub("", section)

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
            for ln in _uncommented(m.group(0) if m else "").splitlines():
                ln = ln.strip()
                mm = re.match(r"-\s*(include|exclude|acknowledge):\s*(\S+)", ln)
                if mm:
                    {"include": inc, "exclude": exc,
                     "acknowledge": ack}[mm.group(1)].append(mm.group(2))
        return cls(inc, exc, ack)

    def __call__(self, path: str) -> bool:
        """True when the path is IN scope. Semantics: an explicit include wins (it can
        carve a code pocket back out of an excluded tree), exclude/acknowledge carve out,
        and everything the map never mentions is IN — silence must not exclude."""
        if any(fnmatch.fnmatch(path, g) for g in self.includes if g != "**"):
            return True
        if any(fnmatch.fnmatch(path, g) for g in self.excludes):
            return False
        if any(fnmatch.fnmatch(path, g) for g in self.acknowledges):
            return False
        return True

    def verdict(self, glob: str) -> str:
        """What the map says about a subtree, as the studio shows it: the rule that
        actually decides, not the one written last."""
        probe = glob.rstrip("*").rstrip("/") + "/probe"
        if any(fnmatch.fnmatch(probe, g) for g in self.includes if g != "**"):
            return "analysed"
        if any(fnmatch.fnmatch(probe, g) for g in self.acknowledges):
            return "one entry"
        if any(fnmatch.fnmatch(probe, g) for g in self.excludes):
            return "external"
        return "analysed"

    def set(self, glob: str, verdict: str, siblings: list[str] | None = None) -> None:
        """Move a subtree between verdicts, in memory. ``save`` writes the file.

        Excluding a subtree that an ANCESTOR include covers would do nothing, because an
        explicit include wins — that is how 13,345 vendored files stayed in scope under a
        drafted `include: Extern-Server/**`. So the ancestor include is lifted and the
        subtree's siblings keep theirs, which leaves every other verdict unchanged.
        """
        for lst in (self.includes, self.excludes, self.acknowledges):
            while glob in lst:
                lst.remove(glob)
        if verdict == "analysed":
            self.includes.append(glob)
        elif verdict in ("external", "one entry"):
            probe = glob.rstrip("*").rstrip("/") + "/probe"
            for anc in [g for g in self.includes
                        if g != glob and g != "**" and fnmatch.fnmatch(probe, g)]:
                self.includes.remove(anc)
                for sib in siblings or []:
                    if sib != glob and sib not in self.includes:
                        self.includes.append(sib)
            (self.excludes if verdict == "external" else self.acknowledges).append(glob)
        else:
            raise ValueError(f"unknown verdict {verdict!r}")

    def render(self) -> str:
        """The ## Scope section, verdicts first and the rule that decides on top.

        Order matters and is not cosmetic: an explicit include beats an exclude, so a
        reader has to be able to see which line wins.
        """
        out = ["## Scope", "",
               "<!-- The pipeline analyses ONLY paths matching an `include:` glob and no",
               "     `exclude:`/`acknowledge:` glob. An explicit include WINS over an",
               "     exclude, so it can carve a pocket of your code out of a vendored tree.",
               "     `acknowledge:` catalogues an owned sub-product as ONE entry without",
               "     decomposing it. Edit here or in the studio's Scope view. -->", ""]
        out += [f"- include: {g}" for g in self.includes if g != "**"]
        out += [f"- exclude: {g}" for g in self.excludes]
        out += [f"- acknowledge: {g}" for g in self.acknowledges]
        return "\n".join(out) + "\n"

    def save(self, md_path: str | Path = MD_FILE) -> None:
        """Replace the ## Scope section, leaving every other section (Direction, notes)
        exactly as it was — the file is the owner's, not the tool's."""
        p = Path(md_path)
        text = p.read_text(encoding="utf-8") if p.exists() else "# gitchronicle\n\n"
        if re.search(r"## Scope.*?(?=\n## |\Z)", text, re.S):
            text = re.sub(r"## Scope.*?(?=\n## |\Z)", self.render(), text, count=1, flags=re.S)
        else:
            text = text.rstrip() + "\n\n" + self.render()
        p.write_text(text, encoding="utf-8")

    def shadowed(self) -> list[tuple[str, str]]:
        """Excludes an include already overrides — they read as filters and do nothing.

        An explicit include wins by design, so it can carve a pocket of code back out of
        an excluded tree; the cost is that a narrower exclude written under a broader
        include is silently inert, which is a trap worth reporting.
        """
        out = []
        for e in self.excludes + self.acknowledges:
            probe = e.replace("**", "x").replace("*", "x")
            for i in self.includes:
                if i != "**" and fnmatch.fnmatch(probe, i):
                    out.append((e, i))
                    break
        return out

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




class Direction:
    """DIRECTION — the owner's standing instructions, kept typed rather than as prose.

    The optional half of this tool's original ask: say up front what you are looking for,
    and let the pipeline lean that way. A ``## Direction`` section in ``gitchronicle.md``;
    absent, the pipeline runs exactly as it does today (auto mode) — silence must not
    change behaviour.

    v0.1 shipped a free-prose ``## Charter``, measured it as ineffective and removed it
    (commit 8d0a8e3). Two things went wrong and both are addressed here. Prose sprayed
    into every prompt is not a lever you can aim, so the statements are TYPED: a glossary
    naming things the model cannot know, rules that constrain grain, and a voice for the
    narration. And nobody could tell whether it had helped, so ``report()`` states exactly
    what was active and where it was applied.

    Be aware it is not free: direction text joins the prompt, which changes the cache key,
    so turning it on or editing it re-pays the naming and chronicle passes.

        ## Direction
        - voice: Write for the person who built this, reading it years later.
        - glossary: luna = the embedded Lua scripting bridge
        - rule: Item prototypes, stats and bonuses are ONE system; never split them.
    """

    VERBS = ("voice", "glossary", "rule", "audience")

    def __init__(self, voice="", audience="", glossary=None, rules=None):
        self.voice = voice
        self.audience = audience
        self.glossary: list[str] = glossary or []
        self.rules: list[str] = rules or []

    @classmethod
    def load(cls, md_path: str | Path = MD_FILE) -> "Direction":
        p = Path(md_path)
        if not p.exists():
            return cls()
        m = re.search(r"## Direction.*?(?=\n## |\Z)", p.read_text(encoding="utf-8"), re.S)
        if not m:
            return cls()
        voice = audience = ""
        gloss: list[str] = []
        rules: list[str] = []
        for ln in _uncommented(m.group(0)).splitlines():
            ln = ln.strip()
            mm = re.match(r"-\s*(voice|audience|glossary|rule)\s*:\s*(.+)", ln, re.I)
            if not mm:
                continue
            verb, rest = mm.group(1).lower(), mm.group(2).strip()
            if verb == "voice":
                voice = rest
            elif verb == "audience":
                audience = rest
            elif verb == "glossary":
                gloss.append(rest)
            else:
                rules.append(rest)
        return cls(voice, audience, gloss, rules)

    def verdict(self, glob: str) -> str:
        """What the map says about a subtree, as the studio shows it: the rule that
        actually decides, not the one written last."""
        probe = glob.rstrip("*").rstrip("/") + "/probe"
        if any(fnmatch.fnmatch(probe, g) for g in self.includes if g != "**"):
            return "analysed"
        if any(fnmatch.fnmatch(probe, g) for g in self.acknowledges):
            return "one entry"
        if any(fnmatch.fnmatch(probe, g) for g in self.excludes):
            return "external"
        return "analysed"

    def set(self, glob: str, verdict: str, siblings: list[str] | None = None) -> None:
        """Move a subtree between verdicts, in memory. ``save`` writes the file.

        Excluding a subtree that an ANCESTOR include covers would do nothing, because an
        explicit include wins — that is how 13,345 vendored files stayed in scope under a
        drafted `include: Extern-Server/**`. So the ancestor include is lifted and the
        subtree's siblings keep theirs, which leaves every other verdict unchanged.
        """
        for lst in (self.includes, self.excludes, self.acknowledges):
            while glob in lst:
                lst.remove(glob)
        if verdict == "analysed":
            self.includes.append(glob)
        elif verdict in ("external", "one entry"):
            probe = glob.rstrip("*").rstrip("/") + "/probe"
            for anc in [g for g in self.includes
                        if g != glob and g != "**" and fnmatch.fnmatch(probe, g)]:
                self.includes.remove(anc)
                for sib in siblings or []:
                    if sib != glob and sib not in self.includes:
                        self.includes.append(sib)
            (self.excludes if verdict == "external" else self.acknowledges).append(glob)
        else:
            raise ValueError(f"unknown verdict {verdict!r}")

    def render(self) -> str:
        """The ## Scope section, verdicts first and the rule that decides on top.

        Order matters and is not cosmetic: an explicit include beats an exclude, so a
        reader has to be able to see which line wins.
        """
        out = ["## Scope", "",
               "<!-- The pipeline analyses ONLY paths matching an `include:` glob and no",
               "     `exclude:`/`acknowledge:` glob. An explicit include WINS over an",
               "     exclude, so it can carve a pocket of your code out of a vendored tree.",
               "     `acknowledge:` catalogues an owned sub-product as ONE entry without",
               "     decomposing it. Edit here or in the studio's Scope view. -->", ""]
        out += [f"- include: {g}" for g in self.includes if g != "**"]
        out += [f"- exclude: {g}" for g in self.excludes]
        out += [f"- acknowledge: {g}" for g in self.acknowledges]
        return "\n".join(out) + "\n"

    def save(self, md_path: str | Path = MD_FILE) -> None:
        """Replace the ## Scope section, leaving every other section (Direction, notes)
        exactly as it was — the file is the owner's, not the tool's."""
        p = Path(md_path)
        text = p.read_text(encoding="utf-8") if p.exists() else "# gitchronicle\n\n"
        if re.search(r"## Scope.*?(?=\n## |\Z)", text, re.S):
            text = re.sub(r"## Scope.*?(?=\n## |\Z)", self.render(), text, count=1, flags=re.S)
        else:
            text = text.rstrip() + "\n\n" + self.render()
        p.write_text(text, encoding="utf-8")

    def shadowed(self) -> list[tuple[str, str]]:
        """Excludes an include already overrides — they read as filters and do nothing.

        An explicit include wins by design, so it can carve a pocket of code back out of
        an excluded tree; the cost is that a narrower exclude written under a broader
        include is silently inert, which is a trap worth reporting.
        """
        out = []
        for e in self.excludes + self.acknowledges:
            probe = e.replace("**", "x").replace("*", "x")
            for i in self.includes:
                if i != "**" and fnmatch.fnmatch(probe, i):
                    out.append((e, i))
                    break
        return out

    def exists(self) -> bool:
        return bool(self.voice or self.audience or self.glossary or self.rules)

    def naming_prefix(self) -> str:
        """Glossary and grain rules — what a namer cannot infer from paths alone."""
        if not (self.glossary or self.rules):
            return ""
        out = []
        if self.glossary:
            out.append("This project's own vocabulary:\n"
                       + "\n".join(f"- {g}" for g in self.glossary))
        if self.rules:
            out.append("The owner's standing rules:\n"
                       + "\n".join(f"- {r}" for r in self.rules))
        return "\n".join(out) + "\n"

    def voice_suffix(self) -> str:
        """Tone for the narration; never changes what the evidence says."""
        bits = []
        if self.audience:
            bits.append(f"Audience: {self.audience}.")
        if self.voice:
            bits.append(self.voice)
        return (" " + " ".join(bits)) if bits else ""

    def key(self) -> str:
        """Identity of this direction, so a change invalidates the prompts it touched."""
        import hashlib
        raw = "\x00".join([self.voice, self.audience, *self.glossary, *self.rules])
        return hashlib.sha1(raw.encode()).hexdigest()[:10] if raw.strip("\x00") else ""

    def report(self, log=print) -> None:
        if not self.exists():
            return
        log(f"  direction: {len(self.glossary)} glossary, {len(self.rules)} rules"
            + (", voice set" if self.voice else "")
            + (f", audience '{self.audience}'" if self.audience else "")
            + f"  [key {self.key()}]")
