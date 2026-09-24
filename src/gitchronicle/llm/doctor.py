"""DOCTOR — check the setup before a long run pays for finding out.

A wrong model name, a missing permission or an endpoint that rejects JSON mode all fail
the same way today: hours in, mid-untangle. Each check here is one cheap call, and each
reports what to do about it. Discovered the hard way: Vertex rejects
``reasoning_effort="none"``, and an endpoint that ignores ``response_format`` produces
prose the parser then has to rescue.
"""

from __future__ import annotations

import time

from .provider import ROLES, LLMError, build_provider

_PROBE_SYS = "You answer in JSON only."
_PROBE_USER = 'Reply exactly {"ok": true} and nothing else.'


def check_scope(log=print) -> int:
    """Rules in gitchronicle.md that do nothing, which no run would ever tell you."""
    from ..scope import Scope
    sc = Scope.load()
    if not sc.exists():
        log("  · scope: none reviewed yet (run `gitchronicle init`)")
        return 0
    shadow = sc.shadowed()
    log(f"  ✓ scope: {len(sc.includes)} include, {len(sc.excludes)} exclude, "
        f"{len(sc.acknowledges)} acknowledge")
    for e, i in shadow[:8]:
        log(f"    ! 'exclude: {e}' does nothing — 'include: {i}' covers it and an "
            f"explicit include wins")
    if len(shadow) > 8:
        log(f"    ! ... and {len(shadow) - 8} more")
    return len(shadow)


def check_providers(cfg: dict, log=print) -> dict:
    providers = cfg.get("providers", {})
    try:
        pr = build_provider(cfg, None)          # no conn: probes are never cached
    except LLMError as exc:
        log(f"  ✗ config: {exc}")
        return {"ok": False, "roles": {}}

    seen: dict[tuple, str] = {}
    out: dict[str, dict] = {}
    ok_all = True
    for role in ROLES:
        if role not in providers:
            continue
        cfgr = pr.roles.get(role) or (pr.embed_cfg if role == "embed" else None)
        if not cfgr:
            continue
        ident = (cfgr.get("kind"), cfgr.get("base_url"), cfgr.get("model"))
        if ident in seen:
            log(f"  · {role}: same endpoint as {seen[ident]}")
            continue
        seen[ident] = role
        if role == "embed":
            out[role] = _probe_embed(pr, log, role)
        else:
            out[role] = _probe_chat(pr, cfgr, log, role)
        # embeddings are optional: nothing in the pipeline embeds, so a missing backend
        # is a note, not a failure
        ok_all = ok_all and (out[role]["ok"] or role == "embed")
    for role in ("chat_large", "naming", "untangle", "narration", "judge"):
        if role not in providers:
            log(f"  · {role}: not set, falls back to "
                f"{'chat_large' if role == 'judge' and 'chat_large' in providers else 'chat'}")
    return {"ok": ok_all, "roles": out}


def _probe_chat(pr, cfgr: dict, log, role: str) -> dict:
    name = f"{cfgr.get('kind', 'openai')}/{cfgr.get('model')}"
    t0 = time.monotonic()
    try:
        r = pr.chat(_PROBE_SYS, _PROBE_USER, want_json=True, role=role)
    except Exception as exc:  # noqa: BLE001 - the point is to report it, not raise
        log(f"  ✗ {role}: {name} — {type(exc).__name__}: {str(exc)[:160]}")
        return {"ok": False, "error": str(exc)[:200]}
    dt = time.monotonic() - t0
    json_ok = isinstance(r, dict) and r.get("ok") is True
    log(f"  {'✓' if json_ok else '~'} {role}: {name} answered in {dt:.1f}s"
        + ("" if json_ok else "  (did not return the JSON asked for — set json_mode = false "
                             "if the endpoint rejects response_format)"))
    return {"ok": True, "json": json_ok, "seconds": round(dt, 2), "model": name}


def _probe_embed(pr, log, role: str) -> dict:
    try:
        v = pr.embed(["gitchronicle probe"])
    except Exception as exc:  # noqa: BLE001
        log(f"  · embed (optional, legacy commands only) unreachable: "
            f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"ok": False}
    log(f"  ✓ embed: {pr.embed_cfg.get('model')} → {v.shape[1]} dimensions")
    return {"ok": True, "dim": int(v.shape[1])}
