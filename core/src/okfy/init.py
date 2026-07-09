import datetime
import hashlib
import json
import subprocess
from pathlib import Path

from okfy.frontmatter import serialize
from okfy.guard import assert_safe_bundle_path


def _git(bundle: Path, *args) -> None:
    subprocess.run(["git", "-C", str(bundle), *args], check=True, capture_output=True)


def _corpus_git_sha(corpus: Path) -> str | None:
    r = subprocess.run(["git", "-C", str(corpus), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _manifest(corpus: Path) -> dict[str, str]:
    out = {}
    for p in sorted(corpus.rglob("*")):
        rel = p.relative_to(corpus)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def init_bundle(path: Path | None, corpus: Path, language: str = "en",
                write_policy: str | None = None, embed: bool = False) -> Path:
    corpus = Path(corpus).resolve()
    if embed:
        if _corpus_git_sha(corpus) is None:
            raise ValueError("--embed requires the corpus to be a git repository")
        path = Path(path) if path is not None else corpus / ".okf"
        if not path.resolve().is_relative_to(corpus):
            raise ValueError("--embed bundle path must live inside the corpus")
    elif path is None:
        raise ValueError("bundle path required (or use --embed)")
    else:
        path = Path(path)
    if write_policy is None:
        write_policy = "direct" if embed else "proposals"
    assert_safe_bundle_path(path)
    path.mkdir(parents=True, exist_ok=False)
    meta = path / "meta"
    meta.mkdir()
    today = datetime.date.today().isoformat()

    (path / ".gitignore").write_text(".okfy-cache/\n", encoding="utf-8")
    (meta / "purpose.md").write_text(serialize(
        {"type": "Purpose", "title": "(to be written by Purpose Interview)",
         "language": language, "write_policy": write_policy, "test_queries": []},
        "Purpose statement pending — /okfy:new fills this in.\n"), encoding="utf-8")
    (meta / "corpus-manifest.json").write_text(
        json.dumps(_manifest(corpus), indent=0, sort_keys=True), encoding="utf-8")
    (meta / "corpus.md").write_text(serialize(
        {"type": "CorpusSnapshot", "corpus": str(corpus), "extracted_at": today,
         "git_sha": _corpus_git_sha(corpus), "manifest": "corpus-manifest.json",
         "embed": embed},
        f"Snapshot of {corpus} taken {today}.\n"), encoding="utf-8")
    (path / "log.md").write_text(f"# Log\n\n## {today}\n\n- init: bundle skeleton\n",
                                 encoding="utf-8")

    if not embed:
        _git(path, "init", "-q")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "init: bundle skeleton")
    return path.resolve()
