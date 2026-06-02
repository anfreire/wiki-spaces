from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from .. import _md
from .._common import resolve_wiki
from ._core import (
    _atomic_register_in_spaces,
    _atomic_remove_from_spaces,
    _ensure_section_at,
    _first_external_descendant,
    _is_in_external_scope,
    _nearest_ancestor_space,
    _remove_space_entry,
    _validate_rel_path,
)

def cmd_remove(args: argparse.Namespace) -> int:
    wiki_root, _err = resolve_wiki(args.wiki, repair=True)
    if wiki_root is None:
        print(_err, file=sys.stderr)
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

    # The target-level check above only inspects `target` itself. Scan the
    # subtree BEFORE the snapshot/rmtree so a refused call mutates nothing —
    # read-before-write / refuse-and-report: an external child (foreign-origin
    # submodule or escaping symlink) lives OUTSIDE the owned tree and rmtree
    # would delete it. Reuse the existing --force-external override (no new
    # vocabulary).
    child_ext = _first_external_descendant(target, wiki_root)
    if child_ext is not None and not args.force_external:
        child_rel = child_ext[0].relative_to(wiki_root).as_posix()
        print(
            f"  ! refusing to remove {rel}/: it contains an external child "
            f"{child_rel} ({child_ext[1]}). Removing it would delete content "
            "outside the owned tree. Pass --force-external to override.",
            file=sys.stderr,
        )
        return 2

    ancestor = _nearest_ancestor_space(wiki_root, target)
    ancestor_index = ancestor / "index.md"
    rel_from_ancestor = target.relative_to(ancestor)
    href = f"{rel_from_ancestor}/index.md"
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"

    try:
        contents = [
            p for p in target.iterdir()
            if not (p.name == "index.md" and p.is_file())
        ]
    except OSError as e:
        print(f"  ! could not read {rel}/: {e}", file=sys.stderr)
        return 1
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
        try:
            text = ancestor_index.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"  ! could not read {printable}index.md: {e}", file=sys.stderr)
            return 2
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

    # A symlink-mounted space (`space mount … --mode symlink`) is a single
    # filesystem entry whose target lives OUTSIDE this directory. `rmtree`
    # raises on a symlink and `copytree` would dereference it (snapshotting
    # the foreign content as a real copy), so detect it up front: the delete
    # is `unlink()` and the only recoverable state is the link target itself
    # (`symlink_dest`), which rollback recreates. This restores mount↔unmount
    # symmetry — a `--mode symlink` mount unmounts cleanly.
    target_is_symlink = target.is_symlink()
    symlink_dest = os.readlink(target) if target_is_symlink else None

    # Snapshot the target directory's contents to a system tempdir before
    # any mutation. Rollback restores faithfully if rmtree fails. `symlinks=
    # True` preserves in-tree symlinks AS symlinks (and copies broken links
    # as-is) — `symlinks=False` would dereference them, so a rollback would
    # restore a symlink child as a dereferenced regular-file copy, silently
    # changing the on-disk shape of content the remove was meant to preserve.
    snapshot_dir = Path(tempfile.mkdtemp(prefix="wiki-spaces-remove-"))
    snapshot_ok = False
    try:
        try:
            if target_is_symlink:
                # Nothing to copy — `symlink_dest` is the entire recoverable
                # state and rollback recreates the link from it.
                snapshot_ok = True
            else:
                shutil.copytree(target, snapshot_dir / "target", symlinks=True)
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
        # Now delete the target. On failure, restore it AND re-add the index
        # entry we just removed.
        try:
            if target_is_symlink:
                target.unlink()
            else:
                shutil.rmtree(target)
        except OSError as rm_err:
            # Restore the target: recreate the symlink, or restore directory
            # contents byte-for-byte.
            restore_ok = True
            try:
                if target_is_symlink:
                    # `unlink` is atomic, so a failure usually leaves the link
                    # in place; recreate only if it's actually gone (lexists
                    # is True for an intact link, even a broken one).
                    if not os.path.lexists(target):
                        os.symlink(symlink_dest, target)
                else:
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(snapshot_dir / "target", target, symlinks=True)
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
