"""Size discipline primitives for wiki-spaces.

Hard char caps at write time on the projected post-write size. Errors on
overflow, never silent truncation. The producer must consolidate, split, or
promote before the next write — predictability is the value.

This module is pure computation (no I/O in the predicate) so callers retain
control over the "shrinking write" escape hatch and any per-skill policy
decisions. The CLI commands `wiki-spaces space log` and (eventually) the size
checks in `wiki-update` wrap this module.

Stdlib only. Match semantics documented in `cap_for`.
"""

from __future__ import annotations

import fcntl
import fnmatch
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import _md


# Ordered list of `(glob_pattern, cap_chars)` — first-match wins.
# Order matters: most-specific patterns first, broadest last.
DEFAULTS: list[tuple[str, int]] = [
    ("index.md", 5000),
    ("log.md", 100000),
    # Archives are log overflow — they share the log's discipline, not
    # the generic 15K content-page cap. Without this, a rotation that
    # legitimately needs more than 15K worth of older entries would be
    # blocked by the default `*.md` rule.
    ("log.archive-*.md", 100000),
    ("*.md", 15000),
]


# ---------- Limit table parsing ----------


def _parse_limits_table(text: str) -> list[tuple[str, int]]:
    """Parse a markdown-table limits file. Returns user pairs in file order.

    Format (extra columns / whitespace tolerated; header rows skipped):

        | Pattern         | Cap (chars) |
        |-----------------|-------------|
        | concepts/*.md   |        8000 |

    Lines that don't parse as `| pattern | cap |` are silently skipped — the
    file is human-edited; minor formatting drift shouldn't break the parser.
    """
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        pattern, cap_str = cells[0], cells[1]
        # Skip the header row and any separator row (`---`).
        if not pattern or pattern.lower() in {"pattern"}:
            continue
        if set(pattern) <= set("-: "):
            continue
        try:
            cap = int(cap_str.replace(",", "").replace("_", "").strip())
        except ValueError:
            continue
        if cap <= 0:
            continue
        if pattern in seen:
            continue
        seen.add(pattern)
        out.append((pattern, cap))
    return out


def read_limits(wiki_root: Path) -> list[tuple[str, int]]:
    """Return the effective limit table for `wiki_root`.

    Reads `<wiki_root>/_meta/limits.md` if present. User-defined patterns
    appear FIRST in their declared order; `DEFAULTS` patterns are appended
    afterwards (filtered to remove any pattern the user already declared).
    First-match precedence means a user's `concepts/*.md` rule wins over the
    broader `*.md` default — narrow user rules can't be silently shadowed.
    """
    limits_path = wiki_root / "_meta" / "limits.md"
    if not limits_path.is_file():
        return list(DEFAULTS)
    try:
        text = limits_path.read_text(encoding="utf-8")
    except OSError:
        return list(DEFAULTS)
    user_pairs = _parse_limits_table(text)
    user_patterns = {p for p, _ in user_pairs}
    return user_pairs + [(p, c) for p, c in DEFAULTS if p not in user_patterns]


# ---------- Cap lookup ----------


def _path_glob_match(rel: str, pattern: str) -> bool:
    """Path-segment-bounded glob match.

    Both `rel` and `pattern` are posix-style strings. Splits on `/` and walks
    segments alongside:
    - `**` matches zero or more whole segments.
    - Any other segment matches one path segment, with `*`, `?`, and `[…]`
      handled via `fnmatch.fnmatchcase` (so `*` does not cross `/`).

    This gives `concepts/*.md` non-recursive semantics (does NOT match
    `concepts/sub/foo.md`) and `concepts/**/*.md` recursive semantics
    (matches at any depth, including zero extra segments). Works on every
    supported Python; doesn't depend on `PurePath.full_match` (3.13+).
    """
    return _match_path_segs(rel.split("/"), pattern.split("/"))


def _match_path_segs(rel: list[str], pat: list[str]) -> bool:
    if not pat:
        return not rel
    head = pat[0]
    if head == "**":
        # Consume zero or more rel segments before continuing.
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


def cap_for(path: Path, wiki_root: Path, limits: list[tuple[str, int]]) -> int:
    """Walk `limits` in order; return the first cap whose glob matches `path`.

    Match semantics:
    - A pattern containing `/` matches against `path.relative_to(wiki_root).as_posix()`
      via `_path_glob_match`, which is path-segment bounded — `concepts/*.md`
      matches `concepts/foo.md` but NOT `concepts/sub/foo.md`. Use `**` for
      recursive matching: `concepts/**/*.md` matches every depth under
      `concepts/`, including `concepts/foo.md` itself.
    - A pattern without `/` matches against `path.name` via `fnmatch.fnmatch`
      — basename only (e.g. `index.md` matches `<root>/index.md`,
      `<root>/projects/foo/index.md`, and any other `index.md` at any depth;
      `*.md` matches every `.md` basename).

    This split fixes the `**/*.md`-doesn't-match-root-files trap that plain
    glob semantics would create. Falls back to a very large cap (effectively
    unbounded) if nothing matches — should never happen in practice given the
    `*.md` default, but defensive.
    """
    try:
        rel = path.relative_to(wiki_root).as_posix()
    except ValueError:
        rel = path.as_posix()
    name = path.name
    for pattern, cap in limits:
        if "/" in pattern:
            if _path_glob_match(rel, pattern):
                return cap
        else:
            if fnmatch.fnmatch(name, pattern):
                return cap
    return 2**31


# ---------- Char counting + cap check ----------


def current_size(path: Path) -> int:
    """Return the char count of `path`'s body (frontmatter excluded).

    Reads the file. Returns 0 if the file is missing or unreadable; that's
    the right answer for "what would a NEW write to this path project against
    its cap" — no existing content to compare against.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(_md.strip_frontmatter(text))


def would_exceed(
    path: Path,
    projected_text: str,
    wiki_root: Path,
    limits: list[tuple[str, int]],
) -> tuple[bool, int, int]:
    """Return `(over_cap, projected_chars, cap)` for a planned write.

    Pure computation — does NOT read the file. The caller supplies
    `projected_text` (the FULL post-write content for a Write, or the FULL
    post-edit content for an Edit), and this function returns whether the
    projected size exceeds the matched cap.

    `projected_chars` is `len(strip_frontmatter(projected_text))` — frontmatter
    is metadata, not content. The "is this a shrinking write?" decision lives
    at the caller (compare against `current_size(path)`); shrinking writes that
    still exceed the cap are the only escape hatch from legacy bloat and
    require the caller to make that policy choice.
    """
    projected_chars = len(_md.strip_frontmatter(projected_text))
    cap = cap_for(path, wiki_root, limits)
    return (projected_chars > cap, projected_chars, cap)


# ---------- Log rotation + append ----------


_LOG_ENTRY_RE = re.compile(r"^- \[")


def _split_log_entries(text: str) -> list[str]:
    """Split a `log.md` body into entries. Each entry begins with `- [`.

    Lines that don't match (blank lines, headings, free-form notes) are
    attached to the preceding entry as continuation — entries are kept whole,
    never cut mid-content.
    """
    entries: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if _LOG_ENTRY_RE.match(line):
            if current:
                entries.append("".join(current))
            current = [line]
        else:
            if current:
                current.append(line)
            else:
                # Pre-amble before the first entry — preserve as a "header"
                # entry that gets kept in `log.md` on rotation (it usually
                # holds the `# Log` heading).
                current = [line]
    if current:
        entries.append("".join(current))
    return entries


def _unique_archive_path(log_path: Path, when: datetime) -> Path:
    """Return an unused archive path next to `log_path`.

    Format: `log.archive-<YYYYMMDD-HHMMSS>.md`. If a file already exists at
    that name (two rotations in the same second), append `-<n>` and increment
    until unique. Guarantees no archive overwrites another.
    """
    stem = log_path.stem  # "log"
    ts = when.strftime("%Y%m%d-%H%M%S")
    base = log_path.with_name(f"{stem}.archive-{ts}.md")
    if not base.exists():
        return base
    n = 1
    while True:
        candidate = log_path.with_name(f"{stem}.archive-{ts}-{n}.md")
        if not candidate.exists():
            return candidate
        n += 1


def _atomic_write(path: Path, content: str) -> None:
    """Write `content` to `path` via tempfile + `os.replace`."""
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_WIN32_NOTICE_EMITTED = False


def _maybe_emit_win32_notice() -> None:
    """Emit a one-time stderr notice on Windows that locking is best-effort.

    `multiprocessing.Pool` workers are fresh processes so each may print
    once — documented as expected behavior, not a bug.
    """
    global _WIN32_NOTICE_EMITTED
    if _WIN32_NOTICE_EMITTED:
        return
    _WIN32_NOTICE_EMITTED = True
    sys.stderr.write(
        "warning: wiki-spaces locking is best-effort on Windows; "
        "concurrent writes may interleave. POSIX recommended for atomicity.\n"
    )


def append_log_with_rotation(
    log_path: Path,
    entry: str,
    cap: int,
    *,
    wiki_root: Path | None = None,
    limits: list[tuple[str, int]] | None = None,
    create_if_missing: bool = False,
    initial_content: str = "# Log\n",
) -> Path | None:
    """Append `entry` to `log_path` with rotation under a single lock.

    Sequence (one `fcntl.flock` covering all of it):
      1. (Optionally) create `log_path` atomically if `create_if_missing`.
      2. Acquire exclusive lock on `log_path`.
      3. Read current content. If the file was just created (empty), write
         `initial_content` first.
      4. If `len(current) + len(entry) > cap`, parse into entries, split at
         midpoint by entry count, write oldest half to a uniquely-named
         archive (`log.archive-<YYYYMMDD-HHMMSS>[-N].md`) atomically, keep
         newest half in `log.md`.
      5. Append `entry` to `log.md` (ensuring trailing newline).
      6. Release lock.

    Returns the archive path when rotation happened, None otherwise.

    `log.md` must already exist (logging is opt-in per CONVENTIONS.md /
    log.md) unless `create_if_missing=True`. With the flag, the create +
    scaffold + lock + append all happen atomically under the same lock —
    two concurrent callers can't both clobber each other's scaffold and
    lose entries. Without the flag, raises `FileNotFoundError`.

    POSIX-only locking. On Windows, the lock is best-effort (a one-time
    stderr notice per process); concurrent writes may interleave but the
    sequence itself still completes correctly within a single process.
    """
    if not log_path.is_file() and not create_if_missing:
        raise FileNotFoundError(
            f"{log_path} does not exist; caller must create it first "
            "(logging is opt-in)"
        )
    # `O_CREAT | O_RDWR` is atomic create-or-open. With flock taken
    # immediately, a concurrent first-call sequence serializes — the
    # second caller sees the first caller's scaffold (and entries)
    # already in place. Without this, two `--create` calls race between
    # the existence check and `write_text`, losing one caller's entry.
    flags = os.O_RDWR | (os.O_CREAT if create_if_missing else 0)
    pre_existed = log_path.is_file()
    fd = os.open(log_path, flags, 0o644)
    try:
        if sys.platform != "win32":
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            _maybe_emit_win32_notice()

        current_bytes = b""
        offset = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            current_bytes += chunk
            offset += len(chunk)
        current = current_bytes.decode("utf-8", errors="replace")
        entry_normalized = entry if entry.endswith("\n") else entry + "\n"
        # All-or-nothing scaffold: if we just opened a missing file via
        # O_CREAT (the first --create call to win the lock), check
        # whether scaffold + new entry fits the cap BEFORE writing the
        # scaffold. Otherwise a cap that admits the scaffold but not
        # scaffold + entry would leave `log.md` with just `# Log\n` on
        # disk after the entry append is refused — partial state on the
        # first --create call.
        if create_if_missing and not current:
            if len(initial_content) + len(entry_normalized) > cap:
                # Unlink the freshly-created empty file so the next
                # call doesn't see "log.md exists" and skip the
                # scaffold step.
                try:
                    if not pre_existed and log_path.stat().st_size == 0:
                        log_path.unlink()
                except OSError:
                    pass
                raise ValueError(
                    f"log scaffold + first entry too large to fit "
                    f"within cap ({cap} chars); shrink the entry or "
                    "increase the log.md cap in _meta/limits.md"
                )
            os.write(fd, initial_content.encode("utf-8"))
            current = initial_content

        archive_path: Path | None = None

        if len(current) + len(entry_normalized) > cap:
            entries = _split_log_entries(current)
            if len(entries) >= 2:
                # Header (everything before the first `- [` entry) stays with
                # the kept half so the file remains well-formed.
                first_entry_idx = next(
                    (i for i, e in enumerate(entries) if e.lstrip().startswith("- [")),
                    None,
                )
                if first_entry_idx is None:
                    # Pathological case: no real entries, but content overflows.
                    # Fall through without rotation; the append still happens.
                    pass
                else:
                    header_entries = entries[:first_entry_idx]
                    real_entries = entries[first_entry_idx:]
                    midpoint = len(real_entries) // 2
                    if midpoint > 0:
                        # PRE-FLIGHT: refuse BEFORE writing the archive or
                        # truncating the log if rotation can't satisfy the
                        # cap. Without this check, a too-large entry would
                        # commit the archive write + truncation, then raise
                        # at the post-rotation fit check below — leaving the
                        # rotation half-applied (archive on disk, log
                        # truncated, no new entry). "Errors on overflow,
                        # never silent truncation, never partial mutation."
                        projected_kept = (
                            "".join(header_entries)
                            + "".join(real_entries[midpoint:])
                        )
                        if (
                            len(projected_kept) + len(entry_normalized) > cap
                        ):
                            raise ValueError(
                                f"log entry too large to fit within cap "
                                f"({cap} chars) even after rotation would "
                                "drop the oldest half; shrink the entry, "
                                "rotate manually, or increase the log.md "
                                "cap in _meta/limits.md"
                            )
                        archive_path = _unique_archive_path(
                            log_path, datetime.now(timezone.utc)
                        )
                        archive_content = "".join(real_entries[:midpoint])
                        # Pre-flight the archive write against its own cap
                        # (matched via the same `_meta/limits.md` config —
                        # `log.archive-*.md` carries the same 100K cap as
                        # `log.md` by default). Without this check, a
                        # pathological rotation could write an over-cap
                        # archive — a framework write that the next
                        # `space audit` would flag. v1 contract: every
                        # framework write enforces its cap.
                        if wiki_root is not None and limits is not None:
                            archive_cap = cap_for(
                                archive_path, wiki_root, limits
                            )
                            if len(archive_content) > archive_cap:
                                raise ValueError(
                                    f"log rotation would write an over-cap "
                                    f"archive ({len(archive_content)} chars "
                                    f"> {archive_cap} cap for "
                                    f"{archive_path.name}); shrink the log "
                                    "or increase the log.archive-*.md cap "
                                    "in _meta/limits.md"
                                )
                        _atomic_write(archive_path, archive_content)
                        kept = projected_kept
                        # Truncate + rewrite log_path inside the lock.
                        os.lseek(fd, 0, os.SEEK_SET)
                        os.ftruncate(fd, 0)
                        os.write(fd, kept.encode("utf-8"))
                        current = kept

        # Last-resort fit check: with no rotation possible (single entry,
        # or pathological content). Reject rather than silently committing
        # an over-cap write.
        if len(current) + len(entry_normalized) > cap:
            raise ValueError(
                f"log entry too large to fit within cap ({cap} chars); "
                "rotate manually or increase the log.md cap in "
                "_meta/limits.md"
            )

        # Append the new entry.
        if current and not current.endswith("\n"):
            os.write(fd, b"\n")
        os.write(fd, entry_normalized.encode("utf-8"))
        os.fsync(fd)
        return archive_path
    finally:
        try:
            if sys.platform != "win32":
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
