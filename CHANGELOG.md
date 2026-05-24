# Changelog

All notable changes to wiki-spaces are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-05-24

The "one unified system" release. Drops the spec-tier vocabulary, ships
Hermes-pattern size discipline, replaces the `--tier1` adoption flag with
a stronger `--adopt`, and folds in eight material defects from the v0.7.0
validation pass — including a critical producer→consumer break in
`space promote → space audit` that 274 prior tests missed.

This release was planned through nine fresh-thread codex review rounds
(`/home/anfreire/.claude/plans/plan-this-out-throughly-cozy-pixel.md`)
and executed in eight green-tree commits. 325 tests pass (was 274).

### Added

- **`wiki-spaces init <path> --adopt`** walks an existing folder, enumerates
  every nested space (a folder with `index.md`), and registers each in the
  appropriate ancestor's `## Spaces` section. Day-1 `wiki-spaces space audit`
  reports zero drift on an adopted tree. Externals (under `shared/`,
  foreign-origin submodules, escaping symlinks) are skipped with a per-skip
  stderr notice; pass `--include-external` to override.
- **`_meta/limits.md`** convention — per-file character caps with markdown-
  table override. Defaults: `index.md` 5,000; `log.md` 100,000 (auto-rotates);
  any other `*.md` 15,000. Matched via `fnmatch`; user patterns checked
  before defaults so a narrow `concepts/*.md` rule can't be silently shadowed.
  Documented in CONVENTIONS.md.
- **`wiki-spaces space log "<message>"`** appends a line to `<wiki>/log.md`
  atomically under `fcntl.flock`. Single-lock check-rotate-append: when the
  projected post-append size exceeds the cap, the oldest half of entries
  moves to `log.archive-<YYYYMMDD-HHMMSS>[-N].md` (unique on second-level
  collision; history never overwrites) before the append. Creates `log.md`
  on first use.
- **`wiki-spaces space manifest set <project> <key> <value> [--json]`** —
  atomic read-modify-write on `<wiki>/.manifest.json` under flock. `--json`
  parses the value as a JSON literal; light coercion otherwise (int for
  `pages_in_vault`, literal `"null"` for `last_commit_synced`).
- **`wiki-spaces space audit --include-external`** opts the read path into
  externally-classified spaces. Plumbed through both the drift walker AND
  the broken-link walker so the two checks always agree on scope.
- **Size violations in audit.** `space audit` scans every owned `.md` file
  against its cap. Over-cap pages flip the exit code (same policy as drift
  and broken wikilinks); pages at ≥80% are reported informationally as
  "approaching cap."
- **`_md.mask_code_spans_offset_preserving(text)`** — new helper. Same
  length as input; every character inside fenced/inline code becomes a
  space. Used by `space promote` for span-based link rewriting (the
  existing line-count-preserving `strip_code_spans` shifts character
  offsets and can't be used for span replacement).
- **`_md.strip_frontmatter(text)`** convenience over `split_frontmatter`.
- **CHANGELOG, README, AGENTS, CONVENTIONS, references, skills** all
  rewritten to drop tier vocabulary. The new spec floor is "a folder with
  `index.md`"; the navigation contract (`## Spaces`) is described
  operationally, not as a tier.

### Changed (breaking)

- **`wiki-spaces init --tier1` is removed.** Replaced by `--adopt`, which
  produces a wiki with zero day-1 audit drift instead of suppressing drift
  via missing `## Spaces`. Every wiki created by `init` now has `## Spaces`
  from t=0. Migration: replace `init --tier1` with `init --adopt` in
  scripts and hook snippets.
- **`wiki-spaces init` without `--description`** no longer writes the
  literal `<one paragraph describing this wiki>` placeholder. The
  `## What this space is` section is omitted entirely when no description
  is provided.
- **`space audit` summary line** changed from
  `spaces: N (X with ## Spaces, Y Tier 1)` to `spaces: N`. The tier
  breakdown was always derivable from the body and added zero signal.
- **All "Tier 1 parent" error messages** rewritten to "ancestor `<path>`
  has no `## Spaces` section." Same semantics, no tier word.

### Fixed (defects from the v0.7.0 validation pass)

- **#1 — critical: `space promote → space audit` producer/consumer break.**
  Promote emitted pathful wikilinks of the form `[[concepts/foo/index]]`;
  audit's `_md.resolve_wikilink` only handled base-relative and bare-
  filename forms, so every rewritten link was flagged broken. The unified
  resolver now tries wiki-root pathful first (matching promote's output),
  then base-relative, then bare filename. Eliminates the same class of
  bug that the v0.3.0 `## Items` regression introduced.
- **#2 — `space add` / `space remove` atomicity.** Both commands mutated
  the filesystem and the ancestor's `## Spaces` in two independent steps
  with no rollback. Now wrapped in a generic `_atomic_mutate_index`
  helper (flock + tempfile + os.replace) with wrapper-level snapshot
  rollback for filesystem side-effects. On `add` failure, the created
  directory and its index.md are removed (user content is preserved). On
  `remove` failure mid-rmtree, the directory contents and the index
  entry are restored from snapshot. In-process exceptions only; crashes
  are out of scope, with a manual-recovery message documented when
  cleanup itself fails.
- **#3 — `space promote` link rewrite scanned raw text.** Code-block
  examples (` ```\n[[foo]] is a wikilink\n``` `) and frontmatter wikilinks
  (`aliases: ["[[foo]]"]`) were rewritten alongside real references. Now
  uses the new offset-preserving mask so code spans and frontmatter are
  invisible to the scan.
- **#4 — `space promote` rollback left the git index dirty.** When promote
  ran `git mv`, the staging area carried a phantom rename after rollback.
  Now runs `git reset HEAD <source> <target>` on rollback (best-effort;
  documented failure mode).
- **#5 — `install --bridge` emitted snippets silently when `repo` was
  unset.** Bridge snippets reference the `repo` config key, so a snippet
  emitted before any plain `install` run lands in a broken state. Now
  prints a one-line stderr warning (stdout stays clean; the documented
  pipe contract still works).
- **#6 — `install` exited 0 on silent partial installs.** Refused-unowned
  destinations now set `had_fatal=True`; install returns 1 unless
  `--force` is passed.
- **#7 — `wiki-update`'s "check `index.md` for duplicates" was unreliable.**
  `## Items` is non-contractual, so `index.md` is not a complete index of
  what's on disk. Skill prose now points the producer at the
  `wiki-search` candidate pass instead.
- **#8 — `space audit` had no `--include-external` flag** despite the
  trust scope explicitly permitting opt-in. New flag plumbed through both
  walkers so drift and broken-link checks always agree on scope.
- **#9 — `wiki-search`'s conditional glob carve-out** re-opened the
  v0.3.0 producer/consumer fix. Reverted to unconditional glob backstop.

### Deprecated

- `--as` is a hidden alias for `--mode` in `space mount`. Removal target
  was v0.9.0; this release supersedes that — `--as` will be removed in
  **v1.1**.

### Documentation

- **`AGENTS.md`** — `## Tiers` (Tier 1 / 2 / 3) replaced by two sections:
  `## What a wiki has` (the spec floor: `index.md`) and
  `## The navigation contract` (`## Spaces` semantics, write-vs-read
  distinction).
- **`CONVENTIONS.md`** — "Knowledge-capture pack" renamed to
  "Memory-aid conventions" (same four members, no "pack" bundle
  vocabulary). New `## _meta/limits.md` section documenting size
  discipline.
- **`README.md`** — adds `--adopt` to the Manual install example with a
  paragraph explaining its behavior; mentions per-file size discipline.
- **`references/SETUP.md`** — Branch A rewritten to use `--adopt` instead
  of `--tier1`; describes the new presence-of-contract semantics.
- **`references/MOUNT.md`** / **`references/EXAMPLES.md`** — tier
  vocabulary scrubbed; refusal wording matches the unified message.
- **`skills/wiki-search` / `wiki-update` / `wiki-tend`** — log writes
  now route through `wiki-spaces space log`; `.manifest.json` writes
  through `space manifest set`. Size-discipline pre-write step added to
  `wiki-update`.

## [0.7.0] — 2026-05-24

### Added

- `wiki-spaces space mount --mode {submodule|clone|symlink}` is the canonical
  mechanism flag (replaces `--as`). `--mode` is required via a mutually
  exclusive argparse group; `--as` remains as a hidden, deprecated alias.
- `wiki-spaces space mount [path]` is now optional. When omitted, the
  destination is derived as `shared/<basename-of-source>/` — the `shared/`
  prefix opts the mount into external trust-scope semantics by convention.
  Basename derivation: drop `?query`/`#fragment` first, trim trailing slash,
  extract tail (scp-style aware), strip `.git` suffix only, reject empty or
  leading-`.` basenames.
- `wiki-spaces space mount --dry-run` prints the planned mount + `## Spaces`
  registration without mutating the filesystem.
- `wiki-spaces space mount --name NAME` overrides the registered
  `## Spaces` entry label (the mounted child's `index.md` is never written).

### Changed

- **Atomic mount registration.** `space mount` now wraps the parent-index
  read-modify-write in an advisory `fcntl.flock` on the ancestor directory
  and writes the new `## Spaces` entry via `tempfile.NamedTemporaryFile` +
  `os.replace`. If registration fails, the just-completed mount
  (symlink/clone/submodule) is rolled back per-mode. Concurrent CLI mounts
  serialize correctly under the directory lock.
- `wiki-spaces doctor` now requires `vendor/kepano/obsidian-bases/SKILL.md`
  alongside `obsidian-markdown` (AGENTS.md names both as syntax references).
- `references/MOUNT.md`, `references/SETUP.md`, and `README.md` updated to
  show `--mode` (the canonical flag) and the optional-`path` form. Manual
  branches in `MOUNT.md` now correctly call
  `wiki-spaces space add ... --force-external` for `shared/` paths.
- `references/SETUP.md` no longer points at a nonexistent README `## Start`
  heading; it references the existing "No tooling at all" subsection.
- `CONVENTIONS.md` adds cross-references from the opt-in bundle catalog and
  layout catalog to `references/SETUP.md` (the install-time priors source).

### Deprecated

- `--as` is a hidden, backwards-compatible alias for `--mode` in `space
  mount`. Removal target: **v0.9.0**. Update any saved snippets / hook
  scripts to `--mode` before that release.

## Earlier versions

Earlier history is tracked in git tags (`v0.1.0` … `v0.6.0`) and the
GitHub release notes. Notable structural changes prior to v0.7.0:

- **v0.6.0** — failed-mount cleanup; `init --tier1` for folder adoption.
- **v0.5.0** — broken-link / orphan audit; `space mount`; Antigravity harness.
- **v0.4.1** — search-backend recommendations narrowed to vetted options.
- **v0.4.0** — `## Spaces` contract enforced on add/remove + audit summary.
- **v0.3.0** — Move 3.1: cross-link scorer extracted into a tested
  `_links.py`; `## Items` demoted to tool-irrelevant; deterministic
  wikilink resolution; doctor exits non-zero on invalid config.
- **v0.2.0** — public PyPI release; agent-driven setup flow stabilized.
- **v0.1.0** — initial release of the spec + skills + CLI.
