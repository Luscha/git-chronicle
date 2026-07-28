"""Build the narrative chronicle (opt-in): the evolution STORY, told as commit-anchored
chapters, for each domain and for the repository as a whole.

Chapters come from LIGHT temporal clustering of a domain's commits (new chapter on a
>~14-day gap or after ~12 commits) — near-in-time commits become one chapter, not a
1:1 trace. A small model narrates each chapter from its commit messages + a
representative diff; a final small-model call distills the domain's "what" summary
from the chapters. The repository chronicle segments the whole timeline into periods
and the big model narrates each. No configuration; resumable via the LLM cache.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime

from ..extract.git_ingest import run_git
from ..storage import now_iso

_GAP_DAYS = 14
_MAX_COMMITS = 12

CHAP_SYS = (
    "You are writing the evolution STORY of one code domain. You get a time window of its "
    "work items (per-commit concerns untangled from the diffs: label + what changed), the "
    "key files touched, and possibly a representative diff. Write that chapter of the "
    "domain's history: concrete, specific, past tense, grounded ONLY in the evidence. Name "
    "the actual mechanisms, subsystems and files that changed. NEVER pad with filler like "
    "'various improvements', 'several changes', 'enhancements were made' — if the evidence "
    "is thin, write one short precise sentence instead. Respond with ONE JSON object.")

DISTILL_SYS = (
    "Given a domain's name and its evolution story, write WHAT this domain IS — its role and "
    "purpose in the system — in 1-2 present-tense sentences. Respond with ONE JSON object: "
    '{"summary":"..."}.')

REPO_SYS = (
    "You are writing the evolution STORY of a whole software project for one time period. "
    "Given which areas (domains) were active and notable commits, narrate what the project "
    "focused on and what advanced in that period. Concrete, past tense. Respond with ONE JSON "
    'object: {"title":"...","narrative":"2-4 sentences"}.')


def _epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def _domain_commits(conn, domain_id):
    rows = conn.execute(
        "SELECT c.hash, c.subject, c.body, c.authored_at FROM commit_domains cd "
        "JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=? AND c.is_merge=0 "
        "ORDER BY c.authored_at ASC", (domain_id,)).fetchall()
    return [{"hash": r["hash"], "subject": r["subject"] or "", "body": (r["body"] or "").strip(),
             "date": r["authored_at"] or "", "t": _epoch(r["authored_at"])} for r in rows]


def _domain_concerns(conn, domain_id) -> dict:
    """commit hash -> this domain's own work items (concern label + summary): the
    per-domain slice of each commit. Narrating from these instead of raw subjects
    keeps multi-feature commits from leaking other features' text into the story."""
    out: dict = defaultdict(list)
    for r in conn.execute(
            "SELECT commit_hash, label, summary FROM concerns WHERE domain_id=?",
            (domain_id,)):
        lab = (r["label"] or "").strip()
        summ = (r["summary"] or "").strip()
        if lab and summ and summ.lower() != lab.lower():
            out[r["commit_hash"]].append(f"{lab}: {summ[:220]}")
        elif lab or summ:
            out[r["commit_hash"]].append((lab or summ)[:220])
    return out


def _chapter_files(conn, domain_id, hashes, cap=8) -> list[str]:
    """The chapter's territory files, by touch count — concrete anchors for the story."""
    qmarks = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT cf.path, COUNT(*) n FROM commit_files cf "
        f"JOIN domain_files df ON df.path = cf.path AND df.domain_id = ? "
        f"WHERE cf.commit_hash IN ({qmarks}) GROUP BY cf.path ORDER BY n DESC, cf.path "
        f"LIMIT ?", (domain_id, *hashes, cap)).fetchall()
    return [r["path"] for r in rows]


def _cluster(commits):
    """Light temporal chaptering: split on a big time gap or after too many commits."""
    chapters, cur = [], []
    for c in commits:
        if cur and (c["t"] - cur[-1]["t"] > _GAP_DAYS * 86400 or len(cur) >= _MAX_COMMITS):
            chapters.append(cur)
            cur = []
        cur.append(c)
    if cur:
        chapters.append(cur)
    return chapters


def _diff_of(conn, repo, domain_id, hashes, max_lines=50):
    files = [r["path"] for r in conn.execute(
        "SELECT path FROM domain_files WHERE domain_id=? ORDER BY weight DESC LIMIT 30", (domain_id,))]
    for h in hashes:
        raw = run_git(repo, ["show", "--no-color", "--format=", "--unified=1", h, "--", *files[:30]],
                      check=False)
        lines = [ln for ln in raw.splitlines() if ln.strip()][:max_lines]
        if lines:
            return "\n".join(lines)
    return ""


def _period_key(iso, gran):
    if gran == "year":
        return iso[:4]
    if gran == "quarter":
        return f"{iso[:4]}-Q{(int(iso[5:7]) - 1) // 3 + 1}"
    return iso[:7]


def _months_between(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))


def _repo_chronicle(conn, provider, log, force):
    if not force and conn.execute(
            "SELECT 1 FROM evolution_chapters WHERE target_type='repo' LIMIT 1").fetchone():
        return
    conn.execute("DELETE FROM evolution_chapters WHERE target_type='repo'")
    commits = conn.execute(
        "SELECT hash, authored_at, author_name, subject FROM commits WHERE is_merge=0 "
        "AND authored_at IS NOT NULL ORDER BY authored_at ASC").fetchall()
    if not commits:
        return
    span = _months_between(commits[0]["authored_at"], commits[-1]["authored_at"])
    gran = "year" if span > 72 else ("quarter" if span > 18 else "month")
    buckets = defaultdict(list)
    for c in commits:
        buckets[_period_key(c["authored_at"], gran)].append(c)
    keys = sorted(buckets)
    dom_name = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM domains")}
    log(f"  repo chronicle: {len(keys)} periods ({gran}) ...")
    for seq, k in enumerate(keys):
        cs = buckets[k]
        hashes = [c["hash"] for c in cs][:400]
        qm = ",".join("?" * len(hashes))
        active = Counter()
        for r in conn.execute(
                f"SELECT domain_id, COUNT(*) n FROM commit_domains WHERE commit_hash IN ({qm}) "
                "GROUP BY domain_id ORDER BY n DESC", hashes):
            active[dom_name.get(r["domain_id"], "?")] = r["n"]
        authors = Counter(c["author_name"] for c in cs if c["author_name"])
        sample = "\n".join(f"- {c['subject']}" for c in cs[:20])
        user = (f"Project period: {k} ({len(cs)} commits)\n"
                f"Active areas: {', '.join(f'{n}({c})' for n, c in active.most_common(8)) or 'n/a'}\n"
                f"Top authors: {', '.join(a for a, _ in authors.most_common(5))}\n"
                f"Sample commits:\n{sample}\n\nReturn "
                '{"title":"...","narrative":"2-4 sentences"}')
        try:
            r = provider.chat(REPO_SYS, user, want_json=True, large=True, cache_extra=f"repo:{k}:{len(cs)}")
        except Exception as exc:  # noqa: BLE001
            log(f"    repo period {k} failed ({exc})")
            r = {}
        r = r if isinstance(r, dict) else {}
        conn.execute(
            "INSERT INTO evolution_chapters (target_type, target_id, seq, period_start, period_end, "
            "title, narrative, commit_hashes, created_at) VALUES ('repo', NULL, ?,?,?,?,?,?,?)",
            (seq, cs[0]["authored_at"], cs[-1]["authored_at"], (r.get("title") or k)[:80],
             (r.get("narrative") or "").strip(), json.dumps([c["hash"][:10] for c in cs[:20]]), now_iso()))
    conn.commit()


def chronicle(conn, provider, repo: str, log=print, force: bool = False) -> dict:
    doms = conn.execute(
        "SELECT id, name FROM domains WHERE status != 'rejected' ORDER BY n_commits DESC").fetchall()
    log(f"  chronicling {len(doms)} domains ...")
    done = 0
    for d in doms:
        did = d["id"]
        if not force and conn.execute(
                "SELECT 1 FROM evolution_chapters WHERE target_type='domain' AND target_id=? LIMIT 1",
                (str(did),)).fetchone():
            continue
        commits = _domain_commits(conn, did)
        if not commits:
            continue
        concerns = _domain_concerns(conn, did)
        chapters = _cluster(commits)
        conn.execute("DELETE FROM evolution_chapters WHERE target_type='domain' AND target_id=?", (str(did),))
        narratives = []
        for seq, ch in enumerate(chapters):
            if len(ch) == 1:
                # one commit needs no narration — its own concern (or message) IS
                # the chapter, and the concern is already this domain's slice
                c0 = ch[0]
                body0 = "; ".join(concerns.get(c0["hash"], [])) \
                    or (c0["body"].splitlines() or [""])[0][:240]
                conn.execute(
                    "INSERT INTO evolution_chapters (target_type, target_id, seq, period_start, "
                    "period_end, title, narrative, commit_hashes, created_at) "
                    "VALUES ('domain', ?,?,?,?,?,?,?,?)",
                    (str(did), seq, c0["date"], c0["date"], c0["subject"][:80],
                     body0[:400], json.dumps([c0["hash"][:10]]), now_iso()))
                narratives.append(body0 or c0["subject"])
                continue
            # evidence: this domain's own work items; raw subject only as fallback
            lines = []
            for c in ch[:14]:
                own = concerns.get(c["hash"])
                if own:
                    lines += [f"- {w}" for w in own[:2]]
                else:
                    lines.append(f"- {c['subject'][:160]}")
            subj = "\n".join(lines)
            files = _chapter_files(conn, did, [c["hash"] for c in ch])
            diff = (_diff_of(conn, repo, did, [c["hash"] for c in ch])
                    if len(ch) <= 4 else "")
            want = "4-7 sentences" if len(ch) >= 5 else "2-4 sentences"
            user = (f"Domain: {d['name']}\nPeriod: {ch[0]['date'][:10]} .. {ch[-1]['date'][:10]} "
                    f"({len(ch)} commits)\nWork items:\n{subj}\n\nKey files touched:\n"
                    + "\n".join(f"  {f}" for f in files)
                    + f"\n\nRepresentative diff:\n{diff or '(none)'}"
                    + '\n\nReturn JSON: {"title":"<=6 word period title",'
                    + f'"narrative":"the story, {want}"}}')
            try:
                r = provider.chat(CHAP_SYS, user, want_json=True,
                                  cache_extra=f"chap:{did}:{ch[0]['hash']}:{ch[-1]['hash']}:{len(ch)}")
            except Exception as exc:  # noqa: BLE001
                log(f"    chapter {did}.{seq} failed ({exc})")
                r = {}
            r = r if isinstance(r, dict) else {}
            narr = (r.get("narrative") or "").strip()
            narratives.append(narr)
            conn.execute(
                "INSERT INTO evolution_chapters (target_type, target_id, seq, period_start, period_end, "
                "title, narrative, commit_hashes, created_at) VALUES ('domain', ?,?,?,?,?,?,?,?)",
                (str(did), seq, ch[0]["date"], ch[-1]["date"], (r.get("title") or "").strip()[:80],
                 narr, json.dumps([c["hash"][:10] for c in ch]), now_iso()))
        # distill the domain's "what" from its story (a one-commit feature has no arc
        # to distill — its register definition already says what it is)
        story = "\n".join(f"- {n}" for n in narratives if n)
        if len(commits) == 1:
            conn.commit()
            done += 1
            continue
        try:
            r = provider.chat(DISTILL_SYS, f"Domain: {d['name']}\nEvolution:\n{story}\n\n"
                              'Return {"summary":"..."}', want_json=True, cache_extra=f"distill:{did}:{len(narratives)}")
            summary = ((r.get("summary") if isinstance(r, dict) else "") or "").strip()
            if summary:
                conn.execute("UPDATE domains SET summary=? WHERE id=? AND status!='confirmed' AND locked=0",
                             (summary, did))
        except Exception as exc:  # noqa: BLE001
            log(f"    distill {did} failed ({exc})")
        conn.commit()
        done += 1
        if done % 5 == 0 or done == len(doms):
            log(f"    {done}/{len(doms)} chronicled")

    _repo_chronicle(conn, provider, log, force)
    n_chap = conn.execute("SELECT COUNT(*) FROM evolution_chapters").fetchone()[0]
    log(f"  {n_chap} chapters total (domain + repo)")
    return {"domains_chronicled": done, "chapters": n_chap}
