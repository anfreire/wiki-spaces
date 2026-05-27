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


def test_install_with_no_harnesses_writes_repo(monkeypatch, tmp_path):
    """PR-N (§41): when zero harnesses are detected, install must still
    write the `repo` config key. Bridge-only users (Cursor, Windsurf,
    GitHub Copilot, Aider) need it so their rule snippets can resolve
    `<repo>/skills/...` and `<repo>/references/...`. Pre-PR-N, install
    returned early with exit 1 and `repo` stayed unset, which made
    `doctor` fail after the documented setup flow."""
    from wiki_spaces import _common
    # No harnesses at all — every detect path points at a missing marker.
    fake_harness = Harness(
        key="claude", detect=(tmp_path / "absent",),
        skills_dir=tmp_path / "absent-skills",
    )
    monkeypatch.setattr(install, "HARNESSES", (fake_harness,))
    monkeypatch.setattr(
        install, "_resolve_install_root",
        lambda *, dry_run: (tmp_path / "fake-read", tmp_path / "fake-write"),
    )
    monkeypatch.setattr(install, "_ensure_vendor_dev", lambda *, dry_run: None)
    cfg_path = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg_path)

    rc, out, _ = _run([])
    assert rc == 0  # No harness selected is no longer an error.
    assert "harnesses: none detected" in out
    # `repo` written to config — `doctor` now passes for bridge-only users.
    assert cfg_path.exists()
    assert "repo = " in cfg_path.read_text()


def test_bridge_warns_when_repo_unset(monkeypatch, tmp_path):
    """`--bridge cursor` emits a stderr warning when the `repo` key is unset
    in the config. The snippet still goes to stdout unchanged — only the
    warning is added. Bridge snippets reference the repo path, so a snippet
    emitted before any `wiki-spaces install` run would land in a broken state
    without the warning."""
    from wiki_spaces import _common
    # Point the config at a path with no `repo` key.
    cfg_path = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg_path)
    rc, out, err = _run(["--bridge", "cursor"])
    assert rc == 0
    # stdout still has the snippet (byte-for-byte; verified by the
    # existing `test_bridge_cursor_emits_exact_file_content`).
    assert out  # non-empty
    # stderr carries the warning.
    assert "warning" in err.lower()
    assert "repo" in err
    assert "unset" in err


def test_bridge_no_warning_when_repo_set(monkeypatch, tmp_path):
    """When `repo` is set, `--bridge` emits no warning — stderr stays clean."""
    from wiki_spaces import _common
    cfg_path = tmp_path / "config"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("repo = /tmp/fake\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg_path)
    rc, out, err = _run(["--bridge", "cursor"])
    assert rc == 0
    assert "warning" not in err.lower()


def test_install_unowned_dst_exits_nonzero_without_force(tmp_path, monkeypatch):
    """When a destination is user-owned (not wiki-spaces-installed) and
    `--force` is not passed, the per-skill action is recorded and the final
    exit code is nonzero — a silently-partial install must not exit 0."""
    from wiki_spaces import _common

    fake_skills = tmp_path / "claude-skills"
    fake_skills.mkdir()
    # Pre-create a user-owned (no OWNED_MARKER) directory at the install
    # destination — install must refuse without --force.
    for skill in ("ws-search", "ws-update", "ws-tend",
                  "obsidian-markdown", "obsidian-bases"):
        d = fake_skills / skill
        d.mkdir()
        (d / "SKILL.md").write_text("user content")  # not our marker

    h = Harness(key="claude", detect=(tmp_path / "claude-marker",), skills_dir=fake_skills)
    (tmp_path / "claude-marker").mkdir()
    # Provide a valid source tree so `source missing` doesn't kick in first.
    read_root = tmp_path / "src"
    for skill in ("ws-search", "ws-update", "ws-tend"):
        (read_root / "skills" / skill).mkdir(parents=True)
        (read_root / "skills" / skill / "SKILL.md").write_text("ok")
    for skill in ("obsidian-markdown", "obsidian-bases"):
        (read_root / "vendor" / "kepano" / skill).mkdir(parents=True)
        (read_root / "vendor" / "kepano" / skill / "SKILL.md").write_text("ok")
    monkeypatch.setattr(install, "HARNESSES", (h,))
    monkeypatch.setattr(install, "_resolve_install_root",
                        lambda *, dry_run: (read_root, read_root))
    monkeypatch.setattr(install, "_ensure_vendor_dev", lambda *, dry_run: None)
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "config")

    rc, out, err = _run([])
    assert rc == 1, "refused-unowned destinations must exit nonzero"
    assert "refusing to overwrite unowned" in err
