"""Log append + rotation for wiki-spaces.

`append_log_with_rotation` is the one write path that lives here: it appends a
`log.md` entry under a parent-directory lock, rotating the oldest half into a
timestamped archive when the projected size would breach the cap. Errors on
overflow, never silent truncation — an over-cap append is the producer's cue
to trim, not a license to cut.

Stdlib only. The limit table and its matcher are owned by `_model`
(`load_limit_table` / `cap_for_path`); `cmd_log` resolves the cap there and
hands the table in. Durable writes route through `_common.atomic_write`.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import _common, _md, _model
from ._common import fcntl


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


_WIN32_NOTICE_EMITTED = False


def _maybe_emit_no_lock_notice() -> None:
    """Emit a one-time stderr notice that locking is unavailable.

    `multiprocessing.Pool` workers are fresh processes so each may print
    once — documented as expected behavior, not a bug.
    """
    global _WIN32_NOTICE_EMITTED
    if _WIN32_NOTICE_EMITTED:
        return
    _WIN32_NOTICE_EMITTED = True
    sys.stderr.write(
        "warning: wiki-spaces locking unavailable (fcntl not present); "
        "concurrent writes may interleave.\n"
    )


def append_log_with_rotation(
    log_path: Path,
    entry: str,
    cap: int,
    *,
    wiki_root: Path | None = None,
    table: _model.LimitTable | None = None,
    create_if_missing: bool = False,
    initial_content: str = "# Log\n",
) -> Path | None:
    """Append `entry` to `log_path` with rotation under a single lock.

    Sequence (one `fcntl.flock` on the PARENT DIRECTORY covering all of it):
      1. Acquire exclusive lock on `log_path.parent` (stable inode — survives
         our own `unlink`/`os.replace` of `log_path` itself).
      2. (Optionally) create `log_path` atomically if `create_if_missing`.
      3. Read current content. If the file was just created (empty), the
         in-memory body starts from `initial_content`.
      4. If `len(current) + len(entry) > cap`, parse into entries, split at
         midpoint by entry count, write oldest half to a uniquely-named
         archive (`log.archive-<YYYYMMDD-HHMMSS>[-N].md`) atomically, keep
         newest half as the in-memory body.
      5. Compose the final body (kept content + the new entry, trailing
         newline ensured) and write `log.md` once via `_common.atomic_write`
         — scaffold, rotation-kept-half, and entry land together or not at
         all (crash-atomic, never a half-truncated log).
      6. Release lock.

    Returns the archive path when rotation happened, None otherwise.

    `log.md` must already exist (logging is opt-in per CONVENTIONS.md /
    log.md) unless `create_if_missing=True`. With the flag, the create +
    scaffold + lock + append all happen atomically under the same lock —
    two concurrent callers can't both clobber each other's scaffold and
    lose entries. Without the flag, raises `FileNotFoundError`.

    Locking note: the lock is on the PARENT DIRECTORY's inode, matching
    `_atomic_mutate_index`'s pattern. Locking the `log.md` inode itself
    (an earlier design) raced with the over-cap unlink path — a concurrent
    `--create` caller could acquire its own fd on the same inode, wait
    for the first caller's flock, and after that caller unlinked the file
    end up writing to an inode no longer linked at `log.md` (data lost).
    The parent-directory lock serializes the create / unlink / replace
    decisions, so a second caller always sees `log_path` in a coherent
    state (either present with the first caller's content, or absent and
    safe to scaffold afresh).

    POSIX-only locking. On Windows, the lock is best-effort (a one-time
    stderr notice per process); concurrent writes may interleave but the
    sequence itself still completes correctly within a single process.
    """
    if not log_path.is_file() and not create_if_missing:
        raise FileNotFoundError(
            f"{log_path} does not exist; caller must create it first "
            "(logging is opt-in)"
        )
    parent_fd = os.open(str(log_path.parent), os.O_RDONLY)
    try:
        if fcntl is not None:
            fcntl.flock(parent_fd, fcntl.LOCK_EX)
        else:
            _maybe_emit_no_lock_notice()

        # `O_CREAT | O_RDWR` is atomic create-or-open *under the parent
        # lock*. Two concurrent first-call sequences serialize: the
        # second caller sees the first caller's scaffold (and entries)
        # already on disk, or — if the first caller failed the cap check
        # and unlinked its empty file — the second caller's O_CREAT
        # creates a fresh inode at the (now-empty) `log_path`.
        flags = os.O_RDWR | (os.O_CREAT if create_if_missing else 0)
        pre_existed = log_path.is_file()
        fd = os.open(log_path, flags, 0o644)
        try:
            # Read the whole file, then close. Every subsequent mutation
            # (scaffold, rotation, append) lands through one final
            # `_common.atomic_write`, so the descriptor is only ever read —
            # the O_CREAT above just claims `log_path` under the lock.
            current_bytes = b""
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                current_bytes += chunk
        finally:
            os.close(fd)
        # Decode STRICTLY: a non-UTF-8 `log.md` is a boundary input, not
        # something to silently rewrite with `\ufffd` replacement chars and
        # persist on the next append (HANDBOOK: distrust boundary inputs;
        # refuse, never truncate). The UnicodeDecodeError propagates to
        # `cmd_log`, which reports it and refuses — matching how the resolver
        # treats a non-UTF-8 index.md / config.
        current = current_bytes.decode("utf-8")
        entry_normalized = entry if entry.endswith("\n") else entry + "\n"
        # All-or-nothing scaffold: if we just opened a missing file via
        # O_CREAT (the first --create call to win the lock), check
        # whether scaffold + new entry fits the cap BEFORE writing the
        # scaffold. Otherwise a cap that admits the scaffold but not
        # scaffold + entry would leave `log.md` with just `# Log\n` on
        # disk after the entry append is refused — partial state on the
        # first --create call.
        if create_if_missing and not current:
            # Measure the scaffold by its frontmatter-stripped body too, so
            # every cap comparison in this function uses the one stripped-body
            # rule (a no-op for the default `# Log\n` scaffold, but it keeps a
            # caller-supplied `initial_content` with frontmatter from
            # reintroducing the two-notions divergence). Matches _projected_size.
            if len(_md.strip_frontmatter(initial_content)) + len(entry_normalized) > cap:
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
            # The empty O_CREAT'd file stays empty on disk; `initial_content`
            # becomes the in-memory base and lands via the final atomic write.
            current = initial_content

        archive_path: Path | None = None

        # Project the post-write size including the conditional
        # separator newline. The write path adds `b"\n"` before the
        # entry when `current` lacks a trailing newline (see "Append
        # the new entry" below); the fit check must include that byte
        # so a too-tight cap with a header like `# Log` (no `\n`)
        # doesn't pass the check and then write 1 byte over cap.
        # The body is frontmatter-stripped to match the rest of the
        # size system (current_size / would_exceed / check_size), so a
        # log.md is judged against its cap identically everywhere.
        def _projected_size(curr: str) -> int:
            # Measure the frontmatter-stripped body so log.md is judged
            # by the SAME definition as current_size / would_exceed /
            # check_size (frontmatter is metadata, not content). The
            # literal writes below still emit the full `curr` (frontmatter
            # + `# Log` header + entries) — only this cap arithmetic
            # excludes the frontmatter, matching the rest of the system.
            # `sep` stays byte-based on the raw `curr`: the literal write
            # appends `b"\n"` based on the full on-disk content's trailing
            # char, and that separator falls in the body region anyway.
            sep = 1 if (curr and not curr.endswith("\n")) else 0
            return len(_md.strip_frontmatter(curr)) + sep + len(entry_normalized)

        if _projected_size(current) > cap:
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
                        if _projected_size(projected_kept) > cap:
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
                        if wiki_root is not None and table is not None:
                            archive_cap = _model.cap_for_path(
                                archive_path, wiki_root, table
                            ).cap
                            # Measure the archive by its stripped body too, so
                            # every cap comparison in the rotation path shares
                            # the one stripped-body rule. The joined oldest
                            # entries never carry frontmatter (it rides in
                            # `header_entries`, kept with the log), so this is a
                            # no-op today — but it keeps the reported number the
                            # one actually checked against the cap.
                            archive_chars = len(
                                _md.strip_frontmatter(archive_content)
                            )
                            if archive_chars > archive_cap:
                                raise ValueError(
                                    f"log rotation would write an over-cap "
                                    f"archive ({archive_chars} chars "
                                    f"> {archive_cap} cap for "
                                    f"{archive_path.name}); shrink the log "
                                    "or increase the log.archive-*.md cap "
                                    "in _meta/limits.md"
                                )
                        _common.atomic_write(archive_path, archive_content)
                        # Keep the newest half as the in-memory body; it lands
                        # with the new entry in the final atomic write below.
                        current = projected_kept

        # Last-resort fit check: with no rotation possible (single entry,
        # or pathological content). Reject rather than silently committing
        # an over-cap write.
        if _projected_size(current) > cap:
            raise ValueError(
                f"log entry too large to fit within cap ({cap} chars); "
                "rotate manually or increase the log.md cap in "
                "_meta/limits.md"
            )

        # Compose the final body and land the whole `log.md` mutation in one
        # crash-atomic replace (temp + fsync + os.replace + parent-dir fsync,
        # all still under the parent-directory flock). A separator newline is
        # inserted when the kept content doesn't already end in one.
        sep = "\n" if (current and not current.endswith("\n")) else ""
        try:
            _common.atomic_write(log_path, current + sep + entry_normalized)
        except OSError:
            # Fail-closed: the archive already landed on disk above. If this
            # final log write fails, remove the orphaned archive so rotation is
            # all-or-nothing (HANDBOOK: writes atomic and fail-closed) — never an
            # archive with the log unchanged and the entry lost. Then re-raise
            # for cmd_log to surface.
            if archive_path is not None:
                try:
                    archive_path.unlink()
                except OSError:
                    pass
            # Also remove the empty `O_CREAT` placeholder this call created: a
            # failed `--create` first write must not leave a 0-byte `log.md`
            # behind (logging would look opted-in but hold nothing, and the
            # next plain append would write entries with no `# Log` scaffold).
            # Mirrors the cap-rejection cleanup above; the placeholder is still
            # empty because `atomic_write` replaces via a temp file, so a
            # pre-replace failure never touched `log_path`.
            if not pre_existed:
                try:
                    if log_path.stat().st_size == 0:
                        log_path.unlink()
                except OSError:
                    pass
            raise
        return archive_path
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(parent_fd)
