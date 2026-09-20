"""Every git call in this package must run through `run_git`, never a raw
`subprocess.run(["git", ...])`.

Measured failure: inside a git hook (a linked-worktree commit, or any
`git --git-dir=... --work-tree=... commit`), git exports GIT_DIR,
GIT_WORK_TREE and GIT_INDEX_FILE into the child environment. A subsequent
`git -C <other-repo> ...` inherits those and answers from the HOOK's repo,
not `<other-repo>` — `-C` changes git's cwd but does not override an
already-exported GIT_DIR. OKFy's generated pre-commit hook runs
`okfy validate`, which asks the CORPUS repo questions while GIT_DIR still
points at the bundle's hook — so validate silently read the wrong repo.
"""
import os
import subprocess

# Kept: identity and transport config a caller may deliberately set for THIS
# invocation (e.g. tests pinning GIT_AUTHOR_*, or a real SSH/askpass setup).
# Everything else starting with GIT_ — GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE,
# GIT_OBJECT_DIRECTORY, etc. — is repo-location state a hook exports for
# ITSELF and must not leak into a `-C <repo>` call to a different repo.
_ALLOW_PREFIXES = ("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG", "GIT_SSH")
_ALLOW_EXACT = {"GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "GIT_EXEC_PATH"}


def git_env() -> dict:
    """os.environ with every non-allowlisted GIT_* key stripped."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("GIT_") and k not in _ALLOW_EXACT \
                and not k.startswith(_ALLOW_PREFIXES):
            del env[k]
    return env


def run_git(repo, *args, **kw) -> subprocess.CompletedProcess:
    """subprocess.run(["git", "-C", str(repo), *args], env=git_env(), **kw).

    A caller-supplied `env` is merged OVER git_env(), so tests that need one
    extra variable don't have to reconstruct the whole allowlist."""
    env = git_env()
    if "env" in kw:
        env.update(kw.pop("env"))
    return subprocess.run(["git", "-C", str(repo), *args], env=env, **kw)
