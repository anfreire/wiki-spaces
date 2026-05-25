"""`wiki-spaces space` subcommands: add, remove, mount, promote, audit, list, files, log, check-size.

Maintains the `## Spaces` exhaustiveness contract automatically so users
never edit ancestor `index.md` files by hand to track child spaces.

Operations:
- `space add <rel-path>`     create a new space and register it in the
                             nearest ancestor's `## Spaces`. Auto-inserts
                             `## Spaces` into any bare-`index.md` ancestor
                             it encounters as the first mutation step
                             (chain helper, atomic under `flock`).
- `space remove <rel-path>`  remove a registered space and its directory.
                             Refuses without `--force` when the space
                             contains content beyond `index.md`. Inserts
                             `## Spaces` into the ancestor if missing
                             before removing the entry, so the contract
                             stays consistent.
- `space mount <src> [path]` mount an external space — git clone, git
                             submodule, or symlink (`--mode`) — verify it has
                             `index.md`, and register it in the nearest
                             ancestor's `## Spaces`. Same chain-helper
                             auto-insert as `space add`. `path` is optional;
                             defaults to `shared/<basename-of-source>/`. Use
                             `--dry-run` to preview; `--name` to override the
                             registered label.
- `space promote <path>`     turn `foo.md` into `foo/index.md` (a child
                             space), rewriting links across the wiki and
                             registering the new space in the ancestor's
                             `## Spaces` atomically under flock.
- `space audit`              walk owned spaces; report `## Spaces` drift,
                             broken `[[wikilinks]]`, size violations, and
                             orphan pages. Read-only — uses the strict
                             resolver and refuses on a bare-`index.md`
                             wiki (no `## Spaces`).

Trust scope: writes stay inside the wiki tree. External spaces (per the
heuristic in CONVENTIONS.md / Owned vs external) are skipped on traversal.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import _md
from ._common import (
    _has_spaces_section,
    nearest_space_root_for_repair,
    nearest_space_root_strict,
    wiki_path,
)


# ---------- Helpers ----------

def _resolve_wiki_strict(explicit: Path | None = None) -> Path | None:
    """Resolve the wiki root for a READ-ONLY operation.

    A wiki is a folder with `index.md` containing a `## Spaces` heading
    (the v1 navigation contract). When the resolved candidate has
    `index.md` but no `## Spaces`, this returns None — the caller refuses
    to operate. Repair-capable callers (`space add`, `space remove`,
    `space mount`, `space promote`, `audit --fix`, `init --adopt`) use
    `_resolve_wiki_for_repair` instead and let the chain helper insert
    `## Spaces` atomically as the first mutation step.
    """
    if explicit:
        p = explicit.expanduser().resolve()
        if (p / "index.md").is_file() and _has_spaces_section(p):
            return p
        return None
    cfg_wiki = wiki_path()
    if cfg_wiki is not None:
        # The user explicitly configured this wiki. If it carries `index.md`
        # but lacks `## Spaces`, refuse — don't silently fall through to a
        # CWD ancestor wiki (that would mask a real config-side spec
        # violation). The fallback only applies when no config wiki was set
        # at all. Same hard-stop for a non-absolute config path: doctor
        # rejects relatives, so `.resolve()` here would silently join to
        # CWD and pick a different wiki per-invocation.
        cfg_expanded = cfg_wiki.expanduser()
        if not cfg_expanded.is_absolute():
            return None
        p = cfg_expanded.resolve()
        if (p / "index.md").is_file() and _has_spaces_section(p):
            return p
        return None
    return nearest_space_root_strict()


def _resolve_wiki_for_repair(explicit: Path | None = None) -> Path | None:
    """Resolve the wiki root for a WRITE operation that may repair `## Spaces`.

    Accepts a bare `index.md` (no `## Spaces`) — the caller's chain helper
    inserts the section atomically as the first mutation step. The
    explicit / config / CWD fallback order matches the strict resolver;
    only the `## Spaces` requirement differs. When the config points at a
    path that does not exist or lacks `index.md`, refuse rather than fall
    through to CWD (mirrors strict's "don't mask a config-side violation"
    behavior — a write command must never silently redirect to a different
    wiki than the user configured).
    """
    if explicit:
        p = explicit.expanduser().resolve()
        return p if (p / "index.md").is_file() else None
    cfg_wiki = wiki_path()
    if cfg_wiki is not None:
        # Same absolute-path requirement as the strict resolver: a relative
        # config path would silently join to CWD via `.resolve()`, picking
        # a different wiki depending on where the agent runs.
        cfg_expanded = cfg_wiki.expanduser()
        if not cfg_expanded.is_absolute():
            return None
        p = cfg_expanded.resolve()
        if (p / "index.md").is_file():
            return p
        return None
    return nearest_space_root_for_repair()


def _validate_entry_text(value: str | None, *, field: str) -> tuple[bool, str | None]:
    """Validate user-supplied text destined for a markdown `## Spaces` entry.

    `## Spaces` entries are markdown links: `- [LABEL](HREF) — DESCRIPTION`.
    A `]` in LABEL closes the label brace; a `)` in HREF closes the href;
    a newline ends the entry. Either would split the produced line and
    leave a `## Spaces` entry the consumer parser (`_md.ENTRY_RE`) can't
    read. Refuse the value upfront so the producer can never emit an
    entry the consumer ignores.
    """
    if value is None or value == "":
        return True, None
    # Newlines / control chars split the entry across markdown lines.
    if any(ord(c) < 0x20 or c == "\x7f" for c in value):
        return False, (
            f"{field} may not contain newline / control characters — the "
            "resulting `## Spaces` entry would be split across lines and "
            "unparseable by the consumer walker"
        )
    # `]` closes a label brace; `)` closes an href; either breaks the
    # markdown link syntax.
    if "]" in value or ")" in value:
        return False, (
            f"{field} may not contain `]` or `)` — the resulting "
            "`## Spaces` entry would be unparseable by the consumer walker"
        )
    return True, None


def _validate_rel_path(rel: str) -> tuple[bool, str | None]:
    """Validate a user-provided relative path. (ok, error_message).

    Rejects:
    - empty, `.`, `..`, and `.git` segments
    - any hidden-directory segment (`.X`): the consumer walker skips
      hidden directories per CONVENTIONS / Reserved top-level folder
      names; the producer must not register spaces the consumer ignores.
    - `_archives` and `_meta` segments: reserved by convention
      (`_archives` is excluded from audit/`wiki-tend` walks, `_meta`
      holds config files like `_meta/limits.md`). Registering a space
      under either creates a `## Spaces` entry no consumer reads.
    - Markdown link metacharacters (`[`, `]`, `(`, `)`, `{`, `}`) in
      any segment: these would otherwise land in `## Spaces` entries
      as raw bytes inside the link syntax — `- [foo)bar/](foo)bar/index.md)`
      — which the parser's `ENTRY_RE` cannot read (regex stops at the
      first `)` or `]`).

    `shared/` is NOT refused at validation — it has dedicated handling
    via `--force-external` in `cmd_add` and is the default mount
    destination per CONVENTIONS / Reserved top-level folder names.
    """
    rel = rel.strip().rstrip("/")
    if not rel:
        return False, "empty path"
    p = Path(rel)
    if p.is_absolute():
        return False, "must be relative to the wiki root"
    for part in p.parts:
        if part in ("", ".", ".."):
            return False, "path may not contain '.', '..', or empty segments"
        if part == ".git":
            return False, "path may not contain '.git' segments"
        if part.startswith("."):
            return False, (
                f"path may not contain hidden segments ({part!r}); hidden "
                "directories are skipped by every consumer walker per "
                "CONVENTIONS / Reserved top-level folder names"
            )
        if part in ("_archives", "_meta"):
            return False, (
                f"path segment {part!r} is reserved by convention — "
                f"{part}/ is excluded from consumer walks "
                "(see CONVENTIONS / Reserved top-level folder names)"
            )
        if any(c in part for c in "[](){}"):
            return False, (
                "path may not contain Markdown link metacharacters "
                "(`[`, `]`, `(`, `)`, `{`, `}`) — the resulting `## Spaces` "
                "entry would be unparseable by the consumer walker"
            )
        # Newlines and other control characters would split the `## Spaces`
        # entry across multiple markdown lines, making it unparseable.
        if any(ord(c) < 0x20 or c == "\x7f" for c in part):
            return False, (
                "path may not contain newline / control characters — the "
                "resulting `## Spaces` entry would be split across lines "
                "and unparseable by the consumer walker"
            )
    return True, None


def _derive_default_path(source: str) -> tuple[str | None, str | None]:
    """Derive the default mount path `shared/<basename>/` from `source`.

    Returns `(rel_path, None)` on success, `(None, error_message)` otherwise.

    Ordering matters (regression-tested):
    1. Drop `?query` and `#fragment` first — URLs like `repo.git?ref=main`
       would otherwise mis-strip `.git` against the suffix `.git?ref=main`.
    2. Trim trailing `/`.
    3. Extract tail (last `/`-separated segment, or for scp-style
       `git@host:org/repo` the slashed tail after `:`).
    4. Strip a `.git` SUFFIX only — applies when tail ends with `.git`
       (`repo.git` → `repo`), not when tail *is* `.git` (which is rejected
       next as starting with `.`).
    5. Reject if empty OR starts with `.`.

    The derived path is always `shared/<basename>/` — the `shared/` prefix
    is what opts the mount into external trust-scope semantics per
    CONVENTIONS.md / Owned vs external.
    """
    if not source or not source.strip():
        return None, "source is empty"
    s = source.strip()
    # 1. Drop query/fragment.
    for marker in ("?", "#"):
        idx = s.find(marker)
        if idx >= 0:
            s = s[:idx]
    # 2. Trim trailing slashes.
    s = s.rstrip("/")
    if not s:
        return None, (
            f"cannot derive default path from {source!r}: empty after trimming. "
            "Pass an explicit `path` argument."
        )
    # 3. Extract tail. Handle scp-style first (`user@host:path`) so the
    # path-after-colon is what we tail-split.
    if ":" in s and not s.startswith(("http://", "https://", "ssh://", "git://", "file://")):
        # scp-style or local path with a colon in the prefix; take the part
        # after the LAST colon (then split by `/`).
        s = s.rsplit(":", 1)[-1]
    tail = s.rsplit("/", 1)[-1]
    # 4. Strip `.git` SUFFIX (not when tail IS `.git`).
    if tail.endswith(".git") and tail != ".git":
        tail = tail[: -len(".git")]
    # 5. Reject empty / hidden.
    if not tail:
        return None, (
            f"cannot derive default path from {source!r}: empty basename. "
            "Pass an explicit `path` argument."
        )
    if tail.startswith("."):
        return None, (
            f"cannot derive default path from {source!r}: basename {tail!r} "
            "starts with '.'. Pass an explicit `path` argument."
        )
    return f"shared/{tail}", None


def _resolve_git_config(wiki_root: Path) -> Path | None:
    """Locate the git config file for `wiki_root`.

    Two layouts to handle per CONVENTIONS.md / `.git`:
    - `<wiki>/.git/` is a directory (regular repo) → `<wiki>/.git/config`.
    - `<wiki>/.git` is a FILE (git worktree or submodule). The file's body
      is `gitdir: <abs-or-rel-path>` pointing at the real git dir. For
      worktrees that dir has a `commondir` file pointing at the shared
      repo, whose `config` is the authoritative origin source.

    Returns the config path when it exists on disk, else None. Pure FS
    parsing — no subprocess, matching the stdlib-only stance of this module.
    """
    git_entry = wiki_root / ".git"
    if git_entry.is_dir():
        config = git_entry / "config"
        return config if config.is_file() else None
    if not git_entry.is_file():
        return None
    try:
        body = git_entry.read_text(encoding="utf-8")
    except OSError:
        return None
    gitdir: Path | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("gitdir:"):
            target = stripped[len("gitdir:"):].strip()
            if not target:
                return None
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (git_entry.parent / target_path).resolve()
            gitdir = target_path
            break
    if gitdir is None or not gitdir.is_dir():
        return None
    # Worktrees keep per-worktree state under gitdir but share config via
    # commondir. Submodules embed config directly under gitdir.
    commondir_file = gitdir / "commondir"
    if commondir_file.is_file():
        try:
            common = commondir_file.read_text(encoding="utf-8").strip()
        except OSError:
            common = ""
        if common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = (gitdir / common_path).resolve()
            shared_config = common_path / "config"
            if shared_config.is_file():
                return shared_config
    config = gitdir / "config"
    return config if config.is_file() else None


def _wiki_origin_url(wiki_root: Path) -> str | None:
    """Return the wiki's origin remote URL from its git config, or None.

    Resolves `.git/` (directory) and `.git` (file — worktree or submodule)
    via `_resolve_git_config`. Best-effort regex parse — no subprocess.
    Handles the common case of a `[remote "origin"]` section with `url = …`.
    """
    config = _resolve_git_config(wiki_root)
    if config is None:
        return None
    import re
    text = config.read_text(encoding="utf-8")
    m = re.search(
        r'\[remote\s+"origin"\][^\[]*?url\s*=\s*(\S+)',
        text,
        re.DOTALL,
    )
    return m.group(1).strip() if m else None


def _is_foreign_submodule(path: Path, wiki_root: Path) -> bool:
    """True when path is registered as a git submodule with a foreign origin.

    Reads `<wiki>/.gitmodules` and compares each submodule's `url =` to the
    wiki's own origin (resolved via `_wiki_origin_url`, which handles `.git/`
    directories as well as `.git` files in submodules and worktrees). When
    either is unreadable, returns False (callers fall back to the other
    heuristics).
    """
    gitmodules = wiki_root / ".gitmodules"
    if not gitmodules.is_file():
        return False
    try:
        rel = path.resolve().relative_to(wiki_root).as_posix()
    except (ValueError, OSError):
        return False
    import re
    text = gitmodules.read_text(encoding="utf-8")
    sections = re.split(r"(?=^\[submodule )", text, flags=re.MULTILINE)
    for section in sections:
        m_path = re.search(r"^\s*path\s*=\s*(.+)$", section, re.MULTILINE)
        if not m_path or m_path.group(1).strip() != rel:
            continue
        m_url = re.search(r"^\s*url\s*=\s*(\S+)", section, re.MULTILINE)
        if not m_url:
            return False
        sub_url = m_url.group(1).strip()
        wiki_url = _wiki_origin_url(wiki_root)
        if wiki_url is None:
            # We can't tell whether it's foreign without our own origin;
            # default to "foreign" to be safe (write protection > recall).
            return True
        return sub_url != wiki_url
    return False


def _is_external(path: Path, wiki_root: Path) -> bool:
    """External-space heuristic per CONVENTIONS.md / Owned vs external.

    Catches: under `<wiki>/shared/`, foreign-origin git submodules (parsed
    from `.gitmodules` vs the wiki's `.git/config` origin), or symlinks
    whose realpath leaves the wiki tree.

    The `shared/` test uses the lexical (unresolved) path, so a symlink placed
    at `<wiki>/shared/...` is external regardless of where it resolves to.
    A symlink whose realpath leaves the tree is caught separately below.
    """
    try:
        rel = path.relative_to(wiki_root)
    except ValueError:
        return True
    if rel.parts and rel.parts[0] == "shared":
        return True
    if _is_foreign_submodule(path, wiki_root):
        return True
    if path.is_symlink():
        target = path.resolve()
        try:
            target.relative_to(wiki_root)
        except ValueError:
            return True
    return False


def _is_in_external_scope(path: Path, wiki_root: Path) -> tuple[bool, str | None]:
    """True when `path` or any ancestor (up to but not including wiki_root) is external.

    Closes a gap in `_is_external`, which only inspects the exact path: a
    descendant of a foreign-submodule mount or escaping symlink would slip
    through it. Returns `(True, reason)` when the path is external,
    `(False, None)` otherwise. `reason` names the offending node for the
    user-facing error message.
    """
    try:
        path.relative_to(wiki_root)
    except ValueError:
        return True, "path is outside the wiki tree"
    p = path
    while True:
        if p == wiki_root:
            return False, None
        if _is_external(p, wiki_root):
            rel = p.relative_to(wiki_root).as_posix()
            return True, (
                f"{rel} is external (per CONVENTIONS / Owned vs external "
                "— under shared/, a foreign-origin submodule, or a symlink "
                "that escapes the wiki tree)"
            )
        p = p.parent


def _nearest_ancestor_space(wiki_root: Path, target: Path) -> Path:
    """Walk up from target.parent until a folder with index.md is found.

    Always terminates at wiki_root (which by definition has index.md).
    """
    p = target.parent
    while True:
        if (p / "index.md").is_file():
            return p
        if p == wiki_root:
            return wiki_root
        p = p.parent


def _walk_classified(wiki_root: Path, *, include_external: bool = False):
    """Yield every space under wiki_root, classified as owned or external.

    Yields `(path, classification, reason)` where:
    - `classification` is `"owned"` or `"external"`.
    - `reason` is None for owned, or a short string for external.

    External classification inherits down: once we descend into an external
    boundary under `include_external=True`, every yielded descendant is also
    classified `"external"`. The bare `_is_external` heuristic only catches
    the boundary itself (the symlink, the foreign submodule, the `shared/`
    path); without the inheritance, a child folder under `shared/team/` that
    isn't itself a symlink or submodule would be misclassified as owned.

    The wiki root itself is always yielded first as `"owned"`. Externals are
    yielded with their reason so callers (e.g., `init --adopt`) can emit
    per-skip notices; callers that only want owned spaces filter the stream.

    Tracks resolved realpaths to break symlink cycles; broken symlinks and
    unreadable directories are skipped silently. Hidden directories (names
    starting with `.`) and `_archives/` are pruned and never yielded.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return
    visited: set[Path] = {root_real}
    yield (wiki_root, "owned", None)
    # Stack of `(path, parent_external)` — `parent_external` carries the
    # in-external-subtree flag down to every descendant we yield.
    stack: list[tuple[Path, bool]] = [(wiki_root, False)]
    while stack:
        current, parent_external = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for child in entries:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name == "_archives":  # retired content — out of audit scope
                continue
            # `_meta/` holds config files (`limits.md`, `taxonomy.md`); no
            # space lives there. Skip so audit/adopt repair passes never
            # register `_meta/...` descendants as spaces — the contract
            # walker prunes those entries (CONVENTIONS / Reserved top-
            # level folder names), so registering would create a
            # producer/consumer break where audit --fix writes an entry
            # `space list` / `space files` can't read.
            if child.name == "_meta":
                continue
            try:
                child_real = child.resolve()
            except OSError:
                continue
            if child_real in visited:
                continue
            visited.add(child_real)
            # Once we're inside an external subtree, every descendant inherits
            # the external classification — only the boundary needs a fresh
            # `_is_external` check.
            child_external = parent_external or _is_external(child, wiki_root)
            if child_external:
                # Surface every external boundary (with or without index.md)
                # so callers like `init --adopt` can emit per-skip notices
                # for the user-visible directory, not the deeper space.
                # With `include_external`, we also descend into the subtree;
                # descendants carry `child_external=True`.
                if parent_external:
                    # Already inside an external subtree — the reason was
                    # surfaced at the boundary; descendants reuse it.
                    reason = "inside an external subtree"
                else:
                    reason = _external_reason(child, wiki_root)
                yield (child, "external", reason)
                if include_external:
                    stack.append((child, True))
                continue
            if (child / "index.md").is_file():
                yield (child, "owned", None)
            stack.append((child, False))


def _external_reason(path: Path, wiki_root: Path) -> str:
    """Short reason string for why `path` is classified external."""
    try:
        rel = path.relative_to(wiki_root)
    except ValueError:
        return "outside the wiki tree"
    if rel.parts and rel.parts[0] == "shared":
        return "under shared/"
    if _is_foreign_submodule(path, wiki_root):
        return "foreign-origin git submodule"
    if path.is_symlink():
        return "symlink escapes the wiki tree"
    return "external (per owned/external heuristic)"


def _walk_owned_spaces(wiki_root: Path, *, include_external: bool = False):
    """Yield every owned space under wiki_root (inclusive).

    Thin filter over `_walk_classified`. With `include_external=True`,
    externally-classified spaces are also yielded (used by `audit
    --include-external`).

    `_walk_classified` surfaces external boundaries (including plain folders
    without `index.md`) so adoption can emit per-skip notices; this filter
    drops anything that isn't an actual space (no `index.md`) before
    yielding, because audit / owned-space callers expect every yielded path
    to be readable as a space.
    """
    for path, classification, _reason in _walk_classified(
        wiki_root, include_external=include_external
    ):
        if classification != "owned" and not include_external:
            continue
        if path == wiki_root or (path / "index.md").is_file():
            yield path


def _walk_via_spaces_contract(
    wiki_root: Path, *, include_external: bool = False,
):
    """Yield every space reachable via `## Spaces` entries — contract-first.

    Yields `(space, is_external)` tuples. The contract walker is the
    consumer-side traversal: it discovers spaces by parsing `## Spaces`
    entries in `index.md`, not by walking the filesystem. An on-disk space
    that isn't registered in its ancestor's `## Spaces` is INVISIBLE to
    this walker (the audit walker `_walk_classified` surfaces such drift).

    Malformed entries are silently skipped here; `_audit_malformed_entries`
    reports them on the audit side. External classification inherits down
    the tree — once we descend into an external child, every yielded
    descendant carries `is_external=True`.

    Owned children are required to resolve under `wiki_root` (escaping
    paths are skipped); external children may legitimately resolve outside
    (that's the whole point of `shared/` symlinks).

    Per the v1 contract, a child is a space only when its `index.md` is
    present AND carries a `## Spaces` heading. A registered child whose
    `index.md` lacks `## Spaces` is drift (audit reports it via the bare-
    section pass) and is NOT yielded — consumer-visible spaces always
    satisfy the navigation contract.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return
    visited: set[Path] = {root_real}
    yield (wiki_root, False)
    # Stack carries (path, is_in_external_scope) so descendants inherit.
    stack: list[tuple[Path, bool]] = [(wiki_root, False)]
    while stack:
        current, parent_external = stack.pop()
        try:
            text = (current / "index.md").read_text(encoding="utf-8")
        except OSError:
            continue
        for entry in _md.parse_section_entries(text, "Spaces"):
            if not entry.href:
                continue
            href_norm = _spaces_href_to_dir(entry.href)
            if not href_norm:
                continue
            href_path = Path(href_norm)
            if href_path.is_absolute() or ".." in href_path.parts:
                continue
            # Reserved-folder pruning per CONVENTIONS / Reserved top-level
            # folder names: hidden segments, `_archives`, and `_meta` are
            # always skipped by the consumer walker. Belt-and-suspenders
            # with the producer-side `_validate_rel_path` refusal — covers
            # pre-v1 wikis that may carry `## Spaces` entries for such
            # paths registered before the producer-side check existed.
            if any(
                part.startswith(".") or part in ("_archives", "_meta")
                for part in href_path.parts
            ):
                continue
            child = current / href_norm
            try:
                child_real = child.resolve()
            except OSError:
                continue
            # Classify via `_is_in_external_scope` rather than `_is_external`
            # so descendants of an external mount whose boundary isn't itself
            # the listed entry (e.g. `projects/vendor/docs/index.md` listed
            # directly when `.gitmodules path = projects/vendor`) inherit the
            # external classification. `_is_external` only checks the exact
            # path; the walker would otherwise treat them as owned.
            descendant_external, _why = _is_in_external_scope(
                child, wiki_root
            )
            child_external = parent_external or descendant_external
            # Resolution-escape is itself an external signal: a regular
            # folder under an escaping symlink whose boundary isn't itself
            # listed in `## Spaces` still resolves outside the wiki tree.
            # Reclassify rather than dropping — that keeps `init --adopt
            # --include-external` and the contract walker agreeing on what's
            # consumer-visible.
            if not child_external:
                try:
                    child_real.relative_to(root_real)
                except ValueError:
                    child_external = True
            if child_external and not include_external:
                continue
            if child_real in visited:
                continue
            child_index = child / "index.md"
            if not child_index.is_file():
                continue
            # v1 contract: a space requires `index.md` AND `## Spaces`.
            # A registered child whose `index.md` lacks the heading is
            # drift; audit's bare-section pass reports it. Consumer
            # traversal must not yield it (otherwise `space list` /
            # `space files` would expose pre-v1 layouts as if valid).
            try:
                child_text = child_index.read_text(encoding="utf-8")
            except OSError:
                continue
            if not _md.has_section(child_text, "Spaces"):
                continue
            visited.add(child_real)
            yield (child, child_external)
            stack.append((child, child_external))


def _walk_md_files_via_contract(
    wiki_root: Path, *, include_external: bool = False,
):
    """Yield `(page_path, is_external)` for every `.md` file reachable via
    the navigation contract.

    Walks the contract-discovered spaces (via `_walk_via_spaces_contract`).
    Within each visited space, descends into PLAIN folders (no `index.md`)
    but stops at child-space boundaries — those are owned by the contract
    walker. Escaping symlinks inside plain folders are treated as external
    when `include_external=True` and skipped otherwise.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return
    for space, space_external in _walk_via_spaces_contract(
        wiki_root, include_external=include_external
    ):
        stack: list[tuple[Path, bool]] = [(space, space_external)]
        seen: set[Path] = set()
        while stack:
            d, d_external = stack.pop()
            try:
                entries = sorted(d.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_file() and entry.suffix == ".md":
                    # Symlinked `.md` files need the same escape check as
                    # symlinked directories: a `foo.md` symlink whose target
                    # resolves outside the wiki tree is external content
                    # masquerading as owned. Skip it unless `include_external`
                    # opts the consumer in, in which case mark it external.
                    file_external = d_external
                    if entry.is_symlink():
                        try:
                            target_real = entry.resolve()
                            target_real.relative_to(root_real)
                        except (OSError, ValueError):
                            if not include_external:
                                continue
                            file_external = True
                    yield (entry, file_external)
                    continue
                if not entry.is_dir():
                    continue
                if entry.name.startswith(".") or entry.name in (
                    "_archives", "_meta",
                ):
                    continue
                # Stop at child-space boundaries — the contract walker owns them.
                if (entry / "index.md").is_file():
                    continue
                # Walk up to catch descendants of external boundaries
                # whose own path isn't itself external (foreign submodule
                # nested folders, etc.). `_is_in_external_scope` is the
                # ancestry-aware check; `_is_external` is exact-path only.
                ancestor_external, _why = _is_in_external_scope(
                    entry, wiki_root
                )
                entry_external = d_external or ancestor_external
                if entry.is_symlink():
                    try:
                        target_real = entry.resolve()
                        target_real.relative_to(root_real)
                    except (OSError, ValueError):
                        entry_external = True
                if entry_external and not include_external:
                    continue
                try:
                    er = entry.resolve()
                except OSError:
                    continue
                if er in seen:
                    continue
                seen.add(er)
                stack.append((entry, entry_external))


def _new_index_md(name: str, description: str | None = None) -> str:
    """index.md body for a freshly created space.

    Always emits title + `## Spaces` (the navigation contract — present from
    t=0 on every CLI-created space, so `space add foo/bar` works immediately
    on a fresh `foo`). When `description` is provided, also emits
    `## What this space is` with the description. Omitting `description`
    skips that section entirely rather than writing a placeholder string.
    Mirrors `init_wiki.build_index_md`.
    """
    if description and description.strip():
        return (
            f"# {name}\n\n## What this space is\n\n{description.strip()}\n\n## Spaces\n\n"
        )
    return f"# {name}\n\n## Spaces\n\n"


def _spaces_href_to_dir(href: str) -> str:
    """Normalize a `## Spaces` entry href to its child-space directory.

    `## Spaces` entries may be written `foo`, `foo/`, or `foo/index.md` (all
    accepted by `_md`); each identifies the same child space. Audit compares
    on this normalized form so a bare-folder href is not mistaken for drift.
    """
    h = href.strip()
    if h.endswith("/index.md"):
        h = h[: -len("/index.md")]
    return h.rstrip("/")


def _add_space_entry(text: str, label: str, href: str, description: str | None):
    """Add a `## Spaces` entry, treating directory-equivalent hrefs as duplicates.

    Idempotent: when an entry already exists pointing at the same directory
    (regardless of `foo`/`foo/`/`foo/index.md` form), returns the text
    unchanged. Without this normalization, `space add foo` against a wiki
    that already lists `- [foo/](foo/)` would append a duplicate.
    """
    target_dir = _spaces_href_to_dir(href)
    for e in _md.parse_section_entries(text, "Spaces"):
        if e.href and _spaces_href_to_dir(e.href) == target_dir:
            return text
    return _md.add_entry(text, "Spaces", label, href, description)


def _remove_space_entry(text: str, href: str) -> str:
    """Remove a `## Spaces` entry by normalized directory match.

    Removes whichever href form happens to be in the file (`foo`/`foo/`/
    `foo/index.md`). Removes ALL equivalent duplicates in one pass, so a
    pre-corrupted wiki with multiple entries for the same directory gets
    fully cleaned up in a single `space remove` call.
    """
    target_dir = _spaces_href_to_dir(href)
    result = text
    while True:
        matched_href = None
        for e in _md.parse_section_entries(result, "Spaces"):
            if e.href and _spaces_href_to_dir(e.href) == target_dir:
                matched_href = e.href
                break
        if matched_href is None:
            return result
        new = _md.remove_entry(result, "Spaces", matched_href)
        if new == result:
            return result
        result = new


# ---------- Subcommands ----------

def cmd_add(args: argparse.Namespace) -> int:
    wiki_root = _resolve_wiki_for_repair(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in "
            "~/.config/wiki-spaces/config.",
            file=sys.stderr,
        )
        return 2
    ok, err = _validate_rel_path(args.path)
    if not ok:
        print(f"  ! invalid path: {err}", file=sys.stderr)
        return 2

    rel = args.path.strip().rstrip("/")
    new_space = wiki_root / rel

    is_external, reason = _is_in_external_scope(new_space, wiki_root)
    if is_external and not args.force_external:
        print(
            f"  ! refusing to operate on external scope: {reason}. "
            "Pass --force-external to override.",
            file=sys.stderr,
        )
        return 2

    if getattr(args, "dry_run", False):
        already_space = (new_space / "index.md").is_file()
        if already_space and not args.force_index:
            print(f"  . (dry-run) {rel}/ already a space; would ensure ancestor entry")
        else:
            print(f"  . (dry-run) would create {rel}/index.md")
        print(
            f"  . (dry-run) would auto-insert `## Spaces` into ancestors "
            f"as needed and register {rel}/ in the nearest ancestor."
        )
        return 0

    # PR-L: size-cap check BEFORE mkdir, so an over-cap description aborts
    # cleanly without leaving an empty directory + a stranded `index.md`.
    already_space = (new_space / "index.md").is_file()
    if not already_space or args.force_index:
        display_name = args.name or new_space.name
        description_for_body = (args.description or "").strip() or None
        projected_text = _new_index_md(display_name, description_for_body)
        try:
            _enforce_size_cap(new_space / "index.md", projected_text, wiki_root)
        except SizeCapExceeded as e:
            print(f"  ! size cap: {e}", file=sys.stderr)
            return 2
    created_dir_this_call = False
    created_index_this_call = False
    # Track every directory THIS call creates (the leaf and any intermediate
    # parents materialized by `mkdir(parents=True)`), deepest-first, so
    # rollback can undo `space add a/b/c/d` against an empty wiki cleanly.
    created_dirs_this_call: list[Path] = []
    if already_space and not args.force_index:
        print(f"  . {rel}/ already a space; ensuring ancestor entry")
        # The pre-existing target's own index might lack `## Spaces` (a wiki
        # adopted from a folder of notes before v1). Repair it before we
        # register it upward — otherwise a re-registered existing space
        # would otherwise stay without `## Spaces`.
        try:
            _ensure_section_at(new_space, wiki_root)
        except RuntimeError as e:
            print(f"  ! {e}", file=sys.stderr)
            return 1
    else:
        # Track exactly what we create so rollback only undoes our own work,
        # never the user's pre-existing content.
        created_dir_this_call = not new_space.exists()
        if created_dir_this_call:
            probe = new_space
            while not probe.exists() and probe != wiki_root:
                created_dirs_this_call.append(probe)
                probe = probe.parent
        new_space.mkdir(parents=True, exist_ok=True)
        display_name = args.name or new_space.name
        description_for_body = (args.description or "").strip() or None
        new_index = new_space / "index.md"
        created_index_this_call = not new_index.exists()
        new_index.write_text(
            _new_index_md(display_name, description_for_body),
            encoding="utf-8",
        )
        print(f"  + {rel}/index.md")

    # Register the new space in each ancestor's `## Spaces`, walking up to
    # the wiki root. The chain helper inserts `## Spaces` into any
    # bare-`index.md` ancestor it encounters as the first mutation step.
    # `cmd_add --description` writes to the child's `## What this space is`,
    # NOT the parent's entry; the parent's entry uses the derived label and
    # a None description.
    try:
        notices, _added = _ensure_spaces_chain_and_register(wiki_root, new_space)
        for n in notices:
            print(n)
    except EnsureChainError as e:
        for n in e.notices:
            print(n)
        _rollback_added_entries(e.added)
        # Roll back our own FS creations (only what we made in THIS call).
        if created_index_this_call:
            try:
                (new_space / "index.md").unlink()
            except OSError:
                pass
        if created_dir_this_call:
            for d in created_dirs_this_call:
                try:
                    if d.exists() and not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass
        print(f"  ! {e}", file=sys.stderr)
        return 1
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    wiki_root = _resolve_wiki_for_repair(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2
    ok, err = _validate_rel_path(args.path)
    if not ok:
        print(f"  ! invalid path: {err}", file=sys.stderr)
        return 2

    rel = args.path.strip().rstrip("/")
    target = wiki_root / rel
    if not (target / "index.md").is_file():
        print(f"  ! {rel}/ is not a space", file=sys.stderr)
        return 2
    if target == wiki_root:
        print("  ! refusing to remove the wiki root", file=sys.stderr)
        return 2

    is_external, reason = _is_in_external_scope(target, wiki_root)
    if is_external and not args.force_external:
        print(
            f"  ! refusing to operate on external scope: {reason}. "
            "Pass --force-external to override.",
            file=sys.stderr,
        )
        return 2

    ancestor = _nearest_ancestor_space(wiki_root, target)
    ancestor_index = ancestor / "index.md"
    rel_from_ancestor = target.relative_to(ancestor)
    href = f"{rel_from_ancestor}/index.md"
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"

    contents = [
        p for p in target.iterdir()
        if not (p.name == "index.md" and p.is_file())
    ]
    if contents and not args.force:
        print(
            f"  ! {rel}/ contains {len(contents)} item(s) beyond index.md; "
            "pass --force to remove anyway",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        # Preview without mutation. Read the ancestor here only for the
        # preview — we do NOT call _ensure_section_at on a dry-run.
        text = ancestor_index.read_text(encoding="utf-8")
        if _md.has_section(text, "Spaces") and _remove_space_entry(text, href) != text:
            print(f"  ~ (dry-run) {printable}index.md ## Spaces  -= [{rel_from_ancestor}/]")
        elif not _md.has_section(text, "Spaces"):
            print(f"  ~ (dry-run) would insert `## Spaces` into {printable}index.md")
        print(f"  . (dry-run) would remove {rel}/")
        return 0

    # Ensure the ancestor's `## Spaces` exists so the entry-removal step has
    # a section to operate on. Placed AFTER all refusal checks and dry-run
    # so a refused or previewed call doesn't mutate anything.
    try:
        _ensure_section_at(ancestor, wiki_root)
    except RuntimeError as e:
        print(f"  ! {e}", file=sys.stderr)
        return 1

    # Snapshot the target directory's contents to a system tempdir before
    # any mutation. Rollback restores byte-for-byte if rmtree fails.
    snapshot_dir = Path(tempfile.mkdtemp(prefix="wiki-spaces-remove-"))
    snapshot_ok = False
    try:
        try:
            shutil.copytree(target, snapshot_dir / "target", symlinks=False)
            snapshot_ok = True
        except (OSError, shutil.Error) as e:
            print(
                f"  ! could not snapshot {rel}/ before removal: {e}. "
                "Refusing to proceed without a recovery snapshot.",
                file=sys.stderr,
            )
            return 2

        # Atomic index update FIRST, under flock. If the registration removal
        # fails, rmtree never runs and the directory stays put.
        rc, info = _atomic_remove_from_spaces(ancestor, ancestor_index, href)
        if rc != 0:
            print(f"  ! {info}", file=sys.stderr)
            return rc
        if info == "removed":
            print(f"  ~ {printable}index.md ## Spaces  -= [{rel_from_ancestor}/]")
        # Now rmtree the target. On failure, restore from snapshot AND
        # re-add the index entry we just removed.
        try:
            shutil.rmtree(target)
        except OSError as rm_err:
            # Restore directory contents byte-for-byte.
            restore_ok = True
            try:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(snapshot_dir / "target", target, symlinks=False)
            except (OSError, shutil.Error) as restore_err:
                restore_ok = False
                print(
                    f"  ! ROLLBACK INCOMPLETE: rmtree failed mid-delete ({rm_err}) "
                    f"AND restore failed ({restore_err}). "
                    f"Manual recovery from snapshot at {snapshot_dir}.",
                    file=sys.stderr,
                )
            # Restore the index entry we removed (best effort — same flock).
            if info == "removed":
                rel_from_ancestor_str = str(rel_from_ancestor)
                label = f"{rel_from_ancestor_str}/"
                href_restore = f"{rel_from_ancestor_str}/index.md"
                rc2, info2 = _atomic_register_in_spaces(
                    ancestor, ancestor_index, label, href_restore, None
                )
                if rc2 != 0:
                    print(
                        f"  ! ROLLBACK INCOMPLETE: rmtree failed ({rm_err}) and "
                        f"the index-entry restore also failed: {info2}. "
                        f"Manual recovery: re-add `[{label}]({href_restore})` "
                        f"to {printable}index.md ## Spaces.",
                        file=sys.stderr,
                    )
                    restore_ok = False
            if not restore_ok:
                # Override the finally-clause cleanup so the user has the
                # snapshot available for manual recovery.
                snapshot_ok = False
                return 2
            print(f"  ! rmtree failed: {rm_err}. Rolled back from snapshot.", file=sys.stderr)
            return 2
        print(f"  - {rel}/")
        return 0
    finally:
        # Clean up the snapshot unless we deliberately preserved it for
        # manual recovery.
        if snapshot_ok:
            shutil.rmtree(snapshot_dir, ignore_errors=True)


def _walk_owned_md_files(wiki_root: Path, *, include_external: bool = False) -> list[Path]:
    """Return every markdown file inside owned scope.

    Walks the tree directory by directory so external mounts at ANY depth are
    pruned — a foreign submodule at `projects/external/` is skipped even
    though `projects/` itself is owned (a plain `rglob` plus top-level filter
    would miss it). Mirrors `_walk_owned_spaces`'s realpath-visited guard so
    in-tree symlink cycles can't hang the walk. Hidden directories,
    `_archives/`, and `_meta/` are excluded — `_meta/limits.md` /
    `_meta/taxonomy.md` are configuration files the audit/promote walkers
    should never treat as content (CONVENTIONS / Reserved top-level
    folder names).

    When `include_external=True`, external subtrees are descended into and
    their `.md` files are included — used by `audit --include-external`.
    """
    out: list[Path] = []
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return out
    visited: set[Path] = {root_real}
    stack: list[Path] = [wiki_root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if entry.is_file() and entry.suffix == ".md":
                # `.md` symlinks pointing outside the wiki tree are external
                # content masquerading as owned — `audit`/`promote` would
                # otherwise rewrite or report them under default scope.
                # Apply the same escape check the directory branch below
                # uses: skip by default, include only when caller opted in.
                if entry.is_symlink():
                    try:
                        entry_real = entry.resolve()
                        entry_real.relative_to(root_real)
                    except (OSError, ValueError):
                        if not include_external:
                            continue
                out.append(entry)
                continue
            if not entry.is_dir():
                continue
            if name.startswith(".") or name in ("_archives", "_meta"):
                continue
            if name.startswith("wiki-spaces-promote-"):
                # Defensive: snapshots used by `space promote` live in /tmp, but
                # if a leftover snapshot dir ever appears under the wiki, skip it.
                continue
            if _is_external(entry, wiki_root) and not include_external:
                continue
            try:
                entry_real = entry.resolve()
            except OSError:
                continue
            if entry_real in visited:
                continue
            visited.add(entry_real)
            stack.append(entry)
    return out


def _count_owned_pages(wiki_root: Path, *, include_external: bool = False) -> int:
    """Count markdown files inside the audit scope.

    With `include_external=True`, files inside externally-classified spaces
    are counted too — used by `audit --include-external` so the summary
    header agrees with the per-page checks (drift / broken-wikilink /
    size-violation walks) below it.
    """
    return len(_walk_owned_md_files(wiki_root, include_external=include_external))


def _audit_content(wiki_root: Path, *, include_external: bool = False) -> tuple[list[tuple[Path, str]], list[Path]]:
    """Scan owned markdown for broken wikilinks and orphan pages.

    Returns `(broken, orphans)`:
    - `broken`  — `(page, target)` for each plain `[[wikilink]]` resolving to
      no page by path, filename, or frontmatter alias. Obsidian embeds
      (`![[...]]`) are never flagged broken — they routinely target non-page
      assets (images, PDFs); a resolvable embed still counts as an incoming
      reference. Links inside fenced code, inline code, and frontmatter are
      ignored — they are not real links.
    - `orphans` — content pages with zero incoming wikilinks, sorted.
      `index.md` and `log.md` are never orphan candidates (navigation /
      append-only log) but still count as link *sources*.

    Both are structural facts. Whether an orphan is acceptable, or how a
    broken link should be repaired, is judgment left to the caller.
    """
    md_files = _walk_owned_md_files(wiki_root, include_external=include_external)

    def _real(p: Path) -> Path:
        try:
            return p.resolve()
        except OSError:
            return p

    candidates: set[Path] = {_real(f) for f in md_files}

    # Frontmatter alias index (alias lowercased -> page) and post-frontmatter
    # bodies, read once per file.
    alias_index: dict[str, Path] = {}
    bodies: dict[Path, str] = {}
    for f in md_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        _, bodies[f] = _md.split_frontmatter(text)
        fm = _md.parse_frontmatter(text)
        if fm and isinstance(fm.get("aliases"), list):
            for alias in fm["aliases"]:
                if alias:
                    alias_index[str(alias).lower()] = f

    broken: list[tuple[Path, str]] = []
    incoming: set[Path] = set()
    for f, body in bodies.items():
        for link, is_embed in _md.find_wikilink_refs(_md.strip_code_spans(body)):
            target = _md.resolve_wikilink(link, f.parent, candidates, wiki_root=wiki_root)
            if target is None:
                aliased = alias_index.get(link.lower())
                if aliased is None:
                    # An embed (`![[...]]`) routinely targets a non-page asset
                    # — image, PDF, audio — absent from the page candidate set.
                    # Only plain `[[links]]` are flagged broken.
                    if not is_embed:
                        broken.append((f, link))
                    continue
                target = _real(aliased)
            if target != _real(f):  # a page linking itself is not "incoming"
                incoming.add(target)

    orphans = [
        f for f in md_files
        if f.name not in ("index.md", "log.md") and _real(f) not in incoming
    ]
    return broken, sorted(orphans)


def _summary_header(
    wiki_root: Path, all_spaces: list[Path], *, include_external: bool = False
) -> list[str]:
    convention_files = [
        "log.md", "_meta/taxonomy.md", ".manifest.json",
        "hot.md", "_template.md", ".obsidian",
    ]
    present = [c for c in convention_files if (wiki_root / c).exists()]

    pages = _count_owned_pages(wiki_root, include_external=include_external)
    if include_external:
        scope_desc = "owned + external scope (excludes hidden / _archives)"
    else:
        scope_desc = "owned scope; excludes hidden / _archives / external"

    lines = [
        f"wiki: {wiki_root}",
        f"  spaces: {len(all_spaces)}",
        f"  pages:  {pages} markdown files ({scope_desc})",
        f"  conventions at root: {', '.join(present) if present else '(none)'}",
    ]
    log = wiki_root / "log.md"
    if log.is_file():
        log_lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if log_lines:
            last = log_lines[-1].strip()
            if len(last) > 100:
                last = last[:97] + "..."
            lines.append(f"  last log:  {last}")
    return lines


def cmd_audit(args: argparse.Namespace) -> int:
    # Read-only by default → strict resolver (refuses missing `## Spaces`).
    # With `--fix` we're a repair surface → repair resolver + an explicit
    # ensure-section pass on the root before we enumerate drift.
    fix = getattr(args, "fix", False)
    remove_stale = getattr(args, "remove_stale", False)
    json_mode = getattr(args, "json", False)
    # JSON mode buffers stdout so the inline drift/broken/etc. lines don't
    # appear in the structured output. Errors still go to stderr.
    if json_mode:
        import io
        from contextlib import redirect_stdout
        _buf = io.StringIO()
        _redirect = redirect_stdout(_buf)
        _redirect.__enter__()
    if remove_stale and not fix:
        print(
            "  ! --remove-stale requires --fix",
            file=sys.stderr,
        )
        return 2
    if fix:
        wiki_root = _resolve_wiki_for_repair(args.wiki)
    else:
        wiki_root = _resolve_wiki_strict(args.wiki)
    if wiki_root is None:
        if fix:
            msg = "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config."
        else:
            msg = (
                "  ! no wiki resolved (or wiki has no `## Spaces` section). "
                "Pass --wiki <path>, set `wiki` in config, or run a write command "
                "(`space add`, `space remove`, `space mount`, `space promote`) "
                "to insert `## Spaces` automatically."
            )
        print(msg, file=sys.stderr)
        return 2

    include_external = getattr(args, "include_external", False)

    if fix:
        # Pass 1: insert `## Spaces` into every owned space that's missing it.
        # The FS walker (`_walk_owned_spaces`) surfaces bare-`index.md`
        # folders too; `_ensure_section_at` makes them spec-compliant.
        for space in list(
            _walk_owned_spaces(wiki_root, include_external=include_external)
        ):
            try:
                text = (space / "index.md").read_text(encoding="utf-8")
            except OSError:
                continue
            if _md.has_section(text, "Spaces"):
                continue
            try:
                _ensure_section_at(space, wiki_root)
            except RuntimeError as e:
                print(f"  ! {e}", file=sys.stderr)
                continue
            rel = space.relative_to(wiki_root)
            anc_label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
            print(f"  ~ {anc_label}/index.md  +inserted `## Spaces`")

    all_spaces = list(_walk_owned_spaces(wiki_root, include_external=include_external))
    for line in _summary_header(
        wiki_root, all_spaces, include_external=include_external
    ):
        print(line)
    print()
    # Every owned space should be listed in the `## Spaces` of its nearest
    # ancestor space. That ancestor can sit across intervening plain folders,
    # so the entry may be a multi-segment path (e.g. `projects/foo`).
    expected: dict[Path, set[str]] = {s: set() for s in all_spaces}
    for s in all_spaces:
        if s == wiki_root:
            continue
        parent = _nearest_ancestor_space(wiki_root, s)
        if parent in expected:
            expected[parent].add(s.relative_to(parent).as_posix())

    issues = 0
    # Track owned spaces whose `index.md` lacks `## Spaces`. Surface them in
    # the JSON payload too so structured consumers see an actionable finding
    # for a non-zero exit (otherwise `audit --json` returns exit_code=1 with
    # every other category empty — the consumer has no idea what failed).
    missing_section_spaces: list[Path] = []
    for space in all_spaces:
        text = (space / "index.md").read_text(encoding="utf-8")
        if not _md.has_section(text, "Spaces"):
            # An owned space whose `index.md` lacks `## Spaces` violates the
            # v1 navigation contract ("No `## Spaces` means no wiki"). Flag it
            # as an issue so read-only audit doesn't silently pass. Without
            # `--fix` the malformed section IS the report; with `--fix` the
            # bare-section repair pass above already inserted the heading
            # before we recomputed `all_spaces`, so this branch should be
            # unreachable when `fix=True` — but we still flag defensively in
            # case `_ensure_section_at` returned an error and was skipped.
            rel = space.relative_to(wiki_root)
            label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
            print(f"{label}/index.md:")
            print("  ! no `## Spaces` section (run `audit --fix` to insert)")
            missing_section_spaces.append(space)
            issues += 1
            continue
        # `## Spaces` hrefs, normalized so `foo`, `foo/`, `foo/index.md`, and
        # nested `projects/foo/index.md` all compare as the directory path.
        listed = {
            _spaces_href_to_dir(e.href)
            for e in _md.parse_section_entries(text, "Spaces")
            if e.href
        }
        # Missing: an owned child space whose nearest ancestor is this space,
        # not listed here. Stale: a listed entry with no index.md on disk
        # (a deleted space, or an entry pointing at a plain folder).
        missing = sorted(expected[space] - listed)
        stale = sorted(
            d for d in listed if not (space / d / "index.md").is_file()
        )
        if missing or stale:
            rel = space.relative_to(wiki_root)
            label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
            print(f"{label}/index.md:")
            for entry in missing:
                print(f"  + missing entry for {entry}/")
            for entry in stale:
                print(f"  - stale entry {entry}/ (no index.md on disk)")
            issues += len(missing) + len(stale)

            # `--fix` repair pass for THIS space: register every missing
            # entry and (optionally) remove stale ones. The fix is mechanical;
            # never creates a directory (a stale entry is removed from the
            # list, not promoted to a real space).
            if fix:
                ancestor_index = space / "index.md"
                for child_rel in missing:
                    label_str = f"{child_rel}/"
                    href = f"{child_rel}/index.md"

                    # Skip if the child's own `index.md` still lacks
                    # `## Spaces` after pass 1 — pass 1 must have failed
                    # to repair it (e.g., over-cap insertion was rejected).
                    # Registering the entry here would create the producer/
                    # consumer break the v1 contract is built to prevent:
                    # parent's `## Spaces` would advertise the child while
                    # the contract walker (which checks `## Spaces` on
                    # entry) skips it. The bare-child report above already
                    # surfaced the underlying issue; don't compound it.
                    child_index = space / child_rel / "index.md"
                    try:
                        child_text = child_index.read_text(encoding="utf-8")
                    except OSError:
                        print(
                            f"  ! could not register [{label_str}] in "
                            f"{label}/index.md: child index unreadable",
                            file=sys.stderr,
                        )
                        continue
                    if not _md.has_section(child_text, "Spaces"):
                        print(
                            f"  ! refusing to register [{label_str}] in "
                            f"{label}/index.md: child still lacks `## Spaces` "
                            "(pass 1 repair failed — fix that first).",
                            file=sys.stderr,
                        )
                        continue

                    # Route through a locked mutate that runs `_enforce_size_cap`
                    # on the projected text — `audit --fix` is a framework
                    # writer and must respect per-file caps. `_atomic_register_
                    # in_spaces` alone doesn't enforce caps; using the mutate
                    # form lets us reject (None, 2, reason) on overflow per the
                    # `_atomic_mutate_index` abort protocol.
                    def _register_mut(
                        fresh_text: str,
                        *,
                        _l=label_str,
                        _h=href,
                    ):
                        new = _add_space_entry(fresh_text, _l, _h, None)
                        if new == fresh_text:
                            return (fresh_text, "noop")
                        try:
                            _enforce_size_cap(ancestor_index, new, wiki_root)
                        except SizeCapExceeded as e:
                            return (None, 2, f"size cap: {e}")
                        return (new, "added")

                    rc, info = _atomic_mutate_index(
                        space, ancestor_index, _register_mut
                    )
                    if rc == 0 and info == "added":
                        print(f"  ~ {label}/index.md ## Spaces  += [{label_str}]")
                        issues -= 1
                    elif rc != 0:
                        print(
                            f"  ! could not register [{label_str}] in "
                            f"{label}/index.md: {info}",
                            file=sys.stderr,
                        )
                if remove_stale:
                    for child_rel in stale:
                        target_dir = space / child_rel
                        ext, _why = _is_in_external_scope(target_dir, wiki_root)
                        if ext and not include_external:
                            print(
                                f"  ! refusing to remove stale external entry "
                                f"{child_rel}/ in {label}/index.md; pass "
                                "--include-external --remove-stale together.",
                                file=sys.stderr,
                            )
                            continue
                        href = f"{child_rel}/index.md"
                        rc, info = _atomic_remove_from_spaces(
                            space, ancestor_index, href
                        )
                        if rc == 0 and info == "removed":
                            print(f"  ~ {label}/index.md ## Spaces  -= [{child_rel}/]")
                            issues -= 1

    # `issues` accumulated drift entries (missing/stale) AND one count per
    # owned space whose `index.md` lacked `## Spaces`. Split them so the
    # summary doesn't mis-label the bare-section count as "drift".
    drift_issues = issues - len(missing_section_spaces)
    broken, orphans = _audit_content(wiki_root, include_external=include_external)

    if broken:
        print()
        by_page: dict[Path, list[str]] = {}
        for page, link in broken:
            by_page.setdefault(page, []).append(link)
        for page in sorted(by_page):
            print(f"<wiki>/{page.relative_to(wiki_root)}:")
            for link in sorted(by_page[page]):
                print(f"  ! broken wikilink [[{link}]]")

    # Size violations — pages over their per-pattern cap. Reported alongside
    # drift and broken links; flips the exit code like the other hard errors.
    # Approaching-cap warnings (>= 80% but under cap) print here too but do
    # NOT flip the exit code.
    from . import _limits as _limits_module
    limits = _limits_module.read_limits(wiki_root)
    md_files = _walk_owned_md_files(wiki_root, include_external=include_external)
    over_cap: list[tuple[Path, int, int]] = []  # (path, chars, cap)
    approaching: list[tuple[Path, int, int]] = []  # (path, chars, cap)
    for f in md_files:
        chars = _limits_module.current_size(f)
        cap = _limits_module.cap_for(f, wiki_root, limits)
        if chars > cap:
            over_cap.append((f, chars, cap))
        elif chars >= int(cap * 0.8):
            approaching.append((f, chars, cap))

    if over_cap:
        print()
        for f, chars, cap in sorted(over_cap):
            rel = f.relative_to(wiki_root)
            print(f"<wiki>/{rel}: ! size {chars} > cap {cap}")
    if approaching:
        print()
        print(
            f"approaching cap (>= 80% full; informational, not an error):"
        )
        for f, chars, cap in sorted(approaching):
            rel = f.relative_to(wiki_root)
            pct = round(chars / cap * 100)
            print(f"  . <wiki>/{rel}: {chars}/{cap} ({pct}%)")

    # Malformed `## Spaces` entries — author errors the framework cannot
    # auto-repair. Reported alongside drift; flips the exit code.
    malformed = _audit_malformed_entries(wiki_root, all_spaces)
    if malformed:
        print()
        by_space: dict[Path, list[str]] = {}
        for sp, issue in malformed:
            by_space.setdefault(sp, []).append(issue)
        for sp in sorted(by_space):
            rel = sp.relative_to(wiki_root)
            label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
            print(f"{label}/index.md:")
            for issue in by_space[sp]:
                print(f"  ! malformed `## Spaces` entry — {issue}")

    # Duplicate aliases — when two pages declare the same alias, wikilink
    # resolution is nondeterministic (last walker visit wins). Always-on
    # audit so the producer can disambiguate before consumers see drift.
    alias_owners = _find_alias_owners(
        wiki_root,
        walker=_walk_owned_md_files,
        include_external=include_external,
    )
    duplicate_aliases = sorted(
        (alias, sorted(pages))
        for alias, pages in alias_owners.items()
        if len(pages) > 1
    )
    if duplicate_aliases:
        print()
        for alias, pages in duplicate_aliases:
            page_list = ", ".join(
                str(p.relative_to(wiki_root)) for p in pages
            )
            print(f"  ! duplicate alias [{alias}] declared by: {page_list}")

    if orphans:
        print(
            f"\norphans: {len(orphans)} page(s) with no incoming wikilinks "
            "(informational — a page may be standalone on purpose):"
        )
        for page in orphans:
            print(f"  . <wiki>/{page.relative_to(wiki_root)}")

    # Orphans and approaching-cap are facts, not errors — they never flip the
    # exit code. Drift, broken wikilinks, over-cap size violations, malformed
    # entries, and duplicate aliases all do.
    errors = (
        drift_issues
        + len(missing_section_spaces)
        + len(broken)
        + len(over_cap)
        + len(malformed)
        + len(duplicate_aliases)
    )
    if json_mode:
        # Drop the captured human output; emit a single JSON object.
        _redirect.__exit__(None, None, None)
        import json as _json
        exit_code = 0 if errors == 0 else 1
        # Rebuild `missing_by_space` / `stale_by_space` from the per-space loop
        # results above (we accumulated them inline; for JSON, walk again
        # cheaply since the data is fast to recompute and avoids carrying
        # state through 100+ lines).
        drift_payload: list[dict] = []
        for space_p in all_spaces:
            try:
                text = (space_p / "index.md").read_text(encoding="utf-8")
            except OSError:
                continue
            if not _md.has_section(text, "Spaces"):
                continue
            listed = {
                _spaces_href_to_dir(e.href)
                for e in _md.parse_section_entries(text, "Spaces")
                if e.href
            }
            miss = sorted(expected[space_p] - listed)
            stl = sorted(
                d for d in listed if not (space_p / d / "index.md").is_file()
            )
            if miss or stl:
                drift_payload.append({
                    "ancestor": str(space_p.relative_to(wiki_root).as_posix()) or ".",
                    "missing": miss,
                    "stale": stl,
                })
        out = {
            "wiki": str(wiki_root),
            "summary": {
                "spaces": len(all_spaces),
                "include_external": include_external,
            },
            "drift": drift_payload,
            "broken_wikilinks": [
                {
                    "page": str(p.relative_to(wiki_root).as_posix()),
                    "target": link,
                }
                for p, link in broken
            ],
            "size_violations": [
                {
                    "path": str(p.relative_to(wiki_root).as_posix()),
                    "chars": chars,
                    "cap": cap,
                }
                for p, chars, cap in over_cap
            ],
            "approaching_cap": [
                {
                    "path": str(p.relative_to(wiki_root).as_posix()),
                    "chars": chars,
                    "cap": cap,
                }
                for p, chars, cap in approaching
            ],
            "orphans": [
                str(p.relative_to(wiki_root).as_posix()) for p in orphans
            ],
            "malformed_entries": [
                {
                    "space": str(sp.relative_to(wiki_root).as_posix()) or ".",
                    "issue": issue,
                }
                for sp, issue in malformed
            ],
            "duplicate_aliases": [
                {
                    "alias": alias,
                    "pages": [
                        str(p.relative_to(wiki_root).as_posix()) for p in pages
                    ],
                }
                for alias, pages in duplicate_aliases
            ],
            # Owned spaces whose `index.md` lacks `## Spaces`. The human
            # output reports these inline; the structured output needs the
            # same hook so JSON consumers (skills, CI) can act on the
            # non-zero exit code with a specific actionable item.
            "missing_spaces_section": [
                str(sp.relative_to(wiki_root).as_posix()) or "."
                for sp in missing_section_spaces
            ],
            "exit_code": exit_code,
        }
        print(_json.dumps(out, indent=2))
        return exit_code
    print()
    if errors == 0:
        info_parts: list[str] = []
        if approaching:
            info_parts.append(f"{len(approaching)} approaching cap")
        if orphans:
            info_parts.append(f"{len(orphans)} orphan(s)")
        tail = f" ({', '.join(info_parts)} reported above)" if info_parts else ""
        print(f"OK: no drift, no broken wikilinks, no size violations{tail}")
        return 0
    parts: list[str] = []
    if drift_issues:
        parts.append(f"{drift_issues} `## Spaces` drift")
    if missing_section_spaces:
        parts.append(
            f"{len(missing_section_spaces)} space(s) missing `## Spaces`"
        )
    if broken:
        parts.append(f"{len(broken)} broken wikilink(s)")
    if over_cap:
        parts.append(f"{len(over_cap)} size violation(s)")
    if malformed:
        parts.append(f"{len(malformed)} malformed `## Spaces` entry/entries")
    if duplicate_aliases:
        parts.append(f"{len(duplicate_aliases)} duplicate alias(es)")
    print(
        f"{errors} issue(s) found: {' + '.join(parts)}. Re-run after fixing, "
        "or use `wiki-spaces space add/remove` for `## Spaces` entries, "
        "and `space promote` then split sections by hand (or shrink the page) "
        "for size violations."
    )
    return 1


def _run_git(cmd: list[str]) -> tuple[int, str]:
    """Run a git command; return `(returncode, stderr-or-error-text)`.

    Returns `(127, ...)` when git itself is missing, so `cmd_mount` can read
    linearly without nesting its own try/except per call.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return 127, "git not found on PATH"
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def cmd_mount(args: argparse.Namespace) -> int:
    """Mount an external space (clone / submodule / symlink) and register it.

    The mechanism is the caller's explicit choice (`--mode`) — collaborative
    vs read-only vs local is a judgment, not something the CLI guesses. The
    CLI does the mechanical part: run the mount, verify the result is a
    wiki-spaces space (`index.md` present), and add the `## Spaces` entry.

    Registration is atomic against partial-write failures: an advisory
    `fcntl.flock` on the ancestor directory serializes concurrent mounts,
    and the parent `index.md` is written via tempfile + `os.replace` so a
    crash mid-write cannot leave the file half-rewritten. If registration
    fails after a successful mount, the mount is rolled back per-mode.
    """
    wiki_root = _resolve_wiki_for_repair(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    # Optional `path`: derive `shared/<basename>/` when omitted.
    if args.path is None:
        derived, derive_err = _derive_default_path(args.source)
        if derived is None:
            print(f"  ! {derive_err}", file=sys.stderr)
            return 2
        path_arg = derived
    else:
        path_arg = args.path

    ok, err = _validate_rel_path(path_arg)
    if not ok:
        print(f"  ! invalid path: {err}", file=sys.stderr)
        return 2

    # `--name` and `--description` land directly inside the parent's
    # `## Spaces` entry as `- [NAME](HREF) — DESCRIPTION`. A `]` in NAME
    # or `)` in either value would break the markdown link syntax,
    # producing an entry `_md.parse_section_entries` can't read.
    for value, field in (
        (args.name, "--name"),
        (args.description, "--description"),
    ):
        ok, why = _validate_entry_text(value, field=field)
        if not ok:
            print(f"  ! {why}", file=sys.stderr)
            return 2

    rel = path_arg.strip().rstrip("/")
    dest = wiki_root / rel
    if dest.exists() or dest.is_symlink():
        print(
            f"  ! {rel} already exists; choose a path that does not exist yet",
            file=sys.stderr,
        )
        return 2

    ancestor = _nearest_ancestor_space(wiki_root, dest)
    ancestor_index = ancestor / "index.md"
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"

    mechanism = args.mechanism
    if mechanism == "submodule" and not (wiki_root / ".git").exists():
        print(
            f"  ! --mode submodule needs the wiki to be a git repo; "
            f"{wiki_root}/.git not found. Use --mode clone or --mode symlink, "
            "or `git init` the wiki first.",
            file=sys.stderr,
        )
        return 2

    # Validate a symlink source before creating anything on disk, so a bad
    # source leaves no empty parent directory behind.
    src_resolved: Path | None = None
    if mechanism == "symlink":
        src = Path(args.source).expanduser()
        try:
            src_resolved = src.resolve()
        except OSError:
            src_resolved = src
        if not src_resolved.is_dir():
            print(f"  ! symlink source is not a directory: {src}", file=sys.stderr)
            return 2

    # Pre-compute the registration label/href so dry-run can print it.
    rel_from_ancestor = dest.relative_to(ancestor)
    label = args.name or f"{rel_from_ancestor}/"
    href = f"{rel_from_ancestor}/index.md"

    if args.dry_run:
        print(f"  . (dry-run) would mount {args.source} -> {rel}/ via {mechanism}")
        desc_part = f" — {args.description}" if args.description else ""
        print(
            f"  . (dry-run) would register entry [{label}]({href}){desc_part} "
            f"in {printable}index.md ## Spaces"
        )
        return 0

    # Pre-flight the ancestor cap check BEFORE running the mount mechanism.
    # `git submodule add` stages a gitlink + edits `.gitmodules`; a cap
    # rejection after that would require manual cleanup the user has to
    # run by hand. Project the registration against the ancestor's current
    # text and refuse early. The in-lock check inside the chain helper
    # still catches concurrent growth between this pre-flight and the
    # actual mutation — pre-flight catches the easy case before any FS
    # mutation, the in-lock check is the authoritative gate.
    try:
        ancestor_text_now = ancestor_index.read_text(encoding="utf-8")
    except OSError:
        ancestor_text_now = ""
    projected_ancestor = ancestor_text_now
    if not _md.has_section(projected_ancestor, "Spaces"):
        if projected_ancestor and not projected_ancestor.endswith("\n"):
            projected_ancestor += "\n"
        projected_ancestor += "\n## Spaces\n\n"
    projected_ancestor = _add_space_entry(
        projected_ancestor, label, href, args.description
    )
    try:
        _enforce_size_cap(ancestor_index, projected_ancestor, wiki_root)
    except SizeCapExceeded as e:
        print(
            f"  ! size cap: {e}. Refusing to mount before any FS "
            "mutation — fix the ancestor's index.md or its cap first.",
            file=sys.stderr,
        )
        return 2

    dest.parent.mkdir(parents=True, exist_ok=True)

    if mechanism == "symlink":
        try:
            dest.symlink_to(src_resolved, target_is_directory=True)
        except OSError as e:
            print(f"  ! symlink failed: {e}", file=sys.stderr)
            return 1
        print(f"  + {rel} -> {src_resolved}  (symlink)")
    elif mechanism == "clone":
        rc, errout = _run_git(["git", "clone", args.source, str(dest)])
        if rc != 0:
            print(f"  ! git clone failed: {errout}", file=sys.stderr)
            return 1
        print(f"  + {rel}/  (git clone of {args.source})")
    else:  # submodule
        rc, errout = _run_git(
            ["git", "-C", str(wiki_root), "submodule", "add", args.source, rel]
        )
        if rc != 0:
            print(f"  ! git submodule add failed: {errout}", file=sys.stderr)
            return 1
        print(f"  + {rel}/  (git submodule of {args.source})")

    # Verify the mount is actually a wiki-spaces space before registering it.
    # The v1 contract requires `index.md` AND `## Spaces` on the mounted
    # target. Auto-inserting `## Spaces` into an external mount would mutate
    # someone else's repo, so we refuse instead — the user coordinates with
    # the upstream owner. A symlink or clone is cleaned up; a submodule
    # cannot be auto-undone safely (`submodule add` already staged a gitlink
    # and edited .gitmodules), so the exact recovery commands are printed.
    if not (dest / "index.md").is_file():
        print(
            f"  ! mounted {rel}/ has no index.md — it is not a wiki-spaces "
            "space, so it was not registered in `## Spaces`.",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism)
        return 1
    try:
        mounted_text = (dest / "index.md").read_text(encoding="utf-8")
    except OSError as e:
        print(
            f"  ! could not read mounted {rel}/index.md: {e}",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism)
        return 1
    if not _md.has_section(mounted_text, "Spaces"):
        print(
            f"  ! mounted {rel}/index.md has no `## Spaces` section. "
            "Coordinate with the upstream owner to add it before mounting; "
            "wiki-spaces does not auto-insert into external spaces.",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism)
        return 1

    # Register in the nearest ancestor's `## Spaces` via the chain helper.
    # The helper inserts `## Spaces` into any bare-`index.md` ancestor it
    # encounters as the first mutation step, then registers the mount.
    # Unlike `cmd_add`, mount's `--name` / `--description` DO map to the
    # parent's entry (label / description), not the child's body — the
    # child here is an external mount we don't write into.
    try:
        notices, _added = _ensure_spaces_chain_and_register(
            wiki_root,
            dest,
            leaf_label=args.name,
            leaf_description=args.description,
        )
        for n in notices:
            print(n)
    except EnsureChainError as e:
        for n in e.notices:
            print(n)
        _rollback_added_entries(e.added)
        _rollback_mount(wiki_root, dest, rel, mechanism)
        print(f"  ! {e}", file=sys.stderr)
        return 1
    return 0


def _rollback_mount(wiki_root: Path, dest: Path, rel: str, mechanism: str) -> None:
    """Undo a partial mount so the filesystem doesn't keep an orphaned space.

    Symlinks and clones are removable in one step; a submodule has already
    staged a gitlink and edited .gitmodules, so we print the manual recovery
    commands instead of guessing.
    """
    if mechanism == "symlink":
        try:
            dest.unlink()
            print(f"  - removed the symlink {rel}", file=sys.stderr)
        except OSError as e:
            print(f"  ! manual cleanup required: could not remove {rel}: {e}", file=sys.stderr)
    elif mechanism == "clone":
        shutil.rmtree(dest, ignore_errors=True)
        print(f"  - removed the clone at {rel}/", file=sys.stderr)
    else:  # submodule
        print(
            f"    `git submodule add` left files on disk, staged a "
            f"gitlink, and edited .gitmodules. To undo:\n"
            f"      git -C {wiki_root} submodule deinit -f {rel}\n"
            f"      git -C {wiki_root} rm -f {rel}\n"
            f"      rm -rf {wiki_root}/.git/modules/{rel}",
            file=sys.stderr,
        )


def _atomic_mutate_index(
    ancestor: Path,
    ancestor_index: Path,
    mutate_fn,
):
    """Atomically apply `mutate_fn` to the ancestor's index.md under flock.

    Generic primitive shared by add / remove / mount. `mutate_fn` takes the
    current text and returns one of:
    - `(new_text, info)` — replace the file with new_text; info is returned
      to the caller. When new_text == current, the write is skipped.
    - A tuple `(None, error_code, reason)` — abort with that error.

    Returns `(rc, info_or_reason)`:
    - `(0, info)` — wrote new_text (or no-op when mutate_fn returned the
      original text). `info` is whatever mutate_fn passed back (e.g.
      "added", "removed", "noop").
    - `(1, reason)` — write failed.
    - `(2, reason)` — mutate_fn returned an abort tuple (e.g. contract
      missing).

    Locking note: the lock is on the ANCESTOR DIRECTORY's inode (stable
    across our `os.replace` of the index file), so two concurrent CLI
    callers serialize correctly. Within the lock we re-read the index
    from disk to pick up any changes that committed after the caller's
    initial read.
    """
    dir_fd = os.open(str(ancestor), os.O_RDONLY)
    try:
        if sys.platform != "win32":
            fcntl.flock(dir_fd, fcntl.LOCK_EX)
        fresh_text = ancestor_index.read_text(encoding="utf-8")
        result = mutate_fn(fresh_text)
        if isinstance(result, tuple) and len(result) == 3 and result[0] is None:
            return result[1], result[2]
        new_text, info = result
        if new_text == fresh_text:
            return 0, info  # caller can interpret e.g. "noop"
        # Write via tempfile + os.replace.
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(ancestor_index.parent),
            prefix=".index.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = tmp.name
        try:
            tmp.write(new_text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp_path, ancestor_index)
        except OSError as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return 1, f"could not write {ancestor_index}: {e}"
        return 0, info
    finally:
        if sys.platform != "win32":
            try:
                fcntl.flock(dir_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(dir_fd)


def _atomic_register_in_spaces(
    ancestor: Path,
    ancestor_index: Path,
    label: str,
    href: str,
    description: str | None,
) -> tuple[int, str]:
    """Atomically add a `## Spaces` entry under an `fcntl.flock` on the ancestor dir.

    Thin wrapper over `_atomic_mutate_index`. Returns `(0, "added")` /
    `(0, "noop")` / `(1, reason)` / `(2, reason)` — same contract as before
    so existing callers (cmd_mount) keep working.
    """
    def add_entry(fresh_text: str):
        if not _md.has_section(fresh_text, "Spaces"):
            return (None, 2, "ancestor `## Spaces` section disappeared between contract check and registration")
        new_text = _add_space_entry(fresh_text, label, href, description)
        return (new_text, "noop" if new_text == fresh_text else "added")

    return _atomic_mutate_index(ancestor, ancestor_index, add_entry)


def _atomic_remove_from_spaces(
    ancestor: Path,
    ancestor_index: Path,
    href: str,
) -> tuple[int, str]:
    """Atomically remove a `## Spaces` entry under flock. Symmetric with
    `_atomic_register_in_spaces`. Same return contract."""
    def remove_entry(fresh_text: str):
        if not _md.has_section(fresh_text, "Spaces"):
            return (None, 2, "ancestor `## Spaces` section disappeared between contract check and removal")
        new_text = _remove_space_entry(fresh_text, href)
        return (new_text, "noop" if new_text == fresh_text else "removed")

    return _atomic_mutate_index(ancestor, ancestor_index, remove_entry)


# ---------- Size discipline ----------
#
# Hoisted into PR-D because `_ensure_section_at` and
# `_ensure_spaces_chain_and_register` (both defined below) enforce per-file
# caps on every projected ancestor mutation. PR-L adds the CLI primitive
# (`space check-size`) and wires the remaining framework-write paths through
# `_enforce_size_cap`; both PRs share the same helpers defined here.


class SizeCapExceeded(Exception):
    """Raised when a projected write would push a file past its cap."""

    def __init__(self, path: Path, chars: int, cap: int):
        self.path = path
        self.chars = chars
        self.cap = cap
        super().__init__(f"{path}: projected {chars} chars > cap {cap}")


def _size_check_outcome(
    path: Path, projected_text: str, wiki_root: Path
) -> tuple[str, int, int]:
    """Return `(outcome, projected_chars, cap)`.

    Outcomes:
    - `"ok"`           — under cap.
    - `"ok-shrinking"` — over cap but smaller than the current on-disk body
                         (legacy bloat escape hatch).
    - `"over"`         — over cap and not shrinking.
    """
    from . import _limits as L
    limits = L.read_limits(wiki_root)
    over, chars, cap = L.would_exceed(path, projected_text, wiki_root, limits)
    if not over:
        return ("ok", chars, cap)
    current = L.current_size(path)
    projected = len(_md.strip_frontmatter(projected_text))
    if projected < current:
        return ("ok-shrinking", chars, cap)
    return ("over", chars, cap)


def _enforce_size_cap(path: Path, projected_text: str, wiki_root: Path) -> None:
    """Raise `SizeCapExceeded` when a projected write would exceed the cap."""
    outcome, chars, cap = _size_check_outcome(path, projected_text, wiki_root)
    if outcome == "over":
        raise SizeCapExceeded(path, chars, cap)


# ---------- `## Spaces` section + chain registration ----------


class EnsureChainError(Exception):
    """Raised by `_ensure_spaces_chain_and_register` on atomic-helper failure.

    Carries the entries already added and the notices already emitted so the
    caller can print the partial trail and roll back FS-side state.
    """

    def __init__(
        self,
        ancestor: Path,
        info: str,
        added: list[tuple[Path, str, str]],
        notices: list[str],
    ):
        self.ancestor = ancestor
        self.info = info
        self.added = added
        self.notices = notices
        super().__init__(f"ensure-chain failed at {ancestor}: {info}")


def _ensure_section_at(space: Path, wiki_root: Path) -> str:
    """Ensure `space/index.md` carries a `## Spaces` heading.

    Returns `"inserted"` or `"noop"`. Does NOT walk up; does NOT register
    anything in any parent. Used by `cmd_remove` (so a child entry can be
    removed once a `## Spaces` exists), by `audit --fix`'s missing-section
    repair pass (PR-E), and by `init --adopt`'s leaf section repair
    (PR-E). Size-capped via `_enforce_size_cap`.

    Raises `RuntimeError` on atomic-helper failure (write or cap).
    """
    space_index = space / "index.md"

    def _mutate(fresh_text: str):
        if _md.has_section(fresh_text, "Spaces"):
            return (fresh_text, "noop")
        text = fresh_text
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n## Spaces\n\n"
        try:
            _enforce_size_cap(space_index, text, wiki_root)
        except SizeCapExceeded as e:
            return (None, 2, f"size cap: {e}")
        return (text, "inserted")

    rc, info = _atomic_mutate_index(space, space_index, _mutate)
    if rc != 0:
        raise RuntimeError(f"ensure-section failed at {space}: {info}")
    return info


def _ensure_spaces_chain_and_register(
    wiki_root: Path,
    leaf_space: Path,
    *,
    leaf_label: str | None = None,
    leaf_description: str | None = None,
) -> tuple[list[str], list[tuple[Path, str, str]]]:
    """For each `(ancestor, child)` edge from `leaf_space` up to and including
    registration in `wiki_root`'s `## Spaces`:

      1. Ensure the ancestor's `index.md` carries `## Spaces`.
      2. Register `child` as an entry in that section.

    Walks ALL the way up: `space add foo/bar` against a wiki where
    `foo/index.md` exists bare and `wiki/index.md` has no `## Spaces`
    registers `bar` in `foo`, then `foo` in `<wiki>`, inserting
    `## Spaces` in both.

    `leaf_label` / `leaf_description` apply to the FIRST iteration (the
    edge that registers `leaf_space` itself) ONLY. Intermediate ancestor
    registrations always use the derived label and `None` description —
    they're book-keeping, not user-typed metadata.

    Returns `(notices, added_entries)`. On any atomic-helper failure,
    raises `EnsureChainError` carrying the partial state so the caller
    can print and roll back.

    Edge case: `leaf_space == wiki_root` → `([], [])` (nothing to do).
    """
    notices: list[str] = []
    added: list[tuple[Path, str, str]] = []
    if leaf_space == wiki_root:
        return notices, added
    child = leaf_space
    is_leaf_edge = True
    while child != wiki_root:
        ancestor = _nearest_ancestor_space(wiki_root, child)
        if ancestor == child:
            break
        ancestor_index = ancestor / "index.md"
        rel_from_ancestor = child.relative_to(ancestor)
        derived_label = f"{rel_from_ancestor}/"
        href = f"{rel_from_ancestor}/index.md"
        label = (
            leaf_label if (is_leaf_edge and leaf_label) else derived_label
        )
        description = leaf_description if is_leaf_edge else None
        is_leaf_edge = False

        def _mutate(
            fresh_text: str,
            *,
            _label=label,
            _href=href,
            _desc=description,
        ):
            text = fresh_text
            inserted = False
            if not _md.has_section(text, "Spaces"):
                if text and not text.endswith("\n"):
                    text += "\n"
                text += "\n## Spaces\n\n"
                inserted = True
            new = _add_space_entry(text, _label, _href, _desc)
            entry_added = new != text
            tag = (
                "inserted-and-added" if inserted and entry_added
                else "inserted" if inserted
                else "added" if entry_added
                else "noop"
            )
            try:
                _enforce_size_cap(ancestor_index, new, wiki_root)
            except SizeCapExceeded as e:
                return (None, 2, f"size cap: {e}")
            return (new, tag)

        rc, info = _atomic_mutate_index(ancestor, ancestor_index, _mutate)
        if rc != 0:
            raise EnsureChainError(ancestor, info, added, notices)

        anc_rel = ancestor.relative_to(wiki_root)
        anc_label = "<wiki>" if str(anc_rel) == "." else f"<wiki>/{anc_rel}"
        if info in ("inserted", "inserted-and-added"):
            notices.append(f"  ~ {anc_label}/index.md  +inserted `## Spaces`")
        if info in ("added", "inserted-and-added"):
            notices.append(f"  ~ {anc_label}/index.md ## Spaces  += [{label}]")
            added.append((ancestor, label, href))

        if ancestor == wiki_root:
            break
        child = ancestor
    return notices, added


def _rollback_added_entries(entries: list[tuple[Path, str, str]]) -> None:
    """Undo entries added by `_ensure_spaces_chain_and_register`, deepest first.

    The chain helper appends to `entries` as it walks UP from the leaf, so the
    first appended entry is the deepest (the leaf's parent) and the last is
    the wiki root. Iterating in forward order therefore removes deepest-first,
    matching the plan's PR-D contract.

    Best-effort: prints to stderr on failure but never raises. Inserted
    `## Spaces` sections are NOT rolled back — they're append-only and
    non-destructive; leaving them in place is the safe choice.
    """
    for ancestor, label, href in entries:
        ancestor_index = ancestor / "index.md"
        rc, info = _atomic_remove_from_spaces(ancestor, ancestor_index, href)
        if rc != 0:
            print(
                f"  ! could not roll back entry [{label}] from {ancestor_index}: {info}",
                file=sys.stderr,
            )


def _find_alias_owners(
    wiki_root: Path,
    *,
    walker=None,
    include_external: bool = False,
) -> dict[str, list[Path]]:
    """Build `{alias.casefold(): [pages]}` across the walked file set.

    Used by `cmd_promote`'s collision preflight (default walker: the FS
    walker `_walk_owned_md_files`) AND by `cmd_audit`'s duplicate-alias
    pass (which passes the FS walker explicitly with `include_external`
    matching the audit flag). A `walker` callable lets cmd_audit's
    duplicate-alias check stay on the FS walker even after the contract
    walker becomes the default consumer surface (PR-K2).

    Pages with no frontmatter or no `aliases:` contribute nothing. A
    single page declaring the same alias in multiple cases (e.g.
    `aliases: [bar, BAR]`) appears once.
    """
    if walker is None:
        walker = _walk_owned_md_files
    out: dict[str, list[Path]] = {}
    for page in walker(wiki_root, include_external=include_external):
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        seen_for_page: set[str] = set()
        for alias in _md.parse_frontmatter_aliases(text):
            key = alias.casefold()
            if key in seen_for_page:
                continue
            seen_for_page.add(key)
            out.setdefault(key, []).append(page)
    return out


# `## Spaces` entries that don't match _md.ENTRY_RE (e.g. empty href) are
# silently dropped by `parse_section_entries`. Audit's malformed pass uses
# its own raw-line scanner so empty hrefs and other failure shapes surface.
import re as _re

_AUDIT_BULLET_RE = _re.compile(r"^\s*-\s+\[([^\]]*)\]\(([^)]*)\)")


def _audit_malformed_entries(
    wiki_root: Path,
    spaces: list[Path],
) -> list[tuple[Path, str]]:
    """Find `## Spaces` entries that fail policy.

    Reported as errors (audit flips the exit code on any of):
    - Empty href (`- [foo]()`).
    - Absolute path href (`- [foo](/abs/path)`).
    - Href containing `..` segments.
    - Href that escapes the wiki root after resolution.
    - Duplicate entries pointing at the same directory.

    Independent of `_md.parse_section_entries` because the raw `ENTRY_RE`
    drops some malformed shapes silently. External-classified targets are
    NOT flagged as escape (they legitimately resolve outside; trust scope
    is the opt-in gate, not malformed-href).

    `audit --fix` does NOT auto-repair these — malformed entries signal
    author intent the framework cannot reconstruct.
    """
    issues: list[tuple[Path, str]] = []
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return issues
    for space in spaces:
        try:
            text = (space / "index.md").read_text(encoding="utf-8")
        except OSError:
            continue
        in_spaces = False
        seen_dirs: set[str] = set()
        for line in text.splitlines():
            stripped = line.rstrip()
            if stripped == "## Spaces":
                in_spaces = True
                continue
            if in_spaces and stripped.startswith("## "):
                in_spaces = False
            if not in_spaces:
                continue
            m = _AUDIT_BULLET_RE.match(line)
            if not m:
                continue
            href = m.group(2)
            if not href.strip():
                issues.append((space, f"empty href: {line.strip()}"))
                continue
            if href.startswith("/"):
                issues.append((space, f"absolute href: {href}"))
                continue
            href_path = Path(href)
            if ".." in href_path.parts:
                issues.append((space, f"href contains `..`: {href}"))
                continue
            # Reserved-folder hrefs per CONVENTIONS / Reserved top-level
            # folder names. The consumer walker prunes these on read, so
            # an entry like `- [_meta/internal/](_meta/internal/index.md)`
            # is invisible to `space list` / `space files`. Audit must
            # flag it — otherwise a pre-v1 layout passes clean while
            # consumers can't see the registered space (producer/consumer
            # break unrepaired).
            if any(
                part.startswith(".") or part in ("_archives", "_meta")
                for part in href_path.parts
            ):
                issues.append((
                    space,
                    f"reserved-folder href: {href} (hidden / `_archives` "
                    "/ `_meta` paths are pruned by the consumer walker; "
                    "remove the entry or move the content to a non-reserved "
                    "path)",
                ))
                continue
            try:
                resolved = (space / href).resolve()
            except OSError:
                issues.append((space, f"href unresolvable: {href}"))
                continue
            child_path = space / href
            is_ext, _why = _is_in_external_scope(child_path, wiki_root)
            try:
                resolved.relative_to(root_real)
                escapes = False
            except ValueError:
                escapes = True
            if escapes and not is_ext:
                issues.append((space, f"href escapes after resolution: {href}"))
                continue
            dir_norm = _spaces_href_to_dir(href)
            if dir_norm in seen_dirs:
                issues.append((space, f"duplicate href dir: {dir_norm}"))
            seen_dirs.add(dir_norm)
    return issues


def _is_git_tracked(path: Path, wiki_root: Path) -> bool:
    """True when `git ls-files --error-unmatch <rel>` returns 0."""
    try:
        rel = path.relative_to(wiki_root)
    except ValueError:
        return False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=wiki_root, capture_output=True, check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _mask_for_link_scan(text: str) -> str:
    """Return an offset-preserving mask of `text` that hides code spans and
    YAML frontmatter from a link scanner.

    Shared by `_rewrite_links_pointing_at` and `_adjust_outgoing_links_for_depth`
    so the scan is consistent across both promote-time rewriters — without
    this, the outgoing-link adjustment would happily rewrite `[[wikilinks]]`
    inside fenced code or frontmatter (producer/consumer break: the
    promote step would emit content the audit step misreads).

    Uses `_md.FRONTMATTER_RE.match` to locate the frontmatter span instead
    of `text.index("---", 4)` so atypical leading newlines / malformed
    fences don't throw.
    """
    m = _md.FRONTMATTER_RE.match(text)
    if m is not None:
        body_start = m.start(2)
        return (" " * body_start) + _md.mask_code_spans_offset_preserving(
            text[body_start:]
        )
    return _md.mask_code_spans_offset_preserving(text)


def _rewrite_links_pointing_at(
    *,
    text: str,
    page: Path,
    old_target: Path,
    new_target: Path,
    new_target_wikilink: str,
    wiki_root: Path,
    candidates: set[Path],
) -> str:
    """Rewrite markdown links AND wikilinks in `text` that resolve to
    `old_target` so they point at `new_target` instead.

    Markdown href is recomputed relative to `page`'s directory (preserves
    nested-page correctness — codex v3 named the wiki-root-relative
    rewrite as silent-corruption-risk for deep linking pages).

    Wikilink target becomes `new_target_wikilink` (e.g. `projects/foo/index`).
    Display preserved when explicit; original target text used as display
    when none was present (rendered text never changes for the reader).

    Uses the unified `_md.resolve_wikilink` (wiki-root pathful first, then
    base-relative, then bare filename) — same precedence as audit, so a
    rewrite the promote step makes can never be misread as broken by a
    subsequent audit.
    """
    masked = _mask_for_link_scan(text)

    replacements: list[tuple[int, int, str]] = []
    for link in _md.parse_markdown_links(masked):
        resolved = _md.resolve_markdown_link(link.href, page, wiki_root)
        if resolved is None or resolved != old_target:
            continue
        new_href = _md.compute_relative_link(new_target, page)
        new_substring = f"[{link.label}]({new_href}{link.anchor})"
        replacements.append((link.span[0], link.span[1], new_substring))

    for wl in _md.parse_wikilink_full(masked):
        resolved = _md.resolve_wikilink(
            wl.target, page.parent, candidates, wiki_root=wiki_root
        )
        if resolved is None or resolved != old_target:
            continue
        display = wl.display if wl.display is not None else wl.target
        anchor = f"#{wl.anchor}" if wl.anchor else ""
        new_inner = f"{new_target_wikilink}{anchor}|{display}"
        replacements.append((wl.span[0], wl.span[1], f"[[{new_inner}]]"))

    if not replacements:
        return text
    replacements.sort(reverse=True)
    out = text
    for start, end, new in replacements:
        out = out[:start] + new + out[end:]
    return out


def _adjust_outgoing_links_for_depth(
    *,
    text: str,
    original_file: Path,
    new_file: Path,
    wiki_root: Path,
) -> str:
    """Adjust the promoted file's own outgoing relative markdown links for
    its new (one-level-deeper) location.

    `[label](rel.md#anchor)` was resolved against `original_file.parent`.
    After the move, the same relative path resolves against `new_file.parent`
    (one level deeper). Rewrite the href so it still resolves to the same
    absolute target. Self-links (`[label](mypage.md)` inside the promoted
    file itself) are re-pointed at `new_file`.
    """
    original_resolved = original_file.resolve()
    new_file_resolved = new_file
    try:
        new_file_resolved = new_file.resolve()
    except OSError:
        pass
    # Mask code spans + frontmatter for the scan — same protection as
    # `_rewrite_links_pointing_at` (§29). Without this, a `[label](file.md)`
    # inside a fenced code block or YAML frontmatter would be rewritten as
    # if it were a real markdown link.
    masked = _mask_for_link_scan(text)
    replacements: list[tuple[int, int, str]] = []
    for link in _md.parse_markdown_links(masked):
        target = _md.resolve_markdown_link(link.href, original_file, wiki_root)
        if target is None:
            continue
        if target == original_resolved:
            target = new_file_resolved
        new_href = _md.compute_relative_link(target, new_file)
        new_substring = f"[{link.label}]({new_href}{link.anchor})"
        if new_substring != text[link.span[0]:link.span[1]]:
            replacements.append((link.span[0], link.span[1], new_substring))
    if not replacements:
        return text
    replacements.sort(reverse=True)
    out = text
    for start, end, new in replacements:
        out = out[:start] + new + out[end:]
    return out


def _restore_from_snapshot(snapshot_dir: Path, wiki_root: Path) -> None:
    """Overwrite every wiki file with its snapshot copy (best-effort)."""
    for snap_file in snapshot_dir.rglob("*"):
        if not snap_file.is_file():
            continue
        rel = snap_file.relative_to(snapshot_dir)
        dest = wiki_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap_file, dest)


def _entry_label_description_map(
    wiki_root: Path, *, include_external: bool
) -> dict[Path, tuple[str, str | None]]:
    """Build `child_resolved_path -> (label, description)` by walking the
    contract and collecting each parent's `## Spaces` entry metadata.

    Used by `cmd_list --json` so the JSON output carries label and
    description fields the placement classifier (skills) consumes.
    """
    out: dict[Path, tuple[str, str | None]] = {}
    for parent, _ext in _walk_via_spaces_contract(
        wiki_root, include_external=include_external
    ):
        try:
            text = (parent / "index.md").read_text(encoding="utf-8")
        except OSError:
            continue
        for entry in _md.parse_section_entries(text, "Spaces"):
            if not entry.href:
                continue
            href_dir = _spaces_href_to_dir(entry.href)
            if not href_dir:
                continue
            href_path = Path(href_dir)
            if href_path.is_absolute() or ".." in href_path.parts:
                continue
            try:
                child_real = (parent / href_dir).resolve()
            except OSError:
                continue
            out[child_real] = (entry.label or href_dir, entry.description)
    return out


def cmd_check_size(args: argparse.Namespace) -> int:
    """Print a size-cap verdict for a projected post-write text.

    Usage:
      wiki-spaces space check-size <rel-path> --projected-file <text-file>
      wiki-spaces space check-size <rel-path> --projected-stdin
      cat new-content.md | wiki-spaces space check-size <rel-path>

    Prints `OK <chars>/<cap>`, `OK-SHRINKING <chars>/<cap>`, or
    `OVER <chars>/<cap>`. Exit 0 for OK and OK-SHRINKING (the shrinking-
    write hatch from legacy bloat); exit 1 for OVER.

    The point of the CLI: skills compute projected content in memory,
    then shell out for the verdict — no more "the LLM did the math
    wrong" as a defect class (§S3). Same `_size_check_outcome` helper
    the framework writers use, so verdicts always agree.
    """
    wiki_root = _resolve_wiki_strict(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved (or wiki has no `## Spaces` section). "
            "Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2
    if args.path.startswith("/") or ".." in Path(args.path).parts:
        print("  ! path must be wiki-root-relative", file=sys.stderr)
        return 2
    target = wiki_root / args.path
    if args.projected_stdin:
        projected = sys.stdin.read()
    elif args.projected_file:
        try:
            projected = Path(args.projected_file).read_text(encoding="utf-8")
        except OSError as e:
            print(
                f"  ! could not read {args.projected_file}: {e}",
                file=sys.stderr,
            )
            return 2
    else:
        # Sensible default: read stdin if it isn't a TTY.
        if not sys.stdin.isatty():
            projected = sys.stdin.read()
        else:
            print(
                "  ! pass --projected-stdin or --projected-file <path>, or "
                "pipe content into stdin",
                file=sys.stderr,
            )
            return 2
    outcome, chars, cap = _size_check_outcome(target, projected, wiki_root)
    label = {"ok": "OK", "ok-shrinking": "OK-SHRINKING", "over": "OVER"}[outcome]
    print(f"{label} {chars}/{cap}")
    return 1 if outcome == "over" else 0


def cmd_list(args: argparse.Namespace) -> int:
    """List spaces reachable via the `## Spaces` contract.

    Default: tab-separated `path\\tclassification` lines for shell use.
    With `--json`: structured `{path, label, description, external}` per
    space (the root is excluded — placement classifiers want children
    only). With `--include-boundaries --include-external`: also surfaces
    external boundary folders without `index.md` (foreign submodules,
    escaping symlinks). The placement classifier in `wiki-update` uses
    that combination to enumerate every external path to exclude.
    """
    wiki_root = _resolve_wiki_strict(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved (or wiki has no `## Spaces` section). "
            "Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    if args.include_boundaries and not args.include_external:
        print(
            "  ! --include-boundaries requires --include-external",
            file=sys.stderr,
        )
        return 2

    spaces = list(
        _walk_via_spaces_contract(
            wiki_root, include_external=args.include_external
        )
    )
    if args.include_boundaries:
        # Surface external boundary folders that `_walk_via_spaces_contract`
        # missed because they lack `index.md` (foreign submodules, escaping
        # symlinks). Dedupe on resolved path; emit lexical path so callers
        # can do `.relative_to(wiki_root)` (an escaping symlink's resolved
        # path is outside the wiki tree).
        seen_real: set[Path] = set()
        for s, _ in spaces:
            try:
                seen_real.add(s.resolve())
            except OSError:
                continue
        for path, classification, _reason in _walk_classified(
            wiki_root, include_external=True
        ):
            if classification != "external":
                continue
            try:
                if path.resolve() in seen_real:
                    continue
            except OSError:
                continue
            spaces.append((path, True))

    label_map = _entry_label_description_map(
        wiki_root, include_external=args.include_external
    )

    if args.json:
        import json
        out: list[dict] = []
        for s, is_ext in spaces:
            if s == wiki_root:
                continue
            try:
                s_real = s.resolve()
            except OSError:
                s_real = s
            rel = s.relative_to(wiki_root).as_posix()
            label, description = label_map.get(s_real, (f"{rel}/", None))
            out.append({
                "path": rel,
                "label": label,
                "description": description,
                "external": is_ext,
            })
        print(json.dumps(out, indent=2))
    else:
        for s, is_ext in spaces:
            rel = "." if s == wiki_root else s.relative_to(wiki_root).as_posix()
            tag = "external" if is_ext else "owned"
            print(f"{rel}\t{tag}")
    return 0


def cmd_files(args: argparse.Namespace) -> int:
    """List `.md` files reachable via the `## Spaces` contract.

    Walks owned scope by default; `--include-external` opts external
    subtrees in. With a `space` argument, scopes output to files under
    that wiki-root-relative space (which must be contract-reachable —
    unregistered subspaces are refused with a hint to run `space audit`).

    The walker runs from `wiki_root` even when a scope is given; output
    is filtered lexically afterward so an opted-in external symlink
    whose resolved path leaves the tree is still included.
    """
    wiki_root = _resolve_wiki_strict(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved (or wiki has no `## Spaces` section). "
            "Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    scope_root: Path | None = None
    if args.space:
        if args.space.startswith("/") or ".." in Path(args.space).parts:
            print("  ! space must be wiki-root-relative", file=sys.stderr)
            return 2
        scope_root = wiki_root / args.space
        if not scope_root.is_dir():
            print(f"  ! {args.space}: not a directory", file=sys.stderr)
            return 2
        # Contract-reachable check: refuse on unregistered scopes. Compare
        # LEXICAL paths, not resolved ones — a user-made symlink alias
        # (`notes-alias/` -> registered `notes/`) resolves to the same path
        # as the registered space, but its lexical path doesn't appear in
        # the contract walker's output. Without the lexical check, an alias
        # would "pass" reachability and then lexical filtering would return
        # an empty list (because the walker yielded the canonical path).
        # The consumer-side contract is exhaustive: only paths the contract
        # walker actually emitted are valid scopes.
        #
        # Naming an external scope explicitly opts the consumer in per
        # AGENTS.md / trust scope: "External spaces are visited only when
        # the user explicitly names one or asks to include all." The
        # opt-in is THE NAMED SCOPE specifically — not "any non-root
        # scope". So:
        #   - Named scope is external (under shared/, foreign submodule,
        #     escaping symlink, or any ancestor thereof): opt-in.
        #   - Named scope is owned (e.g. `projects/foo`): default — owned
        #     external descendants under projects/foo are NOT surfaced
        #     unless the global --include-external flag is set.
        #   - Named scope is the wiki root: default; honor the global flag.
        scope_is_external, _why = _is_in_external_scope(
            scope_root, wiki_root
        )
        scope_include_external = (
            scope_is_external or args.include_external
        )
        reachable: set[Path] = set()
        for s, _ in _walk_via_spaces_contract(
            wiki_root, include_external=scope_include_external
        ):
            reachable.add(s)
        if scope_root not in reachable:
            print(
                f"  ! {args.space}: not reachable via parent `## Spaces`; "
                "unregistered spaces are not consumer-visible. Run "
                "`wiki-spaces space audit` to surface drift.",
                file=sys.stderr,
            )
            return 2

    # Traverse with the same external-opt-in semantics: only when the
    # NAMED scope is itself external (or the global flag is on). For an
    # owned named scope, default behavior — external descendants stay
    # hidden.
    if scope_root is not None:
        scope_is_external, _why = _is_in_external_scope(
            scope_root, wiki_root
        )
        traverse_external = scope_is_external or args.include_external
    else:
        traverse_external = args.include_external
    all_files = list(
        _walk_md_files_via_contract(
            wiki_root, include_external=traverse_external
        )
    )
    if scope_root is None or scope_root == wiki_root:
        files = all_files
    else:
        # Filter lexically — external symlinks live lexically under
        # wiki_root even when their resolved path doesn't.
        scope_str = str(scope_root) + os.sep
        files = [
            (f, is_ext)
            for f, is_ext in all_files
            if str(f).startswith(scope_str) or f == scope_root
        ]

    if args.json:
        import json
        print(
            json.dumps(
                [
                    {
                        "path": f.relative_to(wiki_root).as_posix(),
                        "external": is_ext,
                    }
                    for f, is_ext in files
                ],
                indent=2,
            )
        )
    else:
        for f, is_ext in files:
            tag = "\texternal" if is_ext else ""
            print(f"{f.relative_to(wiki_root)}{tag}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    wiki_root = _resolve_wiki_for_repair(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    ok, err = _validate_rel_path(args.path)
    if not ok:
        print(f"  ! invalid path: {err}", file=sys.stderr)
        return 2
    rel = args.path.strip().rstrip("/")
    if not rel.endswith(".md"):
        print(f"  ! {rel} must be a .md file", file=sys.stderr)
        return 2

    source = wiki_root / rel
    if not source.is_file():
        print(f"  ! {rel} does not exist or is not a regular file", file=sys.stderr)
        return 2
    if source.name == "index.md":
        print(f"  ! cannot promote an index.md", file=sys.stderr)
        return 2

    external, reason = _is_in_external_scope(source.parent, wiki_root)
    if external:
        print(
            f"  ! refusing to promote in external scope: {reason}",
            file=sys.stderr,
        )
        return 2

    # Promote moves the source file (via `source.rename(target)` or `git mv`)
    # then writes the new content via `target.write_text(...)`. If `source`
    # is a symlink, the rename moves the SYMLINK, and the subsequent
    # `write_text` follows the link and overwrites the TARGET — for an
    # escaping `.md` symlink, that target lives outside the wiki tree.
    # Refuse outright: promote's mechanic assumes the source is a regular
    # file under owned scope. Symlinked sources point at content we don't
    # own; rewriting them would mutate someone else's content silently.
    if source.is_symlink():
        try:
            source_target_real = source.resolve()
            source_target_real.relative_to(wiki_root.resolve())
            escapes = False
        except (OSError, ValueError):
            escapes = True
        if escapes:
            print(
                f"  ! refusing to promote {rel}: source is a symlink whose "
                "target resolves outside the wiki tree (external content).",
                file=sys.stderr,
            )
        else:
            print(
                f"  ! refusing to promote {rel}: source is a symlink. "
                "Promote moves the link and rewrites via the link, which "
                "would mutate the symlink target unexpectedly. Operate on "
                "the resolved file directly, or replace the symlink with a "
                "regular file first.",
                file=sys.stderr,
            )
        return 2

    target = source.with_suffix("") / "index.md"
    target_dir = target.parent
    target_rel = target.relative_to(wiki_root).as_posix()
    # Validate the derived target path against the same reserved-folder
    # rules `space add` enforces — `promote _meta.md` would otherwise
    # create `_meta/index.md`, which every consumer walker prunes per
    # CONVENTIONS / Reserved top-level folder names. Same producer/
    # consumer break the validator was added to prevent on the `add`
    # surface.
    target_rel_from_wiki = target_dir.relative_to(wiki_root).as_posix()
    ok, why = _validate_rel_path(target_rel_from_wiki)
    if not ok:
        print(
            f"  ! cannot promote {source.relative_to(wiki_root).as_posix()}: "
            f"derived target {target_rel_from_wiki}/ is not a valid space "
            f"path ({why}). Rename the source file first.",
            file=sys.stderr,
        )
        return 2
    if target_dir.exists():
        # Symlink target dir: even if it's empty, `source.rename(target)`
        # would follow the link and write into whatever the symlink
        # resolves to — including outside the wiki tree. Refuse before the
        # rename, mirroring the symlinked-source refusal above.
        if target_dir.is_symlink():
            print(
                f"  ! refusing to promote into {target_dir.relative_to(wiki_root).as_posix()}/: "
                "target directory is a symlink. Promote would follow the "
                "link and create `index.md` at the symlink target, mutating "
                "content the wiki may not own. Remove or rename the symlink "
                "before promoting.",
                file=sys.stderr,
            )
            return 2
        try:
            entries = list(target_dir.iterdir())
        except OSError:
            entries = []
        if entries:
            print(
                f"  ! target {target_dir.relative_to(wiki_root).as_posix()}/ "
                "already exists with content; refusing",
                file=sys.stderr,
            )
            return 2

    ancestor = _nearest_ancestor_space(wiki_root, source)
    ancestor_index = ancestor / "index.md"
    ancestor_text = ancestor_index.read_text(encoding="utf-8")
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    # If the ancestor's `## Spaces` is missing, `_promote_mutate` (below) inserts
    # it inside the locked region — no separate refuse path.

    basename = source.stem
    source_resolved = source.resolve()

    # Contract-walker adapter: `_find_alias_owners` expects a walker
    # returning a flat iterable of `.md` paths. `_walk_md_files_via_contract`
    # yields `(path, is_external)` tuples; adapt by dropping the flag.
    # Per the plan's §S5 stance B, promote's alias checks and link
    # rewrites consume contract-first traversal — files inside
    # unregistered (drift) spaces are NOT consumer-visible and must not
    # be mutated by promote. Audit / `audit --fix` is the surface that
    # touches drift; promote is a consumer-side write that respects the
    # navigation contract.
    def _contract_md_walker(root, *, include_external=False):
        for path, _is_ext in _walk_md_files_via_contract(
            root, include_external=include_external
        ):
            yield path

    if not args.skip_aliases:
        owners = _find_alias_owners(wiki_root, walker=_contract_md_walker)
        collisions = owners.get(basename.casefold(), [])
        external_owners = [p for p in collisions if p.resolve() != source_resolved]
        if external_owners:
            other = external_owners[0].relative_to(wiki_root).as_posix()
            print(
                f"  ! alias collision: {other} already declares alias "
                f"'{basename}' (case-insensitive). "
                "Re-run with --skip-aliases to promote without alias injection.",
                file=sys.stderr,
            )
            return 2

    source_text = source.read_text(encoding="utf-8")
    fm = _md.parse_frontmatter(source_text) or {}
    summary = fm.get("summary")
    if isinstance(summary, list):
        summary = " ".join(summary)
    description = (str(summary).strip() if summary else "") or None

    # Build candidate set from the contract-first md walk. Drift files
    # (in unregistered spaces, hidden, `_archives/`, `_meta/`) are
    # invisible to the consumer per §S5 stance B; promote does not
    # rewrite links inside them. Audit `--fix` is the repair surface
    # for drift visibility; promote stays consumer-aligned.
    all_md_files = list(_contract_md_walker(wiki_root))
    # The source file itself may not be contract-reachable yet (it's a
    # plain `.md` inside its ancestor space); union it in so its own
    # rewrites work.
    candidates: set[Path] = {p.resolve() for p in all_md_files}
    candidates.add(source_resolved)

    # Compute the post-move absolute target (for link rewriting).
    new_target_abs = target
    new_target_wikilink = source.with_suffix("").relative_to(wiki_root).as_posix() + "/index"

    # Plan rewrites across all other owned md files.
    planned: list[tuple[Path, str]] = []
    rewrite_files = 0
    for page in all_md_files:
        if page.resolve() == source_resolved:
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        new_text = _rewrite_links_pointing_at(
            text=text,
            page=page,
            old_target=source_resolved,
            new_target=new_target_abs,
            new_target_wikilink=new_target_wikilink,
            wiki_root=wiki_root,
            candidates=candidates,
        )
        if new_text != text:
            planned.append((page, new_text))
            rewrite_files += 1

    # Plan promoted file's outgoing-link adjustment + ## Spaces + aliases.
    new_source_text = _adjust_outgoing_links_for_depth(
        text=source_text,
        original_file=source,
        new_file=target,
        wiki_root=wiki_root,
    )
    if not _md.has_section(new_source_text, "Spaces"):
        if new_source_text and not new_source_text.endswith("\n"):
            new_source_text += "\n"
        new_source_text += "\n## Spaces\n\n"
    aliases_added = False
    if not args.skip_aliases:
        new_source_text, aliases_added = _md.frontmatter_add_alias(new_source_text, basename)

    rel_from_ancestor = target.parent.relative_to(ancestor)
    label = f"{rel_from_ancestor}/"
    href = f"{rel_from_ancestor}/index.md"

    if args.dry_run:
        print(f"  . (dry-run) would move {rel} -> {target_rel}")
        print(f"  . (dry-run) would rewrite links in {rewrite_files} file(s)")
        if aliases_added:
            print(f"  . (dry-run) would add alias '{basename}' to {target_rel}")
        if not _md.has_section(ancestor_text, "Spaces"):
            print(f"  . (dry-run) would insert `## Spaces` into {printable}index.md")
        print(f"  . (dry-run) would register entry under {printable}index.md ## Spaces")
        return 0

    # PR-L: preflight EVERY projected write against its size cap BEFORE we
    # touch the filesystem. Promote produces several mutations (the new
    # `index.md`, the planned rewrites, the ancestor's `## Spaces` entry);
    # without the preflight, a cap rejection mid-mutation would leave a
    # half-promoted tree. Project the ancestor's `_add_space_entry` result
    # against the OUTER-read ancestor text — a concurrent writer could
    # change ancestor text between this check and `_atomic_mutate_index`,
    # but the in-helper cap check (PR-D) catches that.
    try:
        _enforce_size_cap(target, new_source_text, wiki_root)
        for page, new_text in planned:
            if page.resolve() == ancestor_index.resolve():
                # Skip the standalone ancestor-rewrite preflight here — we
                # project the COMBINED ancestor mutation (rewrite + section
                # insert + entry add) below so the cap is evaluated against
                # the same text the in-lock `_promote_mutate` will write.
                # Checking only the rewrite-alone result here would mask a
                # combined-growth overflow.
                continue
            _enforce_size_cap(page, new_text, wiki_root)
        # Build the projected ancestor text the same way `_promote_mutate`
        # builds it: start from whatever rewrite the planned pass produced
        # for the ancestor (if any), then insert `## Spaces` if missing, then
        # add the new entry. Project against the OUTER-read ancestor_text;
        # the in-lock check at `_promote_mutate` re-evaluates against fresh
        # text to catch any concurrent growth between preflight and lock.
        ancestor_after_rewrite = next(
            (
                new_text
                for page, new_text in planned
                if page.resolve() == ancestor_index.resolve()
            ),
            ancestor_text,
        )
        projected_ancestor = ancestor_after_rewrite
        if not _md.has_section(projected_ancestor, "Spaces"):
            if projected_ancestor and not projected_ancestor.endswith("\n"):
                projected_ancestor += "\n"
            projected_ancestor += "\n## Spaces\n\n"
        projected_ancestor = _add_space_entry(
            projected_ancestor, label, href, description
        )
        _enforce_size_cap(ancestor_index, projected_ancestor, wiki_root)
    except SizeCapExceeded as e:
        print(
            f"  ! size cap: {e}. Aborted before any FS write.",
            file=sys.stderr,
        )
        return 2

    # Snapshot every affected file outside the wiki tree. We DELIBERATELY
    # exclude `ancestor_index` here — it is mutated only inside
    # `_atomic_mutate_index` (lock-protected atomic write). Including a
    # pre-lock snapshot of it in the rollback set would let `_restore_from_snapshot`
    # overwrite concurrent writes that committed between our outer read at
    # the top of `cmd_promote` and the lock acquisition inside the helper.
    ancestor_index_resolved = ancestor_index.resolve()
    snapshot_dir = Path(tempfile.mkdtemp(prefix="wiki-spaces-promote-"))
    try:
        snapshot_set = {source.resolve()}
        for p, _new in planned:
            p_resolved = p.resolve()
            if p_resolved == ancestor_index_resolved:
                continue
            snapshot_set.add(p_resolved)
        wiki_root_resolved = wiki_root.resolve()
        for p in snapshot_set:
            try:
                rel_p = p.relative_to(wiki_root_resolved)
            except ValueError:
                continue
            snap = snapshot_dir / rel_p
            snap.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, snap)

        # Mutate.
        target_dir_pre_existed = target_dir.exists()
        target_dir.mkdir(parents=True, exist_ok=True)
        moved_via_git = False
        if (wiki_root / ".git").exists() and _is_git_tracked(source, wiki_root):
            try:
                subprocess.run(
                    ["git", "mv",
                     str(source.relative_to(wiki_root)),
                     str(target.relative_to(wiki_root))],
                    cwd=wiki_root, check=True, capture_output=True, text=True,
                )
                moved_via_git = True
            except subprocess.CalledProcessError:
                pass
        if not moved_via_git:
            source.rename(target)

        target.write_text(new_source_text, encoding="utf-8")

        # Apply planned rewrites EXCEPT the ancestor's index.md — the ancestor
        # mutation runs under flock via `_atomic_mutate_index`, recomputing
        # link rewrites against the FRESH text it reads inside the lock so a
        # concurrent `space add` can't clobber our rewrites.
        for page, new_text in planned:
            if page.resolve() == ancestor_index_resolved:
                continue
            page.write_text(new_text, encoding="utf-8")

        def _promote_mutate(fresh_text: str) -> tuple:
            text = fresh_text
            inserted = False
            if not _md.has_section(text, "Spaces"):
                if text and not text.endswith("\n"):
                    text += "\n"
                text += "\n## Spaces\n\n"
                inserted = True
            # Re-apply the link rewrite against fresh text so a concurrent
            # writer can't clobber what we just rewrote.
            rewritten = _rewrite_links_pointing_at(
                text=text,
                page=ancestor_index,
                old_target=source_resolved,
                new_target=new_target_abs,
                new_target_wikilink=new_target_wikilink,
                wiki_root=wiki_root,
                candidates=candidates,
            )
            final = _add_space_entry(rewritten, label, href, description)
            entry_added = final != rewritten
            if inserted and entry_added:
                tag = "inserted-and-added"
            elif inserted:
                tag = "inserted"
            elif entry_added:
                tag = "added"
            else:
                tag = "noop"
            # PR-L: the outer preflight projects the cap against the
            # OUTER-read ancestor text. A concurrent writer can grow the
            # ancestor between preflight and lock — re-check inside the
            # locked region against the actual projected text. Cap rejection
            # returns the `(None, rc, reason)` abort tuple per the
            # `_atomic_mutate_index` protocol.
            try:
                _enforce_size_cap(ancestor_index, final, wiki_root)
            except SizeCapExceeded as e:
                return (None, 2, f"size cap: {e}")
            return (final, tag)

        rc_a, info = _atomic_mutate_index(
            ancestor, ancestor_index, _promote_mutate
        )
        if rc_a != 0:
            raise RuntimeError(f"ancestor mutation failed: {info}")
        if info in ("inserted", "inserted-and-added"):
            print(f"  ~ {printable}index.md  +inserted `## Spaces`")
        if info in ("added", "inserted-and-added"):
            print(f"  ~ {printable}index.md ## Spaces  += [{label}]")

        print(f"  + promoted {rel} -> {target_rel}")
        if rewrite_files:
            print(f"  ~ rewrote links in {rewrite_files} file(s)")
        if aliases_added:
            print(f"  ~ added alias '{basename}' to {target_rel}")
        return 0
    except Exception as e:
        print(f"  ! mutation failed mid-promote: {e}", file=sys.stderr)
        try:
            # Undo the move FIRST (if it happened) so the snapshot restore
            # can put the source back at its original path without colliding
            # with the moved file. Then restore other touched files. Then
            # rmdir the (now-empty) target_dir.
            if target.is_file():
                try:
                    target.unlink()
                except OSError as unlink_err:
                    print(
                        f"  ! could not remove partial target {target}: {unlink_err}",
                        file=sys.stderr,
                    )
            _restore_from_snapshot(snapshot_dir, wiki_root)
            # Defect #4: if we ran `git mv`, the staging index is now in a
            # dirty state (the move was staged, but we've put the file back
            # via snapshot restore, so the staged rename doesn't match the
            # working tree). Unstage so `git status` shows a clean tree.
            # Best-effort: a failure here doesn't break the working-tree
            # rollback, only the staging-area cleanup.
            if moved_via_git:
                try:
                    subprocess.run(
                        ["git", "reset", "HEAD",
                         str(source.relative_to(wiki_root)),
                         str(target.relative_to(wiki_root))],
                        cwd=wiki_root, check=False, capture_output=True, text=True,
                    )
                except (subprocess.SubprocessError, FileNotFoundError) as git_err:
                    print(
                        f"  ! could not unstage rolled-back git mv: {git_err}. "
                        "Run `git reset HEAD <paths>` manually if `git status` "
                        "shows phantom rename.",
                        file=sys.stderr,
                    )
            # Only remove target_dir if WE created it. A pre-existing empty
            # directory (e.g., user did `mkdir page/` before running promote)
            # must survive rollback.
            if not target_dir_pre_existed and target_dir.exists():
                try:
                    if not list(target_dir.iterdir()):
                        target_dir.rmdir()
                except OSError:
                    pass  # non-empty or permission issue — leave for the user
            print("  . rolled back from snapshot", file=sys.stderr)
        except Exception as restore_err:
            print(
                f"  ! ROLLBACK ALSO FAILED: {restore_err}. "
                f"Manual recovery from {snapshot_dir} may be required.",
                file=sys.stderr,
            )
        return 2
    finally:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
def cmd_log(args: argparse.Namespace) -> int:
    """Append a structured line to <wiki>/log.md atomically.

    Two forms:
    - Structured (preferred): `space log <OPERATION> --field key=value ...`
      auto-prepends an ISO-8601 UTC timestamp and emits the canonical
      `- [TIMESTAMP] OPERATION key=value ...` format. The LLM never
      formats timestamps by hand.
    - Raw escape hatch: `space log --raw "<full line>"` for callers
      writing a custom shape (skill prose still recommends the structured
      form).

    Opt-in. `log.md` must already exist; pass `--create` on the first
    call if you want the CLI to scaffold it. Without `--create`, an
    absent `log.md` is a refusal — `log.md` is one of the optional
    conventions per CONVENTIONS.md, not a default.

    Race-safe: a single `fcntl.flock` on the log file covers the whole
    check-rotate-append sequence (`_limits.append_log_with_rotation`).
    """
    wiki_root = _resolve_wiki_strict(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    from . import _limits as _limits_module

    log_path = wiki_root / "log.md"
    create_if_missing = bool(getattr(args, "create", False))
    if not log_path.is_file() and not create_if_missing:
        print(
            f"  ! {log_path.relative_to(wiki_root)} does not exist. "
            "Pass --create to scaffold it, or opt in by running "
            "`wiki-spaces init <wiki> --with log.md`.",
            file=sys.stderr,
        )
        return 2
    if create_if_missing and not log_path.is_file():
        # Framework write — enforce the per-file cap. The initial body
        # is tiny (6 bytes) but the v1 contract is "every framework write
        # enforces the cap": a degenerate user-configured `log.md` cap
        # below 6 chars would otherwise leak an over-cap scaffold file
        # onto disk. The actual scaffold write happens INSIDE
        # `append_log_with_rotation`'s lock (race-safe across concurrent
        # first-time --create calls); this is the pre-flight only.
        try:
            _enforce_size_cap(log_path, "# Log\n", wiki_root)
        except SizeCapExceeded as e:
            print(f"  ! size cap: {e}", file=sys.stderr)
            return 2

    if args.raw is not None:
        message = args.raw
    else:
        if not args.operation:
            print(
                "  ! pass an OPERATION (e.g. `space log SEARCH --field "
                "query=...`) or use --raw \"<line>\".",
                file=sys.stderr,
            )
            return 2
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = [f"- [{ts}]", args.operation.upper()]
        for kv in (args.field or []):
            if "=" not in kv:
                print(
                    f"  ! --field expects key=value, got {kv!r}",
                    file=sys.stderr,
                )
                return 2
            k, v = kv.split("=", 1)
            parts.append(f"{k}={v}")
        message = " ".join(parts)

    limits = _limits_module.read_limits(wiki_root)
    cap = _limits_module.cap_for(log_path, wiki_root, limits)
    try:
        archive = _limits_module.append_log_with_rotation(
            log_path,
            message,
            cap=cap,
            wiki_root=wiki_root,
            limits=limits,
            create_if_missing=create_if_missing,
        )
    except FileNotFoundError as e:
        # Race: log.md was deleted between our check above and the lock
        # acquisition inside the helper. Surface and bail.
        print(f"  ! {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        # PR-M / §26: rotation could not free enough space for the entry.
        # Surface to the user; rotation/trim is their decision.
        print(f"  ! {e}", file=sys.stderr)
        return 1
    if archive is not None:
        print(f"  ~ {log_path.relative_to(wiki_root)} rotated → {archive.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wiki-spaces space",
        description="Manage spaces and the ## Spaces navigation contract.",
    )
    parser.add_argument(
        "--wiki",
        type=Path,
        help="explicit wiki root (defaults to the configured wiki)",
    )
    sub = parser.add_subparsers(dest="op", required=True)

    p_add = sub.add_parser("add", help="create a space and register it with the nearest ancestor")
    p_add.add_argument("path", help="path relative to the wiki root (e.g. projects/acme)")
    p_add.add_argument("--name", help="display name (default: directory basename)")
    p_add.add_argument(
        "--description",
        help="one-paragraph description for the new space's index.md",
    )
    p_add.add_argument(
        "--force-index",
        action="store_true",
        help="overwrite an existing index.md at the target",
    )
    p_add.add_argument(
        "--force-external",
        action="store_true",
        help="permit operating on a space outside the owned tree "
        "(under shared/, a foreign-origin submodule, or a symlink "
        "that escapes the wiki). Default: refuse.",
    )
    p_add.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing. Includes the chain-helper "
        "preview (which ancestors would have `## Spaces` inserted and which "
        "would gain the new entry).",
    )
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="delete a space and unregister it")
    p_remove.add_argument("path", help="path relative to the wiki root")
    p_remove.add_argument(
        "--force",
        action="store_true",
        help="remove even when the space contains files other than index.md",
    )
    p_remove.add_argument(
        "--force-external",
        action="store_true",
        help="permit operating on a space outside the owned tree "
        "(under shared/, a foreign-origin submodule, or a symlink "
        "that escapes the wiki). Default: refuse.",
    )
    p_remove.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing",
    )
    p_remove.set_defaults(func=cmd_remove)

    p_audit = sub.add_parser(
        "audit",
        help="report ## Spaces drift, broken wikilinks, size violations, and orphan pages",
    )
    p_audit.add_argument(
        "--include-external",
        action="store_true",
        help="also walk externally-classified spaces (under shared/, foreign "
        "submodules, escaping symlinks). Trust scope per AGENTS.md says read "
        "ops can opt in to externals; this flag is the opt-in. Plumbed "
        "through both the drift walker and the broken-link walker so the two "
        "checks always agree on scope.",
    )
    p_audit.add_argument(
        "--fix",
        action="store_true",
        help="repair drift in place: insert `## Spaces` into any owned space "
        "missing it, then register every on-disk child that's not yet listed "
        "in its ancestor's `## Spaces`. Touches the filesystem. Use without "
        "`--fix` to preview the same report read-only.",
    )
    p_audit.add_argument(
        "--remove-stale",
        action="store_true",
        help="with `--fix`: also remove `## Spaces` entries whose target "
        "doesn't exist on disk. Externally-classified entries require "
        "`--include-external` together with `--remove-stale` so legitimate "
        "external mounts are not silently unregistered.",
    )
    p_audit.add_argument(
        "--json",
        action="store_true",
        help="emit findings as JSON (drift, missing `## Spaces` section, "
        "broken wikilinks, size violations, approaching cap, orphans, "
        "malformed entries, duplicate aliases). Skills consume this "
        "rather than parsing the human-readable output.",
    )
    p_audit.set_defaults(func=cmd_audit)

    p_mount = sub.add_parser(
        "mount",
        help="mount an external space (clone/submodule/symlink) and register it",
    )
    p_mount.add_argument("source", help="git URL, or local path, of the space to mount")
    p_mount.add_argument(
        "path",
        nargs="?",
        default=None,
        help="destination path relative to the wiki root (e.g. shared/team-foo). "
        "Optional; default is shared/<basename-of-source>/ — the shared/ prefix "
        "opts the mount into external trust-scope semantics.",
    )
    p_mount.add_argument(
        "--mode",
        dest="mechanism",
        required=True,
        choices=("submodule", "clone", "symlink"),
        help="mount mechanism: submodule (collaborative, push changes back), "
        "clone (read-only one-time copy), symlink (local folder)",
    )
    p_mount.add_argument(
        "--description", help="one-line description for the `## Spaces` entry"
    )
    p_mount.add_argument(
        "--name",
        help="override the `## Spaces` entry label (default: <relative-path>/)",
    )
    p_mount.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing",
    )
    p_mount.set_defaults(func=cmd_mount)

    p_check_size = sub.add_parser(
        "check-size",
        help="print a size-cap verdict for a projected post-write text",
    )
    p_check_size.add_argument(
        "path",
        help="wiki-root-relative path of the target file (used to look up "
        "the per-pattern cap from `_meta/limits.md`).",
    )
    p_check_size.add_argument(
        "--projected-file",
        help="read the projected post-write content from this file.",
    )
    p_check_size.add_argument(
        "--projected-stdin",
        action="store_true",
        help="read the projected post-write content from stdin.",
    )
    p_check_size.set_defaults(func=cmd_check_size)

    p_list = sub.add_parser(
        "list",
        help="list spaces reachable via the `## Spaces` contract",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        help="emit `{path, label, description, external}` per space.",
    )
    p_list.add_argument(
        "--include-external",
        action="store_true",
        help="include externally-classified spaces (under shared/, foreign "
        "submodules, escaping symlinks).",
    )
    p_list.add_argument(
        "--include-boundaries",
        action="store_true",
        help="also surface external boundary folders that lack `index.md` "
        "(foreign submodules without one, escaping symlinks). Requires "
        "--include-external. Used by the placement classifier to enumerate "
        "every external path to exclude.",
    )
    p_list.set_defaults(func=cmd_list)

    p_files = sub.add_parser(
        "files",
        help="list .md files reachable via the `## Spaces` contract",
    )
    p_files.add_argument(
        "space",
        nargs="?",
        help="wiki-root-relative space to scope to (must be contract-"
        "reachable). Omit to list every file in the wiki.",
    )
    p_files.add_argument(
        "--json",
        action="store_true",
        help="emit `{path, external}` per file.",
    )
    p_files.add_argument(
        "--include-external",
        action="store_true",
        help="include files inside externally-classified spaces.",
    )
    p_files.set_defaults(func=cmd_files)

    p_promote = sub.add_parser(
        "promote",
        help="promote a .md file to a nested space (foo.md -> foo/index.md)",
    )
    p_promote.add_argument(
        "path",
        help="wiki-root-relative path to a .md file (not index.md)",
    )
    p_promote.add_argument(
        "--skip-aliases",
        action="store_true",
        help="do not inject aliases: [<basename>] into the new index.md "
        "(escape hatch when another page already claims the alias)",
    )
    p_promote.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan + link-rewrite counts; touch nothing",
    )
    p_promote.set_defaults(func=cmd_promote)

    p_log = sub.add_parser(
        "log",
        help="append a structured line to <wiki>/log.md atomically",
    )
    p_log.add_argument(
        "operation",
        nargs="?",
        help="operation name (uppercased; e.g. SEARCH, UPDATE, TEND). "
        "Required unless --raw is passed.",
    )
    p_log.add_argument(
        "--field",
        action="append",
        metavar="KEY=VALUE",
        help="repeatable `key=value` pair appended to the entry. "
        "Example: --field query=\"sourdough\" --field result_pages=3.",
    )
    p_log.add_argument(
        "--raw",
        help="bypass structured formatting; append the given string verbatim "
        "as the entry (CLI does NOT prepend a timestamp).",
    )
    p_log.add_argument(
        "--create",
        action="store_true",
        help="scaffold an empty `log.md` if one does not exist yet. Without "
        "this flag an absent `log.md` is a refusal — logging is opt-in.",
    )
    p_log.set_defaults(func=cmd_log)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
