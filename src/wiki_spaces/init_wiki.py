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

    # Refuse to register a pre-existing folder whose `index.md` lacks
    # `## Spaces` unless the user opts in via `--adopt` (inserts the
    # heading via the chain helper) or `--force` (overwrites the index).
    # Without this check, `init <path-with-bare-index>` would skip the
    # `index.md` write (file exists), write the config anyway, and leave
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
        except OSError as e:
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

    def write(rel: str, content: str) -> None:
        f = root / rel
        if f.exists() and not args.force:
            skipped.append(rel)
            return
        # PR-L: framework writes route through `_enforce_size_cap`. `init`
        # never silently truncates a too-long description — refuse so the
        # user can shorten it. Late import to keep the cold-start path light.
        # All `.md` writes route through here, including `log.md` (the
        # initial `# Log\n` is tiny but the v1 contract is "every framework
        # write enforces the cap"; an absurdly tight configured `log.md`
        # cap would otherwise leak an over-cap framework file onto disk).
        if rel.endswith(".md"):
            from . import space as _space
            try:
                _space._enforce_size_cap(f, content, root)
            except _space.SizeCapExceeded as e:
                # Surface and skip this single write. Track the over-cap
                # path so we can fail the whole `init` invocation if the
                # over-cap write was the wiki's `index.md` (without it,
                # the rest of the scaffold is meaningless and we MUST NOT
                # write the config pointing at a non-wiki path).
                print(f"  ! size cap: {e}", file=sys.stderr)
                skipped.append(rel + " (over cap)")
                over_cap_writes.append(rel)
                return
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        written.append(rel)

    write("index.md", build_index_md(name, description))
    # If the wiki's `index.md` itself was refused by the size-cap helper,
    # there is no wiki — every later step (folders, adopt, config write)
    # assumes the index exists. Stop with a non-zero exit so the user
    # shortens the description and re-runs.
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
    # skipped (unless --include-external). The chain helper inserts
    # `## Spaces` into any bare-`index.md` ancestor along the walk up.
    adopt_registered: list[tuple[str, str]] = []  # (label, ancestor-relative)
    adopt_failed = False
    if args.adopt:
        # Late import: `space` pulls in `fcntl` and other heavy deps that
        # `init_wiki` shouldn't pay for in the no-adopt path.
        from . import space as _space

        # Always repair the root first — even on a zero-nested-spaces wiki
        # the root must carry `## Spaces` after `init --adopt`.
        try:
            _space._ensure_section_at(root, root)
        except RuntimeError as e:
            print(
                f"  ! could not insert `## Spaces` into {root}/index.md: {e}",
                file=sys.stderr,
            )
            return 1

        # v6 plan: pass `include_external` to the walker so `--include-external`
        # actually descends into external subtrees, not just yields the
        # boundaries.
        for path, classification, reason in _space._walk_classified(
            root, include_external=args.include_external
        ):
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
            # When include_external is on, `_walk_classified` may surface
            # external boundary folders that don't actually have `index.md`
            # (foreign submodules, escaping symlinks). Skip those with a
            # per-skip notice rather than trying to register a non-space.
            if not (path / "index.md").is_file():
                rel_path = path.relative_to(root).as_posix()
                print(
                    f"  . skipping {rel_path}/ — no index.md",
                    file=sys.stderr,
                )
                continue

            # Repair the LEAF's own `index.md` first. The chain helper
            # only walks UP from leaf, so a bare nested `foo/index.md`
            # with no children stays bare without this step.
            try:
                _space._ensure_section_at(path, root)
            except RuntimeError as e:
                print(
                    f"  ! adopt failed inserting `## Spaces` into "
                    f"{path}/index.md: {e}",
                    file=sys.stderr,
                )
                adopt_failed = True
                continue

            # Register `path` upward via the chain helper. Bare-index
            # ancestors get `## Spaces` inserted as part of the chain walk.
            # The chain helper's notices are deferred — `init`'s bottom
            # summary print groups adoption activity with the rest of the
            # written-files block so the user sees one tidy report.
            try:
                _notices, added = _space._ensure_spaces_chain_and_register(
                    root, path
                )
                for ancestor, label, _href in added:
                    anc_rel = ancestor.relative_to(root)
                    anc_label = (
                        "<wiki>"
                        if str(anc_rel) == "."
                        else f"<wiki>/{anc_rel}"
                    )
                    adopt_registered.append((label, anc_label))
            except _space.EnsureChainError as e:
                print(
                    f"  ! adopt failed for {path}: {e}",
                    file=sys.stderr,
                )
                _space._rollback_added_entries(e.added)
                adopt_failed = True

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
    return 1 if git_failed else 0


if __name__ == "__main__":
    sys.exit(main())
