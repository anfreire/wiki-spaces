"""Markdown helpers for wiki-spaces tools.

Pure functions on markdown text. No I/O — callers handle file reads/writes.
Stdlib only.

Scope:
- `## Spaces` section parse/edit (add_entry, remove_entry,
  parse_section_entries, has_section).
- Minimal frontmatter parse/serialize for the documented schema (scalars,
  inline arrays, folded `>-` scalars).
- Wikilink discovery and target resolution.

These are the operations the reference skills perform repeatedly. Anything
beyond the documented schema (multi-line block scalars, anchors, custom
tags) falls back to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# ---------- Index sections (## Spaces) ----------

ENTRY_RE = re.compile(
    r"^\s*-\s+\[([^\]]+)\]\(([^)]+)\)(?:\s*[—\-]+\s*(.*))?$"
)
WIKILINK_ENTRY_RE = re.compile(
    r"^\s*-\s+\[\[([^\]]+)\]\](?:\s*[—\-]+\s*(.*))?$"
)


@dataclass(frozen=True)
class IndexEntry:
    """A parsed bullet from a `## <heading>` section (e.g. `## Spaces`).

    Either `href` (markdown-link form, e.g. `path/`) or `wikilink`
    (`[[name]]` form) is set; never both. `description` is the optional
    trailing text after `—`.
    """
    label: str | None
    href: str | None
    wikilink: str | None
    description: str | None


def has_section(text: str, heading: str) -> bool:
    """True when text has a `## <heading>` line."""
    target = f"## {heading}"
    return any(line.rstrip() == target for line in text.splitlines())


def parse_section_entries(text: str, heading: str) -> list[IndexEntry]:
    """Return bullet entries under `## <heading>`. [] when heading absent."""
    out: list[IndexEntry] = []
    target = f"## {heading}"
    in_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line == target:
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("## "):
            break
        m = ENTRY_RE.match(line)
        if m:
            out.append(IndexEntry(
                label=m.group(1),
                href=m.group(2),
                wikilink=None,
                description=(m.group(3) or "").strip() or None,
            ))
            continue
        w = WIKILINK_ENTRY_RE.match(line)
        if w:
            out.append(IndexEntry(
                label=None,
                href=None,
                wikilink=w.group(1),
                description=(w.group(2) or "").strip() or None,
            ))
    return out


def render_entry(label: str, href: str, description: str | None = None) -> str:
    """Render a markdown-link bullet: `- [label](href)` or with ` — description`."""
    base = f"- [{label}]({href})"
    if description:
        return f"{base} — {description}"
    return base


def _section_range(lines: list[str], heading: str) -> tuple[int, int] | None:
    """Return (start, end) line indices of `## <heading>` block, end exclusive.

    start is the line right after the heading; end is the line of the next
    heading or len(lines). None when heading absent.
    """
    target = f"## {heading}"
    start = None
    for i, raw in enumerate(lines):
        if raw.rstrip() == target:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return start, end


def add_entry(
    text: str,
    heading: str,
    label: str,
    href: str,
    description: str | None = None,
) -> str:
    """Add an entry to `## <heading>`. Idempotent on href match.

    If the section exists, the entry is appended at the end of its bullet list
    (before any trailing blank line). If the section is absent, a new section
    is appended at the end of the document (with leading blank-line gap).
    """
    lines = text.splitlines(keepends=False)
    entry = render_entry(label, href, description)
    range_ = _section_range(lines, heading)
    if range_ is None:
        prefix = "" if not lines or lines[-1] == "" else "\n"
        suffix = "\n"
        appended = f"{prefix}## {heading}\n\n{entry}{suffix}"
        out = "\n".join(lines)
        if not out.endswith("\n"):
            out += "\n"
        return out + appended

    start, end = range_
    # Skip leading blank line(s) inside the section.
    body_start = start
    while body_start < end and lines[body_start].strip() == "":
        body_start += 1
    # Idempotent: skip if an entry with same href already exists.
    for i in range(body_start, end):
        m = ENTRY_RE.match(lines[i])
        if m and m.group(2) == href:
            return text  # already present

    # Find insertion point: end of bullet block (before trailing blanks).
    insert_at = end
    while insert_at > body_start and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    new_lines = lines[:insert_at] + [entry] + lines[insert_at:]
    result = "\n".join(new_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def remove_entry(text: str, heading: str, href: str) -> str:
    """Remove the bullet whose href matches. No-op when section or entry absent."""
    lines = text.splitlines(keepends=False)
    range_ = _section_range(lines, heading)
    if range_ is None:
        return text
    start, end = range_
    new_lines: list[str] = []
    removed = False
    for i, line in enumerate(lines):
        if start <= i < end and not removed:
            m = ENTRY_RE.match(line)
            if m and m.group(2) == href:
                removed = True
                continue
        new_lines.append(line)
    if not removed:
        return text
    result = "\n".join(new_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


# ---------- Wikilinks ----------

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def find_wikilinks(text: str) -> list[str]:
    """Return wikilink targets in text, with `|alias` and `#heading` stripped.

    Order preserved, duplicates kept.
    """
    out: list[str] = []
    for m in WIKILINK_RE.finditer(text):
        target = m.group(1)
        # Strip `|alias` and `#heading` per Obsidian semantics.
        target = target.split("|", 1)[0]
        target = target.split("#", 1)[0]
        target = target.strip()
        if target:
            out.append(target)
    return out


_WIKILINK_REF_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")


def find_wikilink_refs(text: str) -> list[tuple[str, bool]]:
    """Return `(target, is_embed)` for each wikilink and embed in text.

    `is_embed` is True for `![[...]]` — an Obsidian embed, which routinely
    targets a non-page asset (image, PDF, audio) — and False for a plain
    `[[...]]` link. Targets have `|alias` and `#heading` stripped; order
    preserved, duplicates kept. `find_wikilinks` is the target-only view of
    the same scan.
    """
    out: list[tuple[str, bool]] = []
    for m in _WIKILINK_REF_RE.finditer(text):
        is_embed = bool(m.group(1))
        target = m.group(2).split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            out.append((target, is_embed))
    return out


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def strip_code_spans(text: str) -> str:
    """Blank out fenced code blocks and inline code spans; line count kept.

    Used before scanning for `[[wikilinks]]` so links shown inside code
    examples aren't mistaken for real links. A fenced block opens and closes
    on a line of 3+ backticks or tildes; the closing fence must use the same
    character and be at least as long as the opener (so a `~~~` line cannot
    close a ``` block, nor a short fence close a longer one). Code content
    becomes blank lines / spaces, so a caller that indexes by line number
    stays aligned.
    """
    out: list[str] = []
    fence: str | None = None  # the opening fence run, e.g. "```" or "~~~~"
    for line in text.splitlines():
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                out.append("")
                continue
            out.append(_INLINE_CODE_RE.sub(lambda mm: " " * len(mm.group(0)), line))
        else:
            out.append("")
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return "\n".join(out)


def _path_distance(a: Path, b: Path) -> int:
    """Directory steps between two absolute paths (0 when equal)."""
    common = 0
    for x, y in zip(a.parts, b.parts):
        if x != y:
            break
        common += 1
    return (len(a.parts) - common) + (len(b.parts) - common)


def resolve_wikilink(
    target: str,
    base: Path,
    candidates: set[Path],
    *,
    wiki_root: Path | None = None,
) -> Path | None:
    """Resolve a wikilink target against a set of candidate page paths.

    `target` is the wikilink contents (no `[[ ]]`, no alias, no heading).
    `base` is the directory of the page doing the linking.
    `candidates` is the set of all known page paths in the wiki (absolute).
    `wiki_root` is the wiki root directory; required for wiki-root pathful
    resolution (when None, the resolver falls back to base-only behavior for
    backward compatibility, but callers should always pass it).

    Resolution order (when `target` contains `/`):
    1. Wiki-root pathful — try `(wiki_root / target).resolve()` against
       candidates, with both `.md` and no-`.md` normalization. This matches
       what `cmd_promote` emits ("projects/foo/index") and is stable across
       page renames. Wiki-root-first is intentional: a base-relative shadow
       like `notes/concepts/foo.md` should NOT capture a `[[concepts/foo]]`
       link the producer emitted with wiki-root intent.
    2. Base-relative pathful — fallback when wiki-root missed. Try
       `(base / target).resolve()`, same `.md` normalization.

    When `target` has no `/`:
    3. Bare filename — match by `Path.name` across candidates; tie-break by
       closest-to-base path distance, then sorted path (deterministic;
       independent of `candidates` iteration order).

    Returns the resolved absolute path, or None when no match.
    """
    # Build the two normalization variants once: with-`.md` and as-given.
    # `target` may or may not include `.md`.
    name_with_md = target if target.endswith(".md") else f"{target}.md"
    name_as_given = target

    if "/" in target:
        # Pathful: try wiki-root first, then base-relative.
        if wiki_root is not None:
            for n in (name_with_md, name_as_given):
                rel = (wiki_root / n).resolve()
                if rel in candidates:
                    return rel
        for n in (name_with_md, name_as_given):
            rel = (base / n).resolve()
            if rel in candidates:
                return rel
        return None

    # Bare filename match anywhere — deterministic: closest to `base`, then
    # sorted path as the final tie-break.
    matches = [c for c in candidates if c.name == name_with_md]
    if not matches:
        return None
    base_resolved = base.resolve()
    return min(
        matches,
        key=lambda c: (_path_distance(base_resolved, c.parent), str(c)),
    )


# ---------- Frontmatter (minimal) ----------

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_text, body). frontmatter_text is None when absent.

    Pure split — no YAML parsing. Use parse_frontmatter for that.
    """
    if not text.startswith("---\n"):
        return None, text
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def parse_frontmatter(text: str) -> dict[str, str | list[str]] | None:
    """Parse the documented schema subset. Returns None when no frontmatter.

    Supported field forms:
    - `key: value` (string scalar)
    - `key: >-\\n  multi-line value` (folded scalar, becomes single string)
    - `key: [item1, item2]` (inline string array)
    - `key:\\n  - item1\\n  - item2` (YAML block-list — the form kepano's
      `obsidian-markdown` skill emits for `tags:` and `aliases:`)

    Anything else (nested mappings, anchors, multi-line plain scalars) is
    not parsed — those fields will be missing from the returned dict.
    Callers that need full YAML should not use this helper.
    """
    fm_text, _ = split_frontmatter(text)
    if fm_text is None:
        return None
    out: dict[str, str | list[str]] = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2)
        if value.startswith(">-") or value == ">-":
            parts: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i] == ""):
                stripped = lines[i].strip()
                if stripped:
                    parts.append(stripped)
                i += 1
            out[key] = " ".join(parts)
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                out[key] = []
            else:
                out[key] = [item.strip().strip("'\"") for item in inner.split(",")]
            i += 1
            continue
        if not value.strip():
            items, consumed = _consume_block_list(lines, i + 1)
            if items is not None:
                out[key] = items
                i += 1 + consumed
                continue
            out[key] = ""
            i += 1
            continue
        out[key] = value.strip().strip("'\"")
        i += 1
    return out


_BLOCK_LIST_ITEM_RE = re.compile(r"^(\s+)-\s*(.*)$")


def _consume_block_list(lines: list[str], start: int) -> tuple[list[str] | None, int]:
    """Collect a YAML block-list starting at `lines[start]`.

    Returns (items, consumed). When no `  - item` line is present at start,
    returns (None, 0) so the caller can fall back to scalar handling.
    """
    items: list[str] = []
    j = start
    while j < len(lines):
        candidate = lines[j]
        if not candidate.strip():
            j += 1
            continue
        m = _BLOCK_LIST_ITEM_RE.match(candidate)
        if not m:
            break
        items.append(m.group(2).strip().strip("'\""))
        j += 1
    if not items:
        return None, 0
    return items, j - start


def update_frontmatter_field(text: str, key: str, value: str) -> str:
    """Set/replace a single scalar `key: value` in existing frontmatter.

    No-op when frontmatter absent. Preserves all other lines and order.
    """
    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        return text
    lines = fm_text.splitlines()
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}: {value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}: {value}")
    new_fm = "\n".join(lines)
    return f"---\n{new_fm}\n---\n{body}"


# ---------- Markdown links (path-aware rewrite for `space promote`) ----------

MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class MarkdownLink:
    """A `[label](href[#anchor])` link with its source span.

    `anchor` includes the leading `#` (or empty string).
    `span` is the (start, end) offsets in the original text.
    """
    label: str
    href: str
    anchor: str
    span: tuple[int, int]


def parse_markdown_links(text: str) -> list[MarkdownLink]:
    """Find every `[label](href[#anchor])` link in `text`.

    Anchor captured separately so a rewrite of just the path part is trivial.
    Wikilink syntax `[[target]]` is not matched (separate primitive).
    """
    out: list[MarkdownLink] = []
    for m in MARKDOWN_LINK_RE.finditer(text):
        href = m.group(2)
        if "#" in href:
            path, anchor_body = href.split("#", 1)
            anchor = "#" + anchor_body
        else:
            path, anchor = href, ""
        out.append(MarkdownLink(
            label=m.group(1),
            href=path,
            anchor=anchor,
            span=m.span(),
        ))
    return out


def _is_external_href(href: str) -> bool:
    """True for URLs, mailto:, anchor-only, or absolute paths."""
    if not href:
        return True
    if href.startswith(("http://", "https://", "mailto:", "ftp://", "//")):
        return True
    if href.startswith("#"):
        return True
    if href.startswith("/"):
        return True
    return False


def resolve_markdown_link(href: str, containing_file: Path, wiki_root: Path) -> Path | None:
    """Resolve a relative `href` against `containing_file`'s directory.

    Returns the absolute path the link refers to, or None when the link is
    external (URL, mailto:, anchor-only, absolute path) or escapes the wiki.
    """
    if _is_external_href(href):
        return None
    base = containing_file.parent
    try:
        target = (base / href).resolve()
    except OSError:
        return None
    try:
        target.relative_to(wiki_root.resolve())
    except ValueError:
        return None
    return target


def compute_relative_link(target: Path, from_file: Path) -> str:
    """POSIX-style relative path from `from_file`'s parent to `target`.

    Markdown uses `/` separators on every platform; `os.path.relpath` uses
    the native separator, so we normalize backslashes to forward slashes.
    """
    import os.path as _osp
    rel = _osp.relpath(str(target), str(from_file.parent))
    return rel.replace("\\", "/")


# ---------- Wikilinks: full structural parser ----------


@dataclass(frozen=True)
class Wikilink:
    """A `[[target[#anchor][|display]]]` wikilink with its source span.

    `target`  — the literal between `[[` and `|` or `#` (no normalization).
    `anchor`  — the `#anchor` portion without the `#`, or "".
    `display` — the `|display` portion, or None when no `|` was present.
    `span`    — (start, end) offsets of the whole `[[...]]` in the source.
    """
    target: str
    anchor: str
    display: str | None
    span: tuple[int, int]


def parse_wikilink_full(text: str) -> list[Wikilink]:
    """Find every `[[...]]` wikilink in `text` with full structure captured.

    Handles all forms: `[[t]]`, `[[t|d]]`, `[[t#a]]`, `[[t#a|d]]`, and
    pathful targets `[[folder/page]]` with optional anchor / display.
    """
    out: list[Wikilink] = []
    for m in WIKILINK_RE.finditer(text):
        inner = m.group(1)
        if "|" in inner:
            tgt_part, display = inner.split("|", 1)
            display = display.strip()
        else:
            tgt_part, display = inner, None
        if "#" in tgt_part:
            target, anchor = tgt_part.split("#", 1)
            anchor = anchor.strip()
        else:
            target, anchor = tgt_part, ""
        out.append(Wikilink(
            target=target.strip(),
            anchor=anchor,
            display=display,
            span=m.span(),
        ))
    return out


# ---------- Frontmatter aliases (additive merge for `space promote`) ----------


def parse_frontmatter_aliases(text: str) -> list[str]:
    """Return the `aliases:` list from frontmatter, or [] when absent.

    Pure parsing — no fs. Always returns a list (wraps a scalar value in
    [scalar] so callers don't have to type-check).
    """
    fm = parse_frontmatter(text)
    if not fm:
        return []
    aliases = fm.get("aliases")
    if aliases is None:
        return []
    if isinstance(aliases, str):
        return [aliases] if aliases else []
    return list(aliases)


_ALIASES_LINE_RE = re.compile(r"^aliases\s*:\s*(.*)$")


def frontmatter_add_alias(text: str, alias: str) -> tuple[str, bool]:
    """Add `alias` to the frontmatter `aliases:` list. Returns (new_text, added).

    Case-insensitive no-op via `casefold()` — matches Obsidian's autocomplete
    and the wiki-level collision preflight in `space.py`.

    Style preservation (best-effort, stdlib-only):
    - No frontmatter        → create block-style frontmatter at the top.
    - No `aliases:` field    → append block-list at the end of frontmatter.
    - `aliases: [a, b]`      → append in-line: `aliases: [a, b, alias]`.
    - `aliases:\\n  - a`      → append a new `  - alias` line at the same indent.

    Edge cases (mixed indentation, comments inside frontmatter) fall back
    to block style and may not match the source style byte-for-byte — tests
    assert the alias is present and parseable, not perfect formatting.
    """
    existing = parse_frontmatter_aliases(text)
    if any(a.casefold() == alias.casefold() for a in existing):
        return text, False

    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        new_fm = f"---\naliases:\n  - {alias}\n---\n"
        return new_fm + text, True

    lines = fm_text.splitlines()
    for i, line in enumerate(lines):
        m = _ALIASES_LINE_RE.match(line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            new_inner = f"{inner}, {alias}" if inner else alias
            lines[i] = f"aliases: [{new_inner}]"
            break
        # Block-list: walk forward until the indented list ends.
        insert_at = i + 1
        indent = "  "
        while insert_at < len(lines):
            cand = lines[insert_at]
            mm = _BLOCK_LIST_ITEM_RE.match(cand)
            if mm:
                indent = mm.group(1)
                insert_at += 1
                continue
            if not cand.strip():
                insert_at += 1
                continue
            break
        lines.insert(insert_at, f"{indent}- {alias}")
        break
    else:
        lines.append("aliases:")
        lines.append(f"  - {alias}")

    new_fm = "\n".join(lines)
    trailing = "\n" if text.endswith("\n") or body else ""
    return f"---\n{new_fm}\n---\n{body}", True
