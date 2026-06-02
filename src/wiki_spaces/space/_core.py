"""Shared internals for the `wiki-spaces space` subcommands.

Validators, trust/external classification, `## Spaces` entry writers, the
atomic index-mutation primitive, the chain-registration helpers, and
size-cap enforcement. Imported by the per-command modules (`add`, `remove`,
`mount`, `promote`, `audit`, `query`, `log`); holds no command `main`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from .. import _md
from .. import _model
from .._common import durable_replace
from .._common import fcntl
from .._common import has_control_chars


# ---------- Helpers ----------


# The markdown-link metacharacters forbidden anywhere inside a `## Spaces`
# path. A `## Spaces` entry is `- [LABEL](HREF) — DESC`; any of these inside
# HREF lands as raw bytes inside the link syntax (`- [foo[bar/](foo[bar/index.md)`)
# which the consumer parser (`_md.ENTRY_RE` / `_AUDIT_BULLET_RE`) cannot read
# back. Producer (`_validate_rel_path`), the audit scanner
# (`_audit_malformed_entries`), AND the traversal consumer
# (`_model.normalize_spaces_href`) all validate against this ONE set —
# sourced from `_model` so they can never drift (producer=consumer).
_SPACES_HREF_METACHARS = _model.SPACES_HREF_METACHARS


def _validate_entry_text(value: str | None, *, field: str) -> tuple[bool, str | None]:
    """Validate user-supplied text destined for a markdown `## Spaces` entry.

    `## Spaces` entries are markdown links: `- [LABEL](HREF) — DESCRIPTION`.
    A `]` in LABEL closes the label brace; a `)` in HREF closes the href;
    a newline ends the entry. Either would split the produced line and
    leave a `## Spaces` entry the consumer parser (`_md.ENTRY_RE`) can't
    read. Refuse the value upfront so the producer can never emit an
    entry the consumer ignores.
    """
    if value is None or value == "":
        return True, None
    # Newlines / control chars split the entry across markdown lines.
    if has_control_chars(value):
        return False, (
            f"{field} may not contain newline / control characters — the "
            "resulting `## Spaces` entry would be split across lines and "
            "unparseable by the consumer walker"
        )
    # `]` closes a label brace; `)` closes an href; either breaks the
    # markdown link syntax.
    if "]" in value or ")" in value:
        return False, (
            f"{field} may not contain `]` or `)` — the resulting "
            "`## Spaces` entry would be unparseable by the consumer walker"
        )
    return True, None


def _validate_rel_path(rel: str) -> tuple[bool, str | None]:
    """Validate a user-provided relative path. (ok, error_message).

    Rejects:
    - empty, `.`, `..`, and `.git` segments
    - any hidden-directory segment (`.X`): the consumer walker skips
      hidden directories per CONVENTIONS / Reserved top-level folder
      names; the producer must not register spaces the consumer ignores.
    - `_archives` and `_meta` segments: reserved by convention
      (`_archives` is excluded from audit/`ws-tend` walks, `_meta`
      holds config files like `_meta/limits.md`). Registering a space
      under either creates a `## Spaces` entry no consumer reads.
    - Markdown link metacharacters (`[`, `]`, `(`, `)`, `{`, `}`) in
      any segment: these would otherwise land in `## Spaces` entries
      as raw bytes inside the link syntax — `- [foo)bar/](foo)bar/index.md)`
      — which the parser's `ENTRY_RE` cannot read (regex stops at the
      first `)` or `]`).

    `shared/` is NOT refused at validation — it has dedicated handling
    via `--force-external` in `cmd_add` and is the default mount
    destination per CONVENTIONS / Reserved top-level folder names.
    """
    rel = rel.strip().rstrip("/")
    if not rel:
        return False, "empty path"
    p = Path(rel)
    if p.is_absolute():
        return False, "must be relative to the wiki root"
    for part in p.parts:
        if part in ("", ".", ".."):
            return False, "path may not contain '.', '..', or empty segments"
        if part == ".git":
            return False, "path may not contain '.git' segments"
        if part.startswith("."):
            return False, (
                f"path may not contain hidden segments ({part!r}); hidden "
                "directories are skipped by every consumer walker per "
                "CONVENTIONS / Reserved top-level folder names"
            )
        if part in _model.RESERVED_NAMES:
            return False, (
                f"path segment {part!r} is reserved by convention — "
                f"{part}/ is excluded from consumer walks "
                "(see CONVENTIONS / Reserved top-level folder names)"
            )
        if any(c in part for c in _SPACES_HREF_METACHARS):
            return False, (
                "path may not contain Markdown link metacharacters "
                "(`[`, `]`, `(`, `)`, `{`, `}`) — the resulting `## Spaces` "
                "entry would be unparseable by the consumer walker"
            )
        # Line-break chars split the `## Spaces` entry across markdown lines,
        # making it unparseable. Route through `has_control_chars` so the guard
        # matches the consumer `str.splitlines()` exactly (it splits on NEL /
        # `\u2028` / `\u2029`, which an `ord(c) < 0x20` check missed) — one
        # source of truth, producer=consumer.
        if has_control_chars(part):
            return False, (
                "path may not contain newline / control characters — the "
                "resulting `## Spaces` entry would be split across lines "
                "and unparseable by the consumer walker"
            )
    return True, None


def _derive_default_path(source: str) -> tuple[str | None, str | None]:
    """Derive the default mount path `shared/<basename>/` from `source`.

    Returns `(rel_path, None)` on success, `(None, error_message)` otherwise.

    Ordering matters (regression-tested):
    1. Drop `?query` and `#fragment` first — URLs like `repo.git?ref=main`
       would otherwise mis-strip `.git` against the suffix `.git?ref=main`.
    2. Trim trailing `/`.
    3. Extract tail (last `/`-separated segment, or for scp-style
       `git@host:org/repo` the slashed tail after `:`).
    4. Strip a `.git` SUFFIX only — applies when tail ends with `.git`
       (`repo.git` → `repo`), not when tail *is* `.git` (which is rejected
       next as starting with `.`).
    5. Reject if empty OR starts with `.`.

    The derived path is always `shared/<basename>/` — the `shared/` prefix
    is what opts the mount into external trust-scope semantics per
    CONVENTIONS.md / Owned vs external.
    """
    if not source or not source.strip():
        return None, "source is empty"
    s = source.strip()
    # 1. Drop query/fragment.
    for marker in ("?", "#"):
        idx = s.find(marker)
        if idx >= 0:
            s = s[:idx]
    # 2. Trim trailing slashes.
    s = s.rstrip("/")
    if not s:
        return None, (
            f"cannot derive default path from {source!r}: empty after trimming. "
            "Pass an explicit `path` argument."
        )
    # 3. Extract tail. Handle scp-style first (`user@host:path`) so the
    # path-after-colon is what we tail-split.
    if ":" in s and not s.startswith(("http://", "https://", "ssh://", "git://", "file://")):
        # scp-style or local path with a colon in the prefix; take the part
        # after the LAST colon (then split by `/`).
        s = s.rsplit(":", 1)[-1]
    tail = s.rsplit("/", 1)[-1]
    # 4. Strip `.git` SUFFIX (not when tail IS `.git`).
    if tail.endswith(".git") and tail != ".git":
        tail = tail[: -len(".git")]
    # 5. Reject empty / hidden.
    if not tail:
        return None, (
            f"cannot derive default path from {source!r}: empty basename. "
            "Pass an explicit `path` argument."
        )
    if tail.startswith("."):
        return None, (
            f"cannot derive default path from {source!r}: basename {tail!r} "
            "starts with '.'. Pass an explicit `path` argument."
        )
    return f"shared/{tail}", None


def _is_in_external_scope(path: Path, wiki_root: Path) -> tuple[bool, str | None]:
    """True when `path` or any ancestor (up to but not including wiki_root) is external.

    Delegates to `_model.classify_external_scope` — the one ancestor-walking
    trust classifier — so the producer's write guards (this) and the read-side
    audit/list share a single producer=consumer path. Returns `(True, reason)`
    when external, `(False, None)` otherwise; `reason` names the external
    boundary (the outermost mount point) for the user-facing error message.
    """
    c = _model.classify_external_scope(path, wiki_root)
    if c.scope is not _model.TrustScope.EXTERNAL:
        return False, None
    if c.boundary is not None:
        try:
            rel = c.boundary.relative_to(wiki_root).as_posix()
        except ValueError:
            rel = None
        if rel is not None:
            return True, (
                f"{rel} is external (per CONVENTIONS / Owned vs external "
                "— under shared/, a foreign-origin submodule, or a symlink "
                "that escapes the wiki tree)"
            )
    return True, "path is outside the wiki tree"


def _first_foreign_submodule_ancestor(path: Path, wiki_root: Path) -> Path | None:
    """First ancestor of `path`'s REALPATH (inclusive) up to but excluding
    `wiki_root` that is a foreign-origin git submodule, else None.

    The mount destination guard refuses materializing INTO a foreign
    submodule's working tree even when the destination is lexically under
    `shared/`: `shared/` sanctions external *trust scope* (parking external
    content under your own folder), not writing into a third party's
    checked-out repo. The blanket `under shared/` exemption in the trust-scope
    arm skips `_is_in_external_scope`, and a submodule working-tree dir that
    lacks `index.md` is walked straight past by `_nearest_ancestor_space`, so
    the chain-external pre-flight never sees it either — this closes that gap.

    Walks the REALPATH so an owned-looking symlink that POINTS into a foreign
    submodule (or a descendant of one) is caught too — not just a lexical
    foreign-submodule ancestor. Escapes are left to the containment arm.
    """
    try:
        p = path.resolve()
        root = wiki_root.resolve()
    except (OSError, RuntimeError):
        return None
    while p != root:
        try:
            p.relative_to(root)
        except ValueError:
            return None  # realpath escaped the tree (the containment arm owns escapes)
        if _model.is_foreign_submodule(p, wiki_root):
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def _nearest_ancestor_space(wiki_root: Path, target: Path) -> Path:
    """Nearest ancestor of `target` with `index.md` on disk.

    Binds a disk index check onto the shared `_model.nearest_ancestor_space`
    walker (producer=consumer with the cache-backed discovery path). Every
    caller passes a strict descendant and `wiki_root` always has `index.md`, so
    the walk always resolves to a real ancestor — `wiki_root` is the floor.
    """
    ancestor = _model.nearest_ancestor_space(
        wiki_root, target, lambda p: (p / "index.md").is_file()
    )
    return ancestor if ancestor is not None else wiki_root


def _probe_existing_ancestor(wiki_root: Path, dest: Path) -> Path:
    """Deepest physically-present ancestor of `dest` (the leaf may not exist).

    Probes with `os.path.lexists` so a broken/cyclic symlink component STOPS
    the walk (it exists as a link) rather than being skipped — the guard must
    inspect it, not walk past it. Always terminates at `wiki_root`.
    """
    probe = dest
    while not os.path.lexists(probe) and probe != wiki_root:
        probe = probe.parent
    return probe


def _dest_physically_mountable(
    wiki_root: Path, dest: Path, probe: Path
) -> str | None:
    """Refusal reason if `dest`'s deepest present ancestor `probe` cannot host
    a new child, else None.

    Two physical-viability checks (independent of trust scope) shared by
    `cmd_mount` and `cmd_add` so both refuse-and-report instead of crashing
    with an uncaught `OSError` at `mkdir`, and `--dry-run` predicts the
    refusal (producer=consumer: one check, two callers):

      1. CONTAINMENT — `probe` must resolve strictly within the tree. A
         `strict=True` resolve turns an escaping / cyclic / broken symlink
         component into a clean refusal instead of an ELOOP `OSError` or a
         `FileExistsError` at mkdir time.
      2. TYPE — `probe` must be a directory. A regular file (or a symlink to
         one) where a parent directory is expected would otherwise blow up
         `mkdir(parents=True)` with `FileExistsError` / `NotADirectoryError`.
    """
    dest_rel = dest.relative_to(wiki_root).as_posix()
    try:
        probe.resolve(strict=True).relative_to(wiki_root.resolve())
    except (OSError, ValueError, RuntimeError):
        return (
            f"{dest_rel}/ escapes the wiki tree or passes through an "
            "unresolvable symlink"
        )
    if not probe.is_dir():
        probe_rel = probe.relative_to(wiki_root).as_posix()
        if probe == dest:
            return f"{probe_rel} already exists and is not a directory"
        return (
            f"{probe_rel} is a file, not a directory; {dest_rel}/ cannot be "
            "created under it"
        )
    return None


def _walk_classified(
    wiki_root: Path, *, include_external: bool = False
) -> Iterator[tuple[Path, _model.TrustScope, str | None]]:
    """Yield every space under wiki_root, classified as owned or external.

    Yields `(path, classification, reason)` where:
    - `classification` is a `_model.TrustScope` (`OWNED` or `EXTERNAL`).
    - `reason` is None for owned, or a short string for external.

    Thin projection over `_model.discover_nodes` — the single owned/external
    discovery traversal — so `init --adopt` (the sole consumer) classifies
    spaces with the EXACT same walk `space audit` uses. A second parallel
    walker used to live here; sharing the model walk keeps adopt's notion of
    "what is a space / what is external" from drifting away from audit's
    (the producer=consumer invariant applied to discovery itself).

    Preserves the old walker's yield contract precisely:
    - the wiki root is yielded first as `OWNED`;
    - an owned folder is yielded only when it carries `index.md` (plain owned
      folders are descended *through* by the model walk but not surfaced here,
      so adopt never tries to register a non-space);
    - every external boundary is yielded with a reason so adopt can emit a
      per-skip notice — the boundary's own reason (`_external_reason`) at the
      mount point, `"inside an external subtree"` for a descendant — and its
      subtree is descended only with `include_external`.

    Discovery order, reserved-name pruning (`.`-hidden, `_archives`, `_meta`),
    external classification, and realpath cycle-breaking all come from the
    model walk; this function only re-shapes its nodes into the older tuple
    contract.
    """
    yield (wiki_root, _model.TrustScope.OWNED, None)
    for n in _model.discover_nodes(wiki_root, include_external=include_external):
        if n.path == wiki_root:
            continue
        if n.trust.scope == _model.TrustScope.EXTERNAL:
            # `boundary == path` ⇒ this folder is the mount point itself; a
            # deeper `boundary` ⇒ we're inside an already-external subtree.
            if n.trust.boundary == n.path:
                reason = _external_reason(n.path, wiki_root)
            else:
                reason = "inside an external subtree"
            yield (n.path, _model.TrustScope.EXTERNAL, reason)
        elif n.has_index:
            yield (n.path, _model.TrustScope.OWNED, None)


def _external_reason(path: Path, wiki_root: Path) -> str:
    """Short reason `path` is classified external, for user-facing messages.

    Delegates to `_model.external_reason_for` (one set of reason strings,
    producer=consumer) with a generic fallback for the rare path that reaches
    here without a specific reason — callers here always hold an external path
    and need a non-None string.
    """
    return (
        _model.external_reason_for(path, wiki_root)
        or "external (per owned/external heuristic)"
    )


def _first_external_descendant(
    target: Path, wiki_root: Path
) -> tuple[Path, str] | None:
    """First STRICT descendant of `target` classified external, or None.

    Guards `cmd_remove`'s rmtree/snapshot from crossing the trust boundary
    into a child the top-level `_is_in_external_scope(target, ...)` check
    (which only inspects the target itself) misses: a foreign-origin
    submodule (a real dir rmtree recurses into, or a copytree-followed
    symlinked dir) or an escaping symlink nested below `target`.

    Scans EXACTLY the subtree `shutil.rmtree(target)` /
    `shutil.copytree(target, symlinks=False)` will delete, pruning NOTHING:
    no hidden / `_archives` / `_meta` skip, no `.git` skip. This is a
    producer=consumer match with the delete-walk it protects — that walk
    prunes nothing, so neither can the guard. The discovery walks
    (`_model.discover_*`, `_walk_space_md_files`) DO prune those reserved
    names because they answer a different question ("what is a
    contract-reachable space"); the guard answers "what will rmtree delete",
    and an external mount parked under a hidden / `_archives` / `_meta` dir
    is deleted by rmtree but would be invisible to a pruning guard — exactly
    the data-loss bug this function exists to prevent.

    Tests EVERY descendant node — file, dir, or symlink — via the
    single-path classifier `_model.external_reason_for` so the remove gate,
    audit, list, and discovery agree on what 'external' means. Retains the realpath
    cycle-break (a `seen` set on `.resolve()`); `.git` is descended like any
    other dir (strict completeness over speed — cost is bounded by the cycle
    guard, and rmtree deletes `.git` too). Returns `(path, reason)` for the
    first external node, else None.
    """
    stack: list[Path] = [target]
    seen: set[Path] = set()
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry == target:
                continue
            if _model.external_reason_for(entry, wiki_root) is not None:
                return (entry, _external_reason(entry, wiki_root))
            if not entry.is_dir():
                continue
            # Descend into EVERY dir rmtree/copytree will delete — hidden,
            # `_archives`, `_meta`, `.git` included. This walks more than the
            # discovery walks (one-shot O(files-in-subtree) on an interactive
            # remove), but pruning any of them is what let an external mount
            # nested under such a dir slip past this guard and get deleted.
            try:
                er = entry.resolve()
            except (OSError, RuntimeError):
                continue
            if er in seen:
                continue
            seen.add(er)
            stack.append(entry)
    return None


def _walk_space_md_files(
    space: Path, wiki_root: Path, *, include_external: bool = False,
) -> Iterator[Path]:
    """Yield `.md` file paths inside a single space, plain-folder semantics.

    Thin wrapper over `_model.descend_plain_md_files` (the one shared inner
    descent) scoped to one space rather than the contract-reachable set —
    producer=consumer with `discover_md_files`, not a hand-copied traversal.

    Used by `cmd_promote` to enumerate sibling `.md` files that will become
    consumer-visible after chain repair registers `space` upward — those files
    need their links to the promoted source rewritten in the same operation
    regardless of whether `space` was contract-reachable beforehand.
    """
    space_external, _why = _is_in_external_scope(space, wiki_root)
    if space_external and not include_external:
        return
    for cf in _model.descend_plain_md_files(
        space, space_external, wiki_root, include_external=include_external
    ):
        yield cf.path


def _render_template_index_md(
    template_path: Path,
    name: str,
    description: str | None = None,
    summary: str | None = None,
) -> str:
    """Render a template file as the new space's `index.md` body.

    Explicit birth mechanism: `space add --from-template <path>` opts
    the new space into a parent-supplied template; without the flag,
    `space add` stays structurally barren.

    Substitutions performed on the template text:
    - `{{ title }}`        → `name`
    - `{{ now }}`          → current UTC ISO-8601 timestamp
    - `{{ description }}`  → `description` (empty string when omitted)
    - `{{ summary }}`      → `summary` (empty string when omitted)

    The three placeholders map to three distinct convention concepts:
    - `description`: navigation metadata — what the parent says about
      the child in `## Spaces` and what the body's `## What this space
      is` section repeats. CLI flag: `--description`.
    - `summary`: page-metadata — the frontmatter `summary:` field per
      CONVENTIONS / Frontmatter schema. CLI flag: `--summary`.
    - `title`: derived from the space name; not a separate concept.

    Always guarantees a `## Spaces` heading at the end (the navigation
    contract). If the template lacks one, we append; if it has one,
    we leave it alone.
    """
    from datetime import datetime, timezone
    try:
        text = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"could not read template {template_path}: {e}") from e
    # Refuse rather than silently drop a supplied summary the template can't
    # receive: a template lacking `{{ summary }}` would otherwise discard
    # `--summary` (the value has no destination — the anti-pattern). Checked
    # here so every render path (real and dry-run) refuses identically.
    if summary and summary.strip() and "{{ summary }}" not in text:
        raise RuntimeError(
            f"--summary was given but template {template_path} has no "
            "`{{ summary }}` placeholder to receive it; add `{{ summary }}` to "
            "the template (e.g. its frontmatter `summary:` field) or drop "
            "--summary."
        )
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = text.replace("{{ title }}", name)
    text = text.replace("{{ now }}", now_iso)
    text = text.replace("{{ description }}", (description or "").strip())
    text = text.replace("{{ summary }}", (summary or "").strip())
    if not _md.has_section(text, "Spaces"):
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n## Spaces\n\n"
    return text


def _add_space_entry(
    text: str, label: str, href: str, description: str | None
) -> str:
    """Add a `## Spaces` entry, treating directory-equivalent hrefs as duplicates.

    Idempotent: when an entry already exists pointing at the same directory
    (regardless of `foo`/`foo/`/`foo/index.md` form), returns the text
    unchanged. Without this normalization, `space add foo` against a wiki
    that already lists `- [foo/](foo/)` would append a duplicate.
    """
    target_dir = _model.href_to_dir(href)
    for e in _md.parse_section_entries(text, "Spaces"):
        if e.href and _model.href_to_dir(e.href) == target_dir:
            return text
    return _md.add_entry(text, "Spaces", label, href, description)


def _remove_space_entry(text: str, href: str) -> str:
    """Remove a `## Spaces` entry by normalized directory match.

    Removes whichever href form happens to be in the file (`foo`/`foo/`/
    `foo/index.md`). Removes ALL equivalent duplicates in one pass, so a
    pre-corrupted wiki with multiple entries for the same directory gets
    fully cleaned up in a single `space remove` call.
    """
    target_dir = _model.href_to_dir(href)
    result = text
    while True:
        matched_href = None
        for e in _md.parse_section_entries(result, "Spaces"):
            if e.href and _model.href_to_dir(e.href) == target_dir:
                matched_href = e.href
                break
        if matched_href is None:
            return result
        new = _md.remove_entry(result, "Spaces", matched_href)
        if new == result:
            return result
        result = new


# ---------- Subcommands ----------



def _nearest_limits_scope(file_path: Path, wiki_root: Path) -> Path:
    """Nearest ancestor SPACE (a dir with `index.md`, from `file_path`'s folder
    up to and including `wiki_root`) that declares its own `_meta/limits.md`,
    else `wiki_root`.

    A root `space audit` crosses owned child spaces by default, but each space
    is autonomous: per CONVENTIONS / Per-space convention auto-detection,
    "every convention check happens at that scope's root … the taxonomy [and
    limits] enforced is the one at that scope." Resolving the nearest declaring
    scope means a nested space's own `_meta/limits.md` governs its own files,
    while a root-level pattern (e.g. `projects/**/*.md`) still applies wherever
    a child declares no override. Closest-config-wins, like `.editorconfig`.
    Without this, a root audit applied the audit root's caps to every file and
    silently missed (or false-flagged) a child space's own size discipline.
    """
    probe = file_path.parent
    while True:
        # `index.md` gate: honor limits.md only at a SPACE root — the exact
        # scopes the malformed-limits audit scans — so resolver and audit agree
        # on active scopes (producer=consumer).
        if (probe / "_meta" / "limits.md").is_file() and (probe / "index.md").is_file():
            return probe
        if probe == wiki_root or probe.parent == probe:
            return wiki_root
        probe = probe.parent


def _resolve_cap_table(
    file_path: Path,
    wiki_root: Path,
    cache: dict[Path, "_model.LimitTable"],
) -> tuple[Path, "_model.LimitTable"]:
    """`(scope_root, table)` for `file_path`, memoized by scope root.

    `scope_root` is `_nearest_limits_scope(...)`; the table is loaded from that
    scope so its patterns match relative to it. Callers pass `scope_root` (NOT
    `wiki_root`) as the `wiki_root` argument to `check_size` / `cap_for_path`
    so per-space patterns resolve against the owning space, not the audit root.
    """
    scope = _nearest_limits_scope(file_path, wiki_root)
    table = cache.get(scope)
    if table is None:
        table = _model.load_limit_table(scope)
        cache[scope] = table
    return scope, table


def scoped_size_verdict(
    file_path: Path,
    projected_text: str,
    wiki_root: Path,
    cache: dict[Path, "_model.LimitTable"] | None = None,
) -> "_model.SizeVerdict":
    """Size verdict for `file_path` against its NEAREST `_meta/limits.md` scope.

    The one cap-resolution path shared by the framework writers
    (`enforce_size_cap`), `space check-size`, and `space audit`: each resolves
    the caps governing `file_path` to the nearest declaring space, so a producer
    and a consumer can never disagree on which cap applies (CONVENTIONS /
    per-space autonomy; HANDBOOK producer=consumer). Without it, writers checked
    the audit root's caps while audit checked each nested space's own — a write
    the producer accepted could be condemned by the very next audit. `cache`
    memoizes loaded tables by scope root for audit's multi-file pass; one-shot
    writers omit it.
    """
    scope, table = _resolve_cap_table(
        file_path, wiki_root, cache if cache is not None else {}
    )
    return _model.check_size(file_path, projected_text, scope, table)


MutateResult = tuple[str, str] | tuple[None, int, str]


def _atomic_mutate_index(
    ancestor: Path,
    ancestor_index: Path,
    mutate_fn: Callable[[str], MutateResult],
) -> tuple[int, object]:
    """Atomically apply `mutate_fn` to the ancestor's index.md under flock.

    Generic primitive shared by add / remove / mount. `mutate_fn` takes the
    current text and returns one of:
    - `(new_text, info)` — replace the file with new_text; info is returned
      to the caller. When new_text == current, the write is skipped.
    - A tuple `(None, error_code, reason)` — abort with that error.

    Returns `(rc, info_or_reason)`:
    - `(0, info)` — wrote new_text (or no-op when mutate_fn returned the
      original text). `info` is whatever mutate_fn passed back (e.g.
      "added", "removed", "noop").
    - `(1, reason)` — write failed.
    - `(2, reason)` — mutate_fn returned an abort tuple (e.g. contract
      missing).

    Locking note: the lock is on the ANCESTOR DIRECTORY's inode (stable
    across our `os.replace` of the index file), so two concurrent CLI
    callers serialize correctly. Within the lock we re-read the index
    from disk to pick up any changes that committed after the caller's
    initial read.
    """
    dir_fd = os.open(str(ancestor), os.O_RDONLY)
    try:
        if fcntl is not None:
            fcntl.flock(dir_fd, fcntl.LOCK_EX)
        try:
            fresh_text = ancestor_index.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return 1, f"could not read {ancestor_index}: {e}"
        result = mutate_fn(fresh_text)
        if isinstance(result, tuple) and len(result) == 3 and result[0] is None:
            return result[1], result[2]
        new_text, info = result
        if new_text == fresh_text:
            return 0, info  # caller can interpret e.g. "noop"
        # Write via tempfile + os.replace. The temp-file CREATE is inside the
        # same OSError guard as the write/replace below: a read-only ancestor
        # dir (or any other create failure) must return a clean `(1, reason)`
        # so the caller rolls back, not raise an uncaught OSError past the
        # chain helper's `except EnsureChainError` — which would leave a
        # materialized mount orphaned and unregistered.
        try:
            durable_replace(ancestor_index, new_text, parent_fd=dir_fd)
        except OSError as e:
            return 1, f"could not write {ancestor_index}: {e}"
        return 0, info
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(dir_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(dir_fd)


def _atomic_register_in_spaces(
    ancestor: Path,
    ancestor_index: Path,
    label: str,
    href: str,
    description: str | None,
) -> tuple[int, str]:
    """Atomically add a `## Spaces` entry under an `fcntl.flock` on the ancestor dir.

    Thin wrapper over `_atomic_mutate_index`. Returns `(0, "added")` /
    `(0, "noop")` / `(1, reason)` / `(2, reason)`. Sole caller is
    `cmd_remove`'s rollback path (re-adding the entry a failed remove already
    took out); symmetric with `_atomic_remove_from_spaces`.
    """
    def add_entry(fresh_text: str) -> MutateResult:
        if not _md.has_section(fresh_text, "Spaces"):
            return (None, 2, "ancestor `## Spaces` section disappeared between contract check and registration")
        new_text = _add_space_entry(fresh_text, label, href, description)
        return (new_text, "noop" if new_text == fresh_text else "added")

    return _atomic_mutate_index(ancestor, ancestor_index, add_entry)


def _atomic_remove_from_spaces(
    ancestor: Path,
    ancestor_index: Path,
    href: str,
) -> tuple[int, str]:
    """Atomically remove a `## Spaces` entry under flock. Symmetric with
    `_atomic_register_in_spaces`. Same return contract."""
    def remove_entry(fresh_text: str) -> MutateResult:
        if not _md.has_section(fresh_text, "Spaces"):
            return (None, 2, "ancestor `## Spaces` section disappeared between contract check and removal")
        new_text = _remove_space_entry(fresh_text, href)
        return (new_text, "noop" if new_text == fresh_text else "removed")

    return _atomic_mutate_index(ancestor, ancestor_index, remove_entry)


# ---------- Size discipline ----------
#
# `_ensure_section_at` and `_ensure_spaces_chain_and_register` (below) enforce
# per-file caps on every projected ancestor mutation; the `space check-size`
# CLI and the other framework-write paths share `enforce_size_cap`. It is
# public so `init_wiki`'s scaffold writer enforces the same cap without
# reaching into a private (one writer, one cap path).


class SizeCapExceeded(Exception):
    """Raised when a projected write would push a file past its cap.

    The message names the matching cap rule (user-override file:line vs
    built-in default) so the refusal traces back to the rule that fired.
    """

    def __init__(self, path: Path, chars: int, cap: int, source: "_model.CapSource") -> None:
        self.path = path
        self.chars = chars
        self.cap = cap
        self.source = source
        # `_format_cap_source` is module-level and resolves at call time, so
        # the forward reference is fine (nothing constructs this at import).
        super().__init__(
            f"{path}: projected {chars} chars > cap {cap} "
            f"({_format_cap_source(source)})"
        )


def enforce_size_cap(path: Path, projected_text: str, wiki_root: Path) -> None:
    """Raise `SizeCapExceeded` when a projected write would exceed the cap.

    Resolves the cap against `path`'s nearest `_meta/limits.md` scope (via
    `scoped_size_verdict`), so a write into a nested space honours THAT space's
    caps — the same scope `space audit` enforces (producer=consumer). The
    exception carries the matching rule's `CapSource` so the refusal names
    user-override file:line vs built-in default. Public: `init_wiki`'s scaffold
    writer calls it so every framework write shares one cap path.
    """
    verdict = scoped_size_verdict(path, projected_text, wiki_root)
    if verdict.outcome == _model.SizeOutcome.OVER:
        raise SizeCapExceeded(
            path, verdict.chars_projected, verdict.cap.cap, verdict.cap.source
        )


# ---------- `## Spaces` section + chain registration ----------


class EnsureChainError(Exception):
    """Raised by `_ensure_spaces_chain_and_register` on atomic-helper failure.

    Carries the entries already added and the notices already emitted so the
    caller can print the partial trail and roll back FS-side state.
    """

    def __init__(
        self,
        ancestor: Path,
        info: str,
        added: list[tuple[Path, str, str]],
        notices: list[str],
    ) -> None:
        self.ancestor = ancestor
        self.info = info
        self.added = added
        self.notices = notices
        super().__init__(f"ensure-chain failed at {ancestor}: {info}")


class ChainExternalRefusal(Exception):
    """Raised by `_preflight_chain_external` when an ancestor `index.md` the
    shared chain helper would write is classified external and `force_external`
    is not set.

    The chain helper walks UP from the leaf and mutates EVERY ancestor's
    `## Spaces` on the way to the wiki root; an external ancestor (under
    `shared/`, a foreign-origin submodule, or an escaping symlink) is scope we
    don't own. The caller prints `self.reason`, naming `self.ancestor`, and
    returns non-zero having mutated NOTHING (read-before-write; external writes
    need explicit instruction — AGENTS.md Sharing & nesting).
    """

    def __init__(self, ancestor: Path, reason: str) -> None:
        self.ancestor = ancestor
        self.reason = reason
        super().__init__(f"refusing external ancestor {ancestor}: {reason}")


def _ensure_section_at(space: Path, wiki_root: Path) -> str:
    """Ensure `space/index.md` carries a `## Spaces` heading.

    Returns `"inserted"` or `"noop"`. Does NOT walk up; does NOT register
    anything in any parent. Used by `cmd_remove` (so a child entry can be
    removed once a `## Spaces` exists), by `audit --fix`'s missing-section
    repair pass, and by `init --adopt`'s leaf section repair. Size-capped
    via `enforce_size_cap`.

    Raises `RuntimeError` on atomic-helper failure (write or cap).
    """
    space_index = space / "index.md"

    def _mutate(fresh_text: str) -> MutateResult:
        if _md.has_section(fresh_text, "Spaces"):
            return (fresh_text, "noop")
        text = fresh_text
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n## Spaces\n\n"
        try:
            enforce_size_cap(space_index, text, wiki_root)
        except SizeCapExceeded as e:
            return (None, 2, f"size cap: {e}")
        return (text, "inserted")

    rc, info = _atomic_mutate_index(space, space_index, _mutate)
    if rc != 0:
        raise RuntimeError(f"ensure-section failed at {space}: {info}")
    return info


@dataclass(frozen=True)
class _ChainEdge:
    """One registration edge on the walk from a leaf space up to the wiki root:
    `child` is registered in `ancestor`'s `## Spaces` as `[label](href)` with
    `description`."""
    ancestor: Path
    child: Path
    label: str
    href: str
    description: str | None


def _iter_chain_edges(
    wiki_root: Path,
    leaf_space: Path,
    *,
    leaf_label: str | None = None,
    leaf_description: str | None = None,
) -> Iterator[_ChainEdge]:
    """Yield each `(ancestor, child)` registration edge from `leaf_space` up to
    and including `wiki_root`.

    The ONE walk behind the chain writer (`_ensure_spaces_chain_and_register`)
    and its two preflights (`_preflight_chain_caps`, `_preflight_chain_external`)
    — maintained once so the producer and the checks can never traverse a
    different ancestor set (producer=consumer). `leaf_label`/`leaf_description`
    apply to the FIRST (leaf) edge only; intermediate ancestor registrations
    use the derived `<child>/` label and no description (book-keeping, not
    user-typed metadata). Stops at `wiki_root` (inclusive), or earlier if the
    walk cannot ascend. Yields nothing when `leaf_space == wiki_root`.
    """
    if leaf_space == wiki_root:
        return
    child = leaf_space
    is_leaf_edge = True
    while child != wiki_root:
        ancestor = _nearest_ancestor_space(wiki_root, child)
        if ancestor == child:
            break
        rel_from_ancestor = child.relative_to(ancestor)
        derived_label = f"{rel_from_ancestor}/"
        href = f"{rel_from_ancestor}/index.md"
        label = leaf_label if (is_leaf_edge and leaf_label) else derived_label
        description = leaf_description if is_leaf_edge else None
        is_leaf_edge = False
        yield _ChainEdge(
            ancestor=ancestor,
            child=child,
            label=label,
            href=href,
            description=description,
        )
        if ancestor == wiki_root:
            break
        child = ancestor


def _ensure_spaces_chain_and_register(
    wiki_root: Path,
    leaf_space: Path,
    *,
    leaf_label: str | None = None,
    leaf_description: str | None = None,
) -> tuple[list[str], list[tuple[Path, str, str]]]:
    """For each `(ancestor, child)` edge from `leaf_space` up to and including
    registration in `wiki_root`'s `## Spaces` (the edges of `_iter_chain_edges`):

      1. Ensure the ancestor's `index.md` carries `## Spaces`.
      2. Register `child` as an entry in that section.

    Walks ALL the way up: `space add foo/bar` against a wiki where
    `foo/index.md` exists bare and `wiki/index.md` has no `## Spaces`
    registers `bar` in `foo`, then `foo` in `<wiki>`, inserting
    `## Spaces` in both.

    Returns `(notices, added_entries)`. On any atomic-helper failure,
    raises `EnsureChainError` carrying the partial state so the caller
    can print and roll back.

    Edge case: `leaf_space == wiki_root` → `([], [])` (nothing to do).
    """
    notices: list[str] = []
    added: list[tuple[Path, str, str]] = []
    for edge in _iter_chain_edges(
        wiki_root, leaf_space,
        leaf_label=leaf_label, leaf_description=leaf_description,
    ):
        ancestor = edge.ancestor
        ancestor_index = ancestor / "index.md"
        label, href, description = edge.label, edge.href, edge.description

        def _mutate(
            fresh_text: str,
            *,
            _label: str = label,
            _href: str = href,
            _desc: str | None = description,
            _idx: Path = ancestor_index,
        ) -> MutateResult:
            text = fresh_text
            inserted = False
            if not _md.has_section(text, "Spaces"):
                if text and not text.endswith("\n"):
                    text += "\n"
                text += "\n## Spaces\n\n"
                inserted = True
            new = _add_space_entry(text, _label, _href, _desc)
            entry_added = new != text
            tag = (
                "inserted-and-added" if inserted and entry_added
                else "inserted" if inserted
                else "added" if entry_added
                else "noop"
            )
            try:
                enforce_size_cap(_idx, new, wiki_root)
            except SizeCapExceeded as e:
                return (None, 2, f"size cap: {e}")
            return (new, tag)

        rc, info = _atomic_mutate_index(ancestor, ancestor_index, _mutate)
        if rc != 0:
            raise EnsureChainError(ancestor, info, added, notices)

        anc_rel = ancestor.relative_to(wiki_root)
        anc_label = "<wiki>" if str(anc_rel) == "." else f"<wiki>/{anc_rel}"
        if info in ("inserted", "inserted-and-added"):
            notices.append(f"  ~ {anc_label}/index.md  +inserted `## Spaces`")
        if info in ("added", "inserted-and-added"):
            notices.append(f"  ~ {anc_label}/index.md ## Spaces  += [{label}]")
            added.append((ancestor, label, href))
    return notices, added


def _preflight_chain_caps(
    wiki_root: Path,
    leaf_space: Path,
    *,
    leaf_label: str | None = None,
    leaf_description: str | None = None,
) -> None:
    """Pre-flight every ancestor write `_ensure_spaces_chain_and_register`
    would make, BEFORE any destructive FS mutation runs.

    Walks the same `(ancestor, child)` edges as the chain helper. For each
    edge, projects the write (insert `## Spaces` if missing, register
    `child`) and runs `enforce_size_cap` on the projection. Raises
    `SizeCapExceeded` on the first overflow. Returns silently on success.

    Used by `cmd_mount` and `cmd_add` to catch ancestor-cap overflow BEFORE
    running destructive operations (`git submodule add`, `git clone`,
    `mkdir`, symlink creation) that the in-lock cap check inside the chain
    helper would otherwise leave half-applied — the chain helper raises
    `EnsureChainError` on mid-chain cap rejection, and for `--mode
    submodule` the rollback is only a manual-recovery notice.
    """
    for edge in _iter_chain_edges(
        wiki_root, leaf_space,
        leaf_label=leaf_label, leaf_description=leaf_description,
    ):
        ancestor_index = edge.ancestor / "index.md"
        text = ancestor_index.read_text(encoding="utf-8")
        if not _md.has_section(text, "Spaces"):
            if text and not text.endswith("\n"):
                text += "\n"
            text += "\n## Spaces\n\n"
        projected = _add_space_entry(text, edge.label, edge.href, edge.description)
        enforce_size_cap(ancestor_index, projected, wiki_root)


def _preflight_chain_external(
    wiki_root: Path,
    leaf_space: Path,
    *,
    force_external: bool = False,
) -> None:
    """Refuse-and-report (read-before-write) for the SHARED chain primitive,
    BEFORE any destructive FS mutation runs.

    Walks the SAME `(ancestor, child)` edges as
    `_ensure_spaces_chain_and_register` / `_preflight_chain_caps`. For every
    ancestor whose `index.md` the chain helper WOULD mutate, classifies that
    ancestor via `_is_in_external_scope` — the same classifier
    `cmd_add` / `cmd_remove` / `cmd_promote` already use, so "external" means
    the same thing everywhere (under `shared/`, a foreign-origin submodule, or
    an escaping symlink). On the first external ancestor it raises
    `ChainExternalRefusal` (unless `force_external`): wiki-spaces does not
    mutate external scope without explicit instruction (AGENTS.md Sharing &
    nesting).

    Keys off the ANCESTOR INDEX being written, NOT off `leaf_space`'s own path
    — so the default `mount shared/foo` (whose nearest ancestor space is the
    OWNED wiki root, since `shared/` is a plain folder with no `index.md`)
    stays allowed. Returns silently on success.

    Walks the SAME `_iter_chain_edges` as the chain helper, so the set of
    ancestors guarded here is identical to the set the chain helper mutates
    (producer=consumer).
    """
    if force_external:
        return
    for edge in _iter_chain_edges(wiki_root, leaf_space):
        is_ext, reason = _is_in_external_scope(edge.ancestor, wiki_root)
        if is_ext:
            raise ChainExternalRefusal(edge.ancestor, reason)


def _rollback_added_entries(entries: list[tuple[Path, str, str]]) -> list[str]:
    """Undo entries added by `_ensure_spaces_chain_and_register`, deepest first.

    The chain helper appends to `entries` as it walks UP from the leaf, so the
    first appended entry is the deepest (the leaf's parent) and the last is
    the wiki root. Iterating in forward order therefore removes deepest-first,
    matching the chain-registration rollback contract.

    Best-effort: returns a list of failure notices (empty when every removal
    succeeded) for the caller to present at the CLI layer; never raises.
    Inserted `## Spaces` sections are NOT rolled back — they're append-only
    and non-destructive; leaving them in place is the safe choice.
    """
    notices: list[str] = []
    for ancestor, label, href in entries:
        ancestor_index = ancestor / "index.md"
        rc, info = _atomic_remove_from_spaces(ancestor, ancestor_index, href)
        if rc != 0:
            notices.append(
                f"  ! could not roll back entry [{label}] from {ancestor_index}: {info}"
            )
    return notices


def _rel_or_str(path: Path | None, wiki_root: Path) -> str | None:
    """JSON serializer helper: wiki-root-relative posix path when the
    target is inside the wiki tree, the absolute string otherwise, None
    when there's no target (e.g. a basename strategy that found nothing).
    Keeps the audit JSON readable for in-tree paths while still surfacing
    external candidates (foreign submodules, escaping symlinks)."""
    if path is None:
        return None
    try:
        return path.relative_to(wiki_root).as_posix()
    except ValueError:
        return str(path)


def _format_cap_source(source: "_model.CapSource") -> str:
    """Human-readable one-liner describing where a cap value came from.

    Used by `cmd_check_size` and the audit's size-violations report —
    the producer can always trace `OK 4977/5000` back to the matching
    rule without guessing at the cap table. Source data lives on the
    structured dataclass; this is the display formatter only.
    """
    if source.kind == _model.CapSourceKind.USER_OVERRIDE:
        loc = (
            f"_meta/limits.md:{source.line + 1}"
            if source.line is not None else "_meta/limits.md"
        )
        return f"cap: user override {source.pattern!r} at {loc}"
    return f"cap: built-in default for {source.pattern!r}"

