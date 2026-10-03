import pytest

from okfy.bundle import Bundle
from okfy.cli import main
from okfy.init import init_bundle


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "okfy test", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                 "GIT_COMMITTER_NAME": "okfy test", "GIT_COMMITTER_EMAIL": "t@example.invalid"}.items():
        monkeypatch.setenv(k, v)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def concept(title, body="Body.\n", extra=""):
    return f"---\ntype: Note\ntitle: {title}\n{extra}---\n{body}"


@pytest.fixture
def bundle(tmp_path):
    corpus = tmp_path / "corpus"
    write(corpus / "a.md", "# a\n")
    return Bundle(init_bundle(tmp_path / "b", corpus, write_policy="direct"))


def run_cli(*argv) -> int:
    return main([str(a) for a in argv])
