"""COST — what the runs actually cost, per stage, from the cache's own token counts.

Every cached call records its tokens and the stage that paid for them, so the bill can be
read back instead of estimated. Prices are per million tokens and live in the config,
because they change and they differ per account:

    [pricing."google/gemini-2.5-flash"]
    input = 0.30
    output = 2.50

Without a price the tokens are still reported — an unpriced model shows its usage and a
dash, which is more useful than a confident wrong number.
"""

from __future__ import annotations

_STAGE_ORDER = ["untangle", "naming", "narration", "distil", "ask", "eval", "chat",
                "chat_large"]


def _price(pricing: dict, model: str) -> tuple[float, float] | None:
    p = pricing.get(model)
    if p is None:                     # "openai/gpt-5" also matches a "gpt-5" entry
        tail = model.split("/")[-1]
        p = pricing.get(tail)
    if not p:
        return None
    return float(p.get("input", 0)), float(p.get("output", 0))


def report(conn, pricing: dict, since: str = "", log=print) -> dict:
    where, args = "", []
    if since:
        where, args = " WHERE created_at >= ?", [since]
    rows = conn.execute(
        "SELECT model, COALESCE(kind, 'chat') AS stage, COUNT(*), "
        "SUM(COALESCE(tokens_in, 0)), SUM(COALESCE(tokens_out, 0)) "
        f"FROM llm_cache{where} GROUP BY model, stage", args).fetchall()
    if not rows:
        log("  no cached calls yet — nothing has been paid for")
        return {"total": 0.0, "rows": []}

    out, total, unpriced = [], 0.0, set()
    for model, stage, n, ti, to in rows:
        pr = _price(pricing, model or "")
        cost = (ti / 1e6 * pr[0] + to / 1e6 * pr[1]) if pr else None
        if cost is None:
            unpriced.add(model)
        else:
            total += cost
        out.append({"model": model, "stage": stage, "calls": n, "tokens_in": ti,
                    "tokens_out": to, "cost": cost})
    out.sort(key=lambda r: (_STAGE_ORDER.index(r["stage"])
                            if r["stage"] in _STAGE_ORDER else 99, -(r["cost"] or 0)))
    log(f"  {'stage':<10} {'model':<34} {'calls':>7} {'in':>11} {'out':>11} {'cost':>9}")
    for r in out:
        cost = f"${r['cost']:.2f}" if r["cost"] is not None else "—"
        log(f"  {r['stage']:<10} {(r['model'] or '?')[:34]:<34} {r['calls']:>7} "
            f"{r['tokens_in']:>11,} {r['tokens_out']:>11,} {cost:>9}")
    log(f"  {'':<10} {'':<34} {sum(r['calls'] for r in out):>7} "
        f"{sum(r['tokens_in'] for r in out):>11,} {sum(r['tokens_out'] for r in out):>11,} "
        f"{'$' + format(total, '.2f'):>9}")
    if unpriced:
        log(f"  no price configured for: {', '.join(sorted(m or '?' for m in unpriced))} "
            f"— add [pricing.\"<model>\"] input/output (per million tokens)")
    return {"total": total, "rows": out}


def estimate(conn, pricing: dict, commits: int, log=print) -> dict:
    """What untangling N more commits should cost, from what this repo's own commits cost.

    Averages are per commit of THIS repository, so they carry its diff sizes and its
    routing mix rather than a number from someone else's project.
    """
    q = ("SELECT model, COUNT(*), SUM(COALESCE(tokens_in,0)), SUM(COALESCE(tokens_out,0)) "
         "FROM llm_cache WHERE COALESCE(kind,'') = 'untangle' GROUP BY model "
         "ORDER BY 2 DESC LIMIT 1")
    row = conn.execute(q).fetchone()
    if not row:
        # caches written before stages were recorded: every call is in one bucket, which
        # over-counts by whatever naming and narration cost
        row = conn.execute(q.replace("WHERE COALESCE(kind,'') = 'untangle' ", "")).fetchone()
        if row:
            log("  (cache predates per-stage accounting: this includes naming and narration)")
    done = conn.execute("SELECT COUNT(DISTINCT commit_hash) FROM concerns").fetchone()[0]
    if not row or not done:
        log("  nothing untangled yet — run once to measure this repository's own rate")
        return {}
    model, calls, ti, to = row
    pr = _price(pricing, model or "")
    per_in, per_out = ti / done, to / done
    log(f"  measured on {done} commits with {model}: {per_in:,.0f} in + {per_out:,.0f} out "
        f"per commit")
    if pr:
        per_commit = per_in / 1e6 * pr[0] + per_out / 1e6 * pr[1]
        log(f"  {commits:,} commits ≈ ${per_commit * commits:.2f} "
            f"(${per_commit * 1000:.2f} per 1000)")
        return {"per_commit": per_commit, "total": per_commit * commits}
    log("  add a [pricing] entry for this model to turn tokens into money")
    return {"per_commit_tokens": (per_in, per_out)}
