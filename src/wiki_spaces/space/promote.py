from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
from .. import _md
from .. import _model
from .._common import atomic_write
from .._common import has_control_chars
from .._common import resolve_wiki
from . import _core
from ._core import (
    ChainExternalRefusal,
    EnsureChainError,
    SizeCapExceeded,
    _add_space_entry,
    _ensure_section_at,
    _ensure_spaces_chain_and_register,
    _is_in_external_scope,
    _nearest_ancestor_space,
    _preflight_chain_caps,
    _preflight_chain_external,
    _rollback_added_entries,
    _validate_rel_path,
    _walk_space_md_files,
    enforce_size_cap,
)

def _find_alias_owners(
    wiki_root: Path,
    *,
    walker: Callable[..., list[Path]] | None = None,
    include_external: bool = False,
) -> dict[str, list[Path]]:
    """Build `{alias.casefold(): [pages]}` across the walked file set.

    Sole caller is `cmd_promote`'s alias-collision preflight, which passes the
    contract walker so the check sees only consumer-visible pages; the default
    walker is `_model.discover_owned_md_files`. (The audit's duplicate-alias
    finding sources its data from `PageIndex`, not this helper.)

    Pages with no frontmatter or no `aliases:` contribute nothing. A
    single page declaring the same alias in multiple cases (e.g.
    `aliases: [bar, BAR]`) appears once.
    """
    if walker is None:
        walker = _model.discover_owned_md_files
    pages = walker(wiki_root, include_external=include_external)
    out: dict[str, list[Path]] = {}
    for page in pages:
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        seen_for_page: set[str] = set()
        for alias in _md.parse_frontmatter_aliases(text):
            key = alias.casefold()
            if key in seen_for_page:
                continue
            seen_for_page.add(key)
            out.setdefault(key, []).append(page)
    return out


def _is_git_tracked(path: Path, wiki_root: Path) -> bool:
    """True when `git ls-files --error-unmatch <rel>` returns 0."""
    try:
        rel = path.relative_to(wiki_root)
    except ValueError:
        return False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=wiki_root, capture_output=True, check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _mask_for_link_scan(text: str) -> str:
    """Return an offset-preserving mask of `text` that hides code spans and
    YAML frontmatter from a link scanner.

    Shared by `_rewrite_links_pointing_at` and `_adjust_outgoing_links_for_depth`
    so the scan is consistent across both promote-time rewriters — without
    this, the outgoing-link adjustment would happily rewrite `[[wikilinks]]`
    inside fenced code or frontmatter (producer/consumer break: the
    promote step would emit content the audit step misreads).

    Uses `_md.FRONTMATTER_RE.match` to locate the frontmatter span instead
    of `text.index("---", 4)` so atypical leading newlines / malformed
    fences don't throw.
    """
    m = _md.FRONTMATTER_RE.match(text)
    if m is not None:
        body_start = m.start(2)
        return (" " * body_start) + _md.mask_code_spans_offset_preserving(
            text[body_start:]
        )
    return _md.mask_code_spans_offset_preserving(text)


def _rewrite_links_pointing_at(
    *,
    text: str,
    page: Path,
    old_target: Path,
    new_target: Path,
    new_target_wikilink: str,
    wiki_root: Path,
    candidates: set[Path],
) -> str:
    """Rewrite markdown links AND wikilinks in `text` that resolve to
    `old_target` so they point at `new_target` instead.

    Markdown href is recomputed relative to `page`'s directory (preserves
    nested-page correctness — a wiki-root-relative rewrite is a
    silent-corruption risk for deep-linking pages).

    Wikilink target becomes `new_target_wikilink` (e.g. `projects/foo/index`).
    Display preserved when explicit; original target text used as display
    when none was present (rendered text never changes for the reader).

    Uses the unified `_md.resolve_wikilink` (wiki-root pathful first, then
    base-relative, then bare filename) — same precedence as audit, so a
    rewrite the promote step makes can never be misread as broken by a
    subsequent audit.
    """
    masked = _mask_for_link_scan(text)

    replacements: list[tuple[int, int, str]] = []
    for link in _md.parse_markdown_links(masked):
        resolved = _md.resolve_markdown_link(link.href, page, wiki_root)
        if resolved is None or resolved != old_target:
            continue
        new_href = _md.encode_markdown_href(
            _md.compute_relative_link(new_target, page)
        )
        new_substring = f"[{link.label}]({new_href}{link.anchor})"
        replacements.append((link.span[0], link.span[1], new_substring))

    for wl in _md.parse_wikilink_full(masked):
        resolved = _md.resolve_wikilink(
            wl.target, page.parent, candidates, wiki_root=wiki_root
        )
        if resolved is None or resolved != old_target:
            continue
        display = wl.display if wl.display is not None else wl.target
        anchor = f"#{wl.anchor}" if wl.anchor else ""
        new_inner = f"{new_target_wikilink}{anchor}|{display}"
        replacements.append((wl.span[0], wl.span[1], f"[[{new_inner}]]"))

    if not replacements:
        return text
    replacements.sort(reverse=True)
    out = text
    for start, end, new in replacements:
        out = out[:start] + new + out[end:]
    return out


def _adjust_outgoing_links_for_depth(
    *,
    text: str,
    original_file: Path,
    new_file: Path,
    wiki_root: Path,
) -> str:
    """Adjust the promoted file's own outgoing relative markdown links for
    its new (one-level-deeper) location.

    `[label](rel.md#anchor)` was resolved against `original_file.parent`.
    After the move, the same relative path resolves against `new_file.parent`
    (one level deeper). Rewrite the href so it still resolves to the same
    absolute target. Self-links (`[label](mypage.md)` inside the promoted
    file itself) are re-pointed at `new_file`.
    """
    original_resolved = original_file.resolve()
    new_file_resolved = new_file
    try:
        new_file_resolved = new_file.resolve()
    except OSError:
        pass
    # Mask code spans + frontmatter for the scan — same protection as
    # `_rewrite_links_pointing_at`. Without this, a `[label](file.md)`
    # inside a fenced code block or YAML frontmatter would be rewritten as
    # if it were a real markdown link.
    masked = _mask_for_link_scan(text)
    replacements: list[tuple[int, int, str]] = []
    for link in _md.parse_markdown_links(masked):
        target = _md.resolve_markdown_link(link.href, original_file, wiki_root)
        if target is None:
            continue
        if target == original_resolved:
            target = new_file_resolved
        new_href = _md.encode_markdown_href(
            _md.compute_relative_link(target, new_file)
        )
        new_substring = f"[{link.label}]({new_href}{link.anchor})"
        if new_substring != text[link.span[0]:link.span[1]]:
            replacements.append((link.span[0], link.span[1], new_substring))
    if not replacements:
        return text
    replacements.sort(reverse=True)
    out = text
    for start, end, new in replacements:
        out = out[:start] + new + out[end:]
    return out


def _restore_from_snapshot(snapshot_dir: Path, wiki_root: Path) -> None:
    """Overwrite every wiki file with its snapshot copy (best-effort)."""
    for snap_file in snapshot_dir.rglob("*"):
        if not snap_file.is_file():
            continue
        rel = snap_file.relative_to(snapshot_dir)
        dest = wiki_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap_file, dest)




def cmd_promote(args: argparse.Namespace) -> int:
    wiki_root, _err = resolve_wiki(args.wiki, repair=True)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2

    ok, err = _validate_rel_path(args.path)
    if not ok:
        print(f"  ! invalid path: {err}", file=sys.stderr)
        return 2
    rel = args.path.strip().rstrip("/")
    if not rel.endswith(".md"):
        print(f"  ! {rel} must be a .md file", file=sys.stderr)
        return 2

    source = wiki_root / rel
    if not source.is_file():
        print(f"  ! {rel} does not exist or is not a regular file", file=sys.stderr)
        return 2
    if source.name == "index.md":
        print("  ! cannot promote an index.md", file=sys.stderr)
        return 2

    external, reason = _is_in_external_scope(source.parent, wiki_root)
    if external:
        print(
            f"  ! refusing to promote in external scope: {reason}",
            file=sys.stderr,
        )
        return 2

    # Promote moves the source file (via `source.rename(target)` or `git mv`)
    # then writes the new content via `atomic_write(target, ...)`. If `source`
    # is a symlink, the rename moves the SYMLINK, and the subsequent write
    # follows the link and overwrites the TARGET — for an
    # escaping `.md` symlink, that target lives outside the wiki tree.
    # Refuse outright: promote's mechanic assumes the source is a regular
    # file under owned scope. Symlinked sources point at content we don't
    # own; rewriting them would mutate someone else's content silently.
    if source.is_symlink():
        if _model.symlink_escapes_wiki(source, wiki_root):
            print(
                f"  ! refusing to promote {rel}: source is a symlink whose "
                "target resolves outside the wiki tree (external content).",
                file=sys.stderr,
            )
        else:
            print(
                f"  ! refusing to promote {rel}: source is a symlink. "
                "Promote moves the link and rewrites via the link, which "
                "would mutate the symlink target unexpectedly. Operate on "
                "the resolved file directly, or replace the symlink with a "
                "regular file first.",
                file=sys.stderr,
            )
        return 2

    target = source.with_suffix("") / "index.md"
    target_dir = target.parent
    target_rel = target.relative_to(wiki_root).as_posix()
    # Validate the derived target path against the same reserved-folder
    # rules `space add` enforces — `promote _meta.md` would otherwise
    # create `_meta/index.md`, which every consumer walker prunes per
    # CONVENTIONS / Reserved top-level folder names. Same producer/
    # consumer break the validator was added to prevent on the `add`
    # surface.
    target_rel_from_wiki = target_dir.relative_to(wiki_root).as_posix()
    ok, why = _validate_rel_path(target_rel_from_wiki)
    if not ok:
        print(
            f"  ! cannot promote {source.relative_to(wiki_root).as_posix()}: "
            f"derived target {target_rel_from_wiki}/ is not a valid space "
            f"path ({why}). Rename the source file first.",
            file=sys.stderr,
        )
        return 2
    if target_dir.exists() or target_dir.is_symlink():
        # Symlink target dir: even if it's empty, `source.rename(target)`
        # would follow the link and write into whatever the symlink
        # resolves to — including outside the wiki tree. Refuse before the
        # rename, mirroring the symlinked-source refusal above.
        if target_dir.is_symlink():
            print(
                f"  ! refusing to promote into {target_dir.relative_to(wiki_root).as_posix()}/: "
                "target directory is a symlink. Promote would follow the "
                "link and create `index.md` at the symlink target, mutating "
                "content the wiki may not own. Remove or rename the symlink "
                "before promoting.",
                file=sys.stderr,
            )
            return 2
        if not target_dir.is_dir():
            # `exists()` is true but it is a regular file (or other non-dir):
            # the derived `<stem>/` path collides with existing content.
            # Refuse before any mutation — otherwise `target_dir.mkdir()`
            # below raises `FileExistsError` mid-flight (HANDBOOK: handle
            # failures at boundaries, refuse don't crash).
            print(
                f"  ! target {target_dir.relative_to(wiki_root).as_posix()}/ "
                "exists and is not a directory; refusing",
                file=sys.stderr,
            )
            return 2
        try:
            entries = list(target_dir.iterdir())
        except OSError:
            entries = []
        if entries:
            print(
                f"  ! target {target_dir.relative_to(wiki_root).as_posix()}/ "
                "already exists with content; refusing",
                file=sys.stderr,
            )
            return 2

    ancestor = _nearest_ancestor_space(wiki_root, source)
    ancestor_index = ancestor / "index.md"
    ancestor_rel = ancestor.relative_to(wiki_root)
    printable = "<wiki>/" if str(ancestor_rel) == "." else f"<wiki>/{ancestor_rel}/"
    try:
        ancestor_text = ancestor_index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ! could not read {printable}index.md: {e}", file=sys.stderr)
        return 1
    # If the ancestor's `## Spaces` is missing, `_promote_mutate` (below) inserts
    # it inside the locked region — no separate refuse path.

    basename = source.stem
    source_resolved = source.resolve()

    # Contract-walker adapter: `_find_alias_owners` expects a walker
    # returning a flat iterable of `.md` paths. `_model.discover_md_files`
    # yields `ConsumerFile`; adapt by projecting `.path`.
    # Promote's alias checks and link rewrites consume contract-first
    # traversal — files inside unregistered (drift) spaces are NOT
    # consumer-visible and must not be mutated by promote. Audit /
    # `audit --fix` is the surface that touches drift; promote is a
    # consumer-side write that respects the navigation contract.
    def _contract_md_walker(
        root: Path, *, include_external: bool = False
    ) -> Iterator[Path]:
        for cf in _model.discover_md_files(
            root, include_external=include_external
        ):
            yield cf.path

    if not args.skip_aliases:
        owners = _find_alias_owners(wiki_root, walker=_contract_md_walker)
        collisions = owners.get(basename.casefold(), [])
        external_owners = [p for p in collisions if p.resolve() != source_resolved]
        if external_owners:
            other = external_owners[0].relative_to(wiki_root).as_posix()
            print(
                f"  ! alias collision: {other} already declares alias "
                f"'{basename}' (case-insensitive). "
                "Re-run with --skip-aliases to promote without alias injection.",
                file=sys.stderr,
            )
            return 2

    try:
        source_text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(
            f"  ! could not read {source.relative_to(wiki_root).as_posix()}: {e}",
            file=sys.stderr,
        )
        return 1
    fm_result = _model.parse_frontmatter_result(source_text)
    if fm_result.status in (
        _model.FrontmatterStatus.MALFORMED,
        _model.FrontmatterStatus.NON_MAPPING,
    ):
        # Refuse rather than silently dropping content: promote rewrites the
        # file (link adjust + alias), and proceeding past frontmatter we can't
        # parse would mangle or wipe it. Audit surfaces the same state as a
        # finding — promote refuses to operate on it (read before write).
        print(
            f"  ! refusing to promote {rel}: its frontmatter is "
            f"{fm_result.status.value} — fix or remove it first so promote "
            "does not rewrite the file with corrupted metadata.",
            file=sys.stderr,
        )
        return 2
    fm = fm_result.data or {}
    summary = fm.get("summary")
    if isinstance(summary, list):
        summary = " ".join(str(s) for s in summary)
    description = (str(summary).strip() if summary else "") or None
    # `summary` is a YAML boundary input written verbatim into the parent's
    # `## Spaces` entry note. A line-break char would split that bullet for the
    # consumer (`str.splitlines()`), injecting a sibling entry or stray heading
    # the walker reads as real (HANDBOOK: distrust boundary inputs;
    # producer=consumer). `add`/`mount` already guard their `## Spaces`
    # descriptions; `promote` must too. Refuse before any FS mutation.
    if description is not None and has_control_chars(description):
        print(
            f"  ! refusing to promote {rel}: its frontmatter `summary` contains "
            "newline / control characters that would split the parent `## Spaces` "
            "entry across lines and corrupt the navigation contract. Fix the "
            "summary first.",
            file=sys.stderr,
        )
        return 2

    # Build candidate set from the contract-first md walk. Drift files
    # (in unregistered spaces, hidden, `_archives/`, `_meta/`) are
    # invisible to the consumer; promote does not
    # rewrite links inside them. Audit `--fix` is the repair surface
    # for drift visibility; promote stays consumer-aligned.
    all_md_files = list(_contract_md_walker(wiki_root))
    # The source file itself may not be contract-reachable yet (it's a
    # plain `.md` inside its ancestor space); union it in so its own
    # rewrites work.
    candidates: set[Path] = {p.resolve() for p in all_md_files}
    candidates.add(source_resolved)
    # Also union in every `.md` file inside `ancestor` (using plain-folder
    # semantics — stop at child-space boundaries). The chain repair below
    # will register `ancestor/` upward and insert `## Spaces` into it if
    # missing, making `ancestor`'s subtree consumer-visible AFTER promote
    # completes. Files that become visible MUST have their links to the
    # promoted source rewritten in the same operation — otherwise promote
    # leaves a producer/consumer break of its own making (sibling links
    # pointing at the now-moved source survive into visible state). When
    # `ancestor` was ALREADY contract-reachable, the union is a no-op
    # (already in `all_md_files`); when it wasn't, this catches the soon-
    # to-be-visible siblings.
    ancestor_md_files = list(_walk_space_md_files(ancestor, wiki_root))
    candidates.update(p.resolve() for p in ancestor_md_files)

    # Compute the post-move absolute target (for link rewriting).
    new_target_abs = target
    new_target_wikilink = source.with_suffix("").relative_to(wiki_root).as_posix() + "/index"

    # Plan rewrites across all owned md files reachable now OR through the
    # chain-repair propagation below. Dedupe on resolved path so files that
    # appear in both `all_md_files` and `ancestor_md_files` are processed
    # once. Sort for deterministic plan ordering across runs.
    rewrite_targets: dict[Path, Path] = {}
    for page in (*all_md_files, *ancestor_md_files):
        try:
            real = page.resolve()
        except OSError:
            continue
        if real == source_resolved:
            continue
        rewrite_targets.setdefault(real, page)
    planned: list[tuple[Path, str]] = []
    rewrite_files = 0
    for page in sorted(rewrite_targets.values()):
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text = _rewrite_links_pointing_at(
            text=text,
            page=page,
            old_target=source_resolved,
            new_target=new_target_abs,
            new_target_wikilink=new_target_wikilink,
            wiki_root=wiki_root,
            candidates=candidates,
        )
        if new_text != text:
            planned.append((page, new_text))
            rewrite_files += 1

    # Plan promoted file's outgoing-link adjustment + ## Spaces + aliases.
    new_source_text = _adjust_outgoing_links_for_depth(
        text=source_text,
        original_file=source,
        new_file=target,
        wiki_root=wiki_root,
    )
    if not _md.has_section(new_source_text, "Spaces"):
        if new_source_text and not new_source_text.endswith("\n"):
            new_source_text += "\n"
        new_source_text += "\n## Spaces\n\n"
    aliases_added = False
    if not args.skip_aliases:
        new_source_text, aliases_added = _md.frontmatter_add_alias(new_source_text, basename)

    rel_from_ancestor = target.parent.relative_to(ancestor)
    label = f"{rel_from_ancestor}/"
    href = f"{rel_from_ancestor}/index.md"

    # Preflight EVERY projected write against its size cap BEFORE we
    # touch the filesystem. Promote produces several mutations (the new
    # `index.md`, the planned rewrites, the ancestor's `## Spaces` entry);
    # without the preflight, a cap rejection mid-mutation would leave a
    # half-promoted tree. Project the ancestor's `_add_space_entry` result
    # against the OUTER-read ancestor text — a concurrent writer could
    # change ancestor text between this check and `_atomic_mutate_index`,
    # but the in-helper cap check catches that.
    try:
        enforce_size_cap(target, new_source_text, wiki_root)
        for page, new_text in planned:
            if page.resolve() == ancestor_index.resolve():
                # Skip the standalone ancestor-rewrite preflight here — we
                # project the COMBINED ancestor mutation (rewrite + section
                # insert + entry add) below so the cap is evaluated against
                # the same text the in-lock `_promote_mutate` will write.
                # Checking only the rewrite-alone result here would mask a
                # combined-growth overflow.
                continue
            enforce_size_cap(page, new_text, wiki_root)
        # Build the projected ancestor text the same way `_promote_mutate`
        # builds it: start from whatever rewrite the planned pass produced
        # for the ancestor (if any), then insert `## Spaces` if missing, then
        # add the new entry. Project against the OUTER-read ancestor_text;
        # the in-lock check at `_promote_mutate` re-evaluates against fresh
        # text to catch any concurrent growth between preflight and lock.
        ancestor_after_rewrite = next(
            (
                new_text
                for page, new_text in planned
                if page.resolve() == ancestor_index.resolve()
            ),
            ancestor_text,
        )
        projected_ancestor = ancestor_after_rewrite
        if not _md.has_section(projected_ancestor, "Spaces"):
            if projected_ancestor and not projected_ancestor.endswith("\n"):
                projected_ancestor += "\n"
            projected_ancestor += "\n## Spaces\n\n"
        projected_ancestor = _add_space_entry(
            projected_ancestor, label, href, description
        )
        enforce_size_cap(ancestor_index, projected_ancestor, wiki_root)
    except SizeCapExceeded as e:
        print(
            f"  ! size cap: {e}. Aborted before any FS write.",
            file=sys.stderr,
        )
        return 2

    # Dry-run AFTER the size-cap preflight (above) and the external-scope check
    # (earlier), so `--dry-run` predicts the real refusals; returns before any
    # FS mutation in the chain repair / move below.
    if args.dry_run:
        print(f"  . (dry-run) would move {rel} -> {target_rel}")
        print(f"  . (dry-run) would rewrite links in {rewrite_files} file(s)")
        if aliases_added:
            print(f"  . (dry-run) would add alias '{basename}' to {target_rel}")
        if not _md.has_section(ancestor_text, "Spaces"):
            print(f"  . (dry-run) would insert `## Spaces` into {printable}index.md")
        print(f"  . (dry-run) would register entry under {printable}index.md ## Spaces")
        return 0

    # Propagate `## Spaces` repair up the chain BEFORE any FS mutation in
    # this command. `_promote_mutate` below only repairs the immediate
    # ancestor; bare intermediate ancestors (e.g., a hand-curated
    # `projects/index.md` lacking `## Spaces`, with the wiki root above)
    # would leave the wiki unreachable to strict consumers even after a
    # successful promote (v1 contract: write commands maintain the
    # navigation contract on every ancestor they cross). Done first so a
    # mid-walk failure leaves zero filesystem state to roll back — no
    # half-rewritten links in `ancestor_index`, no orphan target dir.
    # Chain-added entries that survive a later promote failure stay (they
    # are append-only and consistent — `ancestor/` being registered upward
    # is the correct end-state regardless of whether promote completed).
    #
    # Order matters within the chain repair: insert `## Spaces` in
    # `ancestor/index.md` FIRST, then register `ancestor/` upward. If we
    # registered upward before repairing ancestor's own section and the
    # promote then failed, the rollback would leave `ancestor/` advertised
    # in its parent's `## Spaces` while `ancestor/index.md` itself stayed
    # bare — a producer/consumer break the contract walker would skip.
    if ancestor != wiki_root:
        # Defense-in-depth: refuse if the chain helper would register into an
        # external ancestor's `index.md`. The top-level external check on
        # `source.parent` already walks the full ancestor chain to root (and
        # the promote ancestor chain is a subset of it), so for any valid
        # promote this never fires; keeping the call here guards all three
        # chain-helper callers uniformly and survives future refactors of the
        # top-level check. promote has no --force-external flag (getattr -> False).
        try:
            _preflight_chain_external(
                wiki_root, ancestor,
                force_external=getattr(args, "force_external", False),
            )
        except ChainExternalRefusal as e:
            print(
                f"  ! refusing to register into external ancestor "
                f"{e.ancestor.relative_to(wiki_root).as_posix()}/index.md: "
                f"{e.reason}.",
                file=sys.stderr,
            )
            return 2
        # Preflight every ancestor write the chain helper would make,
        # BEFORE the section insert below. The outer preflight
        # above only projects the IMMEDIATE ancestor's combined write;
        # an upper-ancestor cap overflow (registering `ancestor/` in
        # grandparent, or grandparent's own section insert) would
        # otherwise be caught only after we'd already mutated
        # `ancestor/index.md`. The "preflight ALL planned writes
        # BEFORE any FS mutation" requires catching that here.
        try:
            _preflight_chain_caps(wiki_root, ancestor)
        except SizeCapExceeded as e:
            print(
                f"  ! size cap (chain repair): {e}. Aborted before any FS write.",
                file=sys.stderr,
            )
            return 2
        except (OSError, UnicodeDecodeError) as e:
            print(
                f"  ! could not read an ancestor index.md (chain repair): {e}",
                file=sys.stderr,
            )
            return 1
        try:
            _ensure_section_at(ancestor, wiki_root)
        except RuntimeError as e:
            print(
                f"  ! could not insert `## Spaces` into {printable}index.md: {e}",
                file=sys.stderr,
            )
            return 1
        try:
            chain_notices, _chain_added = _ensure_spaces_chain_and_register(
                wiki_root, ancestor
            )
            for n in chain_notices:
                print(n)
        except EnsureChainError as ce:
            for n in ce.notices:
                print(n)
            for _n in _rollback_added_entries(ce.added):
                print(_n, file=sys.stderr)
            print(f"  ! chain propagation failed: {ce}", file=sys.stderr)
            return 1

    # Snapshot every affected file outside the wiki tree. We DELIBERATELY
    # exclude `ancestor_index` here — it is mutated only inside
    # `_atomic_mutate_index` (lock-protected atomic write). Including a
    # pre-lock snapshot of it in the rollback set would let `_restore_from_snapshot`
    # overwrite concurrent writes that committed between our outer read at
    # the top of `cmd_promote` and the lock acquisition inside the helper.
    ancestor_index_resolved = ancestor_index.resolve()
    snapshot_dir = Path(tempfile.mkdtemp(prefix="wiki-spaces-promote-"))
    # Fail-closed: keep the snapshot unless we confirm the tree is in a
    # known-good state — a clean success, or a fully successful rollback. If
    # rollback fails (or an unexpected exception propagates), the snapshot the
    # error message points at must still be on disk for manual recovery.
    # Mirrors `cmd_remove`'s snapshot guard.
    keep_snapshot = True
    # Bound BEFORE the try so the rollback handler (which reads them) can never
    # raise UnboundLocalError if the body fails before they would be set — e.g.
    # a snapshot copy raising before the mutate step. `target_dir_pre_existed`
    # is read by the rmdir-rollback below and the snapshot phase never creates
    # `target_dir`, so probing it here is equivalent to probing it post-snapshot.
    moved_via_git = False
    target_dir_pre_existed = target_dir.exists()
    try:
        snapshot_set = {source.resolve()}
        for p, _new in planned:
            p_resolved = p.resolve()
            if p_resolved == ancestor_index_resolved:
                continue
            snapshot_set.add(p_resolved)
        wiki_root_resolved = wiki_root.resolve()
        for p in snapshot_set:
            try:
                rel_p = p.relative_to(wiki_root_resolved)
            except ValueError:
                continue
            snap = snapshot_dir / rel_p
            snap.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, snap)

        # Mutate.
        target_dir.mkdir(parents=True, exist_ok=True)
        if (wiki_root / ".git").exists() and _is_git_tracked(source, wiki_root):
            try:
                subprocess.run(
                    ["git", "mv",
                     str(source.relative_to(wiki_root)),
                     str(target.relative_to(wiki_root))],
                    cwd=wiki_root, check=True, capture_output=True, text=True,
                )
                moved_via_git = True
            except subprocess.CalledProcessError:
                pass
        if not moved_via_git:
            source.rename(target)

        atomic_write(target, new_source_text)

        # Apply planned rewrites EXCEPT the ancestor's index.md — the ancestor
        # mutation runs under flock via `_atomic_mutate_index`, recomputing
        # link rewrites against the FRESH text it reads inside the lock so a
        # concurrent `space add` can't clobber our rewrites.
        for page, new_text in planned:
            if page.resolve() == ancestor_index_resolved:
                continue
            atomic_write(page, new_text)

        def _promote_mutate(fresh_text: str) -> _core.MutateResult:
            text = fresh_text
            inserted = False
            if not _md.has_section(text, "Spaces"):
                if text and not text.endswith("\n"):
                    text += "\n"
                text += "\n## Spaces\n\n"
                inserted = True
            # Re-apply the link rewrite against fresh text so a concurrent
            # writer can't clobber what we just rewrote.
            rewritten = _rewrite_links_pointing_at(
                text=text,
                page=ancestor_index,
                old_target=source_resolved,
                new_target=new_target_abs,
                new_target_wikilink=new_target_wikilink,
                wiki_root=wiki_root,
                candidates=candidates,
            )
            final = _add_space_entry(rewritten, label, href, description)
            entry_added = final != rewritten
            if inserted and entry_added:
                tag = "inserted-and-added"
            elif inserted:
                tag = "inserted"
            elif entry_added:
                tag = "added"
            else:
                tag = "noop"
            # The outer preflight projects the cap against the
            # OUTER-read ancestor text. A concurrent writer can grow the
            # ancestor between preflight and lock — re-check inside the
            # locked region against the actual projected text. Cap rejection
            # returns the `(None, rc, reason)` abort tuple per the
            # `_atomic_mutate_index` protocol.
            try:
                enforce_size_cap(ancestor_index, final, wiki_root)
            except SizeCapExceeded as e:
                return (None, 2, f"size cap: {e}")
            return (final, tag)

        rc_a, info = _core._atomic_mutate_index(
            ancestor, ancestor_index, _promote_mutate
        )
        if rc_a != 0:
            raise RuntimeError(f"ancestor mutation failed: {info}")
        if info in ("inserted", "inserted-and-added"):
            print(f"  ~ {printable}index.md  +inserted `## Spaces`")
        if info in ("added", "inserted-and-added"):
            print(f"  ~ {printable}index.md ## Spaces  += [{label}]")

        print(f"  + promoted {rel} -> {target_rel}")
        if rewrite_files:
            print(f"  ~ rewrote links in {rewrite_files} file(s)")
        if aliases_added:
            print(f"  ~ added alias '{basename}' to {target_rel}")
        keep_snapshot = False  # success: tree is consistent, snapshot not needed
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, shutil.Error) as e:
        print(f"  ! mutation failed mid-promote: {e}", file=sys.stderr)
        try:
            # Undo the move FIRST (if it happened) so the snapshot restore
            # can put the source back at its original path without colliding
            # with the moved file. Then restore other touched files. Then
            # rmdir the (now-empty) target_dir.
            if target.is_file():
                try:
                    target.unlink()
                except OSError as unlink_err:
                    print(
                        f"  ! could not remove partial target {target}: {unlink_err}",
                        file=sys.stderr,
                    )
            _restore_from_snapshot(snapshot_dir, wiki_root)
            # If we ran `git mv`, the staging index is now in a dirty state
            # (the move was staged, but we've put the file back via snapshot
            # restore, so the staged rename doesn't match the working tree).
            # Unstage so `git status` shows a clean tree. Best-effort: a
            # failure here doesn't break the working-tree rollback, only
            # the staging-area cleanup.
            if moved_via_git:
                try:
                    subprocess.run(
                        ["git", "reset", "HEAD",
                         str(source.relative_to(wiki_root)),
                         str(target.relative_to(wiki_root))],
                        cwd=wiki_root, check=False, capture_output=True, text=True,
                    )
                except (subprocess.SubprocessError, FileNotFoundError) as git_err:
                    print(
                        f"  ! could not unstage rolled-back git mv: {git_err}. "
                        "Run `git reset HEAD <paths>` manually if `git status` "
                        "shows phantom rename.",
                        file=sys.stderr,
                    )
            # Only remove target_dir if WE created it. A pre-existing empty
            # directory (e.g., user did `mkdir page/` before running promote)
            # must survive rollback.
            if not target_dir_pre_existed and target_dir.exists():
                try:
                    if not list(target_dir.iterdir()):
                        target_dir.rmdir()
                except OSError:
                    pass  # non-empty or permission issue — leave for the user
            print("  . rolled back from snapshot", file=sys.stderr)
            keep_snapshot = False  # rollback succeeded: tree restored from disk
        except (OSError, shutil.Error) as restore_err:
            # keep_snapshot stays True — the message below promises the
            # snapshot for manual recovery, so the finally must not delete it.
            print(
                f"  ! ROLLBACK ALSO FAILED: {restore_err}. "
                f"Manual recovery from {snapshot_dir} may be required.",
                file=sys.stderr,
            )
        return 2
    finally:
        if not keep_snapshot:
            shutil.rmtree(snapshot_dir, ignore_errors=True)

