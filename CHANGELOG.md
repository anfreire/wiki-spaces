# Changelog

All notable changes to wiki-spaces are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

## [4.0.0] — 2026-07-03

The subtraction release: the tool keeps only what has exactly one
deterministic answer — traversal, trust scope, caps, detection — and
every judgment call (dialect tolerance aside) moves to the skills. The
script is read-only by construction; findings name their repair, the
LLM applies it, and a re-run verifies. It ships smaller than what it
replaces.

### Removed

- `audit --fix`, and with it the script's only write path (the atomic
  writer, EOL probing, and the repair pass). The automated repair
  planned every fix from one snapshot: completing a registered-bare dir
  over a valid deep space inserted the heading *and* registered the
  deep child at the root — manufacturing a boundary-crossing entry no
  walk follows, which the audit then blessed as `ok`. Repair is
  judgment, and judgment sits with the caller: each finding names its
  edit, each re-audit re-derives from disk, so repairs converge in any
  order — including that adoption shape.
- The `[](){}` unregistrable-name policy. `HREF_ENCODE` covers every
  grammar-breaking character, so any folder name registers
  percent-encoded (`notes%20%282024%29/index.md`); the `unregistrable`
  finding remains only for a name no entry can carry (a directory
  literally named `index.md`).

### Added

- `crossing` audit finding: an entry that lists through another space's
  boundary is reported with its owner (`remove it; <space>/index.md
  owns the deeper listing`). The walk always declined such entries; the
  audit now judges every entry through the same function — what one
  declines, the other names, so this violation class can never again
  audit clean.
- `missing entry` findings print the exact entry to add, computed by
  the same encode/decode round-trip the parser applies — pasting the
  line verbatim resolves the drift, whatever the folder name.
- Stderr advisory channel in `ws.py`: `list`, `files`, `grep`, and
  `check-size` announce the resolved root (`wiki: …`); walks emit `note:`
  lines naming skipped external paths, spaces unreachable via
  `## Spaces`, unreadable files, and external `check-size` targets.
  Stdout stays pure data.
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
  `unregistrable child name` (a name no contract entry can carry), and,
  under `--external`, `missing entry` drift for a mounted external space
  nobody registered (mount.md's by-hand step stands).
- The audit emits the same stderr walk advisories the data commands do
  (skipped external paths, unlistable directories) — silence never means
  "looked everywhere" on any command now.
- `## Items` documented in CONVENTIONS.md: human navigation, not
  contract; drift there surfaces through the broken-link scan.

### Changed

- Wikilink resolution is index-backed (path-suffix map) instead of
  sweeping every page per path-qualified link — the audit's one
  superlinear term is gone; at thousands of pages it now costs what
  `list` and `files` cost.
- `normalize_href` normalizes trivial equivalents (`./x`, `a//b`,
  `a/./b`, a trailing slash) instead of rejecting them, and a CommonMark
  link title on an entry is tolerated and ignored — both previously
  produced phantom findings that no edit could converge.
- Walk advisories name every skipped external path (`external, skipped
  (--external to include): shared/team/`) instead of a bare count — a
  user's own folder literally named `shared/` is never captured
  silently.
- `grep` marks matches inside external spaces with the same
  `[external]` marker `files` prints.
- `check-size` resolves a relative target from the wiki root, wherever
  the caller stands — it resolved from CWD, misjudging externality and
  reporting `target is external` before `no such file`.
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
- Near-miss heading detection generalized from the case-variant special
  case to the class: `## spaces`, `## Spaces ##`, and `##Spaces` all get
  the rename hint.
- Dot-prefixed names are reserved at any depth for files as well as
  folders — Obsidian cannot display dotfiles either.
- The audit header's `spaces:` counts the dirs that actually carry the
  contract — a bare `index.md` dir is a finding, not a space, and the
  header now speaks the spec's vocabulary.
- Every claim of the script's interpreter states the tested floor:
  stdlib python3 (3.9+), the oldest version CI runs — pinned by a test
  alongside the cap tables.
- `check-size --stdin` reports a planned write that shrinks an over-cap
  file as `ok … shrinking write is progress` (exit 0): the spec's
  shrinking-write allowance is now expressible by the tool.

### Fixed

- An entry listing a space through another space's boundary produced
  zero findings while traversal refused it — the audit exited 0 on a
  contract the walk would not follow. The `crossing` finding closes the
  class.
- `## Spaces` hrefs decode percent-encoding on read (Obsidian writes
  `my%20space/index.md`), every check runs on the decoded name, and
  suggested entries encode through the same round-trip — a folder name
  with a space registers dialect-valid instead of surfacing as
  stale-plus-missing.
- An entry rides any markdown bullet marker (`-`, `*`, `+`), matching the
  spec's "an entry is a markdown bullet" — a `*` or `+` entry no longer
  drops its space off the contract silently. Skills keep writing `-`; a
  non-entry bullet of any marker is flagged malformed.
- `init.md`'s adopt path ensures the adopted root itself carries
  `## Spaces` before the first audit — an existing heading-less
  `index.md` dead-ended setup with `not a wiki`.
- A registered mount that was not a valid wiki was invisible: silently
  dropped from traversal and absent from the default audit.
- A nested space's `_meta/limits.md` was ignored; the same file could be
  `over` from the root and `ok` from the space.
- Trust scope now holds at every git boundary: a nested checkout's
  foreign-origin submodule classified as owned, so `grep` read it
  without `--external`. A submodule is foreign when its URL differs
  from the origin of the repo that *declares* it, at any depth,
  whichever root resolved; quoted `.gitmodules` values are unwrapped.
- `## Spaces` inside YAML frontmatter counted as the contract for
  classification while the link scan ignored it; every reader of a
  document now starts after the frontmatter block.
- Dangling relative links escaping the root (`../gone.md`) passed the
  broken-link scan silently; all relative targets are now checked on
  disk. Wikilinks to assets (`[[report.pdf]]`) were false-positive broken
  and are exempt like embeds; indented code blocks are no longer scanned;
  ambiguous wikilinks credited only their first match as incoming.
- A case-mismatched wikilink (`[[readme]]` for `README.md`) was reported
  broken; wikilink lookups now casefold, the way the dialect resolves
  them. Relative markdown links keep filesystem semantics — the disk
  decides.
- A UTF-8 BOM at the top of a page hid a leading frontmatter block from
  every reader; decoding strips it (`utf-8-sig`) — byte counts and caps
  come from the filesystem and keep it.
- Deep nesting died with a raw `RecursionError` near 1000 levels; the
  walkers are iterative, so depth is bounded by the filesystem, not the
  interpreter.
- `check-size` on a target outside the wiki applied the host's
  `_meta/limits.md`; out-of-tree paths answer to the defaults alone.

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
