"""Install wiki-spaces skills into AI coding harnesses.

Install writes each wiki skill and vendored kepano dependency once into the
shared hub at ~/.agents/skills/. Harnesses that read the hub are served from
there; harnesses that do not read it get per-skill aliases in their configured
alias directories. No whole-directory links are created.

--harness <key> restricts which harnesses are selected. The shared hub is
written only when at least one selected harness reads it, or when --all is
passed. Selecting only a non-hub harness writes aliases only and reports that
the hub was skipped.

After install, write the wiki-spaces data path to
~/.config/wiki-spaces/config so skills can locate AGENTS.md, CONVENTIONS.md,
and references/ on demand.

Two source-resolution cases:
- Dev (source checkout): data lives at the repo root; symlinks point there.
- Installed wheel: data is packaged inside the wheel and copied to
  ~/.local/share/wiki-spaces/ on each install so harness symlinks remain
  valid after the wheel's site-packages location goes away (e.g. ephemeral
  `uvx` runs).

Flags:
  --dry-run             print planned actions; touch nothing
  --copy                force copies instead of symlinks
  --harness <key>       restrict to one harness; can repeat
  --all                 install for every supported harness regardless of detection
"""

from __future__ import annotations

import argparse
import shutil
import sys
from enum import Enum
from pathlib import Path

from ._common import (
    COMMON_SKILLS_DIR,
    CONFIG_PATH,
    HARNESSES,
    KEPANO_DEPS,
    OWNED_MARKER,
    WIKI_SKILLS,
    ConfigUnreadableError,
    Harness,
    LinkResult,
    data_root,
    harness_present,
    is_packaged,
    link_or_copy,
    share_dir,
    skill_rel,
    write_config,
    write_owned_marker,
)


def _ensure_vendor_dev(*, dry_run: bool) -> None:
    """In a dev checkout, vendor/kepano/ may be missing on a fresh clone.
    Run vendor_kepano.main() to populate it. In the packaged case this is
    a no-op (force-include guarantees vendor data is present)."""
    if is_packaged():
        return
    vendor = data_root() / "vendor" / "kepano"
    if all((vendor / s / "SKILL.md").exists() for s in KEPANO_DEPS):
        return
    if dry_run:
        print("vendor/kepano/ missing; would run vendor_kepano first.\n")
        return
    print("vendor/kepano/ missing; running vendor_kepano first...\n")
    from . import vendor_kepano  # late import: avoid cost when vendor is present

    rc = vendor_kepano.main()
    if rc != 0:
        raise SystemExit(rc)


def _materialize_share_dir(*, dry_run: bool) -> Path:
    """Copy packaged data to the stable share dir. Returns the share dir path.

    Used only in the packaged case. The wheel may live in a uvx-ephemeral
    venv, so we don't symlink into site-packages — we copy out to a stable
    location.
    """
    target = share_dir()
    if dry_run:
        print(f"would refresh {target} from packaged data\n")
        return target
    target.mkdir(parents=True, exist_ok=True)
    source = data_root()
    for entry in ("AGENTS.md", "CONVENTIONS.md", "references", "skills", "vendor"):
        src = source / entry
        dst = target / entry
        if not src.exists():
            continue
        if dst.exists():
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            else:
                shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return target


def _resolve_install_root(*, dry_run: bool) -> tuple[Path, Path]:
    """Return (read_root, write_root).

    read_root  — where to read source files from (for existence checks).
    write_root — the path symlinks will target and what gets written as `repo`.

    Dev (source checkout): read_root == write_root == data_root().
    Installed wheel, real install: copy data to share_dir; both == share_dir.
    Installed wheel, dry-run: read from packaged data (no copy made), but
    advertise the share_dir paths the symlinks would target.
    """
    if not is_packaged():
        root = data_root()
        return root, root
    target = share_dir()
    if dry_run:
        return data_root(), target
    return _materialize_share_dir(dry_run=False), target


class _OverwriteVerdict(Enum):
    """Typed outcome of the shared-hub ownership check (HANDBOOK: verdicts
    carry their provenance). `.safe` is the only gate callers need; the
    `.value` names the specific conflict for the warning message."""
    MISSING = "missing"
    OWNED_SYMLINK = "owned-symlink"
    OWNED_COPY = "owned-copy"
    FOREIGN_SYMLINK = "foreign-symlink"
    UNRESOLVABLE_SYMLINK = "unresolvable-symlink"
    UNMARKED_DIR = "unmarked-directory"
    PLAIN_FILE = "plain-file"

    @property
    def safe(self) -> bool:
        return self in (
            _OverwriteVerdict.MISSING,
            _OverwriteVerdict.OWNED_SYMLINK,
            _OverwriteVerdict.OWNED_COPY,
        )


def _can_overwrite_skill(dst: Path, expected_src: Path) -> _OverwriteVerdict:
    if not dst.exists() and not dst.is_symlink():
        return _OverwriteVerdict.MISSING
    if dst.is_symlink():
        try:
            if dst.resolve() == expected_src.resolve():
                return _OverwriteVerdict.OWNED_SYMLINK
            return _OverwriteVerdict.FOREIGN_SYMLINK
        except (OSError, RuntimeError):
            return _OverwriteVerdict.UNRESOLVABLE_SYMLINK
    if dst.is_dir() and (dst / OWNED_MARKER).is_file():
        return _OverwriteVerdict.OWNED_COPY
    if dst.is_dir():
        return _OverwriteVerdict.UNMARKED_DIR
    return _OverwriteVerdict.PLAIN_FILE


def _install_skill_target(
    label: str,
    skill: str,
    dst: Path,
    read_root: Path,
    write_root: Path,
    *,
    dry: bool,
    copy: bool,
    force: bool,
) -> tuple[str | None, bool]:
    rel = skill_rel(skill)
    src = read_root / rel
    expected_src = write_root / rel
    if not src.exists():
        print(f"  {label}: ! source missing {src}", file=sys.stderr)
        return None, True
    verdict = _can_overwrite_skill(dst, expected_src)
    if not force and not verdict.safe:
        print(
            f"  {label}: ! refusing to overwrite {verdict.value} at {dst} "
            "(pass --force to replace)",
            file=sys.stderr,
        )
        return None, True
    if dry:
        verb = "copy" if copy else "link"
        return f"  {label}: would {verb} {expected_src} -> {dst}", False
    mode = link_or_copy(src, dst, prefer_copy=copy)
    if mode == LinkResult.COPY:
        write_owned_marker(dst, src)
    return f"  {label}: {mode.value} {dst}", False


def _install_hub(
    read_root: Path, write_root: Path, *, dry: bool, copy: bool, force: bool
) -> tuple[list[str], bool]:
    actions: list[str] = []
    had_fatal = False
    for skill in (*WIKI_SKILLS, *KEPANO_DEPS):
        action, failed = _install_skill_target(
            "hub",
            skill,
            COMMON_SKILLS_DIR / skill,
            read_root,
            write_root,
            dry=dry,
            copy=copy,
            force=force,
        )
        if action is not None:
            actions.append(action)
        had_fatal = had_fatal or failed
    return actions, had_fatal


def _install_harness_aliases(
    h: Harness, read_root: Path, write_root: Path, *, dry: bool, copy: bool, force: bool
) -> tuple[list[str], bool]:
    actions: list[str] = []
    had_fatal = False
    for alias_dir in h.alias_dirs:
        for skill in (*WIKI_SKILLS, *KEPANO_DEPS):
            action, failed = _install_skill_target(
                h.key,
                skill,
                alias_dir / skill,
                read_root,
                write_root,
                dry=dry,
                copy=copy,
                force=force,
            )
            if action is not None:
                actions.append(action)
            had_fatal = had_fatal or failed
    return actions, had_fatal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--copy", action="store_true", help="force copies instead of symlinks")
    parser.add_argument(
        "--harness",
        action="append",
        default=[],
        help="restrict to one harness; repeatable. Selecting only non-hub "
        "harnesses skips the shared hub unless --all is also passed",
    )
    parser.add_argument("--all", action="store_true", help="install for every supported harness")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing skill directories that wiki-spaces didn't install",
    )
    args = parser.parse_args(argv)

    known_keys = {h.key for h in HARNESSES}
    unknown = [k for k in args.harness if k not in known_keys]
    if unknown:
        print(f"Unknown --harness key(s): {', '.join(unknown)}", file=sys.stderr)
        print("Supported keys:", ", ".join(sorted(known_keys)), file=sys.stderr)
        return 2

    _ensure_vendor_dev(dry_run=args.dry_run)

    if args.all:
        selected = list(HARNESSES)
    elif args.harness:
        # Explicit --harness X is a scope directive — install even when X
        # is undetected (HANDBOOK: never silently narrow a user-named scope).
        selected = [h for h in HARNESSES if h.key in args.harness]
    else:
        selected = [h for h in HARNESSES if harness_present(h)]

    # Hub-once is the v2 default model (README + HARNESS_INTEGRATION). Skip
    # the hub only when the user explicitly narrowed scope with --harness X
    # without --all AND no selected harness reads the hub — anything else
    # silently breaks the "every skill once into the hub" promise.
    explicit_narrow = bool(args.harness) and not args.all
    write_hub = not explicit_narrow or any(h.reads_hub for h in selected)

    # Write the `repo` config key even when no harnesses were detected. Skills
    # locate the installed data via this path, and `doctor` expects it to exist.
    read_root, write_root = _resolve_install_root(dry_run=args.dry_run)

    header = "DRY RUN" if args.dry_run else "INSTALL"
    print(f"=== wiki-spaces {header} ===")
    print(f"  source: {write_root}")
    if selected:
        print(f"  harnesses: {', '.join(h.key for h in selected)}")
    else:
        print("  harnesses: none detected")
    if write_hub:
        print("  hub: written")
    else:
        print("  hub: skipped (no hub-reading harness selected)")
    print()

    any_failure = False
    if write_hub:
        actions, had_fatal = _install_hub(
            read_root, write_root, dry=args.dry_run, copy=args.copy, force=args.force
        )
        for line in actions:
            print(line)
        any_failure = any_failure or had_fatal
    if selected:
        for h in selected:
            if h.reads_hub:
                continue
            actions, had_fatal = _install_harness_aliases(
                h, read_root, write_root, dry=args.dry_run, copy=args.copy, force=args.force
            )
            for line in actions:
                print(line)
            any_failure = any_failure or had_fatal
    else:
        print("  No supported harnesses detected.")
        print("  Use --all to pre-position the shared hub and non-hub aliases,")
        print("  or pass --harness <key> for a specific harness.")
        print()

    if not args.dry_run:
        try:
            write_config({"repo": str(write_root)})
        except ConfigUnreadableError as e:
            print(f"  ! {e}", file=sys.stderr)
            return 1
        print()
        print(f"Wrote repo path to {CONFIG_PATH}")
        print("Next: scaffold a wiki with `wiki-spaces init`, or set wiki = <path> in the config.")

    print()
    if any_failure:
        print("Install completed with errors (see stderr).", file=sys.stderr)
        return 1
    print("Done." if not args.dry_run else "Dry run complete; nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
