"""Unit tests for wiki_spaces.install: --bridge stdout flow + fail-fast on
missing skill sources."""

from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from wiki_spaces import install
from wiki_spaces._common import Harness


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = install.main(args)
    return rc, out.getvalue(), err.getvalue()


def test_bridge_cursor_emits_exact_file_content():
    """`--bridge cursor` must emit the packaged bridge file byte-for-byte. The
    user pipes this into their rules file via shell redirection; any mutation
    here would silently corrupt the rule snippet."""
    from wiki_spaces._common import data_root
    expected = (data_root() / "bridges" / install.BRIDGES["cursor"]).read_text(encoding="utf-8")
    rc, out, _ = _run(["--bridge", "cursor"])
    assert rc == 0
    assert out == expected


def test_bridge_windsurf_emits_exact_file_content():
    from wiki_spaces._common import data_root
    expected = (data_root() / "bridges" / install.BRIDGES["windsurf"]).read_text(encoding="utf-8")
    rc, out, _ = _run(["--bridge", "windsurf"])
    assert rc == 0
    assert out == expected


def test_bridge_unknown_key_rejected_by_argparse():
    """Argparse `choices=` enforces the bridge key whitelist before main()
    runs — invalid keys terminate via SystemExit, not return code."""
    with pytest.raises(SystemExit):
        _run(["--bridge", "bogus"])


def test_bridge_short_circuits_install_writes(tmp_path, monkeypatch):
    """`--bridge` returns before harness install logic runs; it must not
    touch the config file or harness skill dirs. Combined with --dry-run
    or any install flag, --bridge wins."""
    from wiki_spaces import _common
    fake_config = tmp_path / "absent-config"
    monkeypatch.setattr(_common, "CONFIG_PATH", fake_config)
    rc, out, _ = _run(["--dry-run", "--bridge", "cursor"])
    assert rc == 0
    assert out.startswith("---")
    assert not fake_config.exists()


def _empty_read_root(tmp_path: Path) -> Path:
    """A `read_root` with no skill/vendor source files at all — every required
    skill is missing, so install_harness should report fatal."""
    root = tmp_path / "empty-source"
    root.mkdir(exist_ok=True)
    return root


def test_install_harness_returns_fatal_when_source_missing(tmp_path):
    """The contract: every required skill source MUST exist. A missing source
    means the harness will not have a working wiki-spaces surface. This is the
    case codex flagged as the blocking bug — install previously returned 0
    silently while writing the config, leaving a useless install behind."""
    h = Harness(key="claude", detect=(), skills_dir=tmp_path / "claude-skills")
    h.skills_dir.mkdir()
    read_root = _empty_read_root(tmp_path)
    err = io.StringIO()
    with redirect_stderr(err):
        actions, had_fatal = install.install_harness(
            h, read_root, read_root, dry=True, copy=False, force=False
        )
    assert had_fatal is True
    # Each missing skill produces one stderr line, but no stdout actions.
    assert actions == []
    err_text = err.getvalue()
    assert "source missing" in err_text
    # All required skills surfaced (don't pin the exact set — verify count).
    assert err_text.count("source missing") >= 3


def test_install_main_exits_nonzero_on_missing_source(tmp_path, monkeypatch):
    """End-to-end: install.main() must propagate the fatal flag to its exit
    code so setup scripts can gate on it. Config is still written (so partial
    installs are at least self-describing for doctor to flag), but exit is 1."""
    # Point install at an empty read_root + a single test harness.
    from wiki_spaces import _common

    fake_skills = tmp_path / "claude-skills"
    fake_skills.mkdir()
    h = Harness(key="claude", detect=(tmp_path / "claude-marker",), skills_dir=fake_skills)
    (tmp_path / "claude-marker").mkdir()
    monkeypatch.setattr(install, "HARNESSES", (h,))
    monkeypatch.setattr(install, "_resolve_install_root",
                        lambda *, dry_run: (_empty_read_root(tmp_path), _empty_read_root(tmp_path)))
    monkeypatch.setattr(install, "_ensure_vendor_dev", lambda *, dry_run: None)
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "config")

    rc, out, err = _run([])
    assert rc == 1, "missing skill source must exit nonzero"
    assert "source missing" in err
    assert "completed with errors" in err
