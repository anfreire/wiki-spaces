"""`wiki-spaces space` subcommands: add, remove, audit.

Maintains the `## Spaces` exhaustiveness contract automatically so users
never edit ancestor `index.md` files by hand to track child spaces.

Operations:
- `space add <rel-path>`     create a new space; update nearest ancestor's
                             `## Spaces` if that section exists there.
- `space remove <rel-path>`  delete a space; remove the entry from its
                             nearest ancestor's `## Spaces`. Refuses without
                             `--force` when the space contains content.
- `space audit`              walk owned spaces; report drift between actual
                             direct child spaces and listed `## Spaces` entries.

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


def _wiki_origin_url(wiki_root: Path) -> str | None:
    """Return the wiki's origin remote URL from .git/config, or None.

    Best-effort regex parse — no subprocess. Handles the common case of a
    `[remote "origin"]` section with `url = …`.
    """
    git_dir = wiki_root / ".git"
    config = git_dir / "config" if git_dir.is_dir() else None
    if config is None or not config.is_file():
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
    wiki's own origin from `<wiki>/.git/config`. When either is unreadable,
    returns False (callers fall back to the other heuristics).
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
    """Minimal Tier-1 index.md body for a freshly created space."""
    return f"# {name}\n\n## What this space is\n\n{description}\n"


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

    # Update nearest ancestor's ## Spaces (or upgrade Tier 1 → Tier 2 when asked)
    ancestor = _nearest_ancestor_space(wiki_root, new_space)
    ancestor_index = ancestor / "index.md"
    text = ancestor_index.read_text(encoding="utf-8")
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    section_present = _md.has_section(text, "Spaces")
    if not section_present and not args.upgrade_parent:
        print(
            f"  . {printable}index.md has no `## Spaces` section — "
            "leaving its layout unchanged (Tier 1 parent). "
            "Pass --upgrade-parent to add `## Spaces` there and list this space."
        )
        return 0

    rel_from_ancestor = new_space.relative_to(ancestor)
    label = f"{rel_from_ancestor}/"
    href = f"{rel_from_ancestor}/index.md"
    new_text = _md.add_entry(
        text, "Spaces", label, href, args.description
    )
    if new_text == text:
        print(f"  . entry for {label} already in ancestor's ## Spaces")
        return 0
    ancestor_index.write_text(new_text, encoding="utf-8")
    if not section_present:
        print(f"  + {printable}index.md ## Spaces (upgraded to Tier 2)")
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

    # Check for non-index.md content
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

    # Remove from ancestor's ## Spaces (if section present)
    ancestor = _nearest_ancestor_space(wiki_root, target)
    ancestor_index = ancestor / "index.md"
    text = ancestor_index.read_text(encoding="utf-8")
    rel_from_ancestor = target.relative_to(ancestor)
    href = f"{rel_from_ancestor}/index.md"
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    if _md.has_section(text, "Spaces"):
        new_text = _md.remove_entry(text, "Spaces", href)
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


def cmd_audit(args: argparse.Namespace) -> int:
    wiki_root = _resolve_wiki(args.wiki)
    if wiki_root is None:
        print(
            "  ! no wiki resolved. Pass --wiki <path> or set `wiki` in config.",
            file=sys.stderr,
        )
        return 2

    issues = 0
    for space in _walk_owned_spaces(wiki_root):
        index = space / "index.md"
        text = index.read_text(encoding="utf-8")
        if not _md.has_section(text, "Spaces"):
            continue
        # `## Spaces` entries: normalize each href to its child-space dir,
        # so `foo`, `foo/`, and `foo/index.md` all compare equal.
        listed_dirs: set[str] = {
            _spaces_href_to_dir(e.href)
            for e in _md.parse_section_entries(text, "Spaces")
            if e.href
        }
        # Direct child spaces actually on disk (owned, carrying index.md).
        actual_dirs: set[str] = set()
        for child in sorted(space.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if _is_external(child, wiki_root):
                continue
            if (child / "index.md").is_file():
                actual_dirs.add(child.name)
        missing = sorted(actual_dirs - listed_dirs)   # on disk, not listed
        stale = sorted(listed_dirs - actual_dirs)     # listed, no space on disk
        if missing or stale:
            rel = space.relative_to(wiki_root)
            label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
            print(f"{label}/index.md:")
            for m in missing:
                print(f"  + missing entry for {m}/")
            for s in stale:
                print(f"  - stale entry {s}/ (no index.md on disk)")
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
    p_add.add_argument(
        "--upgrade-parent",
        action="store_true",
        help="when the nearest ancestor is Tier 1 (no ## Spaces), add ## Spaces "
        "and list this entry instead of leaving the parent unchanged",
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
