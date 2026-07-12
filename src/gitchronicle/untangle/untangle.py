"""Untangle each commit into semantic CONCERNS — adaptively, by the cheapest sufficient signal.

The DIFF is the truth but ~99% of an LLM call's cost is the diff going *in*. So we route per
commit against the repo's own message quality:

- **message-first** for small, single-purpose commits with a descriptive subject — the subject
  already *is* the concern; a tiny LLM call normalises it into a clean capability label.
- **diff-escalation** only when a commit is likely tangled (many files), its message is thin
  (terse/empty), or its subject looks multi-topic — the cases the diff was added for.

Diffs are capped by lines AND characters (a minified/data line's first 200 chars say all the
model needs), so one data-dump commit can't balloon into a 78k-token prompt. Net: cost scales
with a repo's messiness — near-free on well-documented repos, still high-quality on messy ones.
Concerns remain the atomic clustering unit either way (god-files scatter across concerns, not one
blob), because we never cluster the file — only the semantic concern.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..extract.git_ingest import run_git
from ..storage import mark_stage

# Use the file PATHS to figure out WHICH FEATURE a change serves, then name that feature. Paths
# disambiguate generic edits (a timer under a client-engine dir is the client frame timer). BUT a
# feature can span subsystems — ranking = its client UI + its server logic — and must stay ONE
# concern named after the feature, NOT split by client/server or by directory.
_PATH_RULE = (
    "Use the file PATHS to identify WHICH concrete FEATURE the change serves (the directory tree "
    "shows where code lives and disambiguates generic edits), then name THAT FEATURE. CRITICAL: a "
    "feature can span subsystems — e.g. a 'ranking' feature owns both its client UI files and its "
    "server logic files; keep such a change as ONE concern named after the feature — do NOT split "
    "it by client vs server or by directory, and do NOT prepend the subsystem. Name the feature "
    "the paths+code reveal, never a location-free surface word and never the file's mere location."
)
# --- diff-routed prompt: read the code, split into concerns ---
DIFF_SYS = (
    "You untangle a git commit into its distinct CONCERNS by reading the DIFF and the file PATHS. "
    "The commit MESSAGE is unreliable (often multi-topic, terse, or wrong) — rely on the CODE and "
    "the PATHS. Group the changed files into concerns: each concern is ONE coherent purpose (a "
    "capability or behaviour the change touches). " + _PATH_RULE + " Give each a short plain-English "
    "label; derive it ONLY from THIS commit's own paths and diff — never a generic placeholder. "
    "Prefer the concrete capability over generic surface words (System, Options, Update, Handling). "
    "The label is NOT the commit prefix and NOT a file name. Assign every listed file to exactly "
    "one concern. If the whole commit serves one purpose, return a single concern with all files. "
    "Also give each concern a one-sentence summary of WHAT the change does, grounded in the diff. "
    "Respond with ONE JSON object."
)
# --- message-routed prompt: normalise a single-purpose commit's subject into a clean label ---
MSG_SYS = (
    "You name the single CONCERN a small, single-purpose commit implements, from its subject, body "
    "and the FULL PATHS of the files it changed. " + _PATH_RULE + " Output ONE short plain-English "
    "label for the subsystem + capability — NOT the commit prefix (feat/fix/…), NOT a file name, "
    "NOT a location-free surface word. Keep the concrete meaning of the subject. "
    "Respond with ONE JSON object: {\"concerns\":[{\"label\":\"short capability phrase\"}]}."
)
SCHEMA = ('Return JSON: {"concerns":[{"label":"short capability phrase",'
          '"summary":"one sentence: what this change does","files":["path", ...]}]}')

# --- overflow (bulk-commit) path: benchmarked on void-queue mega commits -------------------
# Files beyond the diff-read cap must not be silently dropped (a 2,000-file initial import is
# the founding evidence of every original feature). Recipe, every part measured:
#   move-pair filter -> vendored-drop filter -> stem-family partition -> code-peek labels.
# Never group by directory (blob dirs hold whole engines); never label from names alone
# (name-priors poison ground); an inconclusive peek yields NO label, not a guess.
PEEK_SYS = (
    "You identify what parts of ONE software project ARE, from code excerpts. For each "
    "numbered FILE FAMILY (files added together in one bulk commit) you get the head of its "
    "most declarative file. Name the feature/subsystem/library the family constitutes and "
    "give a one-sentence concrete definition derived from the CODE (declarations, tokens, "
    "includes) — never from the file name alone. If the code is inconclusive, use the name "
    '"inconclusive". '
    'Respond with ONE JSON object: {"labels":{"<n>":{"name":"...","definition":"..."}}}'
)

_VENDOR_MIN_FILES = 50       # a foreign drop is a sizeable subtree ...
_VENDOR_MAX_OVERLAP = 0.2    # ... whose extensions barely overlap the repo's own profile
_FAMILY_MIN = 2              # smallest stem family that gets its own concern
_PEEK_BATCH = 5              # families per peek call
_PEEK_CHARS = 900            # head bytes per representative


def _split_moves(rows):
    """Move-pair filter (per file): an added file pairing a deleted file with the same
    basename AND same line count is a move under --no-renames — no new semantics."""
    added = [r for r in rows if r["deletions"] == 0 and r["insertions"] > 0]
    deleted = [r for r in rows if r["insertions"] == 0 and r["deletions"] > 0]
    modified = [r for r in rows if r["insertions"] > 0 and r["deletions"] > 0]
    del_sig = Counter((r["path"].rsplit("/", 1)[-1], r["deletions"]) for r in deleted)
    real_adds, moves = [], 0
    for r in added:
        sig = (r["path"].rsplit("/", 1)[-1], r["insertions"])
        if del_sig.get(sig, 0) > 0:
            del_sig[sig] -= 1
            moves += 1
        else:
            real_adds.append(r)
    return moves, [r["path"] for r in real_adds], [r["path"] for r in modified]


def _vendor_roots(paths, repo_exts):
    """Foreign drops: sizeable subtrees whose extension profile is alien to the repo."""
    by_root: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        parts = p.split("/")
        by_root["/".join(parts[:2]) if len(parts) > 2 else "(root)"].append(p)
    vendored = {}
    for root, fs in by_root.items():
        if len(fs) < _VENDOR_MIN_FILES:
            continue
        exts = Counter(f.rsplit(".", 1)[-1].lower() for f in fs if "." in f)
        total = sum(exts.values()) or 1
        overlap = sum(n for e, n in exts.items() if e in repo_exts) / total
        if overlap < _VENDOR_MAX_OVERLAP:
            vendored[root] = fs
    return vendored


def _stem_families(paths):
    """Partition by rarest shared stem (>=2 files); leftover files form no family.
    Scaffold families are routed to leftover: build scaffolding repeats one-file-per-
    directory across the tree (Makefile x12 dirs, precompiled headers everywhere), while
    a real feature's files cluster in one or two directories — dispersion tells them
    apart with no name stoplist."""
    from ..taxonomy.ground import path_stems
    stems_of = {p: path_stems(p) for p in paths}
    freq = Counter(s for st in stems_of.values() for s in st)
    fam: dict[str, list[str]] = defaultdict(list)
    leftover = []
    for p in paths:
        cands = sorted((s for s in stems_of[p] if freq[s] >= _FAMILY_MIN),
                       key=lambda s: (freq[s], -len(s), s))
        if cands:
            fam[cands[0]].append(p)
        else:
            leftover.append(p)
    out = {}
    for s, fs in fam.items():
        if len(fs) < _FAMILY_MIN:
            leftover += fs          # a size-1 bucket must fall back, never vanish
            continue
        dirs = {f.rsplit("/", 1)[0] if "/" in f else "" for f in fs}
        if len(fs) >= 6 and len(dirs) / len(fs) >= 0.8:   # scaffold dispersion signature
            leftover += fs
            continue
        out[s] = fs
    return out, leftover


def _representative(repo, h, stem, files, reader=None):
    """The family's most declarative member: must CARRY the family stem (a bundled foreign
    header can never define the family), prefer headers, break ties by how often the family
    itself includes the file."""
    from ..taxonomy.ground import path_stems
    from ..taxonomy.imports import family_include_counts
    carriers = [f for f in files if stem in path_stems(f)] or files
    inc = (family_include_counts(repo, h, files, reader=reader)
           if len(files) > 2 else Counter())

    def rank(f):
        header = f.rsplit(".", 1)[-1].lower() in ("h", "hpp", "hh", "pyi")
        return (not header, -inc.get(f, 0), len(f))
    return sorted(carriers, key=rank)[0]


def _overflow_concerns(provider, repo, h, rows, repo_exts):
    """Overflow files of one bulk commit -> concerns. Returns list of dicts with 'origin'."""
    moves, adds, modified = _split_moves(rows)
    work = adds + modified
    out = []
    vendored = _vendor_roots(adds, repo_exts)
    vfiles = {f for fs in vendored.values() for f in fs}
    for root, fs in vendored.items():
        out.append({"label": f"vendored drop: {root}", "origin": "import-misc",
                    "summary": f"third-party/vendored subtree of {len(fs)} files under {root}/",
                    "files": fs})
    work = [p for p in work if p not in vfiles]
    fams, leftover = _stem_families(work)

    from ..extract.git_ingest import BatchReader
    reader = BatchReader(repo)
    try:
        items = sorted(fams.items())
        for i in range(0, len(items), _PEEK_BATCH):
            part = items[i:i + _PEEK_BATCH]
            blocks = []
            for j, (s, fs) in enumerate(part):
                rep = _representative(repo, h, s, fs, reader=reader)
                head = reader.read(h, rep, limit=_PEEK_CHARS)
                blocks.append(f"[{j}] family '{s}' ({rep.rsplit('/', 1)[-1]}):\n{head}")
            try:
                got = provider.chat(PEEK_SYS, "\n\n".join(blocks), want_json=True,
                                    cache_extra=f"upeek:{h}:{i}")
            except Exception:  # noqa: BLE001
                got = {}
            labels = got.get("labels", {}) if isinstance(got, dict) else {}
            for j, (s, fs) in enumerate(part):
                r = labels.get(str(j)) or {}
                name = str((r.get("name") if isinstance(r, dict) else "") or "").strip()[:80]
                if name and name.lower() != "inconclusive":
                    out.append({"label": name, "origin": "import",
                                "summary": str(r.get("definition") or "").strip()[:300],
                                "files": fs})
                else:
                    leftover += fs
    finally:
        reader.close()
    if leftover:
        out.append({"label": "bulk change remainder", "origin": "import-misc",
                    "summary": f"{len(leftover)} bulk-changed files without a peek-conclusive "
                               f"family ({moves} moved files excluded)",
                    "files": leftover})
    return out

_CONV = re.compile(r"\b(feat|fix|refactor|chore|docs|style|perf|test|build|ci)\b", re.I)


def _looks_multitopic(subject: str) -> bool:
    """Subject that itself lists several topics — a tangle only the diff can split."""
    s = subject or ""
    if len(_CONV.findall(s)) >= 2:            # two conventional prefixes → two topics
        return True
    if " + " in s:                            # "add X + fix Y"
        return True
    if s.lower().count(" - ") >= 2:           # a bulleted list of changes
        return True
    return False


def _route_to_diff(subject: str, n_files: int, max_msg_files: int, min_subject_len: int) -> bool:
    """True → this commit needs the DIFF; False → its message is enough."""
    s = (subject or "").strip()
    if n_files > max_msg_files:               # many files → likely multi-concern
        return True
    if len(s) < min_subject_len:              # thin subject → untrustworthy
        return True
    if _looks_multitopic(s):                  # subject lists multiple topics
        return True
    return False


def _cap_diff(raw: str, max_lines: int, max_line_chars: int, max_diff_chars: int) -> str:
    """Bound a diff by lines, per-line length (minified/data), and total chars."""
    out = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        if len(ln) > max_line_chars:
            ln = ln[:max_line_chars] + " …[truncated]"
        out.append(ln)
        if len(out) >= max_lines:
            break
    diff = "\n".join(out)
    if len(diff) > max_diff_chars:
        diff = diff[:max_diff_chars] + "\n…[diff truncated]"
    return diff


def _coerce(out, files, fallback_label):
    fileset = set(files)
    concerns, assigned = [], set()
    raw = out.get("concerns") if isinstance(out, dict) else None
    if isinstance(raw, list):
        for cc in raw:
            if not isinstance(cc, dict):
                continue
            label = (cc.get("label") or "").strip()[:80]
            summary = (cc.get("summary") or "").strip()[:300]
            matched = []
            for f in (cc.get("files") or []):
                if not isinstance(f, str):
                    continue
                if f in fileset and f not in assigned:
                    matched.append(f)
                else:
                    base = f.rsplit("/", 1)[-1]
                    for af in files:
                        if af.rsplit("/", 1)[-1] == base and af not in assigned and af not in matched:
                            matched.append(af)
                            break
            # message route returns no files (single concern) — take them all
            if label and not matched and len(raw) == 1:
                matched = [f for f in files if f not in assigned]
            if label and matched:
                concerns.append({"label": label, "summary": summary, "files": matched})
                assigned.update(matched)
    leftover = [f for f in files if f not in assigned]
    if leftover:
        if concerns:
            concerns[0]["files"] += leftover
        else:
            concerns.append({"label": fallback_label, "summary": "", "files": leftover})
    return concerns


def _infer_diff(provider, repo, h, subject, files, caps):
    """DIFF route: read the (bounded) code diff and split into concerns."""
    raw = run_git(repo, ["show", "--no-color", "--format=", "--unified=1", h, "--", *files],
                  check=False)
    diff = _cap_diff(raw, caps["max_diff_lines"], caps["max_line_chars"], caps["max_diff_chars"])
    user = (f"Commit subject (UNRELIABLE hint): {(subject or '')[:120]}\n"
            f"Changed files:\n" + "\n".join(f"  {f}" for f in files) + "\n\n"
            f"DIFF:\n{diff or '(empty)'}\n\n{SCHEMA}")
    try:
        return provider.chat(DIFF_SYS, user, want_json=True, cache_extra=f"udiff:{h}")
    except Exception:  # noqa: BLE001
        return {}


def _infer_msg(provider, repo, h, subject, body, files):
    """MESSAGE route: normalise the subject into one concern label (no diff — cheap). Full file
    PATHS (not basenames) are passed so the label can be grounded in the subsystem/directory."""
    user = (f"Subject: {(subject or '')[:200]}\n"
            f"Body: {(body or '')[:300] or '(none)'}\n"
            f"Changed file paths:\n" + "\n".join(f"  {f}" for f in files[:14]) + "\n\n"
            'Return JSON: {"concerns":[{"label":"short capability phrase"}]}')
    try:
        return provider.chat(MSG_SYS, user, want_json=True, cache_extra=f"umsg:{h}")
    except Exception:  # noqa: BLE001
        return {}


def untangle(conn, provider, repo: str, log=print, force: bool = False,
             max_diff_lines: int = 180, max_files: int = 40, workers: int = 8,
             max_line_chars: int = 300, max_diff_chars: int = 6000,
             max_msg_files: int = 4, min_subject_len: int = 20, scope=None) -> dict:
    if scope is None:
        from ..scope import scope_or_default
        scope = scope_or_default(log=log)
    caps = {"max_diff_lines": max_diff_lines, "max_line_chars": max_line_chars,
            "max_diff_chars": max_diff_chars}
    kinds = {r["hash"]: r["kind"] for r in conn.execute("SELECT hash, kind FROM commits")}
    if force:
        conn.execute("DELETE FROM concerns")
        conn.commit()
    # Skip merge commits: with full-ancestry traversal the merged branch's individual commits are
    # already present and carry the granular content — the merge's diff would just double-count them.
    todo = conn.execute(
        "SELECT c.hash, c.subject, c.body FROM commits c WHERE c.is_merge=0 "
        "AND NOT EXISTS (SELECT 1 FROM concerns cn WHERE cn.commit_hash=c.hash) "
        "ORDER BY c.authored_at").fetchall()
    if not todo:
        log("  all commits already untangled")
        return {"untangled": 0, "concerns": 0}

    # Pre-fetch each commit's (non-vendored) files once, in the main thread — with per-file
    # churn, which the overflow path's move-pair filter needs.
    rows_by: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
            "SELECT commit_hash, path, insertions, deletions FROM commit_files ORDER BY id"):
        if scope(r["path"]):
            rows_by[r["commit_hash"]].append({"path": r["path"],
                                              "insertions": r["insertions"] or 0,
                                              "deletions": r["deletions"] or 0})
    files_by = {h: [x["path"] for x in v] for h, v in rows_by.items()}
    fileset = {r["hash"]: files_by.get(r["hash"], [])[:max_files] for r in todo}
    overflow_by = {r["hash"]: rows_by.get(r["hash"], [])[max_files:] for r in todo}
    # the repo's own extension profile — the vendored-drop filter's baseline
    extc = Counter(p.rsplit(".", 1)[-1].lower()
                   for v in files_by.values() for p in v if "." in p)
    repo_exts = {e for e, _ in extc.most_common(12)}
    # Route each commit: message (cheap) vs diff (escalation).
    route = {r["hash"]: _route_to_diff(r["subject"], len(fileset[r["hash"]]),
                                       max_msg_files, min_subject_len) for r in todo}
    n_diff = sum(1 for r in todo if fileset[r["hash"]] and route[r["hash"]])
    n_msg = sum(1 for r in todo if fileset[r["hash"]] and not route[r["hash"]])
    log(f"  untangling {len(todo)} commits ({workers} workers): "
        f"{n_msg} message-routed (cheap), {n_diff} diff-routed (escalated) ...")

    def work(r):
        h, files = r["hash"], fileset[r["hash"]]
        if route[h]:
            out = _infer_diff(provider, repo, h, r["subject"], files, caps)
        else:
            out = _infer_msg(provider, repo, h, r["subject"], r["body"], files)
        # bulk-commit overflow: files beyond the cap become stem-family concerns
        extra = (_overflow_concerns(provider, repo, h, overflow_by[h], repo_exts)
                 if overflow_by.get(h) else [])
        return out, extra

    results: dict[str, object] = {}
    fetched = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for r in todo:
            if not fileset[r["hash"]]:
                results[r["hash"]] = ({}, [])
                continue
            futs[ex.submit(work, r)] = r["hash"]
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
            fetched += 1
            if fetched % 200 == 0 or fetched == len(futs):
                log(f"    {fetched}/{len(futs)} inferred")

    # Insert in deterministic todo (authored_at) order so concern ids are reproducible.
    done = nconc = nimp = 0
    for r in todo:
        h = r["hash"]
        files = fileset[h]
        if not files:
            mark_stage(conn, "untangle", h)
            continue
        out, extra = results.get(h) or ({}, [])
        for cc in _coerce(out, files, (r["subject"] or "change")[:80]):
            # msg-routed commits get no LLM summary; the subject is the change's one-liner.
            summary = cc["summary"] or (None if route[h] else (r["subject"] or "").strip()[:300])
            conn.execute("INSERT INTO concerns (commit_hash, label, summary, files, kind) "
                         "VALUES (?,?,?,?,?)",
                         (h, cc["label"], summary, json.dumps(cc["files"]), kinds.get(h)))
            nconc += 1
        for cc in extra:
            conn.execute("INSERT INTO concerns (commit_hash, label, summary, files, kind, "
                         "origin) VALUES (?,?,?,?,?,?)",
                         (h, cc["label"], cc["summary"], json.dumps(cc["files"]),
                          kinds.get(h), cc["origin"]))
            nconc += 1
            nimp += cc["origin"] == "import"
        mark_stage(conn, "untangle", h)
        done += 1
        if done % 500 == 0 or done == len(todo):
            conn.commit()
    conn.commit()
    log(f"  {nconc} concerns from {done} commits (avg {nconc / max(done, 1):.1f}/commit); "
        f"routed {n_msg} message / {n_diff} diff; {nimp} peek-labelled import families")
    return {"untangled": done, "concerns": nconc, "msg_routed": n_msg, "diff_routed": n_diff,
            "import_families": nimp}
