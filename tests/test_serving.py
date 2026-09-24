"""Search, MCP and the studio's state, over a knowledge base built in-process."""

from __future__ import annotations

import json
import sqlite3

import pytest

from gitchronicle.serve.mcp import KB, TOOLS, serve_mcp
from gitchronicle.serve.search import Index
from gitchronicle.serve.studio import build_state
from gitchronicle.storage import connect, init_db


@pytest.fixture
def kb(tmp_path):
    path = tmp_path / "kb.db"
    c = connect(path)
    init_db(c)
    c.execute("INSERT INTO commits (hash, author_name, author_email, authored_at, subject, "
              "body, is_merge) VALUES ('abc123def4','Dev','d@e','2021-05-15T10:00:00',"
              "'feat(battlepass): first pass','', 0)")
    c.execute("INSERT INTO commits (hash, author_name, author_email, authored_at, subject, "
              "body, is_merge) VALUES ('beef0001aa','Dev','d@e','2021-08-21T10:00:00',"
              "'battlepass gets removed','', 0)")
    c.execute("INSERT INTO domains (id, name, definition, summary, tier, classification, "
              "status, lifecycle, born_at, first_seen, last_seen, n_commits, created_by) "
              "VALUES (1,'Battlepass System','Seasonal rewards.','Seasonal rewards.',"
              "'feature','feature','named','active','2021-05-15','2021-05-15','2021-08-21',2,"
              "'lineage')")
    c.execute("INSERT INTO domain_files (domain_id, path, weight, source) "
              "VALUES (1,'src/battlepass/ui.py',1.0,'register')")
    c.executemany("INSERT INTO commit_domains (commit_hash, domain_id, weight, source) "
                  "VALUES (?,1,1.0,'lineage')", [("abc123def4",), ("beef0001aa",)])
    c.execute("INSERT INTO evolution_chapters (target_type, target_id, seq, period_start, "
              "period_end, title, narrative, commit_hashes, created_at) VALUES "
              "('domain','1',0,'2021-05-15','2021-08-21','Built then removed',"
              "'It shipped in May 2021 and was removed in August 2021.',"
              "'[\"abc123def4\",\"beef0001aa\"]','2026-01-01')")
    c.commit()
    c.close()
    return path


def test_search_finds_the_entry_its_chapter_and_its_commits(kb):
    hits = Index(str(kb)).search("battlepass")
    assert hits["entries"] and hits["entries"][0]["name"] == "Battlepass System"
    assert hits["chapters"] and "removed" in hits["chapters"][0]["title"].lower()
    assert any(h["hash"].startswith("abc123") for h in hits["commits"])


def test_search_joins_split_words(kb):
    """"battle pass" is spelled battlepass in the code."""
    assert Index(str(kb)).search("battle pass")["entries"][0]["name"] == "Battlepass System"


def test_search_by_year_alone(kb):
    hits = Index(str(kb)).search("what happened in 2021")
    assert hits["years"] == ["2021"] and hits["chapters"]


def test_index_rebuilds_when_the_knowledge_base_changes(kb):
    idx = Index(str(kb))
    assert not idx.search("telemetry")["entries"]
    c = sqlite3.connect(kb)
    c.execute("INSERT INTO domains (id, name, definition, tier, classification, status, "
              "created_by) VALUES (2,'Telemetry','Counters.','feature','feature','named','x')")
    c.commit(); c.close()
    assert idx.search("telemetry")["entries"][0]["name"] == "Telemetry"


def test_mcp_tools_answer_with_evidence(kb):
    k = KB(str(kb))
    assert "Battlepass System" in k.search("battlepass")
    entry = k.entry("Battlepass System")
    assert "2021-05-15" in entry and "Built then removed" in entry
    assert "Battlepass System" in k.path_history("src/battlepass/ui.py")
    year = k.period("2021")
    assert "2 commits" in year and "Battlepass System" in year
    assert "MOST ACTIVE" in year and "FIRST SEEN" in year
    assert "Did you mean" in k.entry("Battlepas")


def test_mcp_speaks_json_rpc(kb, tmp_path):
    import io
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search", "arguments": {"query": "battlepass"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "nope", "arguments": {}}}]
    out = io.StringIO()
    serve_mcp(str(kb), stdin=io.StringIO("\n".join(json.dumps(m) for m in msgs)), stdout=out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[1]["result"]["tools"][0]["name"] == TOOLS[0]["name"]
    assert "Battlepass" in replies[2]["result"]["content"][0]["text"]
    assert "error" in replies[3]


def test_studio_state_carries_catalogue_timeline_and_plan(kb, tmp_path):
    plan = tmp_path / "gitchronicle.plan"
    plan.write_text('entry "Battlepass System"\n  tier feature\n')
    c = connect(kb)
    st = build_state(c, plan)
    assert st["catalogue"][0]["name"] == "Battlepass System"
    assert st["timeline"]["entries"]["Battlepass System"]["act"]
    assert st["chapters"] == 1 and st["plan"].startswith("entry")
