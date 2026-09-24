"""The model hookup: what a config turns into on the wire, and what is refused."""

from __future__ import annotations

import pytest

from gitchronicle.llm.provider import (KINDS, LLMError, NotCached, Provider, _auth_header,
                                       _url, build_provider)


def cfg(**kw):
    base = {"kind": "openai", "base_url": "https://api.example.com/v1", "model": "m",
            "api_key": "secret"}
    base.update(kw)
    return base


def test_a_chat_role_is_the_only_requirement():
    pr = build_provider({"providers": {"chat": cfg()}})
    assert pr.embed_cfg is None
    for role in ("chat_large", "naming", "untangle", "narration", "judge"):
        assert pr._role_cfg(role)["model"] == "m"


def test_judge_falls_back_to_chat_large_before_chat():
    pr = build_provider({"providers": {"chat": cfg(model="small"),
                                       "chat_large": cfg(model="big")}})
    assert pr._role_cfg("judge")["model"] == "big"
    assert pr._role_cfg("untangle")["model"] == "small"


def test_embeddings_are_optional_and_say_so():
    pr = build_provider({"providers": {"chat": cfg()}})
    with pytest.raises(LLMError, match="no \\[providers.embed\\]"):
        pr.embed(["x"])


@pytest.mark.parametrize("providers, match", [
    ({}, "no \\[providers.chat\\]"),
    ({"chat": {"kind": "openai", "model": "m"}}, "needs a base_url"),
    ({"chat": cfg(model=None)}, "needs a model"),
    ({"chat": cfg(kind="telepathy")}, "not one of"),
    ({"chat": cfg(), "wobble": cfg()}, "unknown role"),
])
def test_bad_config_fails_before_anything_is_spent(providers, match):
    with pytest.raises(LLMError, match=match):
        build_provider({"providers": providers})


def test_auth_per_kind_and_extra_headers():
    assert _auth_header(cfg())["Authorization"] == "Bearer secret"
    az = _auth_header(cfg(kind="azure"))
    assert az["api-key"] == "secret" and "Authorization" not in az
    an = _auth_header(cfg(kind="anthropic"))
    assert an["x-api-key"] == "secret" and an["anthropic-version"]
    assert _auth_header(cfg(headers={"x-org": "acme"}))["x-org"] == "acme"


def test_azure_adds_its_api_version_and_custom_query_survives():
    url = _url(cfg(kind="azure", base_url="https://x.openai.azure.com/openai/deployments/d"),
               "/chat/completions")
    assert "api-version=" in url
    assert "beta=1" in _url(cfg(query={"beta": 1}), "/chat/completions")


def test_anthropic_defaults_to_the_public_endpoint_and_messages_path():
    pr = build_provider({"providers": {"chat": {"kind": "anthropic", "model": "claude",
                                                "api_key": "k"}}})
    assert pr.chat_cfg["base_url"] == "https://api.anthropic.com"
    assert _url(pr.chat_cfg, "/v1/messages").endswith("/v1/messages")
    assert _url(cfg(kind="anthropic", base_url="https://gw/v1"), "/v1/messages") == \
        "https://gw/v1/messages"


def test_vertex_refuses_to_serve_embeddings():
    with pytest.raises(LLMError, match="embeddings"):
        build_provider({"providers": {"chat": cfg(),
                                      "embed": {"kind": "vertex", "model": "m",
                                                "base_url": "https://x"}}})


def _payload_of(provider, monkeypatch):
    seen = {}

    def fake_post(url, payload, headers, retries=6):
        seen.update(url=url, payload=payload, headers=headers)
        return {"choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 30},
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "id": "x"}
    monkeypatch.setattr(provider, "_post", fake_post)
    return seen


def test_params_and_thinking_reach_the_request(monkeypatch):
    pr = build_provider({"providers": {"chat": cfg(thinking_budget=0,
                                                   params={"max_tokens": 128})}})
    seen = _payload_of(pr, monkeypatch)
    pr.chat("s", "u")
    assert seen["payload"]["extra_body"]["google"]["thinking_config"]["thinking_budget"] == 0
    assert seen["payload"]["max_tokens"] == 128


def test_anthropic_shapes_system_and_max_tokens(monkeypatch):
    pr = build_provider({"providers": {"chat": {"kind": "anthropic", "model": "claude",
                                                "api_key": "k", "thinking_budget": 1024}}})
    seen = _payload_of(pr, monkeypatch)
    pr.chat("be brief", "hello", want_json=False)
    assert seen["payload"]["system"] == "be brief"
    assert seen["payload"]["messages"][0]["content"] == "hello"
    assert seen["payload"]["thinking"]["budget_tokens"] == 1024
    assert seen["url"].endswith("/v1/messages")


def test_cost_counts_thinking_tokens(tmp_path, monkeypatch):
    from gitchronicle.storage import connect, init_db
    conn = connect(tmp_path / "c.db"); init_db(conn)
    pr = build_provider({"providers": {"chat": cfg()}}, conn)
    _payload_of(pr, monkeypatch)
    pr.chat("s", "u", stage="untangle")
    row = conn.execute("SELECT kind, tokens_in, tokens_out FROM llm_cache").fetchone()
    # total 30 - prompt 10 = 20 billed out, though completion_tokens said 2
    assert row[0] == "untangle" and row[1] == 10 and row[2] == 20


def test_the_cache_answers_the_second_time(tmp_path, monkeypatch):
    from gitchronicle.storage import connect, init_db
    conn = connect(tmp_path / "c.db"); init_db(conn)
    pr = build_provider({"providers": {"chat": cfg()}}, conn)
    calls = []

    def fake_post(url, payload, headers, retries=6):
        calls.append(payload)
        return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}
    monkeypatch.setattr(pr, "_post", fake_post)
    assert pr.chat("s", "u") == {"ok": True}
    assert pr.chat("s", "u") == {"ok": True}
    assert len(calls) == 1
    pr.cache_only = True
    with pytest.raises(NotCached):
        pr.chat("s", "different question")


def test_changing_a_generation_knob_is_a_different_answer(tmp_path, monkeypatch):
    from gitchronicle.storage import connect, init_db
    conn = connect(tmp_path / "c.db"); init_db(conn)
    calls = []

    def run(**extra):
        pr = build_provider({"providers": {"chat": cfg(**extra)}}, conn)
        monkeypatch.setattr(pr, "_post", lambda *a, **k: (
            calls.append(1), {"choices": [{"message": {"content": "{}"}}], "usage": {}})[1])
        pr.chat("s", "u")
    run()
    run()
    assert len(calls) == 1
    run(thinking_budget=0)          # a different request deserves a different cache entry
    assert len(calls) == 2


def test_every_kind_is_documented():
    assert set(KINDS) == {"openai", "azure", "anthropic", "ollama", "vertex"}
    assert isinstance(Provider.__doc__, str)
