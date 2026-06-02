# Changelog

All notable changes to wiki-spaces are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

## [1.2.0] — 2026-06-02

### Added

- Internal semantic model layer (`_model.py`): orthogonal node facts, a
  unified page index, and verdicts that carry their own provenance
  (`CapVerdict`/`CapSource`, `WikilinkResolution` attempt traces,
  `FrontmatterResult` status). `space audit` and `space check-size` surface
  that provenance.
- Two model-owned traversals — a consumer/contract walk and an owned/FS walk
  — now back `space list`, `space files`, `space promote`, and `space audit`,
  replacing the per-command private walkers so the writer's output and the
  reader's input can no longer diverge.
- `wiki-spaces manifest set/get/list`: read and write `.manifest.json` safely
  (parent-directory `flock` + atomic replace), so skills stop embedding the
  raw snippet.
- `space caps`: list the effective size-cap rules at a wiki root with their
  source (built-in default vs. `_meta/limits.md` override).
- `space add --from-template <path>` for explicit template-seeded spaces and
  `space add --summary` for the frontmatter `summary:` field.

### Changed

- **PyYAML (`pyyaml>=6`) is now a required runtime dependency.** Frontmatter
  parsing routes through `yaml.safe_load` for full YAML semantics (folded
  scalars, block lists, aliases) and to distinguish malformed frontmatter
  from absent during audit.
- Unified the wiki resolver behind one `_common` helper (strict vs. repair
  variants) instead of per-command resolution.
- `space check-size` reads the projected content from stdin (or
  `--projected-file`) and refuses a TTY, rather than the foot-gun that returned
  a misleading `OK 0/<cap>` when nothing was piped. The old `--projected-stdin`
  flag was removed — stdin is now the default source.
- `--wiki` is accepted in either argv position for `space` and `manifest`.
- Removed the unused reconciliation engine from the model layer (built,
  never consumed).

### Fixed

- `.manifest.json` writes reject non-finite JSON values (`NaN`, `Infinity`,
  `-Infinity`, numeric overflows such as `1e999`, including nested values) on
  coerce, read, and write, keeping the file portable; the schema is validated
  on read (refuse-to-overwrite a malformed file).
- Atomic writes fsync the parent directory after `os.replace` (manifest,
  `space` index mutations, and `_log` log/archive writes) so the rename
  itself is crash-durable, not just the temp file's bytes.
- `_model` git-origin resolution is worktree-aware: inside a git worktree
  (where `.git` is a file and config is shared via `commondir`) the wiki's
  origin now resolves correctly, so a same-origin submodule is no longer
  misclassified as foreign.
- Contract reachability propagates only through valid spaces — a registered
  child whose `index.md` lacks `## Spaces` is drift, not a reachable space.
- Frontmatter consumers are hardened against `yaml.safe_load` type coercion;
  size caps skip non-positive values, drop shadowed built-in duplicates, and
  report 1-based line numbers in JSON.
- `space add --summary` is refused on an already-existing space without
  `--force-index`, and `add --description` is validated as `## Spaces` entry
  text and included in the chain cap pre-flight.
- Framework writes are uniformly fail-closed: a failed `space mount`, `add`,
  `promote`, or `init` removes any directories, placeholders, or overwritten
  `index.md` it created, and content writes are crash-atomic through one shared
  `atomic_write` (temp + fsync + `os.replace` + parent-dir fsync).
- Non-UTF-8 and unreadable boundary inputs no longer crash the tool: the wiki
  resolver, discovery walkers, writers, config reader, git metadata, and the
  vendored kepano `COMMIT` degrade or refuse with a clear cause instead of
  raising.
- Producer=consumer alignment across the toolchain: `space audit` flags exactly
  the `## Spaces` hrefs the writers refuse and the consumer walkers drop
  (metacharacters, reserved / `..` / absolute / self-referential, duplicates),
  scans `## Spaces` fence-aware so code-block examples aren't misread, and
  honors the nearest per-space `_meta/limits.md` in writers, `check-size`, and
  audit alike.

### Security

- Hardened every boundary input that can reach the `## Spaces` navigation
  contract: control characters, NEL / line / paragraph separators, and
  markdown-link metacharacters in `--name`, `--description`, and paths are
  rejected (they could otherwise inject a second heading or a malformed entry),
  and discovery prunes control-char-named directories so `init --adopt` /
  `audit --fix` can't register an entry the consumer can't read.
- Framework writers refuse to write through a symlink whose realpath escapes
  the wiki tree or lands in external scope (`shared/`, a foreign submodule),
  and `mount` / `add` / `audit` / `remove` never mutate across the external
  trust boundary.

### Documentation

- Documented the full v1 mount contract (`index.md` **and** `## Spaces`) in the
  `space mount` help and `references/MOUNT.md`.
- Extracted the page-promotion playbook to `references/PROMOTE.md` and
  centralized `log.md` write mechanics in `CONVENTIONS.md`, keeping the
  reference skills lean (common path inline, specialized depth one click away).
- Reconciled the spec and skill guidance with shipped behavior across
  `AGENTS.md`, `CONVENTIONS.md`, and the three `ws-*` skills.

## [1.1.0] — 2026-05-27

### Changed

- Renamed the three reference skills: `wiki-search` → `ws-search`,
  `wiki-update` → `ws-update`, `wiki-tend` → `ws-tend`, across source,
  bridges, and documentation.

### Fixed

- `hot.md` gets a 100,000-char default cap matching `log.md` (it is a
  convention file, not a content page).
- `doctor` distinguishes a `symlink-external` install from a
  `symlink-broken` one, and its success path nudges toward `space audit` for
  content health.
- Exhaustive correctness, clarity, and cross-platform review pass.

### Documentation

- Documented shape-based promotion triggers in `AGENTS.md`, the `hot.md`
  size cap and the `_meta/limits.md` override in `CONVENTIONS.md`/`SETUP.md`,
  skill source paths for external aggregators, that `init --adopt` does not
  size-audit existing content, and `space audit` as a post-setup health check.

## [1.0.0] — 2026-05-24

Initial release.
