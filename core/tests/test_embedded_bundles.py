import json

from conftest import write

from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.init import _manifest, init_bundle
from okfy.segment import survey
from okfy.update import corpus_diff


def _git_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    write(corpus / "doc.md", "# doc\n")
    write(corpus / "plain/notes.md", "# a folder that only looks like one\n")
    # both marker names, neither one a bundle's: still an ordinary folder
    write(corpus / "plain/meta/purpose.md", "# why this folder exists\n")
    write(corpus / "plain/meta/corpus.md", "---\ntype: Note\ntitle: corpus\n---\n")
    run_git(corpus, "init", "-q", check=True)
    run_git(corpus, "add", "-A", check=True)
    run_git(corpus, "commit", "-q", "-m", "c", check=True)
    return corpus


def test_corpus_listings_skip_an_embedded_bundle_at_any_path(tmp_path):
    corpus = _git_corpus(tmp_path)
    kb = Bundle(init_bundle(corpus / "kb", corpus, embed=True))
    run_git(corpus, "add", "-A", check=True)
    run_git(corpus, "commit", "-q", "-m", "kb", check=True)

    m = _manifest(corpus)
    assert "doc.md" in m and "plain/notes.md" in m and "plain/meta/corpus.md" in m
    assert not [p for p in m if p.startswith("kb/")]
    listed = survey(corpus)["files"]
    names = [f["path"] if isinstance(f, dict) else f for f in listed]
    assert "doc.md" in names and "plain/notes.md" in names
    assert not [p for p in names if p.startswith("kb/")]

    # a second bundle embedded later is not new corpus in a git-mode diff
    init_bundle(corpus / "docs/other-kb", corpus, embed=True)
    write(corpus / "new.md", "# new\n")
    run_git(corpus, "add", "-A", check=True)
    run_git(corpus, "commit", "-q", "-m", "more", check=True)
    assert corpus_diff(kb)["added"] == ["new.md"]


def test_init_does_not_list_its_own_embedded_bundle(tmp_path):
    kb = init_bundle(_git_corpus(tmp_path) / "kb", tmp_path / "corpus", embed=True)
    saved = json.loads((kb / "meta/corpus-manifest.json").read_text(encoding="utf-8"))
    assert "doc.md" in saved and not [p for p in saved if p.startswith("kb/")]
