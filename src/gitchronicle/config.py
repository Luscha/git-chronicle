"""Configuration loading: TOML file merged over built-in defaults."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "repo": {"path": ".", "rev_range": "HEAD~300..HEAD"},
    "db": {"path": "gitchronicle.db"},
    "output": {"html": "graph.html", "json": "domains.json"},
    # No vendor default: a knowledge base built against a provider the owner did not
    # choose is worse than a clear error. `gitchronicle doctor` checks what is configured.
    "providers": {},
    "cluster": {
        "knn": 10,
        "edge_threshold": 0.12,
        "temporal_window_days": 14,
        "resolution_sweep": [0.4, 0.6, 0.8, 1.0, 1.3, 1.6],
        "target_min_clusters": 12,
        "target_max_clusters": 55,
        "weights": {
            "embedding": 0.35,
            "cochange": 0.20,
            "component": 0.15,
            "branch": 0.15,
            "temporal": 0.10,
            "author": 0.05,
        },
    },
    "label": {"max_commits_ctx": 30, "max_body_chars": 400},
    # Feature reconstruction. Default method "taxonomy" = induce a fixed feature taxonomy
    # once (clustered facets, contrastively named, with definitions), then classify every
    # concern against it BY ID (embedding fast path -> LLM shortlist pick -> propose-new for
    # the rest). "leiden" = pure embedding clustering (no chat LLM; legacy).
    "catalog": {
        "method": "taxonomy",
        "knn": 10,
        # CPM Leiden resolution (gamma) sweep; the coarsest STABLE gamma is picked data-driven.
        # CPM avoids modularity's resolution limit, so large dense regions split into real pieces.
        "cpm_gamma_sweep": [0.008, 0.012, 0.016, 0.02, 0.025, 0.03, 0.04, 0.05, 0.07, 0.09],
        "min_domain_concerns": 2,
        "seed": 20240607,
        # Rare-file affinity: concerns sharing a feature-specific file cluster together even
        # when labels are generic; god-files (df > 95th pct) are excluded so they can't blob.
        "file_affinity_weight": 0.6,
        # taxonomy method:
        "induce_min_cluster": 3,   # smaller proto-clusters are left to classification
        "shortlist_k": 8,          # candidate features shown to the LLM per concern
        "classify_batch": 12,      # concerns per classification call
        "propose_batch": 30,       # none-fits per propose-new call
        # Embedding fast path: assign without the LLM only on a CLEAR winner. Validated on
        # void-queue: wrong fast picks clustered at top1 0.62-0.63, correct ones >= 0.66.
        "fast_margin": 0.06,       # top1-top2 margin ...
        "fast_floor": 0.66,        # ... and absolute top1 floor
        "audit_z": -2.0,           # per-feature outlier threshold (z-score of centroid cosine)
        "audit_move_margin": 0.03, # audit move must beat the old home cosine by this much
        "audit_unassign_floor": 0.55,  # auditor answered 0 + home cosine below this -> freed
        "autoconfirm_runs": 2,     # provisional -> named after surviving this many runs ...
        "autoconfirm_concerns": 5, # ... with at least this many concerns
        # v3 ground layer:
        "census_top": 800,         # stems kept in the census (LLM input stays bounded)
        "glossary_chunk": 150,     # census stems per glossary-drafting call
        "glossary_max_docs": 120,  # in-repo docs harvested
        "stem_boost": 0.12,        # shortlist bonus when concern & feature share a stem
        "novelty_min": 4,          # min cluster size for batch-novelty proposals
    },
    "discover": {"max_diffs": 3, "max_diff_lines": 120, "max_files_ctx": 25, "max_hints": 12,
                 # near-duplicate domain merge: consider name-token OR embedding-similar pairs
                 "max_merge_pairs": 80, "merge_sim_min": 0.80},
    # Adaptive routing: message-first (cheap) for small single-purpose commits; escalate to the
    # DIFF only when a commit is tangled (many files), terse, or multi-topic. Diffs are capped by
    # lines/line-length/total chars so data-dump commits can't balloon the prompt. Cost scales
    # with a repo's message quality. Concurrent LLM calls feasible on large histories.
    "untangle": {
        "workers": 8,
        "max_diff_lines": 180, "max_line_chars": 300, "max_diff_chars": 6000,
        "max_msg_files": 4, "min_subject_len": 20,
    },
    "lifecycle": {"dormancy_days": 120, "removed_threshold": 0.25, "merged_threshold": 0.5},
    "index": {},
    "ask": {"k_domains": 8, "k_commits": 10},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines into os.environ."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_config(path: str | Path = "config.toml") -> dict[str, Any]:
    """Load config.toml (if present) merged over DEFAULTS; also load .env."""
    _load_dotenv()
    p = Path(path)
    if p.exists():
        with p.open("rb") as fh:
            user = tomllib.load(fh)
        return _deep_merge(DEFAULTS, user)
    return _deep_merge(DEFAULTS, {})


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    """Nested lookup: get(cfg, 'providers.chat.model')."""
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
