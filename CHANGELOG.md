# Changelog

All notable changes to wiki-spaces are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[Semantic Versioning](https://semver.org/).

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
