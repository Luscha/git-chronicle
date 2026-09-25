"""The model hookup: what a config turns into on the wire, and what is refused."""

from __future__ import annotations

import pytest

from gitchronicle.llm.endpoints import KINDS, ROLES
from gitchronicle.llm.provider import (LLMError, NotCached, Provider, _auth_header,
                                       _url, build_provider)


def conf(roles=None, endpoints=None, **role_fields):
    """A whole configuration: one endpoint, one model, unless a test says otherwise."""
    return {"endpoints": endpoints or {"e": {"base_url": "https://api.example.com/v1",
                                             "api_key": "secret"}},
            "roles": roles or {"chat": {"model": "m", **role_fields}}}


def cfg(**kw):
    """One role's resolved shape, for the payload tests."""
    base = {"protocol": "openai", "base_url": "https://api.example.com/v1", "model": "m",
            "api_key": "secret"}
    base.update(kw)
    return base


def test_one_endpoint_and_one_model_is_a_whole_configuration():
    pr = build_provider(conf())
    for role in ROLES:
        assert pr._role_cfg(role)["model"] == "m"


def test_judge_follows_answer_before_chat():
    """Grading is answering — but with a different model, if you say so."""
    pr = build_provider(conf(roles={"chat": {"model": "small"}, "answer": {"model": "big"}}))
    assert pr._role_cfg("judge")["model"] == "big"
    assert pr._role_cfg("untangle")["model"] == "small"


def test_a_role_can_live_on_another_endpoint_entirely():
    """The reason this is two tables: 6,000 untangle calls locally, answers on a frontier
    model — stated once each, not repeated per role."""
    pr = build_provider(conf(
        endpoints={"local": {"kind": "ollama"}, "work": {"kind": "groq", "api_key": "k"}},
        roles={"chat": {"endpoint": "work", "model": "big"},
               "untangle": {"endpoint": "local", "model": "qwen", "think": "off"}}))
    assert pr._role_cfg("untangle")["protocol"] == "ollama"
    assert pr._role_cfg("untangle")["think_budget"] == 0
    assert pr._role_cfg("answer")["base_url"].startswith("https://api.groq.com")


@pytest.mark.parametrize("bad, match", [
    ({}, "no model configured"),
    ({"endpoints": {"e": {"kind": "azure"}}, "roles": {"chat": {"model": "m"}}},
     "no base_url"),
    ({"endpoints": {"e": {"base_url": "https://x"}}, "roles": {"chat": {}}}, "needs a model"),
    ({"endpoints": {"e": {"kind": "telepathy"}}, "roles": {"chat": {"model": "m"}}},
     "not one I ship a row for"),
    ({"endpoints": {"e": {"base_url": "https://x"}}, "roles": {"wobble": {"model": "m"}}},
     "no model configured"),
    ({"endpoints": {"a": {"base_url": "https://x"}, "b": {"base_url": "https://y"}},
      "roles": {"chat": {"model": "m"}}}, "names no endpoint"),
])
def test_bad_config_fails_before_anything_is_spent(bad, match):
    with pytest.raises(LLMError, match=match):
        build_provider(bad)


def test_auth_is_a_method_not_a_vendor_name():
    """Five ways to prove who you are — a vendor nobody here has heard of is one of them
    with a different header, and needs no code."""
    assert _auth_header(cfg())["Authorization"] == "Bearer secret"
    az = _auth_header(cfg(auth="header:api-key"))
    assert az["api-key"] == "secret" and "Authorization" not in az
    an = _auth_header(cfg(auth="header:x-api-key", protocol="anthropic"))
    assert an["x-api-key"] == "secret" and an["anthropic-version"]
    assert _auth_header(cfg(auth="none")) == {}
    assert _auth_header(cfg(headers={"x-org": "acme"}))["x-org"] == "acme"
    # some endpoints take the key in the URL instead
    assert "k=secret" in _url(cfg(auth="query:k"), "/chat/completions")


def test_api_version_and_custom_query_survive():
    url = _url(cfg(base_url="https://x.openai.azure.com/openai/deployments/d",
                   api_version="2024-10-21"), "/chat/completions")
    assert "api-version=2024-10-21" in url
    assert "beta=1" in _url(cfg(query={"beta": 1}), "/chat/completions")


def test_a_kind_carries_its_own_address_and_protocol():
    pr = build_provider(conf(endpoints={"e": {"kind": "anthropic", "api_key": "k"}}))
    assert pr.chat_cfg["base_url"] == "https://api.anthropic.com"
    assert pr.chat_cfg["protocol"] == "anthropic"
    assert _url(pr.chat_cfg, "/v1/messages").endswith("/v1/messages")


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
    pr = build_provider(conf(think="off", params={"max_tokens": 128}))
    seen = _payload_of(pr, monkeypatch)
    pr.chat("s", "u")
    assert seen["payload"]["max_tokens"] == 128
    # an OpenAI-compatible endpoint takes an effort, and used to be sent Google's body
    assert seen["payload"]["reasoning_effort"] == "none"
    assert "extra_body" not in seen["payload"]


def test_thinking_is_written_the_way_each_endpoint_spells_it(monkeypatch):
    """One setting for the user; six wire formats. Sending Google's shape to Groq was
    wrong even where it was silently ignored."""
    goog = build_provider(conf(endpoints={"g": {"dialect": "google", "base_url": "https://g"}},
                               roles={"chat": {"model": "m", "think": "off"}}))
    seen = _payload_of(goog, monkeypatch)
    goog.chat("s", "u")
    assert seen["payload"]["extra_body"]["google"]["thinking_config"]["thinking_budget"] == 0
    ollama = build_provider(conf(endpoints={"o": {"kind": "ollama"}},
                                 roles={"chat": {"model": "m", "think": "off"}}))
    seen = _payload_of(ollama, monkeypatch)
    ollama.chat("s", "u", want_json=False)
    assert seen["payload"]["think"] is False
    # Anthropic has no "off": thinking is simply not enabled
    anth = build_provider(conf(endpoints={"a": {"kind": "anthropic", "api_key": "k"}},
                               roles={"chat": {"model": "m", "think": "off"}}))
    seen = _payload_of(anth, monkeypatch)
    anth.chat("s", "u", want_json=False)
    assert "thinking" not in seen["payload"]


def test_a_kind_with_one_home_needs_no_base_url():
    """A kind is sugar for a row of data — never a branch. Adding a vendor is a row, or
    four fields in your own config, and never a release here."""
    for kind, expect in (("gemini", "generativelanguage"), ("anthropic", "api.anthropic.com"),
                         ("openai", "api.openai.com"), ("ollama", "localhost:11434"),
                         ("groq", "api.groq.com"), ("openrouter", "openrouter.ai"),
                         ("litellm", "localhost:4000")):
        pr = build_provider(conf(endpoints={"e": {"kind": kind, "api_key": "k"}}))
        assert expect in pr.chat_cfg["base_url"]


def test_anthropic_shapes_system_and_max_tokens(monkeypatch):
    pr = build_provider(conf(endpoints={"a": {"kind": "anthropic", "api_key": "k"}},
                             roles={"chat": {"model": "claude", "think": 1024}}))
    seen = _payload_of(pr, monkeypatch)
    pr.chat("be brief", "hello", want_json=False)
    assert seen["payload"]["system"] == "be brief"
    assert seen["payload"]["messages"][0]["content"] == "hello"
    assert seen["payload"]["thinking"]["budget_tokens"] == 1024
    assert seen["url"].endswith("/v1/messages")


def test_cost_counts_thinking_tokens(tmp_path, monkeypatch):
    from gitchronicle.storage import connect, init_db
    conn = connect(tmp_path / "c.db"); init_db(conn)
    pr = build_provider(conf(), conn)
    _payload_of(pr, monkeypatch)
    pr.chat("s", "u", stage="untangle")
    row = conn.execute("SELECT kind, tokens_in, tokens_out FROM llm_cache").fetchone()
    # total 30 - prompt 10 = 20 billed out, though completion_tokens said 2
    assert row[0] == "untangle" and row[1] == 10 and row[2] == 20


def test_the_cache_answers_the_second_time(tmp_path, monkeypatch):
    from gitchronicle.storage import connect, init_db
    conn = connect(tmp_path / "c.db"); init_db(conn)
    pr = build_provider(conf(), conn)
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
        pr = build_provider(conf(**extra), conn)
        monkeypatch.setattr(pr, "_post", lambda *a, **k: (
            calls.append(1), {"choices": [{"message": {"content": "{}"}}], "usage": {}})[1])
        pr.chat("s", "u")
    run()
    run()
    assert len(calls) == 1
    run(think="off")                # a different request deserves a different cache entry
    assert len(calls) == 2


def test_every_kind_is_documented():
    # the row table may grow freely; the PROTOCOLS are what cost code
    from gitchronicle.llm.endpoints import PROTOCOLS
    assert set(PROTOCOLS) == {"openai", "anthropic", "ollama"}
    assert {"openai", "azure", "anthropic", "ollama", "vertex", "gemini"} <= set(KINDS)
    assert isinstance(Provider.__doc__, str)


def test_a_key_can_come_from_a_file_the_host_mounted(tmp_path):
    """docker and k8s mount secrets as files, and an agent host that launches the process
    can point at one without the key touching a command line, a config or the environment."""
    from gitchronicle.llm.provider import _resolve_secret
    secret = tmp_path / "key"
    secret.write_text("sk-from-a-file\n")
    assert _resolve_secret(f"file:{secret}") == "sk-from-a-file"
    assert _resolve_secret("env:NOT_SET_ANYWHERE") == ""
    assert _resolve_secret("sk-literal") == "sk-literal"


def test_a_role_can_be_pointed_elsewhere_without_editing_a_file():
    """Flags and the environment are the only channel a host has for a process it starts —
    and the environment alone, with no config file at all, is a whole configuration."""
    from gitchronicle.llm.provider import apply_overrides
    cfg = {"endpoints": {"e": {"kind": "openai", "api_key": "k"}},
           "roles": {"chat": {"model": "small"}}}
    said = apply_overrides(cfg, models=["answer=big"], thinks=["answer=off"],
                           ends=[], env={"GITCHRONICLE_UNTANGLE_THINK": "medium"})
    pr = build_provider(cfg)
    assert pr._role_cfg("answer")["model"] == "big"
    assert pr._role_cfg("answer")["think_budget"] == 0
    assert pr._role_cfg("untangle")["think_effort"] == "medium"
    assert any("env" in x for x in said) and any("flag" in x for x in said)

    from gitchronicle.llm.endpoints import resolve
    only_env = resolve({}, env={"GITCHRONICLE_KIND": "openai", "GITCHRONICLE_API_KEY": "k",
                                "GITCHRONICLE_MODEL": "gpt-4o-mini"})
    assert only_env["chat"]["model"] == "gpt-4o-mini"
    assert only_env["untangle"]["base_url"] == "https://api.openai.com/v1"


def test_settings_edit_the_two_tables_and_leave_the_file_alone(tmp_path):
    """The studio writes where a job runs and what it runs — never a credential, and never
    anything else in a file somebody else wrote."""
    from gitchronicle import settings
    cfg = tmp_path / "config.toml"
    cfg.write_text('# my notes\n[repo]\npath = "/x"\n\n[endpoints.work]\n'
                   '# the cheap one\nkind = "groq"\napi_key = "env:KEY"\n\n'
                   '[roles.chat]\nmodel = "small"\n')
    before = settings.read(cfg)
    assert [e["name"] for e in before["endpoints"]] == ["work"]
    assert before["endpoints"][0]["key"] == "env:KEY"      # the reference, not the secret
    assert [r["role"] for r in before["roles"] if r["set"]] == ["chat"]

    settings.write(cfg, endpoints={"local": {"kind": "ollama"}},
                   roles={"untangle": {"endpoint": "local", "model": "qwen", "think": "off"},
                          "answer": {"model": "big"}})
    text = cfg.read_text()
    assert "# my notes" in text and "# the cheap one" in text and 'api_key = "env:KEY"' in text
    after = settings.read(cfg)
    roles = {r["role"]: r for r in after["roles"]}
    assert roles["untangle"]["endpoint"] == "local" and roles["untangle"]["think"] == "off"
    assert roles["chat"]["model"] == "small"               # untouched
    # and the file still knows the addresses, for someone who has not chosen yet
    assert "groq" in after["kinds"] and "ollama" in after["kinds"]
    # it all still resolves
    pr = build_provider({"endpoints": {e["name"]: {"kind": e["kind"]} for e in after["endpoints"]},
                         "roles": {r["role"]: {"endpoint": r["endpoint"] or "work",
                                               "model": r["model"]}
                                   for r in after["roles"] if r["set"]}})
    assert pr._role_cfg("untangle")["protocol"] == "ollama"
    assert pr._role_cfg("answer")["protocol"] == "openai"


def test_a_key_is_never_printed_only_where_it_came_from(tmp_path):
    """A reference names a location and is safe to show; a literal key is not — and one
    reached a terminal because the environment path passed the value straight through."""
    from gitchronicle.llm.endpoints import key_ref
    assert key_ref({"api_key": "env:OPENAI_API_KEY"}) == "env:OPENAI_API_KEY"
    assert key_ref({"api_key": "file:/run/secrets/k"}) == "file:/run/secrets/k"
    assert key_ref({"auth": "adc"}) == "application default credentials"
    assert key_ref({}) == "none"
    shown = key_ref({"api_key": "sk-supersecretvalue"})
    assert "supersecret" not in shown and shown.endswith("alue)")


def test_a_key_typed_in_the_studio_lands_beside_the_config_not_in_it(tmp_path):
    """Refusing to take a key at all protected nothing on a localhost tool and forced
    everyone into a text editor. What matters is WHERE it is written: the .env beside the
    config (0600), with the config carrying only the reference — so it stays shareable."""
    from gitchronicle import settings
    cfg = tmp_path / "config.toml"
    cfg.write_text('[endpoints.work]\nkind = "groq"\n')
    ref = settings.write_secret(cfg, "work", "sk-super-secret")
    assert ref == "env:WORK_API_KEY"
    env = tmp_path / ".env"
    assert env.read_text().strip() == "WORK_API_KEY=sk-super-secret"
    assert oct(env.stat().st_mode)[-3:] == "600"
    settings.write(cfg, endpoints={"work": {"api_key": ref}})
    assert "sk-super-secret" not in cfg.read_text()
    assert settings.read(cfg)["endpoints"][0]["key"] == "env:WORK_API_KEY"
    # a reference typed instead of a key is taken as one, and stores nothing
    assert settings.write_secret(cfg, "work", "file:/run/secrets/k") == "file:/run/secrets/k"
    # and the caller can insist on keeping one file
    assert settings.write_secret(cfg, "work", "sk-inline", mode="inline") == "sk-inline"


def test_a_role_can_be_handed_back_to_its_fallback(tmp_path):
    """"Inherit" is the ABSENCE of a block. Clearing the endpoint and keeping the model
    leaves a role that is not inheriting, it is broken — and the panel offered exactly
    that: a blank entry that cleared one field."""
    from gitchronicle import settings
    cfg = tmp_path / "config.toml"
    cfg.write_text('[endpoints.a]\nkind = "groq"\n\n[roles.chat]\nmodel = "big"\n\n'
                   '[roles.naming]\nendpoint = "a"\nmodel = "small"\n\n[pricing.x]\ninput = 1\n')
    settings.write(cfg, roles={"naming": {"_remove": True}})
    text = cfg.read_text()
    assert "[roles.naming]" not in text
    assert "[roles.chat]" in text and "[pricing.x]" in text     # nothing else disturbed
    pr = build_provider({"endpoints": {"a": {"kind": "groq", "api_key": "k"}},
                         "roles": {"chat": {"model": "big"}}})
    assert pr._role_cfg("naming")["model"] == "big"             # it follows chat again
