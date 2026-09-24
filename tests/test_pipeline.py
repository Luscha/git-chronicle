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


def test_scope_can_be_rewritten_without_touching_the_rest_of_the_file(tmp_path, monkeypatch):
    """The scope view edits this file; everything else in it is the owner's."""
    from gitchronicle.scope import Scope
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gitchronicle.md").write_text(
        "# gitchronicle\n\n## Scope\n\n- include: src/**\n- exclude: vendor/**\n\n"
        "## Direction\n\nvoice: terse\n")
    sc = Scope.load()
    assert sc.verdict("vendor/**") == "external" and sc.verdict("src/**") == "analysed"
    sc.set("src/thirdparty/**", "external", siblings=["src/app/**"])
    sc.save()
    again = Scope.load()
    # the exclusion took effect although a broader include covered it
    assert again("src/thirdparty/boost.hpp") is False
    assert again("src/app/main.py") is True
    assert "## Direction" in (tmp_path / "gitchronicle.md").read_text()
    assert "voice: terse" in (tmp_path / "gitchronicle.md").read_text()


def test_acknowledged_subtree_is_not_analysed(tmp_path, monkeypatch):
    from gitchronicle.scope import Scope
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gitchronicle.md").write_text("# x\n\n## Scope\n\n- acknowledge: tools/vendor/**\n")
    sc = Scope.load()
    assert sc.verdict("tools/vendor/**") == "one entry"
    assert sc("tools/vendor/thing.py") is False


def test_a_stated_relation_becomes_an_edge(config, provider, monkeypatch, tmp_path):
    """Imports show code calling code. Content rendered by a framework references nothing
    an extractor can read, so the owner's statement is the only evidence there is."""
    from gitchronicle.taxonomy.ledger import Ledger
    from gitchronicle.taxonomy.relations import build_relations
    plan = ('entry "Reactor"\n  claim src/reactor/**\nentry "Dashboard"\n'
            '  claim src/dashboard/**\n  uses   "Reactor"\n')
    conn, kb = build(config, provider, monkeypatch, tmp_path, plan=plan)
    from gitchronicle.storage import connect
    out = connect(config["output"]["kb"])
    build_relations(out, config["repo"]["path"], log=lambda *_: None, ledger=Ledger.load())
    out.commit()
    edges = {(r[0], r[1], r[2]) for r in out.execute(
        "SELECT s.name, t.name, e.why FROM domain_edges e JOIN domains s ON s.id=e.src_domain "
        "JOIN domains t ON t.id=e.dst_domain")}
    assert any(s == "Dashboard" and d == "Reactor" for s, d, _ in edges)
    assert any("stated" in (why or "") for s, d, why in edges if s == "Dashboard")


def test_a_stated_relation_to_an_unknown_entry_is_reported_not_silent(config, provider,
                                                                     monkeypatch, tmp_path):
    from gitchronicle.taxonomy.ledger import Ledger
    from gitchronicle.taxonomy.relations import build_relations
    plan = 'entry "Reactor"\n  claim src/reactor/**\n  uses   "Nonexistent System"\n'
    conn, kb = build(config, provider, monkeypatch, tmp_path, plan=plan)
    from gitchronicle.storage import connect
    out = connect(config["output"]["kb"])
    said = []
    build_relations(out, config["repo"]["path"], log=lambda m="": said.append(str(m)),
                    ledger=Ledger.load())
    assert any("Nonexistent System" in m for m in said)


def test_a_refused_relation_is_removed_not_merely_unproposed(config, provider, monkeypatch,
                                                             tmp_path):
    """`not-uses` used to stop the studio PROPOSING a link while doing nothing to one the
    imports had already asserted — so a link judged wrong could not be taken back."""
    from gitchronicle.storage import connect
    from gitchronicle.taxonomy.ledger import Ledger
    from gitchronicle.taxonomy.relations import build_relations
    plan = ('entry "Reactor"\n  claim src/reactor/**\nentry "Dashboard"\n'
            '  claim src/dashboard/**\n  uses   "Reactor"\n')
    build(config, provider, monkeypatch, tmp_path, plan=plan)
    out = connect(config["output"]["kb"])
    said = []
    (tmp_path / "gitchronicle.plan").write_text(plan + 'not-uses   "Dashboard" -> "Reactor"\n')
    build_relations(out, config["repo"]["path"], log=lambda m="": said.append(str(m)),
                    ledger=Ledger.load())
    out.commit()
    edges = {(r[0], r[1]) for r in out.execute(
        "SELECT s.name, t.name FROM domain_edges e JOIN domains s ON s.id=e.src_domain "
        "JOIN domains t ON t.id=e.dst_domain")}
    assert ("Dashboard", "Reactor") not in edges
    # stating both is a contradiction, and saying nothing would leave the owner guessing
    assert any("not-uses" in m for m in said)


def test_an_import_resolves_only_to_a_file_its_own_syntax_could_name():
    """`#include "config.h"` in char_affect.cpp was resolving to config.js in the web
    panel. 85 of this repository's 227 import edges were that same collision."""
    from gitchronicle.taxonomy.relations import _resolve
    by_base = {"config": [(1, "ccc/frontend/public/config.js"), (2, "Server/src/config.hpp")]}
    assert _resolve("config", "c", by_base) == [(2, "Server/src/config.hpp")]
    assert _resolve("config", "es", by_base) == [(1, "ccc/frontend/public/config.js")]
    # an unknown extension is left alone: .fx includes .fxh, .forge requires .lua
    assert _resolve("x", "c", {"x": [(3, "shaders/terrain.fxh")]}) == [(3, "shaders/terrain.fxh")]


def test_edge_evidence_names_the_files_behind_a_link(config, provider, monkeypatch, tmp_path):
    """A link the owner cannot interrogate is a link the owner cannot judge."""
    from gitchronicle.storage import connect
    from gitchronicle.taxonomy.relations import edge_evidence
    plan = ('entry "Reactor"\n  claim src/reactor/**\nentry "Dashboard"\n'
            '  claim src/dashboard/**\n')
    build(config, provider, monkeypatch, tmp_path, plan=plan)
    out = connect(config["output"]["kb"])
    ev = edge_evidence(out, config["repo"]["path"], "Dashboard", "Reactor")
    assert any(e["file"].startswith("src/dashboard/") and e["ref"] == "cooling"
               and e["target"] == "src/reactor/cooling.py" for e in ev), ev


def test_a_chapter_carries_what_happened_after_it(config, provider, monkeypatch, tmp_path):
    """The battle pass was switched off in a commit whose own summary says so, and the
    chapter ran two months past it and was narrated "Introduced and Refined". What was
    missing was not the commit: it was that the work then stopped, and that the file is
    not in the repository today."""
    from gitchronicle.chronicle.chronicle import _boundaries, _cluster, _domain_commits
    from gitchronicle.storage import connect
    conn, _ = build(config, provider, monkeypatch, tmp_path)
    out = connect(config["output"]["kb"])
    seen = []
    for (did,) in out.execute("SELECT id FROM domains"):
        commits = _domain_commits(out, did)
        if commits:
            seen += _boundaries(out, config["repo"]["path"], did, _cluster(commits))
    # the shim was added and deleted inside this history; no territory can hold it,
    # because territory is built from the files that still exist
    assert any("src/legacy/shim.py" in b["gone"] for b in seen), seen
    assert seen[-1]["last"] is True and all(b["gap_days"] >= 0 for b in seen)


def test_rust_module_paths_resolve_to_their_module_not_their_type():
    """Alacritty produced ZERO dependency links: every import pattern the extractor knew
    was C, Python, JS or Lua. Rust names a module by path, and the last segment is the
    type — its own snake_case/CamelCase convention tells them apart without parsing."""
    from gitchronicle.taxonomy.imports import extract_import_refs
    text = ("use alacritty_terminal::term::Term;\n"
            "pub use crate::display::window::Window;\n"
            "mod event;\n")
    assert extract_import_refs(text, with_kind=True) == {
        ("term", "rs"), ("window", "rs"), ("event", "rs")}
