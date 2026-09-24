"""A real (tiny) git repository and a provider that answers without a network.

The pipeline's inputs are git and a chat model, so the fixtures replace exactly those two
and leave everything else — SQLite, the assembly, the ledger — running for real.
"""

from __future__ import annotations

import json
import subprocess

import pytest


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """Three features, one rename, one deletion — enough to exercise every stage."""
    r = tmp_path / "toy"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "dev@example.com")
    git(r, "config", "user.name", "Dev")

    def commit(msg, files, day):
        for path, text in files.items():
            f = r / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        git(r, "add", "-A")
        git(r, "-c", f"user.name=Dev", "commit", "-q", "-m", msg,
            "--date", f"2024-01-{day:02d}T12:00:00")

    commit("feat(reactor): core loop", {"src/reactor/core.py": "def spin():\n    return 1\n"}, 1)
    commit("feat(reactor): cooling", {"src/reactor/cooling.py": "def cool():\n    pass\n"}, 2)
    commit("feat(dashboard): first panel",
           {"src/dashboard/panel.py": "from cooling import cool\n"}, 3)
    git(r, "mv", "src/dashboard/panel.py", "src/dashboard/main_panel.py")
    commit("refactor(dashboard): rename the panel", {}, 4)
    commit("feat(dashboard): charts", {"src/dashboard/charts.py": "chart = 1\n"}, 5)
    commit("chore(legacy): drop the old shim", {"src/legacy/shim.py": "# temporary\n"}, 6)
    (r / "src/legacy/shim.py").unlink()
    commit("chore(legacy): remove the shim", {}, 7)
    return r


class FakeProvider:
    """Answers from the prompt itself: one concern per commit, named after its files."""

    def __init__(self, conn=None):
        self.conn = conn
        self.calls = []
        self.cache_only = False
        self.chat_cfg = {"model": "fake", "kind": "openai"}
        self.chat_large_cfg = self.chat_cfg
        self.embed_cfg = None
        self.roles = {"chat": self.chat_cfg}

    def chat(self, system, user, want_json=True, cache_extra="", large=False,
             role=None, stage=""):
        self.calls.append((role or stage or "chat", cache_extra))
        if "definition" in system or "name" in system.lower():
            return {"name": "Reactor Core", "definition": "The toy reactor."}
        if "narrative" in system or "narrative" in user:
            return {"title": "A period", "narrative": "Work happened."}
        if "summary" in user and "Evolution" in user:
            return {"summary": "A toy feature."}
        first = ""
        for line in user.splitlines():
            if line.strip().startswith(("- ", "+++ ", "file:")) or "/" in line:
                first = line.strip().lstrip("- ")
                break
        return {"concerns": [{"label": (first or "change")[:60], "summary": "did a thing",
                              "files": []}]}

    def embed(self, texts):  # pragma: no cover - nothing in the pipeline embeds
        raise RuntimeError("no embeddings in tests")

    def close(self):
        pass


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def config(repo, tmp_path):
    return {
        "repo": {"path": str(repo), "rev_range": "main"},
        "db": {"path": str(tmp_path / "work.db")},
        "output": {"kb": str(tmp_path / "kb.db"), "kb_dir": str(tmp_path / "kb")},
        "providers": {"chat": {"kind": "openai", "base_url": "http://localhost:1",
                               "model": "fake", "api_key": "x"}},
        "untangle": {"workers": 2},
    }


@pytest.fixture
def json_dumps():
    return json.dumps
