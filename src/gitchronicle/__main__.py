"""Enable `python -m gitchronicle` without installing the console script."""

from .cli import app

if __name__ == "__main__":
    app()
