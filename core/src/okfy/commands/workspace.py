from pathlib import Path

from okfy.crosswalk import candidates
from okfy.workspace import Workspace, init_workspace, workspace_status

from .common import _print


def cmd_workspace(a) -> int:
    if a.wcmd == "init":
        members = []
        for spec in a.member:
            try:
                role, rest = spec.split(":", 1)
                name, mpath = rest.split("=", 1)
            except ValueError:
                raise ValueError(f"bad --member spec {spec!r} "
                                 "(want role:name=path)") from None
            members.append((name, Path(mpath), role))
        path = init_workspace(a.dir, members, title=a.title)
        _print({"created": str(path)})
    elif a.wcmd == "status":
        _print(workspace_status(Workspace.load(a.dir)))
    elif a.wcmd == "export":
        from okfy.export_fusion import export_workspace
        out = export_workspace(Workspace.load(a.dir), a.out)
        _print({"exported": str(out)})
    elif a.wcmd == "package":
        from okfy.package import package_workspace
        package_workspace(Workspace.load(a.dir))
        _print({"packaged": str(Path(a.dir).resolve())})
    return 0


def cmd_link_candidates(a) -> int:
    _print([r.__dict__ for r in candidates(Workspace.load(a.dir))])
    return 0
