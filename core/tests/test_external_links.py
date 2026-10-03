from conftest import concept, write

from okfy.query import links
from okfy.validate import resolve_link, validate_integrity


def test_any_scheme_is_external_not_a_bundle_path(bundle):
    page = bundle.root / "a.md"
    for t in ("https://x.org/a.md", "mailto:me@x.org", "other-tool://NODE-1/page.md",
              "other-tool://NODE-1", "tel:+100"):
        assert resolve_link(bundle, page, t) is None, t
    assert resolve_link(bundle, page, "b.md") == "b"
    assert resolve_link(bundle, page, "./sub/c.md#h") == "sub/c"


def test_external_links_are_reported_and_never_dangle(bundle):
    write(bundle.root / "b.md", concept("B"))
    write(bundle.root / "a.md", concept(
        "A", "See [b](b.md), [n](other-tool://NODE-1/page.md), [t](other-tool://T-2) "
             "and [n again](other-tool://NODE-1/page.md).\n"))
    assert links(bundle, "a") == {
        "id": "a", "out": ["b"], "backlinks": [],
        "external": ["other-tool://NODE-1/page.md", "other-tool://T-2"]}
    assert "W_DANGLING_LINK" not in [f.code for f in validate_integrity(bundle).findings]
