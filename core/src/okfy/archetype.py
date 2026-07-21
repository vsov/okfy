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
        consumption_protocol=d.get("consumption_protocol", ""),
        root=root,
    )
