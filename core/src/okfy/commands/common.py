import json

from okfy.archetype import load_archetype
from okfy.bundle import Bundle


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _archetype_for(bundle: Bundle):
    plan = bundle.plan()
    name = (plan.meta.get("archetype") if plan else None) or "decision-support"
    return load_archetype(name)
