import shutil
import subprocess
import uuid

import pytest
from conftest import concept, run_cli, write

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.ids import migrate_ids
from okfy.init import init_bundle
from okfy.proposals import accept, propose, refine
from okfy.validate import validate_conformance, validate_integrity


def codes(bundle):
    r = validate_integrity(bundle)
    return [f.code for f in r.findings]


def okf_id(bundle, cid):
    return bundle.get(cid).meta.get("okf_id")


def test_init_mints_bundle_uid(bundle):
    uid = bundle.purpose()["bundle_uid"]
    assert str(uuid.UUID(uid)) == uid
    assert "W_BUNDLE_UID_MISSING" not in codes(bundle)


def test_migrate_backfills_and_rerun_is_a_noop(bundle):
    # a bundle made before ids: no bundle_uid, a page without okf_id, and an
    # owner comment that the backfill must not lose
    p = bundle.root / "meta/purpose.md"
    text = p.read_text(encoding="utf-8")
    p.write_text("\n".join(line for line in text.split("\n")
                           if not line.startswith("bundle_uid:"))
                 .replace("language: en", "language: en  # owner note"),
                 encoding="utf-8")
    write(bundle.root / "a.md", concept("A"))
    assert {"W_BUNDLE_UID_MISSING", "W_OKF_ID_MISSING"} <= set(codes(bundle))

    out = migrate_ids(bundle)
    assert out["bundle_uid"] and out["okf_id"] == ["a"]
    assert "# owner note" in p.read_text(encoding="utf-8")
    assert not {"W_BUNDLE_UID_MISSING", "W_OKF_ID_MISSING"} & set(codes(bundle))

    before = {f: f.read_bytes() for f in bundle.root.rglob("*.md")}
    assert migrate_ids(bundle) == {"bundle_uid": None, "okf_id": []}
    assert before == {f: f.read_bytes() for f in bundle.root.rglob("*.md")}


def test_reserved_and_meta_pages_carry_no_okf_id(bundle):
    write(bundle.root / "a.md", concept("A"))
    assert run_cli("package", bundle.root) == 0
    assert run_cli("migrate", "ids", bundle.root) == 0
    assert okf_id(bundle, "a")
    assert "okf_id" not in (bundle.root / "index.md").read_text(encoding="utf-8")
    assert "okf_id" not in (bundle.root / "log.md").read_text(encoding="utf-8")
    assert "okf_id" not in bundle.purpose()
    assert not [f for f in validate_conformance(bundle).findings
                if f.code == "E_INDEX_FRONTMATTER"]


def test_migrate_commits_past_the_policy_hook(bundle):
    write(bundle.root / "a.md", concept("A"))
    assert run_cli("migrate", "ids", bundle.root) == 0
    from okfy.gitenv import run_git
    st = run_git(bundle.root, "status", "--porcelain", "--", "a.md",
                 capture_output=True, text=True).stdout
    assert st == ""


def test_duplicate_and_blank_okf_id_are_errors(bundle):
    write(bundle.root / "a.md", concept("A", extra="okf_id: same\n"))
    write(bundle.root / "b.md", concept("B", extra="okf_id: same\n"))
    write(bundle.root / "c.md", concept("C", extra="okf_id: ''\n"))
    found = codes(bundle)
    assert found.count("E_OKF_ID_DUPLICATE") == 1
    assert "E_OKF_ID" in found
    # migrate does not paper over a blank id
    migrate_ids(bundle)
    assert okf_id(bundle, "c") == ""


def test_a_moved_page_keeps_its_okf_id(bundle):
    write(bundle.root / "a.md", concept("A"))
    migrate_ids(bundle)
    oid = okf_id(bundle, "a")
    (bundle.root / "notes").mkdir()
    (bundle.root / "a.md").rename(bundle.root / "notes/renamed.md")
    assert migrate_ids(bundle)["okf_id"] == []
    assert okf_id(bundle, "notes/renamed") == oid
    assert "E_OKF_ID_DUPLICATE" not in codes(bundle)


def _propose(bundle, text, target, action, **kw):
    meta, body = frontmatter.parse(text)
    p = propose(bundle, meta, body, target=target, action=action,
                actor="claude-code/1.0", **kw)
    return bundle.concept_id(p)


def test_accept_update_keeps_the_page_okf_id(bundle):
    write(bundle.root / "a.md", concept("A"))
    migrate_ids(bundle)
    oid = okf_id(bundle, "a")
    # the rewrite dropped the id line, then one that tries to change it
    accept(bundle, _propose(bundle, concept("A", "New.\n"), "a", "update"))
    assert okf_id(bundle, "a") == oid
    accept(bundle, _propose(bundle, concept("A", "Newer.\n", "okf_id: forged\n"),
                            "a", "update"))
    assert okf_id(bundle, "a") == oid


def test_accept_create_and_supersede_mint_fresh_ids(bundle):
    write(bundle.root / "a.md", concept("A"))
    migrate_ids(bundle)
    oid = okf_id(bundle, "a")
    accept(bundle, _propose(bundle, concept("Fresh", extra=f"okf_id: {oid}\n"),
                            "fresh", "create"))
    assert okf_id(bundle, "fresh") not in (None, oid)
    accept(bundle, _propose(bundle, concept("A two"), "a", "supersede", new_id="a2"))
    assert okf_id(bundle, "a") == oid
    assert okf_id(bundle, "a2") not in (None, oid)
    assert "E_OKF_ID_DUPLICATE" not in codes(bundle)


def test_refine_refuses_to_change_okf_id(bundle):
    write(bundle.root / "a.md", concept("A"))
    migrate_ids(bundle)
    with pytest.raises(ValueError, match="okf_id"):
        refine(bundle, "a", concept("A", "Edited.\n"))
    refine(bundle, "a", concept("A", "Edited.\n", f"okf_id: {okf_id(bundle, 'a')}\n"))


def _legacy(bundle):
    """Strip init's bundle_uid and commit, as a bundle made before ids."""
    p = bundle.root / "meta/purpose.md"
    p.write_text("".join(line for line in p.read_text(encoding="utf-8").splitlines(True)
                         if not line.startswith("bundle_uid:")), encoding="utf-8")
    write(bundle.root / "a.md", concept("A"))
    write(bundle.root / "notes/b.md", concept("B"))
    run_git(bundle.root, "add", "-A", check=True)
    run_git(bundle.root, "commit", "-q", "-m", "legacy", check=True)


def _ids(root):
    b = Bundle(root)
    return b.purpose()["bundle_uid"], {c: okf_id(b, c) for c in ("a", "notes/b")}


def test_offline_clones_migrate_to_the_same_ids(bundle, tmp_path):
    _legacy(bundle)
    clones = [tmp_path / "one", tmp_path / "two"]
    for d in clones:
        run_git(tmp_path, "clone", "-q", str(bundle.root), str(d), check=True)
        migrate_ids(Bundle(d))
    (uid1, ids1), (uid2, ids2) = _ids(clones[0]), _ids(clones[1])
    assert uid1 == uid2 and str(uuid.UUID(uid1)) == uid1
    assert ids1 == ids2 and None not in ids1.values() and len(set(ids1.values())) == 2


def test_a_new_page_at_a_moved_page_path_gets_its_own_id(bundle):
    _legacy(bundle)
    migrate_ids(bundle)
    oid = okf_id(bundle, "a")
    (bundle.root / "a.md").rename(bundle.root / "moved.md")
    write(bundle.root / "a.md", concept("A again"))
    migrate_ids(bundle)
    assert okf_id(bundle, "moved") == oid and okf_id(bundle, "a") not in (None, oid)


def test_migrate_keeps_flow_style_frontmatter_valid(bundle):
    write(bundle.root / "a.md", "---\n{type: Note, title: A}\n---\nBody.\n")
    assert migrate_ids(bundle)["okf_id"] == ["a"]
    meta, body = frontmatter.parse((bundle.root / "a.md").read_text(encoding="utf-8"))
    assert meta["type"] == "Note" and meta["title"] == "A" and meta["okf_id"]
    assert body == "Body.\n"


def test_purpose_keeps_its_bundle_uid_through_accept_and_refine(bundle):
    uid = bundle.purpose()["bundle_uid"]
    text = (bundle.root / "meta/purpose.md").read_text(encoding="utf-8")
    dropped = "".join(line for line in text.splitlines(True)
                      if not line.startswith("bundle_uid:"))
    accept(bundle, _propose(bundle, dropped, "meta/purpose", "update"))
    assert bundle.purpose()["bundle_uid"] == uid
    forged = dropped.replace("---\n", "---\nbundle_uid: forged\n", 1)
    accept(bundle, _propose(bundle, forged, "meta/purpose", "update"))
    assert bundle.purpose()["bundle_uid"] == uid
    for edited in (dropped, forged):
        with pytest.raises(ValueError, match="bundle_uid"):
            refine(bundle, "meta/purpose", edited)
    assert bundle.purpose()["bundle_uid"] == uid


def _commit_all(root, msg):
    run_git(root, "add", "-A", check=True)
    run_git(root, "commit", "-q", "-m", msg, check=True)


@pytest.mark.parametrize("commit_new", [True, False])
def test_a_page_replacing_a_deleted_one_gets_its_own_id(bundle, commit_new):
    _legacy(bundle)
    migrate_ids(bundle)
    oid = okf_id(bundle, "a")
    _commit_all(bundle.root, "ids")
    (bundle.root / "a.md").unlink()
    _commit_all(bundle.root, "drop a")
    write(bundle.root / "a.md", concept("Unrelated"))
    if commit_new:
        _commit_all(bundle.root, "new a")
    migrate_ids(bundle)
    assert okf_id(bundle, "a") not in (None, oid)


def _legacy_embedded(corpus, rel):
    kb = Bundle(init_bundle(corpus / rel, corpus, embed=True))
    p = kb.root / "meta/purpose.md"
    p.write_text("".join(line for line in p.read_text(encoding="utf-8").splitlines(True)
                         if not line.startswith("bundle_uid:")), encoding="utf-8")
    return kb


def _git_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    write(corpus / "doc.md", "# doc\n")
    run_git(corpus, "init", "-q", check=True)
    _commit_all(corpus, "c")
    return corpus


def test_a_bundle_replacing_a_deleted_one_gets_its_own_uid(tmp_path):
    corpus = _git_corpus(tmp_path)
    old = _legacy_embedded(corpus, "kb")
    _commit_all(corpus, "kb")
    uid = migrate_ids(old)["bundle_uid"]
    _commit_all(corpus, "ids")
    shutil.rmtree(old.root)
    _commit_all(corpus, "drop kb")
    new = _legacy_embedded(corpus, "kb")
    _commit_all(corpus, "another kb")
    assert migrate_ids(new)["bundle_uid"] not in (None, uid)


def test_bundles_whose_paths_differ_by_a_space_get_distinct_uids(tmp_path):
    corpus = _git_corpus(tmp_path)
    one, two = _legacy_embedded(corpus, "kb"), _legacy_embedded(corpus, " kb")
    _commit_all(corpus, "both")
    assert migrate_ids(one)["bundle_uid"] != migrate_ids(two)["bundle_uid"]


def test_supersede_does_not_copy_bundle_uid(bundle):
    write(bundle.root / "a.md", concept("A"))
    accept(bundle, _propose(bundle, concept("A two", extra="bundle_uid: forged\n"),
                            "a", "supersede", new_id="a2"))
    assert "bundle_uid" not in bundle.get("a2").meta


def _clone_and_migrate(src, dst, *config):
    run_git(dst.parent, "clone", "-q", str(src), str(dst), check=True)
    for kv in config:
        run_git(dst, "config", *kv, check=True)
    migrate_ids(Bundle(dst))
    return _ids(dst)


def test_a_page_restored_by_a_merge_gets_the_same_id_in_every_clone(bundle, tmp_path):
    _legacy(bundle)
    root = bundle.root
    run_git(root, "branch", "side", check=True)
    (root / "a.md").unlink()
    _commit_all(root, "drop a")
    main = run_git(root, "branch", "--show-current", capture_output=True,
                   text=True).stdout.strip()
    run_git(root, "checkout", "-q", "side", check=True)
    write(root / "a.md", concept("A", "Kept.\n"))
    _commit_all(root, "edit a")
    run_git(root, "checkout", "-q", main, check=True)
    run_git(root, "merge", "-q", "side", capture_output=True)   # modify/delete conflict
    write(root / "a.md", concept("A", "Resolved to new text.\n"))
    run_git(root, "add", "a.md", check=True)
    run_git(root, "commit", "-q", "--no-edit", check=True)
    one = _clone_and_migrate(root, tmp_path / "one")
    assert one == _clone_and_migrate(root, tmp_path / "two")


def test_derived_ids_ignore_every_clone_local_git_setting(bundle, tmp_path, monkeypatch):
    key = tmp_path / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, capture_output=True)
    for kv in (("gpg.format", "ssh"), ("user.signingkey", str(key)),
               ("commit.gpgsign", "true")):
        run_git(bundle.root, "config", *kv, check=True)
    _legacy(bundle)                                   # a signed commit adds the pages
    plain = _clone_and_migrate(bundle.root, tmp_path / "plain")

    noisy = tmp_path / "noisy"
    run_git(tmp_path, "clone", "-q", str(bundle.root), str(noisy), check=True)
    head = run_git(noisy, "rev-parse", "HEAD", capture_output=True, text=True).stdout
    write(noisy / ".git/info/grafts", head)           # HEAD as a parentless root
    for kv in (("log.showSignature", "true"), ("log.showRoot", "false"),
               ("color.ui", "always"), ("i18n.logOutputEncoding", "UTF-16"),
               ("log.follow", "true"), ("diff.renames", "copies"),
               ("log.diffMerges", "separate"), ("format.pretty", "oneline"),
               ("log.abbrevCommit", "true"), ("log.decorate", "full"),
               ("core.quotePath", "false"), ("diff.relative", "true"),
               ("core.precomposeUnicode", "false")):
        run_git(noisy, "config", *kv, check=True)
    for k, v in {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "log.showRoot",
                 "GIT_CONFIG_VALUE_0": "false", "GIT_GLOB_PATHSPECS": "1",
                 "GIT_NOTES_REF": "refs/notes/x", "LC_ALL": "C"}.items():
        monkeypatch.setenv(k, v)
    migrate_ids(Bundle(noisy))
    assert plain == _ids(noisy)
