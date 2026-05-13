# scripts

## What this space is

Thin PEP 723 shims that forward to the `wiki_spaces` package modules under
[`../src/wiki_spaces/`](../src/wiki_spaces/). Kept for the documented
dev-from-source flow (`./scripts/<name>.py`); once installed via pip/uvx,
the canonical entry is the `wiki-spaces` console script (see
[`../pyproject.toml`](../pyproject.toml)) which dispatches into the same
package modules.

## Items

- [`install.py`](install.py) — `wiki-spaces install`: link wiki + kepano skills into detected harnesses; write `repo = <data-path>` to the config.
- [`init_wiki.py`](init_wiki.py) — `wiki-spaces init`: scaffold a new canonical wiki at a given path; writes `wiki = <path>` to the config (unless `--no-config`).
- [`doctor.py`](doctor.py) — `wiki-spaces doctor`: read-only audit of config validity, vendor pin, per-harness skill install state.
- [`update.py`](update.py) — `wiki-spaces update`: re-vendor kepano (dev) + re-run install (idempotent superset).
- [`vendor_kepano.py`](vendor_kepano.py) — `wiki-spaces vendor-kepano`: shallow-clone kepano upstream and refresh `vendor/kepano/`. Dev-only; refuses to run from a packaged install.

Invocation: `./scripts/<name>.py [args]`. Each shim declares its inline
metadata for uv; first run on a fresh box may take a moment while `uv`
resolves Python ≥3.11.
