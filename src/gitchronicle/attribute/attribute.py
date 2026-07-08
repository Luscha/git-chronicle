"""Derive commit⇄domain membership from concerns (many-to-many).

A commit belongs to every domain its concerns landed in. A multi-topic commit therefore
attaches to several domains — and a god-file's commits spread across domains, because
attribution follows the untangled concern, not the file.
"""

from __future__ import annotations

from collections import Counter, defaultdict


def attribute(conn, log=print) -> dict:
    commit_domains: dict[str, list[int]] = defaultdict(list)
    kinds: dict[str, str] = {}
    for r in conn.execute("SELECT commit_hash, domain_id, kind FROM concerns WHERE domain_id IS NOT NULL"):
        commit_domains[r["commit_hash"]].append(r["domain_id"])
        kinds[r["commit_hash"]] = r["kind"]

    conn.execute("DELETE FROM commit_domains")
    links = 0
    for h, doms in commit_domains.items():
        total = len(doms)
        for did, cnt in Counter(doms).items():
            conn.execute(
                "INSERT OR REPLACE INTO commit_domains (commit_hash, domain_id, weight, kind, source) "
                "VALUES (?,?,?,?, 'concern')", (h, did, round(cnt / total, 3), kinds.get(h)))
            links += 1
    conn.commit()

    conn.execute(
        "UPDATE domains SET "
        "n_commits = (SELECT COUNT(*) FROM commit_domains cd WHERE cd.domain_id = domains.id), "
        "first_seen = (SELECT MIN(c.authored_at) FROM commit_domains cd "
        "  JOIN commits c ON c.hash = cd.commit_hash WHERE cd.domain_id = domains.id), "
        "last_seen  = (SELECT MAX(c.authored_at) FROM commit_domains cd "
        "  JOIN commits c ON c.hash = cd.commit_hash WHERE cd.domain_id = domains.id)")
    conn.commit()

    multi = conn.execute(
        "SELECT COUNT(*) FROM (SELECT commit_hash FROM commit_domains GROUP BY commit_hash "
        "HAVING COUNT(*) > 1)").fetchone()[0]
    log(f"  {links} commit→domain links; {multi} commits span >1 domain")
    return {"links": links, "multi_domain_commits": multi}
