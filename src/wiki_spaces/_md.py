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


def _path_distance(a: Path, b: Path) -> int:
    """Directory steps between two absolute paths (0 when equal)."""
    common = 0
    for x, y in zip(a.parts, b.parts):
        if x != y:
            break
        common += 1
    return (len(a.parts) - common) + (len(b.parts) - common)


def resolve_wikilink(target: str, base: Path, candidates: set[Path]) -> Path | None:
    """Resolve a wikilink target against a set of candidate page paths.

    `target` is the wikilink contents (no `[[ ]]`, no alias, no heading).
    `base` is the directory of the page doing the linking.
    `candidates` is the set of all known page paths in the wiki (absolute).

    Resolution order:
    1. Path-relative match from `base` — the most specific, since it is the
       linking page's own directory. An implicit `.md` suffix is allowed.
    2. Filename match anywhere. When several pages share the filename, the
       one whose directory is closest to `base` wins; any remaining tie
       breaks on sorted path — so the result never depends on the iteration
       order of the `candidates` set.

    Returns the resolved absolute path, or None when no match.
    """
    # Normalize: target may or may not include `.md`.
    name = target if target.endswith(".md") else f"{target}.md"

    # 1. Exact path-relative match from the linking page's directory.
    rel = (base / name).resolve()
    if rel in candidates:
        return rel

    # 2. Filename match anywhere — deterministic: closest to `base`, then
    #    sorted path as the final tie-break.
    matches = [c for c in candidates if c.name == name]
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
