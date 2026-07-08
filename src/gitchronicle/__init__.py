"""gitchronicle: reconstruct a project's feature journey from its git history."""

from .cli import app

__version__ = "0.1.0"


def main() -> None:
    app()
