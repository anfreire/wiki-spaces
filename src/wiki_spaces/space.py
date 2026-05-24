"""`wiki-spaces space` subcommands: add, remove, audit.

Maintains the `## Spaces` exhaustiveness contract automatically so users
never edit ancestor `index.md` files by hand to track child spaces.

Operations:
- `space add <rel-path>`     create a new space and register it in the
                             nearest ancestor's `## Spaces`. **Atomically
                             refuses** when the ancestor has no `## Spaces`
                             section — exits non-zero, touches nothing. The
                             LLM/skill layer handles adding the section.
- `space remove <rel-path>`  symmetric: refuses when the ancestor has no
                             `## Spaces` section. Otherwise removes the
                             entry and the directory. Refuses without
                             `--force` when the space contains content
                             beyond `index.md`.
- `space mount <src> [path]` mount an external space — git clone, git
                             submodule, or symlink (`--mode`) — verify it has
                             `index.md`, and register it in the nearest
                             ancestor's `## Spaces`. Refuses when the
                             ancestor has no `## Spaces` section, like
                             `space add`. `path` is optional; defaults to
                             `shared/<basename-of-source>/`. Use `--dry-run`
                             to preview; `--name` to override the registered
                             label; `--as` is a deprecated alias for
                             `--mode` (removed in v1.1).
- `space audit`              walk owned spaces; report `## Spaces` drift,
                             broken `[[wikilinks]]`, and orphan pages.
                             Always-on summary header (space count, page
                             count, conventions detected). Drift and broken
                             links set a non-zero exit; orphans are
                             informational and do not.

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
from ._common import nearest_space_root, wiki_path


DEFAULT_DESCRIPTION = "<one paragraph describing this space>"


# ---------- Helpers ----------

def _resolve_wiki(explicit: Path | None = None) -> Path | None:
    """Resolve the wiki root: explicit, then config, then nearest CWD ancestor.

    The CWD fallback lets users operate on whatever wiki they're inside
    without a config first — `wiki-spaces space audit` in any wiki tree
    just works.
    """
    if explicit:
        p = explicit.expanduser().resolve()
        return p if (p / "index.md").is_file() else None
    cfg_wiki = wiki_path()
    if cfg_wiki is not None:
        p = cfg_wiki.expanduser().resolve()
        if (p / "index.md").is_file():
            return p
    return nearest_space_root()


def _validate_rel_path(rel: str) -> tuple[bool, str | None]:
    """Validate a user-provided relative path. (ok, error_message).

    Same rule as `init --folders`: reject empty, `.`, `..`, and `.git`
    segments. Other hidden names (`.archive`, `.config`, etc.) are allowed.
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


def _walk_classified(wiki_root: Path):
    """Yield every space under wiki_root, classified as owned or external.

    Yields `(path, classification, reason)` where:
    - `classification` is `"owned"` or `"external"`.
    - `reason` is None for owned, or a short string for external.

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
    stack = [wiki_root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for child in entries:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name == "_archives":  # retired content — out of audit scope
                continue
            try:
                child_real = child.resolve()
            except OSError:
                continue
            if child_real in visited:
                continue
            visited.add(child_real)
            if _is_external(child, wiki_root):
                # Surface every external boundary (with or without index.md)
                # so callers like `init --adopt` can emit per-skip notices
                # for the user-visible directory, not the deeper space
                # (which the user may not even know is there). External
                # subtrees are NOT descended into (trust scope).
                reason = _external_reason(child, wiki_root)
                yield (child, "external", reason)
                continue
            if (child / "index.md").is_file():
                yield (child, "owned", None)
            stack.append(child)


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


def _walk_owned_spaces(wiki_root: Path):
    """Yield every owned space under wiki_root (inclusive). External skipped.

    Thin filter over `_walk_classified`. Preserves the original signature for
    callers that don't need external classification.
    """
    for path, classification, _reason in _walk_classified(wiki_root):
        if classification == "owned":
            yield path


def _new_index_md(name: str, description: str) -> str:
    """index.md body for a freshly created space.

    Includes an empty `## Spaces` heading so the navigation contract is live
    from t=0 (matches `init_wiki.build_index_md`). Every CLI-rolled space gets
    a `## Spaces` section, so `space add foo/bar` works on a fresh `foo`
    without a second step.
    """
    return (
        f"# {name}\n\n## What this space is\n\n{description}\n\n## Spaces\n\n"
    )


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
    wiki_root = _resolve_wiki(args.wiki)
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

    # Atomic refuse when the navigation contract is absent — the LLM/skill
    # layer handles adding the `## Spaces` section, not the CLI. Keeps
    # `space add` all-or-nothing.
    ancestor = _nearest_ancestor_space(wiki_root, new_space)
    ancestor_index = ancestor / "index.md"
    text = ancestor_index.read_text(encoding="utf-8")
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    if not _md.has_section(text, "Spaces"):
        print(
            f"  ! cannot register {rel}/: nearest ancestor {printable}index.md "
            "has no `## Spaces` section.",
            file=sys.stderr,
        )
        print(
            f"    Add `## Spaces` to {printable}index.md first, then retry. "
            "See AGENTS.md for the navigation contract.",
            file=sys.stderr,
        )
        return 2

    # Already exists?
    already_space = (new_space / "index.md").is_file()
    if already_space and not args.force_index:
        print(f"  . {rel}/ already a space; ensuring ancestor entry")
    else:
        new_space.mkdir(parents=True, exist_ok=True)
        display_name = args.name or new_space.name
        description = (args.description or DEFAULT_DESCRIPTION).strip()
        (new_space / "index.md").write_text(
            _new_index_md(display_name, description), encoding="utf-8"
        )
        print(f"  + {rel}/index.md")

    rel_from_ancestor = new_space.relative_to(ancestor)
    label = f"{rel_from_ancestor}/"
    href = f"{rel_from_ancestor}/index.md"
    new_text = _add_space_entry(text, label, href, args.description)
    if new_text == text:
        print(f"  . entry for {label} already in ancestor's ## Spaces")
        return 0
    ancestor_index.write_text(new_text, encoding="utf-8")
    print(f"  ~ {printable}index.md ## Spaces  += [{label}]")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    wiki_root = _resolve_wiki(args.wiki)
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
    text = ancestor_index.read_text(encoding="utf-8")
    rel_from_ancestor = target.relative_to(ancestor)
    href = f"{rel_from_ancestor}/index.md"
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    if not _md.has_section(text, "Spaces"):
        print(
            f"  ! cannot remove {rel}/: nearest ancestor {printable}index.md "
            "has no `## Spaces` section.",
            file=sys.stderr,
        )
        print(
            f"    No `## Spaces` section means no entry to remove. Add "
            f"`## Spaces` to {printable}index.md first, or remove the directory "
            "manually to bypass the contract.",
            file=sys.stderr,
        )
        return 2

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

    new_text = _remove_space_entry(text, href)
    if new_text != text:
        if args.dry_run:
            print(f"  ~ (dry-run) {printable}index.md ## Spaces  -= [{rel_from_ancestor}/]")
        else:
            ancestor_index.write_text(new_text, encoding="utf-8")
            print(f"  ~ {printable}index.md ## Spaces  -= [{rel_from_ancestor}/]")

    if args.dry_run:
        print(f"  . (dry-run) would remove {rel}/")
        return 0
    shutil.rmtree(target)
    print(f"  - {rel}/")
    return 0


def _walk_owned_md_files(wiki_root: Path) -> list[Path]:
    """Return every markdown file inside owned scope.

    Walks the tree directory by directory so external mounts at ANY depth are
    pruned — a foreign submodule at `projects/external/` is skipped even
    though `projects/` itself is owned (a plain `rglob` plus top-level filter
    would miss it). Mirrors `_walk_owned_spaces`'s realpath-visited guard so
    in-tree symlink cycles can't hang the walk. Hidden directories and
    `_archives/` are excluded.
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
                out.append(entry)
                continue
            if not entry.is_dir():
                continue
            if name.startswith(".") or name == "_archives":
                continue
            if name.startswith("wiki-spaces-promote-"):
                # Defensive: snapshots used by `space promote` live in /tmp, but
                # if a leftover snapshot dir ever appears under the wiki, skip it.
                continue
            if _is_external(entry, wiki_root):
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


def _count_owned_pages(wiki_root: Path) -> int:
    """Count markdown files inside owned scope (see `_walk_owned_md_files`)."""
    return len(_walk_owned_md_files(wiki_root))


def _audit_content(wiki_root: Path) -> tuple[list[tuple[Path, str]], list[Path]]:
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
    md_files = _walk_owned_md_files(wiki_root)

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


def _summary_header(wiki_root: Path, all_spaces: list[Path]) -> list[str]:
    convention_files = [
        "log.md", "_meta/taxonomy.md", ".manifest.json",
        "hot.md", "_template.md", ".obsidian",
    ]
    present = [c for c in convention_files if (wiki_root / c).exists()]

    pages = _count_owned_pages(wiki_root)

    lines = [
        f"wiki: {wiki_root}",
        f"  spaces: {len(all_spaces)}",
        f"  pages:  {pages} markdown files (owned scope; excludes hidden / _archives / external)",
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
    wiki_root = _resolve_wiki(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    all_spaces = list(_walk_owned_spaces(wiki_root))
    for line in _summary_header(wiki_root, all_spaces):
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
    for space in all_spaces:
        text = (space / "index.md").read_text(encoding="utf-8")
        if not _md.has_section(text, "Spaces"):
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

    drift_issues = issues
    broken, orphans = _audit_content(wiki_root)

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
    md_files = _walk_owned_md_files(wiki_root)
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

    if orphans:
        print(
            f"\norphans: {len(orphans)} page(s) with no incoming wikilinks "
            "(informational — a page may be standalone on purpose):"
        )
        for page in orphans:
            print(f"  . <wiki>/{page.relative_to(wiki_root)}")

    # Orphans and approaching-cap are facts, not errors — they never flip the
    # exit code. `## Spaces` drift, broken wikilinks, and over-cap size
    # violations do.
    errors = drift_issues + len(broken) + len(over_cap)
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
    if broken:
        parts.append(f"{len(broken)} broken wikilink(s)")
    if over_cap:
        parts.append(f"{len(over_cap)} size violation(s)")
    print(
        f"{errors} issue(s) found: {' + '.join(parts)}. Re-run after fixing, "
        "or use `wiki-spaces space add/remove` for `## Spaces` entries, "
        "and `space promote --split` (or shrink) for size violations."
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
    wiki_root = _resolve_wiki(args.wiki)
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

    rel = path_arg.strip().rstrip("/")
    dest = wiki_root / rel
    if dest.exists() or dest.is_symlink():
        print(
            f"  ! {rel} already exists; choose a path that does not exist yet",
            file=sys.stderr,
        )
        return 2

    # Atomic refuse when the navigation contract is absent — the same
    # `## Spaces` requirement as `space add`, checked before any filesystem
    # work so a refusal is a no-op.
    ancestor = _nearest_ancestor_space(wiki_root, dest)
    ancestor_index = ancestor / "index.md"
    text = ancestor_index.read_text(encoding="utf-8")
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    if not _md.has_section(text, "Spaces"):
        print(
            f"  ! cannot register {rel}/: nearest ancestor {printable}index.md "
            "has no `## Spaces` section.",
            file=sys.stderr,
        )
        print(
            f"    Add `## Spaces` to {printable}index.md first, then retry. "
            "See AGENTS.md for the navigation contract.",
            file=sys.stderr,
        )
        return 2

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
    # A symlink or clone is cleaned up; a submodule cannot be auto-undone
    # safely (`submodule add` already staged a gitlink and edited
    # .gitmodules), so the exact recovery commands are printed instead.
    if not (dest / "index.md").is_file():
        print(
            f"  ! mounted {rel}/ has no index.md — it is not a wiki-spaces "
            "space, so it was not registered in `## Spaces`.",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism)
        return 1

    # Register in the nearest ancestor's `## Spaces`. Atomicity:
    # - Lock the ancestor directory (its inode is stable across our write).
    # - Re-read the index fresh (a concurrent op could have changed it).
    # - Write via tempfile + os.replace (the pattern from cmd_promote).
    # - If write fails for any reason, roll back the mount.
    rc, message = _atomic_register_in_spaces(
        ancestor, ancestor_index, label, href, args.description
    )
    if rc != 0:
        print(f"  ! registration failed: {message}", file=sys.stderr)
        _rollback_mount(wiki_root, dest, rel, mechanism)
        return rc
    if message == "added":
        print(f"  ~ {printable}index.md ## Spaces  += [{label}]")
    else:  # "noop"
        print(f"  . entry for {label} already in ancestor's ## Spaces")
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


def _atomic_register_in_spaces(
    ancestor: Path,
    ancestor_index: Path,
    label: str,
    href: str,
    description: str | None,
) -> tuple[int, str]:
    """Atomically add a `## Spaces` entry under an `fcntl.flock` on the ancestor dir.

    Returns `(0, "added")` when the entry was inserted, `(0, "noop")` when it
    was already present, `(1, "<reason>")` on write failure (caller should
    roll back the mount), `(2, "<reason>")` when the index lost its
    `## Spaces` section between the initial contract check and the lock
    acquisition (concurrent removal — caller should roll back).

    Locking note: the lock is on the ANCESTOR DIRECTORY's inode (stable
    across our `os.replace` of the index file), so two concurrent CLI mounts
    serialize correctly. Within the lock we re-read the index from disk to
    pick up any changes that committed after our caller's initial read.
    """
    dir_fd = os.open(str(ancestor), os.O_RDONLY)
    try:
        fcntl.flock(dir_fd, fcntl.LOCK_EX)
        fresh_text = ancestor_index.read_text(encoding="utf-8")
        if not _md.has_section(fresh_text, "Spaces"):
            return 2, "ancestor `## Spaces` section disappeared between contract check and registration"
        new_text = _add_space_entry(fresh_text, label, href, description)
        if new_text == fresh_text:
            return 0, "noop"
        # Write via tempfile + os.replace (same pattern as cmd_promote).
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
        return 0, "added"
    finally:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(dir_fd)


def _find_alias_owners(wiki_root: Path) -> dict[str, list[Path]]:
    """Build `{alias.casefold(): [pages]}` across owned wiki files.

    Used by `cmd_promote`'s collision preflight. Pages with no frontmatter
    or no `aliases:` contribute nothing. A single page declaring the same
    alias in multiple cases (e.g. `aliases: [bar, BAR]`) appears once.
    """
    out: dict[str, list[Path]] = {}
    for page in _walk_owned_md_files(wiki_root):
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
    replacements: list[tuple[int, int, str]] = []
    for link in _md.parse_markdown_links(text):
        resolved = _md.resolve_markdown_link(link.href, page, wiki_root)
        if resolved is None or resolved != old_target:
            continue
        new_href = _md.compute_relative_link(new_target, page)
        new_substring = f"[{link.label}]({new_href}{link.anchor})"
        replacements.append((link.span[0], link.span[1], new_substring))

    for wl in _md.parse_wikilink_full(text):
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
    replacements: list[tuple[int, int, str]] = []
    for link in _md.parse_markdown_links(text):
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


def cmd_promote(args: argparse.Namespace) -> int:
    wiki_root = _resolve_wiki(args.wiki)
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

    target = source.with_suffix("") / "index.md"
    target_dir = target.parent
    target_rel = target.relative_to(wiki_root).as_posix()
    if target_dir.exists():
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
    if not _md.has_section(ancestor_text, "Spaces"):
        print(
            f"  ! cannot register the promoted space: nearest ancestor "
            f"{printable}index.md has no `## Spaces` section.",
            file=sys.stderr,
        )
        print(
            f"    Add `## Spaces` to {printable}index.md first, then retry. "
            "See AGENTS.md for the navigation contract.",
            file=sys.stderr,
        )
        return 2

    basename = source.stem
    source_resolved = source.resolve()

    if not args.skip_aliases:
        owners = _find_alias_owners(wiki_root)
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

    # Build candidate set from the current owned md tree.
    all_md_files = list(_walk_owned_md_files(wiki_root))
    candidates: set[Path] = {p.resolve() for p in all_md_files}

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

    if args.dry_run:
        print(f"  . (dry-run) would move {rel} -> {target_rel}")
        print(f"  . (dry-run) would rewrite links in {rewrite_files} file(s)")
        if aliases_added:
            print(f"  . (dry-run) would add alias '{basename}' to {target_rel}")
        print(f"  . (dry-run) would register entry under {printable}index.md ## Spaces")
        return 0

    # Snapshot every affected file outside the wiki tree.
    snapshot_dir = Path(tempfile.mkdtemp(prefix="wiki-spaces-promote-"))
    try:
        snapshot_set = {source.resolve(), ancestor_index.resolve()}
        for p, _new in planned:
            snapshot_set.add(p.resolve())
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

        # Apply planned rewrites EXCEPT the ancestor's index.md — we need to
        # fold the entry-add into the same write so link rewrites aren't
        # clobbered by a later `_add_space_entry(stale_text)`.
        ancestor_index_resolved = ancestor_index.resolve()
        ancestor_text_after_rewrites = ancestor_text
        for page, new_text in planned:
            if page.resolve() == ancestor_index_resolved:
                ancestor_text_after_rewrites = new_text
                continue
            page.write_text(new_text, encoding="utf-8")

        rel_from_ancestor = target.parent.relative_to(ancestor)
        label = f"{rel_from_ancestor}/"
        href = f"{rel_from_ancestor}/index.md"
        new_ancestor_text = _add_space_entry(
            ancestor_text_after_rewrites, label, href, description
        )
        if new_ancestor_text != ancestor_text:
            ancestor_index.write_text(new_ancestor_text, encoding="utf-8")
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
    """Append a line to <wiki>/log.md atomically, rotating if over cap.

    Race-safe: a single `fcntl.flock` on the log file covers the whole
    check-rotate-append sequence (`_limits.append_log_with_rotation`).
    Skills call this rather than writing `log.md` directly so concurrent
    skill invocations never lose lines.
    """
    wiki_root = _resolve_wiki(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    from . import _limits as _limits_module

    log_path = wiki_root / "log.md"
    limits = _limits_module.read_limits(wiki_root)
    cap = _limits_module.cap_for(log_path, wiki_root, limits)

    # Ensure the message ends with a newline; preserve the user's text otherwise.
    message = args.message
    archive = _limits_module.append_log_with_rotation(log_path, message, cap=cap)
    if archive is not None:
        print(f"  ~ {log_path.relative_to(wiki_root)} rotated → {archive.name}")
    return 0


_MANIFEST_INT_FIELDS = {"pages_in_vault"}
_MANIFEST_NULLABLE_FIELDS = {"last_commit_synced"}


def _coerce_manifest_value(key: str, raw: str, as_json: bool):
    """Convert a CLI value string to the right type for the manifest schema.

    - With `--json`: parse `raw` as a JSON literal (`42`, `null`, `"foo"`).
    - Without: documented typed fields get light coercion (int for
      `pages_in_vault`; `null` for `last_commit_synced` when the user
      types the literal "null"). Everything else is treated as a string.
    """
    if as_json:
        return json.loads(raw)
    if key in _MANIFEST_INT_FIELDS:
        return int(raw)
    if key in _MANIFEST_NULLABLE_FIELDS and raw.strip().lower() == "null":
        return None
    return raw


def cmd_manifest_set(args: argparse.Namespace) -> int:
    """Atomic read-modify-write on `<wiki>/.manifest.json` under flock.

    Reads `.manifest.json` (creates with `{"projects": {}}` when missing),
    sets `projects[<project>][<key>] = <value>` (typed per the schema or
    `--json`), writes via tempfile + `os.replace`. POSIX-only locking;
    on Windows, best-effort (a one-time stderr notice per process).
    """
    wiki_root = _resolve_wiki(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    try:
        value = _coerce_manifest_value(args.key, args.value, args.json)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  ! invalid value for {args.key!r}: {e}", file=sys.stderr)
        return 2

    manifest_path = wiki_root / ".manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Open with O_RDWR | O_CREAT so we can lock; flock POSIX-only.
    fd = os.open(manifest_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if sys.platform != "win32":
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            # Best-effort on Windows. Reuses the one-time notice helper
            # from `_limits` to avoid duplicate stderr spam.
            from . import _limits as _limits_module
            _limits_module._maybe_emit_win32_notice()

        # Read current contents (treat empty file as the default schema).
        raw = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
        if raw.strip():
            try:
                manifest = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                print(
                    f"  ! {manifest_path.relative_to(wiki_root)} is not valid JSON ({e}); refusing to overwrite",
                    file=sys.stderr,
                )
                return 2
            if not isinstance(manifest, dict) or "projects" not in manifest:
                # Treat malformed/missing-projects as a reset.
                manifest = {"projects": {}}
        else:
            manifest = {"projects": {}}

        projects = manifest.setdefault("projects", {})
        if not isinstance(projects, dict):
            projects = {}
            manifest["projects"] = projects
        proj_entry = projects.setdefault(args.project, {})
        if not isinstance(proj_entry, dict):
            proj_entry = {}
            projects[args.project] = proj_entry
        proj_entry[args.key] = value

        # Atomic write via tempfile + os.replace.
        new_text = json.dumps(manifest, indent=2) + "\n"
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.tmp-",
            dir=str(manifest_path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_text)
            os.replace(tmp_path, manifest_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        print(f"  ~ {manifest_path.relative_to(wiki_root)}: projects[{args.project!r}][{args.key!r}] = {value!r}")
        return 0
    finally:
        try:
            if sys.platform != "win32":
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
        help="report ## Spaces drift, broken wikilinks, and orphan pages",
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
    mech_group = p_mount.add_mutually_exclusive_group(required=True)
    mech_group.add_argument(
        "--mode",
        dest="mechanism",
        choices=("submodule", "clone", "symlink"),
        help="mount mechanism: submodule (collaborative, push changes back), "
        "clone (read-only one-time copy), symlink (local folder)",
    )
    mech_group.add_argument(
        "--as",
        dest="mechanism",
        choices=("submodule", "clone", "symlink"),
        help=argparse.SUPPRESS,  # deprecated alias for --mode; removed in v0.9.0
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
        help="append a line to <wiki>/log.md atomically (rotates if over cap)",
    )
    p_log.add_argument(
        "message",
        help="the log line to append. Convention per CONVENTIONS / log.md: "
        "`- [TIMESTAMP] OPERATION key=value ...`",
    )
    p_log.set_defaults(func=cmd_log)

    p_manifest = sub.add_parser(
        "manifest",
        help="manage <wiki>/.manifest.json (project sync state)",
    )
    manifest_sub = p_manifest.add_subparsers(dest="manifest_op", required=True)
    p_manifest_set = manifest_sub.add_parser(
        "set",
        help="atomic read-modify-write of one manifest field",
    )
    p_manifest_set.add_argument(
        "project",
        help="project slug under `projects.<project>`",
    )
    p_manifest_set.add_argument(
        "key",
        help="manifest field name (e.g. last_synced, last_commit_synced, pages_in_vault, source_cwd)",
    )
    p_manifest_set.add_argument(
        "value",
        help="new value. Treated as a string by default; pass --json to parse "
        "as a JSON literal (42, null, \"foo\"). Documented typed fields get "
        "light coercion (int for pages_in_vault; the literal 'null' for "
        "last_commit_synced).",
    )
    p_manifest_set.add_argument(
        "--json",
        action="store_true",
        help="parse <value> as a JSON literal instead of a string",
    )
    p_manifest_set.set_defaults(func=cmd_manifest_set)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
