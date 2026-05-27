"""Unit tests for _limits.py — size discipline primitives."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from wiki_spaces import _limits


# ---------- DEFAULTS / read_limits / cap_for ----------


def test_defaults_ordering():
    """Most-specific patterns first; broadest last.
    `log.archive-*.md` and `hot.md` share the log's 100K cap (archives
    are log overflow, hot.md is a user-owned scratchpad — neither is a
    generic content page) and are ordered before the broad `*.md` rule
    so the specific patterns win for those files."""
    patterns = [p for p, _ in _limits.DEFAULTS]
    assert patterns == ["index.md", "log.md", "log.archive-*.md", "hot.md", "*.md"]


def test_log_archive_default_cap_matches_log():
    """`log.archive-*.md` files inherit the log.md cap by default —
    rotations of a 100K log shouldn't be artificially blocked by the
    generic 15K content-page cap."""
    log_cap = next(c for p, c in _limits.DEFAULTS if p == "log.md")
    archive_cap = next(
        c for p, c in _limits.DEFAULTS if p == "log.archive-*.md"
    )
    assert archive_cap == log_cap


def test_hot_md_default_cap_matches_log():
    """hot.md is a user-owned convention file (scratchpad) — same category
    as log.md (user-owned, tools never rewrite). Both get 100K by default."""
    log_cap = next(c for p, c in _limits.DEFAULTS if p == "log.md")
    hot_cap = next(c for p, c in _limits.DEFAULTS if p == "hot.md")
    assert hot_cap == log_cap


def test_read_limits_returns_defaults_when_no_override(tmp_path):
    assert _limits.read_limits(tmp_path) == _limits.DEFAULTS


def test_read_limits_parses_user_table(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "# Size limits\n"
        "\n"
        "| Pattern         | Cap (chars) |\n"
        "|-----------------|-------------|\n"
        "| concepts/*.md   |        8000 |\n"
        "| projects/**/*.md|       20000 |\n"
    )
    result = _limits.read_limits(tmp_path)
    # User patterns come first, in file order.
    assert result[0] == ("concepts/*.md", 8000)
    assert result[1] == ("projects/**/*.md", 20000)
    # Defaults are appended after.
    assert ("index.md", 5000) in result
    assert ("*.md", 15000) in result


def test_read_limits_user_overrides_default(tmp_path):
    """If the user redeclares a default pattern, the user value wins and the
    default is dropped (not appended)."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern  | Cap (chars) |\n"
        "|----------|-------------|\n"
        "| index.md |        9999 |\n"
    )
    result = _limits.read_limits(tmp_path)
    assert ("index.md", 9999) in result
    assert ("index.md", 5000) not in result


def test_read_limits_skips_malformed_rows(tmp_path):
    """Header rows, separator rows, and unparseable cap values are skipped."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n"
        "|---|---|\n"
        "| valid.md | 1234 |\n"
        "| bad-cap.md | not-a-number |\n"
        "| negative.md | -5 |\n"
        "| zero.md | 0 |\n"
    )
    result = _limits.read_limits(tmp_path)
    user_patterns = [p for p, _ in result if p not in {p2 for p2, _ in _limits.DEFAULTS}]
    assert "valid.md" in user_patterns
    assert "bad-cap.md" not in user_patterns
    assert "negative.md" not in user_patterns
    assert "zero.md" not in user_patterns


def test_cap_for_basename_pattern_matches_any_depth(tmp_path):
    """A pattern without `/` matches against `path.name` at any depth."""
    limits = _limits.DEFAULTS
    assert _limits.cap_for(tmp_path / "index.md", tmp_path, limits) == 5000
    assert _limits.cap_for(
        tmp_path / "projects" / "foo" / "index.md", tmp_path, limits
    ) == 5000
    assert _limits.cap_for(
        tmp_path / "deep" / "nested" / "path" / "index.md", tmp_path, limits
    ) == 5000


def test_cap_for_pathful_pattern_matches_relative_posix(tmp_path):
    limits = [("concepts/*.md", 8000), *_limits.DEFAULTS]
    assert _limits.cap_for(tmp_path / "concepts" / "foo.md", tmp_path, limits) == 8000
    # `notes/foo.md` does NOT match `concepts/*.md`.
    assert _limits.cap_for(tmp_path / "notes" / "foo.md", tmp_path, limits) == 15000
    # Root `foo.md` does NOT match `concepts/*.md` either.
    assert _limits.cap_for(tmp_path / "foo.md", tmp_path, limits) == 15000


def test_cap_for_pathful_glob_non_recursive(tmp_path):
    """`concepts/*.md` matches one segment under `concepts/` only — NOT
    deeper paths. fnmatch's `*` matches `/` too; the path-segment-bounded
    `PurePosixPath.match` is what gives the expected non-recursive semantics."""
    limits = [("concepts/*.md", 8000), *_limits.DEFAULTS]
    # One-level (must match the user's 8000 cap)
    assert _limits.cap_for(tmp_path / "concepts" / "foo.md", tmp_path, limits) == 8000
    # Deeper path (must FALL THROUGH to the default *.md cap)
    assert (
        _limits.cap_for(tmp_path / "concepts" / "sub" / "foo.md", tmp_path, limits)
        == 15000
    )


def test_cap_for_pathful_glob_recursive(tmp_path):
    """`concepts/**/*.md` matches every depth under `concepts/`."""
    limits = [("concepts/**/*.md", 8000), *_limits.DEFAULTS]
    assert _limits.cap_for(tmp_path / "concepts" / "foo.md", tmp_path, limits) == 8000
    assert (
        _limits.cap_for(tmp_path / "concepts" / "sub" / "foo.md", tmp_path, limits)
        == 8000
    )
    assert (
        _limits.cap_for(
            tmp_path / "concepts" / "a" / "b" / "c" / "foo.md", tmp_path, limits
        )
        == 8000
    )


def test_user_pattern_beats_default_glob(tmp_path):
    """The core regression: a narrow user rule must win over the broad default."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern       | Cap (chars) |\n"
        "|---------------|-------------|\n"
        "| concepts/*.md |        8000 |\n"
    )
    limits = _limits.read_limits(tmp_path)
    cap = _limits.cap_for(tmp_path / "concepts" / "foo.md", tmp_path, limits)
    assert cap == 8000, (
        "Default *.md cap (15000) silently shadowed the user's narrower "
        f"concepts/*.md cap (8000); got cap={cap}. Limit ordering is wrong."
    )


def test_cap_for_root_md_matches_star_md_fallback(tmp_path):
    """Root-level `foo.md` falls through to the `*.md` default. This is the
    `**/*.md`-doesn't-match-root-files trap the fnmatch split fixes."""
    cap = _limits.cap_for(tmp_path / "foo.md", tmp_path, _limits.DEFAULTS)
    assert cap == 15000


def test_cap_for_log_md_matches_specific_default(tmp_path):
    cap = _limits.cap_for(tmp_path / "log.md", tmp_path, _limits.DEFAULTS)
    assert cap == 100000


# ---------- current_size / would_exceed ----------


def test_current_size_excludes_frontmatter(tmp_path):
    page = tmp_path / "foo.md"
    page.write_text(
        "---\n"
        "title: Foo\n"
        "tags: [a, b]\n"
        "---\n"
        "body content here\n"
    )
    # Body is "body content here\n" — 18 chars.
    assert _limits.current_size(page) == 18


def test_current_size_no_frontmatter(tmp_path):
    page = tmp_path / "foo.md"
    page.write_text("just body\n")
    assert _limits.current_size(page) == len("just body\n")


def test_current_size_missing_file_returns_zero(tmp_path):
    assert _limits.current_size(tmp_path / "no-such-file.md") == 0


def test_would_exceed_under_cap(tmp_path):
    page = tmp_path / "foo.md"
    over, projected, cap = _limits.would_exceed(
        page, "short body\n", tmp_path, _limits.DEFAULTS
    )
    assert not over
    assert projected == len("short body\n")
    assert cap == 15000  # *.md default


def test_would_exceed_over_cap(tmp_path):
    page = tmp_path / "foo.md"
    big = "x" * 16000
    over, projected, cap = _limits.would_exceed(
        page, big, tmp_path, _limits.DEFAULTS
    )
    assert over
    assert projected == 16000
    assert cap == 15000


def test_would_exceed_strips_frontmatter_from_projection(tmp_path):
    """Frontmatter doesn't count toward the projected size."""
    page = tmp_path / "foo.md"
    text = "---\n" + "k: v\n" * 100 + "---\n" + "body\n"
    over, projected, cap = _limits.would_exceed(page, text, tmp_path, _limits.DEFAULTS)
    assert projected == len("body\n")
    assert not over


def test_would_exceed_index_md_uses_5k_cap(tmp_path):
    page = tmp_path / "projects" / "foo" / "index.md"
    over, projected, cap = _limits.would_exceed(
        page, "x" * 6000, tmp_path, _limits.DEFAULTS
    )
    assert cap == 5000
    assert over


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
        _limits.append_log_with_rotation(log, _make_log_entry(1), cap=100000)


def test_append_under_cap_does_not_rotate(tmp_path):
    log = _make_log(tmp_path)
    for i in range(5):
        archive = _limits.append_log_with_rotation(log, _make_log_entry(i), cap=100000)
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
        _limits.append_log_with_rotation(log, e, cap=cap)

    # Force a rotation with the 12th entry.
    archive = _limits.append_log_with_rotation(log, entries[11], cap=cap)

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
        archive = _limits.append_log_with_rotation(log, e, cap=cap)
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
        _limits.append_log_with_rotation(log, e, cap=cap)
    # The very last entry must be in log.md.
    assert "value19" in log.read_text()


def _concurrent_worker(args):
    log_path_str, i = args
    _limits.append_log_with_rotation(Path(log_path_str), _make_log_entry(i), cap=100000)


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
    _limits.append_log_with_rotation(
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


# ---------- _md.strip_frontmatter (factored from split_frontmatter) ----------


def test_strip_frontmatter_returns_body_only():
    from wiki_spaces import _md

    text = "---\ntitle: x\n---\nbody here\n"
    assert _md.strip_frontmatter(text) == "body here\n"


def test_strip_frontmatter_no_frontmatter_returns_input():
    from wiki_spaces import _md

    assert _md.strip_frontmatter("just body\n") == "just body\n"
