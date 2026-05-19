"""Install wiki-spaces skills into detected AI coding harnesses.

For each detected (or selected) harness with a skills concept, link the wiki
skills + vendored kepano skills into the harness's skills directory. After
install, write the wiki-spaces data path to ~/.config/wiki-spaces/config so
skills can locate AGENTS.md, CONVENTIONS.md, and references/ on demand.

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

The user's project state is NOT modified by this script. Cursor, Windsurf,
GitHub Copilot, and Aider users see references/HARNESS_INTEGRATION.md for
optional manual rule snippets.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ._common import (
    CONFIG_PATH,
    HARNESSES,
    KEPANO_DEPS,
    WIKI_SKILLS,
    Harness,
    data_root,
    harness_present,
    is_owned_install,
    is_packaged,
    link_or_copy,
    share_dir,
    write_config,
    write_owned_marker,
)

BRIDGES: dict[str, str] = {
    "cursor": "cursor/wiki-spaces.mdc",
    "windsurf": "windsurf/wiki-spaces.md",
}


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
    for entry in ("AGENTS.md", "CONVENTIONS.md", "bridges", "references", "skills", "vendor"):
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


def install_harness(
    h: Harness, read_root: Path, write_root: Path, *, dry: bool, copy: bool, force: bool
) -> list[str]:
    actions: list[str] = []
    for skill in (*WIKI_SKILLS, *KEPANO_DEPS):
        rel = ("skills" if skill in WIKI_SKILLS else "vendor/kepano") + f"/{skill}"
        src = read_root / rel
        if not src.exists():
            actions.append(f"  {h.key}: ! source missing {src}")
            continue
        dst = h.skills_dir / skill
        if not force and not is_owned_install(dst, src):
            actions.append(
                f"  {h.key}: ! refusing to overwrite unowned {dst} "
                "(pass --force to replace)"
            )
            continue
        if dry:
            future_src = write_root / rel
            actions.append(f"  {h.key}: would link {future_src} -> {dst}")
            continue
        mode = link_or_copy(src, dst, prefer_copy=copy)
        if mode == "copy":
            write_owned_marker(dst, src)
        actions.append(f"  {h.key}: {mode} {dst}")
    return actions


def _emit_bridge(key: str) -> int:
    if key not in BRIDGES:
        print(
            f"Unknown bridge key {key!r}. Supported: {', '.join(sorted(BRIDGES))}",
            file=sys.stderr,
        )
        return 2
    src = data_root() / "bridges" / BRIDGES[key]
    if not src.is_file():
        print(f"  ! bridge file missing on disk: {src}", file=sys.stderr)
        return 1
    sys.stdout.write(src.read_text(encoding="utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--copy", action="store_true", help="force copies instead of symlinks")
    parser.add_argument("--harness", action="append", default=[], help="restrict to one harness; repeatable")
    parser.add_argument("--all", action="store_true", help="install for every supported harness")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing skill directories that wiki-spaces didn't install",
    )
    parser.add_argument(
        "--bridge",
        metavar="KEY",
        choices=sorted(BRIDGES),
        help="emit a project-scoped rule snippet to stdout for a harness without "
        "a skills concept (cursor, windsurf). Pipe to the appropriate rules "
        "file: `wiki-spaces install --bridge cursor > .cursor/rules/wiki-spaces.mdc`. "
        "Ignores --dry-run / --copy / --harness / --all (it writes nothing — the "
        "caller controls placement via shell redirection).",
    )
    args = parser.parse_args(argv)

    if args.bridge:
        return _emit_bridge(args.bridge)

    _ensure_vendor_dev(dry_run=args.dry_run)

    known_keys = {h.key for h in HARNESSES}
    unknown = [k for k in args.harness if k not in known_keys]
    if unknown:
        print(f"Unknown --harness key(s): {', '.join(unknown)}")
        print("Supported keys:", ", ".join(sorted(known_keys)))
        return 2

    selected = [h for h in HARNESSES if (not args.harness or h.key in args.harness)]
    if not args.all:
        selected = [h for h in selected if harness_present(h)]

    if not selected:
        print("No harnesses selected. Either:")
        print("  - run with --all to pre-position skills for every supported harness, or")
        print("  - run with --harness <key> for one of:", ", ".join(sorted(known_keys)))
        print("  - if you only use Cursor / Windsurf / Copilot / Aider, see")
        print("    references/HARNESS_INTEGRATION.md for manual integration snippets.")
        return 1

    read_root, write_root = _resolve_install_root(dry_run=args.dry_run)

    header = "DRY RUN" if args.dry_run else "INSTALL"
    print(f"=== wiki-spaces {header} ===")
    print(f"  source: {write_root}")
    print(f"  harnesses: {', '.join(h.key for h in selected)}")
    print()

    for h in selected:
        for line in install_harness(
            h, read_root, write_root, dry=args.dry_run, copy=args.copy, force=args.force
        ):
            print(line)

    if not args.dry_run:
        write_config({"repo": str(write_root)})
        print()
        print(f"Wrote repo path to {CONFIG_PATH}")
        print("Next: scaffold a wiki with `wiki-spaces init`, or set wiki = <path> in the config.")

    print()
    print("Done." if not args.dry_run else "Dry run complete; nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
