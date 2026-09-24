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
    ("c",   re.compile(r'#\s*include\s*[<"]([^">]+)[">]')),                  # C/C++/ObjC
    ("py",  re.compile(r'^\s*from\s+([\w.]+)\s+import\b', re.M)),            # Python
    ("py",  re.compile(r'^\s*import\s+([\w.,\s]+)', re.M)),                   # Python / Java
    ("req", re.compile(r'\brequire\s*\(?\s*[\'"]([^\'"]+)[\'"]')),            # Lua / JS
    ("es",  re.compile(r'^\s*import\s+.*?from\s+[\'"]([^\'"]+)[\'"]', re.M)),  # ES modules
    ("rs",  re.compile(r'^\s*(?:pub\s+)?use\s+([\w:]+)', re.M)),                # Rust
    ("rs",  re.compile(r'^\s*(?:pub\s+)?mod\s+(\w+)\s*;', re.M)),               # Rust
]

# Rust names a module by path, not by file: `use alacritty_terminal::term::Term` points
# at term.rs, and the trailing segment is the TYPE. Module segments are snake_case and
# types are CamelCase, which is the language's own convention and enough to tell them
# apart without parsing.
_RUST_SEG = re.compile(r"[a-z_][a-z0-9_]*$")


def extract_import_refs(text: str, with_kind: bool = False):
    """Basenames (no extension, lowercased) this file references via import/include.

    With `with_kind`, each reference comes back as (basename, syntax) — which of the five
    forms found it. The syntax is what makes a reference resolvable: `#include "config.h"`
    cannot possibly mean a TypeScript file, and matching by basename alone said it did.
    """
    refs: set = set()
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            for tok in re.split(r"[,\s]+", m.group(1).strip()):
                if not tok:
                    continue
                if kind == "rs":
                    segs = [x for x in tok.split("::") if _RUST_SEG.match(x)]
                    # `use crate::grid::Grid` -> grid; `use self::event` -> event
                    segs = [x for x in segs if x not in ("crate", "self", "super", "std")]
                    base = segs[-1] if segs else ""
                else:
                    base = tok.replace("\\", "/").rsplit("/", 1)[-1]
                    base = base.rsplit(".", 1)[0]
                if base:
                    refs.add((base.lower(), kind) if with_kind else base.lower())
    return refs


def family_include_counts(repo: str, commit: str, files: list[str],
                          max_read: int = 40, head_chars: int = 12000,
                          reader=None) -> Counter:
    """How many times each family member is referenced BY other members, at `commit`.
    Reads each member's head once; resolves references within the family by basename.
    Pass a git_ingest.BatchReader to avoid one subprocess per file."""
    base_of = {f: f.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() for f in files}
    by_base: dict[str, list[str]] = {}
    for f, b in base_of.items():
        by_base.setdefault(b, []).append(f)
    counts: Counter = Counter()
    for f in files[:max_read]:
        if reader is not None:
            text = reader.read(commit, f, limit=head_chars)
        else:
            text = run_git(repo, ["show", f"{commit}:{f}"], check=False)[:head_chars]
        if not text:
            continue
        for ref in extract_import_refs(text):
            for target in by_base.get(ref, []):
                if target != f:
                    counts[target] += 1
    return counts
