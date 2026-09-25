"""ENDPOINTS — where models live, separately from which job uses them.

A provider is three independent things, and folding them into one `kind` is what makes a
vendor list go stale: a PROTOCOL (how to talk), an ADDRESS with an AUTH method (where, and
how to prove you may), and a DIALECT (how that endpoint spells "think less"). Only the
first is code, and there are three of them. Everything else is data, so a new vendor is a
few lines in your own config and never a release here.

    [endpoints.work]
    base_url = "https://api.groq.com/openai/v1"
    auth     = "bearer"
    api_key  = "env:GROQ_API_KEY"

    [roles.untangle]
    endpoint = "work"
    model    = "llama-3.3-70b-versatile"
    think    = "off"

`kind` is sugar for a row of the table below — never a branch in the code — and anything
it does not cover is the same four fields written out. For the true long tail, point an
endpoint at a LiteLLM or OpenRouter proxy: that is the OpenAI protocol, and it costs this
project no maintenance at all.
"""

from __future__ import annotations

# The jobs. Each falls back, so one endpoint and one model is a complete configuration.
ROLES = ("chat", "answer", "untangle", "naming", "narration", "judge")
FALLBACK = {"answer": ("chat",), "untangle": ("chat",), "naming": ("chat",),
            "narration": ("chat",), "judge": ("answer", "chat")}
ROLE_HELP = {
    "chat": "the default — every job that names no model of its own",
    "answer": "answering questions: `ask`, and the studio",
    "untangle": "one call per commit: what changed and why. The bulk of the bill",
    "naming": "the names in your catalogue — pin it, changing it renames everything",
    "narration": "the stories",
    "judge": "grading in `eval` — worth a different model from the one that answered",
}

PROTOCOLS = ("openai", "anthropic", "ollama")   # the only thing that is code
DIALECTS = ("openai", "google", "anthropic", "ollama", "none")

# Sugar. Each row is data: protocol, address, how to authenticate, how it spells thinking.
# Adding one here saves typing; not having one costs nothing.
KINDS: dict[str, dict] = {
    "openai": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "dialect": "google", "key_env": "GEMINI_API_KEY"},
    "vertex": {"auth": "adc", "dialect": "google"},          # base_url built from project
    "anthropic": {"protocol": "anthropic", "base_url": "https://api.anthropic.com",
                  "auth": "header:x-api-key", "dialect": "anthropic",
                  "key_env": "ANTHROPIC_API_KEY"},
    "ollama": {"protocol": "ollama", "base_url": "http://localhost:11434/v1",
               "auth": "none", "dialect": "ollama"},
    "azure": {"auth": "header:api-key", "key_env": "AZURE_OPENAI_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "together": {"base_url": "https://api.together.xyz/v1", "key_env": "TOGETHER_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "scaleway": {"base_url": "https://api.scaleway.ai/v1", "key_env": "SCALEWAY_API_KEY"},
    "deepinfra": {"base_url": "https://api.deepinfra.com/v1/openai"},
    "fireworks": {"base_url": "https://api.fireworks.ai/inference/v1"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY"},
    "xai": {"base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    "llamacpp": {"base_url": "http://localhost:8080/v1", "auth": "none"},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "auth": "none"},
    "vllm": {"base_url": "http://localhost:8000/v1", "auth": "none"},
    "litellm": {"base_url": "http://localhost:4000", "key_env": "LITELLM_API_KEY"},
}
_DEFAULTS = {"protocol": "openai", "auth": "bearer", "dialect": "openai"}


def key_ref(cfg: dict) -> str:
    """Where a credential comes from, never the credential.

    A reference (`env:`, `file:`, `${}`) IS the safe thing to show — it names a location.
    A literal key is not, and one reached a terminal once because the environment path
    passed the value straight through. Everything that prints a key goes through here.
    """
    if cfg.get("auth") == "adc":
        return "application default credentials"
    raw = str(cfg.get("_api_key_ref") or cfg.get("api_key") or "")
    if raw.startswith(("env:", "file:", "${")):
        return raw
    if not raw:
        return "none"
    return f"set, not shown (…{raw[-4:]})"


def resolve_endpoint(name: str, spec: dict) -> dict:
    """One endpoint: its kind's row, then whatever the config says over the top."""
    out = dict(_DEFAULTS)
    kind = spec.get("kind")
    if kind:
        row = KINDS.get(str(kind).lower())
        if row is None:
            raise ValueError(
                f"endpoints.{name}.kind = {kind!r} is not one I ship a row for. Either "
                f"write base_url/auth yourself, or pick one of: {', '.join(sorted(KINDS))}")
        out.update({k: v for k, v in row.items() if k != "key_env"})
        if row.get("key_env") and not spec.get("api_key"):
            out["api_key"] = f"env:{row['key_env']}"
    out.update({k: v for k, v in spec.items() if v is not None})
    out["name"] = name
    if out["protocol"] not in PROTOCOLS:
        raise ValueError(f"endpoints.{name}.protocol = {out['protocol']!r}; "
                         f"the protocols are {', '.join(PROTOCOLS)}")
    return out


def _think(value) -> dict:
    """`think` as the config writes it, kept in its own units until the wire."""
    v = str(value).strip().lower()
    if v in ("off", "none", "no", "0"):
        return {"think_budget": 0}
    if v.isdigit():
        return {"think_budget": int(v)}
    if v in ("low", "medium", "high"):
        return {"think_effort": v}
    raise ValueError(f"think = {value!r}: use off, a token budget, or low|medium|high")


def from_env(cfg: dict, env=None) -> bool:
    """A whole configuration out of the environment, for a process nobody wrote a file for.

    Written INTO the config rather than resolved past it, so `models`, `doctor` and the
    studio all describe the same setup — the first version resolved it privately and every
    surface but the run itself reported "nothing configured".
    """
    import os

    env = os.environ if env is None else env
    if not (env.get("GITCHRONICLE_BASE_URL") or env.get("GITCHRONICLE_KIND")):
        return False
    ends = cfg.setdefault("endpoints", {})
    ends.setdefault("env", {k: v for k, v in {
        "kind": env.get("GITCHRONICLE_KIND"),
        "base_url": env.get("GITCHRONICLE_BASE_URL"),
        "api_key": env.get("GITCHRONICLE_API_KEY"),
        "protocol": env.get("GITCHRONICLE_PROTOCOL"),
        "auth": env.get("GITCHRONICLE_AUTH"),
        "dialect": env.get("GITCHRONICLE_DIALECT")}.items() if v})
    if env.get("GITCHRONICLE_MODEL"):
        cfg.setdefault("roles", {}).setdefault(
            "chat", {"endpoint": "env", "model": env["GITCHRONICLE_MODEL"]})
    return True


def resolve(cfg: dict, env=None) -> dict:
    """Every role, fully resolved: endpoint + model + thinking, ready for the wire.

    Roles inherit along FALLBACK, so one endpoint and one model is a whole configuration.
    An environment with no config file at all still works — which is what an agent host
    launching `gitchronicle mcp` or a CI job has.
    """
    import os

    env = os.environ if env is None else env
    from_env(cfg, env)            # a configuration with no file at all
    ends = {n: resolve_endpoint(n, s) for n, s in (cfg.get("endpoints") or {}).items()}
    roles = dict(cfg.get("roles") or {})

    out: dict[str, dict] = {}
    for role in ROLES:
        spec = roles.get(role)
        if spec is None:
            continue
        spec = dict(spec)
        name = spec.pop("endpoint", None) or (next(iter(ends)) if len(ends) == 1 else None)
        if name is None:
            raise ValueError(f"roles.{role} names no endpoint, and there is more than one "
                             f"to guess from ({', '.join(sorted(ends)) or 'none'})")
        if name not in ends:
            raise ValueError(f"roles.{role}.endpoint = {name!r}; "
                             f"endpoints are {', '.join(sorted(ends)) or 'none'}")
        merged = {**ends[name], **spec}
        if "think" in merged:
            merged.update(_think(merged.pop("think")))
        if not merged.get("model"):
            raise ValueError(f"roles.{role} needs a model")
        out[role] = merged
    # a role with no block of its own follows the chain, so `chat` alone is enough
    for role in ROLES:
        if role in out:
            continue
        for parent in FALLBACK.get(role, ()):
            if parent in out:
                out[role] = {**out[parent], "inherited_from": parent}
                break
    return out
