# Golden wiki — edge inventory

The golden wiki (`tests/conftest.py::build_golden_wiki`) is **one canonical
deterministic tree** that is the union of every wiki *shape* and every
*committable* traversal *edge*. Its observable output is pinned by the
exact-match snapshots in this directory and exercised by `tests/test_golden.py`.

- **Snapshots** (`*.json`): exact-match, canonical (`sort_keys`, 2-space indent).
  Regenerate **only** deliberately with `REGEN_GOLDEN=1 pytest tests/test_golden.py`
  and review the diff — golden drift is never silent.
- **Inventory guard**: `test_golden_wiki_covers_every_edge` asserts each edge
  below actually surfaces in live output, so the fixture can't lose coverage.

## Why these edges and not symlinks / real git

Exact-match snapshots must be **deterministic on every POSIX platform**. Real
`os.symlink` and real `git submodule add` produce platform- or
checkout-dependent state, so they are **not** in the golden tree. The
foreign-submodule *classification* edge **is** here, because it is driven purely
by parsing `.git/config` + `.gitmodules` text (no git binary). The symlink /
clone / submodule *traversal* edges live in `tests/test_traversal_gate.py`
(escaping symlink, symlink cycle, index-less boundary, foreign-submodule
descendant) and the mount tests in `tests/test_space.py`.

## Snapshots

| File | Command |
|------|---------|
| `list.json` | `space list --json` |
| `list_external.json` | `space list --include-external --json` |
| `files.json` | `space files --json` |
| `files_external.json` | `space files --include-external --json` |
| `audit.json` | `space audit --json` (the `wiki` path and pyyaml-coupled MALFORMED frontmatter fields are normalized — see `normalize_audit_payload`) |

## Edge catalogue

Each row is tagged `# EDGE: <name>` at its definition site in `build_golden_wiki`.

| Edge | Where in the tree | Surfaces in |
|------|-------------------|-------------|
| registered-space | `concepts`, `projects`, `notebook`, … | `list` (owned) |
| deep-nesting | `concepts/sub` (registered under `concepts`) | `list` (last, LIFO) |
| flat-branch / minimum-viable | `flat/` (empty `## Spaces`) | `list` |
| no-description-entry | `[Flat](flat/index.md)` (no `— desc`) | `list` → `description: null` |
| description-provenance | `[Concepts](…) — ideas and notes` | `list` → `description` |
| stale-entry | `[Ghost](ghost/index.md)` (no dir) | `audit.drift[.].stale` |
| drift-missing (unregistered valid space) | `drift/` (not in any `## Spaces`) | `audit.drift[.].missing` |
| bare-index | `bare/index.md` (no `## Spaces`) | `audit.missing_spaces_section` + `audit.drift[.].missing` |
| malformed-entry: empty href | `- [Empty]()` in `malformed-host` | `audit.malformed_entries` |
| malformed-entry: unparseable bullet | `- [[half` in `malformed-host` | `audit.malformed_entries` |
| reserved-href | `- [Reserved](_meta/x/index.md)` | `audit.malformed_entries` + `audit.drift[malformed-host].stale` (`_meta/x`) |
| plain-folder | `concepts/plain/` (no `index.md`) | `files` (`concepts/plain/asset.md`), not in `list` |
| reserved-dir (pruned) | `_archives/old.md`, `_meta/limits.md` | absent from `files` |
| valid-frontmatter | `notebook/daily.md` | absent from `audit.malformed_frontmatter` |
| malformed-frontmatter (yaml error) | `notebook/snippets.md` | `audit.malformed_frontmatter` (`status: malformed`) |
| nonmapping-frontmatter | `notebook/nonmap.md` | `audit.malformed_frontmatter` (`status: non_mapping`) |
| duplicate-aliases | `aliasdup/a.md` + `b.md` (both `shared-alias`) | `audit.duplicate_aliases` |
| orphan | `concepts/foo.md`, `projects/acme/notes.md`, … | `audit.orphans` |
| approaching-cap (user override) | `notebook/approaching.md` + `_meta/limits.md` (cap 400) | `audit.approaching_cap` (`cap_source.kind: user_override`) |
| valid-wikilink | `concepts/foo.md` → `[[bar]]` (basename) | NOT in `audit.broken_wikilinks` |
| broken-wikilink | `notebook/brokenlink.md` → `[[definitely-missing-target]]` | `audit.broken_wikilinks` (with resolver `tried` trace) |
| spaces-heading-as-content | `notebook/about-spaces.md` (an `## Spaces` H2 in a non-index page) | `audit.orphans` (treated as a normal page, NOT a space) |
| shared-external | `shared/team/` (path-classified external) | `list_external` / `files_external` (`external: true`); absent from owned |
| foreign-submodule | `projects/vendor/` (via `.gitmodules`) | `list_external` / `files_external` (`external: true`); absent from owned |
| promote-candidate (hub) | `notebook/` (6 direct content pages) | `audit.promote_candidates` (`kind: hub`); never flips `exit_code` |
| LIFO ordering | every snapshot's list order | exact list order in all snapshots |

Focused trigger / non-trigger coverage for the self-maintenance signals
(`promote_candidates` kinds `hub`/`split_ready`, `prune_candidates` kind
`hot_distill`, and that none of them flip the exit code) lives in
`tests/test_audit_signals.py`.
