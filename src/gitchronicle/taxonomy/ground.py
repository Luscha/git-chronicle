"""GROUND — the repo-level evidence layer (catalog v3, phase 0).

Per-commit reading is myopic: feature identity lives in AGGREGATES a single diff never
shows — file-name stems repeated across years (uiAvatarBuilder.py + avatar_builder.proto),
and the repo's own prose (README/Doc design documents that literally name and define its
systems). A human newcomer reads the file tree and Doc/ first; this stage does the same.

Everything expensive is local and O(unique paths):
  1. STEM CENSUS — tokenize every path ever touched into unigram/bigram name stems,
     weight by concern activity, flag ubiquity (generic words). Pure Python, seconds.
  2. DOC HARVEST — titles + excerpts of in-repo markdown/docs at HEAD. Local reads.
  3. GLOSSARY — the big model drafts the repo's feature vocabulary from the condensed
     census + doc excerpts (a few chunked calls; input is O(top stems + docs), so the
     cost is ~constant no matter how many commits the history has).

The glossary is a CANDIDATE vocabulary — induction validates it against actual concern
clusters; entities with no cluster support are simply never used.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..extract.git_ingest import run_git

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


def build_census(conn, top_n: int = 800, min_concerns: int = 3) -> int:
    """Stem census over every path the history touched, weighted by concern activity."""
    file_concerns: dict[str, int] = Counter()
    for r in conn.execute("SELECT files FROM concerns"):
        for f in json.loads(r["files"] or "[]"):
            file_concerns[f] += 1
    all_files = {r["path"] for r in conn.execute("SELECT DISTINCT path FROM commit_files")}

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


def harvest_docs(repo: str, max_docs: int = 120, excerpt_lines: int = 30) -> list[dict]:
    """Titles + excerpts of in-repo prose at HEAD — the repo describing itself."""
    out = []
    for path in run_git(repo, ["ls-files"]).splitlines():
        p = path.strip()
        if not _DOC_RE.search(p):
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


GLOSSARY_SYS = (
    "You are reconstructing the FEATURE VOCABULARY of one software project from repo-level "
    "evidence: a census of file-name STEMS (words from file/directory names, with how many "
    "files carry them, how much change activity they attract, and SAMPLE CHANGES — what "
    "work on those files actually did), and/or excerpts of the repo's own documentation. "
    "Draft the project's feature/subsystem entities: things a maintainer would name as A "
    "FEATURE of this project. Rules: an entity needs concrete evidence — never invent one; "
    "derive each definition from what the sample changes/docs SHOW the thing does, in "
    "concrete terms; if the evidence does not reveal what an entity actually IS, OMIT it "
    "entirely — a placeholder definition ('provides X-related functionality') is worse "
    "than no entry, so never write one; generic engineering words are not entities; prefer "
    "the project's own vocabulary (a doc title naming a system wins); one entity per real "
    "feature, not one per file. For each give: name (<=4 words), definition (one concrete "
    "sentence + Includes/Excludes if the evidence supports it), the stems that evidence "
    "it, and doc paths if any. List stems whose evidence you could NOT interpret under "
    '"unknown" — they will be retried with deeper evidence.\n'
    'Respond with ONE JSON object: {"entities":[{"name":"...","definition":"...",'
    '"stems":["..."],"docs":["path", ...]}],"unknown":["stem", ...]}'
)

PEEK_SYS = (
    "You are identifying what parts of one software project ARE, from actual code "
    "excerpts. For each STEM you get the head of its most-changed file. If the code "
    "reveals a nameable feature/subsystem/library of the project, emit an entity with a "
    "concrete one-sentence definition; if not, skip it. Never write a placeholder "
    "definition.\n"
    'Respond with ONE JSON object: {"entities":[{"name":"...","definition":"...",'
    '"stems":["..."]}]}'
)

GLOSSARY_MERGE_SYS = (
    "Deduplicate this machine-drafted feature vocabulary of ONE software project: combine "
    "entries that are the same feature under different names (keep the best name, union "
    "their stems/docs). DO NOT curate or drop entries — an entity with thin evidence is "
    "handled downstream (it is simply never used); your only job is merging duplicates and "
    "renaming a name that is a bare generic engineering word to what its evidence shows. "
    "Every input entity must appear in the output exactly once (merged or as-is).\n"
    'Respond with ONE JSON object: {"entities":[{"name":"...","definition":"...",'
    '"stems":["..."],"docs":["..."]}]}'
)


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


def draft_glossary(conn, provider, repo: str, cfg: dict, log=print) -> int:
    """Chunked glossary drafting over the condensed census + docs, then one merge pass.
    Census rows carry three evidence tiers: sample file names, concern labels (what work
    there did), declared symbols (the code's own vocabulary); stems the model still can't
    interpret escalate to a code-peek round. Omission is the last resort, not the default."""
    cat = cfg.get("catalog", {})
    chunk = int(cat.get("glossary_chunk", 150))
    census = conn.execute(
        "SELECT stem, n_files, n_concerns, ubiquity, sample_paths FROM stem_census "
        "WHERE is_god=0 ORDER BY n_concerns DESC").fetchall()
    docs = harvest_docs(repo, max_docs=int(cat.get("glossary_max_docs", 120)))
    log(f"  census: {len(census)} stems; docs: {len(docs)}")
    if not census and not docs:
        return 0
    labels = _stem_change_labels(conn, {r["stem"] for r in census})
    symcache: dict[str, list] = {}

    # Two SEPARATE evidence passes. Mixing them fails in a measured way: rich doc prose
    # dominates attention and every census chunk returns only the doc entities, mining
    # zero from the stems. Docs are mined once; census chunks are mined alone.
    drafts = []

    def _collect(out, tier):
        for e in (out.get("entities") or []) if isinstance(out, dict) else []:
            if isinstance(e, dict) and (e.get("name") or "").strip():
                drafts.append({**e, "tier": tier})

    rich = [d for d in docs if "design" in d["path"].lower() or "readme" in d["path"].lower()]
    for i in range(0, len(rich), 12):
        block = "\n\n".join(f"### {d['path']}\n{d['excerpt'][:900]}" for d in rich[i:i + 12])
        try:
            _collect(provider.chat(GLOSSARY_SYS, f"KEY DOC EXCERPTS:\n{block}",
                                   want_json=True, large=True, cache_extra=f"glossary-docs:{i}"),
                     tier=4)
        except Exception:  # noqa: BLE001
            pass
    n_docs_pass = len(drafts)

    unknown: list[str] = []

    def _row(r) -> str:
        paths = json.loads(r["sample_paths"])
        names = ", ".join(p.rsplit("/", 1)[-1] for p in paths[:3])
        line = (f"- {r['stem']}  (files={r['n_files']}, activity={r['n_concerns']}, "
                f"e.g. {names})")
        if labels.get(r["stem"]):
            line += "\n    changes: " + "; ".join(l[:60] for l in labels[r["stem"]])
        syms = _file_symbols(repo, paths[0], symcache) if paths else []
        if syms:
            line += "\n    symbols: " + ", ".join(syms)
        return line

    for i in range(0, len(census), chunk):
        part = census[i:i + chunk]
        user = (f"STEM CENSUS (chunk {i // chunk + 1}) — mine the FEATURES this vocabulary "
                f"evidences; the 'changes' and 'symbols' lines are the semantic evidence:\n"
                + "\n".join(_row(r) for r in part))
        try:
            out = provider.chat(GLOSSARY_SYS, user, want_json=True, large=True,
                                cache_extra=f"glossary-census:{i}")
        except Exception:  # noqa: BLE001
            continue
        _collect(out, tier=2)
        if isinstance(out, dict):
            unknown += [str(s) for s in (out.get("unknown") or [])][:40]
    n_census_pass = len(drafts) - n_docs_pass

    # escalation: stems the model could not interpret get actual code excerpts
    peek = [r for r in census if r["stem"] in set(unknown)][:60]
    for i in range(0, len(peek), 15):
        blocks = []
        for r in peek[i:i + 15]:
            paths = json.loads(r["sample_paths"])
            if not paths:
                continue
            head = run_git(repo, ["show", f"HEAD:{paths[0]}"], check=False)[:1200]
            blocks.append(f"### stem: {r['stem']}  ({paths[0]})\n{head}")
        if not blocks:
            continue
        try:
            _collect(provider.chat(PEEK_SYS, "\n\n".join(blocks), want_json=True, large=True,
                                   cache_extra=f"glossary-peek:{i}"), tier=3)
        except Exception:  # noqa: BLE001
            continue

    # Direct entity injection (measured as the ONLY working ground integration): peek-labelled
    # import families from bulk commits enter as entities verbatim — never as census evidence.
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    n_import = 0
    for r in conn.execute("SELECT label, summary, files FROM concerns WHERE origin='import'"):
        stems: set = set()
        for f in json.loads(r["files"] or "[]")[:8]:
            stems |= path_stems(f)
        drafts.append({"name": r["label"], "definition": r["summary"] or "",
                       "stems": sorted(stems - god), "tier": 3, "source": "import"})
        n_import += 1
    log(f"  drafted {len(drafts)} candidate entities ({n_docs_pass} docs, "
        f"{n_census_pass} census, "
        f"{len(drafts) - n_docs_pass - n_census_pass - n_import} code-peek of "
        f"{len(peek)} unknown, {n_import} injected import families)")

    # Deterministic merge: entities whose NORMALIZED stem sets overlap >=50% of the smaller
    # set are the same thing (a system and its facet, or the same library under 'libx'/'x').
    # Territory conflicts resolve by EVIDENCE TIER (doc > peek > labels), not by wording:
    # a code-peek definition beats one inferred from a single incidental commit's label.
    merged: list[dict] = []
    for e in sorted(drafts, key=lambda d: -int(d.get("tier") or 2)):
        est = {str(s).lower().strip() for s in (e.get("stems") or []) if str(s).strip()}
        nest = _norm_stems(est)
        home = None
        for m in merged:
            small = min(len(nest), len(m["_norm"])) or 1
            if nest and len(nest & m["_norm"]) * 2 >= small:
                home = m
                break
        if home is None:
            merged.append({**e, "_stems": est, "_norm": nest})
        else:
            home["_stems"] |= est
            home["_norm"] |= nest
            et, ht = int(e.get("tier") or 2), int(home.get("tier") or 2)
            if et > ht:                          # higher evidence tier wins name+definition
                home["name"], home["definition"], home["tier"] = e["name"], e.get("definition"), et
            elif et == ht:
                if len(str(e.get("definition") or "")) > len(str(home.get("definition") or "")):
                    home["definition"] = e["definition"]
                if 0 < len(str(e.get("name") or "")) < len(str(home.get("name") or "")):
                    home["name"] = e["name"]     # shorter name = the system, not the facet
            home["docs"] = list(dict.fromkeys((home.get("docs") or []) + (e.get("docs") or [])))
    drafts = [{**m, "stems": sorted(m.pop("_stems"))} for m in merged]
    for d in drafts:
        d.pop("_norm", None)

    # Stem-bucketed chunked LLM dedupe: near-dups co-locate in a chunk (sorting by primary
    # stem), the model merges duplicates but may never drop; a chunk that over-drops is kept
    # from the deterministic result instead. Tier re-attaches by stem overlap afterwards.
    drafts.sort(key=lambda m: ((m.get("stems") or ["~"])[0], str(m["name"]).lower()))
    tier_of_stem: dict[str, int] = {}
    for d in drafts:
        for s in (d.get("stems") or []):
            tier_of_stem[s] = max(tier_of_stem.get(s, 0), int(d.get("tier") or 2))
    final: list[dict] = []
    for i in range(0, len(drafts), 130):
        part = drafts[i:i + 130]
        listing = "\n".join(
            f"{j}: {d['name']} — {str(d.get('definition') or '')[:90]} "
            f"|stems: {', '.join((d.get('stems') or [])[:5])}" for j, d in enumerate(part))
        try:
            out = provider.chat(GLOSSARY_MERGE_SYS, listing, want_json=True, large=True,
                                cache_extra=f"glossary-merge:{i}:{len(part)}")
            ents = [e for e in (out.get("entities") or []) if isinstance(e, dict)
                    and (e.get("name") or "").strip()] if isinstance(out, dict) else []
        except Exception:  # noqa: BLE001
            ents = []
        if ents and len(ents) >= len(part) * 0.5:
            for e in ents:
                stems = [str(s).lower().strip() for s in (e.get("stems") or []) if str(s).strip()]
                e["tier"] = max((tier_of_stem.get(s, 0) for s in stems), default=2) or 2
                e["stems"] = stems
                final.append(e)
        else:
            final.extend(part)

    conn.execute("DELETE FROM glossary WHERE status='candidate'")
    seen = set()
    n = n_placeholder = 0
    for e in final:
        name = str(e["name"]).strip()[:60]
        if name.lower() in seen:
            continue
        # a definition that adds no information beyond the name is worse than no entity:
        # it anchors nothing and pollutes review. The stem stays in the census either way.
        d = str(e.get("definition") or "").strip().lower()
        name_words = set(re.findall(r"[a-z]+", name.lower()))
        d_head = set(re.findall(r"[a-z]+", d[:70]))
        if (not d or "related functionality" in d
                or (d.startswith(("manages", "handles", "provides"))
                    and len(d_head - name_words - {"manages", "handles", "provides",
                                                   "functionality", "including", "related",
                                                   "system", "and", "the", "for", "with"}) < 3)):
            n_placeholder += 1
            continue
        seen.add(name.lower())
        stems = [str(s).lower().strip() for s in (e.get("stems") or []) if str(s).strip()][:12]
        docs_ev = [str(d) for d in (e.get("docs") or [])][:6]
        tier = int(e.get("tier") or 2)
        source = e.get("source") or ("doc" if docs_ev else ("peek" if tier == 3 else "census"))
        conn.execute(
            "INSERT INTO glossary (name, definition, stems, evidence, source, tier, status) "
            "VALUES (?,?,?,?,?,?, 'candidate')",
            (name, str(e.get("definition") or "").strip()[:500], json.dumps(stems),
             json.dumps({"docs": docs_ev}), source, tier))
        n += 1
    conn.commit()
    log(f"  glossary: {n} entities after merge ({n_placeholder} placeholder-definition "
        f"candidates suppressed)")
    return n


def inject_new_import_entities(conn, log=print) -> int:
    """Incremental path: import families from NEW bulk commits become glossary entities
    even when the full glossary re-draft is skipped. Deterministic, no LLM.
    Novelty is decided by STEM TERRITORY, not by name — the drafting merge renames and
    merges families, so name matching re-injects everything it already absorbed (measured:
    275 duplicates). A family whose normalized stems are already half-claimed by one
    existing entity is represented; its concerns classify against that entity instead."""
    god = {r["stem"] for r in conn.execute("SELECT stem FROM stem_census WHERE is_god=1")}
    have_names = {r["name"].lower() for r in conn.execute("SELECT name FROM glossary")}
    territories = []
    claimed_all: set = set()
    for r in conn.execute("SELECT stems FROM glossary"):
        t = _norm_stems(set(json.loads(r["stems"] or "[]")))
        territories.append(t)
        claimed_all |= t
    for r in conn.execute("SELECT stems FROM domains "
                          "WHERE status IN ('named','provisional','confirmed')"):
        claimed_all |= _norm_stems(set(json.loads(r["stems"] or "[]")))
    n = 0
    for r in conn.execute("SELECT label, summary, files FROM concerns WHERE origin='import'"):
        name = r["label"].strip()[:60]
        if not name or name.lower() in have_names:
            continue
        stems: set = set()
        for f in json.loads(r["files"] or "[]")[:8]:
            stems |= path_stems(f)
        stems -= god
        nest = _norm_stems(stems)
        if not nest:
            continue
        # a re-imported system is not a new one: skip when the family's vocabulary is
        # majority-claimed ANYWHERE (a re-init re-imports the whole repo — measured: 1,424
        # duplicate injections under the per-entity rule), or half-claimed by one entity
        if len(nest & claimed_all) * 2 >= len(nest):
            continue
        if any(len(nest & t) * 2 >= (min(len(nest), len(t)) or 1) for t in territories):
            continue
        conn.execute(
            "INSERT INTO glossary (name, definition, stems, evidence, source, tier, status) "
            "VALUES (?,?,?,?, 'import', 3, 'candidate')",
            (name, (r["summary"] or "").strip()[:500],
             json.dumps(sorted(stems)[:12]), json.dumps({"docs": []})))
        have_names.add(name.lower())
        territories.append(nest)
        n += 1
    conn.commit()
    if n:
        log(f"  injected {n} new import-family entities into the glossary")
    return n


def ground(conn, provider, repo: str, cfg: dict, log=print, force: bool = False) -> dict:
    """Phase 0 of the taxonomy: build the repo-level evidence layer."""
    have = conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]
    if have and not force:
        log(f"  glossary exists ({have} entities) — skipping ground (use --force to rebuild)")
        n_new = inject_new_import_entities(conn, log)
        return {"glossary": have + n_new, "skipped": True}
    n_stems = build_census(conn, top_n=int(cfg.get("catalog", {}).get("census_top", 800)))
    log(f"  stem census: {n_stems} stems")
    n_gloss = draft_glossary(conn, provider, repo, cfg, log)
    return {"stems": n_stems, "glossary": n_gloss}
