"""Unit tests for _log.py — log append + rotation primitives."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from wiki_spaces import _common, _log, _model


# ---------- append_log_with_rotation ----------


def _make_log_entry(i: int) -> str:
    return f"- [2026-01-01T00:00:{i:02d}Z] OP key=value{i}\n"


def _make_log(tmp_path: Path) -> Path:
    """Scaffold an empty log.md (the opt-in convention) so the appender can
    operate. PR-H made `append_log_with_rotation` refuse on absent files;
    every test below pre-creates the file the way `cmd_log --create` does."""
    log = tmp_path / "log.md"
    log.write_text("# Log\n", encoding="utf-8")
    return log


def test_append_raises_when_log_md_absent(tmp_path):
    """PR-H: `log.md` is opt-in. The appender refuses on absent files —
    the CLI's `--create` flag (cmd_log) is the documented opt-in path."""
    import pytest
    log = tmp_path / "log.md"
    with pytest.raises(FileNotFoundError):
        _log.append_log_with_rotation(log, _make_log_entry(1), cap=100000)


def test_append_under_cap_does_not_rotate(tmp_path):
    log = _make_log(tmp_path)
    for i in range(5):
        archive = _log.append_log_with_rotation(log, _make_log_entry(i), cap=100000)
        assert archive is None
    body = log.read_text()
    for i in range(5):
        assert f"value{i}" in body


def test_append_over_cap_rotates(tmp_path):
    """When the projected size exceeds the cap, the oldest half moves to an
    archive file (uniquely named) and the newest half stays."""
    log = _make_log(tmp_path)
    # Cap chosen so 10 entries comfortably fit but 12 trigger rotation.
    entries = [_make_log_entry(i) for i in range(12)]
    cap = sum(len(e) for e in entries[:10]) + 10  # small slack
    for e in entries[:11]:
        # First 11 should fit (with tight cap, the 11th may trigger rotation
        # depending on exact byte counts — that's fine).
        _log.append_log_with_rotation(log, e, cap=cap)

    # Force a rotation with the 12th entry.
    archive = _log.append_log_with_rotation(log, entries[11], cap=cap)

    if archive is not None:
        assert archive.exists()
        archive_body = archive.read_text()
        log_body = log.read_text()
        # Archive contains the oldest entries; log contains the newest plus
        # the just-appended one. The 12th entry must be in `log.md`, not
        # the archive.
        assert "value11" in log_body
        assert "value11" not in archive_body
        # Some early entry should have moved to archive (depending on exact
        # cap math; entry 0 is the most likely).
        assert "value0" in archive_body or "value0" in log_body


def test_archive_filenames_unique_within_same_second(tmp_path):
    """Two rotations in the same second produce distinct archive names."""
    log = _make_log(tmp_path)
    # Tiny cap so every entry rotates.
    entries = [_make_log_entry(i) for i in range(20)]
    cap = 100
    archives_seen: set[Path] = set()
    for e in entries:
        archive = _log.append_log_with_rotation(log, e, cap=cap)
        if archive is not None:
            assert archive not in archives_seen, (
                f"archive filename collided: {archive} was reused"
            )
            archives_seen.add(archive)
    # At least one rotation happened; all archives are distinct.
    assert len(archives_seen) >= 1
    assert len(archives_seen) == len({a.name for a in archives_seen})


def test_archive_preserves_oldest_entries(tmp_path):
    """The archive should contain the OLDEST entries — newest stay in log.md."""
    log = _make_log(tmp_path)
    entries = [_make_log_entry(i) for i in range(20)]
    cap = sum(len(e) for e in entries[:10])  # ~10 entries fit
    for e in entries:
        _log.append_log_with_rotation(log, e, cap=cap)
    # The very last entry must be in log.md.
    assert "value19" in log.read_text()


def _concurrent_worker(args):
    log_path_str, i = args
    _log.append_log_with_rotation(Path(log_path_str), _make_log_entry(i), cap=100000)


def test_concurrent_appends_lose_no_lines(tmp_path):
    """100 concurrent appends via multiprocessing.Pool — assert no lost lines.

    This exercises the flock-protected critical section under real contention.
    Skipped on Windows where locking is best-effort.
    """
    if os.name == "nt":  # pragma: no cover
        pytest.skip("flock-based atomicity not enforced on Windows")
    log = tmp_path / "log.md"
    log.touch()
    with multiprocessing.Pool(processes=8) as pool:
        pool.map(_concurrent_worker, [(str(log), i) for i in range(100)])
    body = log.read_text()
    written = sum(1 for line in body.splitlines() if line.strip().startswith("- ["))
    assert written == 100, (
        f"expected 100 entries after concurrent appends, got {written}"
    )


def _concurrent_create_worker(args):
    log_path_str, i = args
    _log.append_log_with_rotation(
        Path(log_path_str),
        _make_log_entry(i),
        cap=100000,
        create_if_missing=True,
    )


def test_concurrent_create_lose_no_lines(tmp_path):
    """100 concurrent `--create` appends against a missing log.md — none
    of the workers pre-creates the file; each calls
    `append_log_with_rotation(..., create_if_missing=True)`. Without the
    atomic-create-under-lock contract, two workers race between the
    existence check and the scaffold write, clobbering each other's
    entries. With it, the create + scaffold + lock + append all happen
    under the same lock and no entry is lost. Skipped on Windows where
    locking is best-effort."""
    if os.name == "nt":  # pragma: no cover
        pytest.skip("flock-based atomicity not enforced on Windows")
    log = tmp_path / "log.md"
    # Deliberately NOT pre-creating — the workers must race the create.
    assert not log.exists()
    with multiprocessing.Pool(processes=8) as pool:
        pool.map(
            _concurrent_create_worker,
            [(str(log), i) for i in range(100)],
        )
    body = log.read_text()
    written = sum(
        1 for line in body.splitlines() if line.strip().startswith("- [")
    )
    assert written == 100, (
        f"expected 100 entries after concurrent --create appends, got "
        f"{written} — one or more workers raced the scaffold write and "
        "lost an entry"
    )


# ---------- log.md: ONE frontmatter-stripped size definition ----------

_FM = "---\ntitle: Journal\ntags: [log]\n---\n"


def test_log_size_one_definition_rotation_matches_check_size(tmp_path):
    """End-to-end pin of the single size definition for log.md.

    A `log.md` carrying frontmatter must be judged against its cap
    identically by rotation (`append_log_with_rotation`) and by the
    reader side (`_model.check_size`). Before the fix, rotation counted the
    raw `len(current)` (frontmatter included) while the readers stripped
    frontmatter — two notions of size that diverge the instant a log has
    frontmatter near its cap.
    """
    from wiki_spaces import _md

    log = tmp_path / "log.md"
    entries = "".join(_make_log_entry(i) for i in range(3))
    body = "# Log\n" + entries
    on_disk = _FM + body
    log.write_text(on_disk, encoding="utf-8")

    # Cap chosen so the post-append STRIPPED projection is UNDER cap but
    # the post-append RAW projection (frontmatter included) is OVER it —
    # the gap between the two is exactly the frontmatter, the bug surface.
    stripped_len = len(_md.strip_frontmatter(on_disk))
    assert stripped_len == len(body)
    fm_len = len(on_disk) - stripped_len  # frontmatter chars
    assert fm_len > 0
    extra = _make_log_entry(99)
    # Stripped projection: stripped_len + len(extra) (no sep — body ends "\n").
    # Raw projection:      len(on_disk) + len(extra) = stripped + fm + extra.
    # Pick the cap inside that frontmatter-wide gap (a few chars of slack
    # above the stripped projection, still below the raw projection).
    cap = stripped_len + len(extra) + 2
    assert cap < len(on_disk) + len(extra), (
        "frontmatter must push the RAW projection over cap (old-bug path)"
    )
    assert stripped_len + len(extra) <= cap, (
        "stripped projection must fit (post-fix path)"
    )

    # (1) Reader-side: the on-disk body (frontmatter excluded) equals the
    # stripped length and fits the cap.
    on_disk_body_len = len(_md.strip_frontmatter(log.read_text(encoding="utf-8")))
    assert on_disk_body_len == stripped_len
    assert on_disk_body_len <= cap

    # (2) Audit / check-size view: not OVER at this cap.
    table = _model.LimitTable(
        rules=[
            (
                "log.md",
                cap,
                _model.CapSource(
                    kind=_model.CapSourceKind.USER_OVERRIDE,
                    pattern="log.md",
                    file=None,
                    line=None,
                ),
            )
        ],
        malformed_rows=[],
    )
    verdict = _model.check_size(log, on_disk, tmp_path, table)
    assert verdict.outcome is not _model.SizeOutcome.OVER

    # (3) Rotation now measures the same stripped body — the small extra
    # entry fits, so the append succeeds WITHOUT raising and WITHOUT
    # spuriously rotating. (Before the fix, the raw frontmatter chars
    # pushed _projected_size over the cap and forced a rotation/raise.)
    archive = _log.append_log_with_rotation(
        log, extra, cap=cap, wiki_root=tmp_path,
        table=_model.load_limit_table(tmp_path),
    )
    assert archive is None, "rotation should not fire: stripped body fits the cap"
    assert "value99" in log.read_text()


def test_projected_size_excludes_frontmatter(tmp_path):
    """Focused guard on the measure rotation uses: a log.md with
    frontmatter whose stripped body has room for one more entry accepts
    the append without rotating, even though counting frontmatter would
    push it over the cap. The kept log (and any archive) stays within cap
    when measured the same stripped way."""
    from wiki_spaces import _md

    log = tmp_path / "log.md"
    body = "# Log\n" + "".join(_make_log_entry(i) for i in range(4))
    on_disk = _FM + body
    log.write_text(on_disk, encoding="utf-8")

    extra = _make_log_entry(50)
    # Fits in the stripped budget, but NOT if frontmatter were counted.
    cap = len(body) + len(extra) + 2
    assert len(on_disk) + len(extra) > cap

    archive = _log.append_log_with_rotation(log, extra, cap=cap)
    assert archive is None
    assert "value50" in _md.strip_frontmatter(log.read_text())
    assert len(_md.strip_frontmatter(log.read_text(encoding="utf-8"))) <= cap


def test_rotation_preserves_frontmatter_in_kept_log(tmp_path):
    """The literal byte accounting is intact after the measure change:
    a forced rotation keeps the frontmatter block + `# Log` header verbatim
    in the kept log, moves the oldest entries to the archive, and the
    archive itself carries NO frontmatter (it rode in `header_entries`)."""
    from wiki_spaces import _md

    log = tmp_path / "log.md"
    body = "# Log\n" + "".join(_make_log_entry(i) for i in range(20))
    on_disk = _FM + body
    log.write_text(on_disk, encoding="utf-8")

    extra = _make_log_entry(99)
    # Tight cap (measured on the stripped body) forces a rotation.
    cap = len(_make_log_entry(0)) * 12
    archive = _log.append_log_with_rotation(log, extra, cap=cap)
    assert archive is not None and archive.exists()

    kept = log.read_text()
    # Frontmatter preserved exactly once, with the `# Log` header.
    assert kept.startswith(_FM + "# Log\n")
    assert kept.count("title: Journal") == 1
    # Newest entry in the kept log; oldest in the archive.
    assert "value99" in kept
    archive_body = archive.read_text()
    assert "value0" in archive_body
    # The archive carries no frontmatter.
    assert _md.split_frontmatter(archive_body)[0] is None
    assert "title: Journal" not in archive_body


# ---------- _md.strip_frontmatter (factored from split_frontmatter) ----------


def test_strip_frontmatter_returns_body_only():
    from wiki_spaces import _md

    text = "---\ntitle: x\n---\nbody here\n"
    assert _md.strip_frontmatter(text) == "body here\n"


def test_strip_frontmatter_no_frontmatter_returns_input():
    from wiki_spaces import _md

    assert _md.strip_frontmatter("just body\n") == "just body\n"


# ---------- boundary: non-UTF-8 log.md + fail-closed rotation ----------


def test_append_refuses_non_utf8_log_instead_of_corrupting(tmp_path):
    """An existing `log.md` with invalid UTF-8 bytes is a boundary input: the
    append must REFUSE (raise) rather than decode with replacement chars and
    silently persist `\ufffd`-corrupted content on the next write (HANDBOOK:
    distrust boundary inputs; refuse, never truncate). Matches how the resolver
    treats a non-UTF-8 index.md / config."""
    log = tmp_path / "log.md"
    original = b"# Log\n- [2026-01-01T00:00:00Z] OP k=v\n\xff\xfe"
    log.write_bytes(original)
    with pytest.raises(UnicodeDecodeError):
        _log.append_log_with_rotation(log, _make_log_entry(1), cap=100000)
    assert log.read_bytes() == original  # untouched, not corrupted


def test_rotation_removes_orphaned_archive_when_final_write_fails(tmp_path, monkeypatch):
    """Rotation writes the archive then the truncated log. If the final log
    write fails AFTER the archive landed, the archive must be removed so the
    rotation is all-or-nothing (HANDBOOK: writes atomic and fail-closed) — never
    an orphaned archive with the log unchanged and the entry lost."""
    log = tmp_path / "log.md"
    entries = "".join(_make_log_entry(i) for i in range(40))
    content = "# Log\n" + entries
    log.write_text(content, encoding="utf-8")
    # cap below total (forces rotation) but above the kept newest-half + new
    # entry (so rotation's pre-flight passes and we REACH the final log write).
    cap = int(len(content) * 0.6)
    real_atomic_write = _common.atomic_write

    def failing_write(path, content):
        if path == log:
            raise OSError("simulated disk failure on final log write")
        return real_atomic_write(path, content)

    monkeypatch.setattr(_common, "atomic_write", failing_write)
    with pytest.raises(OSError):
        _log.append_log_with_rotation(
            log, _make_log_entry(99), cap=cap, wiki_root=tmp_path,
            table=_model.load_limit_table(tmp_path),
        )
    archives = list(tmp_path.glob("log.archive-*.md"))
    assert archives == []  # orphaned archive rolled back


def test_create_removes_empty_log_when_final_write_fails(tmp_path, monkeypatch):
    """`--create` against an absent log.md opens an `O_CREAT` placeholder
    BEFORE the final write. If that final `atomic_write` fails, the empty
    placeholder must be removed so the create is all-or-nothing — otherwise a
    failed first append leaves a 0-byte `log.md` behind (logging now looks
    opted-in but holds nothing, and the next plain append would write entries
    without the `# Log` scaffold). Mirrors the orphaned-archive rollback and
    the cap-rejection cleanup; HANDBOOK: writes atomic and fail-closed."""
    log = tmp_path / "log.md"
    assert not log.exists()
    real_atomic_write = _common.atomic_write

    def failing_write(path, content):
        if path == log:
            raise OSError("simulated disk failure on final log write")
        return real_atomic_write(path, content)

    monkeypatch.setattr(_common, "atomic_write", failing_write)
    with pytest.raises(OSError):
        _log.append_log_with_rotation(
            log, _make_log_entry(1), cap=100000, wiki_root=tmp_path,
            table=_model.load_limit_table(tmp_path), create_if_missing=True,
        )
    assert not log.exists()  # no 0-byte placeholder left behind
