# Changelog

All notable changes to wiki-spaces are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Overflow guidance no longer leads with "split". An overflow is diagnosed
  — bloat → distill; growth → reshape the space (siblings under a hub,
  promotion, a regrouped layout) — with continuation filenames
  (`topic-2.md`, `topic-more.md`) named as the anti-pattern. `ws-update`
  step 7 carries the procedure; the spec, README, core blocks, `ws-tend`,
  `init.md`, and both `ws.py` verdict strings follow it.
- `shared/` classifies as external at any depth, matching the any-depth
  semantics of the submodule and symlink rules — a nested space's mounts
  get the same fence the root's do.
- Size caps resolve per space: the nearest `_meta/limits.md` at or above a
  file wins (the `_template.md` rule), and the lookup never crosses a
  trust boundary — an external space answers to its own limits or the
  defaults, never the host's. `check-size` verdicts no longer depend on
  which root resolved.
- `audit --fix` report lines read `fixed` / `fix-skipped` (previously
  `~` / `!` under one `fixed` prefix), and a case-variant heading
  (`## spaces`) is reported with a rename hint instead of having a second
  heading inserted next to it.
- `audit --fix` follows one completion rule: it completes a half-declared
  space — registers an unlisted valid child, inserts the heading a
  registered child lacks — and never promotes an undeclared folder; a
  coincidental `index.md` (a docs site, say) is reported, not rewritten.
  Bare and plain dirs group transparently — a space beneath them
  registers at its nearest real ancestor — and an entry crossing another
  space's boundary is declined: that space owns the deeper listing.
- Near-miss heading detection generalized from the case-variant special
  case to the class: `## spaces`, `## Spaces ##`, and `##Spaces` all get
  the rename hint, and repair defers on each.
- Dot-prefixed names are reserved at any depth for files as well as
  folders — Obsidian cannot display dotfiles either.
- `check-size --stdin` reports a planned write that shrinks an over-cap
  file as `ok … shrinking write is progress` (exit 0): the spec's
  shrinking-write allowance is now expressible by the tool.

### Added

- Stderr advisory channel in `ws.py`: `list`, `files`, `grep`, and
  `check-size` announce the resolved root (`wiki: …`); walks emit `note:`
  lines for skipped external paths, spaces unreachable via `## Spaces`,
  unreadable files, and external `check-size` targets. Stdout stays pure
  data.
- New audit findings: `mount` (a registered external mount that is no
  longer a wiki — watched by the *default* audit; interior findings still
  need `--external`), broken relative markdown links alongside wikilinks,
  and `unreadable` (non-UTF-8) files. Findings inside external spaces are
  marked `[external]` under `--external`.
- `references/share.md` in ws-update — the producer side of sharing:
  verify a space stands alone, pick snapshot / repo / two-way sync, carve
  it into its own repo, optionally re-mount it as your own consumer.
- `_meta/ignore.md` convention: folder names the filesystem walk skips
  (one per line, nearest-file lookup like `limits.md`, trust-boundary
  fenced) — the reserved set is user-extensible, so a repo-root wiki can
  silence `node_modules`-style vendor trees without hardcoded lists. A
  contract-registered space is still reached.
- New audit findings: `duplicate entry`, `a second ## Spaces heading`,
  `unregistrable child name` (a folder whose name cannot survive a
  contract entry — `[](){}`), and, under `--external`, `missing entry`
  drift for a mounted external space nobody registered (`--fix` never
  registers those; mount.md's by-hand step stands).
- The audit emits the same stderr walk advisories the data commands do
  (skipped external paths, unlistable directories) — silence never means
  "looked everywhere" on any command now.
- `## Items` documented in CONVENTIONS.md: human navigation, not
  contract; drift there surfaces through the broken-link scan.

### Fixed

- `## Spaces` hrefs are read as CommonMark destinations: percent-encoding
  decodes on read (Obsidian writes `my%20space/index.md`), every check
  runs on the decoded name, and `audit --fix` writes encoded hrefs — a
  folder name with a space registers dialect-valid instead of surfacing
  as stale-plus-missing and being "repaired" with a link no renderer
  follows. The audit's unregistrable verdict asks the same encode/decode
  round-trip the fix verifies before writing.
- An entry rides any markdown bullet marker (`-`, `*`, `+`), matching the
  spec's "an entry is a markdown bullet" — a `*` or `+` entry no longer
  drops its space off the contract silently, with `--fix` appending a
  duplicate `-` entry beneath it. Skills keep writing `-`; a non-entry
  bullet of any marker is flagged malformed.
- `init.md`'s adopt path ensures the adopted root itself carries
  `## Spaces` before the first audit — an existing heading-less
  `index.md` dead-ended setup with `not a wiki`.
- A registered mount that was not a valid wiki was invisible: silently
  dropped from traversal and absent from the default audit.
- A nested space's `_meta/limits.md` was ignored; the same file could be
  `over` from the root and `ok` from the space.
- `audit --external` findings advertised `(audit --fix inserts it)` on
  external files that `--fix` correctly refuses to touch; external
  contract findings now say the owner repairs them.
- Trust scope now holds at every git boundary: a nested checkout's
  foreign-origin submodule classified as owned — `grep` read it without
  `--external` and `audit --fix` could write into it. A submodule is
  foreign when its URL differs from the origin of the repo that
  *declares* it, at any depth, whichever root resolved; quoted
  `.gitmodules` values are unwrapped.
- `audit --fix` wrote contract entries its own parser rejects (folder
  names carrying `[](){}`), reported them as `fixed`, and the malformed
  bullet then blocked every later registration in that index. Entries now
  round-trip through the parser before writing; an offending name is an
  `unregistrable` finding, never a block on its siblings.
- `## Spaces` inside YAML frontmatter counted as the contract for
  classification while the link scan ignored it; every reader of a
  document now starts after the frontmatter block.
- Dangling relative links escaping the root (`../gone.md`) passed the
  broken-link scan silently; all relative targets are now checked on
  disk. Wikilinks to assets (`[[report.pdf]]`) were false-positive broken
  and are exempt like embeds; indented code blocks are no longer scanned;
  ambiguous wikilinks credited only their first match as incoming.
- Deep nesting died with a raw `RecursionError` near 1000 levels; the
  walkers are iterative, so depth is bounded by the filesystem, not the
  interpreter.
- `check-size` on a target outside the wiki applied the host's
  `_meta/limits.md`; out-of-tree paths answer to the defaults alone.
- A registration rewrite converted a CRLF `index.md` wholesale to LF;
  repairs now probe and preserve the file's line endings, and
  `write_atomic` no longer lets the platform translate newlines.

## [3.0.0] — 2026-06-10

### Removed

- Python package, CLI, installer, and doctor command.
- Manifest machinery and the `.manifest.json` file.
- Vendored kepano skills from the core repository.
- Log rotation and glob-based size cap tables.
- The `repo` configuration key.
- PyPI distribution.

### Added

- Bundled `ws.py` script duplicated inside each of the three skill
  directories: deterministic traversal (`list`, `files`), trust-scoped
  regex search (`grep`), cap verdicts (`check-size`), and bounded repair
  (`audit --fix`).
- Distribution via `npx skills add anfreire/wiki-spaces` (companions:
  `npx skills add kepano/obsidian-skills --skill obsidian-markdown
  --skill obsidian-bases`).
- Byte-based, basename-keyed size cap model; `_meta/limits.md` overrides
  per basename, with the literal `*.md` re-capping the content-page
  catch-all.
- Detached wiki discovery and conflict announcement when multiple wikis
  are found; an unusable configured `wiki` key is reported with its
  reason, never skipped silently.
- Self-contained skills that manage their own operations.

### Changed

- The `## Spaces` contract is now maintained directly by the skills.
- `ws-update` suggests a `ws-tend` pass when audit findings fall outside the
  sync's write scope (suggestion only, never auto-run).
- Size caps are enforced via a detect-and-repair model.
- The `log.md` file is demoted to optional append-only notes with no rotation.
- Kepano skills are now installed as a companion package.

## [2.0.0] — 2026-06-04

### Removed

- The `bridges/` directory.
- The `--bridge` flag.
- The `BRIDGES` dict and `_emit_bridge` helper.
- Harnesses antigravity, hermes, devin/windsurf, and aider as installable or auto-detected options.

### Added

- Integration of opencode, copilot, and cursor as first-class hub-reading harnesses.

### Changed

- The install model is now a single shared hub at `~/.agents/skills/` with per-skill owned-gated aliases for claude and kiro only (the two harnesses without documented hub support).
- Command `doctor` verifies install state against the hub-maximalism harness table.

### Unchanged

- Python floor `>=3.11`.
- Runtime dependency on pyyaml only.
- The `AGENTS.md` `## Spaces` spec remains frozen.

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
