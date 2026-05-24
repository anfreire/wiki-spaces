"""Scaffold a new wiki at the given path and register it as the canonical wiki.

Always writes the spec-required `index.md`. Optional files via --with:
  --with log.md _meta/taxonomy.md .manifest.json _template.md hot.md

Optional folders via --folders (plain directories, no `index.md` — they
become spaces only if the user later adds one). Nested paths like
`projects/foo` are accepted; bare hidden names (`.archive`) are allowed;
`.git` is reserved:
  --folders concepts entities projects/acme

After scaffolding, writes `wiki = <path>` to ~/.config/wiki-spaces/config so
all skills can locate it. Pass --no-config for tests / dry workflows where
you don't want to clobber the config. (Scaffolding spaces inside an existing
wiki is out of scope for this script — the parent's `## Spaces` would also
need updating; do that mount via references/MOUNT.md instead.)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ._common import CONFIG_PATH, write_config

OPTIONAL = {"log.md", "hot.md", "_template.md", "_meta/taxonomy.md", ".manifest.json"}


def build_index_md(name: str, description: str | None = None) -> str:
    """Compose the initial index.md.

    Always emits title + `## Spaces` (the navigation contract — present from
    t=0 on every CLI-created wiki, so `space add foo/bar` works immediately).
    When `description` is provided, also emits `## What this space is` with
    the description. Omitting `description` skips that section entirely
    rather than writing a placeholder string the user would later have to
    overwrite.
    """
    parts = [f"# {name}", ""]
    if description and description.strip():
        parts += ["## What this space is", "", description.strip(), ""]
    parts += ["## Spaces", ""]
    return "\n".join(parts)

LOG_MD = "# Log\n"
HOT_MD = "# Hot\n\n_Currently active work._\n"
TEMPLATE_MD = """---
title: >-
  {{ title }}
category:
tags: []
aliases: []
sources: []
summary: >-
  ≤200 chars
created: {{ now }}
updated: {{ now }}
---

# {{ title }}

One-paragraph summary.

## Key Ideas

## Open Questions
"""
TAXONOMY_MD = """# Tag Taxonomy

Canonical tag vocabulary. Max 5 tags per page; lowercase/hyphenated.

## Domain Tags

| Tag | Purpose | Aliases |
|---|---|---|

## Type Tags

| Tag | Purpose |
|---|---|
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new wiki-spaces wiki.")
    parser.add_argument("path", type=Path, help="target directory (created if missing)")
    parser.add_argument(
        "--with",
        dest="extras",
        nargs="*",
        default=[],
        choices=sorted(OPTIONAL),
        help="optional convention files to include",
    )
    parser.add_argument("--name", help="display name (defaults to directory basename)")
    parser.add_argument(
        "--description",
        help="one-paragraph description of this wiki. Injected into index.md's "
        "`## What this space is` section verbatim. When omitted, the section "
        "is skipped entirely — no placeholder text is written.",
    )
    parser.add_argument(
        "--folders",
        nargs="+",
        default=[],
        metavar="PATH",
        help="folders to create as plain directories; nested paths like "
        "'projects/foo' are accepted",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="adopt an existing folder of notes as a wiki. Scaffolds index.md "
        "if missing, then walks every nested folder containing index.md and "
        "registers each in the appropriate ancestor's `## Spaces` section so "
        "audit reports zero drift on day 1. External subtrees (under `shared/`, "
        "foreign-origin submodules, escaping symlinks) are skipped with a "
        "per-skip notice on stderr unless --include-external is set.",
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="when used with --adopt, also register externally-classified "
        "spaces (under `shared/`, foreign submodules, escaping symlinks). "
        "Off by default.",
    )
    parser.add_argument(
        "--git",
        action="store_true",
        help="run 'git init -b main' inside the new wiki after scaffolding",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="do not write wiki=<path> to ~/.config/wiki-spaces/config (default: write)",
    )
    args = parser.parse_args(argv)

    root = args.path.resolve()
    name = args.name or root.name
    description = args.description.strip() if args.description else None

    folders: list[str] = []
    invalid_folders: list[str] = []
    seen: set[str] = set()
    for raw in args.folders:
        folder = raw.strip().rstrip("/")
        if not folder:
            invalid_folders.append(raw)
            continue
        rel = Path(folder)
        if rel.is_absolute():
            invalid_folders.append(raw)
            continue
        bad_part = False
        for part in rel.parts:
            if part in ("", ".", "..") or part == ".git":
                bad_part = True
                break
        if bad_part:
            invalid_folders.append(raw)
            continue
        try:
            normalized = (root / rel).resolve().relative_to(root)
        except ValueError:
            invalid_folders.append(raw)
            continue
        normalized_str = normalized.as_posix()
        if normalized_str not in seen:
            folders.append(normalized_str)
            seen.add(normalized_str)
    if invalid_folders:
        bad = ", ".join(repr(f) for f in invalid_folders)
        print(f"  ! invalid folder name(s): {bad}", file=sys.stderr)
        print(
            "    folder paths must be relative, stay inside the wiki root, "
            "and have no '.', '..', or '.git' segments. Nested paths like "
            "'projects/foo' are accepted.",
            file=sys.stderr,
        )
        return 2

    root.mkdir(parents=True, exist_ok=True)

    folder_collisions: list[str] = []
    for folder in folders:
        target = root / folder
        if target.exists() and not target.is_dir():
            folder_collisions.append(folder)
            continue
        for parent in target.parents:
            if parent == root or root not in parent.parents:
                break
            if parent.exists() and not parent.is_dir():
                folder_collisions.append(folder)
                break
    if folder_collisions:
        bad = ", ".join(repr(f) for f in folder_collisions)
        print(
            f"  ! cannot create folder(s) {bad}: a non-directory file exists at that path",
            file=sys.stderr,
        )
        return 2

    written: list[str] = []
    skipped: list[str] = []

    def write(rel: str, content: str) -> None:
        f = root / rel
        if f.exists() and not args.force:
            skipped.append(rel)
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        written.append(rel)

    write("index.md", build_index_md(name, description))

    for opt in args.extras:
        match opt:
            case "log.md":
                write("log.md", LOG_MD)
            case "hot.md":
                write("hot.md", HOT_MD)
            case "_template.md":
                write("_template.md", TEMPLATE_MD)
            case "_meta/taxonomy.md":
                write("_meta/taxonomy.md", TAXONOMY_MD)
            case ".manifest.json":
                write(".manifest.json", json.dumps({"projects": {}}, indent=2) + "\n")

    for folder in folders:
        target = root / folder
        if target.is_dir():
            skipped.append(folder + "/")
            continue
        target.mkdir(parents=True)
        written.append(folder + "/")
        if args.git:
            # Empty dirs are invisible to git; drop a .gitkeep so the scaffold
            # survives clone/checkout. Removed by the user once the folder has
            # real content.
            keep = target / ".gitkeep"
            keep.touch()

    git_failed = False
    if args.git and not (root / ".git").exists():
        try:
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            written.append(".git/")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  ! git init failed: {e}", file=sys.stderr)
            git_failed = True
    elif args.git:
        skipped.append(".git/")

    # `--adopt`: scan for existing nested spaces and register them in their
    # nearest ancestor's `## Spaces`. Externals are reported on stderr and
    # skipped (unless --include-external).
    adopt_registered: list[tuple[str, str]] = []  # (label, ancestor-relative)
    if args.adopt:
        # Late import: `space` pulls in `fcntl` and other heavy deps that
        # `init_wiki` shouldn't pay for in the no-adopt path.
        from . import space as _space, _md as _md_module

        for path, classification, reason in _space._walk_classified(root):
            if path == root:
                continue
            if classification == "external" and not args.include_external:
                rel_path = path.relative_to(root).as_posix()
                print(
                    f"  . skipping {rel_path}/ — classified external "
                    f"({reason}). Rename to use as owned, or pass "
                    f"--include-external to override.",
                    file=sys.stderr,
                )
                continue

            ancestor = _space._nearest_ancestor_space(root, path)
            ancestor_index = ancestor / "index.md"
            try:
                ancestor_text = ancestor_index.read_text(encoding="utf-8")
            except OSError:
                continue
            # Every CLI-created/adopted wiki has `## Spaces` from t=0, but a
            # pre-existing adopted index.md might have had it stripped. Skip
            # silently if the ancestor lacks the section — the user can add
            # it and re-run.
            if not _md_module.has_section(ancestor_text, "Spaces"):
                continue
            rel_from_ancestor = path.relative_to(ancestor)
            label = f"{rel_from_ancestor}/"
            href = f"{rel_from_ancestor}/index.md"
            new_text = _space._add_space_entry(ancestor_text, label, href, None)
            if new_text != ancestor_text:
                ancestor_index.write_text(new_text, encoding="utf-8")
                anc_rel = ancestor.relative_to(root)
                anc_label = "<wiki>" if str(anc_rel) == "." else f"<wiki>/{anc_rel}"
                adopt_registered.append((label, anc_label))

    if not args.no_config:
        write_config({"wiki": str(root)})

    print(f"wiki: {root}")
    for w in written:
        print(f"  + {w}")
    for s in skipped:
        print(f"  . {s} (exists; --force to overwrite)")
    for label, anc in adopt_registered:
        print(f"  ~ {anc}/index.md ## Spaces  += [{label}]")
    if not written and not adopt_registered:
        print("  (nothing written)")
    if not args.no_config:
        print(f"  → registered as canonical wiki in {CONFIG_PATH}")
    return 1 if git_failed else 0


if __name__ == "__main__":
    sys.exit(main())
