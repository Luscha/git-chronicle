"""SETTINGS — the endpoints and the roles, read and written without touching the rest.

The models are the entry point: everything else in gitchronicle is deterministic, and the
one thing a new user must get right is which endpoint answers which job. Two tables say it:
`[endpoints.<name>]` is where models live, `[roles.<role>]` is which one does which job.

Credentials ARE settable here, and by default they do not land in the config file: the key
goes into the `.env` beside it (0600, already git-ignored, already loaded) and the config
gets `api_key = "env:NAME"`. Refusing to take a key at all was a rule that protected
nothing — on a localhost tool the key is already on the same disk, in a file the same user
owns — while forcing everyone into a text editor. What matters is not *who types it* but
*where it is written*, and a config file people share or commit is the wrong place.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .llm.endpoints import FALLBACK, KINDS, ROLE_HELP, ROLES

_ENDPOINT_FIELDS = ("kind", "protocol", "base_url", "auth", "dialect", "project", "location",
                    "api_version", "api_key")
_ROLE_FIELDS = ("endpoint", "model", "think")


def _section(lines: list[str], header: str) -> tuple[int, int] | None:
    head = re.compile(rf"^\s*\[{re.escape(header)}\]\s*$")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and not re.match(r"^\s*\[", lines[end]):
        end += 1
    return start, end


def _endpoint_row(name: str, spec: dict) -> dict:
    from .llm.endpoints import key_ref, resolve_endpoint
    e = resolve_endpoint(name, spec)
    return {"name": name, "kind": e.get("kind"), "protocol": e["protocol"],
            "base_url": e.get("base_url", ""), "auth": e["auth"], "dialect": e.get("dialect"),
            "project": e.get("project", ""), "location": e.get("location", ""),
            "key": key_ref(e)}          # the reference, never the secret


def read(path: str | Path) -> dict:
    """Both tables as the file has them, plus the endpoints we know the address of."""
    import tomllib

    p = Path(path)
    raw = tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    ends = raw.get("endpoints", {}) or {}
    roles = raw.get("roles", {}) or {}
    return {
        "path": str(p),
        # resolved, not raw: a kind carries the address, the auth and the dialect, and a
        # panel that showed only what the file spells would report none of them
        "endpoints": [_endpoint_row(n, e) for n, e in ends.items()],
        "roles": [
            {"role": r, "set": r in roles, "what": ROLE_HELP.get(r, ""),
             "endpoint": (roles.get(r) or {}).get("endpoint")
                         or (next(iter(ends)) if len(ends) == 1 else None),
             "model": (roles.get(r) or {}).get("model"),
             "think": str((roles.get(r) or {}).get("think", "")),
             "falls_back_to": FALLBACK.get(r, ("",))[0]}
            for r in ROLES],
        # base_url AND auth: a panel that only knew the address could not tell that vertex
        # wants a project instead of a key
        "kinds": {k: {"base_url": v.get("base_url", ""),
                      "auth": v.get("auth", "bearer"),
                      "protocol": v.get("protocol", "openai")} for k, v in KINDS.items()},
    }


def _env_name(endpoint: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", endpoint.upper()).strip("_") + "_API_KEY"


def write_secret(config_path: str | Path, endpoint: str, key: str, mode: str = "env") -> str:
    """Store a credential and return the reference the config should carry.

    `env` (default) writes it to the .env beside the config and returns `env:NAME`;
    `inline` returns the key itself, for someone who would rather keep one file; `ref`
    stores nothing and takes what was typed as a reference already (`env:…`, `file:…`).
    """
    if mode == "ref" or key.startswith(("env:", "file:", "${")):
        return key
    if mode == "inline":
        return key
    name = _env_name(endpoint)
    env_file = Path(config_path).resolve().parent / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    at = next((i for i, ln in enumerate(lines) if ln.split("=", 1)[0].strip() == name), None)
    if at is None:
        lines.append(f"{name}={key}")
    else:
        lines[at] = f"{name}={key}"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
    os.environ[name] = key            # the running process should not need a restart
    return f"env:{name}"


def write(path: str | Path, endpoints: dict | None = None, roles: dict | None = None) -> list[str]:
    """Apply {name: {...}} / {role: {...}} to the file, in place, line by line.

    Every comment, every credential and every other section survives, because the studio
    edits a file somebody else wrote and has no business reformatting it.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    said = []
    for table, fields, changes in (("endpoints", _ENDPOINT_FIELDS, endpoints or {}),
                                   ("roles", _ROLE_FIELDS, roles or {})):
        for name, spec in changes.items():
            if table == "roles" and name not in ROLES:
                raise ValueError(f"unknown role {name!r}; roles are {', '.join(ROLES)}")
            header = f"{table}.{name}"
            span = _section(lines, header)
            # "inherit" is the absence of a block, not a block with fields cleared: a role
            # with a model and no endpoint is not inheriting, it is broken
            if spec.get("_remove"):
                if span is not None:
                    start, end = span
                    while end < len(lines) and not lines[end].strip():
                        end += 1
                    del lines[start:end]
                    said.append(f"removed [{header}] — it inherits again")
                continue
            if span is None:
                while lines and not lines[-1].strip():
                    lines.pop()
                lines += ["", f"[{header}]"]
                span = _section(lines, header)
                said.append(f"added [{header}]")
            start, end = span
            block = lines[start:end]
            for key in fields:
                if key not in spec:
                    continue
                value = spec[key]
                at = next((i for i, ln in enumerate(block)
                           if re.match(rf"^\s*{key}\s*=", ln)), None)
                if value in (None, ""):
                    if at is not None:
                        block.pop(at)
                        said.append(f"{header}.{key} cleared")
                    continue
                rendered = f'{key} = "{value}"' if isinstance(value, str) else f"{key} = {value}"
                if at is None:
                    tail = max((i for i, ln in enumerate(block) if ln.strip()), default=0)
                    block.insert(tail + 1, rendered)
                else:
                    block[at] = re.sub(r"^\s*", "", rendered)
                said.append(f"{header}.{key} = {value}")
            lines[start:end] = block
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return said
