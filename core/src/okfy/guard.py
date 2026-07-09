from pathlib import Path

TOOL_MARKER = ".okfy-tool-repo"


class GuardError(RuntimeError):
    pass


def assert_safe_bundle_path(path: Path) -> None:
    """Bundles are private artifacts — never created inside the OKFy tool repo."""
    p = Path(path).resolve()
    for parent in [p, *p.parents]:
        if (parent / TOOL_MARKER).is_file():
            raise GuardError(
                f"refusing to create a Bundle under the OKFy tool repo ({parent}). "
                "Bundles are private; pick a path outside this repository."
            )
