import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml


@dataclass
class Archetype:
    name: str
    version: int
    description: str
    canonical_types: list[str]
    required_fields: dict[str, list[str]]
    required_sections: dict[str, list[str]]
    layout: dict[str, str]
    purpose_checks: list[dict]
    link_rules: list[dict]
    nonempty_sections: dict[str, list[str]]
    # {type: {field: [allowed values]}} — closed vocabularies the validator
    # enforces (E_FIELD_ENUM); free text in an enum field is machine-invisible
    field_enums: dict[str, dict[str, list[str]]]
    # OPTIONAL token-size guidance: {"types": {T: {target_min, target_max}},
    # "resident_max": int}. Advisory only — `okfy budget` reports against it and
    # nothing gates on it. An archetype without the block is not defective; its
    # report simply shows no targets.
    budgets: dict
    consumption_protocol: str
    root: Path


def archetypes_root() -> Path:
    return Path(str(resources.files("okfy"))) / "archetypes"


def load_archetype(name: str) -> Archetype:
    root = archetypes_root() / name
    spec = root / "archetype.yaml"
    if not spec.is_file():
        raise FileNotFoundError(f"unknown archetype: {name} (looked in {spec})")
    d = yaml.safe_load(spec.read_text(encoding="utf-8"))
    return Archetype(
        name=d["name"], version=int(d["version"]), description=d.get("description", ""),
        canonical_types=d.get("canonical_types", []),
        required_fields=d.get("required_fields", {}),
        required_sections=d.get("required_sections", {}),
        layout=d.get("layout", {}),
        purpose_checks=d.get("purpose_checks", []),
        link_rules=d.get("link_rules", []),
        nonempty_sections=d.get("nonempty_sections", {}),
        field_enums=d.get("field_enums", {}),
        budgets=d.get("budgets", {}) or {},
        consumption_protocol=d.get("consumption_protocol", ""),
        root=root,
    )


def checks_digest(archetype) -> str:
    """A digest of the questions an L3 review answered.

    A recorded `pass` is a pass against a specific check, phrased a specific
    way. Rewording `decision-ready` or adding a sixth purpose check changes what
    the review would have concluded, and without this the old verdict silently
    carries forward as though it had answered the new question. `sampled` is
    pinned by `sampled_fingerprint`, `seed` by the corpus — this is the third
    leg, and the artifact was resting on two.

    `sort_keys` because the digest is about the CONTENT of the checks, and a
    reordering of the YAML is not a change to what they ask."""
    checks = list(getattr(archetype, "purpose_checks", None) or []) if archetype else []
    payload = json.dumps(checks, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
