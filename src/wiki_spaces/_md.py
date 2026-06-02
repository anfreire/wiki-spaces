"""Markdown helpers for wiki-spaces tools.

Pure functions on markdown text. No I/O — callers handle file reads/writes.

Scope:
- `## Spaces` section parse/edit (add_entry, remove_entry,
  parse_section_entries, has_section).
- Frontmatter parse/serialize via PyYAML (full safe_load semantics).
- Wikilink discovery and target resolution.

These are the operations the reference skills perform repeatedly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml


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
    """True when `text` has a real (non-fenced) `## <heading>` line."""
    return find_section_bounds(text.splitlines(), heading) is not None


def find_section_bounds(lines: list[str], heading: str) -> tuple[int, int, int] | None:
    """Locate `## <heading>` in `lines`: `(heading_line, body_start, body_end)`
    with `body_end` exclusive (next `## ` heading or EOF), or None when absent.
    A `## <heading>` shown inside a fenced code block is skipped — it is an
    example, not the navigation contract.

    The single `## <heading>` boundary scanner — `add_entry`/`remove_entry`,
    `parse_section_entries`, and `_model.parse_section_block` all consume it so
    producer edits and consumer reads never drift on what a section spans.
    """
    target = f"## {heading}"
    fenced = _fenced_line_mask(lines)
    heading_line: int | None = None
    for i, raw in enumerate(lines):
        if not fenced[i] and raw.rstrip() == target:
            heading_line = i
            break
    if heading_line is None:
        return None
    body_start = heading_line + 1
    body_end = len(lines)
    for i in range(body_start, len(lines)):
        if not fenced[i] and lines[i].startswith("## "):
            body_end = i
            break
    return heading_line, body_start, body_end


def parse_section_entries(text: str, heading: str) -> list[IndexEntry]:
    """Return bullet entries under `## <heading>`. [] when heading absent."""
    out: list[IndexEntry] = []
    lines = text.splitlines()
    bounds = find_section_bounds(lines, heading)
    if bounds is None:
        return out
    _, body_start, body_end = bounds
    for raw in lines[body_start:body_end]:
        line = raw.rstrip()
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
    bounds = find_section_bounds(lines, heading)
    if bounds is None:
        prefix = "" if not lines or lines[-1] == "" else "\n"
        suffix = "\n"
        appended = f"{prefix}## {heading}\n\n{entry}{suffix}"
        out = "\n".join(lines)
        if not out.endswith("\n"):
            out += "\n"
        return out + appended

    _, start, end = bounds
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
    bounds = find_section_bounds(lines, heading)
    if bounds is None:
        return text
    _, start, end = bounds
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


_WIKILINK_REF_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")


def find_wikilink_refs(text: str) -> list[tuple[str, bool]]:
    """Return `(target, is_embed)` for each wikilink and embed in text.

    `is_embed` is True for `![[...]]` — an Obsidian embed, which routinely
    targets a non-page asset (image, PDF, audio) — and False for a plain
    `[[...]]` link. Targets have `|alias` and `#heading` stripped; order
    preserved, duplicates kept.
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


def _fenced_line_mask(lines: list[str]) -> list[bool]:
    """Per-line mask: True for every line inside a fenced code block, including
    the opening and closing fence lines.

    The one fence-state scanner — shared by `strip_code_spans` and
    `find_section_bounds` so a `## heading` (or `[[wikilink]]`) shown inside a
    code example is never read as real content. A fence opens/closes on a line
    of 3+ backticks or tildes; the closer must use the same character and be at
    least as long as the opener.
    """
    mask = [False] * len(lines)
    fence: str | None = None
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                mask[i] = True
        else:
            mask[i] = True
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return mask


def strip_code_spans(text: str) -> str:
    """Blank out fenced code blocks and inline code spans; line count kept.

    Used before scanning for `[[wikilinks]]` so links shown inside code
    examples aren't mistaken for real links. A fenced block opens and closes
    on a line of 3+ backticks or tildes; the closing fence must use the same
    character and be at least as long as the opener (so a `~~~` line cannot
    close a ``` block, nor a short fence close a longer one). Code content
    becomes blank lines / spaces, so a caller that indexes by line number
    stays aligned.

    NOTE: line count is preserved, but the TOTAL CHARACTER COUNT is not —
    a "def foo():\n" code line collapses to "\n". Use
    `mask_code_spans_offset_preserving` for callers that index by char
    offset (e.g., span-based link rewriting in `space/promote.py`).
    """
    lines = text.splitlines()
    fenced = _fenced_line_mask(lines)
    out: list[str] = []
    for i, line in enumerate(lines):
        if fenced[i]:
            out.append("")
        else:
            out.append(_INLINE_CODE_RE.sub(lambda mm: " " * len(mm.group(0)), line))
    return "\n".join(out)


def mask_code_spans_offset_preserving(text: str) -> str:
    """Return `text` with code-span content replaced by spaces — same length.

    Every character inside a fenced code block or inline code span becomes a
    space. The output has the SAME length as the input, character-for-character;
    `out[i]` corresponds to `text[i]`. Use this for callers that scan a masked
    copy for spans (e.g., wikilink matches) but apply replacements back to the
    original text by character offset.

    The newline structure is preserved exactly (no line collapse), so a fenced
    code line in the original survives in the mask as a blank line of the
    same character length.
    """
    out: list[str] = []
    fence: str | None = None
    lines = text.splitlines(keepends=True)
    for line in lines:
        # Split the line into body + trailing newline (if any).
        if line.endswith("\r\n"):
            body, eol = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, eol = line[:-1], "\n"
        else:
            body, eol = line, ""
        m = _FENCE_RE.match(body)
        if fence is None:
            if m:
                # Fence-opener line: the fence chars stay (so a re-scan would
                # still see the fence); content after the fence on the same
                # line is rare but preserved as spaces.
                fence = m.group(1)
                # The fence run itself stays; anything after it becomes spaces.
                fence_text = m.group(0)
                rest = body[len(fence_text):]
                out.append(fence_text + (" " * len(rest)) + eol)
                continue
            # Outside any fence: blank inline code spans with spaces.
            masked = _INLINE_CODE_RE.sub(lambda mm: " " * len(mm.group(0)), body)
            out.append(masked + eol)
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                # Fence-closer line: fence chars stay; rest becomes spaces.
                fence_text = m.group(0)
                rest = body[len(fence_text):]
                out.append(fence_text + (" " * len(rest)) + eol)
                fence = None
            else:
                # Inside a fence: every body char becomes a space; eol stays.
                out.append((" " * len(body)) + eol)
    return "".join(out)


def resolve_wikilink(
    target: str,
    base: Path,
    candidates: set[Path],
    *,
    wiki_root: Path | None = None,
) -> Path | None:
    """Resolve a wikilink target against a set of candidate page paths.

    Candidates-set adapter over `_model.resolve_wikilink` for callers that
    only have a `candidates` set and don't care about alias resolution
    (e.g. the promote link-rewriter — aliases are content metadata, not
    rewrite targets). Builds an alias-free `PageIndex` on the fly and
    returns just the resolved target path (or `None`).

    Callers that need alias resolution, frontmatter-error reporting, or
    the full attempt trace should use `_model.resolve_wikilink` directly
    with a properly-built `PageIndex` from `_model.build_page_index`.

    `wiki_root=None` is accepted for callers without a wiki root and
    disables the wiki-root pathful strategy (only base-relative + bare
    filename run); the promote rewriter always passes `wiki_root`.
    """
    from . import _model

    if wiki_root is None:
        # Pre-_model behavior: no wiki-root pathful strategy. Build an
        # alias-free index and use base as the implicit "root" only for
        # the bare-filename / base-relative case.
        wiki_root = base
    index = _model.PageIndex(
        pages=set(candidates),
        by_basename=_index_by_basename(candidates),
        by_alias={},
        duplicate_aliases={},
        frontmatter_errors={},
    )
    res = _model.resolve_wikilink(target, base, index, wiki_root=wiki_root)
    return res.target


def _index_by_basename(paths: set[Path] | Iterable[Path]) -> dict[str, list[Path]]:
    """Tiny helper: bucket paths by their basename for the back-compat
    wrapper. Mirrors the field in `_model.PageIndex`."""
    out: dict[str, list[Path]] = {}
    for p in paths:
        out.setdefault(p.name, []).append(p)
    return out


# ---------- Frontmatter (minimal) ----------

FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_text, body). frontmatter_text is None when absent.

    Pure split — no YAML parsing. Use parse_frontmatter for that. Fences are
    matched with `\\r?\\n` so a CRLF-terminated file (Windows / `autocrlf`
    checkout of the Obsidian wire format) is recognized like its LF twin —
    an LF-only match would silently treat it as having no frontmatter,
    dropping aliases and counting the metadata against the size cap.
    """
    if not text.startswith(("---\n", "---\r\n")):
        return None, text
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def strip_frontmatter(text: str) -> str:
    """Return `text` with the YAML frontmatter block (if any) removed.

    Convenience wrapper over `split_frontmatter` — returns just the body.
    Used for size-cap char counting (`_model.check_size` and the `_log`
    rotation path): frontmatter is metadata, not content, and is excluded
    from size caps.
    """
    _, body = split_frontmatter(text)
    return body


class FrontmatterStatus(Enum):
    ABSENT = "absent"
    OK = "ok"
    MALFORMED = "malformed"              # yaml.YAMLError
    NON_MAPPING = "non_mapping"          # parsed but top level is not a dict


@dataclass(frozen=True)
class FrontmatterResult:
    """Frontmatter parse with provenance.

    Distinguishes ABSENT from MALFORMED (broken YAML) so the audit can surface
    a malformed block as its own finding instead of treating it as no
    frontmatter. `parse_frontmatter` is the `.data`-only view of this same
    parse — one parser, two shapes.
    """
    status: FrontmatterStatus
    data: dict[str, Any] | None
    error_line: int | None
    error_message: str | None


def parse_frontmatter_result(text: str) -> FrontmatterResult:
    """Parse YAML frontmatter and report the outcome with provenance.

    Distinguishes ABSENT (no `---` block), OK (parsed to a dict), MALFORMED
    (`yaml.YAMLError`), and NON_MAPPING (parsed to something that isn't a dict
    — e.g. a top-level list). Error line/column are surfaced from PyYAML's
    `mark` when available. The single frontmatter parser; `parse_frontmatter`
    is its `.data` view.

    Supports the full YAML 1.1 safe subset. Empty frontmatter (`---\\n\\n---`)
    is OK with `{}`. A bare `---\\n---` (no newline before the closing fence)
    is NOT a frontmatter block (`FRONTMATTER_RE` needs the newline) and is
    ABSENT.
    """
    fm_text, _ = split_frontmatter(text)
    if fm_text is None:
        return FrontmatterResult(FrontmatterStatus.ABSENT, None, None, None)
    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        line = None
        mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
        if mark is not None and getattr(mark, "line", None) is not None:
            line = mark.line  # 0-based, relative to frontmatter content
        return FrontmatterResult(FrontmatterStatus.MALFORMED, None, line, str(e))
    if parsed is None:
        return FrontmatterResult(FrontmatterStatus.OK, {}, None, None)
    if not isinstance(parsed, dict):
        return FrontmatterResult(
            FrontmatterStatus.NON_MAPPING,
            None,
            None,
            f"top-level YAML is {type(parsed).__name__}, expected mapping",
        )
    return FrontmatterResult(FrontmatterStatus.OK, parsed, None, None)


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """The `.data` view of `parse_frontmatter_result`: the parsed mapping, or
    `None` when frontmatter is absent, malformed, or not a mapping.

    A `.data`-extracting wrapper over the single rich parser — no second YAML
    parse. Callers that need to distinguish absent from malformed use
    `parse_frontmatter_result`.
    """
    return parse_frontmatter_result(text).data


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

    The href is percent-decoded (`unquote`) before resolution: Obsidian
    emits a markdown link to a spaced filename as `my%20note.md`, the only
    valid spaced-destination form (a raw space ends the CommonMark
    destination), so `%20` must decode to a space to match the on-disk
    `my note.md`. `encode_markdown_href` is the producer-side inverse, so a
    rewritten link round-trips back through this resolver (producer=consumer).
    """
    if _is_external_href(href):
        return None
    base = containing_file.parent
    try:
        target = (base / unquote(href)).resolve()
    except (OSError, RuntimeError):
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


def encode_markdown_href(href: str) -> str:
    """Percent-encode a relative markdown href for emission into `[label](href)`.

    The producer-side inverse of the `unquote` in `resolve_markdown_link`: a
    space is the only character that breaks CommonMark destination parsing (a
    raw space ends the destination), so a rewritten href pointing at a spaced
    path (`my note/index.md`) must emit `%20` to stay a valid Obsidian markdown
    link that the resolver reads back to the same target. Encoding is limited to
    the space so a space-free href is returned byte-for-byte unchanged — no
    blast radius on the existing rewrites.
    """
    return href.replace(" ", "%20")


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
    if not isinstance(aliases, list):
        return []
    return [a for a in aliases if isinstance(a, str) and a]


_ALIASES_LINE_RE = re.compile(r"^aliases\s*:\s*(.*)$")
# Used by `frontmatter_add_alias` to locate the end of an `aliases:` block
# list so a new alias can be appended at the existing indent. Matches both
# indented (`  - foo`) and flush-left (`- foo`) item lines.
_BLOCK_LIST_ITEM_RE = re.compile(r"^(\s*)-\s*(.*)$")

# A value safe to emit as a YAML *plain* scalar in both block (`- x`) and flow
# (`[..., x]`) context: starts alphanumeric, then only alphanumerics, spaces,
# and `_ . -`. Anything else (notably the YAML indicators `: ` ` #` `,` `[]{}`)
# must be quoted so the consumer reads back the same value.
_SAFE_PLAIN_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]*$")


def _yaml_quote_alias(alias: str) -> str:
    """Render `alias` as a YAML scalar that round-trips through the consumer.

    `frontmatter_add_alias` writes the `aliases:` list as raw text; the consumer
    (`parse_frontmatter_aliases` → PyYAML) reads it back. A value carrying a YAML
    indicator therefore breaks producer=consumer unless it is quoted: a stem like
    `meeting: notes` emitted bare as `- meeting: notes` parses as a *mapping*
    (the alias is dropped), and `a, b` appended to an inline `[...]` list splits
    into two entries. Plain-safe values pass through unchanged (preserving the
    existing block/inline formatting and its tests); everything else becomes a
    double-quoted scalar with `\\` and `"` escaped — valid in both block and flow
    context, so one rendering serves every insertion branch below.
    """
    if _SAFE_PLAIN_ALIAS_RE.match(alias):
        return alias
    return '"' + alias.replace("\\", "\\\\").replace('"', '\\"') + '"'


def frontmatter_add_alias(text: str, alias: str) -> tuple[str, bool]:
    """Add `alias` to the frontmatter `aliases:` list. Returns (new_text, added).

    Case-insensitive no-op via `casefold()` — matches Obsidian's autocomplete
    and the wiki-level collision preflight in `space/promote.py`.

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

    rendered = _yaml_quote_alias(alias)
    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        new_fm = f"---\naliases:\n  - {rendered}\n---\n"
        return new_fm + text, True

    lines = fm_text.splitlines()
    for i, line in enumerate(lines):
        m = _ALIASES_LINE_RE.match(line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            new_inner = f"{inner}, {rendered}" if inner else rendered
            lines[i] = f"aliases: [{new_inner}]"
            break
        if rest and not rest.startswith("["):
            # Scalar form: convert to block list. Re-render the existing scalar
            # too — a quoted `"a: b"` strips to `a: b`, which would itself break
            # as a bare `- a: b` (mapping) without re-quoting.
            lines[i] = "aliases:"
            scalar = rest.strip("'\"")
            lines.insert(i + 1, f"  - {_yaml_quote_alias(scalar)}")
            lines.insert(i + 2, f"  - {rendered}")
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
        lines.insert(insert_at, f"{indent}- {rendered}")
        break
    else:
        lines.append("aliases:")
        lines.append(f"  - {rendered}")

    new_fm = "\n".join(lines)
    return f"---\n{new_fm}\n---\n{body}", True
