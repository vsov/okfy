# okfy-normalize

Converts raw documents (PDF, Office, images) into normalized Markdown plus a
`source-map.jsonl` sidecar, so a concept citing `handbook.md#L811-L824` can be
traced back to the page it came from.

Deliberately outside OKFy's core. Core validates the sidecar with the standard
library alone (`okfy sourcemap`); this adapter produces it and may depend on
whatever a converter needs.

## Use

```
uv run okfy-normalize <src> <corpus-dir> [--backend NAME] [--option K=V ...]
okfy sourcemap <bundle>          # verifies the sidecar against the corpus
```

Copy `<corpus-dir>/source-map.jsonl` to `<bundle>/meta/source-map.jsonl`.

## Backends

| Name | Installs | Handles |
|------|----------|---------|
| `passthrough` | nothing | `.md`, `.markdown`, `.txt` |
| `docling` | `uv pip install docling` | PDF, Office, images |

`passthrough` is the default and needs no third-party package, which is what
keeps this adapter testable without a multi-hundred-package ML stack. Naming a
backend whose package is missing exits non-zero with the install line — never an
ImportError traceback.

No `[docling]` extra is declared: it would pin ~130 packages (torch, opencv)
into this adapter's lockfile for a path nothing installs, and would name one
converter in the metadata of an adapter built so any converter fits.

Every option passed with `--option` is covered by `converter_options_digest`, so
two conversions of the same file with different settings are distinguishable.

## Limits, stated rather than implied

- `page` and `bbox` are carried, never verified — the core does not open raw
  documents, and a bbox is the converter's claim.
- The `docling` backend currently emits one whole-file span. Its per-item
  provenance does not line up with the exported Markdown's line numbers without
  re-deriving them, and claiming a finer mapping before measuring it against a
  real document would be a fabrication.
- The adapter never writes into the source tree; `dest` inside `src` is refused.
