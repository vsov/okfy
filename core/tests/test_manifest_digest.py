import json
import subprocess

from conftest import run_cli, write

from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.init import init_bundle, manifest_digest
from okfy.update import refresh_snapshot
from okfy.validate import validate_integrity


def _digest_on_disk(bundle):
    return manifest_digest(json.loads(
        (bundle.root / "meta/corpus-manifest.json").read_text(encoding="utf-8")))


def test_snapshot_digest_covers_dirty_bytes_a_git_sha_does_not(tmp_path):
    corpus = tmp_path / "corpus"
    write(corpus / "a.md", "# a\n")
    run_git(corpus, "init", "-q", check=True)
    run_git(corpus, "add", "-A", check=True)
    run_git(corpus, "commit", "-q", "-m", "c", check=True)
    b = Bundle(init_bundle(tmp_path / "b", corpus, write_policy="direct"))
    first = b.get("meta/corpus").meta
    assert first["manifest_digest"] == _digest_on_disk(b)

    write(corpus / "a.md", "# a, edited but not committed\n")
    assert refresh_snapshot(b, force=True)["refreshed"]
    second = b.get("meta/corpus").meta
    assert second["git_sha"] == first["git_sha"]
    assert second["manifest_digest"] != first["manifest_digest"]
    assert second["manifest_digest"] == _digest_on_disk(b)
    assert "E_MANIFEST_DIGEST" not in [f.code for f in validate_integrity(b).findings]

    (b.root / "meta/corpus-manifest.json").write_text('{"a.md": "0"}', encoding="utf-8")
    assert "E_MANIFEST_DIGEST" in [f.code for f in validate_integrity(b).findings]


def test_policy_hook_lets_a_snapshot_refresh_through(tmp_path):
    corpus = tmp_path / "corpus"
    write(corpus / "a.md", "# a\n")
    b = Bundle(init_bundle(tmp_path / "b", corpus))          # write_policy: proposals
    assert run_cli("package", b.root) == 0
    run_git(b.root, "add", ".", check=True)
    run_git(b.root, "commit", "-q", "--no-verify", "-m", "package", check=True)
    write(corpus / "a.md", "# a, changed\n")
    assert refresh_snapshot(b)["refreshed"]
    run_git(b.root, "add", "meta/corpus.md", check=True)
    # okfy kept off PATH: this checks the hook's own write-policy gate only
    hook = subprocess.run([str(b.root / ".git/hooks/pre-commit")], cwd=b.root,
                          env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert hook.returncode == 0, hook.stderr
