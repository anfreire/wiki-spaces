#!/usr/bin/env python3
"""ws.py — the wiki-spaces helper bundled with each reference skill.

A wiki is a folder whose `index.md` contains a `## Spaces` heading.
This script hands a skill deterministic facts about one:

  list        spaces reachable via the `## Spaces` contract
  files       markdown files reachable via the contract
  grep        regex line search over the files the contract reaches
  check-size  cap verdict for one file, before or after writing
  audit       detect contract drift, broken wikilinks, over-cap files;
              --fix inserts missing `## Spaces` headings and registers
              unlisted owned child spaces — nothing else

Stdlib only, zero dependencies, read-mostly (`audit --fix` is the one
write path). Exit codes: 0 clean, 1 findings (for grep, no match),
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
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote

# Caps are UTF-8 bytes on disk, frontmatter included, keyed by basename.
DEFAULT_CAPS = {"index.md": 5000, "log.md": 100000, "hot.md": 100000}
DEFAULT_MD_CAP = 15000
RESERVED_NAMES = {"_archives", "_meta"}
ORPHAN_EXEMPT = {"index.md", "log.md", "hot.md", "_template.md"}
HREF_METACHARS = "[](){}"

# A traversal node: (root-relative posix path, filesystem path, external?).
Node = tuple[str, Path, bool]

# The description separator admits em dash, en dash, and hyphen — the
# three are near-indistinguishable on screen and writers emit all of them.
ENTRY_RE = re.compile(r"^\s*-\s+\[([^\]]+)\]\(([^)]+)\)(?:\s*[—–\-]+\s*(.*))?$")
BULLET_RE = re.compile(r"^\s*-\s+\S")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
LIMIT_LINE_RE = re.compile(r"^\s*([^:#|\s][^:]*?)\s*:\s*(\d+)\s*$")


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def write_atomic(path: Path, text: str) -> None:
    """Durable atomic write: temp file, fsync, replace, then fsync the
    parent so the rename survives a crash (best-effort where a directory
    cannot be opened, e.g. Windows). The original file's mode is carried
    over — mkstemp's 0600 must not leak onto the replaced file."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ws-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, os.stat(path).st_mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dirfd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dirfd)
    except OSError:
        pass
    finally:
        os.close(dirfd)


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


def find_section(
    lines: list[str], heading: str = "Spaces",
) -> tuple[int, int, int] | None:
    """`(heading_line, body_start, body_end)` for a real (non-fenced)
    `## <heading>`, body_end exclusive (next `## ` or EOF). None if absent."""
    target = "## " + heading
    fenced = fenced_mask(lines)
    head = None
    for i, raw in enumerate(lines):
        if not fenced[i] and raw.rstrip() == target:
            head = i
            break
    if head is None:
        return None
    end = len(lines)
    for i in range(head + 1, len(lines)):
        if not fenced[i] and lines[i].startswith("## "):
            end = i
            break
    return head, head + 1, end


def has_spaces(text: str | None) -> bool:
    return text is not None and find_section(text.splitlines()) is not None


def strip_code(text: str) -> str:
    """Blank fenced blocks and space out inline code so nothing inside a
    code example is read as a link. Line count is preserved."""
    lines = text.splitlines()
    fenced = fenced_mask(lines)
    out = []
    for i, line in enumerate(lines):
        if fenced[i]:
            out.append("")
        else:
            out.append(INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line))
    return "\n".join(out)


def parse_spaces(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Bullets under `## Spaces`: `(entries, malformed)`. An entry is
    `- [label](href)`; any other bullet-shaped line is malformed — the
    contract has exactly one entry shape."""
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
            entries.append((m.group(1), m.group(2)))
        elif BULLET_RE.match(line):
            malformed.append(line.strip())
    return entries, malformed


def normalize_href(href: str) -> str | None:
    """`## Spaces` href -> child directory, or None when unregistrable:
    empty, absolute, `..`, reserved segment, or a markdown metacharacter."""
    h = href.strip()
    if not h or h.startswith("/"):
        return None
    if h.endswith("/index.md"):
        h = h[: -len("/index.md")]
    h = h.rstrip("/")
    if h in ("", ".") or any(c in h for c in HREF_METACHARS):
        return None
    parts = h.split("/")
    if any(p == ".." or is_reserved(p) for p in parts):
        return None
    return h


def is_reserved(name: str) -> bool:
    return name.startswith(".") or name in RESERVED_NAMES


def body_after_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[i + 1:])
    return text


def blank_spaces_section(body: str) -> str:
    """Blank the `## Spaces` body: navigation entries are contract, not
    content, and must not enter the link scan."""
    lines = body.splitlines()
    bounds = find_section(lines)
    if bounds is None:
        return body
    _, start, end = bounds
    for i in range(start, end):
        lines[i] = ""
    return "\n".join(lines)


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
            why = ("not absolute" if not p.is_absolute()
                   else "missing on disk" if not p.is_dir()
                   else "no index.md with a ## Spaces heading")
            reason = f"config `wiki` ignored: {p} ({why})"
    return None, reason


def resolve_root(explicit: str | None) -> Path:
    """Explicit path, else nearest CWD-ancestor wiki, else the `wiki` key in
    the user config. Exits 2 when nothing resolves — naming the configured
    path it had to ignore, so a broken config never fails silently."""
    if explicit:
        p = Path(os.path.abspath(os.path.expanduser(explicit)))
        if not is_wiki(p):
            die(f"not a wiki: {p} (no index.md with a ## Spaces heading)")
        return p
    cwd = Path(os.getcwd())
    for p in (cwd, *cwd.parents):
        if is_wiki(p):
            return p
    found, reason = config_wiki()
    if found is not None:
        return found
    die("no wiki found: pass --wiki, run inside one, or set `wiki` in "
        "~/.config/wiki-spaces/config" + (f"; {reason}" if reason else ""))


def die(msg: str) -> NoReturn:
    print(f"ws.py: {msg}", file=sys.stderr)
    raise SystemExit(2)


# ---------- trust scope ----------

@lru_cache(maxsize=None)
def _git_config_path(root: Path) -> Path | None:
    git = root / ".git"
    if git.is_dir():
        cfg = git / "config"
        return cfg if cfg.is_file() else None
    body = read_text(git) if git.is_file() else None
    if body is None:
        return None
    gitdir = None
    for line in body.splitlines():
        if line.strip().startswith("gitdir:"):
            target = line.strip()[len("gitdir:"):].strip()
            if target:
                gitdir = Path(target)
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir).resolve()
            break
    if gitdir is None or not gitdir.is_dir():
        return None
    # A worktree shares config via commondir; a submodule embeds it.
    common = read_text(gitdir / "commondir")
    if common and common.strip():
        cp = Path(common.strip())
        if not cp.is_absolute():
            cp = (gitdir / cp).resolve()
        cfg = cp / "config"
        if cfg.is_file():
            return cfg
    cfg = gitdir / "config"
    return cfg if cfg.is_file() else None


@lru_cache(maxsize=None)
def _origin_url(root: Path) -> str | None:
    cfg = _git_config_path(root)
    text = read_text(cfg) if cfg else None
    if text is None:
        return None
    m = re.search(r'\[remote\s+"origin"\][^\[]*?url\s*=\s*(\S+)', text, re.DOTALL)
    return m.group(1).strip() if m else None


@lru_cache(maxsize=None)
def _foreign_submodules(root: Path) -> frozenset[str]:
    """Submodule paths (root-relative posix) whose origin differs from the
    wiki's own — or whose comparison cannot be made."""
    text = read_text(root / ".gitmodules")
    if text is None:
        return frozenset()
    origin = _origin_url(root)
    foreign = set()
    for section in re.split(r"(?=^\[submodule )", text, flags=re.MULTILINE):
        m_path = re.search(r"^\s*path\s*=\s*(.+)$", section, re.MULTILINE)
        if not m_path:
            continue
        m_url = re.search(r"^\s*url\s*=\s*(\S+)", section, re.MULTILINE)
        url = m_url.group(1).strip() if m_url else None
        if url is None or origin is None or url != origin:
            foreign.add(m_path.group(1).strip())
    return frozenset(foreign)


def _scope_reason(parts: tuple[str, ...], root: Path) -> str | None:
    if parts and parts[0] == "shared":
        return "under shared/"
    subs = _foreign_submodules(root)
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in subs:
            return "foreign-origin git submodule"
    return None


def external_reason(path: Path, root: Path) -> str | None:
    """Why `path` is external relative to the resolved root, or None."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "outside the wiki tree"
    reason = _scope_reason(rel.parts, root)
    if reason:
        return reason
    if path.is_symlink():
        target = safe_resolve(path)
        root_real = safe_resolve(root)
        if target is None or root_real is None or target.is_symlink():
            return "symlink escapes the wiki tree"
        try:
            trel = target.relative_to(root_real)
        except ValueError:
            return "symlink escapes the wiki tree"
        if _scope_reason(trel.parts, root):
            return "symlink into external scope"
    return None


# ---------- traversal (the `## Spaces` contract) ----------

def walk_spaces(root: Path, include_external: bool = False) -> list[Node]:
    """Spaces reachable via the contract, preorder, entries in file order:
    `(rel, path, external)` with the root first as `.`. Malformed hrefs are
    skipped, external children only enter on request (and stay marked down
    their subtree), realpaths guard against cycles, and a child counts only
    when its own index.md carries `## Spaces`."""
    root_real = safe_resolve(root)
    if root_real is None:
        return []
    visited = {root_real}
    out = [(".", root, False)]

    def descend(space: Path, rel: str, external: bool) -> None:
        text = read_text(space / "index.md")
        if text is None:
            return
        entries, _ = parse_spaces(text)
        for _label, href in entries:
            nd = normalize_href(href)
            if nd is None:
                continue
            child = space / nd
            child_real = safe_resolve(child)
            if child_real is None:
                continue
            ext = external or external_reason(child, root) is not None
            if not ext:
                try:
                    child_real.relative_to(root_real)
                except ValueError:
                    ext = True
            if ext and not include_external:
                continue
            if child_real in visited or not is_wiki(child):
                continue
            visited.add(child_real)
            crel = nd if rel == "." else f"{rel}/{nd}"
            out.append((crel, child, ext))
            descend(child, crel, ext)

    descend(root, ".", False)
    return out


def _descend(
    d: Path, drel: str, dext: bool, root: Path, include_external: bool,
    seen: set[Path], files: list[Node], spaces: list[Node] | None,
) -> None:
    """The one directory descent under both views. Collects `.md` files
    (realpath-deduped, hidden/reserved dirs skipped, external honored).
    With a `spaces` sink it records space-shaped dirs (an index.md, heading
    or not) and keeps descending — the audit's filesystem truth. Without
    one it stops at child-space boundaries — they belong to the contract
    walk."""
    if spaces is not None and (d / "index.md").is_file():
        spaces.append((drel, d, dext))
    try:
        children = sorted(d.iterdir(), key=lambda p: p.name)
    except OSError:
        return
    # Files first, deduped by realpath with the real file preferred over a
    # symlink alias (CLAUDE.md -> AGENTS.md is one page, under its own name).
    here = {}
    for c in children:
        if not (c.is_file() and c.suffix == ".md"):
            continue
        ext = dext
        if c.is_symlink() and external_reason(c, root) is not None:
            if not include_external:
                continue
            ext = True
        cr = safe_resolve(c)
        if cr is None or cr in seen:
            continue
        kept = here.get(cr)
        if kept is None or (kept[0].is_symlink() and not c.is_symlink()):
            here[cr] = (c, ext)
    for cr, (c, ext) in here.items():
        seen.add(cr)
        files.append((c.name if drel == "." else f"{drel}/{c.name}", c, ext))
    for c in children:
        if not c.is_dir() or is_reserved(c.name):
            continue
        if spaces is None and (c / "index.md").is_file():
            continue
        ext = dext or external_reason(c, root) is not None
        if ext and not include_external:
            continue
        cr = safe_resolve(c)
        if cr is None or cr in seen:
            continue
        seen.add(cr)
        crel = c.name if drel == "." else f"{drel}/{c.name}"
        _descend(c, crel, ext, root, include_external, seen, files, spaces)


def walk_files(root: Path, include_external: bool = False) -> list[Node]:
    """Markdown files reachable via the contract: per space, plain-folder
    descent only."""
    files, seen = [], set()
    for srel, space, sext in walk_spaces(root, include_external):
        _descend(space, srel, sext, root, include_external, seen, files, None)
    return files


def walk_owned(
    root: Path, include_external: bool = False,
) -> tuple[list[Node], list[Node]]:
    """Filesystem walk inside trust scope (the audit's view — it must see
    what the contract misses): `(md_files, space_dirs)`."""
    files, spaces = [], []
    root_real = safe_resolve(root)
    if root_real is None:
        return files, spaces
    _descend(root, ".", False, root, include_external, {root_real}, files, spaces)
    return files, spaces


# ---------- caps ----------

def load_caps(root: Path) -> dict[str, int]:
    """Basename-keyed caps: defaults overlaid with `_meta/limits.md` lines
    of the form `basename: bytes`. The literal name `*.md` re-caps the
    catch-all for content pages — a reserved name, not a glob. Non-positive
    caps and anything else in the file are ignored."""
    caps = dict(DEFAULT_CAPS)
    text = read_text(root / "_meta" / "limits.md")
    if text is None:
        return caps
    for line in text.splitlines():
        m = LIMIT_LINE_RE.match(line)
        if m and "/" not in m.group(1) and "\\" not in m.group(1):
            value = int(m.group(2))
            if value > 0:
                caps[m.group(1)] = value
    return caps


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

def link_scan(md_files: list[Node]) -> tuple[list[tuple[str, str]], set[str]]:
    """`(broken, incoming)` over the given files. Broken: plain `[[links]]`
    that resolve to no known page (embeds `![[...]]` are exempt — they
    routinely target assets). Incoming: wikilink and relative markdown-link
    targets, for the orphan check. Code blocks and spans are stripped first;
    in index.md the `## Spaces` body is contract, not content."""
    by_rel = {rel: path for rel, path, _ext in md_files}
    by_stem = {}
    for rel in by_rel:
        by_stem.setdefault(posixpath.basename(rel)[:-3], []).append(rel)

    def resolve(target: str, page_rel: str) -> list[str]:
        t = target if target.lower().endswith(".md") else target + ".md"
        found = set()
        if t in by_rel:
            found.add(t)
        here = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), t))
        if here in by_rel:
            found.add(here)
        if "/" in t:
            found.update(k for k in by_rel if k.endswith("/" + t))
        else:
            found.update(by_stem.get(t[:-3], ()))
        return sorted(found)

    broken, incoming = [], set()
    for rel, path, _ext in md_files:
        text = read_text(path)
        if text is None:
            continue
        body = body_after_frontmatter(text)
        if posixpath.basename(rel) == "index.md":
            body = blank_spaces_section(body)
        scan = strip_code(body)
        for m in WIKILINK_RE.finditer(scan):
            target = m.group(2).split("|", 1)[0].split("#", 1)[0].strip()
            if not target:
                continue
            found = resolve(target, rel)
            if found:
                if found[0] != rel:
                    incoming.add(found[0])
            elif not m.group(1):
                broken.append((rel, target))
        for m in MD_LINK_RE.finditer(scan):
            href = m.group(1)
            if "://" in href or href.startswith(("mailto:", "#", "/")):
                continue
            href = unquote(href.split("#", 1)[0])
            if not href.endswith(".md"):
                continue
            t = posixpath.normpath(posixpath.join(posixpath.dirname(rel), href))
            if t in by_rel and t != rel:
                incoming.add(t)
    return broken, incoming


def space_findings(
    root: Path, space_dirs: list[Node],
) -> tuple[list[tuple[str, Path]], list[tuple[str, str]],
           list[tuple[str, str, bool]], list[tuple[str, str]]]:
    """Contract findings per space-shaped dir: missing `## Spaces` headings,
    malformed entries, drift (missing = unlisted owned child space, nearest
    ancestor wins; stale = registrable entry with no index.md on disk)."""
    by_path = {path: rel for rel, path, _ext in space_dirs}
    owned = {path for rel, path, ext in space_dirs if not ext}

    def nearest_ancestor(path: Path) -> Path | None:
        p = path.parent
        while True:
            if p in by_path:
                return p
            if p == root or p == p.parent:
                return None
            p = p.parent

    children = {}
    for _rel, path, ext in space_dirs:
        if path == root or ext:
            continue
        anc = nearest_ancestor(path)
        if anc is not None:
            children.setdefault(anc, []).append(path)

    bare, malformed, missing, stale = [], [], [], []
    for rel, path, _ext in sorted(space_dirs):
        text = read_text(path / "index.md")
        if text is None:
            continue
        if not has_spaces(text):
            bare.append((rel, path))
            continue
        entries, bad = parse_spaces(text)
        for raw in bad:
            malformed.append((rel, raw))
        listed = set()
        for _label, href in entries:
            nd = normalize_href(href)
            if nd is None:
                malformed.append((rel, f"unregistrable href: {href.strip()}"))
                continue
            listed.add(nd)
            if not (path / nd / "index.md").is_file():
                stale.append((rel, nd))
        for child in sorted(children.get(path, ())):
            crel = child.relative_to(path).as_posix()
            if crel not in listed:
                missing.append((rel, crel, path in owned))
    return bare, malformed, missing, stale


def fix_pass(
    root: Path, space_dirs: list[Node], caps: dict[str, int],
) -> list[str]:
    """The bounded repair: insert missing `## Spaces` headings, then register
    unlisted owned child spaces — owned files only, never past a cap, never
    into an index with a malformed bullet, never a child still missing its
    own heading. Returns report lines."""
    fixed = []
    owned = [(rel, path) for rel, path, ext in sorted(space_dirs) if not ext]
    cap = cap_for("index.md", caps)
    for rel, path in owned:
        index = path / "index.md"
        text = read_text(index)
        if text is None or has_spaces(text):
            continue
        new = text + ("" if text.endswith("\n") or not text else "\n") + "\n## Spaces\n"
        if cap is not None and len(new.encode("utf-8")) > cap:
            fixed.append(f"! {_ix(rel)}: heading insert would exceed cap {cap}")
            continue
        try:
            write_atomic(index, new)
        except OSError as exc:
            fixed.append(f"! {_ix(rel)}: write failed: {exc}")
            continue
        fixed.append(f"~ {_ix(rel)}: inserted ## Spaces heading")

    _bare, _malformed, missing, _stale = space_findings(root, space_dirs)
    by_space = {}
    for rel, crel, is_owned in missing:
        if is_owned is False:
            continue
        by_space.setdefault(rel, []).append(crel)
    by_path = {rel: path for rel, path, _ext in space_dirs}
    for rel in sorted(by_space):
        path = by_path[rel]
        if path not in {p for r, p in owned}:
            continue
        index = path / "index.md"
        text = read_text(index)
        if text is None or not has_spaces(text):
            continue
        _entries, bad = parse_spaces(text)
        if bad:
            fixed.append(f"! {_ix(rel)}: malformed bullet in ## Spaces — "
                         "repair it first, then re-run audit --fix")
            continue
        for crel in by_space[rel]:
            if not has_spaces(read_text(path / crel / "index.md")):
                fixed.append(f"! {_ix(rel)}: not registering {crel}/ — child "
                             "still lacks ## Spaces")
                continue
            lines = text.splitlines()
            head, start, end = find_section(lines)
            at = start
            for i in range(end - 1, start - 1, -1):
                if lines[i].strip():
                    at = i + 1
                    break
            else:
                lines[head:head + 1] = [lines[head], ""]
                at = head + 2
            lines.insert(at, f"- [{crel}/]({crel}/index.md)")
            new = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
            if cap is not None and len(new.encode("utf-8")) > cap:
                fixed.append(f"! {_ix(rel)}: registering {crel}/ would "
                             f"exceed cap {cap}")
                continue
            try:
                write_atomic(index, new)
            except OSError as exc:
                fixed.append(f"! {_ix(rel)}: write failed: {exc}")
                continue
            text = new
            fixed.append(f"~ {_ix(rel)}: registered {crel}/")
    return fixed


def cmd_audit(root: Path, include_external: bool, fix: bool) -> int:
    caps = load_caps(root)
    md, spaces = walk_owned(root, include_external)
    fixed = fix_pass(root, spaces, caps) if fix else []
    if fixed:
        md, spaces = walk_owned(root, include_external)

    bare, malformed, missing, stale = space_findings(root, spaces)
    broken, incoming = link_scan(md)
    over = []
    for rel, path, _ext in md:
        cap = cap_for(posixpath.basename(rel), caps)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if cap is not None and size > cap:
            over.append((rel, size, cap))
    orphans = [rel for rel, _path, _ext in md
               if posixpath.basename(rel) not in ORPHAN_EXEMPT
               and rel not in incoming]

    print(f"wiki: {root}")
    print(f"spaces: {len(spaces)}  pages: {len(md)}")
    for line in fixed:
        print(f"fixed {line}")
    for rel, _path in bare:
        print(f"contract {_ix(rel)}: no ## Spaces heading"
              + ("" if fix else " (audit --fix inserts it)"))
    for rel, raw in malformed:
        print(f"contract {_ix(rel)}: malformed entry: {raw}")
    for rel, crel, _owned in missing:
        print(f"drift {_ix(rel)}: missing entry for {crel}/"
              + ("" if fix else " (audit --fix registers owned children)"))
    for rel, nd in stale:
        print(f"drift {_ix(rel)}: stale entry {nd}/ (no index.md on disk)")
    for rel, target in sorted(broken):
        print(f"broken {rel}: [[{target}]]")
    for rel, size, cap in sorted(over):
        print(f"over-cap {rel}: {size} > {cap} bytes — split, promote, or trim")
    if orphans:
        print(f"orphans ({len(orphans)} — informational, a page may be "
              "standalone on purpose):")
        for rel in sorted(orphans):
            print(f"  . {rel}")
    errors = len(bare) + len(malformed) + len(missing) + len(stale) \
        + len(broken) + len(over)
    print(f"issues: {errors}" if errors else "ok")
    return 1 if errors else 0


# ---------- entry points ----------

def cmd_list(root: Path, include_external: bool) -> int:
    for rel, _path, ext in walk_spaces(root, include_external):
        print(rel + (" [external]" if ext else ""))
    return 0


def cmd_files(root: Path, include_external: bool) -> int:
    for rel, _path, ext in walk_files(root, include_external):
        print(rel + (" [external]" if ext else ""))
    return 0


def cmd_grep(
    root: Path, pattern: str, ignore_case: bool, include_external: bool,
) -> int:
    """Line matches as `rel:line: text` over the contract-reachable files —
    the exact set `files` prints, so trust scope and reserved dirs hold."""
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        die(f"bad pattern: {exc}")
    matched = False
    for rel, path, _ext in walk_files(root, include_external):
        text = read_text(path)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matched = True
                print(f"{rel}:{n}: {line.rstrip()}")
    return 0 if matched else 1


def cmd_check_size(root: Path, target: str, use_stdin: bool) -> int:
    path = Path(target)
    name = path.name
    if not use_stdin and path.is_dir():
        die(f"target is a directory: {target}")
    if use_stdin:
        size = len(sys.stdin.buffer.read())
    else:
        try:
            size = path.stat().st_size
        except OSError:
            die(f"no such file: {target} (pipe planned content with --stdin)")
    cap = cap_for(name, load_caps(root))
    if cap is None:
        print(f"ok {name}: {size} bytes, no cap")
        return 0
    if size > cap:
        print(f"over {name}: {size} > {cap} bytes — split, promote, or trim; "
              "never truncate")
        return 1
    print(f"ok {name}: {size} <= {cap} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ws.py", description="wiki-spaces helper: deterministic traversal "
        "and search, cap verdicts, and audit/repair for a wiki.")
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
                            help="also cross external spaces (shared/, foreign "
                            "submodules, escaping symlinks)")
        return sp

    command("list", "spaces reachable via the ## Spaces contract", external=True)
    command("files", "markdown files reachable via the contract", external=True)
    sp = command("grep", "regex line search over the files the contract "
                 "reaches", external=True)
    sp.add_argument("pattern", help="python regex matched against each line")
    sp.add_argument("-i", "--ignore-case", action="store_true",
                    help="case-insensitive match")
    sp = command("check-size", "cap verdict for one file")
    sp.add_argument("target", help="file the content is (or will be) written to")
    sp.add_argument("--stdin", action="store_true",
                    help="measure stdin instead of the file on disk")
    sp = command("audit", "detect drift, broken wikilinks, over-cap files",
                 external=True)
    sp.add_argument("--fix", action="store_true",
                    help="insert missing ## Spaces headings and register "
                    "unlisted owned child spaces")

    args = parser.parse_args(argv)
    root = resolve_root(args.wiki)
    if args.cmd == "list":
        return cmd_list(root, args.external)
    if args.cmd == "files":
        return cmd_files(root, args.external)
    if args.cmd == "grep":
        return cmd_grep(root, args.pattern, args.ignore_case, args.external)
    if args.cmd == "check-size":
        return cmd_check_size(root, args.target, args.stdin)
    return cmd_audit(root, args.external, args.fix)


if __name__ == "__main__":
    raise SystemExit(main())
