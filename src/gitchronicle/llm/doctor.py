"""DOCTOR — is this setup going to work, and what will it cost you in time?

Everything here is a check somebody had to run by hand once: git, the repository, the
scope map, and then the part that decides the bill — which endpoint answers which job,
how much of it is reasoning, and where its credential comes from (the reference, never
the value).
"""

from __future__ import annotations

import time

from .endpoints import ROLE_HELP, ROLES
from .provider import LLMError, build_provider

_PROBE_SYS = "You answer in JSON only."
_PROBE_USER = 'Reply with exactly: {"ok": true}'


def _think_of(cfg: dict) -> str:
    """What this role does about reasoning, and what that costs in wall-clock."""
    if cfg.get("think_budget") is not None:
        b = int(cfg["think_budget"])
        return "thinking off" if b == 0 else f"thinking ≤{b}"
    if cfg.get("think_effort"):
        return f"thinking {cfg['think_effort']}"
    return "thinking unlimited"


from .endpoints import key_ref as _key_of


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
    for line in cfg.get("_overrides") or []:
        log(f"  override: {line}")
    try:
        pr = build_provider(cfg, None)          # no conn: probes are never cached
    except LLMError as exc:
        log(f"  ✗ models: {exc}")
        return {"ok": False, "roles": {}}

    seen: dict[tuple, str] = {}
    out: dict[str, dict] = {}
    ok_all = True
    for role in ROLES:
        c = pr.roles.get(role)
        if not c:
            continue
        where = f"{c.get('name', '?')} · {c.get('protocol')}/{c['model']}"
        if c.get("inherited_from"):
            log(f"  · {role}: follows {c['inherited_from']}")
            continue
        ident = (c.get("base_url"), c.get("model"), c.get("think_budget"), c.get("think_effort"))
        if ident in seen:
            log(f"  · {role}: same model as {seen[ident]}")
            continue
        seen[ident] = role
        out[role] = _probe(pr, c, log, role, where)
        ok_all = ok_all and out[role]["ok"]
    return {"ok": ok_all, "roles": out}


def _probe(pr, c: dict, log, role: str, where: str) -> dict:
    t0 = time.monotonic()
    try:
        r = pr.chat(_PROBE_SYS, _PROBE_USER, want_json=True, role=role)
    except Exception as exc:  # noqa: BLE001 - the point is to report it, not raise
        log(f"  ✗ {role}: {where} — {type(exc).__name__}: {str(exc)[:160]}")
        return {"ok": False, "error": str(exc)[:200]}
    dt = time.monotonic() - t0
    json_ok = isinstance(r, dict) and r.get("ok") is True
    log(f"  {'✓' if json_ok else '~'} {role}: {where} answered in {dt:.1f}s"
        + ("" if json_ok else "  (did not return the JSON asked for — set json_mode = false "
                              "if the endpoint rejects response_format)"))
    think = _think_of(c)
    # the probe prompt is tiny, so its latency hides what thinking does on a real call:
    # answering went 13.4s -> 2.9s here, and it was one line of config
    log(f"      {ROLE_HELP.get(role, '')} · {think} · key: {_key_of(c)}"
        + (f"  — billed as output; `--think {role}=off` or a budget" if "unlimited" in think
           else ""))
    return {"ok": True, "json": json_ok, "seconds": round(dt, 2), "model": c["model"],
            "think": think, "key": _key_of(c)}
