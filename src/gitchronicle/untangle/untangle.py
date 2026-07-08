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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..enrich.signals import is_vendored
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
SCHEMA = 'Return JSON: {"concerns":[{"label":"short capability phrase","files":["path", ...]}]}'

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
                concerns.append({"label": label, "files": matched})
                assigned.update(matched)
    leftover = [f for f in files if f not in assigned]
    if leftover:
        if concerns:
            concerns[0]["files"] += leftover
        else:
            concerns.append({"label": fallback_label, "files": leftover})
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
             max_msg_files: int = 4, min_subject_len: int = 20) -> dict:
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

    # Pre-fetch each commit's (non-vendored) files once, in the main thread.
    files_by: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT commit_hash, path FROM commit_files ORDER BY id"):
        if not is_vendored(r["path"]):
            files_by[r["commit_hash"]].append(r["path"])
    fileset = {r["hash"]: files_by.get(r["hash"], [])[:max_files] for r in todo}
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
            return _infer_diff(provider, repo, h, r["subject"], files, caps)
        return _infer_msg(provider, repo, h, r["subject"], r["body"], files)

    results: dict[str, object] = {}
    fetched = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for r in todo:
            if not fileset[r["hash"]]:
                results[r["hash"]] = {}
                continue
            futs[ex.submit(work, r)] = r["hash"]
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
            fetched += 1
            if fetched % 200 == 0 or fetched == len(futs):
                log(f"    {fetched}/{len(futs)} inferred")

    # Insert in deterministic todo (authored_at) order so concern ids are reproducible.
    done = nconc = 0
    for r in todo:
        h = r["hash"]
        files = fileset[h]
        if not files:
            mark_stage(conn, "untangle", h)
            continue
        out = results.get(h) or {}
        for cc in _coerce(out, files, (r["subject"] or "change")[:80]):
            conn.execute("INSERT INTO concerns (commit_hash, label, files, kind) VALUES (?,?,?,?)",
                         (h, cc["label"], json.dumps(cc["files"]), kinds.get(h)))
            nconc += 1
        mark_stage(conn, "untangle", h)
        done += 1
        if done % 500 == 0 or done == len(todo):
            conn.commit()
    conn.commit()
    log(f"  {nconc} concerns from {done} commits (avg {nconc / max(done, 1):.1f}/commit); "
        f"routed {n_msg} message / {n_diff} diff")
    return {"untangled": done, "concerns": nconc, "msg_routed": n_msg, "diff_routed": n_diff}
