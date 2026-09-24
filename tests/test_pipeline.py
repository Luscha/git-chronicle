"""End to end on a real git repository with a stub model: ingest, untangle, assemble,
emit, then ask the result questions. Nothing here touches the network."""

from __future__ import annotations

import json
import sqlite3

from gitchronicle.extract import ingest
from gitchronicle.enrich import enrich
from gitchronicle.storage import connect, init_db
from gitchronicle.taxonomy.lineage import build_lineage, emit_register
from gitchronicle.untangle.untangle import untangle


def build(config, provider, monkeypatch, tmp_path, plan: str = ""):
    monkeypatch.chdir(tmp_path)
    if plan:
        (tmp_path / "gitchronicle.plan").write_text(plan)
    conn = connect(config["db"]["path"])
    init_db(conn)
    ingest(conn, config["repo"]["path"], config["repo"]["rev_range"], log=lambda *_: None)
    enrich(conn, log=lambda *_: None)
    untangle(conn, provider, config["repo"]["path"], log=lambda *_: None, workers=2)
    res = build_lineage(conn, config["repo"]["path"], log=lambda *_: None)
    emit_register(conn, config["repo"]["path"], res, config["output"]["kb"], provider,
                  log=lambda *_: None)
    return conn, sqlite3.connect(config["output"]["kb"])


def test_full_run_produces_a_knowledge_base(config, provider, monkeypatch, tmp_path):
    conn, kb = build(config, provider, monkeypatch, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0] > 0
    kb.row_factory = sqlite3.Row
    assert kb.execute("SELECT COUNT(*) FROM domains").fetchone()[0] > 0


def test_untangle_is_incremental_and_ordered_newest_first(config, provider, monkeypatch,
                                                          tmp_path):
    conn, _ = build(config, provider, monkeypatch, tmp_path)
    before = conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0]
    calls = len(provider.calls)
    untangle(conn, provider, config["repo"]["path"], log=lambda *_: None, workers=2)
    assert conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0] == before
    assert len(provider.calls) == calls          # nothing re-asked
    rows = conn.execute(
        "SELECT c.authored_at FROM concerns cn JOIN commits c ON c.hash = cn.commit_hash "
        "ORDER BY cn.id").fetchall()
    dates = [r[0] for r in rows]
    assert dates == sorted(dates, reverse=True)  # newest commit gets the lowest id


def test_a_rule_moves_files_and_their_history(config, provider, monkeypatch, tmp_path):
    plan = 'entry "Dashboard"\n  claim src/dashboard/**\n  tier feature\n'
    _, kb = build(config, provider, monkeypatch, tmp_path, plan=plan)
    kb.row_factory = sqlite3.Row
    row = kb.execute("SELECT id, tier, created_by FROM domains WHERE name='Dashboard'").fetchone()
    assert row and row["tier"] == "feature" and row["created_by"] == "ledger"
    files = {r[0] for r in kb.execute("SELECT path FROM domain_files WHERE domain_id=?",
                                      (row["id"],))}
    assert any(f.startswith("src/dashboard/") for f in files)
    assert kb.execute("SELECT COUNT(*) FROM commit_domains WHERE domain_id=?",
                      (row["id"],)).fetchone()[0] > 0


def test_territory_uses_the_file_s_current_name_after_a_rename(config, provider,
                                                               monkeypatch, tmp_path):
    _, kb = build(config, provider, monkeypatch, tmp_path)
    paths = {r[0] for r in kb.execute("SELECT DISTINCT path FROM domain_files")}
    assert "src/dashboard/panel.py" not in paths       # the pre-rename name is gone
    if any(p.startswith("src/dashboard/") for p in paths):
        assert "src/dashboard/main_panel.py" in paths


def test_a_rebuild_reproduces_the_catalogue(config, provider, monkeypatch, tmp_path):
    conn, kb = build(config, provider, monkeypatch, tmp_path)
    first = sorted(r[0] for r in kb.execute("SELECT name FROM domains"))
    res = build_lineage(conn, config["repo"]["path"], log=lambda *_: None)
    emit_register(conn, config["repo"]["path"], res, config["output"]["kb"], provider,
                  log=lambda *_: None)
    again = sorted(r[0] for r in sqlite3.connect(config["output"]["kb"])
                   .execute("SELECT name FROM domains"))
    assert first == again


def test_scope_excludes_paths(config, provider, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gitchronicle.md").write_text(
        "# x\n\n## Scope\n\n- include: src/reactor/**\n- exclude: src/legacy/**\n")
    conn, kb = build(config, provider, monkeypatch, tmp_path)
    seen = set()
    for (f,) in conn.execute("SELECT files FROM concerns"):
        seen |= set(json.loads(f or "[]"))
    assert not any(p.startswith("src/legacy/") for p in seen)


def test_a_repository_started_from_scratch_has_no_inherited_baseline(config, provider,
                                                                     monkeypatch, tmp_path):
    """A fork's first commit is the upstream drop; a small first commit is the owner's own
    work, and was being filed as inherited (httpie: 7 files, treated as vendor code)."""
    conn, kb = build(config, provider, monkeypatch, tmp_path)
    kinds = {r[0] for r in kb.execute("SELECT classification FROM domains")}
    assert "inherited" not in kinds                       # nothing here predates the project
    names = {r[0] for r in kb.execute("SELECT name FROM domains")}
    assert not any(n.startswith("Inherited baseline") for n in names)
    # unplaced work still keeps a home, under a name that claims nothing about provenance
    assert "upkeep" in kinds or all("Upkeep" not in n for n in names)


def test_narrowing_the_scope_drops_evidence_it_excludes(config, provider, monkeypatch,
                                                        tmp_path):
    """The scope map is the authority. When it is absent for one run the wider fallback
    untangles excluded commits, and those concerns used to survive every later run — 247
    of them here, enough to change the catalogue."""
    from gitchronicle.untangle.untangle import untangle
    conn, _ = build(config, provider, monkeypatch, tmp_path)
    before = conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0]
    (tmp_path / "gitchronicle.md").write_text(
        "# x\n\n## Scope\n\n- include: src/reactor/**\n- exclude: src/dashboard/**\n")
    untangle(conn, provider, config["repo"]["path"], log=lambda *_: None, workers=2)
    seen = set()
    for (f,) in conn.execute("SELECT files FROM concerns"):
        seen |= set(json.loads(f or "[]"))
    assert not any(p.startswith("src/dashboard/") for p in seen)
    assert conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0] < before
