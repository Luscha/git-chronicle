"""A thin, provider-agnostic LLM layer.

Two capabilities — ``chat`` and ``embed`` — over any OpenAI-compatible endpoint
(Ollama, OpenAI, Scaleway, Vertex, ...). Chat responses are cached in ``llm_cache`` so
re-labelling never re-pays. Swapping providers is a config change, not a code change.

``kind="vertex"`` reaches Vertex AI through its OpenAI-compatible surface, so it reuses
the OpenAI request path entirely and differs only in how it authorises: no API key, an
OAuth token minted from Application Default Credentials. Vertex exposes no
OpenAI-compatible *embeddings* endpoint — keep embeddings on Ollama (or another
OpenAI-compatible provider) when chat runs on Vertex.

Anthropic (chat-only, no embeddings endpoint) would be a separate adapter; it is
intentionally not wired for the local-first slice. Claude models served *by* Vertex are
not reachable here either: they speak the native Anthropic Messages shape on
``:rawPredict``, not the OpenAI one.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    if cfg.get("kind") == "vertex":
        h = {"Authorization": f"Bearer {_adc_token().token()}"}
        if cfg.get("project"):
            # user ADC (`gcloud auth application-default login`) carries no billing
            # project of its own; Vertex bills and quotas against this one.
            h["x-goog-user-project"] = str(cfg["project"])
        return h
    return {"Authorization": f"Bearer {cfg.get('api_key', 'x')}"}


def _vertex_base_url(cfg: dict) -> str:
    """The OpenAI-compatible surface; the caller's client appends /chat/completions."""
    loc = cfg.get("location") or "us-central1"
    project = cfg.get("project") or _adc_token().project
    if not project:
        raise LLMError("vertex: set providers.<role>.project (ADC carries no default project)")
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
    """Holds chat + embed endpoint configs and a DB connection for caching."""

    def __init__(self, chat_cfg: dict, embed_cfg: dict, chat_large_cfg: dict | None = None,
                 conn=None, roles: dict | None = None):
        self.chat_cfg = chat_cfg
        self.chat_large_cfg = chat_large_cfg or chat_cfg
        self.embed_cfg = embed_cfg
        # Named roles beyond chat/chat_large. Naming and narration are different jobs:
        # naming wants the model whose answers the catalogue was built on (its output IS
        # the entry name, so changing model renames everything and churns the catalogue),
        # narration wants whichever model writes the best prose. Unset roles fall back to
        # chat, so a single-provider config behaves exactly as before.
        self.roles = {"chat": chat_cfg, "chat_large": self.chat_large_cfg,
                      **(roles or {})}
        self.conn = conn
        self.cache_only = False
        self.db_lock = threading.Lock()   # guards the shared sqlite conn (concurrent untangle)
        timeout = max(float(chat_cfg.get("timeout", 900)),
                      float(self.chat_large_cfg.get("timeout", 900)))
        self._client = httpx.Client(timeout=timeout)

    # -- embeddings -------------------------------------------------------
    def embed(self, texts: list[str]) -> np.ndarray:
        cfg = self.embed_cfg
        url = cfg["base_url"].rstrip("/") + "/embeddings"
        headers = _auth_header(cfg)
        batch = int(cfg.get("batch", 16))
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            payload = {"model": cfg["model"], "input": chunk}
            data = self._post(url, payload, headers)
            rows = sorted(data["data"], key=lambda d: d.get("index", 0))
            out.extend(r["embedding"] for r in rows)
        return np.asarray(out, dtype=np.float32)

    # -- chat -------------------------------------------------------------
    def chat(self, system: str, user: str, want_json: bool = True,
             cache_extra: str = "", large: bool = False,
             role: str | None = None) -> dict | str:
        cfg = self.roles.get(role or ("chat_large" if large else "chat")) or self.chat_cfg
        provider, model = cfg.get("kind", "openai"), cfg["model"]
        # Generation params are part of the identity of a response — cache on them too, so
        # changing temperature/seed correctly invalidates stale entries.
        key = _cache_key(provider, model, "chat", system, user, cache_extra,
                         str(cfg.get("temperature", "")), str(cfg.get("seed", "")),
                         # part of the key only when set, so existing caches stay valid
                         *[f"{k}={cfg[k]}" for k in ("reasoning_effort", "thinking_budget")
                           if cfg.get(k) is not None])
        cached = self._cache_get(key)
        if cached is not None:
            return _extract_json(cached) if want_json else cached
        if self.cache_only:
            raise NotCached(key)

        if provider == "ollama":
            content, usage = self._chat_ollama(cfg, system, user, want_json)
        else:
            content, usage = self._chat_openai(cfg, system, user, want_json)
        self._cache_put(key, provider, model, content, usage)
        return _extract_json(content) if want_json else content

    def _chat_openai(self, cfg: dict, system: str, user: str, want_json: bool):
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = _auth_header(cfg)
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": float(cfg.get("temperature", 0.2)),
        }
        if cfg.get("seed") is not None:   # reproducible sampling (generic; honoured where supported)
            payload["seed"] = int(cfg["seed"])
        # Thinking models reason before answering and bill it as output: on untangle it
        # was over half of all tokens. Gemini takes a budget (0 = off; Vertex rejects
        # reasoning_effort="none"), other endpoints take reasoning_effort.
        if cfg.get("reasoning_effort"):
            payload["reasoning_effort"] = cfg["reasoning_effort"]
        if cfg.get("thinking_budget") is not None:
            payload["extra_body"] = {"google": {"thinking_config": {
                "thinking_budget": int(cfg["thinking_budget"])}}}
        # json_mode=false is the escape hatch for endpoints that reject response_format;
        # _extract_json already tolerates a model that answers with prose around the object.
        if want_json and cfg.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        data = self._post(url, payload, headers)
        return data["choices"][0]["message"]["content"], data.get("usage")

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
                    raise LLMError(f"POST {url} -> {resp.status_code}: {resp.text[:200]}")
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

    def _cache_put(self, key, provider, model, response, usage) -> None:
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
                (key, provider, model, "chat", response, ti, to, now_iso()),
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
    """Expand api_key values of the form 'env:VAR' or '${VAR}' from the environment."""
    if isinstance(value, str):
        if value.startswith("env:"):
            return os.environ.get(value[4:], "")
        if value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1], "")
    return value


def build_provider(cfg: dict, conn=None) -> Provider:
    """Construct a Provider from the merged config's ``providers`` section."""
    providers = cfg.get("providers", {})
    resolved = {}
    for name in ("chat", "embed", "chat_large", "naming"):
        if name not in providers:
            continue
        pc = dict(providers[name])
        if pc.get("kind") == "anthropic":
            raise LLMError(
                f"providers.{name}.kind='anthropic' is not supported; "
                "use an OpenAI-compatible endpoint (ollama/openai/together/scaleway/vertex)."
            )
        if pc.get("kind") == "vertex":
            if name == "embed":
                raise LLMError(
                    "providers.embed.kind='vertex': Vertex exposes no OpenAI-compatible "
                    "embeddings endpoint. Keep embeddings on ollama."
                )
            pc["project"] = pc.get("project") or _adc_token().project
            # config.toml is merged OVER built-in defaults, so a base_url belonging to a
            # different provider survives when this role is switched to vertex — and then
            # silently wins. Any endpoint that is not Vertex's is wrong here by definition.
            if "aiplatform.googleapis.com" not in (pc.get("base_url") or ""):
                pc["base_url"] = _vertex_base_url(pc)
        pc["api_key"] = _resolve_secret(pc.get("api_key", ""))
        resolved[name] = pc
    extra = {k: v for k, v in resolved.items() if k not in ("chat", "embed", "chat_large")}
    return Provider(resolved["chat"], resolved["embed"], resolved.get("chat_large"),
                    conn=conn, roles=extra)
