"""DOSSIER export — the copywriter corpus: one md+json bundle per feature.

Each dossier is self-contained: what the feature IS (register definition + distilled
summary), its territory (worktree files), its relations (uses / used-by), its story
(chronicle chapters), and every attributed commit as a hash citation. An index.md
orders features as a development journey (by first attributed commit).

Pure DB reads — run after `run` (+ optional `chronicle`). No LLM, no network.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

_MIN_COMMITS_FULL = 1     # features with no attributed commits get no dossier (stubs stay in taxonomy export)
_TERRITORY_CAP = 60
_COMMIT_CAP = 400


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:64] or "feature"


def export_dossiers(conn, out_dir: str, log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # the bundle must be a pure projection of THIS db — stale pages from an earlier
    # run polluting the browse surface is worse than no page at all
    for old in out.iterdir():
        if old.is_file() and (old.suffix in (".md", ".json") or old.name == "kb.html"):
            old.unlink()

    doms = [dict(r) for r in conn.execute(
        "SELECT id, name, definition, summary, stems, classification, fan_in, "
        "tier, born_at, last_seen, lifecycle "
        "FROM domains WHERE status IN ('named','confirmed','provisional')")]
    by_id = {d["id"]: d for d in doms}

    commits = defaultdict(list)
    for r in conn.execute(
            "SELECT cd.domain_id, c.hash, c.authored_at, c.subject FROM commit_domains cd "
            "JOIN commits c ON c.hash = cd.commit_hash WHERE c.is_merge = 0 "
            "ORDER BY c.authored_at"):
        commits[r["domain_id"]].append(
            {"hash": r["hash"][:10], "date": (r["authored_at"] or "")[:10],
             "subject": r["subject"] or ""})

    territory = defaultdict(list)
    terr_total = Counter()
    for r in conn.execute(
            "SELECT domain_id, path, source FROM domain_files "
            "ORDER BY source = 'register' DESC, weight DESC"):
        terr_total[r["domain_id"]] += 1
        if len(territory[r["domain_id"]]) < _TERRITORY_CAP:
            territory[r["domain_id"]].append({"path": r["path"], "source": r["source"]})

    edges_out, edges_in = defaultdict(list), defaultdict(list)
    for r in conn.execute("SELECT src_domain, dst_domain, weight FROM domain_edges WHERE type='uses'"):
        if r["src_domain"] in by_id and r["dst_domain"] in by_id:
            edges_out[r["src_domain"]].append((by_id[r["dst_domain"]]["name"], r["weight"]))
            edges_in[r["dst_domain"]].append((by_id[r["src_domain"]]["name"], r["weight"]))

    chapters = defaultdict(list)
    for r in conn.execute(
            "SELECT target_id, title, narrative, period_start, period_end, commit_hashes "
            "FROM evolution_chapters WHERE target_type='domain' ORDER BY seq"):
        chapters[int(r["target_id"])].append(dict(r))

    written, journey, kb_records = 0, [], []
    for d in sorted(doms, key=lambda x: x["name"].lower()):
        cs = commits.get(d["id"], [])
        slug = _slug(d["name"])
        rec = {
            "slug": slug,
            "name": d["name"], "definition": d["definition"] or "",
            "summary": d["summary"] or "", "classification": d["classification"],
            "tier": d["tier"] or "feature",
            "born": (d["born_at"] or "")[:10], "last": (d["last_seen"] or "")[:10],
            "lifecycle": d["lifecycle"] or "active",
            "stems": json.loads(d["stems"] or "[]"),
            "territory": territory.get(d["id"], []),
            "territory_total": terr_total.get(d["id"], 0),
            "uses": [{"feature": n, "files": w} for n, w in
                     sorted(edges_out.get(d["id"], []), key=lambda x: -x[1])],
            "used_by": [{"feature": n, "files": w} for n, w in
                        sorted(edges_in.get(d["id"], []), key=lambda x: -x[1])],
            "chapters": [{"title": c["title"], "narrative": c["narrative"],
                          "period": f"{(c['period_start'] or '')[:10]}..{(c['period_end'] or '')[:10]}",
                          "commits": json.loads(c["commit_hashes"] or "[]")}
                         for c in chapters.get(d["id"], [])],
            "commits": cs[:_COMMIT_CAP],
        }
        kb_records.append(rec)
        # md/json dossier files only for features with history; the KB shows everything
        if len(cs) < _MIN_COMMITS_FULL:
            continue
        (out / f"{slug}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))

        md = [f"# {d['name']}", ""]
        if rec["definition"]:
            md += [rec["definition"], ""]
        if rec["summary"] and rec["summary"] != rec["definition"]:
            md += [f"> {rec['summary']}", ""]
        if rec["uses"] or rec["used_by"]:
            md.append("## Relations")
            md += [f"- uses **{u['feature']}** ({u['files']:.0f} importing files)" for u in rec["uses"]]
            md += [f"- used by **{u['feature']}** ({u['files']:.0f} importing files)" for u in rec["used_by"]]
            md.append("")
        if rec["territory"]:
            md.append("## Territory")
            md += [f"- `{t['path']}`" + (" *(historical)*" if t["source"] == "history" else "")
                   for t in rec["territory"][:30]]
            md.append("")
        if rec["chapters"]:
            md.append("## Story")
            for c in rec["chapters"]:
                md.append(f"### {c['title'] or c['period']} ({c['period']})")
                if c["narrative"]:
                    md.append(c["narrative"])
                md.append("commits: " + ", ".join(f"`{h}`" for h in c["commits"][:12]))
                md.append("")
        md.append(f"## Commits ({len(cs)})")
        md += [f"- `{c['hash']}` {c['date']} {c['subject']}" for c in cs[:_COMMIT_CAP]]
        (out / f"{slug}.md").write_text("\n".join(md) + "\n")

        if d["classification"] not in ("vendored", "inherited"):
            journey.append((cs[0]["date"], d["name"], slug, len(cs),
                            bool(rec["used_by"]) and len(rec["used_by"]) >= 3))
        written += 1

    journey.sort()
    idx = ["# Feature journey", "",
           f"{written} features with attributed history (of {len(doms)} in the register).", ""]
    for date, name, slug, n, hub in journey:
        star = " ⭐" if hub else ""
        idx.append(f"- {date} — [{name}]({slug}.md) ({n} commits){star}")
    (out / "index.md").write_text("\n".join(idx) + "\n")
    from .kb import render_kb
    render_kb(kb_records, out / "kb.html")
    log(f"  {written} dossiers -> {out}/ (+ index.md, kb.html with all "
        f"{len(kb_records)} features)")
    return {"dossiers": written, "kb_features": len(kb_records)}
