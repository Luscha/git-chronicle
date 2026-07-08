"""A thin, provider-agnostic LLM layer.

Two capabilities — ``chat`` and ``embed`` — over any OpenAI-compatible endpoint
(Ollama, OpenAI, Scaleway, ...). Chat responses are cached in ``llm_cache`` so
re-labelling never re-pays. Swapping providers is a config change, not a code change.

Anthropic (chat-only, no embeddings endpoint) would be a separate adapter; it is
intentionally not wired for the local-first slice.
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

    def __init__(self, chat_cfg: dict, embed_cfg: dict, chat_large_cfg: dict | None = None, conn=None):
        self.chat_cfg = chat_cfg
        self.chat_large_cfg = chat_large_cfg or chat_cfg
        self.embed_cfg = embed_cfg
        self.conn = conn
        self.db_lock = threading.Lock()   # guards the shared sqlite conn (concurrent untangle)
        timeout = max(float(chat_cfg.get("timeout", 900)),
                      float(self.chat_large_cfg.get("timeout", 900)))
        self._client = httpx.Client(timeout=timeout)

    # -- embeddings -------------------------------------------------------
    def embed(self, texts: list[str]) -> np.ndarray:
        cfg = self.embed_cfg
        url = cfg["base_url"].rstrip("/") + "/embeddings"
        headers = {"Authorization": f"Bearer {cfg.get('api_key', 'x')}"}
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
             cache_extra: str = "", large: bool = False) -> dict | str:
        cfg = self.chat_large_cfg if large else self.chat_cfg
        provider, model = cfg.get("kind", "openai"), cfg["model"]
        # Generation params are part of the identity of a response — cache on them too, so
        # changing temperature/seed correctly invalidates stale entries.
        key = _cache_key(provider, model, "chat", system, user, cache_extra,
                         str(cfg.get("temperature", "")), str(cfg.get("seed", "")))
        cached = self._cache_get(key)
        if cached is not None:
            return _extract_json(cached) if want_json else cached

        if provider == "ollama":
            content, usage = self._chat_ollama(cfg, system, user, want_json)
        else:
            content, usage = self._chat_openai(cfg, system, user, want_json)
        self._cache_put(key, provider, model, content, usage)
        return _extract_json(content) if want_json else content

    def _chat_openai(self, cfg: dict, system: str, user: str, want_json: bool):
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.get('api_key', 'x')}"}
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": float(cfg.get("temperature", 0.2)),
        }
        if cfg.get("seed") is not None:   # reproducible sampling (generic; honoured where supported)
            payload["seed"] = int(cfg["seed"])
        if want_json:
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
    for name in ("chat", "embed", "chat_large"):
        if name not in providers:
            continue
        pc = dict(providers[name])
        if pc.get("kind") == "anthropic":
            raise LLMError(
                f"providers.{name}.kind='anthropic' is not supported; "
                "use an OpenAI-compatible endpoint (ollama/openai/together/scaleway)."
            )
        pc["api_key"] = _resolve_secret(pc.get("api_key", ""))
        resolved[name] = pc
    return Provider(resolved["chat"], resolved["embed"], resolved.get("chat_large"), conn=conn)
