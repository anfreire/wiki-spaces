#!/usr/bin/env python3
"""ws.py — the wiki-spaces helper bundled with each reference skill.

A wiki is a folder whose `index.md` contains a `## Spaces` heading.
This script hands a skill deterministic facts about one. It parses the
contract, never the content: structure — traversal, trust scope, caps,
drift — is the tool's; meaning — what a page says, links, tags — is
the caller's to read and judge, with `grep` as the sweep that feeds
that judgment.

  list        spaces reachable via the `## Spaces` contract, each with
              its entry description — the placement hints
  files       markdown files reachable via the contract
  grep        regex line search over the files the contract reaches
              (-F takes the pattern as a literal string)
  check-size  cap verdict for one file, before or after writing
  audit       detect contract drift (including an entry that lists
              through another space's boundary), over-cap and
              unreadable files, and registered mounts that stopped
              being wikis; findings name their repair wherever one is
              safe to name — a `missing entry` prints the exact line
              to add; author-intent findings name the problem — and
              applying it is the caller's edit, never this script's

Stdout is data. Stderr carries the resolved root (the audit prints it
as its stdout header instead) and `note:` advisories naming what a walk
skipped (external paths, unreachable spaces, stale entries, unreadable
files, unlistable directories), the enclosing wiki when the resolved
root is nested inside one, and the configured wiki when the resolved
root is not it — silence speaks for the walk's reach, never the whole
disk.
Stdlib-only python3 (3.9+), zero dependencies, read-only: no command
writes to a wiki. Exit codes: 0 clean, 1 findings (for grep, no match),
2 cannot operate.

The copies bundled with ws-search, ws-update, and ws-tend must stay
byte-identical; the wiki-spaces repo pins that with a test.
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, NoReturn
from urllib.parse import unquote

# Caps are UTF-8 bytes on disk, frontmatter included, keyed by basename.
DEFAULT_CAPS = {"index.md": 5000, "log.md": 100000, "hot.md": 100000}
DEFAULT_MD_CAP = 15000
RESERVED_NAMES = {"_archives", "_meta"}
# A folder's own space: a dot-folder at its root, resolved as the wiki
# from anywhere inside the folder. Dot-prefixed, so no walk enters it as
# a child — it is reached as a root, or through a mount.
OWN_SPACE = ".wiki-spaces"
# An entry's href is percent-encoded, so any folder name survives the
# entry grammar: encode what would break or blur the `[label](href)`
# shape (`%` itself first, so encoding round-trips through the
# percent-decode every read applies). The grammar lives on one physical
# line, so every character Python's splitlines treats as a line
# boundary rides encoded too (UTF-8 bytes, the decode unquote applies).
LINE_BREAKS = {"\n": "%0A", "\r": "%0D", "\v": "%0B", "\f": "%0C",
               "\x1c": "%1C", "\x1d": "%1D", "\x1e": "%1E",
               "\x85": "%C2%85", "\u2028": "%E2%80%A8",
               "\u2029": "%E2%80%A9"}
HREF_ENCODE = {"%": "%25", " ": "%20", "#": "%23", "<": "%3C", ">": "%3E",
               "(": "%28", ")": "%29", "[": "%5B", "]": "%5D",
               "{": "%7B", "}": "%7D", '"': "%22", **LINE_BREAKS}

# A traversal node: (root-relative posix path, filesystem path, external?).
Node = tuple[str, Path, bool]

# An entry rides any markdown bullet marker; skills write the canonical
# `-`. A trailing quoted link title is tolerated and ignored. The
# description separator admits em dash, en dash, and hyphen — the three
# are near-indistinguishable on screen and writers emit all of them.
ENTRY_RE = re.compile(
    r'^\s*[-*+]\s+\[([^\]]+)\]\(\s*([^)]+?)(?:\s+"[^"]*")?\s*\)'
    r"(?:\s*[—–\-]+\s*(.*))?$")
BULLET_RE = re.compile(r"^\s*[-*+]\s+\S")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
LIMIT_LINE_RE = re.compile(r"^\s*([^:#|\s][^:]*?)\s*:\s*(\d+)\s*$")
# A heading rides 0–3 leading spaces in the dialect (4 is a code block)
# — the contract, its near-misses, and the section closer all read with
# the same tolerance, so a file that visibly renders the heading can
# never read as bare.
CONTRACT_HEAD_RE = re.compile(r"^ {0,3}## Spaces\s*$")
SECTION_END_RE = re.compile(r" {0,3}##? ")
NEAR_MISS_RE = re.compile(r"^ {0,3}##\s*spaces\s*#*\s*$", re.IGNORECASE)


def read_text(path: Path) -> str | None:
    """utf-8-sig: a BOM is byte-order metadata, not content — decoding
    drops it so no reader mistakes it for the first character. Byte
    counts (caps) come from the filesystem and keep it."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _ident(path: Path) -> tuple[int, int] | None:
    """Filesystem identity for cycle and alias dedupe: one stat instead of
    a per-component resolve, so deep trees stay linear."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino


def _rel_parts(path: Path, root: Path) -> tuple[str, ...] | None:
    """`path` relative to `root` as name parts, None when outside. A
    single string pass — pathlib's relative_to materializes every
    ancestor and goes quadratic on deep trees."""
    p, r = os.fspath(path), os.fspath(root).rstrip("/")
    if p == r:
        return ()
    if not p.startswith(r + "/"):
        return None
    return tuple(p[len(r) + 1:].split("/"))


# ---------- markdown primitives ----------

def fenced_mask(lines: list[str]) -> list[bool]:
    """True per line inside a fenced code block, fences included. A fence
    closes only on the same character and at least the opener's length."""
    mask = [False] * len(lines)
    fence = None
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                mask[i] = True
        else:
            mask[i] = True
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return mask


def frontmatter_end(lines: list[str]) -> int:
    """Lines the YAML frontmatter block occupies (fences included), 0 when
    there is none. Frontmatter is metadata, never contract or content —
    every reader of a document body starts after it."""
    if not lines or lines[0].strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return i + 1
    return 0


def find_section(
    lines: list[str], heading: str = "Spaces",
) -> tuple[int, int, int] | None:
    """`(heading_line, body_start, body_end)` for a real (non-fenced,
    post-frontmatter) `## <heading>`, 0–3 leading spaces tolerated the
    way the dialect renders headings. body_end exclusive: the next `#`
    or `##` heading — the dialect ends a section there, while a deeper
    `###` grouping stays inside — or EOF. None if absent. Indices are
    into `lines` as given."""
    rx = re.compile(r"^ {0,3}" + re.escape("## " + heading) + r"\s*$")
    fenced = fenced_mask(lines)
    fm = frontmatter_end(lines)
    head = None
    for i, raw in enumerate(lines):
        if i >= fm and not fenced[i] and rx.match(raw):
            head = i
            break
    if head is None:
        return None
    end = len(lines)
    for i in range(head + 1, len(lines)):
        if not fenced[i] and SECTION_END_RE.match(lines[i]):
            end = i
            break
    return head, head + 1, end


def has_spaces(text: str | None) -> bool:
    return text is not None and find_section(text.splitlines()) is not None


def _heading_near_miss(text: str) -> str | None:
    """A near-miss of the `## Spaces` heading (`## spaces`, `## Spaces ##`,
    `##Spaces`) when the exact heading is absent — the author almost
    certainly meant the contract, so repairs defer to a rename instead of
    inserting a second heading next to it."""
    lines = text.splitlines()
    fenced = fenced_mask(lines)
    fm = frontmatter_end(lines)
    for i, raw in enumerate(lines):
        s = raw.rstrip()
        if (i >= fm and not fenced[i] and not CONTRACT_HEAD_RE.match(s)
                and NEAR_MISS_RE.match(s)):
            return s
    return None


def extra_spaces_headings(text: str) -> int:
    """`## Spaces` headings beyond the first (non-fenced, post-frontmatter).
    The contract reads only the first; later blocks are dead weight worth
    naming."""
    lines = text.splitlines()
    fenced = fenced_mask(lines)
    fm = frontmatter_end(lines)
    n = sum(1 for i, raw in enumerate(lines)
            if i >= fm and not fenced[i] and CONTRACT_HEAD_RE.match(raw))
    return max(0, n - 1)


def parse_spaces(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Bullets under `## Spaces`: `(entries, malformed)`. An entry is
    `- [label](href)` with an optional `— description` tail, under any
    markdown bullet marker (`-`, `*`, `+`); any other bullet-shaped line
    is malformed — the contract has exactly one entry shape. Entries are
    `(label, href, description)`, description `""` when absent."""
    bounds = find_section(text.splitlines())
    if bounds is None:
        return [], []
    _, start, end = bounds
    lines = text.splitlines()[start:end]
    entries, malformed = [], []
    for raw in lines:
        line = raw.rstrip()
        m = ENTRY_RE.match(line)
        if m:
            entries.append((m.group(1), m.group(2), (m.group(3) or "").strip()))
        elif BULLET_RE.match(line):
            malformed.append(line.strip())
    return entries, malformed


def normalize_href(href: str) -> str | None:
    """`## Spaces` href -> child directory, or None when unregistrable:
    empty, absolute, escaping (`..`), carrying a colon or a raw `#`, or
    a reserved segment. A colon marks a URI (`https://…`, `mailto:`),
    and a raw `#` a fragment or anchor — never a wiki-relative name.
    Obsidian forbids both characters in names, and a name's `#` rides
    percent-encoded (`%23`), so no real folder loses its entry. The
    href is percent-encoded (Obsidian writes `my%20space/index.md`),
    so it is decoded after the fragment check and every other check
    runs on the decoded name — the one the filesystem knows. Trivial
    equivalents (`./x`, `a//b`, a trailing slash) normalize instead of
    failing."""
    h = href.strip()
    if "#" in h:
        return None
    h = unquote(h)
    if not h or h.startswith("/") or ":" in h:
        return None
    h = posixpath.normpath(h)
    if h.endswith("/index.md"):
        h = h[: -len("/index.md")]
    if h in ("", "."):
        return None
    parts = h.split("/")
    if any(p == ".." or is_reserved(p) for p in parts):
        return None
    return h


def encode_href(name: str) -> str:
    """A directory name as a contract-entry destination — the inverse of
    the decode every href read applies. Any name survives: what would
    break the entry grammar rides percent-encoded, so the audit's
    suggested entries parse back to the disk name."""
    return "".join(HREF_ENCODE.get(c, c) for c in name)


def is_reserved(name: str) -> bool:
    return name.startswith(".") or name in RESERVED_NAMES


# ---------- discovery ----------

def is_wiki(path: Path) -> bool:
    return has_spaces(read_text(path / "index.md"))


def config_wiki() -> tuple[Path | None, str | None]:
    """The configured canonical wiki: `(path, None)` when the `wiki` key
    resolves, `(None, why-it-was-ignored)` when a key exists but is
    unusable, `(None, None)` when no config carries the key."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    text = read_text(Path(base) / "wiki-spaces" / "config")
    if text is None:
        return None, None
    reason = None
    for line in text.splitlines():
        m = re.match(r"^\s*wiki\s*=\s*(.+?)\s*$", line)
        if not m:
            continue
        p = Path(os.path.expanduser(m.group(1)))
        if p.is_absolute() and is_wiki(p):
            return p, None
        if reason is None:
            why = "not absolute" if not p.is_absolute() else _unwiki_reason(p)
            reason = f"config `wiki` ignored: {p} ({why})"
    return None, reason


def _unwiki_reason(p: Path) -> str:
    """Why `p` is not a wiki, for an advisory: nothing on disk (a link
    gone stale, say), or no index.md with the heading — naming a
    near-miss heading, since the author almost certainly meant the
    contract."""
    if not p.is_dir():
        return "a broken link" if p.is_symlink() else "missing on disk"
    text = read_text(p / "index.md")
    miss = _heading_near_miss(text) if text is not None else None
    return ("no index.md with a ## Spaces heading"
            + (f'; it carries "{miss}" — rename it' if miss else ""))


def _wiki_at(p: Path) -> Path | None:
    """The wiki a folder answers as: its own contract, else the space it
    carries (OWN_SPACE, returned resolved: a link to a space in another
    wiki names that space, so the root and its advisories read the same
    from every entry), else None. A dot-folder on disk that is not a
    wiki is noted, never passed over in silence — the folder meant to
    keep a space, and the next candidate would take its place
    unannounced."""
    if is_wiki(p):
        return p
    own = p / OWN_SPACE
    if is_wiki(own):
        return safe_resolve(own) or own
    if own.is_symlink() or own.exists():
        note(f"{own} is not a wiki ({_unwiki_reason(own)}) — skipped")
    return None


def resolve_root(explicit: str | None) -> Path:
    """Explicit path, else the nearest CWD ancestor, else the `wiki` key
    in the user config — every candidate answering as `_wiki_at` says.
    Exits 2 when nothing resolves — naming the configured path it had to
    ignore, so a broken config never fails silently."""
    if explicit:
        p = Path(os.path.abspath(os.path.expanduser(explicit)))
        found = _wiki_at(p)
        if found is None:
            die(f"not a wiki: {p} ({_unwiki_reason(p)})")
        return found
    cwd = Path(os.getcwd())
    for p in (cwd, *cwd.parents):
        found = _wiki_at(p)
        if found is not None:
            return found
    found, reason = config_wiki()
    if found is not None:
        return found
    die("no wiki found: pass --wiki, run inside one, or set `wiki` in "
        "the config (~/.config/wiki-spaces/config, or under "
        "$XDG_CONFIG_HOME when set)" + (f"; {reason}" if reason else ""))


def _enclosing_wiki(root: Path) -> Path | None:
    """The nearest wiki ancestor above the resolved root, or None. Trust
    scope never looks above the root, so a fence declared up there — a
    `shared/` segment, a submodule of an enclosing repo — does not hold
    below it: a mount the enclosing walk calls external can read as
    owned from the nested root. Re-rooting on purpose is how a space is
    judged standalone; the advisory names the fact so nobody re-roots
    blind."""
    for p in root.parents:
        if is_wiki(p):
            return p
    return None


def _configured_elsewhere(root: Path) -> Path | None:
    """The configured wiki when the resolved root is not it — a folder's
    own space resolved from CWD, a nested space re-rooted: the advisory
    names the canonical root, so the one the caller stands in is never
    mistaken for it, and what belongs past the root has its address."""
    configured, _reason = config_wiki()
    if configured is None:
        return None
    c, r = safe_resolve(configured), safe_resolve(root)
    if c is None or r is None or c == r:
        return None
    return configured


def die(msg: str) -> NoReturn:
    print(f"ws.py: {msg}", file=sys.stderr)
    raise SystemExit(2)


def note(msg: str) -> None:
    """Advisory to stderr: provenance and skipped-scope hints stay out of
    stdout, which remains pure data."""
    print(f"note: {msg}", file=sys.stderr)


class WalkNotes:
    """What a walk skipped, for the advisory channel — mutable by design:
    the walkers accumulate into it and the command emits once at the end.
    Without it, clean output can silently mean "didn't look everywhere"."""

    def __init__(self) -> None:
        self.contract: set[tuple[int, int]] = set()
        self.unreachable: list[str] = []
        self.stale: list[str] = []
        self.external: list[str] = []
        self.unreadable: list[str] = []
        self.denied: list[str] = []

    def emit(self) -> None:
        if self.unreachable:
            note("unreachable via ## Spaces (unlisted, bare, or listed past "
                 "a space boundary — audit names the repair): "
                 + ", ".join(sorted(set(self.unreachable))))
        if self.stale:
            note("stale entries, skipped (no space on disk — the audit "
                 "is the repair surface): " + ", ".join(sorted(set(self.stale))))
        if self.external:
            note("external, skipped (--external to include): "
                 + ", ".join(sorted(set(self.external))))
        if self.unreadable:
            note("unreadable (not UTF-8), skipped: "
                 + ", ".join(sorted(self.unreadable)))
        if self.denied:
            note("unlistable directories, skipped: "
                 + ", ".join(sorted(set(self.denied))))


# ---------- trust scope ----------

def _config_value(raw: str) -> str:
    """A git-config value with optional surrounding double quotes removed.
    Escape sequences inside are left as-is — pathological names lose to
    simplicity here."""
    v = raw.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


@lru_cache(maxsize=None)
def _submodule_paths(repo: Path) -> frozenset[str]:
    """Submodule paths (repo-relative posix) declared by `repo`'s
    .gitmodules. A submodule names another repository by definition —
    its content answers to that repository, not to this wiki — so every
    declared path classifies external. An owned mount of your own
    second repo is a clone, not a submodule."""
    text = read_text(repo / ".gitmodules")
    if text is None:
        return frozenset()
    return frozenset(
        _config_value(m.group(1))
        for m in re.finditer(r"^\s*path\s*=\s*(.+)$", text, re.MULTILINE))


@lru_cache(maxsize=None)
def _is_repo(path: Path) -> bool:
    """A git boundary: a `.git` entry (directory, or the file a submodule
    or worktree carries)."""
    return (path / ".git").exists()


def _link_target(path: Path) -> Path | None:
    """The path a symlink names — one hop, normalized, unresolved: the
    position the author pointed at, whatever sits there now."""
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    return Path(os.path.normpath(os.path.join(os.fspath(path.parent), raw)))


def _symlink_reason(path: Path, root: Path) -> str | None:
    """Why a symlink at `path` leaves trust scope: the position it names
    inside the tree is external — an alias into `shared/` or a
    submodule, judged by the path the link spells, so a `shared/` mount
    that is itself a link still fences — or its realpath lands inside
    the tree at one. A target outside the tree has no position to
    judge, so the link answers to where it sits, like a clone — under
    `shared/` external, elsewhere owned (a folder's own space mounted
    from the wiki). None for an owned alias, an outside target, or a
    link with nothing on disk behind it (the walk names it stale)."""
    target = safe_resolve(path)
    root_real = safe_resolve(root)
    if target is None or root_real is None:
        return None
    named = _link_target(path)
    for candidate, base in ((named, root), (named, root_real),
                            (target, root_real)):
        if candidate is None:
            continue
        rel = _rel_parts(candidate, base)
        if rel is not None and _scope_reason(rel, root):
            return "symlink into external scope"
    return None


# Scope state per (root, parts-prefix): an explicit run-lifetime memo so
# deep trees build each prefix once, iteratively — an lru_cache recursion
# would hit the interpreter limit the walkers avoid, and a per-call scan
# would go quadratic on every node.
_SCOPE_STATE: dict[tuple[Path, tuple[str, ...]],
                   tuple[str | None, Path, str]] = {}


def _scope_state(root: Path,
                 parts: tuple[str, ...]) -> tuple[str | None, Path, str]:
    """`(reason, declaring_repo, rel_inside_repo)` for root/<parts>. The
    reason is sticky down the tree, and every component is judged by the
    three rules in this one resolver: `shared/` marks external at any
    depth (every space is a wiki one level down, so a nested space's
    mounts deserve the same fence); a declared git submodule is external
    — judged against the .gitmodules of the repo that declares it, at
    every git boundary on the path, whichever root resolved; a symlink
    component whose target, as named or as resolved, stays inside the
    tree at an external position marks external from that point down —
    one whose target lies outside the tree answers to where it sits,
    like a clone — so scope, caps, ignores, and advisories cannot
    disagree about a mount."""
    hit = _SCOPE_STATE.get((root, parts))
    if hit is not None:
        return hit
    pending = []
    prefix = parts
    while prefix and (root, prefix) not in _SCOPE_STATE:
        pending.append(prefix)
        prefix = prefix[:-1]
    state = _SCOPE_STATE.get((root, prefix), (None, root, ""))
    for q in reversed(pending):
        # Provisional, the parent's state: a link that names a path
        # through itself asks for this prefix while it is being judged
        # — a loop, which Python 3.13+ resolves "as far as possible"
        # instead of raising — and reads this instead of recursing; the
        # walk names the link stale.
        _SCOPE_STATE[(root, q)] = state
        reason, repo, rel = state
        if reason is None:
            part = q[-1]
            if part == "shared":
                state = ("under shared/", repo, rel)
            else:
                rel = part if not rel else f"{rel}/{part}"
                cur = root.joinpath(*q)
                sym = (_symlink_reason(cur, root) if cur.is_symlink()
                       else None)
                if sym is not None:
                    state = (sym, repo, rel)
                elif rel in _submodule_paths(repo):
                    state = ("git submodule", repo, rel)
                else:
                    state = ((None, cur, "") if _is_repo(cur)
                             else (None, repo, rel))
        _SCOPE_STATE[(root, q)] = state
    return state


def _scope_reason(parts: tuple[str, ...], root: Path) -> str | None:
    return _scope_state(root, parts)[0]


def external_reason(path: Path, root: Path) -> str | None:
    """Why `path` is external relative to the resolved root, or None.
    Judged per path component through the one scope resolver, so a file
    inside an external mount answers external however it is reached."""
    rel = _rel_parts(path, root)
    if rel is None:
        return "outside the wiki tree"
    return _scope_reason(rel, root)


# ---------- traversal (the `## Spaces` contract) ----------

def _boundary_space(space: Path, nd: str) -> str | None:
    """The first intermediate segment of `nd` that is itself a space — an
    entry through it lists what that space owns, so the walk declines the
    href and the audit names it. None when `nd` only groups through plain
    or bare folders. The walk and the audit judge entries through this
    one function: what one declines, the other reports."""
    parts = nd.split("/")
    for k in range(1, len(parts)):
        if is_wiki(space.joinpath(*parts[:k])):
            return "/".join(parts[:k])
    return None


def walk_spaces(
    root: Path, include_external: bool = False,
    notes: WalkNotes | None = None,
    descs: dict[str, str] | None = None,
) -> list[Node]:
    """Spaces reachable via the contract, preorder, entries in file order:
    `(rel, path, external)` with the root first as `.`. Malformed hrefs are
    skipped, external children only enter on request (and stay marked down
    their subtree), realpaths guard against cycles, a child counts only
    when its own index.md carries `## Spaces`, and an href may group
    through plain folders but never through another space — the space on
    the way owns the listing of what lies beneath it. Iterative, so depth
    is bounded by the filesystem, not the interpreter. `notes` collects
    what the walk declined: skipped externals, registered children failing
    the contract, stale or file-naming entries with no space on disk,
    entries crossing a space boundary. `descs` collects each reached
    space's entry description, keyed by rel."""
    root_id = _ident(root)
    if root_id is None:
        return []
    visited = {root_id}
    out: list[Node] = []
    stack: list[tuple[Path, str, bool]] = [(root, ".", False)]
    while stack:
        space, rel, external = stack.pop()
        out.append((rel, space, external))
        text = read_text(space / "index.md")
        if text is None:
            continue
        entries, _ = parse_spaces(text)
        pushes = []
        for _label, href, desc in entries:
            nd = normalize_href(href)
            if nd is None:
                continue
            crel = nd if rel == "." else f"{rel}/{nd}"
            if _boundary_space(space, nd) is not None:
                if notes is not None:
                    notes.unreachable.append(crel + "/")
                continue
            child = space / nd
            child_id = _ident(child)
            if child_id is None:
                if notes is not None:
                    notes.stale.append(crel + "/")
                continue
            ext = external or external_reason(child, root) is not None
            if ext and not include_external:
                if notes is not None:
                    notes.external.append(crel + "/")
                continue
            if child_id in visited:
                continue
            if not is_wiki(child):
                if notes is not None:
                    if (child / "index.md").is_file():
                        notes.unreachable.append(crel + "/")
                    else:
                        notes.stale.append(crel + "/")
                continue
            visited.add(child_id)
            if descs is not None and desc:
                descs[crel] = desc
            pushes.append((child, crel, ext))
        stack.extend(reversed(pushes))
    return out


def _descend(
    d: Path, drel: str, dext: bool, root: Path, include_external: bool,
    seen: set[tuple[int, int]], files: list[Node],
    spaces: list[Node] | None, notes: WalkNotes | None = None,
) -> None:
    """The one directory descent under both views, iterative so depth is
    bounded by the filesystem, not the interpreter. Collects `.md` files
    (realpath-deduped; reserved names are invisible at any depth, files
    and folders alike; `_meta/ignore.md` skips the folders it names —
    folders only, per its contract; external honored).
    With a `spaces` sink it records space-shaped dirs (an index.md, heading
    or not) and keeps descending — the audit's filesystem truth. Without
    one it stops at child-space boundaries — they belong to the contract
    walk. `notes` collects skipped externals, unlistable directories, and
    owned space dirs the contract never reached."""
    stack: list[tuple[Path, str, bool]] = [(d, drel, dext)]
    while stack:
        d, drel, dext = stack.pop()
        if spaces is not None and (d / "index.md").is_file():
            spaces.append((drel, d, dext))
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            if notes is not None:
                notes.denied.append(drel + "/")
            continue
        ignored = ignored_for_dir(d, root)
        # Files first, deduped by realpath with the real file preferred
        # over a symlink alias (CLAUDE.md -> AGENTS.md is one page, under
        # its own name).
        here = {}
        for c in children:
            if not (c.is_file() and c.suffix == ".md") or is_reserved(c.name):
                continue
            ext = dext
            if c.is_symlink() and external_reason(c, root) is not None:
                if not include_external:
                    if notes is not None:
                        notes.external.append(
                            c.name if drel == "." else f"{drel}/{c.name}")
                    continue
                ext = True
            cr = _ident(c)
            if cr is None or cr in seen:
                continue
            kept = here.get(cr)
            if kept is None or (kept[0].is_symlink() and not c.is_symlink()):
                here[cr] = (c, ext)
        for cr, (c, ext) in here.items():
            seen.add(cr)
            files.append((c.name if drel == "." else f"{drel}/{c.name}", c,
                          ext))
        pushes = []
        for c in children:
            if not c.is_dir() or is_reserved(c.name) or c.name in ignored:
                continue
            crel = c.name if drel == "." else f"{drel}/{c.name}"
            ext = dext or external_reason(c, root) is not None
            if spaces is None and (c / "index.md").is_file():
                if notes is not None and not ext:
                    cres = _ident(c)
                    if cres is not None and cres not in notes.contract:
                        notes.unreachable.append(crel + "/")
                continue
            if ext and not include_external:
                if notes is not None:
                    notes.external.append(crel + "/")
                continue
            cres = _ident(c)
            if cres is None or cres in seen:
                continue
            seen.add(cres)
            pushes.append((c, crel, ext))
        stack.extend(reversed(pushes))


def walk_files(
    root: Path, include_external: bool = False,
    notes: WalkNotes | None = None,
) -> list[Node]:
    """Markdown files reachable via the contract: per space, plain-folder
    descent only."""
    files: list[Node] = []
    seen: set[tuple[int, int]] = set()
    reached = walk_spaces(root, include_external, notes)
    if notes is not None:
        notes.contract = {r for r in (_ident(p) for _rel, p, _e in reached)
                          if r is not None}
    for srel, space, sext in reached:
        _descend(space, srel, sext, root, include_external, seen, files, None,
                 notes)
    return files


def walk_owned(
    root: Path, include_external: bool = False,
    notes: WalkNotes | None = None,
) -> tuple[list[Node], list[Node]]:
    """Filesystem walk inside trust scope (the audit's view — it must see
    what the contract misses): `(md_files, space_dirs)`."""
    files, spaces = [], []
    root_id = _ident(root)
    if root_id is None:
        return files, spaces
    _descend(root, ".", False, root, include_external, {root_id}, files,
             spaces, notes)
    return files, spaces


# ---------- caps and ignores (the `_meta/` conventions) ----------

@lru_cache(maxsize=None)
def _limits_overrides(limits: Path) -> dict[str, int] | None:
    """Parsed `basename: bytes` lines of one limits file, None when the
    file is absent or unreadable. The literal name `*.md` re-caps the
    catch-all for content pages — a reserved name, not a glob. Non-positive
    caps and anything else in the file are ignored. Cached for the run —
    callers treat the result as read-only."""
    text = read_text(limits)
    if text is None:
        return None
    overrides: dict[str, int] = {}
    for line in text.splitlines():
        m = LIMIT_LINE_RE.match(line)
        if m and "/" not in m.group(1):
            value = int(m.group(2))
            if value > 0:
                overrides[m.group(1)] = value
    return overrides


@lru_cache(maxsize=None)
def _ignored_names(ignore: Path) -> frozenset[str]:
    """Folder names listed in one `_meta/ignore.md` — plain names, one per
    line, `#` lines are comments; paths and globs are not supported.
    Cached for the run."""
    text = read_text(ignore)
    if text is None:
        return frozenset()
    names = set()
    for line in text.splitlines():
        name = line.strip()
        if (name and not name.startswith("#") and "/" not in name
                and name not in (".", "..")):
            names.add(name)
    return frozenset(names)


# Nearest-`_meta/` results per (dir, root, name): the same explicit-memo
# shape as _SCOPE_STATE, and for the same reason — the walkers visit
# parents first, so every lookup extends a cached ancestor in one step.
_NEAREST_META: dict[tuple[Path, Path, str], Path | None] = {}


def _nearest_meta(start: Path, root: Path, name: str) -> Path | None:
    """The nearest readable `_meta/<name>` at or above `start` within its
    trust domain — closest ancestor wins, the `_template.md` rule. The
    walk stops at the domain top (the resolved root, or the outermost
    external component), so an external space never answers to the host
    wiki's configuration."""
    chain = []
    cur = start
    found: Path | None = None
    while True:
        key = (cur, root, name)
        if key in _NEAREST_META:
            found = _NEAREST_META[key]
            break
        chain.append(cur)
        candidate = cur / "_meta" / name
        if read_text(candidate) is not None:
            found = candidate
            break
        if cur == root or cur == cur.parent:
            break
        rel = _rel_parts(cur, root)
        if rel is None:
            break
        if (rel and _scope_reason(rel, root) is not None
                and _scope_reason(rel[:-1], root) is None):
            break                      # cur is its trust domain's top
        cur = cur.parent
    for c in chain:
        _NEAREST_META[(c, root, name)] = found
    return found


@lru_cache(maxsize=None)
def _caps_for_dir(start: Path, root: Path) -> dict[str, int]:
    caps = dict(DEFAULT_CAPS)
    if _rel_parts(start, root) is None:
        # Outside the wiki tree there is no trust domain to consult:
        # the defaults govern, never the host's overrides.
        return caps
    limits = _nearest_meta(start, root, "limits.md")
    overrides = _limits_overrides(limits) if limits else None
    if overrides:
        caps.update(overrides)
    return caps


def caps_for_path(path: Path, root: Path) -> dict[str, int]:
    """Caps governing `path`: DEFAULT_CAPS overlaid with the nearest
    `_meta/limits.md` at or above its directory — closest ancestor wins,
    the `_template.md` rule. The search never leaves the trust domain:
    an external file answers to its own space's limits (or the defaults),
    never to the host wiki's, and a path outside the root answers to the
    defaults alone. Cached per directory for the run — treat the result
    as read-only."""
    return _caps_for_dir(path.parent, root)


def ignored_for_dir(path: Path, root: Path) -> frozenset[str]:
    """Names the filesystem walk skips inside `path`: the nearest
    `_meta/ignore.md` at or above it, the limits rule. The ignore list
    silences implicit discovery only — the `## Spaces` contract still
    reaches a space it names explicitly."""
    if _rel_parts(path, root) is None:
        return frozenset()
    ignore = _nearest_meta(path, root, "ignore.md")
    return _ignored_names(ignore) if ignore else frozenset()


def cap_for(name: str, caps: dict[str, int]) -> int | None:
    """Exact basename entry, else the `*.md` catch-all, else uncapped."""
    if name in caps:
        return caps[name]
    if name.endswith(".md"):
        return caps.get("*.md", DEFAULT_MD_CAP)
    return None


# ---------- audit ----------

def _ix(rel: str) -> str:
    return "index.md" if rel == "." else f"{rel}/index.md"


class Findings(NamedTuple):
    """Contract findings, one channel per kind. `bare` rows carry
    `(rel, ext, near_miss, registered)`; entry rows carry `(rel, detail,
    ext)`; `crossing` rows carry `(rel, nd, boundary, ext)`; `missing`
    and `unregistrable` rows carry the child's externality; `mounts`
    rows carry `(rel, nd)`."""
    bare: list[tuple[str, bool, str | None, bool]]
    malformed: list[tuple[str, str, bool]]
    duplicate: list[tuple[str, str, bool]]
    crossing: list[tuple[str, str, str, bool]]
    extra_heading: list[tuple[str, bool]]
    missing: list[tuple[str, str, bool]]
    unregistrable: list[tuple[str, str, bool]]
    file_entry: list[tuple[str, str, bool]]
    stale: list[tuple[str, str, bool]]
    mounts: list[tuple[str, str]]

    def count(self) -> int:
        return sum(len(channel) for channel in self)


def space_findings(root: Path, space_dirs: list[Node]) -> Findings:
    """Contract findings per space-shaped dir. A dir whose index.md lacks
    the `## Spaces` heading is not a space — it is reported bare (with any
    near-miss heading it carries, and whether a parent has registered it),
    never treated as contract. Entry findings: malformed and unregistrable
    hrefs, duplicates, entries crossing another space's boundary (the walk
    declines them through the same rule — that space owns the deeper
    listing), dead second headings, entries naming a file rather than a
    space (an `## Items` line in the wrong section, not drift), stale
    entries (no space on disk), and unhealthy mounts (a registered
    external child that is not a wiki — the entry is ours to watch even
    though the interior is not). Drift: an
    unlisted child space attaches to its nearest ancestor that is a real
    space — bare and plain dirs group transparently on the way up — and a
    child whose name cannot survive a contract entry is named as
    unregistrable instead."""
    texts = {path: read_text(path / "index.md")
             for _rel, path, _ext in space_dirs}
    true_spaces = {path for path, text in texts.items() if has_spaces(text)}

    def nearest_ancestor(path: Path) -> Path | None:
        p = path.parent
        while True:
            if p in true_spaces:
                return p
            if p == root or p == p.parent:
                return None
            p = p.parent

    children = {}
    for _rel, path, ext in space_dirs:
        if path == root or path not in true_spaces:
            continue
        anc = nearest_ancestor(path)
        if anc is not None:
            children.setdefault(anc, []).append((path, ext))

    f = Findings([], [], [], [], [], [], [], [], [], [])
    targets: set[Path] = set()
    for rel, path, ext in sorted(space_dirs):
        text = texts[path]
        if text is None or path not in true_spaces:
            continue
        if extra_spaces_headings(text):
            f.extra_heading.append((rel, ext))
        entries, bad = parse_spaces(text)
        for raw in bad:
            f.malformed.append((rel, raw, ext))
        listed = set()
        for _label, href, _desc in entries:
            nd = normalize_href(href)
            if nd is None:
                f.malformed.append((rel, f"unregistrable href: {href.strip()}",
                                    ext))
                continue
            if nd in listed:
                f.duplicate.append((rel, nd, ext))
                continue
            listed.add(nd)
            boundary = _boundary_space(path, nd)
            if boundary is not None:
                f.crossing.append((rel, nd, boundary, ext))
                continue
            child = path / nd
            targets.add(child)
            if child.is_file():
                f.file_entry.append((rel, nd, ext))
            elif not (child / "index.md").is_file():
                f.stale.append((rel, nd, ext))
            elif (not ext and external_reason(child, root) is not None
                  and not is_wiki(child)):
                f.mounts.append((rel, nd))
        for child, child_ext in sorted(children.get(path, ())):
            crel = child.relative_to(path).as_posix()
            if crel in listed:
                continue
            # The same round-trip the suggested entry rides: a child is
            # registrable exactly when its encoded href decodes back.
            if normalize_href(encode_href(crel)) != crel:
                f.unregistrable.append((rel, crel, child_ext))
            else:
                f.missing.append((rel, crel, child_ext))
    for rel, path, ext in sorted(space_dirs):
        text = texts[path]
        if text is None or path in true_spaces:
            continue
        f.bare.append((rel, ext, _heading_near_miss(text), path in targets))
    return f


def _suggest_entry(crel: str) -> str:
    """The exact entry a `missing entry` finding asks for — computed by
    the same encode/normalize round-trip the audit validates names with,
    so pasting it verbatim converges. A label the grammar cannot carry
    raw (a `]`, a line break) rides encoded; hrefs always do."""
    breakers = {"]"} | set(LINE_BREAKS)
    label = crel if not set(crel) & breakers else encode_href(crel)
    return f"- [{label}/]({encode_href(crel)}/index.md)"


def cmd_audit(root: Path, include_external: bool) -> int:
    notes = WalkNotes()
    md, spaces = walk_owned(root, include_external, notes)

    f = space_findings(root, spaces)
    over, unreadable = [], []
    for rel, path, _ext in md:
        if read_text(path) is None:
            unreadable.append(rel)
            continue
        cap = cap_for(posixpath.basename(rel), caps_for_path(path, root))
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if cap is not None and size > cap:
            over.append((rel, size, cap))
    ext_of = {rel: ext for rel, _path, ext in md}

    def mark(rel: str) -> str:
        return " [external]" if ext_of.get(rel) else ""

    def space_mark(ext: bool) -> str:
        return " [external]" if ext else ""

    print(f"wiki: {root}")
    # A bare index.md dir is a finding, not a space — the header counts
    # the dirs that actually carry the contract, the spec's vocabulary.
    true_spaces = sum(1 for _rel, p, _e in spaces if is_wiki(p))
    print(f"spaces: {true_spaces}  pages: {len(md)}")
    for rel, ext, near_miss, registered in f.bare:
        if ext:
            hint = " (external — its owner repairs it)"
        elif near_miss is not None:
            hint = f' (carries "{near_miss}" — rename it to ## Spaces)'
        elif registered:
            hint = " (registered — add the ## Spaces heading to complete it)"
        else:
            hint = (" (not a space until it carries ## Spaces — promote "
                    "it: add the heading and register it; repo furniture "
                    "is silenced via _meta/ignore.md instead)")
        print(f"contract {_ix(rel)}: no ## Spaces heading{hint}")
    for rel, raw, ext in f.malformed:
        print(f"contract {_ix(rel)}: malformed entry: {raw}{space_mark(ext)}")
    for rel, nd, ext in f.duplicate:
        print(f"contract {_ix(rel)}: duplicate entry: {nd}/{space_mark(ext)}")
    for rel, nd, boundary, ext in f.crossing:
        print(f"contract {_ix(rel)}: entry {nd}/ crosses the boundary of "
              f"{boundary}/ — remove it; {boundary}/index.md owns the "
              f"deeper listing{space_mark(ext)}")
    for rel, ext in f.extra_heading:
        print(f"contract {_ix(rel)}: a second ## Spaces heading — the "
              f"contract reads only the first{space_mark(ext)}")
    for rel, crel, ext in f.unregistrable:
        print(f"contract {_ix(rel)}: unregistrable child name: {crel}/ — "
              f"the name does not survive a contract entry; rename it"
              f"{space_mark(ext)}")
    for rel, nd, ext in f.file_entry:
        print(f"contract {_ix(rel)}: entry {nd} names a file, not a space "
              f"— files are reachable by traversal alone{space_mark(ext)}")
    for rel, crel, ext in f.missing:
        if ext:
            hint = " (register mounts by hand) [external]"
        else:
            hint = f" — add: {_suggest_entry(crel)}"
        print(f"drift {_ix(rel)}: missing entry for {crel}/{hint}")
    for rel, nd, ext in f.stale:
        print(f"drift {_ix(rel)}: stale entry {nd}/ (no index.md on disk)"
              f"{space_mark(ext)}")
    for rel, nd in f.mounts:
        crel = nd if rel == "." else f"{rel}/{nd}"
        print(f"mount {crel}/: registered but not a wiki (index.md lacks "
              "## Spaces) — external, its owner repairs it")
    for rel, size, cap in sorted(over):
        print(f"over-cap {rel}: {size} > {cap} bytes{mark(rel)}")
    for rel in sorted(unreadable):
        print(f"unreadable {rel}: not UTF-8 — invisible to search"
              f"{mark(rel)}")
    errors = f.count() + len(over) + len(unreadable)
    print(f"issues: {errors}" if errors else "ok")
    notes.emit()
    return 1 if errors else 0


# ---------- entry points ----------

def cmd_list(root: Path, include_external: bool) -> int:
    notes = WalkNotes()
    descs: dict[str, str] = {}
    for rel, _path, ext in walk_spaces(root, include_external, notes, descs):
        desc = descs.get(rel, "")
        print(rel + (" [external]" if ext else "")
              + (f" — {desc}" if desc else ""))
    notes.emit()
    return 0


def cmd_files(root: Path, include_external: bool) -> int:
    notes = WalkNotes()
    for rel, _path, ext in walk_files(root, include_external, notes):
        print(rel + (" [external]" if ext else ""))
    notes.emit()
    return 0


def cmd_grep(
    root: Path, pattern: str, ignore_case: bool, include_external: bool,
    fixed: bool = False,
) -> int:
    """Line matches as `rel:line: text` over the contract-reachable files —
    the exact set `files` prints, so trust scope and reserved dirs hold.
    Under --external a match inside an external space carries the same
    `[external]` marker `files` prints. With `fixed` the pattern matches
    as a literal string — escaping has one right answer, so the tool
    carries it and a swept name keeps its metacharacters."""
    try:
        rx = re.compile(re.escape(pattern) if fixed else pattern,
                        re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        die(f"bad pattern: {exc}")
    notes = WalkNotes()
    matched = False
    for rel, path, ext in walk_files(root, include_external, notes):
        text = read_text(path)
        if text is None:
            notes.unreadable.append(rel)
            continue
        mark = " [external]" if ext else ""
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matched = True
                print(f"{rel}:{n}: {line.rstrip()}{mark}")
    notes.emit()
    return 0 if matched else 1


def cmd_check_size(root: Path, target: str, use_stdin: bool) -> int:
    p = Path(os.path.expanduser(target))
    if not p.is_absolute():
        p = root / p                    # relative targets are wiki paths,
    path = Path(os.path.abspath(p))     # wherever the caller stands
    name = path.name
    if path.is_dir():
        die(f"target is a directory: {target}")
    if use_stdin:
        size = len(sys.stdin.buffer.read())
    else:
        try:
            size = path.stat().st_size
        except OSError:
            die(f"no such file: {target} (pipe planned content with --stdin)")
    reason = external_reason(path, root)
    if reason is not None:
        note(f"target is external ({reason}) — external spaces are "
             "written only on explicit instruction")
    cap = cap_for(name, caps_for_path(path, root))
    if cap is None:
        print(f"ok {name}: {size} bytes, no cap")
        return 0
    if size > cap:
        current = None
        if use_stdin:
            try:
                current = path.stat().st_size
            except OSError:
                current = None
        if current is not None and size < current:
            print(f"ok {name}: {size} bytes — still over cap {cap} but "
                  f"below the current {current}; a shrinking write is "
                  "progress")
            return 0
        print(f"over {name}: {size} > {cap} bytes — never truncate")
        return 1
    print(f"ok {name}: {size} <= {cap} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ws.py", description="wiki-spaces helper: deterministic traversal "
        "and search, cap verdicts, and a read-only audit for a wiki.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def command(
        name: str, help_text: str, external: bool = False,
    ) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--wiki", metavar="PATH",
                        help="wiki root (default: nearest CWD-ancestor wiki, "
                        "then the config `wiki` key)")
        if external:
            sp.add_argument("--external", action="store_true",
                            help="also cross external spaces (shared/, git "
                            "submodules, symlinks into either)")
        return sp

    command("list", "spaces reachable via the ## Spaces contract, each "
            "with its entry description", external=True)
    command("files", "markdown files reachable via the contract", external=True)
    sp = command("grep", "regex line search over the files the contract "
                 "reaches", external=True)
    sp.add_argument("pattern", help="python regex matched against each line")
    sp.add_argument("-i", "--ignore-case", action="store_true",
                    help="case-insensitive match")
    sp.add_argument("-F", "--fixed-strings", action="store_true",
                    help="match the pattern as a literal string, not a regex")
    sp = command("check-size", "cap verdict for one file")
    sp.add_argument("target", help="file the content is (or will be) written "
                    "to (a relative path resolves from the wiki root)")
    sp.add_argument("--stdin", action="store_true",
                    help="measure stdin instead of the file on disk")
    command("audit", "detect contract drift, over-cap and unreadable files, "
            "and unhealthy mounts; findings name the repair where one is "
            "safe to name", external=True)

    args = parser.parse_args(argv)
    root = resolve_root(args.wiki)
    if args.cmd != "audit":
        print(f"wiki: {root}", file=sys.stderr)
    enclosing = _enclosing_wiki(root)
    if enclosing is not None:
        note(f"root is nested inside a wiki ({enclosing}) — scope is "
             "relative to the resolved root: what the enclosing wiki "
             "fences as external can read as owned here")
    configured = _configured_elsewhere(root)
    if configured is not None:
        note(f"the configured wiki is {configured} — not the resolved root")
    if args.cmd == "list":
        return cmd_list(root, args.external)
    if args.cmd == "files":
        return cmd_files(root, args.external)
    if args.cmd == "grep":
        return cmd_grep(root, args.pattern, args.ignore_case, args.external,
                        args.fixed_strings)
    if args.cmd == "check-size":
        return cmd_check_size(root, args.target, args.stdin)
    return cmd_audit(root, args.external)


if __name__ == "__main__":
    raise SystemExit(main())
