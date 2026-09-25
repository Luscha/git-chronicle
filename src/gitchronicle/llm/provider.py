"""A thin, provider-agnostic LLM layer.

Two capabilities — ``chat`` and ``embed`` — over any OpenAI-compatible endpoint
(Ollama, OpenAI, Scaleway, Vertex, ...). Chat responses are cached in ``llm_cache`` so
re-labelling never re-pays. Swapping providers is a config change, not a code change.

``kind="vertex"`` reaches Vertex AI through its OpenAI-compatible surface, so it reuses
the OpenAI request path entirely and differs only in how it authorises: no API key, an
OAuth token minted from Application Default Credentials. Vertex exposes no
OpenAI-compatible *embeddings* endpoint — keep embeddings on Ollama (or another
OpenAI-compatible provider) when chat runs on Vertex.

``kind="anthropic"`` speaks the native Messages API, and ``kind="azure"`` adds the
api-key header and api-version query Azure OpenAI wants. Anything else that speaks
OpenAI (Groq, OpenRouter, llama.cpp, LM Studio, Together, ...) needs no adapter: give it
a ``base_url``. Provider-specific knobs never need code — ``headers``, ``query`` and
``params`` are passed through as written.

Embeddings are optional: nothing in the current pipeline embeds, and a config without an
``embed`` role simply cannot call ``embed()``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

import httpx
import numpy as np


def _parse_duration(s: str) -> float:
    """Parse a duration like '50s939ms', '362ms', '1m2s', '1.5s' into seconds."""
    total = 0.0
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)", s):
        total += float(val) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return total


def _retry_after_seconds(resp, default: float = 5.0, cap: float = 65.0) -> float:
    """How long to wait after a 429, from Retry-After or a rate-limit-reset header."""
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return min(float(ra) + 0.5, cap)
        except ValueError:
            pass
    reset = (resp.headers.get("x-ratelimit-reset-tokens")
             or resp.headers.get("x-ratelimit-reset-requests"))
    if reset:
        secs = _parse_duration(reset)
        if secs > 0:
            return min(secs + 0.5, cap)
    return default

from ..storage import now_iso
from . import endpoints
from .endpoints import KINDS, ROLES


class LLMError(RuntimeError):
    pass


class NotCached(LLMError):
    """A cache-only provider was asked for something it has never paid for."""


_VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class _AuthResponse:
    """google-auth's transport response shape (status/headers/data)."""

    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        self.headers = resp.headers
        self.data = resp.content


class _HttpxAuthRequest:
    """google-auth's transport interface over httpx.

    google-auth ships transports for `requests`, `urllib3` and gRPC; this project speaks
    httpx everywhere, so the token refresh borrows that client rather than making the
    vertex extra drag in a second HTTP stack.
    """

    def __init__(self):
        self._client = httpx.Client(timeout=60)

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        return _AuthResponse(self._client.request(
            method, url, content=body, headers=headers, timeout=timeout or 60))


class _ADCToken:
    """OAuth bearer minted from Application Default Credentials.

    Vertex refuses API keys. Its tokens live about an hour — less than a full untangle
    over a large history — so the token is refreshed in place instead of being resolved
    once at startup, and the refresh is locked because untangle runs several workers
    against this one credential.
    """

    _SKEW = 300.0        # refresh this early: a call must not start on a dying token

    def __init__(self):
        try:
            import google.auth
        except ImportError as exc:
            raise LLMError("kind='vertex' needs google-auth: pip install 'gitchronicle[vertex]'"
                           ) from exc
        self._request = _HttpxAuthRequest()
        self._creds, self.project = google.auth.default(scopes=[_VERTEX_SCOPE])
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if not self._creds.token or self._stale():
                self._creds.refresh(self._request)
            return self._creds.token

    def _stale(self) -> bool:
        exp = getattr(self._creds, "expiry", None)
        if exp is None:
            return False
        from datetime import datetime, timezone
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return (exp - datetime.now(timezone.utc)).total_seconds() < self._SKEW


_adc: _ADCToken | None = None
_adc_lock = threading.Lock()


def _adc_token() -> _ADCToken:
    """The process-wide ADC credential (built on first use, so non-Vertex runs never
    touch google-auth and never need it installed)."""
    global _adc
    with _adc_lock:
        if _adc is None:
            _adc = _ADCToken()
    return _adc


def _auth_header(cfg: dict) -> dict:
    """How this endpoint wants to be told who you are — declared, not inferred.

    `bearer` · `header:<name>` · `query:<name>` (added in _url) · `adc` · `none`. A vendor
    nobody here has heard of is one of these five with a different header name.
    """
    auth = str(cfg.get("auth", "bearer"))
    key = cfg.get("api_key", "")
    if auth == "adc":
        h = {"Authorization": f"Bearer {_adc_token().token()}"}
        if cfg.get("project"):
            # user ADC (`gcloud auth application-default login`) carries no billing
            # project of its own; Vertex bills and quotas against this one.
            h["x-goog-user-project"] = str(cfg["project"])
    elif auth.startswith("header:"):
        h = {auth.split(":", 1)[1]: key}
    elif auth in ("none", "query") or auth.startswith("query:"):
        h = {}
    else:
        h = {"Authorization": f"Bearer {key or 'x'}"}
    if cfg.get("protocol") == "anthropic":
        h.setdefault("anthropic-version", cfg.get("anthropic_version", "2023-06-01"))
    # whatever the endpoint in front of the model needs: org ids, gateway keys, tenancy
    h.update({str(k): str(v) for k, v in (cfg.get("headers") or {}).items()})
    return h


def _url(cfg: dict, path: str) -> str:
    """Endpoint URL, with any query the provider needs (Azure wants api-version)."""
    base = cfg["base_url"].rstrip("/")
    if cfg.get("protocol") == "anthropic" and base.endswith("/v1"):
        base = base[:-3]                      # /v1/messages is appended whole
    url = base + path
    q = dict(cfg.get("query") or {})
    if cfg.get("api_version") and "api-version" not in q:
        q["api-version"] = cfg["api_version"]
    auth = str(cfg.get("auth", ""))
    if auth.startswith("query:"):             # endpoints that take the key in the URL
        q[auth.split(":", 1)[1]] = cfg.get("api_key", "")
    if q:
        from urllib.parse import urlencode
        url += ("&" if "?" in url else "?") + urlencode(q)
    return url


def _vertex_base_url(cfg: dict) -> str:
    """The OpenAI-compatible surface; the caller's client appends /chat/completions."""
    loc = cfg.get("location") or "us-central1"
    project = cfg.get("project") or _adc_token().project
    if not project:
        raise LLMError("an adc endpoint needs a project (ADC carries no default one)")
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project}/locations/{loc}/endpoints/openapi"


def _extract_json(text: str) -> dict:
    """Tolerant JSON parse: handles code fences / prose around the object."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise LLMError(f"Could not parse JSON from model response: {text[:200]!r}")


class Provider:
    """Every role, already resolved to an endpoint and a model, plus the response cache.

    Resolution (which endpoint, which model, what fallback) happens once in
    ``llm/endpoints.py``; nothing here decides it again. What is left is protocol: three
    ways of speaking, chosen by the endpoint's own ``protocol``.
    """

    def __init__(self, roles: dict, conn=None):
        if "chat" not in roles:
            raise LLMError("no [roles.chat]: every run needs one default model")
        self.roles = roles
        self.chat_cfg = roles["chat"]
        self.conn = conn
        self.cache_only = False
        self.db_lock = threading.Lock()   # guards the shared sqlite conn (concurrent untangle)
        self._refused_thinking: set[str] = set()   # models that reject the setting outright
        timeout = max((float(c.get("timeout", 900)) for c in roles.values()), default=900)
        self._client = httpx.Client(timeout=timeout)

    def _role_cfg(self, role: str) -> dict:
        return self.roles.get(role) or self.chat_cfg

    # -- chat -------------------------------------------------------------
    def chat(self, system: str, user: str, want_json: bool = True,
             cache_extra: str = "", large: bool = False,
             role: str | None = None, stage: str = "") -> dict | str:
        cfg = self._role_cfg(role or ("answer" if large else "chat"))
        provider, model = cfg.get("protocol", "openai"), cfg["model"]
        # Generation params are part of the identity of a response — cache on them too, so
        # changing temperature/seed correctly invalidates stale entries.
        key = _cache_key(provider, model, "chat", system, user, cache_extra,
                         str(cfg.get("temperature", "")), str(cfg.get("seed", "")),
                         # part of the key only when set, so existing caches stay valid
                         *[f"{k}={cfg[k]}" for k in ("think_effort", "think_budget")
                           if cfg.get(k) is not None])
        cached = self._cache_get(key)
        if cached is not None:
            return _extract_json(cached) if want_json else cached
        if self.cache_only:
            raise NotCached(key)

        if provider == "ollama":
            content, usage = self._chat_ollama(cfg, system, user, want_json)
        elif provider == "anthropic":
            content, usage = self._chat_anthropic(cfg, system, user, want_json)
        else:
            content, usage = self._chat_openai(cfg, system, user, want_json)
        # the row remembers WHICH job paid, so `gitchronicle cost` can say where the
        # money went rather than only how much
        self._cache_put(key, provider, model, content, usage,
                        stage or role or ("answer" if large else "chat"))
        return _extract_json(content) if want_json else content

    def _chat_openai(self, cfg: dict, system: str, user: str, want_json: bool):
        url = _url(cfg, "/chat/completions")
        headers = _auth_header(cfg)
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": float(cfg.get("temperature", 0.2)),
        }
        if cfg.get("seed") is not None:   # reproducible sampling (generic; honoured where supported)
            payload["seed"] = int(cfg["seed"])
        # Thinking bills as output — over half of all untangle tokens here — and every
        # vendor spells the setting differently. One setting, translated: a budget is
        # Google's shape, an effort is OpenAI's, and writing Google's into an OpenAI-
        # compatible endpoint (which this did) is wrong even when it is silently ignored.
        _apply_thinking(cfg, payload)
        # anything else the endpoint accepts (max_tokens, top_p, provider extensions):
        # written into the body as given, so a new knob never needs a new release
        payload.update(cfg.get("params") or {})
        # json_mode=false is the escape hatch for endpoints that reject response_format;
        # _extract_json already tolerates a model that answers with prose around the object.
        if want_json and cfg.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        data = self._post(url, payload, headers)
        return data["choices"][0]["message"]["content"], data.get("usage")

    def list_models(self, role: str = "chat") -> list[str]:
        """What this role's endpoint says it can run — the question a model name answers
        badly. Every kind has a listing; a failure returns nothing rather than raising, so
        a panel can offer free text beside it."""
        cfg = self._role_cfg(role)
        kind = cfg.get("protocol", "openai")
        root = cfg["base_url"].rstrip("/")
        try:
            if kind == "ollama":
                base = root[:-3].rstrip("/") if root.endswith("/v1") else root
                r = self._client.get(f"{base}/api/tags", timeout=10)
                if r.status_code >= 400:
                    raise LLMError(f"{r.status_code}: {r.text[:160]}")
                return sorted(m["name"] for m in r.json().get("models", []))
            if kind == "anthropic":
                r = self._client.get(f"{root}/v1/models", headers=_auth_header(cfg),
                                     timeout=10)
                if r.status_code >= 400:
                    raise LLMError(f"{r.status_code}: {r.text[:160]}")
                return sorted(m["id"] for m in r.json().get("data", []))
            if cfg.get("auth") == "adc":
                return []          # Vertex's OpenAI surface has no listing endpoint
            r = self._client.get(f"{root}/models", headers=_auth_header(cfg),
                                 params=cfg.get("query") or None, timeout=10)
            # a 401 answers with a perfectly good JSON body and no models in it; reporting
            # that as "this endpoint lists none" is how a wrong key looks like a shrug
            if r.status_code >= 400:
                raise LLMError(f"{r.status_code}: {r.text[:160]}")
            data = r.json()
            rows = data.get("data", data if isinstance(data, list) else [])
            if not rows and isinstance(data, dict) and data.get("error"):
                raise LLMError(str(data["error"])[:160])
            return sorted(str(m.get("id") or m.get("name") or m) for m in rows)
        except Exception as exc:   # noqa: BLE001 - a listing is a convenience, never a gate
            # ... but saying nothing turned a 401 into "this endpoint lists no models",
            # at the one moment a newcomer most needs to know which it was
            self.last_list_error = f"{type(exc).__name__}: {exc}"[:200]
            return []

    def _chat_anthropic(self, cfg: dict, system: str, user: str, want_json: bool):
        """The native Messages API: system is its own field, and JSON mode does not exist
        — the prompt already asks for JSON and _extract_json tolerates prose around it."""
        payload = {
            "model": cfg["model"],
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": int(cfg.get("max_tokens", 4096)),
            "temperature": float(cfg.get("temperature", 0.2)),
        }
        _apply_thinking(cfg, payload)
        payload.update(cfg.get("params") or {})
        data = self._post(_url(cfg, "/v1/messages"), payload, _auth_header(cfg))
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        u = data.get("usage") or {}
        return text, {"prompt_tokens": u.get("input_tokens"),
                      "completion_tokens": u.get("output_tokens")}

    def _chat_ollama(self, cfg: dict, system: str, user: str, want_json: bool):
        """Ollama's native /api/chat — lets us cap threads/context (num_thread,
        num_ctx) so a local model can't saturate every core and lock the machine."""
        root = cfg["base_url"].rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3].rstrip("/")
        options = {"temperature": float(cfg.get("temperature", 0.2))}
        if cfg.get("seed") is not None:
            options["seed"] = int(cfg["seed"])
        if cfg.get("num_thread"):
            options["num_thread"] = int(cfg["num_thread"])
        if cfg.get("num_ctx"):
            options["num_ctx"] = int(cfg["num_ctx"])
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": options,
            "keep_alive": cfg.get("keep_alive", "5m"),
        }
        if want_json:
            payload["format"] = "json"
        _apply_thinking(cfg, payload)          # Ollama 0.9+: think = true|false
        data = self._post(root + "/api/chat", payload, {})
        content = data.get("message", {}).get("content", "")
        usage = {"prompt_tokens": data.get("prompt_eval_count"),
                 "completion_tokens": data.get("eval_count")}
        return content, usage

    # -- transport + cache ------------------------------------------------
    def _post(self, url: str, payload: dict, headers: dict, retries: int = 6) -> dict:
        last: Exception | None = None
        attempt = rate_waits = 0
        while attempt < retries:
            try:
                resp = self._client.post(url, json=payload, headers=headers)
                if resp.status_code < 400:
                    return resp.json()
                if resp.status_code == 429:
                    # Rate limited. Token buckets refill CONTINUOUSLY, so wait just long enough
                    # for a partial refill (short, escalating) rather than the header's
                    # time-to-full — otherwise workers idle most of each window. Never give up on
                    # a 429 (that would emit a fallback concern); these waits don't count against
                    # the retry budget. Retry-After, if the server sends one, is an upper bound.
                    wait = min(2.0 + rate_waits * 2.0, _retry_after_seconds(resp, default=30, cap=30))
                    time.sleep(max(wait, 1.0))
                    rate_waits += 1
                    if rate_waits > 60:      # safety valve against being throttled forever
                        raise LLMError(f"POST {url}: rate-limited {rate_waits}x, giving up")
                    continue
                # other 4xx (bad request, auth, 402 credit) won't succeed on retry — fail fast.
                if resp.status_code < 500:
                    body = resp.text[:200]
                    # a reasoning model refusing a thinking setting is a config mistake with
                    # a fix, not a transport failure — say which
                    if resp.status_code == 400 and _about_thinking(body):
                        # Endpoints disagree about how to say "think less", and some reject
                        # the word rather than ignore it (Scaleway's gpt-oss: "does not
                        # support reasoning effort none"). Drop the setting once and go on:
                        # a run must not die because a model has opinions about a knob.
                        dropped = {k: v for k, v in payload.items()
                                   if k not in ("reasoning_effort", "think")}
                        dropped.pop("extra_body", None)
                        if dropped != payload:
                            self._refused_thinking.add(str(payload.get("model", "")))
                            return self._post(url, dropped, headers, retries - attempt)
                        raise LLMError(
                            f"{payload.get('model', '')} will not take that thinking setting "
                            f"— and dropping it did not help.\n  {body}")
                    raise LLMError(f"POST {url} -> {resp.status_code}: {body}")
                last = LLMError(f"{resp.status_code}: {resp.text[:120]}")   # 5xx: retry
            except (httpx.HTTPError, json.JSONDecodeError) as exc:  # transient transport error
                last = exc
            attempt += 1
            time.sleep(1.5 * attempt)
        raise LLMError(f"POST {url} failed after {retries} tries: {last}")

    def _cache_get(self, key: str) -> str | None:
        if self.conn is None:
            return None
        with self.db_lock:
            row = self.conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return row["response"] if row else None

    def _cache_put(self, key, provider, model, response, usage, stage: str = "chat") -> None:
        if self.conn is None:
            return
        ti = (usage or {}).get("prompt_tokens")
        to = (usage or {}).get("completion_tokens")
        # thinking models bill their reasoning as output but may leave it out of
        # completion_tokens; the total is what the invoice counts
        total = (usage or {}).get("total_tokens")
        if total and ti is not None and total - ti > (to or 0):
            to = total - ti
        with self.db_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache "
                "(key, provider, model, kind, response, tokens_in, tokens_out, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (key, provider, model, stage, response, ti, to, now_iso()),
            )
            self.conn.commit()

    def close(self) -> None:
        self._client.close()


def _cache_key(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _resolve_secret(value):
    """Expand an api_key written as 'env:VAR', '${VAR}' or 'file:/path'.

    A file reference is how a key reaches a process it did not start: docker and k8s mount
    secrets as files, and an agent host that launches `gitchronicle mcp` can point at one
    without the key ever appearing in a command line, a config file or the environment.
    """
    if isinstance(value, str):
        if value.startswith("env:"):
            return os.environ.get(value[4:], "")
        if value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1], "")
        if value.startswith("file:"):
            path = Path(value[5:]).expanduser()
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LLMError(f"api_key file {path}: {exc}") from exc
    return value


def _about_thinking(body: str) -> bool:
    low = body.lower()
    return any(w in low for w in ("thinking", "reasoning_effort", "reasoning effort"))


def _apply_thinking(cfg: dict, payload: dict) -> None:
    """Write this role's reasoning setting the way ITS endpoint spells it.

    `thinking_budget` is a number of tokens (0 = off) and `reasoning_effort` is a word;
    both are accepted from the config, and which one the wire carries is decided here by
    kind — not by whichever the user happened to write. Anything an endpoint invents
    beyond this still goes through `params`.
    """
    dialect = cfg.get("dialect", "openai")
    budget, effort = cfg.get("think_budget"), cfg.get("think_effort")
    if budget is None and not effort:
        return
    if dialect == "none":
        return
    if dialect == "google":
        # Google takes a budget; an effort is mapped to one, since its API has no efforts
        b = int(budget) if budget is not None else {"none": 0, "low": 512, "medium": 2048,
                                                    "high": 8192}.get(str(effort), 1024)
        payload.setdefault("extra_body", {})["google"] = {
            "thinking_config": {"thinking_budget": b}}
    elif dialect == "anthropic":
        # Anthropic has no "off": thinking is simply not enabled, and its minimum is 1024
        if budget == 0 or str(effort) == "none":
            return
        b = int(budget) if budget else {"low": 1024, "medium": 4096, "high": 16000}.get(
            str(effort), 1024)
        payload["thinking"] = {"type": "enabled", "budget_tokens": max(1024, b)}
    elif dialect == "ollama":
        payload["think"] = not (budget == 0 or str(effort) == "none")
    else:
        # OpenAI and everything speaking its protocol: an effort, never a Google body
        payload["reasoning_effort"] = (
            effort or {0: "none"}.get(int(budget) if budget is not None else -1)
            or ("low" if (budget or 0) <= 1024 else "medium" if (budget or 0) <= 4096
                else "high"))


def apply_overrides(cfg: dict, models=None, thinks=None, ends=None, env=None) -> list[str]:
    """Point a role at another model, another endpoint, or a different amount of thinking,
    without editing a file.

    `--model untangle=qwen2.5-coder:14b`, `--endpoint untangle=local`, `--think answer=off`,
    or the environment (`GITCHRONICLE_ANSWER_MODEL`, `GITCHRONICLE_UNTANGLE_THINK`) — which
    is the only channel an agent host or a CI job has for a process it starts itself.
    Returns what it changed, so `doctor` and `models` can say it out loud.
    """
    from ..config import OVERRIDES

    env = os.environ if env is None else env
    endpoints.from_env(cfg, env)
    models = OVERRIDES["model"] if models is None else models
    thinks = OVERRIDES["think"] if thinks is None else thinks
    ends = OVERRIDES.get("endpoint", []) if ends is None else ends
    roles = cfg.setdefault("roles", {})
    said = []

    def put(role: str, key: str, value, src: str):
        if role not in ROLES:
            raise LLMError(f"unknown role {role!r}; roles are {', '.join(ROLES)}")
        # a role nobody configured starts from chat's: pointing one job at another model
        # should not mean writing out an endpoint again
        if role not in roles:
            roles[role] = dict(roles.get("chat", {}))
        roles[role][key] = value
        said.append(f"roles.{role}.{key} = {value}  ({src})")

    for role in ROLES:
        var = role.upper()
        for key, suffix in (("model", "MODEL"), ("think", "THINK"), ("endpoint", "ENDPOINT")):
            if env.get(f"GITCHRONICLE_{var}_{suffix}"):
                put(role, key, env[f"GITCHRONICLE_{var}_{suffix}"], "env")
    for key, specs in (("model", models), ("think", thinks), ("endpoint", ends)):
        for spec in specs or ():
            role, _, value = str(spec).partition("=")
            if not value:
                raise LLMError(f"--{key} takes role=value, got {spec!r}")
            put(role.strip(), key, value.strip(), "flag")
    return said


def build_provider(cfg: dict, conn=None) -> Provider:
    """Resolve `[endpoints]` + `[roles]` into one Provider.

    Resolution — which endpoint a role uses, what it inherits, how thinking is spelled —
    is `llm.endpoints`. What is left here is what only this layer knows: credentials are
    expanded now (never stored expanded), and an ADC endpoint learns its own address.
    """
    try:
        roles = endpoints.resolve(cfg)
    except ValueError as exc:
        raise LLMError(str(exc)) from exc
    if "chat" not in roles:
        raise LLMError(
            "no model configured. One endpoint and one model is a whole configuration:\n"
            '  [endpoints.mine]\n  kind = "openai"        # or ollama, gemini, anthropic, '
            'groq, openrouter, ...\n  api_key = "env:OPENAI_API_KEY"\n\n'
            '  [roles.chat]\n  model = "gpt-4o-mini"\n\n'
            "`gitchronicle models` lists the endpoints it knows the address of.")
    for name, rc in roles.items():
        if rc.get("auth") == "adc":
            rc["project"] = rc.get("project") or _adc_token().project
            if "aiplatform.googleapis.com" not in (rc.get("base_url") or ""):
                rc["base_url"] = _vertex_base_url(rc)
        if not rc.get("base_url"):
            raise LLMError(f"roles.{name}: endpoint {rc.get('name')!r} has no base_url")
        # the reference is kept so doctor and the studio can say where a key comes from
        # without ever holding the key itself
        rc["_api_key_ref"] = rc.get("api_key", "")
        rc["api_key"] = _resolve_secret(rc.get("api_key", ""))
    return Provider(roles, conn=conn)
