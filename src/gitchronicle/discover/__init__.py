"""LLM domain synthesis: name each candidate domain and infer what/why/evolution,
grounded in code diffs (messages used only as untrusted hints)."""

from .discover import discover

__all__ = ["discover"]
