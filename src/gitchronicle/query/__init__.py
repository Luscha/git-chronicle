"""Knowledge-base retrieval: semantic (embeddings) + full-text (FTS5)."""

from .retrieve import keyword_commits, keyword_domains, resolve_domain, semantic

__all__ = ["semantic", "keyword_domains", "keyword_commits", "resolve_domain"]
