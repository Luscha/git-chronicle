"""Static import/include extraction — deterministic structural evidence, no LLM.

Used by the mega-commit overflow path to pick each stem family's REPRESENTATIVE file:
the member most included by its own family is the declarative core (and a bundled
foreign header that nothing in the family's stem vocabulary owns can never win —
the dump_proto/lzo.h failure mode from the benchmark).

Regexes are deliberately coarse: we only need within-family resolution by basename,
not a build-system-accurate dependency graph.
"""

from __future__ import annotations

import re
from collections import Counter

from ..extract.git_ingest import run_git

_PATTERNS = [
    re.compile(r'#\s*include\s*[<"]([^">]+)[">]'),                   # C/C++/ObjC
    re.compile(r'^\s*from\s+([\w.]+)\s+import\b', re.M),              # Python
    re.compile(r'^\s*import\s+([\w.,\s]+)', re.M),                    # Python / Java
    re.compile(r'\brequire\s*\(?\s*[\'"]([^\'"]+)[\'"]'),             # Lua / JS
    re.compile(r'^\s*import\s+.*?from\s+[\'"]([^\'"]+)[\'"]', re.M),  # ES modules
]


def extract_import_refs(text: str) -> set[str]:
    """Basenames (no extension, lowercased) this file references via import/include."""
    refs: set[str] = set()
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            for tok in re.split(r"[,\s]+", m.group(1).strip()):
                if not tok:
                    continue
                base = tok.replace("\\", "/").rsplit("/", 1)[-1]
                base = base.rsplit(".", 1)[0]
                if base:
                    refs.add(base.lower())
    return refs


def family_include_counts(repo: str, commit: str, files: list[str],
                          max_read: int = 40, head_chars: int = 12000) -> Counter:
    """How many times each family member is referenced BY other members, at `commit`.
    Reads each member's head once; resolves references within the family by basename."""
    base_of = {f: f.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() for f in files}
    by_base: dict[str, list[str]] = {}
    for f, b in base_of.items():
        by_base.setdefault(b, []).append(f)
    counts: Counter = Counter()
    for f in files[:max_read]:
        text = run_git(repo, ["show", f"{commit}:{f}"], check=False)[:head_chars]
        if not text:
            continue
        for ref in extract_import_refs(text):
            for target in by_base.get(ref, []):
                if target != f:
                    counts[target] += 1
    return counts
