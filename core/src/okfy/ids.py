"""Stable identity: one `bundle_uid` per bundle, one `okf_id` per page.

A concept id is its path, so it changes when the page moves; these do not.
Both are opaque uuid strings, minted once, persisted, and never recomputed.
A new bundle or page gets a random uuid4. A legacy one backfilled by
`okfy migrate ids` gets a uuid5 derived from provenance every clone shares,
because two clones of one bundle may each migrate offline and must not end
up as two bundles: see `_legacy_uid`.

`bundle_uid` lives in `meta/purpose.md`'s frontmatter: root `index.md` may
declare nothing but `okf_version` (OKF v0.2 §8, `E_INDEX_FRONTMATTER`), and
purpose.md is already the bundle's own metadata. `okf_id` lives in each
concept page's frontmatter, so it travels with the file through a rename.

Pages that carry no `okf_id` — the reserved `index.md` and `log.md`, the
generated root docs, and `meta/*` — are addressed as `bundle_uid` plus their
bundle-relative path; their names are fixed, so the pair is already stable."""
import os
import uuid

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.gitenv import run_git

# The frontmatter keys only the core writes: a proposal or a refine never sets them.
IDENTITY_KEYS = ("bundle_uid", "okf_id")
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://okfy.dev/ids")


def new_id() -> str:
    return str(uuid.uuid4())


def carries_okf_id(concept_id: str) -> bool:
    return not concept_id.startswith("meta/")


def _pinned_git(root, *args) -> str | None:
    """Run a provenance read with everything that could make two clones of
    one history print different answers pinned or cleared. Command-line `-c`
    beats every config file and the GIT_CONFIG_* env, so a key set here wins
    whatever a clone carries; `run_git` already strips every GIT_* variable
    but identity, transport, GIT_EXEC_PATH and GIT_CONFIG_*. The class, for
    `git log -1 --format=%H --name-status -- <path>` and `rev-parse --show-prefix`:

    - history rewrites: replace refs (`--no-replace-objects`), legacy
      `info/grafts` (GIT_GRAFT_FILE at a path that cannot exist). A shallow
      boundary is one too and cannot be undone here: see `_legacy_uid`.
    - which commit is picked: `log.showRoot` (root commit's adds),
      `log.follow`, `diff.renames`/`renameLimit` (`--no-renames`),
      `log.diffMerges` (`--diff-merges`); ordering keys do not matter on the
      linear `--first-parent` walk.
    - what is printed: `format.pretty` (`--format`), `core.abbrev`/
      `log.abbrevCommit` (`%H` is full), `log.showSignature` (prints into
      stdout), `color.*` (`--no-color`), `i18n.logOutputEncoding` (re-encodes
      all output; `--encoding=UTF-8`, decoded strictly), pager (`--no-pager`).
      `log.decorate`, `log.mailmap`, `log.date`, `notes.*` never reach `%H`.
    - paths: pathspec magic (`--literal-pathspecs`; GIT_*_PATHSPECS are
      stripped), `core.precomposeUnicode` (NFD/NFC on macOS, pinned on).
      `core.quotePath`, `diff.relative` and `diff.noprefix` change only the
      printed path, which is never parsed; `core.ignoreCase` is not consulted
      by tree pathspecs.
    - known and not pinned: `core.worktree` moves the work tree root, so it
      changes `--show-prefix` and with it a derived bundle_uid. It is repo
      layout, not display, and has no neutral value to pin.
    - errors: LC_ALL=C, so the two failures that mean "no history" — not a
      repository, no commits — are recognised; any other one (e.g.
      `safe.directory` refusing the repo) raises rather than read as none."""
    r = run_git(root, "--no-pager", "--no-replace-objects", "--literal-pathspecs",
                "-c", "log.showSignature=false", "-c", "log.showRoot=true",
                "-c", "log.follow=false", "-c", "core.precomposeUnicode=true",
                "-c", "i18n.logOutputEncoding=UTF-8", *args,
                env={"GIT_GRAFT_FILE": os.path.join(os.devnull, "grafts"), "LC_ALL": "C"},
                capture_output=True, encoding="utf-8")
    if r.returncode == 0:
        return r.stdout
    if "not a git repository" in r.stderr or "does not have any commits" in r.stderr:
        return None
    raise RuntimeError(f"git {args[0]} failed in {root}: {r.stderr.strip()}")


def _added_in(root, rel: str) -> str | None:
    """The commit that created the file now at `rel` (relative to `root`):
    the latest commit that added or deleted that path, if it added it. The
    latest, not the first, so a page or bundle deleted and later replaced at
    the same path is a new lineage rather than its predecessor; and None when
    the path's last event was a delete or it was never committed, because a
    file history has not recorded yet has no provenance a clone could share.
    The mainline is read with each merge diffed against its first parent, so
    a merge that brings a deleted page back counts as its add; otherwise git
    shows no diff for the merge and reports the delete."""
    out = _pinned_git(root, "log", "-1", "--first-parent", "--diff-merges=first-parent",
                      "--no-renames", "--no-color", "--encoding=UTF-8",
                      "--diff-filter=AD", "--format=%H", "--name-status", "--", rel)
    words = (out or "").split()
    return words[0] if words[1:2] == ["A"] else None


def _legacy_uid(root) -> str:
    """uuid5 over the commit that created this bundle's meta/purpose.md and
    the bundle's path inside its repo. Both are history, so every full clone
    reads the same pair and no later edit changes it; the path tells apart
    bundles embedded in one corpus by the same commit. A shallow clone that
    does not reach that commit reads a different one. With no such commit
    there is no shared provenance: a random one."""
    commit = _added_in(root, "meta/purpose.md")
    if commit is None:
        return new_id()
    prefix = _pinned_git(root, "rev-parse", "--show-prefix").removesuffix("\n")
    return str(uuid.uuid5(_NS, f"bundle_uid\0{commit}\0{prefix}"))


def _add_key(path, key: str, value: str) -> None:
    """Insert `key: value` as the first frontmatter line. A text insert rather
    than a re-serialize, so the owner's own YAML — comments, quoting, key
    order — is left byte-for-byte as it was. Where the insert does not read
    back as exactly that key added (a flow mapping `{type: Note, ...}` has no
    first line to put it before) the frontmatter is re-serialized instead."""
    text = path.read_text(encoding="utf-8")
    meta, body = frontmatter.parse(text)
    want = ({key: value, **meta}, body)
    out = f"---\n{key}: {value}\n{text[4:]}"
    try:
        same = frontmatter.parse(out) == want
    except frontmatter.FrontmatterError:
        same = False
    if not same:
        out = frontmatter.serialize(*want)
    path.write_text(out, encoding="utf-8")


def migrate_ids(bundle: Bundle) -> dict:
    """Backfill `bundle_uid` and every missing `okf_id`. A key already present
    is never touched — even an empty one, which `okfy validate` reports
    instead of having it silently replaced — so a rerun writes nothing.

    An `okf_id` is uuid5 over the bundle_uid, the commit that created the
    page (`_added_in`) and its path, so clones agree page by page; `#n` steps
    past one a page moved in the working tree still holds. A page with no
    such commit gets a random one."""
    out = {"bundle_uid": None, "okf_id": []}
    purpose = bundle.get("meta/purpose")
    if purpose is None:
        raise FileNotFoundError("meta/purpose.md missing — not an OKFy bundle?")
    uid = purpose.meta.get("bundle_uid")
    if "bundle_uid" not in purpose.meta:
        uid = out["bundle_uid"] = _legacy_uid(bundle.root)
        _add_key(purpose.path, "bundle_uid", uid)
    concepts = bundle.concepts()
    taken = {str(c.meta["okf_id"]) for c in concepts if "okf_id" in c.meta}
    for c in concepts:
        if carries_okf_id(c.id) and "okf_id" not in c.meta:
            commit, n = _added_in(bundle.root, f"{c.id}.md"), 0
            oid = new_id() if commit is None else None
            while oid is None or oid in taken:
                oid = str(uuid.uuid5(_NS, f"okf_id\0{uid}\0{commit}\0{c.id}#{n}"))
                n += 1
            taken.add(oid)
            _add_key(c.path, "okf_id", oid)
            out["okf_id"].append(c.id)
    return out
