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
from ..scope import Direction
from ..llm.provider import NotCached
from ..storage import now_iso

_GAP_DAYS = 14
_MAX_COMMITS = 12
_DORMANT_DAYS = 120    # a gap this long is the story pausing, not a weekend
_UPKEEP_SHARE = 0.75   # a span this routine is one "kept it running" chapter, not many
_MAX_UPKEEP = 40       # ... but never so long that it stops being readable

_CHAP_SYS_BASE = (
    "You are writing the evolution STORY of one code domain. You get a time window of its "
    "work items (per-commit concerns untangled from the diffs: label + what changed), the "
    "key files touched, and possibly a representative diff. Write that chapter of the "
    "domain's history: concrete, specific, past tense, grounded ONLY in the evidence. Name "
    "the actual mechanisms, subsystems and files that changed. NEVER pad with filler like "
    "'various improvements', 'several changes', 'enhancements were made' — if the evidence "
    "is thin, write one short precise sentence instead. Respond with ONE JSON object.")
CHAP_SYS = _CHAP_SYS_BASE
_DIR_KEY = ""

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
        "SELECT c.hash, c.subject, c.body, c.authored_at, "
        # growth = a file of this domain that gained lines and lost none. numstat reports
        # a newly added file exactly so, and change_type is never populated by ingest.
        "  EXISTS (SELECT 1 FROM commit_files cf JOIN domain_files df "
        "          ON df.path = cf.path AND df.domain_id = cd.domain_id "
        "          WHERE cf.commit_hash = c.hash "
        "            AND cf.deletions = 0 AND cf.insertions > 0) AS added "
        "FROM commit_domains cd "
        "JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=? AND c.is_merge=0 "
        "ORDER BY c.authored_at ASC", (domain_id,)).fetchall()
    return [{"hash": r["hash"], "subject": r["subject"] or "", "body": (r["body"] or "").strip(),
             "date": r["authored_at"] or "", "t": _epoch(r["authored_at"]),
             "added": bool(r["added"])} for r in rows]


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
    """Chapter on ARCS, not on the calendar.

    Cutting purely on a 14-day gap produced a chapter per burst of work, so a feature that
    ships a content update every month got a chapter every month: Battle Pass came out as
    fourteen chapters, several of them nothing but a restated commit subject. Its actual
    story is four arcs -- built in 2021, removed months later, revived in 2024, then a
    long content-maintenance era.

    An arc ends where the story genuinely turns: a long dormancy (the feature stopped and
    later came back), or a change of mode between building and maintaining. Runs of
    routine upkeep collapse into ONE chapter however long they last, because "kept
    working for two years" is one fact, not twenty-four.
    """
    # 1. cut only where the work genuinely stopped. Cutting on every build/upkeep
    #    alternation was tried and is worse than the calendar it replaced -- the two kinds
    #    interleave commit by commit, so it split Battle Pass into eighteen.
    spans, cur = [], []
    for c in commits:
        if cur and (c["t"] - cur[-1]["t"]) / 86400 > _DORMANT_DAYS:
            spans.append(cur)
            cur = []
        cur.append(c)
    if cur:
        spans.append(cur)

    # 2. a span that is mostly upkeep is ONE chapter however long it ran; a span of real
    #    building is chaptered at reading length.
    chapters = []
    for span in spans:
        upkeep = sum(_mode(c) == "upkeep" for c in span)
        step = _MAX_UPKEEP if upkeep >= _UPKEEP_SHARE * len(span) else _MAX_COMMITS
        chapters += [span[i:i + step] for i in range(0, len(span), step)]
    return chapters


def _mode(commit) -> str:
    """Is this commit building the thing, or keeping it running?

    Structure, not vocabulary: a commit where the domain gained code without losing any is
    building it; one that only rewrites existing lines is maintaining it. Reading the
    subject was tried first and is too loose to be useful -- 'fix' appears in most messages
    in this corpus, so a two-year build era scored 60% upkeep and collapsed into a single
    unreadable chapter. Whether new code appeared is a fact; what the message called it is
    a habit.
    """
    return "build" if commit.get("added") else "upkeep"


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
        except NotCached:
            raise
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



def _chapter_prompt(conn, repo, did, name, ch, concerns) -> tuple[str, str]:
    """The chapter's prompt and its cache key.

    Shared by the prefetch and the write pass so the two can never drift: a prefetch that
    built a different prompt would warm the wrong cache entry and silently buy nothing.

    The key names the DOMAIN, not its row id. Ids are assigned by insertion order, so any
    change to the catalogue renumbers them and every chapter misses — one rename pass
    re-paid ~470 narrations that were already in the cache. The name plus the chapter's
    own commit range identifies the same work across rebuilds, which is the same reason
    the ledger keys on names.
    """
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
    diff = _diff_of(conn, repo, did, [c["hash"] for c in ch]) if len(ch) <= 4 else ""
    want = "4-7 sentences" if len(ch) >= 5 else "2-4 sentences"
    user = (f"Domain: {name}\nPeriod: {ch[0]['date'][:10]} .. {ch[-1]['date'][:10]} "
            f"({len(ch)} commits)\nWork items:\n{subj}\n\nKey files touched:\n"
            + "\n".join(f"  {f}" for f in files)
            + f"\n\nRepresentative diff:\n{diff or '(none)'}"
            + '\n\nReturn JSON: {"title":"<=6 word period title",'
            + f'"narrative":"the story, {want}"}}')
    # the direction only enters the key when there IS one: appending an empty suffix still
    # changes the key, which silently re-paid 482 cached narrations the first time
    suffix = f":{_DIR_KEY}" if _DIR_KEY else ""
    return user, f"chap:{name}:{ch[0]['hash']}:{ch[-1]['hash']}:{len(ch)}{suffix}"


def _prefetch(conn, provider, repo, doms, workers, log) -> None:
    """Warm the cache in parallel, then let the sequential pass read it.

    The narration calls are independent, but the WRITES are not: chapter rows carry a seq
    and the distil step reads the narratives in order. Rather than make the writer
    concurrent and inherit that ordering problem, this issues the same calls with the same
    cache keys first — so the real pass runs unchanged, at cache speed. 850 sequential
    calls is hours; the work itself is a few minutes.
    """
    from concurrent.futures import ThreadPoolExecutor

    jobs = []
    for d in doms:
        did = d["id"]
        commits = _domain_commits(conn, did)
        if not commits:
            continue
        concerns = _domain_concerns(conn, did)
        for ch in _cluster(commits):
            if len(ch) > 1:
                jobs.append(_chapter_prompt(conn, repo, did, d["name"], ch, concerns))
    if not jobs:
        return
    log(f"    warming {len(jobs)} chapter narrations ({workers} workers) ...")
    done = [0]

    def run(job):
        user, key = job
        try:
            provider.chat(CHAP_SYS, user, want_json=True, cache_extra=key)
        except Exception:      # a failure here is retried by the sequential pass
            pass
        done[0] += 1
        if done[0] % 50 == 0:
            log(f"      {done[0]}/{len(jobs)}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run, jobs))


def chronicle(conn, provider, repo: str, log=print, force: bool = False,
              workers: int = 8, cached_only: bool = False) -> dict:
    """Narrate every entry's evolution.

    ``cached_only`` narrates only what the cache already holds and leaves the rest for a
    paid run. The knowledge base is rebuilt on every update, so without it a plain update
    erased all 779 chapters until the next ``--chronicle``.
    """
    # voice shapes HOW the story reads, never what the evidence says — the grounding
    # rules in CHAP_SYS stay in front of it and are not overridable from the file
    direction = Direction.load()
    direction.report(log)
    global CHAP_SYS, _DIR_KEY
    CHAP_SYS = _CHAP_SYS_BASE + direction.voice_suffix()
    _DIR_KEY = direction.key()
    doms = conn.execute(
        "SELECT id, name FROM domains WHERE status != 'rejected' ORDER BY n_commits DESC").fetchall()
    log(f"  chronicling {len(doms)} domains{' (cache only)' if cached_only else ''} ...")
    if not cached_only:
        _prefetch(conn, provider, repo, doms, workers, log)
    was, provider.cache_only = provider.cache_only, cached_only
    try:
        done, pending = _narrate(conn, provider, repo, doms, force, log)
        try:
            _repo_chronicle(conn, provider, log, force)
        except NotCached:
            pass
    finally:
        provider.cache_only = was
    n_chap = conn.execute("SELECT COUNT(*) FROM evolution_chapters").fetchone()[0]
    log(f"  {n_chap} chapters total (domain + repo)")
    if pending:
        log(f"  {len(pending)} entries not narrated yet (e.g. {', '.join(pending[:4])}) — "
            f"run `gitchronicle update --chronicle`")
    return {"domains_chronicled": done, "chapters": n_chap, "pending": pending}


def _narrate(conn, provider, repo, doms, force, log) -> tuple[int, list[str]]:
    done, pending = 0, []
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
        # rows are collected first and written together: an entry is narrated whole or
        # not at all, so a cache-only run never leaves half a story behind
        rows, narratives = [], []
        try:
            for seq, ch in enumerate(_cluster(commits)):
                if len(ch) == 1:
                    # one commit needs no narration — its own concern (or message) IS
                    # the chapter, and the concern is already this domain's slice
                    c0 = ch[0]
                    own = concerns.get(c0["hash"], [])
                    body0 = "; ".join(own) or (c0["body"].splitlines() or [""])[0][:240]
                    # Title from THIS domain's concern label, not the commit subject. A
                    # single commit here usually ships several features at once, so its
                    # subject names other people's work.
                    title = (own[0].split(":", 1)[0] if own else c0["subject"])[:80]
                    rows.append((seq, c0["date"], c0["date"], title, body0[:400],
                                 [c0["hash"][:10]]))
                    narratives.append(body0 or c0["subject"])
                    continue
                user, key = _chapter_prompt(conn, repo, did, d["name"], ch, concerns)
                try:
                    r = provider.chat(CHAP_SYS, user, want_json=True, cache_extra=key)
                except NotCached:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log(f"    chapter {did}.{seq} failed ({exc})")
                    r = {}
                r = r if isinstance(r, dict) else {}
                narr = (r.get("narrative") or "").strip()
                narratives.append(narr)
                rows.append((seq, ch[0]["date"], ch[-1]["date"],
                             (r.get("title") or "").strip()[:80], narr,
                             [c["hash"][:10] for c in ch]))
        except NotCached:
            pending.append(d["name"])
            continue
        conn.execute("DELETE FROM evolution_chapters WHERE target_type='domain' AND target_id=?",
                     (str(did),))
        conn.executemany(
            "INSERT INTO evolution_chapters (target_type, target_id, seq, period_start, "
            "period_end, title, narrative, commit_hashes, created_at) "
            "VALUES ('domain', ?,?,?,?,?,?,?,?)",
            [(str(did), seq, a, b, t, n, json.dumps(h), now_iso())
             for seq, a, b, t, n, h in rows])
        # distill the domain's "what" from its story (a one-commit feature has no arc
        # to distill — its register definition already says what it is)
        story = "\n".join(f"- {n}" for n in narratives if n)
        if len(commits) > 1:
            try:
                r = provider.chat(DISTILL_SYS, f"Domain: {d['name']}\nEvolution:\n{story}\n\n"
                                  'Return {"summary":"..."}', want_json=True,
                                  cache_extra=f"distill:{d['name']}:{len(narratives)}")
                summary = ((r.get("summary") if isinstance(r, dict) else "") or "").strip()
                if summary:
                    conn.execute("UPDATE domains SET summary=? WHERE id=? AND status!='confirmed' "
                                 "AND locked=0", (summary, did))
            except NotCached:
                pass
            except Exception as exc:  # noqa: BLE001
                log(f"    distill {did} failed ({exc})")
        conn.commit()
        done += 1
        if done % 25 == 0:
            log(f"    {done}/{len(doms)} chronicled")
    return done, pending


