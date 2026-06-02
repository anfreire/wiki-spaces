from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from .. import _md
from .. import _model
from .._common import resolve_wiki
from . import _core
from ._core import (
    EnsureChainError,
    SizeCapExceeded,
    _SPACES_HREF_METACHARS,
    _add_space_entry,
    _atomic_remove_from_spaces,
    _ensure_section_at,
    _ensure_spaces_chain_and_register,
    _format_cap_source,
    _is_in_external_scope,
    _rel_or_str,
    _rollback_added_entries,
    _walk_classified,
    enforce_size_cap,
    scoped_size_verdict,
)

_AUDIT_EXEMPT_FILES = frozenset({"index.md", "log.md", "hot.md", "_template.md"})

# Files exempt from the MALFORMED-FRONTMATTER check — ONLY `_template.md`. Its
# `{{ … }}` placeholders are substituted at render time and are deliberately NOT
# valid YAML, so auditing the raw template as a content page contradicts
# producer=consumer: `init --with _template.md` writes a file the same tool's
# `audit` would then condemn. The other special files CAN carry real frontmatter
# (a space's `index.md` aliases, a `log.md`/`hot.md` header); malformed YAML
# there silently drops the page's aliases, so it stays in scope — the exhaustive
# release gate must surface it. This is a strict subset of _AUDIT_EXEMPT_FILES:
# the two checks have different exemption rationales and must not share a set.
_FRONTMATTER_AUDIT_EXEMPT_FILES = frozenset({"_template.md"})


def _blank_spaces_section(body: str) -> str:
    """Blank the `## Spaces` section body so the broken-wikilink scan ignores
    navigation entries.

    `## Spaces` is the navigation contract (`- [label](href)`), not content.
    A stray `- [[child]]` there is a (malformed) navigation entry, not a
    content cross-reference; scanning it as a content wikilink double-reports
    it (broken link AND missing-entry drift). Returns `body` unchanged when
    there is no `## Spaces` section (content pages). Line count is preserved.
    """
    block = _model.parse_section_block(body, "Spaces")
    if block is None:
        return body
    start, end = block.body_span
    lines = body.splitlines()
    for i in range(start, min(end, len(lines))):
        lines[i] = ""
    return "\n".join(lines)


def _audit_content(
    wiki_root: Path, *, include_external: bool = False,
    md_files: list[Path] | None = None,
) -> tuple[
    list[tuple[Path, str, "_model.WikilinkResolution"]],
    list[Path],
    list[tuple[Path, "_model.FrontmatterResult"]],
    list[tuple[str, list[Path]]],
]:
    """Scan owned markdown for broken wikilinks, orphan pages, malformed
    frontmatter, and duplicate aliases.

    Returns `(broken, orphans, malformed_frontmatter)`:
    - `broken`  — `(page, target, resolution)` for each plain `[[wikilink]]`
      whose `_model.resolve_wikilink` returns `WikilinkStatus.UNRESOLVED`.
      The `resolution` object carries the ordered `attempts` trace so
      the JSON consumer can show which lookup strategies tried what and
      missed. Ambiguous aliases are NOT reported as broken; they reach
      a target deterministically (sorted candidate). Embeds (`![[...]]`)
      are excluded — they routinely target non-page assets.
    - `orphans` — content pages with zero incoming wikilinks, sorted.
      `index.md` and `log.md` exempt.
    - `malformed_frontmatter` — `(page, FrontmatterResult)` for each page
      whose YAML frontmatter failed to parse. Previously these were
      silently dropped (the `_md` wrapper returns `None` for both ABSENT
      and MALFORMED); the model layer's `FrontmatterStatus` distinguishes
      so the audit can flag them.

    Routes through `_model.build_page_index` + `_model.resolve_wikilink`
    so the audit, promote, and search consumers all share one resolver.
    Ambiguous-alias pages live in `page_index.duplicate_aliases` and are
    surfaced separately by the caller — derived from that same index (one
    alias parse), NOT from a second `_find_alias_owners` walk (that walker
    is promote's own; audit does not call it).

    `md_files` may be passed in by `cmd_audit` to reuse its single owned-files
    walk; when None it is discovered here (the standalone-call path).
    """
    if md_files is None:
        md_files = _model.discover_owned_md_files(
            wiki_root, include_external=include_external
        )

    def _real(p: Path) -> Path:
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return p

    page_index = _model.build_page_index(_real(f) for f in md_files)

    # Read post-frontmatter bodies once for the link scan.
    bodies: dict[Path, str] = {}
    for f in md_files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        _, bodies[f] = _md.split_frontmatter(text)

    broken: list[tuple[Path, str, _model.WikilinkResolution]] = []
    incoming: set[Path] = set()
    for f, body in bodies.items():
        f_real = _real(f)
        # `## Spaces` is the navigation contract ONLY in `index.md`. Blank it
        # there so navigation entries (incl. a stray `[[child]]`) aren't
        # scanned as content links. In a non-index content page, `## Spaces`
        # is just an ordinary heading, so its wikilinks ARE content and must
        # still be audited — blanking them everywhere would hide broken links
        # and make audit non-exhaustive.
        nav_body = _blank_spaces_section(body) if f.name == "index.md" else body
        scan_body = _md.strip_code_spans(nav_body)
        for link, is_embed in _md.find_wikilink_refs(scan_body):
            res = _model.resolve_wikilink(
                link, f.parent, page_index, wiki_root=wiki_root,
            )
            if res.status == _model.WikilinkStatus.RESOLVED:
                if res.target is not None and res.target != f_real:
                    incoming.add(res.target)
                continue
            if res.status == _model.WikilinkStatus.AMBIGUOUS_ALIAS:
                # An ambiguous alias resolves to *something* (just not
                # deterministically). Pick the first candidate by sort
                # order so orphan counting is reproducible across runs;
                # the user-visible duplicate_aliases finding (sourced
                # from `page_index.duplicate_aliases`) is what flags the
                # ambiguity itself.
                chosen = res.candidates[0]
                if chosen != f_real:
                    incoming.add(chosen)
                continue
            # UNRESOLVED. Only plain `[[links]]` are flagged broken;
            # `![[...]]` embeds routinely target non-page assets.
            if not is_embed:
                broken.append((f, link, res))

    orphans = [
        f for f in md_files
        if f.name not in _AUDIT_EXEMPT_FILES and _real(f) not in incoming
    ]
    # Both the malformed-frontmatter and duplicate-alias findings come from the
    # SAME `page_index`, whose keys are RESOLVED paths (`_real`). Map them back
    # to the caller's raw `md_files` paths so the audit prints them relative to
    # the LEXICAL wiki_root: a resolved path escapes the tree under a symlink-
    # mounted external space (`shared/foo` → outside the wiki), and the
    # renderers' `page.relative_to(wiki_root)` would raise `ValueError` on it —
    # crashing the whole `audit --include-external` with a raw traceback
    # (HANDBOOK: handle failures at boundaries; producer=consumer — one map,
    # both findings).
    raw_by_real: dict[Path, Path] = {_real(f): f for f in md_files}
    # Frontmatter errors come from build_page_index; surface them so the
    # audit can flag malformed YAML rather than silently dropping the
    # page's aliases. Only `_template.md` is exempt (see
    # _FRONTMATTER_AUDIT_EXEMPT_FILES): its `{{ … }}` placeholders are render-
    # time substitutions, not page frontmatter. index.md/log.md/hot.md stay in
    # scope — malformed YAML there is a real finding the release gate must show.
    malformed_frontmatter = sorted(
        (
            (raw_by_real.get(page, page), result)
            for page, result in page_index.frontmatter_errors.items()
            if page.name not in _FRONTMATTER_AUDIT_EXEMPT_FILES
        ),
        key=lambda kv: str(kv[0]),
    )
    duplicate_aliases = sorted(
        (alias, sorted(raw_by_real.get(p, p) for p in pages))
        for alias, pages in page_index.duplicate_aliases.items()
    )
    return broken, sorted(orphans), malformed_frontmatter, duplicate_aliases


def _summary_header(
    wiki_root: Path,
    all_spaces: list[Path],
    nodes: list["_model.NodeFacts"],
    pages: int,
    *,
    include_external: bool = False,
) -> list[str]:
    convention_files = [
        "log.md", "_meta/taxonomy.md", "_meta/limits.md", ".manifest.json",
        "hot.md", "_template.md", ".obsidian",
    ]
    present = [c for c in convention_files if (wiki_root / c).exists()]

    if include_external:
        scope_desc = "owned + external scope (excludes hidden / _archives)"
    else:
        scope_desc = "owned scope; excludes hidden / _archives / external"

    # Partition the spaces count into contract-reachable vs.
    # drift so the user sees both facts at a glance. Audit walks the
    # filesystem (every space-shaped folder) while list walks the navigation
    # contract (only registered ones); the model carries both, so the audit
    # surfaces the partition rather than just one of the two numbers. `nodes`
    # and `pages` are passed in from cmd_audit's single discovery pass.
    spaces = [
        n for n in nodes
        if n.has_index and n.has_spaces_section
        and n.trust.scope == _model.TrustScope.OWNED
    ]
    reachable = [n for n in spaces if n.contract_reachable]
    drift = [n for n in spaces if not n.contract_reachable]
    spaces_line = f"  spaces: {len(all_spaces)}"
    if drift:
        spaces_line += f" ({len(reachable)} contract-reachable, {len(drift)} drift)"

    lines = [
        f"wiki: {wiki_root}",
        spaces_line,
        f"  pages:  {pages} markdown files ({scope_desc})",
        f"  conventions at root: {', '.join(present) if present else '(none)'}",
    ]
    log = wiki_root / "log.md"
    if log.is_file():
        try:
            log_text = log.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            log_text = ""
        log_lines = [ln for ln in log_text.splitlines() if ln.strip()]
        if log_lines:
            last = log_lines[-1].strip()
            if len(last) > 100:
                last = last[:97] + "..."
            lines.append(f"  last log:  {last}")
    return lines


def _owned_space_paths(
    nodes: list["_model.NodeFacts"], include_external: bool
) -> list[Path]:
    """Owned spaces (folders with `index.md`, including bare ones) in
    filesystem discovery order, for the audit's space enumeration.

    Filters already-discovered `nodes` via `_model.is_space_node` so the
    audit reuses its single `_model.discover_nodes` walk instead of a
    separate one."""
    return [n.path for n in nodes if _model.is_space_node(n, include_external)]


# Structural thresholds for the self-maintenance signals below. These are
# INFORMATIONAL: they never flip the audit exit code (the release gate stays
# stable). The framework surfaces the signal; the LLM decides whether to act.
PROMOTE_MIN_HUB_PAGES = 6      # direct content pages that make a space hub-like
PROMOTE_MIN_SPLIT_H2 = 2       # H2 boundaries that make an over-full page splittable
SIGNAL_APPROACHING_RATIO = 0.8  # >= 80% of cap, matching the approaching-cap line


def _count_h2_sections(text: str) -> int:
    """Count `## ` headings in a page body (frontmatter stripped). A page with
    several distinct H2 sections has clean boundaries to split on — the
    structural half of the split-ready promote signal."""
    body = _md.strip_frontmatter(text)
    return sum(1 for line in body.splitlines() if line.startswith("## "))




class CandidateKind(Enum):
    """Why a page/space surfaced as a self-maintenance audit signal."""
    HUB = "hub"
    SPLIT_READY = "split_ready"
    HOT_DISTILL = "hot_distill"


@dataclass(frozen=True)
class AuditCandidate:
    """An informational promote/prune audit signal. `kind` selects which
    metrics are populated — HUB: `pages`; SPLIT_READY: `chars`+`cap`+`h2_count`;
    HOT_DISTILL: `chars`+`cap`. `to_json` renders the legacy per-kind dict shape
    at the `audit --json` boundary (keys are canonicalized downstream, so only
    the key/value set matters)."""
    path: str
    kind: CandidateKind
    reason: str
    pages: int | None = None
    chars: int | None = None
    cap: int | None = None
    h2_count: int | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"path": self.path, "kind": self.kind.value}
        if self.pages is not None:
            out["pages"] = self.pages
        if self.chars is not None:
            out["chars"] = self.chars
        if self.cap is not None:
            out["cap"] = self.cap
        if self.h2_count is not None:
            out["h2_count"] = self.h2_count
        out["reason"] = self.reason
        return out


def _audit_promote_candidates(
    md_files: list[Path],
    all_spaces: list[Path],
    wiki_root: Path,
    table_cache: dict[Path, "_model.LimitTable"],
) -> list[AuditCandidate]:
    """Structural promote-candidate signal (informational) — pages/spaces that
    have accreted enough to warrant becoming (or spawning) a space. Per the
    spec's structural triggers ("accreted siblings, hub-like content, distinct
    sub-topics — not just size overflow"):

    - ``hub``: a space whose directory holds >= PROMOTE_MIN_HUB_PAGES direct
      content pages (accreted siblings under a hub-like ``index.md``).
    - ``split_ready``: a content page at >= 80% of its cap AND carrying
      >= PROMOTE_MIN_SPLIT_H2 H2 sections — size pressure plus clean split
      boundaries. Size-gated on purpose: a large-but-under-cap reference doc
      (its cap bumped in ``_meta/limits.md``) is NOT flagged, only a page
      actually approaching its limit with somewhere to split.

    Never contributes to the audit's error tally."""
    space_set = set(all_spaces)
    candidates: list[AuditCandidate] = []

    # hub — accreted siblings. Count direct (non-index) content pages per dir.
    # Symlinked `.md` (e.g. CLAUDE.md -> AGENTS.md aliases, mount artifacts) are
    # NOT distinct accreted content — skip them so they don't inflate the count.
    direct_pages: dict[Path, int] = {}
    for f in md_files:
        if f.name == "index.md" or f.is_symlink():
            continue
        direct_pages[f.parent] = direct_pages.get(f.parent, 0) + 1
    for space in sorted(space_set):
        n = direct_pages.get(space, 0)
        if n >= PROMOTE_MIN_HUB_PAGES:
            candidates.append(AuditCandidate(
                path=space.relative_to(wiki_root).as_posix() or ".",
                kind=CandidateKind.HUB,
                pages=n,
                reason=(
                    f"{n} direct content pages — consider grouping into sub-spaces"
                ),
            ))

    # split_ready — size pressure + clean H2 boundaries. Skip symlink aliases
    # (the real target is evaluated on its own).
    for f in sorted(md_files):
        if f.name == "index.md" or f.is_symlink():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Per-space caps: a split_ready signal in a nested space is judged
        # against THAT space's `_meta/limits.md`, not the audit root's.
        verdict = scoped_size_verdict(f, text, wiki_root, table_cache)
        threshold = int(verdict.cap.cap * SIGNAL_APPROACHING_RATIO)
        if verdict.chars_projected < threshold:
            continue
        h2 = _count_h2_sections(text)
        if h2 >= PROMOTE_MIN_SPLIT_H2:
            candidates.append(AuditCandidate(
                path=f.relative_to(wiki_root).as_posix(),
                kind=CandidateKind.SPLIT_READY,
                chars=verdict.chars_projected,
                cap=verdict.cap.cap,
                h2_count=h2,
                reason=(
                    f"{verdict.chars_projected}/{verdict.cap.cap} chars with "
                    f"{h2} H2 sections — `space promote` then split by hand"
                ),
            ))
    return candidates


def _audit_prune_candidates(
    md_files: list[Path],
    wiki_root: Path,
    table_cache: dict[Path, "_model.LimitTable"],
) -> list[AuditCandidate]:
    """Prune signal (informational) — opt-in ``hot.md`` scratch buffers at
    >= 80% of their cap. Distinct remediation from split: a hot buffer is meant
    to be DRAINED (distilled into cold structured pages), not subdivided.

    The clock-dependent ``updated:``-staleness prune is intentionally NOT here —
    it varies with wall-clock time and so can't be snapshotted deterministically;
    the manifest-gated >30d staleness check in ws-tend covers that case against a
    recorded ``last_synced`` instead. Never contributes to the error tally."""
    out: list[AuditCandidate] = []
    for f in sorted(md_files):
        if f.name != "hot.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Per-space caps: an opt-in `hot.md` is judged against its own space's
        # `_meta/limits.md` (nested hot buffers keep their own discipline).
        verdict = scoped_size_verdict(f, text, wiki_root, table_cache)
        if verdict.chars_projected >= int(verdict.cap.cap * SIGNAL_APPROACHING_RATIO):
            out.append(AuditCandidate(
                path=f.relative_to(wiki_root).as_posix(),
                kind=CandidateKind.HOT_DISTILL,
                chars=verdict.chars_projected,
                cap=verdict.cap.cap,
                reason="hot buffer >= 80% full — distill into cold pages",
            ))
    return out


def cmd_audit(args: argparse.Namespace) -> int:
    # Read-only by default → strict resolver (refuses missing `## Spaces`).
    # With `--fix` we're a repair surface → repair resolver + an explicit
    # ensure-section pass on the root before we enumerate drift.
    fix = getattr(args, "fix", False)
    remove_stale = getattr(args, "remove_stale", False)
    json_mode = getattr(args, "json", False)
    # JSON mode buffers stdout so the inline drift/broken/etc. lines don't
    # appear in the structured output. Errors still go to stderr.
    if remove_stale and not fix:
        print(
            "  ! --remove-stale requires --fix",
            file=sys.stderr,
        )
        return 2
    wiki_root, _err = resolve_wiki(args.wiki, repair=fix)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2

    include_external = getattr(args, "include_external", False)

    # JSON mode buffers the human report into a discarded StringIO; only the
    # structured object is emitted, to the real stdout captured here. The
    # try/finally guarantees stdout is always restored, so an early return or
    # exception can never leak the redirect and swallow later output.
    _real_stdout = sys.stdout
    if json_mode:
        import io
        sys.stdout = io.StringIO()
    try:
        if fix:
            # Pass 1: insert `## Spaces` into every owned space that's missing it.
            # The model space enumeration surfaces bare-`index.md` folders too;
            # `_ensure_section_at` makes them spec-compliant. Discovered fresh
            # here because the loop mutates index.md as it goes.
            fix_nodes = _model.discover_nodes(
                wiki_root, include_external=include_external
            )
            # --include-external is a READ flag: external nodes are still reported
            # (below) but the repair MUTATION must never cross the trust boundary
            # (AGENTS.md owned/external; refuse-and-report, no auto-write).
            owned_fix_spaces = [
                n.path for n in fix_nodes
                if _model.is_space_node(n, include_external)
                and n.trust.scope == _model.TrustScope.OWNED
            ]
            for space in owned_fix_spaces:
                try:
                    text = (space / "index.md").read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if _md.has_section(text, "Spaces"):
                    continue
                try:
                    _ensure_section_at(space, wiki_root)
                except RuntimeError as e:
                    print(f"  ! {e}", file=sys.stderr)
                    continue
                rel = space.relative_to(wiki_root)
                anc_label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
                print(f"  ~ {anc_label}/index.md  +inserted `## Spaces`")

        # One node discovery (post-fix, since pass 1 mutates index.md); the space
        # list, the summary partition, and the JSON drift payload all read from it.
        nodes = _model.discover_nodes(wiki_root, include_external=include_external)
        all_spaces = _owned_space_paths(nodes, include_external)
        # Trust scope per path, read straight from the one discovery walk — the
        # --fix mutation gate and the report stay consistent (no second classifier).
        scope_by_path = {n.path: n.trust.scope for n in nodes}
        # One owned-files walk, reused by the summary count, the content scan, the
        # size checks, and the duplicate-alias pass below (previously four walks).
        md_files = _model.discover_owned_md_files(
            wiki_root, include_external=include_external
        )
        for line in _summary_header(
            wiki_root, all_spaces, nodes, len(md_files),
            include_external=include_external,
        ):
            print(line)
        print()
        # Drift (missing/stale `## Spaces` entries) is computed ONCE, via the
        # model helper, and shared by BOTH the human report below and the JSON
        # payload further down — so the audit's two output surfaces can never
        # disagree about drift (the producer=consumer invariant applied to the
        # command's own outputs). `drift_from_nodes` reproduces the
        # nearest-ancestor / lenient-href / on-disk-stale semantics the human
        # loop used to compute inline; keying by space lets the loop look up its
        # own missing/stale instead of recomputing them.
        drift_list = _model.drift_from_nodes(
            nodes, include_external=include_external
        )
        drift_by_space: dict[Path, _model.SpaceDrift] = {
            sd.space: sd for sd in drift_list
        }

        issues = 0
        # Track owned spaces whose `index.md` lacks `## Spaces`. Surface them in
        # the JSON payload too so structured consumers see an actionable finding
        # for a non-zero exit (otherwise `audit --json` returns exit_code=1 with
        # every other category empty — the consumer has no idea what failed).
        missing_section_spaces: list[Path] = []
        for space in all_spaces:
            try:
                text = (space / "index.md").read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not _md.has_section(text, "Spaces"):
                # An owned space whose `index.md` lacks `## Spaces` violates the
                # v1 navigation contract ("No `## Spaces` means no wiki"). Flag it
                # as an issue so read-only audit doesn't silently pass. Without
                # `--fix` the malformed section IS the report; with `--fix` the
                # bare-section repair pass above already inserted the heading
                # before we recomputed `all_spaces`, so this branch should be
                # unreachable when `fix=True` — but we still flag defensively in
                # case `_ensure_section_at` returned an error and was skipped.
                rel = space.relative_to(wiki_root)
                label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
                print(f"{label}/index.md:")
                print("  ! no `## Spaces` section (run `audit --fix` to insert)")
                missing_section_spaces.append(space)
                issues += 1
                continue
            # Missing/stale come from the shared `drift_from_nodes` result above —
            # one drift computation, not a second inline pass. A space with no
            # drift simply has no entry in the map.
            sd = drift_by_space.get(space)
            missing = sd.missing if sd is not None else []
            stale = sd.stale if sd is not None else []
            if missing or stale:
                rel = space.relative_to(wiki_root)
                label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
                print(f"{label}/index.md:")
                for entry in missing:
                    print(f"  + missing entry for {entry}/")
                for entry in stale:
                    print(f"  - stale entry {entry}/ (no index.md on disk)")
                issues += len(missing) + len(stale)

                # `--fix` repair pass for THIS space: register every missing
                # entry and (optionally) remove stale ones. The fix is mechanical;
                # never creates a directory (a stale entry is removed from the
                # list, not promoted to a real space).
                #
                # Only repair (mutate index.md of) OWNED spaces. With
                # --include-external an external space's drift is REPORTED above
                # (the missing/stale lines already printed) but never auto-fixed —
                # external writes need explicit instruction (AGENTS.md
                # owned/external). The pre-existing per-child external stale guard
                # below is now subsumed for the space-as-external case but kept for
                # the owned-space-with-external-child-entry case.
                if fix and scope_by_path.get(space) == _model.TrustScope.OWNED:
                    ancestor_index = space / "index.md"
                    # If the ancestor's `## Spaces` contains ANY unparseable
                    # bullet (broken paren, missing link, half wikilink — see
                    # `_AUDIT_BULLET_SHAPE_RE`), refuse to register missing
                    # entries in this index. Otherwise `audit --fix` would add
                    # a SECOND, valid bullet next to the broken one — compounding
                    # the malformation instead of letting it surface for repair.
                    # Malformed entries signal author intent the
                    # framework can't reconstruct; the user repairs first.
                    if _has_unparseable_bullet(text):
                        rel = space.relative_to(wiki_root)
                        label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
                        print(
                            f"  ! refusing missing-entry registration in "
                            f"{label}/index.md: unparseable bullet present in "
                            "`## Spaces`. Repair the malformed line first; the "
                            "next `audit --fix` will register normally.",
                            file=sys.stderr,
                        )
                        continue
                    for child_rel in missing:
                        label_str = f"{child_rel}/"
                        href = f"{child_rel}/index.md"

                        # Skip if the child's own `index.md` still lacks
                        # `## Spaces` after pass 1 — pass 1 must have failed
                        # to repair it (e.g., over-cap insertion was rejected).
                        # Registering the entry here would create the producer/
                        # consumer break the v1 contract is built to prevent:
                        # parent's `## Spaces` would advertise the child while
                        # the contract walker (which checks `## Spaces` on
                        # entry) skips it. The bare-child report above already
                        # surfaced the underlying issue; don't compound it.
                        child_index = space / child_rel / "index.md"
                        try:
                            child_text = child_index.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            print(
                                f"  ! could not register [{label_str}] in "
                                f"{label}/index.md: child index unreadable",
                                file=sys.stderr,
                            )
                            continue
                        if not _md.has_section(child_text, "Spaces"):
                            print(
                                f"  ! refusing to register [{label_str}] in "
                                f"{label}/index.md: child still lacks `## Spaces` "
                                "(pass 1 repair failed — fix that first).",
                                file=sys.stderr,
                            )
                            continue

                        # Route through a locked mutate that runs `enforce_size_cap`
                        # on the projected text — `audit --fix` is a framework
                        # writer and must respect per-file caps. `_atomic_register_
                        # in_spaces` alone doesn't enforce caps; using the mutate
                        # form lets us reject (None, 2, reason) on overflow per the
                        # `_atomic_mutate_index` abort protocol.
                        def _register_mut(
                            fresh_text: str,
                            *,
                            _l: str = label_str,
                            _h: str = href,
                        ) -> _core.MutateResult:
                            new = _add_space_entry(fresh_text, _l, _h, None)
                            if new == fresh_text:
                                return (fresh_text, "noop")
                            try:
                                enforce_size_cap(ancestor_index, new, wiki_root)
                            except SizeCapExceeded as e:
                                return (None, 2, f"size cap: {e}")
                            return (new, "added")

                        rc, info = _core._atomic_mutate_index(
                            space, ancestor_index, _register_mut
                        )
                        if rc == 0 and info == "added":
                            print(f"  ~ {label}/index.md ## Spaces  += [{label_str}]")
                            issues -= 1
                        elif rc != 0:
                            print(
                                f"  ! could not register [{label_str}] in "
                                f"{label}/index.md: {info}",
                                file=sys.stderr,
                            )
                    if remove_stale:
                        for child_rel in stale:
                            target_dir = space / child_rel
                            ext, _why = _is_in_external_scope(target_dir, wiki_root)
                            if ext and not include_external:
                                print(
                                    f"  ! refusing to remove stale external entry "
                                    f"{child_rel}/ in {label}/index.md; pass "
                                    "--include-external --remove-stale together.",
                                    file=sys.stderr,
                                )
                                continue
                            href = f"{child_rel}/index.md"
                            rc, info = _atomic_remove_from_spaces(
                                space, ancestor_index, href
                            )
                            if rc == 0 and info == "removed":
                                print(f"  ~ {label}/index.md ## Spaces  -= [{child_rel}/]")
                                issues -= 1

        # `issues` accumulated drift entries (missing/stale) AND one count per
        # owned space whose `index.md` lacked `## Spaces`. Split them so the
        # summary doesn't mis-label the bare-section count as "drift".
        drift_issues = issues - len(missing_section_spaces)
        broken, orphans, malformed_frontmatter, duplicate_aliases = _audit_content(
            wiki_root, include_external=include_external, md_files=md_files,
        )

        if broken:
            print()
            by_page: dict[Path, list[str]] = {}
            for page, link, _res in broken:
                by_page.setdefault(page, []).append(link)
            for page in sorted(by_page):
                print(f"<wiki>/{page.relative_to(wiki_root)}:")
                for link in sorted(by_page[page]):
                    print(f"  ! broken wikilink [[{link}]]")

        # Size violations — pages over their per-pattern cap. Reported alongside
        # drift and broken links; flips the exit code like the other hard errors.
        # Approaching-cap warnings (>= 80% but under cap) print here too but do
        # NOT flip the exit code.
        # Read caps through the same unified model the write enforcer and
        # `space caps` use, so the size verdicts and the malformed-limits finding
        # below share one parse of `_meta/limits.md` (producer=consumer) and the
        # over/approaching lines carry the matching rule's provenance.
        table = _model.load_limit_table(wiki_root)
        # Per-file cap tables, memoized by the scope root they were loaded from, so
        # a root audit honors each nested space's OWN `_meta/limits.md` instead of
        # applying the audit root's caps to every file it crosses (CONVENTIONS /
        # per-space autonomy). The root table seeds the cache; the malformed-limits
        # report below loads each owned space's scope from it (or directly) so a
        # malformed NESTED limits.md is flagged by the root audit too.
        table_cache: dict[Path, "_model.LimitTable"] = {wiki_root: table}
        # Reuse the single owned-files walk computed above. Feeding the on-disk
        # text in as the projected text makes `chars_projected` equal the on-disk
        # body length and the `OK_SHRINKING` branch unreachable here — behaviour
        # identical to the legacy `current_size` + `cap_for` derivation, now with
        # the `CapSource` attached to each verdict.
        over_cap: list[tuple[Path, "_model.SizeVerdict"]] = []
        approaching: list[tuple[Path, "_model.SizeVerdict"]] = []
        for f in md_files:
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # An unreadable file counted as 0 chars under the legacy
                # `current_size` path — never over or approaching. Skip it.
                continue
            verdict = scoped_size_verdict(f, text, wiki_root, table_cache)
            if verdict.outcome == _model.SizeOutcome.OVER:
                over_cap.append((f, verdict))
            elif verdict.chars_projected >= int(verdict.cap.cap * 0.8):
                approaching.append((f, verdict))

        # Self-maintenance signals — structural promote candidates ("files grow
        # into spaces") and hot-buffer prune candidates. Both INFORMATIONAL: they
        # are reported below and in the JSON payload but never enter the `errors`
        # tally, so the audit exit code (the release gate) is unaffected.
        promote_candidates = _audit_promote_candidates(
            md_files, all_spaces, wiki_root, table_cache
        )
        prune_candidates = _audit_prune_candidates(md_files, wiki_root, table_cache)

        if over_cap:
            print()
            for f, verdict in sorted(over_cap, key=lambda t: t[0]):
                rel = f.relative_to(wiki_root)
                print(
                    f"<wiki>/{rel}: ! size {verdict.chars_projected} > "
                    f"cap {verdict.cap.cap} ({_format_cap_source(verdict.cap.source)})"
                )
        if approaching:
            print()
            print(
                "approaching cap (>= 80% full; informational, not an error):"
            )
            for f, verdict in sorted(approaching, key=lambda t: t[0]):
                rel = f.relative_to(wiki_root)
                chars = verdict.chars_projected
                cap = verdict.cap.cap
                pct = round(chars / cap * 100)
                print(
                    f"  . <wiki>/{rel}: {chars}/{cap} ({pct}%) "
                    f"— {_format_cap_source(verdict.cap.source)}"
                )

        # Malformed `_meta/limits.md` rows across EVERY owned space, not only
        # the root: a root audit crosses owned spaces for size verdicts, so its
        # gate must also flag a malformed nested limits file (producer=consumer).
        # A space with no markdown never entered `table_cache`, so load each
        # declaring scope directly; root reuses `table` (the parse `space caps`
        # shares). Flips the exit code.
        malformed_limits: list[tuple[Path, list[tuple[int, str]]]] = []
        for scope in sorted({wiki_root, *all_spaces}):
            if not (scope / "_meta" / "limits.md").is_file():
                continue
            scope_table = table if scope == wiki_root else table_cache.get(scope)
            if scope_table is None:
                scope_table = _model.load_limit_table(scope)
                table_cache[scope] = scope_table
            if scope_table.malformed_rows:
                malformed_limits.append((scope, scope_table.malformed_rows))
        malformed_limits_count = sum(len(rows) for _, rows in malformed_limits)
        for scope, rows in malformed_limits:
            rel = scope.relative_to(wiki_root)
            label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
            print()
            print(f"{label}/_meta/limits.md:")
            for line, raw in rows:
                print(f"  ! malformed limits row (line {line + 1}): {raw}")

        # Malformed `## Spaces` entries — author errors the framework cannot
        # auto-repair. Reported alongside drift; flips the exit code.
        malformed = _audit_malformed_entries(wiki_root, all_spaces)
        if malformed:
            print()
            by_space: dict[Path, list[str]] = {}
            for sp, issue in malformed:
                by_space.setdefault(sp, []).append(issue)
            for sp in sorted(by_space):
                rel = sp.relative_to(wiki_root)
                label = "<wiki>" if str(rel) == "." else f"<wiki>/{rel}"
                print(f"{label}/index.md:")
                for issue in by_space[sp]:
                    print(f"  ! malformed `## Spaces` entry — {issue}")

        # Duplicate aliases — when two pages declare the same alias, wikilink
        # resolution is nondeterministic (last walker visit wins). Always-on
        # audit so the producer can disambiguate before consumers see drift.
        # `duplicate_aliases` comes from `_audit_content`'s `page_index` — the SAME
        # index the broken-wikilink resolver uses — so the finding and the resolver
        # can never disagree (one alias parse, not a second `_find_alias_owners`).
        if duplicate_aliases:
            print()
            for alias, pages in duplicate_aliases:
                page_list = ", ".join(
                    str(p.relative_to(wiki_root)) for p in pages
                )
                print(f"  ! duplicate alias [{alias}] declared by: {page_list}")

        # Malformed YAML frontmatter — pages whose frontmatter raised a
        # YAMLError or parsed to something that isn't a mapping. Previously
        # these silently parsed as ABSENT (the `_md` wrapper returned
        # `None` either way), so the audit couldn't tell "no frontmatter"
        # from "broken frontmatter." `FrontmatterStatus` distinguishes,
        # and the audit surfaces malformed pages as an error so the producer
        # repairs before downstream consumers (search, link resolution) see
        # the dropped aliases.
        if malformed_frontmatter:
            print()
            for page, result in malformed_frontmatter:
                rel = page.relative_to(wiki_root)
                line_hint = (
                    f" (line {result.error_line + 2})"
                    if result.error_line is not None else ""
                )
                status = result.status.value
                print(f"<wiki>/{rel}: ! malformed frontmatter ({status}){line_hint}")

        if orphans:
            print(
                f"\norphans: {len(orphans)} page(s) with no incoming wikilinks "
                "(informational — a page may be standalone on purpose):"
            )
            for page in orphans:
                print(f"  . <wiki>/{page.relative_to(wiki_root)}")

        # Self-maintenance signals — informational, structural; the LLM decides
        # whether to act (ws-tend surfaces these; ws-update consults the promote
        # ones at its size-discipline step).
        if promote_candidates:
            print(
                "\npromote candidates (informational — structural; the LLM decides "
                "whether to act):"
            )
            for c in promote_candidates:
                print(f"  . <wiki>/{c.path}: {c.kind.value} — {c.reason}")
        if prune_candidates:
            print("\nprune candidates (informational):")
            for c in prune_candidates:
                print(f"  . <wiki>/{c.path}: {c.kind.value} — {c.reason}")

        # Orphans and approaching-cap are facts, not errors — they never flip the
        # exit code. Drift, broken wikilinks, over-cap size violations, malformed
        # entries, malformed limits rows, and duplicate aliases all do.
        errors = (
            drift_issues
            + len(missing_section_spaces)
            + len(broken)
            + len(over_cap)
            + len(malformed)
            + malformed_limits_count
            + len(duplicate_aliases)
            + len(malformed_frontmatter)
        )
        if json_mode:
            # Discard the captured human report; emit only the JSON
            # object, to the real stdout (finally restores sys.stdout).
            exit_code = 0 if errors == 0 else 1
            # Without --fix, reuse the single `drift_list` computed above (the
            # human report and this payload then share one result and can't
            # disagree). With --fix, the repair loop mutated index.md mid-pass, so
            # the pre-fix `drift_list` would over-report — listing entries the fix
            # just registered — and contradict `exit_code`, which already counts
            # only the drift that REMAINS. Recompute from fresh nodes so the
            # payload reflects post-fix reality (a registration the fix refused —
            # over-cap, unparseable bullet — correctly stays listed).
            payload_drift = (
                _model.drift_from_nodes(
                    _model.discover_nodes(
                        wiki_root, include_external=include_external
                    ),
                    include_external=include_external,
                )
                if fix
                else drift_list
            )
            drift_payload = [
                {
                    "ancestor": sd.space.relative_to(wiki_root).as_posix(),
                    "missing": sd.missing,
                    "stale": sd.stale,
                }
                for sd in payload_drift
            ]
            out = {
                "wiki": str(wiki_root),
                "summary": {
                    "spaces": len(all_spaces),
                    "include_external": include_external,
                },
                "drift": drift_payload,
                "broken_wikilinks": _aggregate_broken_wikilinks(broken, wiki_root),
                "size_violations": [
                    {
                        "path": str(p.relative_to(wiki_root).as_posix()),
                        "chars": verdict.chars_projected,
                        "cap": verdict.cap.cap,
                        "cap_source": {
                            "kind": verdict.cap.source.kind.value,
                            "pattern": verdict.cap.source.pattern,
                            "file": _rel_or_str(verdict.cap.source.file, wiki_root),
                            "line": verdict.cap.source.line + 1
                            if verdict.cap.source.line is not None else None,
                        },
                    }
                    for p, verdict in over_cap
                ],
                "approaching_cap": [
                    {
                        "path": str(p.relative_to(wiki_root).as_posix()),
                        "chars": verdict.chars_projected,
                        "cap": verdict.cap.cap,
                        "cap_source": {
                            "kind": verdict.cap.source.kind.value,
                            "pattern": verdict.cap.source.pattern,
                            "file": _rel_or_str(verdict.cap.source.file, wiki_root),
                            "line": verdict.cap.source.line + 1
                            if verdict.cap.source.line is not None else None,
                        },
                    }
                    for p, verdict in approaching
                ],
                "orphans": [
                    str(p.relative_to(wiki_root).as_posix()) for p in orphans
                ],
                # Informational self-maintenance signals — see `_audit_promote_
                # candidates` / `_audit_prune_candidates`. Each entry already carries
                # wiki-root-relative paths. Never affect `exit_code`.
                "promote_candidates": [c.to_json() for c in promote_candidates],
                "prune_candidates": [c.to_json() for c in prune_candidates],
                "malformed_entries": [
                    {
                        "space": str(sp.relative_to(wiki_root).as_posix()) or ".",
                        "issue": issue,
                    }
                    for sp, issue in malformed
                ],
                "duplicate_aliases": [
                    {
                        "alias": alias,
                        "pages": [
                            str(p.relative_to(wiki_root).as_posix()) for p in pages
                        ],
                    }
                    for alias, pages in duplicate_aliases
                ],
                # Owned spaces whose `index.md` lacks `## Spaces`. The human
                # output reports these inline; the structured output needs the
                # same hook so JSON consumers (skills, CI) can act on the
                # non-zero exit code with a specific actionable item.
                "missing_spaces_section": [
                    str(sp.relative_to(wiki_root).as_posix()) or "."
                    for sp in missing_section_spaces
                ],
                "malformed_frontmatter": [
                    {
                        "page": str(page.relative_to(wiki_root).as_posix()),
                        "status": result.status.value,
                        # File-relative 1-based, matching the human surface
                        # (`error_line + 2`) and the `malformed_limits` 1-based
                        # convention below — `result.error_line` is 0-based within
                        # the frontmatter body, so +1 for the opening `---` fence
                        # and +1 for 0-based→1-based. A consumer mapping this to a
                        # file line now lands on the right one.
                        "error_line": (
                            result.error_line + 2
                            if result.error_line is not None else None
                        ),
                        "error_message": result.error_message,
                    }
                    for page, result in malformed_frontmatter
                ],
                # Each row carries its owning `scope` (wiki-root-relative posix,
                # "." for root) so a consumer locates which space's limits file
                # is broken. Root rows still mirror `space caps --json`'s
                # `malformed_rows` (`line`, `raw`) — the shared root parse.
                "malformed_limits": [
                    {"scope": scope.relative_to(wiki_root).as_posix(),
                     "line": line + 1, "raw": raw}
                    for scope, rows in malformed_limits
                    for line, raw in rows
                ],
                "exit_code": exit_code,
            }
            print(json.dumps(out, indent=2), file=_real_stdout)
            return exit_code
        print()
        if errors == 0:
            info_parts: list[str] = []
            if approaching:
                info_parts.append(f"{len(approaching)} approaching cap")
            if orphans:
                info_parts.append(f"{len(orphans)} orphan(s)")
            if promote_candidates:
                info_parts.append(f"{len(promote_candidates)} promote candidate(s)")
            if prune_candidates:
                info_parts.append(f"{len(prune_candidates)} prune candidate(s)")
            tail = f" ({', '.join(info_parts)} reported above)" if info_parts else ""
            print(f"OK: no drift, no broken wikilinks, no size violations{tail}")
            return 0
        parts: list[str] = []
        if drift_issues:
            parts.append(f"{drift_issues} `## Spaces` drift")
        if missing_section_spaces:
            parts.append(
                f"{len(missing_section_spaces)} space(s) missing `## Spaces`"
            )
        if broken:
            parts.append(f"{len(broken)} broken wikilink(s)")
        if over_cap:
            parts.append(f"{len(over_cap)} size violation(s)")
        if malformed:
            parts.append(f"{len(malformed)} malformed `## Spaces` entry/entries")
        if duplicate_aliases:
            parts.append(f"{len(duplicate_aliases)} duplicate alias(es)")
        if malformed_frontmatter:
            parts.append(f"{len(malformed_frontmatter)} malformed frontmatter")
        if malformed_limits:
            parts.append(f"{malformed_limits_count} malformed limits row(s)")
        print(
            f"{errors} issue(s) found: {' + '.join(parts)}. Re-run after fixing, "
            "or use `wiki-spaces space add/remove` for `## Spaces` entries, "
            "and `space promote` then split sections by hand (or shrink the page) "
            "for size violations."
        )
        return 1
    finally:
        sys.stdout = _real_stdout




@dataclass(frozen=True)
class AdoptResult:
    """Outcome of `adopt_tree`.

    `registered` is `(label, ancestor-label)` pairs the caller prints in its
    own summary block (so adoption lines group with the rest of the written
    report). `failed` is True when any nested space could not be repaired or
    registered (the caller exits non-zero). `root_failed` is True when the
    fatal root-`## Spaces` insertion failed — the caller aborts before
    writing any config.
    """
    registered: list[tuple[str, str]]
    failed: bool
    root_failed: bool


def adopt_tree(root: Path, *, include_external: bool) -> AdoptResult:
    """Adopt an existing folder of notes as a wiki: insert `## Spaces` into the
    root and every nested bare `index.md`, then register each nested space
    upward in its ancestor's `## Spaces` so `audit` reports zero drift.

    The public entry point `init --adopt` calls — adoption is space-domain
    orchestration over the chain helpers (`_ensure_section_at`,
    `_ensure_spaces_chain_and_register`), so it lives here next to them rather
    than being assembled from `space`'s privates by `init_wiki` (one module,
    one owner of the `## Spaces` contract). Externally-classified subtrees are
    reported on stderr and skipped unless `include_external`.

    Does NOT size-check existing content — that is `space audit`'s job, run as
    the post-init step in `references/SETUP.md`. Per-skip and per-failure
    notices go to stderr; the `registered` pairs are returned for the caller's
    summary. Best-effort batch: one failed adoption sets `failed` but does not
    abort the rest, so the exit code can still signal partial failure.
    """
    registered: list[tuple[str, str]] = []
    failed = False

    # Always repair the root first — even a zero-nested-spaces wiki must carry
    # `## Spaces` after `init --adopt`.
    try:
        _ensure_section_at(root, root)
    except RuntimeError as e:
        print(
            f"  ! could not insert `## Spaces` into {root}/index.md: {e}",
            file=sys.stderr,
        )
        return AdoptResult(registered=[], failed=False, root_failed=True)

    for path, classification, reason in _walk_classified(
        root, include_external=include_external
    ):
        if path == root:
            continue
        if (
            classification == _model.TrustScope.EXTERNAL
            and not include_external
        ):
            rel_path = path.relative_to(root).as_posix()
            print(
                f"  . skipping {rel_path}/ — classified external "
                f"({reason}). Rename to use as owned, or pass "
                f"--include-external to override.",
                file=sys.stderr,
            )
            continue
        # With include_external on, `_walk_classified` may surface external
        # boundary folders that lack `index.md` (foreign submodules, escaping
        # symlinks). Skip those rather than trying to register a non-space.
        if not (path / "index.md").is_file():
            rel_path = path.relative_to(root).as_posix()
            print(f"  . skipping {rel_path}/ — no index.md", file=sys.stderr)
            continue

        # Repair the LEAF's own `index.md` first. The chain helper only walks
        # UP from the leaf, so a bare nested `foo/index.md` with no children
        # stays bare without this step.
        try:
            _ensure_section_at(path, root)
        except RuntimeError as e:
            print(
                f"  ! adopt failed inserting `## Spaces` into "
                f"{path}/index.md: {e}",
                file=sys.stderr,
            )
            failed = True
            continue

        # Register `path` upward via the chain helper. Bare-index ancestors get
        # `## Spaces` inserted as part of the chain walk. The chain helper's
        # notices are deferred — the caller's summary groups adoption activity
        # with the rest of the written-files report.
        try:
            _notices, added = _ensure_spaces_chain_and_register(root, path)
            for ancestor, label, _href in added:
                anc_rel = ancestor.relative_to(root)
                anc_label = (
                    "<wiki>" if str(anc_rel) == "." else f"<wiki>/{anc_rel}"
                )
                registered.append((label, anc_label))
        except EnsureChainError as e:
            print(f"  ! adopt failed for {path}: {e}", file=sys.stderr)
            for _n in _rollback_added_entries(e.added):
                print(_n, file=sys.stderr)
            failed = True

    return AdoptResult(registered=registered, failed=failed, root_failed=False)



_AUDIT_BULLET_RE = re.compile(r"^\s*-\s+\[([^\]]*)\]\(([^)]*)\)")
# Detect any line that LOOKS LIKE a `## Spaces` link/wikilink bullet — even
# if it fails to parse. A line with `- [` (or `* [` / `+ [`) followed by
# anything is the author's signal that they tried to author an entry. If
# none of the well-formed patterns (`ENTRY_RE`, `WIKILINK_ENTRY_RE`,
# `_AUDIT_BULLET_RE`) match, the entry is unparseable — silently invisible
# to traversal AND to the existing malformed pass. Surface it so the user
# can repair the bullet.
_AUDIT_BULLET_SHAPE_RE = re.compile(r"^\s*[-*+]\s+\[")


def _has_unparseable_bullet(text: str) -> bool:
    """True iff text's `## Spaces` section contains any bullet that looks
    like an entry but is not a valid, registrable `- [label](href)`.

    Covers both the unparseable shapes (broken paren, missing link, half
    wikilink `- [[name]`) AND well-formed `- [[name]]` wikilink-form: the
    navigation contract is `- [label](href)`, so a wikilink-form bullet
    carries no registrable href and registers nothing. `audit --fix` checks
    this before registering missing entries — adding a valid bullet next to
    one of these would compound the malformed line (e.g. leave a redundant
    `[[child]]` + `[child/](child/index.md)` pair) rather than surface it for
    repair.
    """
    lines = text.splitlines()
    # Fence-aware bounds: a `## Spaces` shown inside a fenced code block is an
    # example the consumer's `find_section_bounds` skips, so the audit scans the
    # SAME body lines the walker does (producer=consumer) instead of manually
    # toggling on a raw `## Spaces` line.
    bounds = _md.find_section_bounds(lines, "Spaces")
    if bounds is None:
        return False
    _, body_start, body_end = bounds
    for line in lines[body_start:body_end]:
        if not _AUDIT_BULLET_SHAPE_RE.match(line):
            continue
        # The canonical, registrable entry is the ANCHORED `_md.ENTRY_RE`.
        # `_AUDIT_BULLET_RE` matches only a PREFIX, so a line with trailing
        # content after the first `)` (`- [Foo](foo)bar/index.md)`) slips past
        # it; gate on the anchored `ENTRY_RE` so those count as unparseable too
        # (producer=consumer: the consumer walker drops what `ENTRY_RE` can't
        # read, so `audit --fix` must not register a sibling next to it).
        if _md.ENTRY_RE.match(line):
            continue
        # Reaches here for unparseable bullets, trailing-content-after-`)`
        # shapes, and `- [[name]]` wikilink-form (no registrable href) — none
        # is a valid, registrable entry.
        return True
    return False


def _audit_malformed_entries(
    wiki_root: Path,
    spaces: list[Path],
) -> list[tuple[Path, str]]:
    """Find `## Spaces` entries that fail policy.

    Reported as errors (audit flips the exit code on any of):
    - Empty href (`- [foo]()`).
    - Absolute path href (`- [foo](/abs/path)`).
    - Href containing `..` segments.
    - Href containing a Markdown link metacharacter (`_SPACES_HREF_METACHARS`):
      the producer (`_validate_rel_path` / `space add`) refuses these, so the
      consumer must too (producer=consumer).
    - Href that escapes the wiki root after resolution.
    - Duplicate entries pointing at the same directory.

    Independent of `_md.parse_section_entries` because the raw `ENTRY_RE`
    drops some malformed shapes silently. External-classified targets are
    NOT flagged as escape (they legitimately resolve outside; trust scope
    is the opt-in gate, not malformed-href).

    `audit --fix` does NOT auto-repair these — malformed entries signal
    author intent the framework cannot reconstruct.
    """
    issues: list[tuple[Path, str]] = []
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return issues
    for space in spaces:
        try:
            text = (space / "index.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        bounds = _md.find_section_bounds(lines, "Spaces")
        if bounds is None:
            continue
        _, body_start, body_end = bounds
        seen_dirs: set[str] = set()
        for line in lines[body_start:body_end]:
            m = _AUDIT_BULLET_RE.match(line)
            if not m:
                if _md.WIKILINK_ENTRY_RE.match(line):
                    # A well-formed `- [[name]]` wikilink-form bullet. The
                    # navigation contract is `- [label](href)`, so this
                    # carries no registrable href: traversal skips it (it
                    # registers nothing) while it masquerades as a valid
                    # entry. Flag it so the user converts it to link form —
                    # otherwise the dir it names reads as unregistered drift
                    # AND the body wikilink scan double-reports it.
                    issues.append(
                        (space, f"wikilink-form entry (no registrable href): "
                                f"{line.strip()}")
                    )
                elif _AUDIT_BULLET_SHAPE_RE.match(line):
                    # A bullet that LOOKS LIKE a `## Spaces` entry (starts with
                    # `- [`, `* [`, or `+ [`) but matches neither
                    # `_AUDIT_BULLET_RE` nor `WIKILINK_ENTRY_RE` is
                    # unparseable. Common shapes: unbalanced `(href)` (no
                    # closing paren), missing parens entirely (`- [label]`),
                    # half wikilink (`- [[name]` with one closing bracket).
                    # Surface it so the user repairs the bullet — otherwise
                    # traversal silently skips the entry while audit reports
                    # OK, and `audit --fix` could add a SECOND, valid entry
                    # alongside the broken one.
                    issues.append(
                        (space, f"unparseable bullet: {line.strip()}")
                    )
                continue
            href = m.group(2)
            if not href.strip():
                issues.append((space, f"empty href: {line.strip()}"))
                continue
            if href.startswith("/"):
                issues.append((space, f"absolute href: {href}"))
                continue
            href_path = Path(href)
            if ".." in href_path.parts:
                issues.append((space, f"href contains `..`: {href}"))
                continue
            # Producer/consumer symmetry: `space add` / `_validate_rel_path`
            # refuse any of `_SPACES_HREF_METACHARS` in a path segment, so a
            # hand-authored href carrying one is the same break from the reader
            # side. Check the href as the CONSUMER parses it (`_AUDIT_BULLET_RE`
            # group 2 / `href` — truncated at the first `)`, exactly like
            # `_md.ENTRY_RE`), so audit and `space list` agree on what the href
            # is. A `)` can never sit inside a parsed href (it closes the link),
            # so a `)`-in-href shape is never caught by THIS metachar check (the
            # truncated href carries no metachar). The two `)` shapes split:
            # one that still parses as a canonical entry
            # (`- [foo)-bar/](foo)-bar/index.md)`, where `-bar…` reads as the
            # description) surfaces as drift; one that does NOT parse (trailing
            # content after `)`, `- [Foo](foo)bar/index.md)`) is caught by the
            # canonical-entry check below. Flag (NOT auto-repaired: author
            # intent the framework can't reconstruct).
            bad = [c for c in _SPACES_HREF_METACHARS if c in href]
            if bad:
                issues.append((
                    space,
                    f"href contains Markdown link metacharacter(s) "
                    f"{''.join(sorted(set(bad)))!r}: {line.strip()} "
                    "(producer `space add` refuses these; the entry is "
                    "unreadable by the consumer walker)",
                ))
                continue
            # Reserved-folder hrefs per CONVENTIONS / Reserved top-level
            # folder names. The consumer walker prunes these on read, so
            # an entry like `- [_meta/internal/](_meta/internal/index.md)`
            # is invisible to `space list` / `space files`. Audit must
            # flag it — otherwise a pre-v1 layout passes clean while
            # consumers can't see the registered space (producer/consumer
            # break unrepaired).
            if any(
                _model.is_reserved_segment(part)
                for part in href_path.parts
            ):
                issues.append((
                    space,
                    f"reserved-folder href: {href} (hidden / `_archives` "
                    "/ `_meta` paths are pruned by the consumer walker; "
                    "remove the entry or move the content to a non-reserved "
                    "path)",
                ))
                continue
            try:
                resolved = (space / href).resolve()
            except (OSError, RuntimeError):
                issues.append((space, f"href unresolvable: {href}"))
                continue
            child_path = space / href
            is_ext, _why = _is_in_external_scope(child_path, wiki_root)
            try:
                resolved.relative_to(root_real)
                escapes = False
            except ValueError:
                escapes = True
            if escapes and not is_ext:
                issues.append((space, f"href escapes after resolution: {href}"))
                continue
            # Producer=consumer: the canonical entry reader is the ANCHORED
            # `_md.ENTRY_RE`. `_AUDIT_BULLET_RE` matched only a PREFIX, so a
            # line whose truncated href is clean but which carries trailing
            # content after the first `)` (`- [Foo](foo)bar/index.md)`) or an
            # empty label reaches here yet fails the anchored parse —
            # `parse_section_entries` and the traversal walker silently drop it,
            # so `space list` never surfaces the space. (A `)`-shape that DOES
            # parse, `- [foo)-bar/](foo)-bar/index.md)` where `-bar…` reads as
            # the description, matches `ENTRY_RE` and is left to the drift pass.)
            # Flag the unparseable shapes so audit never reports OK on an entry
            # the consumer ignores — CONVENTIONS / Malformed entries.
            if not _md.ENTRY_RE.match(line) and not _md.WIKILINK_ENTRY_RE.match(line):
                issues.append((
                    space,
                    "unparseable entry (trailing content after `)`, empty "
                    "label, or otherwise not a canonical `- [label](href)` the "
                    f"consumer walker can read): {line.strip()}",
                ))
                continue
            dir_norm = _model.href_to_dir(href)
            # Self-referential entry: the href normalizes to the space's own
            # directory (`.` — e.g. `./index.md`). The consumer walker skips it
            # (a space is never its own child), so a clean audit would pass a
            # no-op contract entry the consumer ignores (producer=consumer).
            if dir_norm in ("", "."):
                issues.append((
                    space,
                    f"self-referential href (points at the space itself; the "
                    f"consumer walker skips it): {line.strip()}",
                ))
                continue
            if dir_norm in seen_dirs:
                issues.append((space, f"duplicate href dir: {dir_norm}"))
            seen_dirs.add(dir_norm)
    return issues



def _aggregate_broken_wikilinks(
    broken: list[tuple[Path, str, "_model.WikilinkResolution"]],
    wiki_root: Path,
) -> list[dict]:
    """Group broken-wikilink occurrences by target so the JSON consumer
    doesn't see 52 near-identical entries for the same broken target.

    The per-occurrence shape made repair-locality hard to scan (the
    producer ended up `Counter`-ing client-side). Each output
    entry now carries the target, the resolver's attempt trace (same
    for every page referencing the target — the resolver is
    deterministic), the reason, and the sorted list of referencing
    pages. Per-page count is preserved as `len(pages)`.
    """
    by_target: dict[str, dict] = {}
    pages_seen: dict[str, set[str]] = {}
    for page, target, res in broken:
        page_rel = str(page.relative_to(wiki_root).as_posix())
        if target in by_target:
            pages_seen[target].add(page_rel)
            continue
        pages_seen[target] = {page_rel}
        by_target[target] = {
            "target": target,
            "pages": [],
            "tried": [
                {
                    "strategy": a.strategy,
                    "candidate": _rel_or_str(a.candidate, wiki_root),
                    "outcome": a.outcome,
                }
                for a in res.attempts
            ],
            "reason": res.reason,
        }
    out = list(by_target.values())
    for entry in out:
        entry["pages"] = sorted(pages_seen[entry["target"]])
    out.sort(key=lambda e: e["target"])
    return out

