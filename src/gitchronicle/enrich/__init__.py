"""Cheap, LLM-free per-commit enrichment: language, conventional-commit parse,
component (path taxonomy) assignment."""

from .signals import enrich

__all__ = ["enrich"]
