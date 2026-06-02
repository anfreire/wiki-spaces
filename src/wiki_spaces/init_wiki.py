"""Scaffold a new wiki at the given path and register it as the canonical wiki.

Always writes the spec-required `index.md`. Optional files via --with:
  --with log.md _meta/taxonomy.md .manifest.json _template.md hot.md

Optional folders via --folders (plain directories, no `index.md` — they
become spaces only if the user later adds one). Nested paths like
`projects/foo` are accepted. Reserved segments are refused per CONVENTIONS
/ Reserved top-level folder names: hidden directories (`.X`), `.git/`,
`_archives/`, and `_meta/` — these are all skipped by consumer walkers,
so creating them via `--folders` would silently produce content no skill
can read.
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

from ._common import (
    CONFIG_PATH,
    ConfigUnreadableError,
    atomic_write,
    has_control_chars,
    new_index_md,
    write_config,
)

OPTIONAL = {"log.md", "hot.md", "_template.md", "_meta/taxonomy.md", ".manifest.json"}


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
  {{ summary }}
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
        help="do not write wiki=<path> to ${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config (default: write)",
    )
    args = parser.parse_args(argv)

    # Refuse wiki-root basenames that have explicit wiki-spaces semantics
    # when nested inside a parent wiki: `_archives` is excluded from audit
    # walks, `_meta` holds config files, `shared` is external-by-default.
    # The contract walker prunes these names as children regardless of
    # whether the user later mounts the wiki under another. Refusing at
    # init time prevents the surprise of a buried, unreachable wiki.
    # Hidden basenames (`.notes/`, `.private/`) are NOT refused — they're
    # a legitimate standalone-wiki UX pattern (e.g. `~/.notes/`); the
    # asymmetry with `_validate_rel_path` (which refuses hidden child
    # paths) is intentional and reflects the difference between a wiki
    # root and a child path within a wiki.
    #
    # The check uses the LEXICAL basename (pre-resolve) because walker
    # pruning operates on lexical child names — a symlink at
    # `<parent>/_archives` → `/real-wiki` is still pruned by parent
    # walkers as `_archives`, regardless of where the symlink resolves.
    from . import _model
    lexical_basename = args.path.expanduser().name
    if lexical_basename in (*_model.RESERVED_NAMES, "shared"):
        print(
            f"  ! invalid wiki root: {lexical_basename!r} is a reserved "
            "name per CONVENTIONS / Reserved top-level folder names. "
            "Consumer walkers would prune this as a child if nested under "
            "another wiki, creating a producer/consumer break. Choose a "
            "different basename.",
            file=sys.stderr,
        )
        return 2
    root = args.path.resolve()
    name = args.name or root.name
    # `name` and `--description` land directly in the scaffold's `index.md`. A
    # line-break char would inject a second `## Spaces` heading ahead of the
    # canonical one and corrupt the contract a consumer reads first (HANDBOOK:
    # distrust boundary inputs). `name` is validated AFTER the `root.name`
    # fallback so the directory basename is guarded too — a directory named
    # `x\u2028## Spaces` is as much a boundary input as `--name` is.
    if has_control_chars(name):
        src = "--name" if args.name is not None else f"the directory basename {root.name!r}"
        print(
            "  ! wiki name may not contain newline / control characters "
            f"({src} would corrupt the scaffold's `## Spaces` contract); pass "
            "--name with a clean value or rename the directory.",
            file=sys.stderr,
        )
        return 2
    if args.description is not None and has_control_chars(args.description):
        print(
            "  ! --description may not contain newline / control characters "
            "(they would corrupt the scaffold's `## Spaces` contract).",
            file=sys.stderr,
        )
        return 2
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
            # Mirror `space._validate_rel_path` so producer-side reserved
            # names refuse symmetrically. Hidden segments (`.X`) and
            # `_archives` / `_meta` are skipped by every consumer walker
            # per CONVENTIONS / Reserved top-level folder names; creating
            # them at init time would silently produce content no skill
            # can reach.
            if part in ("", ".", "..") or _model.is_reserved_segment(part):
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
            "have no '.', '..', or empty segments, and avoid reserved names "
            "(hidden `.X` directories including `.git`; `_archives`; `_meta`) "
            "per CONVENTIONS / Reserved top-level folder names. Nested "
            "paths like 'projects/foo' are accepted.",
            file=sys.stderr,
        )
        return 2

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"  ! could not create {root}: {e}", file=sys.stderr)
        return 1

    # Refuse to register a pre-existing folder whose `index.md` lacks
    # `## Spaces` unless the user opts in via `--adopt` (inserts the
    # heading via the chain helper) or `--force` (overwrites the index).
    # Without this check, `init <path-where-index.md-lacks-the-section>`
    # would skip the `index.md` write (file exists), write the config
    # anyway, and leave
    # the configured wiki rejected by every strict consumer (audit,
    # doctor, skills) — a producer/consumer break the v1 contract is
    # built to prevent.
    existing_index = root / "index.md"
    if (
        existing_index.is_file()
        and not args.force
        and not args.adopt
    ):
        from . import _md
        try:
            existing_text = existing_index.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(
                f"  ! could not read {existing_index}: {e}",
                file=sys.stderr,
            )
            return 1
        if not _md.has_section(existing_text, "Spaces"):
            print(
                f"  ! {root}/index.md exists but has no `## Spaces` heading. "
                "v1 requires the navigation contract.\n"
                "    Either:\n"
                "      • `wiki-spaces init <path> --adopt` to insert "
                "`## Spaces` and register every nested space, or\n"
                "      • `wiki-spaces init <path> --force` to overwrite the "
                "existing index.md with a fresh one.",
                file=sys.stderr,
            )
            return 2

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
    over_cap_writes: list[str] = []
    write_errors: list[str] = []

    def write(rel: str, content: str) -> None:
        f = root / rel
        if f.exists() and not args.force:
            skipped.append(rel)
            return
        # Framework writes route through `space.enforce_size_cap`. `init`
        # never silently truncates a too-long description — refuse so the
        # user can shorten it. Late import to keep the cold-start path light.
        # All `.md` writes route through here, including `log.md` (the
        # initial `# Log\n` is tiny but the v1 contract is "every framework
        # write enforces the cap"; an absurdly tight configured `log.md`
        # cap would otherwise leak an over-cap framework file onto disk).
        if rel.endswith(".md"):
            from . import space as _space
            try:
                _space.enforce_size_cap(f, content, root)
            except _space.SizeCapExceeded as e:
                # Surface and skip this single write. Track the over-cap
                # path so we can fail the whole `init` invocation if the
                # over-cap write was the wiki's `index.md` (without it,
                # the rest of the scaffold is meaningless and we MUST NOT
                # write the config pointing at a non-wiki path).
                print(f"  ! size cap: {e}", file=sys.stderr)
                over_cap_writes.append(rel)
                return
        from . import _model
        if _model.symlink_escapes_wiki(f, root):
            print(
                f"  ! refusing to write {rel}: it is a symlink whose target "
                "resolves outside the wiki tree. `atomic_write` would follow it "
                "and clobber content beyond the trust boundary. Replace the "
                "symlink with a regular file.",
                file=sys.stderr,
            )
            write_errors.append(rel)
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(f, content)
        except OSError as e:
            print(f"  ! could not write {rel}: {e}", file=sys.stderr)
            write_errors.append(rel)
            return
        written.append(rel)

    write("index.md", new_index_md(name, description))
    # If the wiki's `index.md` itself was refused by the size-cap helper,
    # there is no wiki — every later step (folders, adopt, config write)
    # assumes the index exists. Stop with a non-zero exit so the user
    # shortens the description and re-runs.
    if "index.md" in write_errors:
        print(
            "  ! init aborted: could not write `index.md` (see error above).",
            file=sys.stderr,
        )
        return 1
    if "index.md" in over_cap_writes:
        print(
            "  ! init aborted: `index.md` would exceed the per-file cap. "
            "Shorten `--description` and re-run.",
            file=sys.stderr,
        )
        return 2

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
        try:
            target.mkdir(parents=True)
        except OSError as e:
            # An unwritable root (e.g. init into an existing read-only dir, where
            # the root `mkdir(exist_ok=True)` succeeded) fails here. Refuse-and-
            # report like the root-creation path, never a raw traceback.
            write_errors.append(folder + "/")
            print(f"  ! could not create {folder}/: {e}", file=sys.stderr)
            continue
        written.append(folder + "/")
        if args.git:
            # Empty dirs are invisible to git; drop a .gitkeep so the scaffold
            # survives clone/checkout. Removed by the user once the folder has
            # real content.
            keep = target / ".gitkeep"
            try:
                keep.touch()
            except OSError as e:
                write_errors.append(folder + "/.gitkeep")
                print(f"  ! could not create {folder}/.gitkeep: {e}", file=sys.stderr)

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

    # `--adopt`: insert `## Spaces` into the root and every nested bare
    # `index.md`, and register each nested space in its ancestor's `## Spaces`
    # so audit reports zero drift on day 1. `space.adopt_tree` owns the
    # orchestration (it lives next to the chain helpers it drives); `init`
    # only renders the result. Late import: `space` pulls in `fcntl` and other
    # deps the no-adopt path shouldn't pay for.
    adopt_registered: list[tuple[str, str]] = []  # (label, ancestor-label)
    adopt_failed = False
    if args.adopt:
        from . import space as _space

        result = _space.adopt_tree(root, include_external=args.include_external)
        if result.root_failed:
            return 1
        adopt_registered = result.registered
        adopt_failed = result.failed

    partial = bool(over_cap_writes) or bool(write_errors) or adopt_failed
    config_error: str | None = None
    if not args.no_config and not partial:
        try:
            write_config({"wiki": str(root)})
        except ConfigUnreadableError as e:
            config_error = str(e)

    print(f"wiki: {root}")
    for w in written:
        print(f"  + {w}")
    for s in skipped:
        print(f"  . {s} (exists; --force to overwrite)")
    for label, anc in adopt_registered:
        print(f"  ~ {anc}/index.md ## Spaces  += [{label}]")
    if not written and not adopt_registered:
        print("  (nothing written)")
    if not args.no_config and not partial and config_error is None:
        print(f"  → registered as canonical wiki in {CONFIG_PATH}")
    elif not args.no_config and partial:
        print("  ! wiki NOT registered — fix errors above and re-run")
    if config_error is not None:
        print(f"  ! wiki NOT registered: {config_error}", file=sys.stderr)
    # Best-effort batch: one failing adoption doesn't abort the whole run,
    # but the exit code MUST signal partial failure. A success (rc=0) on
    # `init --adopt` with unrepaired drift would lie to callers / CI
    # gating on the return value — they'd treat the wiki as fully adopted
    # while strict consumers (audit, doctor, skills) still reject parts.
    if adopt_failed:
        return 1
    # An over-cap framework write that wasn't `index.md` (e.g., a
    # `--with log.md` whose cap rejected the scaffold) must also flip
    # the exit code so the user knows a requested write was refused.
    # The v1 contract is "errors on overflow, never silent truncation"
    # — silent rc=0 with a missing file would be a partial-success lie.
    if over_cap_writes:
        return 1
    # A failed folder / scaffold write (recorded above) must flip the exit code
    # too — a silent rc=0 with a missing requested folder would be a
    # partial-success lie to callers / CI gating on the return value.
    if write_errors:
        return 1
    # An unreadable existing config refused the wiki registration: the files
    # are on disk but discovery is not wired up, so signal partial failure
    # rather than a misleading rc=0 (HANDBOOK: handle failures at boundaries).
    if config_error is not None:
        return 1
    return 1 if git_failed else 0


if __name__ == "__main__":
    sys.exit(main())
