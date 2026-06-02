"""Internal semantic model for wiki-spaces.

Shared shapes for the concepts the framework reasons about: nodes
(filesystem facts), the page index, the cap table, size verdicts,
wikilink resolution, and frontmatter status. Commands consume these
where they need them — `list` and the audit summary read node facts,
`caps` and the size checks read the cap table, the wikilink audit reads
the page index.

Filesystem I/O is allowed (we walk paths and read files). No CLI I/O
(no print) — presentation lives at the CLI layer. The shapes carry
the data presentation needs.

Design constraints, applied throughout:

- Use-case agnostic. No `project`, `recipe`, or other domain vocabulary.
- Per-space autonomy. Discovery walks structure; convention detection
  (frontmatter present? taxonomy.md present?) is per-space, consumed
  after discovery by whoever cares. No propagation.
- Avoid edge-case branches. A "missing" entry is a node with the
  relevant boolean set, not a special case. A malformed YAML file is
  a FrontmatterResult with `status=MALFORMED`, not `None`.
- Verdicts carry their provenance. `CapVerdict` carries `CapSource`;
  `WikilinkResolution` carries `attempts`; `FrontmatterResult` carries
  `error_line`. Presentation reads these; it doesn't recompute them.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import _md
from ._common import has_control_chars


# ---------- Trust scope ----------


class TrustScope(Enum):
    OWNED = "owned"
    EXTERNAL = "external"


@dataclass(frozen=True)
class TrustClassification:
    """Whether a path is owned or external, plus the boundary that decided it.

    The owned/external split is path-based per CONVENTIONS / Owned vs
    external: under `<wiki>/shared/`, a foreign-origin submodule, or a
    symlink whose realpath escapes the wiki tree. `boundary` is set to
    the nearest external ancestor (which may be the path itself); `reason`
    names the heuristic that fired.
    """
    scope: TrustScope
    boundary: Path | None
    reason: str | None


# ---------- ## Spaces sections ----------


@dataclass(frozen=True)
class SectionBlock:
    """Raw, lossless view of a `## <heading>` block.

    Carries the literal body lines and their source spans so downstream
    parsers (well-formed entries, malformed scanners, registration
    collectors) can all consume the same primitive. `parse_section_block`
    is the only function that should walk a markdown text looking for
    section boundaries.
    """
    heading: str
    heading_line: int                    # 0-based line index of `## <heading>`
    body_lines: list[str]                # raw, including trailing whitespace
    body_span: tuple[int, int]           # (start_line, end_line_exclusive)


@dataclass(frozen=True)
class SpacesEntry:
    """One bullet under `## Spaces`, normalized.

    `description` is the text after `— ` on the entry line (the
    parent's navigation description for the child space). `href` is the
    raw href string (use `normalize_spaces_href` to get the directory).
    """
    label: str
    href: str
    description: str | None
    source_line: int                     # 0-based line in the parent index.md


# ---------- Frontmatter ----------
#
# The frontmatter parser and its result types live in `_md` (the markdown
# layer that owns the parse). Re-exported here so the domain-layer consumers
# (audit, promote, page index) keep referring to `_model.FrontmatterResult`
# etc. — one parser, one source of truth.
FrontmatterStatus = _md.FrontmatterStatus
FrontmatterResult = _md.FrontmatterResult
parse_frontmatter_result = _md.parse_frontmatter_result


# ---------- Nodes (filesystem-level facts) ----------


@dataclass(frozen=True)
class NodeFacts:
    """Orthogonal facts about one folder under the wiki root.

    No status enum: the facts are independent booleans plus trust
    classification and edge-derived flags. Consumers filter on whichever
    combination they care about:

    - `list`         → `has_index AND has_spaces_section AND contract_reachable`
    - `audit drift`  → `has_index AND NOT (has_spaces_section AND contract_reachable)`
    - `audit unreg`  → `has_index AND has_spaces_section AND NOT registered_in_nearest_ancestor`
    - `external`    → `trust.scope == EXTERNAL`

    `contract_reachable` is computed by BFS from `wiki_root` along
    `## Spaces` chains; it is a stronger condition than
    `registered_in_nearest_ancestor` because a chain can break above.
    """
    path: Path
    real_path: Path | None
    has_index: bool
    has_spaces_section: bool
    trust: TrustClassification
    nearest_ancestor_space: Path | None
    registered_in_nearest_ancestor: bool
    contract_reachable: bool


# ---------- Page index ----------


@dataclass(frozen=True)
class PageIndex:
    """Unified candidates + alias index + frontmatter error map.

    Built once per audit/list/promote pass; consumed by `resolve_wikilink`
    and by the audit's duplicate-alias / malformed-frontmatter findings.
    No parallel structures can drift; aliases use casefold.
    """
    pages: set[Path]
    by_basename: dict[str, list[Path]]
    by_alias: dict[str, list[Path]]                          # casefold(alias) → pages
    duplicate_aliases: dict[str, list[Path]]                 # casefold(alias) → pages (>1)
    frontmatter_errors: dict[Path, FrontmatterResult]


# ---------- Wikilink resolution ----------


class WikilinkStatus(Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS_ALIAS = "ambiguous_alias"


@dataclass(frozen=True)
class ResolutionAttempt:
    """One strategy the resolver tried.

    `candidate` is the path the strategy probed (None when the strategy
    has no notion of a single candidate, e.g. basename lookup). `outcome`
    is one of `matched`, `no_match`, `ambiguous`.
    """
    strategy: str                        # "wiki_root_pathful" | "base_relative" | "basename" | "alias"
    candidate: Path | None
    outcome: str


@dataclass(frozen=True)
class WikilinkResolution:
    """The result of resolving one `[[target]]`.

    `target` is set when `status == RESOLVED`. `candidates` is set when
    `status == AMBIGUOUS_ALIAS` (duplicate-alias collision). `attempts`
    is the ordered trace of strategies the resolver ran.
    """
    status: WikilinkStatus
    target: Path | None
    candidates: list[Path]
    attempts: list[ResolutionAttempt]
    reason: str | None                   # short human-readable when unresolved


# ---------- Caps + sizes ----------


class CapSourceKind(Enum):
    BUILTIN_DEFAULT = "builtin_default"
    USER_OVERRIDE = "user_override"


@dataclass(frozen=True)
class CapSource:
    """Where a cap value came from. Display layer formats this; we do not."""
    kind: CapSourceKind
    pattern: str
    file: Path | None                    # _meta/limits.md when USER_OVERRIDE; None for builtin
    line: int | None                     # line in limits.md when USER_OVERRIDE


@dataclass(frozen=True)
class CapVerdict:
    cap: int
    source: CapSource


@dataclass(frozen=True)
class LimitTable:
    """The active cap rules at a wiki root, in match order.

    `rules` is the list of `(pattern, cap, source)` tuples — user
    overrides (from `_meta/limits.md`) first, then built-in defaults.
    `malformed_rows` collects rows the parser couldn't read; audit
    surfaces them so the user can repair without silent loss.
    """
    rules: list[tuple[str, int, CapSource]]
    malformed_rows: list[tuple[int, str]]


class SizeOutcome(Enum):
    OK = "ok"
    OK_SHRINKING = "ok_shrinking"
    OVER = "over"


@dataclass(frozen=True)
class SizeVerdict:
    outcome: SizeOutcome
    chars_projected: int
    chars_current: int
    cap: CapVerdict


# ====================================================================
# Raw, lossless primitives
# ====================================================================


def parse_section_block(text: str, heading: str) -> SectionBlock | None:
    """Find `## <heading>` and return its raw body lines + spans.

    Returns None when the heading is absent. The body runs from the line
    immediately after the heading to (but not including) the next `## `
    heading or end-of-file. Trailing whitespace on each line is preserved
    so downstream parsers see the exact bytes.

    The rich, span-carrying view of a section block: section-aware scanners
    (well-formed entries, malformed bullets, registration edges) consume this.
    The boundary scan itself is `_md.find_section_bounds` — the one primitive
    shared with `_md`'s producer-side editors, so reads and writes agree on
    what a section spans.
    """
    lines = text.splitlines()
    bounds = _md.find_section_bounds(lines, heading)
    if bounds is None:
        return None
    heading_line, body_start, body_end = bounds
    return SectionBlock(
        heading=heading,
        heading_line=heading_line,
        body_lines=lines[body_start:body_end],
        body_span=(body_start, body_end),
    )


# The markdown-link metacharacters forbidden anywhere inside a `## Spaces`
# href. A `## Spaces` entry is `- [LABEL](HREF) — DESC`; `[ ] ( )` break the
# link parse outright, while `{ }` survive it yet are rejected by the audit
# scanner. Canonical home for the set so the traversal consumer
# (`normalize_spaces_href`), the audit scanner, and the writer
# (`_validate_rel_path`) all agree on what a registrable href is — without it,
# `foo{bar}/index.md` was traversed by `list`/`files` yet flagged malformed by
# `audit` (producer=consumer asymmetry).
SPACES_HREF_METACHARS = "[](){}"


def normalize_spaces_href(href: str) -> tuple[str | None, str | None]:
    """Normalize a `## Spaces` entry href to its child directory.

    Returns `(normalized_dir, error)`. On success, `error` is None and
    `normalized_dir` is the directory the entry refers to (e.g. `foo`,
    `nested/bar`). On failure, `normalized_dir` is None and `error` is
    a short reason.

    Failure shapes (from CONVENTIONS / Malformed entries):
      - empty href
      - absolute href
      - href containing `..` segments
      - reserved-name segment (hidden `.X`, `_archives`, `_meta`)
      - href containing a markdown-link metacharacter (`SPACES_HREF_METACHARS`)
    """
    if not href.strip():
        return None, "empty href"
    h = href.strip()
    if h.startswith("/"):
        return None, "absolute href"
    if h.endswith("/index.md"):
        h = h[: -len("/index.md")]
    h = h.rstrip("/")
    if h in ("", "."):
        return None, "self-referential href (normalizes to the space itself)"
    bad = [c for c in SPACES_HREF_METACHARS if c in h]
    if bad:
        return None, f"href contains markdown metacharacter(s): {''.join(bad)}"
    parts = Path(h).parts
    if ".." in parts:
        return None, "href contains `..`"
    for part in parts:
        if is_reserved_segment(part):
            return None, f"reserved-name segment: {part}"
    return h, None


# ---------- External / owned classification ----------


def _resolve_git_config(wiki_root: Path) -> Path | None:
    """Locate the git config file for `wiki_root` (worktree/submodule-aware).

    Two layouts per CONVENTIONS.md / `.git`:
    - `<wiki>/.git/` is a directory (regular repo) → `<wiki>/.git/config`.
    - `<wiki>/.git` is a FILE (git worktree or submodule). Its body is
      `gitdir: <path>` pointing at the real git dir. A worktree's gitdir
      carries a `commondir` file pointing at the shared repo, whose `config`
      is the authoritative origin source; a submodule embeds config directly
      under its gitdir.

    Returns the config path when it exists on disk, else None. Pure FS
    parsing — no subprocess, matching this module's stdlib-only stance.

    Single source of truth: `space`'s owned/external classifiers route through
    this module (`classify_external_scope` / `is_foreign_submodule`), so they
    and these walkers can never disagree on a worktree. (The bug this
    consolidation fixes: a walker that read `gitdir/config` directly returned
    None in a worktree — worktrees share config via `commondir` — and that None
    made `is_foreign_submodule` treat every submodule as foreign.)
    """
    git_entry = wiki_root / ".git"
    if git_entry.is_dir():
        config = git_entry / "config"
        return config if config.is_file() else None
    if not git_entry.is_file():
        return None
    try:
        body = git_entry.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    gitdir: Path | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("gitdir:"):
            target = stripped[len("gitdir:"):].strip()
            if not target:
                return None
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (git_entry.parent / target_path).resolve()
            gitdir = target_path
            break
    if gitdir is None or not gitdir.is_dir():
        return None
    # Worktrees keep per-worktree state under gitdir but share config via
    # commondir. Submodules embed config directly under gitdir.
    commondir_file = gitdir / "commondir"
    if commondir_file.is_file():
        try:
            common = commondir_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            common = ""
        if common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = (gitdir / common_path).resolve()
            shared_config = common_path / "config"
            if shared_config.is_file():
                return shared_config
    config = gitdir / "config"
    return config if config.is_file() else None


def _wiki_origin_url(wiki_root: Path) -> str | None:
    """Return the wiki's origin remote URL from its git config, or None.

    Resolves `.git/` (directory) and `.git` (file — worktree or submodule)
    via `_resolve_git_config`. Best-effort regex parse — no subprocess.
    """
    config = _resolve_git_config(wiki_root)
    if config is None:
        return None
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = re.search(
        r'\[remote\s+"origin"\][^\[]*?url\s*=\s*(\S+)',
        text,
        re.DOTALL,
    )
    return m.group(1).strip() if m else None


def is_foreign_submodule(path: Path, wiki_root: Path) -> bool:
    gitmodules = wiki_root / ".gitmodules"
    if not gitmodules.is_file():
        return False
    try:
        rel = path.resolve().relative_to(wiki_root).as_posix()
    except (ValueError, OSError, RuntimeError):
        return False
    try:
        text = gitmodules.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    sections = re.split(r"(?=^\[submodule )", text, flags=re.MULTILINE)
    for section in sections:
        m_path = re.search(r"^\s*path\s*=\s*(.+)$", section, re.MULTILINE)
        if not m_path or m_path.group(1).strip() != rel:
            continue
        m_url = re.search(r"^\s*url\s*=\s*(\S+)", section, re.MULTILINE)
        if not m_url:
            return False
        sub_url = m_url.group(1).strip()
        wiki_url = _wiki_origin_url(wiki_root)
        if wiki_url is None:
            return True
        return sub_url != wiki_url
    return False


def external_reason_for(path: Path, wiki_root: Path) -> str | None:
    """Return the reason `path` is itself external, or None."""
    try:
        rel = path.relative_to(wiki_root)
    except ValueError:
        return "path is outside the wiki tree"
    if rel.parts and rel.parts[0] == "shared":
        return "under shared/"
    if is_foreign_submodule(path, wiki_root):
        return "foreign-origin git submodule"
    if path.is_symlink():
        try:
            target = path.resolve()
            trel = target.relative_to(wiki_root)
        except (OSError, ValueError, RuntimeError):
            return "symlink escapes the wiki tree"
        if target.is_symlink():
            # `resolve()` returned a still-symlink: a cyclic / self-referential
            # link that can't be canonicalized to a real file. It resolves to
            # no owned content, so treat it as escaping — and stop, because
            # recursing into `classify_external_scope` on a path that resolves
            # to itself would not terminate.
            return "symlink escapes the wiki tree"
        # An owned-looking symlink whose realpath lands inside external scope
        # (under `shared/`, or inside a foreign submodule) is external too —
        # kept symmetric with the `space` package's external write-guards so
        # the producer's guards and this consumer-side classifier agree on the
        # same aliases (producer=consumer).
        if trel.parts and trel.parts[0] == "shared":
            return "symlink into external shared/ scope"
        if classify_external_scope(target, wiki_root).scope == TrustScope.EXTERNAL:
            return "symlink into a foreign-origin submodule"
    return None


def symlink_escapes_wiki(path: Path, wiki_root: Path) -> bool:
    """True when `path` is a symlink that resolves into external trust scope.

    `_common.atomic_write` follows a symlink to its realpath, so a framework
    write through such a link mutates content beyond the trust boundary
    (HANDBOOK: writes stay inside the trust boundary). This is the producer-side
    guard the framework writers (`space log`, `space add --force-index`, `init`,
    `space promote`) check before writing. It DELEGATES to the consumer-side
    classifier `external_reason_for` rather than re-deriving containment, so the
    producer and consumer can never disagree on what is external (HANDBOOK:
    unify, don't fork). An internal alias whose realpath stays inside writes
    through; an escaping / cyclic link, or one resolving under `shared/` or into
    a foreign submodule, is refused.
    """
    return path.is_symlink() and external_reason_for(path, wiki_root) is not None


def classify_external_scope(path: Path, wiki_root: Path) -> TrustClassification:
    """Walk from `path` up to (not including) `wiki_root` to find the external boundary.

    Returns `OWNED` when neither `path` nor any ancestor up to (but not
    including) `wiki_root` is external. Otherwise returns `EXTERNAL`
    with the *outermost* external ancestor as the boundary — for
    `shared/team/sub` the boundary is `shared/`, not `shared/team/sub`,
    because that is the actual mount point.

    Walking to the outermost (instead of returning at the first match)
    means callers like audit and adopt can quote the correct user-facing
    folder ("this is inside `shared/`") rather than the deeper path.
    """
    try:
        path.relative_to(wiki_root)
    except ValueError:
        # The path is outside the wiki tree entirely. The boundary is
        # the path itself — there is nothing further to walk towards
        # `wiki_root`.
        return TrustClassification(
            scope=TrustScope.EXTERNAL,
            boundary=path,
            reason="path is outside the wiki tree",
        )
    outermost: tuple[Path, str] | None = None
    p = path
    while p != wiki_root:
        reason = external_reason_for(p, wiki_root)
        if reason is not None:
            outermost = (p, reason)
        p = p.parent
    if outermost is None:
        return TrustClassification(scope=TrustScope.OWNED, boundary=None, reason=None)
    boundary, reason = outermost
    return TrustClassification(scope=TrustScope.EXTERNAL, boundary=boundary, reason=reason)


# ====================================================================
# Discovery + registration edges
# ====================================================================


RESERVED_NAMES = ("_archives", "_meta")


def is_reserved_segment(name: str) -> bool:
    """True for a path segment every consumer walker / validator skips: a
    hidden `.X` folder (`.git`, `.obsidian`, …) or a reserved convention folder
    (`_archives`, `_meta`). The one predicate behind the reserved-name skip so
    the walkers and the href validator can't drift on what they prune."""
    return name.startswith(".") or name in RESERVED_NAMES


def _walk_filesystem(wiki_root: Path, *, include_external: bool) -> Iterator[Path]:
    """Yield every folder under `wiki_root` worth considering as a node.

    Includes the wiki root itself. Prunes hidden directories (names
    starting with `.`), `_archives/`, `_meta/`. External subtrees are
    yielded only when `include_external=True`.

    Realpath-based cycle detection.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return
    visited: set[Path] = {root_real}
    yield wiki_root
    stack: list[Path] = [wiki_root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for child in entries:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in RESERVED_NAMES:
                continue
            # A line-break char in the name makes the directory unrepresentable
            # as a `## Spaces` entry: the bullet would split across lines for the
            # consumer (`str.splitlines()`). Prune it like a reserved name so a
            # discovery-driven writer (`init --adopt`, `audit --fix`) never
            # registers an entry the consumer can't read (producer=consumer).
            if has_control_chars(child.name):
                continue
            try:
                child_real = child.resolve()
            except (OSError, RuntimeError):
                continue
            if child_real in visited:
                continue
            tc = classify_external_scope(child, wiki_root)
            if tc.scope == TrustScope.EXTERNAL and not include_external:
                # Yield the boundary so callers (adopt, audit) can emit a
                # per-skip notice, but DON'T descend — and DON'T mark its
                # realpath visited. An external symlink can alias an owned
                # space (e.g. `shared` -> `z/foo`); recording its realpath
                # here would later skip the *real* owned `z/foo` as "already
                # visited", hiding its drift. `visited` is cycle detection for
                # descent, so a non-descended boundary must not poison it.
                yield child
                continue
            visited.add(child_real)
            yield child
            stack.append(child)


def _registered_dirs(parent_index_text: str) -> set[str]:
    """Set of normalized child directories listed in `## Spaces`.

    Skips malformed entries. Used by `discover_nodes` to compute
    `registered_in_nearest_ancestor` per child.
    """
    block = parse_section_block(parent_index_text, "Spaces")
    if block is None:
        return set()
    out: set[str] = set()
    for entry in _md.parse_section_entries(parent_index_text, "Spaces"):
        if not entry.href:
            continue
        normalized, err = normalize_spaces_href(entry.href)
        if err is not None or normalized is None:
            continue
        out.add(normalized)
    return out


def discover_nodes(
    wiki_root: Path,
    *,
    include_external: bool = False,
) -> list[NodeFacts]:
    """Walk the wiki tree and return one `NodeFacts` per folder.

    The wiki root is always first. Children are in directory-sort order
    (deterministic). External subtrees are pruned unless `include_external`;
    external boundaries (the first external folder encountered) are
    yielded either way so callers can emit a per-skip notice.

    `registered_in_nearest_ancestor` is computed against the *nearest
    folder with an index.md* (which may be many levels up if the
    intermediate folders are plain). `contract_reachable` is computed by
    a separate BFS from `wiki_root` along `## Spaces` registrations —
    see `_compute_contract_reachable`.
    """
    folders = list(_walk_filesystem(wiki_root, include_external=include_external))
    # Read each folder's index.md once and cache the result.
    index_text_cache: dict[Path, str | None] = {}
    for folder in folders:
        idx = folder / "index.md"
        try:
            index_text_cache[folder] = idx.read_text(encoding="utf-8") if idx.is_file() else None
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError: a non-UTF-8 index.md is a legal Obsidian-wire
            # boundary input — degrade to no contract, never crash discovery.
            index_text_cache[folder] = None

    registered_dirs_cache: dict[Path, set[str]] = {}
    for folder, text in index_text_cache.items():
        registered_dirs_cache[folder] = _registered_dirs(text) if text is not None else set()

    # A folder is a valid space only when its index.md carries `## Spaces`.
    # `contract_reachable` propagates ONLY through valid spaces (and the root
    # is the anchor), matching the consumer walker which never yields or
    # descends a registered-but-bare child.
    valid_spaces: set[Path] = {
        folder
        for folder, text in index_text_cache.items()
        if text is not None and _md.has_section(text, "Spaces")
    }

    reachable = _compute_contract_reachable(
        wiki_root,
        folders=folders,
        registered_dirs=registered_dirs_cache,
        valid_spaces=valid_spaces,
    )

    out: list[NodeFacts] = []
    for folder in folders:
        text = index_text_cache.get(folder)
        has_index = text is not None
        has_spaces = text is not None and _md.has_section(text, "Spaces")
        trust = classify_external_scope(folder, wiki_root)
        ancestor = nearest_ancestor_space(
            wiki_root, folder, lambda p: index_text_cache.get(p) is not None
        )
        if ancestor is None or folder == wiki_root:
            registered_local = folder == wiki_root
        else:
            try:
                rel = folder.resolve().relative_to(ancestor.resolve()).as_posix()
            except (OSError, ValueError, RuntimeError):
                rel = folder.name
            registered_local = rel in registered_dirs_cache.get(ancestor, set())
        try:
            real_path: Path | None = folder.resolve()
        except (OSError, RuntimeError):
            real_path = None
        out.append(NodeFacts(
            path=folder,
            real_path=real_path,
            has_index=has_index,
            has_spaces_section=has_spaces,
            trust=trust,
            nearest_ancestor_space=ancestor,
            registered_in_nearest_ancestor=registered_local,
            contract_reachable=folder in reachable,
        ))
    return out


def nearest_ancestor_space(
    wiki_root: Path,
    target: Path,
    has_index: Callable[[Path], bool],
) -> Path | None:
    """Nearest ancestor of `target` (exclusive) whose folder has an index.md.

    `has_index(p)` reports whether folder `p` carries an `index.md` — the one
    walk, parametrized on its index source so the cache-backed discovery path
    and `space`'s disk-backed command path share it (producer=consumer).
    Returns None when `target` is the wiki root itself or no ancestor up to and
    including `wiki_root` qualifies.
    """
    if target == wiki_root:
        return None
    p = target.parent
    while True:
        if has_index(p):
            return p
        if p == wiki_root:
            return wiki_root if has_index(wiki_root) else None
        p = p.parent


def _compute_contract_reachable(
    wiki_root: Path,
    *,
    folders: list[Path],
    registered_dirs: dict[Path, set[str]],
    valid_spaces: set[Path],
) -> set[Path]:
    """BFS from `wiki_root` along `## Spaces` chains to find reachable spaces.

    A folder is `contract_reachable` when there exists a path
    `wiki_root → A → B → … → folder` where each step is a well-formed
    `## Spaces` entry in the parent's `index.md`, the child folder exists in
    the discovery set, AND the child is itself a valid space (its `index.md`
    carries `## Spaces`). The wiki root is the anchor and is always reachable.

    The valid-space requirement is what keeps this aligned with the consumer
    walker: a registered child that has an `index.md` but no `## Spaces` is
    drift, not a consumer-visible space, so it is NOT reachable (and cannot
    propagate reachability — its `registered_dirs` are empty anyway). Without
    it, a bare child would read as `contract_reachable=True`, a trap for any
    caller that uses reachability directly as the consumer predicate.

    Note this is strictly stronger than `registered_in_nearest_ancestor`:
    a folder listed in its parent's `## Spaces` is locally registered
    but unreachable if the parent itself is not reachable, or if the folder
    is not a valid space.
    """
    folder_set = {f.resolve(): f for f in folders if f.exists()}
    reachable: set[Path] = set()
    if wiki_root not in folders:
        return reachable
    queue: list[Path] = [wiki_root]
    reachable.add(wiki_root)
    while queue:
        current = queue.pop()
        for child_rel in registered_dirs.get(current, set()):
            child = current / child_rel
            try:
                child_real = child.resolve()
            except (OSError, RuntimeError):
                continue
            if child_real not in folder_set:
                continue
            real_folder = folder_set[child_real]
            if real_folder in reachable:
                continue
            # Only valid spaces are consumer-reachable. The root is the
            # anchor (always reachable); every other hop must be a space
            # whose index.md carries `## Spaces`.
            if real_folder != wiki_root and real_folder not in valid_spaces:
                continue
            reachable.add(real_folder)
            queue.append(real_folder)
    return reachable


# ====================================================================
# Command-facing traversals
# ====================================================================
#
# Two distinct model-owned traversals, mirroring the two the CLI needs:
#
# - CONSUMER / CONTRACT (`discover_consumer_spaces`, `discover_md_files`):
#   follows `## Spaces`, hides unregistered drift, skips malformed/reserved
#   hrefs, honours the external opt-in. Backs `space list`, `space files`,
#   and the consumer-visible part of `space promote`.
# - OWNED / FILESYSTEM (`discover_owned_md_files`, `drift_from_nodes` over
#   `discover_nodes`): sees owned on-disk structure including drift and bare
#   `index.md` folders. Backs `space audit`.
#
# They are NOT collapsible: the consumer walker must hide what the audit
# walker must surface. Output order is part of the contract (skills parse it),
# so these reproduce the stack-driven (LIFO) discovery order exactly.


@dataclass(frozen=True)
class ConsumerSpace:
    """A space reachable via the `## Spaces` navigation contract.

    `label`/`description` come from the registering entry in
    `source_parent`'s `index.md` (at `source_line`); the wiki root carries
    `None` for all four (it has no registering entry). `external` is the
    consumer-visible trust flag, inherited down an external subtree.
    """
    path: Path
    real_path: Path | None
    external: bool
    label: str | None
    description: str | None
    source_parent: Path | None
    source_line: int | None


@dataclass(frozen=True)
class ConsumerFile:
    """A `.md` file reachable via the navigation contract (plain-folder
    descent inside each consumer space, stopping at child-space boundaries)."""
    path: Path
    real_path: Path | None
    external: bool


@dataclass(frozen=True)
class ExternalBoundary:
    """An externally-classified folder NOT visible as a consumer space —
    a foreign submodule or escaping symlink lacking `index.md` + `## Spaces`.
    Surfaced by `space list --include-boundaries` so a placement classifier
    can enumerate every external path to exclude."""
    path: Path
    real_path: Path | None
    reason: str | None


@dataclass(frozen=True)
class SpaceDrift:
    """`## Spaces` drift for one ancestor space.

    `missing` = owned child folders (with `index.md`, INCLUDING bare ones
    lacking `## Spaces`) whose nearest ancestor is this space but which are
    not listed in its `## Spaces`. `stale` = listed entries whose target has
    no `index.md` on disk. Both sorted. `space` is absolute; the caller
    relativises for display.
    """
    space: Path
    missing: list[str]
    stale: list[str]


def href_to_dir(href: str) -> str:
    """Lenient `## Spaces` href → child-directory normalizer.

    Strips a trailing `/index.md` and trailing slashes; does NOT reject
    reserved/absolute/`..` hrefs (that is `normalize_spaces_href`'s job).
    Drift's `listed` set uses this lenient form so a reserved href like
    `_meta/x/index.md` still surfaces as a stale `_meta/x` entry — matching
    the read-only audit's behaviour. The one href->dir normalizer, shared by `space`'s `## Spaces` add/remove and the audit drift scan.
    """
    h = href.strip()
    if h.endswith("/index.md"):
        h = h[: -len("/index.md")]
    return h.rstrip("/")


def _spaces_entries_with_lines(text: str) -> list[SpacesEntry]:
    """Parse `## Spaces` markdown-link entries with their source line index.

    Reuses `parse_section_block` (the section-boundary primitive) and
    `_md.ENTRY_RE` (the canonical entry regex `_md.parse_section_entries`
    uses), so this never drifts from the producer's parse. Wikilink-form
    bullets are skipped — a `## Spaces` entry needs an href.
    """
    block = parse_section_block(text, "Spaces")
    if block is None:
        return []
    out: list[SpacesEntry] = []
    start = block.body_span[0]
    for offset, raw in enumerate(block.body_lines):
        m = _md.ENTRY_RE.match(raw)
        if not m:
            continue
        out.append(SpacesEntry(
            label=m.group(1),
            href=m.group(2),
            description=(m.group(3) or "").strip() or None,
            source_line=start + offset,
        ))
    return out


def discover_consumer_spaces(
    wiki_root: Path,
    *,
    include_external: bool = False,
) -> list[ConsumerSpace]:
    """Spaces reachable via `## Spaces`, in stack-driven discovery order.

    The wiki root is first (no registering entry). Each child is discovered
    by parsing its parent's `## Spaces` in file order; malformed/reserved
    hrefs are skipped, external children are skipped unless `include_external`
    (and then carry `external=True`, inherited down the subtree), and a child
    is yielded only when its own `index.md` carries `## Spaces` (a registered
    bare child is drift, not a consumer space). Realpath cycle detection.

    Label/description are attached inline from the registering entry, so the
    contract is walked once — there is no second contract walk.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return []
    visited: set[Path] = {root_real}
    out: list[ConsumerSpace] = [ConsumerSpace(
        path=wiki_root, real_path=root_real, external=False,
        label=None, description=None, source_parent=None, source_line=None,
    )]
    # Stack carries (space, is_external) so descendants inherit external.
    stack: list[tuple[Path, bool]] = [(wiki_root, False)]
    while stack:
        current, parent_external = stack.pop()
        try:
            text = (current / "index.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for entry in _spaces_entries_with_lines(text):
            if not entry.href:
                continue
            normalized, err = normalize_spaces_href(entry.href)
            if err is not None or normalized is None:
                continue
            child = current / normalized
            try:
                child_real = child.resolve()
            except (OSError, RuntimeError):
                continue
            tc = classify_external_scope(child, wiki_root)
            child_external = parent_external or tc.scope == TrustScope.EXTERNAL
            # A child whose realpath escapes the wiki tree is external even
            # when no ancestor matched a named heuristic (resolution escape).
            if not child_external:
                try:
                    child_real.relative_to(root_real)
                except ValueError:
                    child_external = True
            if child_external and not include_external:
                continue
            if child_real in visited:
                continue
            child_index = child / "index.md"
            if not child_index.is_file():
                continue
            try:
                child_text = child_index.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # v1 contract: a consumer space requires `index.md` AND `## Spaces`.
            if not _md.has_section(child_text, "Spaces"):
                continue
            visited.add(child_real)
            out.append(ConsumerSpace(
                path=child,
                real_path=child_real,
                external=child_external,
                label=entry.label or normalized,
                description=entry.description,
                source_parent=current,
                source_line=entry.source_line,
            ))
            stack.append((child, child_external))
    return out


def descend_plain_md_files(
    space: Path,
    space_external: bool,
    wiki_root: Path,
    *,
    include_external: bool = False,
) -> Iterator[ConsumerFile]:
    """Yield `.md` files inside ONE space, descending PLAIN folders only.

    Stops at nested child-space boundaries (folders with their own `index.md`)
    and skips hidden, `_archives`, `_meta`, and (by default) external
    descendants. `space_external` seeds the external flag for `space` itself
    (the caller has already classified it). Output is DFS over sorted directory
    entries.

    The one inner descent behind both `discover_md_files` (the contract-
    reachable set, one consumer space at a time) and `space._walk_space_md_files`
    (a single space, for promote's sibling rewrites) — one producer=consumer
    walk, not two hand-copied ones.
    """
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return
    stack: list[tuple[Path, bool]] = [(space, space_external)]
    seen: set[Path] = set()
    while stack:
        d, d_external = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.suffix == ".md":
                file_external = d_external
                # A `.md` symlink whose realpath lands in external scope
                # (escapes the tree, under shared/, or inside a foreign
                # submodule) is external — kept in step with the classifier
                # `external_reason_for` so this walker and audit/list agree.
                if entry.is_symlink() and external_reason_for(entry, wiki_root) is not None:
                    if not include_external:
                        continue
                    file_external = True
                yield ConsumerFile(
                    path=entry,
                    real_path=_safe_resolve(entry),
                    external=file_external,
                )
                continue
            if not entry.is_dir():
                continue
            if is_reserved_segment(entry.name):
                continue
            # Stop at child-space boundaries — the contract walk owns them.
            if (entry / "index.md").is_file():
                continue
            ancestor_external = (
                classify_external_scope(entry, wiki_root).scope
                == TrustScope.EXTERNAL
            )
            entry_external = d_external or ancestor_external
            if entry.is_symlink():
                try:
                    entry.resolve().relative_to(root_real)
                except (OSError, ValueError, RuntimeError):
                    entry_external = True
            if entry_external and not include_external:
                continue
            er = _safe_resolve(entry)
            if er is None:
                continue
            if er in seen:
                continue
            seen.add(er)
            stack.append((entry, entry_external))


def discover_md_files(
    wiki_root: Path,
    *,
    include_external: bool = False,
) -> list[ConsumerFile]:
    """`.md` files reachable via the navigation contract.

    Walks each consumer space (via `discover_consumer_spaces`) and descends
    PLAIN folders inside it via `descend_plain_md_files`, stopping at child-
    space boundaries. Output order follows the consumer-space order, files
    within each space in sorted directory order.
    """
    out: list[ConsumerFile] = []
    for cs in discover_consumer_spaces(wiki_root, include_external=include_external):
        out.extend(
            descend_plain_md_files(
                cs.path, cs.external, wiki_root, include_external=include_external
            )
        )
    return out


def discover_owned_md_files(
    wiki_root: Path,
    *,
    include_external: bool = False,
) -> list[Path]:
    """Every `.md` file inside OWNED scope (filesystem walk, not contract).

    Walks directory-by-directory so external mounts at any depth are pruned
    (a foreign submodule at `nested/vendor/` is skipped though `nested/`
    is owned). Hidden directories, `_archives/`, `_meta/`, and stray
    `wiki-spaces-promote-*` snapshot dirs are excluded. `.md` symlinks whose
    target escapes the tree are skipped by default. Realpath cycle guard.

    With `include_external=True`, external subtrees are descended and their
    files included (the `audit --include-external` scope). Backs `space
    audit`. Order is filesystem order — every audit consumer sorts or
    set-reduces downstream.
    """
    out: list[Path] = []
    try:
        root_real = wiki_root.resolve()
    except OSError:
        return out
    visited: set[Path] = {root_real}
    stack: list[Path] = [wiki_root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if entry.is_file() and entry.suffix == ".md":
                # A `.md` symlink whose realpath lands in external scope
                # (escapes the tree, under shared/, or inside a foreign
                # submodule) is external — kept in step with the classifier
                # `external_reason_for` so this owned-scope walk and audit agree.
                if entry.is_symlink() and external_reason_for(entry, wiki_root) is not None:
                    if not include_external:
                        continue
                out.append(entry)
                continue
            if not entry.is_dir():
                continue
            if is_reserved_segment(name):
                continue
            if name.startswith("wiki-spaces-promote-"):
                continue
            if (
                external_reason_for(entry, wiki_root) is not None
                and not include_external
            ):
                continue
            er = _safe_resolve(entry)
            if er is None:
                continue
            if er in visited:
                continue
            visited.add(er)
            stack.append(entry)
    return out


def external_boundaries(
    nodes: list[NodeFacts],
    consumer_spaces: list[ConsumerSpace],
) -> list[ExternalBoundary]:
    """Externally-classified folders that are NOT consumer-visible spaces.

    The set difference between every external node (from
    `discover_nodes(include_external=True)`) and the consumer spaces (from
    `discover_consumer_spaces(include_external=True)`): what remains are the
    external boundaries lacking `index.md` + `## Spaces`. Preserves node
    order. Replaces the `_walk_classified` second walk in `space list
    --include-boundaries`.
    """
    consumer_real = {
        cs.real_path for cs in consumer_spaces if cs.real_path is not None
    }
    out: list[ExternalBoundary] = []
    for n in nodes:
        if n.trust.scope != TrustScope.EXTERNAL:
            continue
        if n.real_path is not None and n.real_path in consumer_real:
            continue
        out.append(ExternalBoundary(
            path=n.path, real_path=n.real_path, reason=n.trust.reason,
        ))
    return out


def drift_from_nodes(
    nodes: list[NodeFacts],
    *,
    include_external: bool = False,
) -> list[SpaceDrift]:
    """`## Spaces` drift per ancestor space, from already-discovered nodes.

    Reproduces the read-only audit's expected/listed/stale semantics:

    - A *space node* is one with `index.md` (owned, or external when
      `include_external`) — this INCLUDES bare `index.md` folders, which is
      why a registered-or-not bare child still surfaces as a parent's MISSING
      entry (the requirement the contract-only missing helper would miss).
    - `expected[parent]` = each space node's path relative to its nearest
      ancestor space.
    - `listed` = lenient href dirs in the space's `## Spaces` (so a reserved
      href surfaces as a stale entry, matching current audit output).
    - A space lacking `## Spaces` contributes no listed/stale of its own (its
      missing-section is a separate finding) but its children still appear in
      its parent's `expected`.

    `stale` targets are checked on disk (`<space>/<dir>/index.md`).
    """
    spaces = [n for n in nodes if is_space_node(n, include_external)]
    expected: dict[Path, set[str]] = {n.path: set() for n in spaces}
    for n in spaces:
        if n.nearest_ancestor_space is None:
            continue  # the wiki root has no registering ancestor
        parent = n.nearest_ancestor_space
        if parent not in expected:
            continue
        try:
            rel = n.path.relative_to(parent).as_posix()
        except ValueError:
            rel = n.path.name
        expected[parent].add(rel)

    out: list[SpaceDrift] = []
    for n in spaces:
        space = n.path
        try:
            text = (space / "index.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _md.has_section(text, "Spaces"):
            continue
        listed = {
            href_to_dir(e.href)
            for e in _md.parse_section_entries(text, "Spaces")
            if e.href
        }
        missing = sorted(expected[space] - listed)
        stale = sorted(
            d for d in listed if not (space / d / "index.md").is_file()
        )
        if missing or stale:
            out.append(SpaceDrift(space=space, missing=missing, stale=stale))
    return out


def is_space_node(n: NodeFacts, include_external: bool) -> bool:
    """True when a node is an audit 'space' — has `index.md` and is in scope.

    Owned `index.md` folders always, external ones only with
    `include_external`. Bare-index folders qualify (has_index regardless of
    has_spaces_section)."""
    if not n.has_index:
        return False
    if n.trust.scope == TrustScope.EXTERNAL and not include_external:
        return False
    return True


def _safe_resolve(p: Path) -> Path | None:
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return None


# ====================================================================
# Page index + wikilink resolution
# ====================================================================


def build_page_index(files: Iterable[Path]) -> PageIndex:
    """Build the unified candidates+alias index from a set of page paths.

    Reads each file's frontmatter once to extract aliases. Aliases are
    indexed casefold (matches Obsidian's lookup semantics). Duplicate
    aliases are surfaced as a separate map — the resolver returns
    `AMBIGUOUS_ALIAS` for them rather than silently picking.

    Files whose frontmatter is malformed are recorded in
    `frontmatter_errors`; their aliases are NOT indexed (we cannot trust
    the parse). The audit reports these as findings.
    """
    pages: set[Path] = set()
    by_basename: dict[str, list[Path]] = {}
    alias_sets: dict[str, set[Path]] = {}
    frontmatter_errors: dict[Path, FrontmatterResult] = {}
    for path in files:
        pages.add(path)
        by_basename.setdefault(path.name, []).append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        result = parse_frontmatter_result(text)
        if result.status in (FrontmatterStatus.MALFORMED, FrontmatterStatus.NON_MAPPING):
            frontmatter_errors[path] = result
            continue
        if result.status != FrontmatterStatus.OK or result.data is None:
            continue
        aliases = result.data.get("aliases")
        if aliases is None:
            continue
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            if not isinstance(alias, str) or not alias:
                continue
            key = alias.casefold()
            alias_sets.setdefault(key, set()).add(path)
    by_alias_raw = {k: sorted(v) for k, v in alias_sets.items()}
    duplicate_aliases = {k: v for k, v in by_alias_raw.items() if len(v) > 1}
    return PageIndex(
        pages=pages,
        by_basename=by_basename,
        by_alias=by_alias_raw,
        duplicate_aliases=duplicate_aliases,
        frontmatter_errors=frontmatter_errors,
    )


def _path_distance(a: Path, b: Path) -> int:
    common = 0
    for x, y in zip(a.parts, b.parts):
        if x != y:
            break
        common += 1
    return (len(a.parts) - common) + (len(b.parts) - common)


def resolve_wikilink(
    target: str,
    base: Path,
    page_index: PageIndex,
    *,
    wiki_root: Path,
) -> WikilinkResolution:
    """Resolve a `[[target]]` wikilink with full attempt trace.

    Strategies, in order:
    1. wiki-root pathful: `(wiki_root / target).resolve()` against `page_index.pages`.
    2. base-relative pathful: `(base / target).resolve()` against `page_index.pages`.
    3. basename: match by `Path.name`; tie-break by closest-to-base distance,
       then sorted path.
    4. alias: lookup in `page_index.by_alias` (casefold). Duplicate aliases
       yield `AMBIGUOUS_ALIAS`, never last-writer-wins.

    Pathful strategies only run when `target` contains `/`. Bare targets
    run basename then alias.
    """
    attempts: list[ResolutionAttempt] = []
    name_with_md = target if target.endswith(".md") else f"{target}.md"
    name_as_given = target

    if "/" in target:
        for n in (name_with_md, name_as_given):
            cand = (wiki_root / n).resolve()
            if cand in page_index.pages:
                attempts.append(ResolutionAttempt(
                    strategy="wiki_root_pathful",
                    candidate=cand,
                    outcome="matched",
                ))
                return WikilinkResolution(
                    status=WikilinkStatus.RESOLVED,
                    target=cand,
                    candidates=[],
                    attempts=attempts,
                    reason=None,
                )
        attempts.append(ResolutionAttempt(
            strategy="wiki_root_pathful",
            candidate=None,
            outcome="no_match",
        ))
        for n in (name_with_md, name_as_given):
            cand = (base / n).resolve()
            if cand in page_index.pages:
                attempts.append(ResolutionAttempt(
                    strategy="base_relative",
                    candidate=cand,
                    outcome="matched",
                ))
                return WikilinkResolution(
                    status=WikilinkStatus.RESOLVED,
                    target=cand,
                    candidates=[],
                    attempts=attempts,
                    reason=None,
                )
        attempts.append(ResolutionAttempt(
            strategy="base_relative",
            candidate=None,
            outcome="no_match",
        ))
        return WikilinkResolution(
            status=WikilinkStatus.UNRESOLVED,
            target=None,
            candidates=[],
            attempts=attempts,
            reason="no path-resolved page; pathful targets do not fall back to basename/alias",
        )

    # Bare target: basename then alias.
    basename_matches = page_index.by_basename.get(name_with_md, [])
    if basename_matches:
        try:
            base_resolved = base.resolve()
        except OSError:
            base_resolved = base
        chosen = min(
            basename_matches,
            key=lambda c: (_path_distance(base_resolved, c.parent), str(c)),
        )
        attempts.append(ResolutionAttempt(
            strategy="basename",
            candidate=chosen,
            outcome="matched",
        ))
        return WikilinkResolution(
            status=WikilinkStatus.RESOLVED,
            target=chosen,
            candidates=[],
            attempts=attempts,
            reason=None,
        )
    attempts.append(ResolutionAttempt(
        strategy="basename",
        candidate=None,
        outcome="no_match",
    ))
    key = target.casefold()
    alias_matches = page_index.by_alias.get(key, [])
    if len(alias_matches) > 1:
        attempts.append(ResolutionAttempt(
            strategy="alias",
            candidate=None,
            outcome="ambiguous",
        ))
        return WikilinkResolution(
            status=WikilinkStatus.AMBIGUOUS_ALIAS,
            target=None,
            candidates=sorted(alias_matches),
            attempts=attempts,
            reason=f"alias {target!r} claimed by {len(alias_matches)} pages",
        )
    if alias_matches:
        attempts.append(ResolutionAttempt(
            strategy="alias",
            candidate=alias_matches[0],
            outcome="matched",
        ))
        return WikilinkResolution(
            status=WikilinkStatus.RESOLVED,
            target=alias_matches[0],
            candidates=[],
            attempts=attempts,
            reason=None,
        )
    attempts.append(ResolutionAttempt(
        strategy="alias",
        candidate=None,
        outcome="no_match",
    ))
    return WikilinkResolution(
        status=WikilinkStatus.UNRESOLVED,
        target=None,
        candidates=[],
        attempts=attempts,
        reason="no basename match and no alias claims this target",
    )


# ====================================================================
# Caps + sizes
# ====================================================================


_BUILTIN_LIMITS: list[tuple[str, int]] = [
    ("index.md", 5000),
    ("log.md", 100_000),
    ("log.archive-*.md", 100_000),
    ("hot.md", 100_000),
    ("*.md", 15_000),
]


def load_limit_table(wiki_root: Path) -> LimitTable:
    """Load user overrides from `_meta/limits.md`, then built-in defaults.

    User rules come first (so they win on match-in-order). Each rule
    carries its `CapSource` — built-in (no file/line) or user-override
    (with the source line in `limits.md`).
    """
    rules: list[tuple[str, int, CapSource]] = []
    malformed: list[tuple[int, str]] = []
    user_path = wiki_root / "_meta" / "limits.md"
    if user_path.is_file():
        try:
            text = user_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            # An EXISTING-but-unreadable limits.md (bad permissions, or non-UTF-8
            # bytes in the Obsidian wire format -> UnicodeDecodeError, which is a
            # ValueError not an OSError and previously escaped as a crash) must
            # not be swallowed as "no overrides" (HANDBOOK: handle failures at
            # boundaries). Caps fall back to built-ins, but record the failure as
            # a malformed row so `space caps` and `space audit` both surface it
            # and exit non-zero rather than silently shipping the wrong caps.
            text = ""
            malformed.append((0, f"could not read {user_path.name}: {e}"))
        for i, raw in enumerate(text.splitlines()):
            line = raw.strip()
            if not line.startswith("|") or set(line) <= set("|-: "):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            pattern, raw_cap = cells[0], cells[1]
            if pattern.lower() in ("pattern", "patterns"):
                continue
            try:
                cap = int(raw_cap.replace(",", "").replace("_", "").strip())
            except ValueError:
                malformed.append((i, raw))
                continue
            if cap <= 0:
                malformed.append((i, raw))
                continue
            rules.append((pattern, cap, CapSource(
                kind=CapSourceKind.USER_OVERRIDE,
                pattern=pattern,
                file=user_path,
                line=i,
            )))
    # Drop built-in defaults whose pattern the user already redeclared:
    # otherwise the listing carries dead rules (first-match-wins means the
    # user rule fires; the shadowed built-in never runs) and JSON consumers
    # of `space caps` see ghost entries that don't match write enforcement.
    user_patterns = {p for p, _, src in rules if src.kind == CapSourceKind.USER_OVERRIDE}
    for pattern, cap in _BUILTIN_LIMITS:
        if pattern in user_patterns:
            continue
        rules.append((pattern, cap, CapSource(
            kind=CapSourceKind.BUILTIN_DEFAULT,
            pattern=pattern,
            file=None,
            line=None,
        )))
    return LimitTable(rules=rules, malformed_rows=malformed)


def _path_glob_match(rel: str, pattern: str) -> bool:
    return _match_path_segs(rel.split("/"), pattern.split("/"))


def _match_path_segs(rel: list[str], pat: list[str]) -> bool:
    if not pat:
        return not rel
    head = pat[0]
    if head == "**":
        if _match_path_segs(rel, pat[1:]):
            return True
        if not rel:
            return False
        return _match_path_segs(rel[1:], pat)
    if not rel:
        return False
    if not fnmatch.fnmatchcase(rel[0], head):
        return False
    return _match_path_segs(rel[1:], pat[1:])


def cap_for_path(path: Path, wiki_root: Path, table: LimitTable | None = None) -> CapVerdict:
    """Resolve the cap for `path` against `table`, returning value + source.

    These are the canonical match semantics:
    - patterns containing `/` match the wiki-root-relative posix path
    - patterns without `/` match the basename only
    - first match wins; user overrides precede defaults
    """
    if table is None:
        table = load_limit_table(wiki_root)
    try:
        rel = path.relative_to(wiki_root).as_posix()
    except ValueError:
        rel = path.as_posix()
    name = path.name
    for pattern, cap, source in table.rules:
        if "/" in pattern:
            if _path_glob_match(rel, pattern):
                return CapVerdict(cap=cap, source=source)
        else:
            # `fnmatchcase` (not `fnmatch`) so basename matching is
            # case-SENSITIVE and platform-independent — matching the
            # path-pattern branch (`_match_path_segs` → `fnmatchcase`). Plain
            # `fnmatch` folds case via `os.path.normcase`, so the same cap rule
            # would resolve differently on a case-insensitive FS (macOS) than
            # on Linux.
            if fnmatch.fnmatchcase(name, pattern):
                return CapVerdict(cap=cap, source=source)
    # Defensive: the built-in `*.md` rule guarantees a match for `.md`
    # files. Non-markdown paths get a very-large cap (effectively
    # unbounded) so the framework writers don't reject non-content writes.
    return CapVerdict(
        cap=2**31,
        source=CapSource(
            kind=CapSourceKind.BUILTIN_DEFAULT,
            pattern="<unbounded>",
            file=None,
            line=None,
        ),
    )


def check_size(
    path: Path,
    projected_text: str,
    wiki_root: Path,
    table: LimitTable | None = None,
) -> SizeVerdict:
    """Produce a `SizeVerdict` for a projected write to `path`.

    Frontmatter is excluded from the char count (metadata, not content).
    A projected write strictly smaller than the current on-disk body is
    the shrinking-write escape hatch — `OK_SHRINKING` even when over
    the cap. Otherwise `OK` when under cap, `OVER` when above.
    """
    cap = cap_for_path(path, wiki_root, table)
    chars_projected = len(_md.strip_frontmatter(projected_text))
    try:
        current_text = path.read_text(encoding="utf-8")
        chars_current = len(_md.strip_frontmatter(current_text))
    except (OSError, UnicodeDecodeError):
        chars_current = 0
    if chars_projected <= cap.cap:
        return SizeVerdict(
            outcome=SizeOutcome.OK,
            chars_projected=chars_projected,
            chars_current=chars_current,
            cap=cap,
        )
    if chars_current > 0 and chars_projected < chars_current:
        return SizeVerdict(
            outcome=SizeOutcome.OK_SHRINKING,
            chars_projected=chars_projected,
            chars_current=chars_current,
            cap=cap,
        )
    return SizeVerdict(
        outcome=SizeOutcome.OVER,
        chars_projected=chars_projected,
        chars_current=chars_current,
        cap=cap,
    )
