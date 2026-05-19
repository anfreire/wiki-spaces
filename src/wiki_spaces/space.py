"""`wiki-spaces space` subcommands: add, remove, audit.

Maintains the `## Spaces` exhaustiveness contract automatically so users
never edit ancestor `index.md` files by hand to track child spaces.

Operations:
- `space add <rel-path>`     create a new space and register it in the
                             nearest ancestor's `## Spaces`. **Atomically
                             refuses** when the ancestor has no `## Spaces`
                             section (Tier 1 parent) — exits non-zero,
                             touches nothing. The LLM/skill layer handles
                             upgrading Tier 1 parents.
- `space remove <rel-path>`  symmetric: refuses when ancestor is Tier 1.
                             Otherwise removes the entry and the directory.
                             Refuses without `--force` when the space
                             contains content beyond `index.md`.
- `space audit`              walk owned spaces; report drift between actual
                             direct child spaces and listed `## Spaces`
                             entries. Always-on summary header (tier
                             breakdown, page count, conventions detected).

Trust scope: writes stay inside the wiki tree. External spaces (per the
heuristic in CONVENTIONS.md / Owned vs external) are skipped on traversal.
"""

from __future__ import annotations

import argparse
import sys
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


def _walk_owned_spaces(wiki_root: Path):
    """Yield every owned space under wiki_root (inclusive). External skipped.

    Tracks resolved realpaths to break symlink cycles; broken symlinks and
    unreadable directories are skipped silently.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return
    visited: set[Path] = {root_real}
    yield wiki_root
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
            if _is_external(child, wiki_root):
                continue
            try:
                child_real = child.resolve()
            except OSError:
                continue
            if child_real in visited:
                continue
            visited.add(child_real)
            if (child / "index.md").is_file():
                yield child
            stack.append(child)


def _new_index_md(name: str, description: str) -> str:
    """Tier-2 index.md body for a freshly created space.

    Includes an empty `## Spaces` heading so the navigability contract is live
    from t=0 (matches `init_wiki.build_index_md`). Keeps the "CLI-rolled spaces
    are Tier 2" mental model consistent — `space add foo/bar` works on a fresh
    `foo` without a second upgrade step.
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

    # Atomic refuse when the contract is absent — the LLM/skill layer handles
    # Tier-1 parents, not the CLI. Keeps `space add` all-or-nothing.
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
            f"    Add `## Spaces` to {printable}index.md first (the parent is "
            "currently Tier 1). See AGENTS.md / Tiers for the contract.",
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
            f"    The parent is Tier 1; there is no contract to update. Add "
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
    import shutil
    shutil.rmtree(target)
    print(f"  - {rel}/")
    return 0


def _count_owned_pages(wiki_root: Path) -> int:
    """Count markdown files inside owned scope, walking the tree directory by
    directory so external mounts at ANY depth are pruned. A simple `rglob`
    plus top-level filter misses a foreign submodule at `projects/external/`
    because `projects/` itself is owned. Mirrors `_walk_owned_spaces`'s
    realpath-visited guard so in-tree symlink cycles can't hang the audit.
    """
    count = 0
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return 0
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
                count += 1
                continue
            if not entry.is_dir():
                continue
            if name.startswith(".") or name == "_archives":
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
    return count


def _summary_header(wiki_root: Path, all_spaces: list[Path]) -> list[str]:
    tier2 = sum(
        1 for s in all_spaces
        if _md.has_section((s / "index.md").read_text(encoding="utf-8"), "Spaces")
    )
    tier1 = len(all_spaces) - tier2

    convention_files = [
        "log.md", "_meta/taxonomy.md", ".manifest.json",
        "hot.md", "_template.md", ".obsidian",
    ]
    present = [c for c in convention_files if (wiki_root / c).exists()]

    pages = _count_owned_pages(wiki_root)

    lines = [
        f"wiki: {wiki_root}",
        f"  spaces: {len(all_spaces)} ({tier2} with `## Spaces`, {tier1} Tier 1)",
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

    if issues == 0:
        print("OK: no `## Spaces` drift")
        return 0
    print(f"\n{issues} issue(s) found. Re-run after fixing, or use "
          "`wiki-spaces space add/remove` to update individual entries.")
    return 1


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
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="delete a space and unregister it")
    p_remove.add_argument("path", help="path relative to the wiki root")
    p_remove.add_argument(
        "--force",
        action="store_true",
        help="remove even when the space contains files other than index.md",
    )
    p_remove.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing",
    )
    p_remove.set_defaults(func=cmd_remove)

    p_audit = sub.add_parser("audit", help="report ## Spaces drift across the wiki")
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
