"""Git history extraction. All access to git is isolated here so the backend
(currently the ``git`` CLI) can later be swapped for pure-Python ``dulwich``."""

from .git_ingest import ingest, rev_list

__all__ = ["ingest", "rev_list"]
