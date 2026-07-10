"""Human-facing taxonomy operations — the OPTIONAL review seam.

The pipeline never blocks on a human: provisional features are used the moment they are
proposed. What lives here is the seam where a human *may* step in, CLI/automation-first:

- **changeset** — the pending proposals (provisional features) as text or a machine dict.
- **review plan** — a `git rebase -i`-style verb-prefixed text file: edit verbs in $EDITOR
  (or in a PR), then apply. Verbs: accept | reject | lock | merge -> target | rename -> new.
- **direct ops** — merge/rename/reject/confirm/lock for scripting.
- **export/import** — the taxonomy as a plain TOML file, so review can also happen in
  ordinary code review (commit the file, open a PR, import on merge).

Every applied verb is recorded in `annotations` — these corrections are the golden records
that accumulate into the regression set (no upfront labeling). Rejects are tombstoned so a
rejected candidate is never re-proposed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from ..storage import now_iso

_ACTIVE = "status IN ('named','provisional','confirmed')"


def _ann(conn, did, field, old, new, kind, note=None):
    conn.execute(
        "INSERT INTO annotations (target_type,target_id,field,old_value,new_value,note,author,"
        "kind,created_at) VALUES ('domain',?,?,?,?,?,'user',?,?)",
        (str(did), field, None if old is None else str(old),
         None if new is None else str(new), note, kind, now_iso()))


def resolve_feature(conn, ref: str):
    """A feature by '[id]', bare id, exact slug, or (unique-ish) name match."""
    m = re.fullmatch(r"\[?(\d+)\]?", ref.strip())
    if m:
        return conn.execute(f"SELECT * FROM domains WHERE id=? AND {_ACTIVE}",
                            (int(m.group(1)),)).fetchone()
    return conn.execute(
        f"SELECT * FROM domains WHERE {_ACTIVE} AND (slug=? OR lower(name)=lower(?) "
        f"OR name LIKE ?) ORDER BY n_commits DESC LIMIT 1",
        (ref, ref, f"%{ref}%")).fetchone()


# --- changeset ---------------------------------------------------------------
def pending_changeset(conn) -> list[dict]:
    """Provisional features + context to judge them (size, samples, nearest neighbour)."""
    rows = conn.execute(
        "SELECT id, name, definition FROM domains WHERE status='provisional' ORDER BY id").fetchall()
    if not rows:
        return []
    # nearest existing feature by definition embedding (if the classify run left them)
    emb = {int(r["target_id"]): np.frombuffer(r["vector"], dtype=np.float32)
           for r in conn.execute("SELECT target_id, vector FROM embeddings WHERE target_type='taxdef'")}
    settled = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM domains WHERE status IN ('named','confirmed')")}
    out = []
    for r in rows:
        n = conn.execute("SELECT COUNT(*) FROM concerns WHERE domain_id=?", (r["id"],)).fetchone()[0]
        samples = [x["label"] for x in conn.execute(
            "SELECT label FROM concerns WHERE domain_id=? LIMIT 3", (r["id"],))]
        near = ""
        if r["id"] in emb and settled:
            v = emb[r["id"]]
            v = v / (np.linalg.norm(v) + 1e-9)
            best, bs = None, -1.0
            for did, name in settled.items():
                if did in emb:
                    w = emb[did] / (np.linalg.norm(emb[did]) + 1e-9)
                    c = float(v @ w)
                    if c > bs:
                        best, bs = name, c
            if best:
                near = f"{best} ({bs:.2f})"
        out.append({"id": r["id"], "name": r["name"], "definition": r["definition"] or "",
                    "n_concerns": n, "samples": samples, "nearest": near})
    return out


def render_changeset(changes: list[dict]) -> str:
    if not changes:
        return "taxonomy: no pending proposals"
    lines = [f"taxonomy: {len(changes)} pending proposed feature(s):"]
    for c in changes:
        hint = f"  nearest: {c['nearest']}" if c["nearest"] else ""
        lines.append(f"  + [{c['id']}] {c['name']}  ({c['n_concerns']} concerns){hint}")
        if c["samples"]:
            lines.append(f"      e.g. {'; '.join(s[:60] for s in c['samples'])}")
    lines.append("review with: gitchronicle taxonomy review [--edit]")
    return "\n".join(lines)


# --- review plan (rebase -i style) -------------------------------------------
PLAN_HEADER = """\
# gitchronicle taxonomy review — edit the VERB at the start of each line, save, close.
# verbs:
#   accept [id]                       keep the feature (confirmed)
#   reject [id]                       wrong feature: tombstone the name, re-home its concerns
#   lock   [id]                       accept + never let auto-runs touch it again
#   merge  [id] ... -> <target>       fold into an existing feature (name or [id])
#   rename [id] ... -> <new name>     keep but rename
# lines starting with # are ignored; text after | is context only
"""


def review_plan(changes: list[dict]) -> str:
    lines = [PLAN_HEADER]
    for c in changes:
        hint = f" | nearest: {c['nearest']}" if c["nearest"] else ""
        eg = "; ".join(s[:50] for s in c["samples"][:2])
        lines.append(f"accept [{c['id']}] {c['name']} | {c['n_concerns']} concerns"
                     f"{hint}" + (f" | e.g. {eg}" if eg else ""))
    return "\n".join(lines) + "\n"


_VERB_RE = re.compile(r"^\s*(accept|reject|lock|merge|rename)\s+\[(\d+)\]([^|]*)", re.I)


def parse_plan(text: str) -> list[dict]:
    actions = []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = _VERB_RE.match(ln)
        if not m:
            continue
        verb, did, rest = m.group(1).lower(), int(m.group(2)), m.group(3)
        act = {"verb": verb, "id": did}
        if verb in ("merge", "rename"):
            _, _, target = rest.partition("->")
            act["target"] = target.strip()
            if not act["target"]:
                continue           # merge/rename without a target: ignore, stays pending
        actions.append(act)
    return actions


# --- direct operations --------------------------------------------------------
def do_confirm(conn, d, lock: bool = False, log=print):
    _ann(conn, d["id"], "status", d["status"], "confirmed", "confirmation")
    conn.execute("UPDATE domains SET status='confirmed', locked=? WHERE id=?",
                 (1 if lock else d["locked"], d["id"]))
    log(f"  confirmed{' & locked' if lock else ''}: {d['name']}")


def do_rename(conn, d, new_name: str, log=print):
    _ann(conn, d["id"], "name", d["name"], new_name, "correction")
    slug = re.sub(r"[^a-z0-9]+", "-", new_name.lower()).strip("-")[:60]
    conn.execute("INSERT OR IGNORE INTO domain_aliases (domain_id, alias) VALUES (?,?)",
                 (d["id"], d["name"]))
    conn.execute("UPDATE domains SET name=?, slug=?, "
                 "status=CASE WHEN status='provisional' THEN 'confirmed' ELSE status END "
                 "WHERE id=?", (new_name, slug, d["id"]))
    log(f"  renamed: {d['name']} -> {new_name}")


def do_merge(conn, d, target_row, log=print):
    """Fold d into target: concerns repoint, name kept as alias, row removed."""
    _ann(conn, target_row["id"], "merge", d["name"], target_row["name"], "correction",
         note=f"merged domain {d['id']} into {target_row['id']}")
    conn.execute("UPDATE concerns SET domain_id=? WHERE domain_id=?", (target_row["id"], d["id"]))
    conn.execute("INSERT OR IGNORE INTO domain_aliases (domain_id, alias) VALUES (?,?)",
                 (target_row["id"], d["name"]))
    conn.execute("DELETE FROM domains WHERE id=?", (d["id"],))
    log(f"  merged: {d['name']} -> {target_row['name']}")


def do_reject(conn, d, log=print):
    """Tombstone the name (never re-proposed) and free its concerns for re-classification."""
    _ann(conn, d["id"], "status", d["status"], "rejected", "correction")
    conn.execute("INSERT OR REPLACE INTO taxonomy_tombstones (name, reason, created_at) "
                 "VALUES (?,?,?)", (d["name"], "rejected in review", now_iso()))
    n = conn.execute("UPDATE concerns SET domain_id=NULL, assign_source=NULL, assign_conf=NULL "
                     "WHERE domain_id=?", (d["id"],)).rowcount
    conn.execute("UPDATE domains SET status='rejected' WHERE id=?", (d["id"],))
    log(f"  rejected: {d['name']} (tombstoned; {n} concerns freed for re-classification)")
    return n


def apply_actions(conn, provider, cfg, actions: list[dict], log=print) -> dict:
    """Apply review verbs, then re-home freed concerns (frozen: no new features from a
    review) and refresh derived state so the KB stays consistent."""
    counts = {"accept": 0, "reject": 0, "lock": 0, "merge": 0, "rename": 0}
    freed = 0
    for a in actions:
        d = conn.execute("SELECT * FROM domains WHERE id=?", (a["id"],)).fetchone()
        if not d or d["status"] == 'rejected':
            log(f"  skip [{a['id']}]: not found")
            continue
        if a["verb"] == "accept":
            do_confirm(conn, d, lock=False, log=log)
        elif a["verb"] == "lock":
            do_confirm(conn, d, lock=True, log=log)
        elif a["verb"] == "rename":
            do_rename(conn, d, a["target"][:60], log=log)
        elif a["verb"] == "merge":
            t = resolve_feature(conn, a["target"])
            if not t or t["id"] == d["id"]:
                log(f"  skip merge [{a['id']}]: target {a['target']!r} not found")
                continue
            do_merge(conn, d, t, log=log)
        elif a["verb"] == "reject":
            freed += do_reject(conn, d, log=log)
        counts[a["verb"]] += 1
    conn.commit()

    if freed and provider is not None:
        log(f"  re-classifying {freed} freed concerns against the remaining taxonomy ...")
        from .classify import classify
        classify(conn, provider, cfg, frozen=True, log=log)
    # refresh derived state (cheap, no LLM)
    from ..attribute import attribute
    from ..catalog.catalog import _derive_files
    from .classify import rollups
    _derive_files(conn)
    attribute(conn, log=lambda *_: None)
    rollups(conn)
    conn.commit()
    return counts


# --- export / import ----------------------------------------------------------
def export_taxonomy(conn, path: str | Path, log=print) -> int:
    """The taxonomy as a reviewable TOML file (PR-based review flow)."""
    rows = conn.execute(
        "SELECT d.id, d.name, d.definition, d.status, d.locked, a.name AS area "
        "FROM domains d LEFT JOIN areas a ON a.id=d.area_id "
        "WHERE d.status IN ('named','provisional','confirmed') "
        "ORDER BY a.name, d.name").fetchall()
    lines = ["# gitchronicle taxonomy — edit name/definition/status/locked, then "
             "`gitchronicle taxonomy import <file>`", ""]
    for r in rows:
        lines += ["[[feature]]",
                  f"id = {r['id']}",
                  f"name = {json.dumps(r['name'])}",
                  f"status = {json.dumps(r['status'])}",
                  f"locked = {'true' if r['locked'] else 'false'}",
                  f"area = {json.dumps(r['area'] or '')}",
                  f"definition = {json.dumps(r['definition'] or '')}",
                  ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    log(f"  exported {len(rows)} features -> {path}")
    return len(rows)


def import_taxonomy(conn, path: str | Path, log=print) -> dict:
    """Apply an edited export: match by id, update fields; unknown ids create confirmed
    features; features absent from the file are left untouched (import never deletes)."""
    import tomllib
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    changed = created = 0
    for f in data.get("feature", []):
        fid = f.get("id")
        row = conn.execute("SELECT * FROM domains WHERE id=?", (fid,)).fetchone() if fid else None
        name = str(f.get("name") or "").strip()[:60]
        definition = str(f.get("definition") or "").strip()[:500]
        status = str(f.get("status") or "confirmed")
        if status not in ("named", "provisional", "confirmed", "rejected"):
            status = "confirmed"
        locked = 1 if f.get("locked") else 0
        if row is None:
            if not name:
                continue
            conn.execute("INSERT INTO domains (name, slug, definition, classification, status, "
                         "locked, created_by) VALUES (?,?,?, 'feature',?,?, 'user')",
                         (name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60],
                          definition, status, locked))
            created += 1
            continue
        updates = {}
        if name and name != row["name"]:
            do_rename(conn, row, name, log=lambda *_: None)
            updates["name"] = name
        if definition != (row["definition"] or ""):
            _ann(conn, row["id"], "definition", row["definition"], definition, "correction")
            updates["definition"] = definition
        if status == "rejected" and row["status"] != "rejected":
            do_reject(conn, row, log=log)
        elif status != row["status"]:
            _ann(conn, row["id"], "status", row["status"], status, "correction")
            updates["status"] = status
        if locked != row["locked"]:
            updates["locked"] = locked
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE domains SET {sets} WHERE id=?", (*updates.values(), row["id"]))
            changed += 1
    conn.commit()
    log(f"  imported: {changed} updated, {created} created")
    return {"updated": changed, "created": created}
