from __future__ import annotations

import argparse
import sys
from pathlib import Path
from .. import _md
from .. import _model
from .._common import atomic_write
from .._common import has_control_chars
from .._common import new_index_md
from .._common import resolve_wiki
from ._core import (
    ChainExternalRefusal,
    EnsureChainError,
    SizeCapExceeded,
    _dest_physically_mountable,
    _ensure_section_at,
    _ensure_spaces_chain_and_register,
    _format_cap_source,
    _is_in_external_scope,
    _preflight_chain_caps,
    _preflight_chain_external,
    _probe_existing_ancestor,
    _render_template_index_md,
    _rollback_added_entries,
    _validate_entry_text,
    _validate_rel_path,
    enforce_size_cap,
    scoped_size_verdict,
)

def cmd_add(args: argparse.Namespace) -> int:
    wiki_root, _err = resolve_wiki(args.wiki, repair=True)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2
    ok, err = _validate_rel_path(args.path)
    if not ok:
        print(f"  ! invalid path: {err}", file=sys.stderr)
        return 2
    ok, why = _validate_entry_text(args.description, field="--description")
    if not ok:
        print(f"  ! {why}", file=sys.stderr)
        return 2
    # `--name` becomes the child index's `# title`; a newline-bearing value could
    # inject a `## Spaces` heading into the child's own contract (argv is a
    # boundary input). Reject control chars (`]`/`)` stay legal — the name is an
    # H1 title, not a `## Spaces` entry).
    if args.name is not None and has_control_chars(args.name):
        print(
            "  ! --name may not contain newline / control characters "
            "(they would corrupt the child's `## Spaces` contract).",
            file=sys.stderr,
        )
        return 2

    # --summary only makes sense paired with --from-template
    # (frontmatter is set by the template; without one, there is no
    # frontmatter to land in). Refuse loudly rather than silently
    # dropping the user's value.
    if getattr(args, "summary", None) and not getattr(args, "from_template", None):
        print(
            "  ! --summary requires --from-template (the frontmatter "
            "field is set by the template's `{{ summary }}` placeholder; "
            "without a template there is no frontmatter to write).",
            file=sys.stderr,
        )
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

    # Physical viability of the destination's deepest present ancestor — the
    # same containment + dir-type check `cmd_mount` runs, so `add` refuses-and-
    # reports instead of crashing at `mkdir` on a cyclic / broken-symlink or
    # file component, and `--dry-run` predicts the refusal. `_is_in_external_
    # scope` above already names an escaping-symlink ancestor as external
    # scope; this catches the cyclic / file-component cases it classifies
    # owned, and runs even under `--force-external` (an unmountable path is
    # unmountable regardless of trust override).
    unmountable = _dest_physically_mountable(
        wiki_root, new_space, _probe_existing_ancestor(wiki_root, new_space)
    )
    if unmountable is not None:
        print(f"  ! refusing to add {rel}: {unmountable}.", file=sys.stderr)
        return 2

    # Refuse-and-report if the chain helper would register into an EXTERNAL
    # ancestor's `index.md`. The target-level check above already refuses when
    # `new_space` itself is external; this covers the orthogonal case where
    # `new_space` is owned but a HIGHER ancestor space is external. Checked
    # BEFORE the dry-run branch so `--dry-run` predicts the real refusal. add
    # defines --force-external, so it is honored here too.
    try:
        _preflight_chain_external(
            wiki_root, new_space, force_external=args.force_external
        )
    except ChainExternalRefusal as e:
        print(
            f"  ! refusing to register into external ancestor "
            f"{e.ancestor.relative_to(wiki_root).as_posix()}/index.md: "
            f"{e.reason}. Pass --force-external to override.",
            file=sys.stderr,
        )
        return 2

    # Size-cap check BEFORE mkdir, so an over-cap description aborts
    # cleanly without leaving an empty directory + a stranded `index.md`.
    already_space = (new_space / "index.md").is_file()
    template_path: Path | None = None
    if getattr(args, "from_template", None):
        # Resolve template path: wiki-root-relative if not absolute.
        raw = Path(args.from_template)
        template_path = raw if raw.is_absolute() else (wiki_root / raw)
        if not template_path.is_file():
            print(
                f"  ! template file not found: {template_path}",
                file=sys.stderr,
            )
            return 2
    # --summary only takes effect when the index.md is rendered (new
    # space or --force-index). For an already-existing space without
    # --force-index, the index is left untouched — so --summary would
    # be silently dropped. Refuse loudly instead.
    if already_space and not args.force_index and getattr(args, "summary", None):
        print(
            "  ! --summary cannot be applied: "
            f"{rel}/ is already a space, and without --force-index its "
            "index.md is not re-rendered. Pass --force-index to overwrite "
            "with a freshly-rendered template, or edit the frontmatter "
            "by hand.",
            file=sys.stderr,
        )
        return 2
    if not already_space or args.force_index:
        display_name = args.name or new_space.name
        description_for_body = (args.description or "").strip() or None
        if template_path is not None:
            try:
                projected_text = _render_template_index_md(
                    template_path, display_name, description_for_body,
                    summary=(getattr(args, "summary", None) or "").strip() or None,
                )
            except RuntimeError as e:
                print(f"  ! {e}", file=sys.stderr)
                return 2
        else:
            projected_text = new_index_md(display_name, description_for_body)
        try:
            enforce_size_cap(new_space / "index.md", projected_text, wiki_root)
        except SizeCapExceeded as e:
            print(f"  ! size cap: {e}", file=sys.stderr)
            return 2
    # Pre-flight every ancestor write the chain helper would make. Refuses
    # BEFORE `mkdir` so an upper-ancestor cap overflow can't strand an empty
    # leaf directory. The in-lock cap check inside the chain helper still
    # catches concurrent growth.
    try:
        _preflight_chain_caps(
            wiki_root,
            new_space,
            leaf_description=(args.description or "").strip() or None,
        )
    except SizeCapExceeded as e:
        print(f"  ! size cap: {e}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ! could not read an ancestor index.md: {e}", file=sys.stderr)
        return 2

    # Pre-flight the target's OWN `## Spaces` insertion cap for the already-a-
    # space path. The real command repairs a bare existing target via
    # `_ensure_section_at` (which enforces the cap), but the dry-run branch
    # returns before it — so dry-run could preview success while the real run
    # refuses. Project the SAME insertion `_ensure_section_at` makes and enforce
    # the cap here (producer=consumer with the real path); the in-lock check
    # there stays authoritative against concurrent growth.
    if already_space and not args.force_index:
        target_index = new_space / "index.md"
        try:
            existing = target_index.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            existing = ""
        if not _md.has_section(existing, "Spaces"):
            projected = existing
            if projected and not projected.endswith("\n"):
                projected += "\n"
            projected += "\n## Spaces\n\n"
            try:
                enforce_size_cap(target_index, projected, wiki_root)
            except SizeCapExceeded as e:
                print(f"  ! size cap: {e}", file=sys.stderr)
                return 2

    # Dry-run AFTER every read-only preflight (target/ancestor external scope,
    # template existence, --summary applicability, leaf + ancestor size caps),
    # so `--dry-run` predicts the real command's refusals instead of previewing
    # a plan that would fail. Returns before the first FS mutation below.
    if getattr(args, "dry_run", False):
        if already_space and not args.force_index:
            print(f"  . (dry-run) {rel}/ already a space; would ensure ancestor entry")
        else:
            print(f"  . (dry-run) would create {rel}/index.md")
        print(
            f"  . (dry-run) would auto-insert `## Spaces` into ancestors "
            f"as needed and register {rel}/ in the nearest ancestor."
        )
        return 0

    created_dir_this_call = False
    created_index_this_call = False
    # `--force-index` overwrites an EXISTING index; capture its content so a
    # later chain-registration failure can restore it (read-before-write).
    overwritten_index_content: str | None = None
    # Track every directory THIS call creates (the leaf and any intermediate
    # parents materialized by `mkdir(parents=True)`), deepest-first, so
    # rollback can undo `space add a/b/c/d` against an empty wiki cleanly.
    created_dirs_this_call: list[Path] = []
    if already_space and not args.force_index:
        print(f"  . {rel}/ already a space; ensuring ancestor entry")
        # The pre-existing target's own index might lack `## Spaces` (a wiki
        # adopted from a folder of notes before v1). Repair it before we
        # register it upward — otherwise a re-registered existing space
        # would otherwise stay without `## Spaces`.
        try:
            _ensure_section_at(new_space, wiki_root)
        except RuntimeError as e:
            print(f"  ! {e}", file=sys.stderr)
            return 1
    else:
        # Track exactly what we create so rollback only undoes our own work,
        # never the user's pre-existing content.
        created_dir_this_call = not new_space.exists()
        if created_dir_this_call:
            probe = new_space
            while not probe.exists() and probe != wiki_root:
                created_dirs_this_call.append(probe)
                probe = probe.parent
        try:
            new_space.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # An unwritable parent dir makes mkdir fail. The physical-viability
            # guard checks containment + type, not writability, so refuse-and-
            # report here instead of dumping a Traceback; undo any parents this
            # call partially created (deepest-first, empty-only), matching the
            # EnsureChainError rollback below.
            if created_dir_this_call:
                for d in created_dirs_this_call:
                    try:
                        if d.exists() and not any(d.iterdir()):
                            d.rmdir()
                    except OSError:
                        pass
            print(f"  ! could not create {rel}/: {e}", file=sys.stderr)
            return 1
        new_index = new_space / "index.md"
        if _model.symlink_escapes_wiki(new_index, wiki_root):
            # `atomic_write` follows a symlink to its realpath; an escaping
            # `index.md` would push the write outside the trust boundary
            # (HANDBOOK: writes stay inside the trust boundary). Refuse and undo
            # any dirs THIS call created, mirroring the mkdir-failure rollback.
            if created_dir_this_call:
                for d in created_dirs_this_call:
                    try:
                        if d.exists() and not any(d.iterdir()):
                            d.rmdir()
                    except OSError:
                        pass
            print(
                f"  ! refusing to write {rel}/index.md: it is a symlink whose "
                "target resolves outside the wiki tree (writes stay inside the "
                "trust boundary). Replace the symlink with a regular file.",
                file=sys.stderr,
            )
            return 1
        created_index_this_call = not new_index.exists()
        if not created_index_this_call:
            # Overwriting an existing index via `--force-index`: snapshot the
            # old body so rollback can restore it if registration fails.
            try:
                overwritten_index_content = new_index.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                overwritten_index_content = None
        try:
            atomic_write(new_index, projected_text)
        except OSError as e:
            # Fail-closed: a failed index write must leave no orphaned space, so
            # the rollback mirrors the mkdir + chain-failure paths (HANDBOOK:
            # writes are atomic and fail-closed).
            if created_index_this_call:
                try:
                    new_index.unlink()
                except OSError:
                    pass
            elif overwritten_index_content is not None:
                try:
                    atomic_write(new_index, overwritten_index_content)
                except OSError:
                    pass
            if created_dir_this_call:
                for d in created_dirs_this_call:
                    try:
                        if d.exists() and not any(d.iterdir()):
                            d.rmdir()
                    except OSError:
                        pass
            print(f"  ! could not write {rel}/index.md: {e}", file=sys.stderr)
            return 1
        print(f"  + {rel}/index.md")
        # Report the cap via the same nearest-`_meta/limits.md` scope
        # `enforce_size_cap` just enforced, not the root default — else a
        # nested space's display contradicts its enforcement (producer=consumer).
        cap_verdict = scoped_size_verdict(new_index, projected_text, wiki_root).cap
        print(
            f"    (cap: {cap_verdict.cap} chars — "
            f"{_format_cap_source(cap_verdict.source)})"
        )

    # Register the new space in each ancestor's `## Spaces`, walking up
    # to the wiki root. The chain helper inserts `## Spaces` into any
    # bare-`index.md` ancestor it encounters as the first mutation step.
    # `--description` writes to BOTH the child's `## What this space is`
    # body AND the parent's `## Spaces` entry note — so `space list`
    # downstream sees the same description the user typed. Symmetric
    # with `space mount`, which also writes `--description` to the
    # parent entry.
    leaf_description = (args.description or "").strip() or None
    try:
        notices, _added = _ensure_spaces_chain_and_register(
            wiki_root,
            new_space,
            leaf_description=leaf_description,
        )
        for n in notices:
            print(n)
    except EnsureChainError as e:
        for n in e.notices:
            print(n)
        for _n in _rollback_added_entries(e.added):
            print(_n, file=sys.stderr)
        # Roll back our own FS creations (only what we made in THIS call).
        if created_index_this_call:
            try:
                (new_space / "index.md").unlink()
            except OSError:
                pass
        elif overwritten_index_content is not None:
            # Restore the pre-existing index `--force-index` overwrote, so a
            # registration failure never destroys the user's content.
            try:
                atomic_write(new_space / "index.md", overwritten_index_content)
            except OSError:
                pass
        if created_dir_this_call:
            for d in created_dirs_this_call:
                try:
                    if d.exists() and not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass
        print(f"  ! {e}", file=sys.stderr)
        return 1
    return 0

