from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from .. import _md
from .._common import resolve_wiki
from ._core import (
    ChainExternalRefusal,
    EnsureChainError,
    SizeCapExceeded,
    _derive_default_path,
    _dest_physically_mountable,
    _ensure_spaces_chain_and_register,
    _first_foreign_submodule_ancestor,
    _is_in_external_scope,
    _nearest_ancestor_space,
    _preflight_chain_caps,
    _preflight_chain_external,
    _probe_existing_ancestor,
    _rollback_added_entries,
    _validate_entry_text,
    _validate_rel_path,
)

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
    wiki-spaces space (`index.md` with `## Spaces`), and add the `## Spaces`
    entry.

    Registration is atomic against partial-write failures: an advisory
    `fcntl.flock` on the ancestor directory serializes concurrent mounts,
    and the parent `index.md` is written via tempfile + `os.replace` so a
    crash mid-write cannot leave the file half-rewritten. If registration
    fails after a successful mount, the mount is rolled back per-mode.
    """
    wiki_root, _err = resolve_wiki(args.wiki, repair=True)
    if wiki_root is None:
        print(_err, file=sys.stderr)
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

    # Refuse unless the destination is a safe, in-tree, owned-or-`shared/`
    # location. A mount must materialize content INSIDE the wiki and never
    # through an escaping symlink (someone else's filesystem), a foreign
    # submodule (someone else's repo working tree), or an unresolvable/cyclic
    # symlink. `shared/` is the one sanctioned external location — external
    # *trust scope*, but physically in-tree. Two arms, both before the dry-run
    # branch so `--dry-run` predicts the refusal:
    #   1. physical viability — the deepest physically-present ancestor of
    #      `dest` must resolve strictly within the tree AND be a directory
    #      (`_dest_physically_mountable`, shared with `cmd_add`): catches
    #      escaping / escape-then-reenter / cyclic / broken symlinks and a
    #      file masquerading as a parent dir, refusing cleanly instead of
    #      crashing with an OSError at mkdir;
    #   2. trust scope — a foreign-origin submodule ancestor is refused even
    #      under `shared/` (mounting into someone else's repo working tree);
    #      outside `shared/`, the ancestor must additionally be OWNED.
    rel_posix = dest.relative_to(wiki_root).as_posix()
    under_shared = rel_posix == "shared" or rel_posix.startswith("shared/")
    probe = _probe_existing_ancestor(wiki_root, dest)
    unmountable = _dest_physically_mountable(wiki_root, dest, probe)
    if unmountable is not None:
        print(
            f"  ! refusing to mount: {unmountable}. Mount inside the wiki "
            "(e.g. shared/<name>/).",
            file=sys.stderr,
        )
        return 2
    # A foreign-origin submodule ancestor is ALWAYS refused — mounting into it
    # materializes content inside a third party's checked-out repo working
    # tree, the precise harm `shared/` does NOT license (`shared/` sanctions
    # FRESH content under your own folder, not writes into someone else's
    # submodule that happens to live there). Escaping-symlink ancestors are
    # already refused by the physical-viability arm above. This runs even when
    # `under_shared`, where the broader external-scope arm below is skipped.
    foreign = _first_foreign_submodule_ancestor(probe, wiki_root)
    if foreign is not None:
        print(
            f"  ! refusing to mount: {foreign.relative_to(wiki_root).as_posix()} "
            "is a foreign-origin submodule (someone else's repo working tree); "
            "mounting into it would write external scope. Pick an owned "
            "destination or a fresh path under shared/.",
            file=sys.stderr,
        )
        return 2
    if not under_shared:
        is_ext, ext_reason = _is_in_external_scope(probe, wiki_root)
        if is_ext:
            print(
                f"  ! refusing to mount into external scope: {ext_reason}. "
                "Mount under shared/ or pick an owned destination — "
                "wiki-spaces does not write into external scope.",
                file=sys.stderr,
            )
            return 2

    # Pre-compute the registration label/href so dry-run can print it.
    rel_from_ancestor = dest.relative_to(ancestor)
    label = args.name or f"{rel_from_ancestor}/"
    href = f"{rel_from_ancestor}/index.md"

    # Refuse-and-report if the chain helper would register into an EXTERNAL
    # ancestor's `index.md`. The helper walks UP from the mount and writes
    # every ancestor's `## Spaces`; an external ancestor (under `shared/`, a
    # foreign-origin submodule, or an escaping symlink) is scope we don't own
    # — writing it needs explicit instruction. Checked BEFORE the dry-run
    # branch so `--dry-run` predicts the real refusal (and before the size-cap
    # pre-flight so the trust-scope refusal wins); no FS state is touched.
    # mount has no --force-external flag, so the getattr resolves to False (no
    # override; we don't invent new flags).
    try:
        _preflight_chain_external(
            wiki_root, dest, force_external=getattr(args, "force_external", False)
        )
    except ChainExternalRefusal as e:
        print(
            f"  ! refusing to register into external ancestor "
            f"{e.ancestor.relative_to(wiki_root).as_posix()}/index.md: "
            f"{e.reason}. wiki-spaces does not mutate external scope without "
            "explicit instruction.",
            file=sys.stderr,
        )
        return 2

    # Pre-flight the FULL chain's size caps BEFORE running the mount mechanism.
    # `git submodule add` stages a gitlink + edits `.gitmodules`; a cap
    # rejection after that would require manual cleanup the user has to run
    # by hand. The chain helper walks UP from the leaf and writes to every
    # ancestor on the way to the wiki root — checking only the nearest
    # ancestor misses overflow on an upper ancestor's `## Spaces` registration,
    # which the chain helper would surface as `EnsureChainError` after the
    # destructive mount step had already run. Refuse early instead. The
    # in-lock check inside the chain helper is still authoritative for
    # concurrent growth between this pre-flight and the actual mutation.
    try:
        _preflight_chain_caps(
            wiki_root,
            dest,
            leaf_label=args.name,
            leaf_description=args.description,
        )
    except SizeCapExceeded as e:
        print(
            f"  ! size cap: {e}. Refusing to mount before any FS "
            "mutation — fix the ancestor's index.md or its cap first.",
            file=sys.stderr,
        )
        return 2
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ! could not read an ancestor index.md: {e}", file=sys.stderr)
        return 2

    # Dry-run AFTER all read-only preflights (destination scope, external
    # ancestor, full-chain size caps), so `--dry-run` predicts the real
    # refusals; returns before the first FS mutation below.
    if args.dry_run:
        print(f"  . (dry-run) would mount {args.source} -> {rel}/ via {mechanism}")
        desc_part = f" — {args.description}" if args.description else ""
        print(
            f"  . (dry-run) would register entry [{label}]({href}){desc_part} "
            f"in {printable}index.md ## Spaces"
        )
        return 0

    # The parent dirs THIS call is about to materialize, deepest-first. Every
    # failure path below removes them (empty-only) so a failed mount stays
    # fail-closed, mirroring `cmd_add`'s created-dir rollback. A pre-existing
    # parent is never recorded, so it survives.
    created_parents: list[Path] = []
    probe = dest.parent
    while not probe.exists() and probe != wiki_root:
        created_parents.append(probe)
        probe = probe.parent
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # An unwritable parent dir (e.g. read-only) makes mkdir fail. The
        # physical-viability guard checks containment + type, not writability,
        # so refuse-and-report here instead of dumping a Traceback.
        _remove_empty_created_dirs(created_parents)
        print(
            f"  ! could not create {rel}/: {e}. Mount aborted before any "
            "mount mechanism ran.",
            file=sys.stderr,
        )
        return 1

    if mechanism == "symlink":
        try:
            dest.symlink_to(src_resolved, target_is_directory=True)
        except OSError as e:
            _remove_empty_created_dirs(created_parents)
            print(f"  ! symlink failed: {e}", file=sys.stderr)
            return 1
        print(f"  + {rel} -> {src_resolved}  (symlink)")
    elif mechanism == "clone":
        rc, errout = _run_git(["git", "clone", args.source, str(dest)])
        if rc != 0:
            _remove_empty_created_dirs(created_parents)
            print(f"  ! git clone failed: {errout}", file=sys.stderr)
            return 1
        print(f"  + {rel}/  (git clone of {args.source})")
    else:  # submodule
        rc, errout = _run_git(
            ["git", "-C", str(wiki_root), "submodule", "add", args.source, rel]
        )
        if rc != 0:
            _remove_empty_created_dirs(created_parents)
            print(f"  ! git submodule add failed: {errout}", file=sys.stderr)
            return 1
        print(f"  + {rel}/  (git submodule of {args.source})")

    # Verify the mount is actually a wiki-spaces space before registering it.
    # The v1 contract requires `index.md` AND `## Spaces` on the mounted
    # target. Auto-inserting `## Spaces` into an external mount would mutate
    # someone else's repo, so we refuse instead — the user coordinates with
    # the upstream owner. `_rollback_mount` auto-undoes all three mount
    # mechanisms (symlink unlink, clone rmtree, submodule deinit+rm+cache
    # prune); residue from a partial submodule undo gets a manual-recovery
    # hint on stderr.
    if not (dest / "index.md").is_file():
        print(
            f"  ! mounted {rel}/ has no index.md — it is not a wiki-spaces "
            "space, so it was not registered in `## Spaces`.",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism, created_parents)
        return 1
    try:
        mounted_text = (dest / "index.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(
            f"  ! could not read mounted {rel}/index.md: {e}",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism, created_parents)
        return 1
    if not _md.has_section(mounted_text, "Spaces"):
        print(
            f"  ! mounted {rel}/index.md has no `## Spaces` section. "
            "Coordinate with the upstream owner to add it before mounting; "
            "wiki-spaces does not auto-insert into external spaces.",
            file=sys.stderr,
        )
        _rollback_mount(wiki_root, dest, rel, mechanism, created_parents)
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
        for _n in _rollback_added_entries(e.added):
            print(_n, file=sys.stderr)
        _rollback_mount(wiki_root, dest, rel, mechanism, created_parents)
        print(f"  ! {e}", file=sys.stderr)
        return 1
    return 0


def _rollback_mount(
    wiki_root: Path,
    dest: Path,
    rel: str,
    mechanism: str,
    created_parents: list[Path],
) -> None:
    """Undo a partial mount so the filesystem doesn't keep an orphaned space.

    All three mechanisms are auto-rolled-back. For a submodule the rollback
    runs the standard undo sequence (`submodule deinit`, `git rm`, prune
    the `.git/modules/<rel>` cache). If any step fails, the residual
    manual-recovery commands are printed so the user can finish cleanup.
    Once the mount is undone, the parent dirs THIS call created are removed
    (empty-only) so the failed mount leaves no residue.
    """
    if mechanism == "symlink":
        try:
            dest.unlink()
            print(f"  - removed the symlink {rel}", file=sys.stderr)
        except OSError as e:
            print(f"  ! manual cleanup required: could not remove {rel}: {e}", file=sys.stderr)
    elif mechanism == "clone":
        try:
            shutil.rmtree(dest)
            print(f"  - removed the clone at {rel}/", file=sys.stderr)
        except OSError as e:
            print(
                f"  ! manual cleanup required: could not remove the clone at "
                f"{rel}/: {e}",
                file=sys.stderr,
            )
    else:
        # submodule: undo the gitlink + .gitmodules edit + .git/modules cache.
        # `git submodule add` already staged the gitlink and edited .gitmodules
        # but no commit has happened — the undo is purely local and safe.
        failures: list[str] = []
        deinit_rc, deinit_err = _run_git(
            ["git", "-C", str(wiki_root), "submodule", "deinit", "-f", rel]
        )
        if deinit_rc != 0:
            failures.append(f"submodule deinit -f {rel}: {deinit_err.strip()}")
        rm_rc, rm_err = _run_git(["git", "-C", str(wiki_root), "rm", "-f", rel])
        if rm_rc != 0:
            failures.append(f"git rm -f {rel}: {rm_err.strip()}")
        modules_cache = wiki_root / ".git" / "modules" / rel
        if modules_cache.exists():
            try:
                shutil.rmtree(modules_cache)
            except OSError as e:
                failures.append(f"rm -rf {modules_cache}: {e}")
        if failures:
            print(
                f"  ! submodule rollback for {rel} left residue. Run manually:\n"
                f"      git -C {wiki_root} submodule deinit -f {rel}\n"
                f"      git -C {wiki_root} rm -f {rel}\n"
                f"      rm -rf {wiki_root}/.git/modules/{rel}\n"
                f"    Failures: {'; '.join(failures)}",
                file=sys.stderr,
            )
        else:
            print(f"  - removed the submodule at {rel}/", file=sys.stderr)
    _remove_empty_created_dirs(created_parents)


def _remove_empty_created_dirs(dirs: list[Path]) -> None:
    """Remove dirs created by a now-failed mount, deepest-first, empty-only.

    `dirs` is the deepest-first list of parents the mount materialized. A dir
    that is non-empty (held content this call did not create) or already gone
    is left as-is — the same conservative rollback `cmd_add` performs.
    """
    for d in dirs:
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass

