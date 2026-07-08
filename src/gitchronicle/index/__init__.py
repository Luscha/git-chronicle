"""Build the knowledge-base indexes: semantic (embeddings) + full-text (FTS5)."""

from .index import build_index, fts_available

__all__ = ["build_index", "fts_available"]
