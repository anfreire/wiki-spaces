"""Tests for wiki_spaces.install: the hub + per-harness alias model.

Two layers:
- In-process unit tests drive `_can_overwrite_skill`, `_install_skill_target`,
  `_install_hub`, and `_install_harness_aliases` directly with explicit tmp_path
  roots/destinations (no frozen-HOME problem — paths are arguments).
- Subprocess fake-HOME tests drive `install` end to end against an isolated
  home (see `tests.conftest.run_cli_subprocess` for why a subprocess is the
  only correct isolation here).
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

from wiki_spaces import _common, install
from wiki_spaces._common import (
    OWNED_MARKER,
    Harness,
)

from tests.conftest import (
    ALL_SKILL_NAMES,
    KEPANO_SKILL_NAMES,
    WIKI_SKILL_NAMES,
    run_cli_subprocess,
    seed_fake_home,
    seed_source_tree,
)


def _alias_harness(key: str, alias_dir: Path) -> Harness:
    return Harness(
        key=key,
        detect=(),
        reads_hub=False,
        alias_dirs=(alias_dir,),
        source_url="https://example.test/skills",
    )


# ---------------------------------------------------------------------------
# _can_overwrite_skill — ownership gate, exercised on every destination shape.
# ---------------------------------------------------------------------------


def test_can_overwrite_skill_allows_missing_destination(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    verdict = install._can_overwrite_skill(tmp_path / "absent", src)
    assert verdict is install._OverwriteVerdict.MISSING
    assert verdict.safe


def test_can_overwrite_skill_allows_symlink_pointing_at_expected_src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.symlink_to(src)
    verdict = install._can_overwrite_skill(dst, src)
    assert verdict is install._OverwriteVerdict.OWNED_SYMLINK
    assert verdict.safe


def test_can_overwrite_skill_refuses_symlink_to_foreign_target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    dst = tmp_path / "dst"
    dst.symlink_to(foreign)
    verdict = install._can_overwrite_skill(dst, src)
    assert verdict is install._OverwriteVerdict.FOREIGN_SYMLINK
    assert not verdict.safe


def test_can_overwrite_skill_allows_owned_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / OWNED_MARKER).write_text("ours")
    verdict = install._can_overwrite_skill(dst, src)
    assert verdict is install._OverwriteVerdict.OWNED_COPY
    assert verdict.safe


def test_can_overwrite_skill_refuses_unmarked_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "SKILL.md").write_text("user content")
    verdict = install._can_overwrite_skill(dst, src)
    assert verdict is install._OverwriteVerdict.UNMARKED_DIR
    assert not verdict.safe


def test_can_overwrite_skill_refuses_plain_file(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.write_text("user file")
    verdict = install._can_overwrite_skill(dst, src)
    assert verdict is install._OverwriteVerdict.PLAIN_FILE
    assert not verdict.safe


# ---------------------------------------------------------------------------
# _install_hub / _install_harness_aliases / _install_skill_target — fatal paths
# and the copy fallback, all with explicit roots so HOME stays irrelevant.
# ---------------------------------------------------------------------------


def test_install_hub_reports_fatal_when_every_source_missing(tmp_path):
    empty = tmp_path / "empty-source"
    empty.mkdir()
    err = io.StringIO()
    with redirect_stderr(err):
        actions, had_fatal = install._install_hub(
            empty, empty, dry=True, copy=False, force=False
        )
    assert had_fatal is True
    assert actions == []
    assert err.getvalue().count("source missing") == len(ALL_SKILL_NAMES)


def test_install_harness_aliases_reports_fatal_when_source_missing(tmp_path):
    empty = tmp_path / "empty-source"
    empty.mkdir()
    h = _alias_harness("claude", tmp_path / "claude" / "skills")
    err = io.StringIO()
    with redirect_stderr(err):
        actions, had_fatal = install._install_harness_aliases(
            h, empty, empty, dry=True, copy=False, force=False
        )
    assert had_fatal is True
    assert actions == []
    assert err.getvalue().count("source missing") == len(ALL_SKILL_NAMES)


def test_install_harness_aliases_dry_run_plans_every_skill(tmp_path):
    read_root = seed_source_tree(tmp_path / "src")
    alias_dir = tmp_path / "claude" / "skills"
    h = _alias_harness("claude", alias_dir)
    actions, had_fatal = install._install_harness_aliases(
        h, read_root, read_root, dry=True, copy=False, force=False
    )
    assert had_fatal is False
    assert len(actions) == len(ALL_SKILL_NAMES)
    assert all("would" in line for line in actions)
    assert not alias_dir.exists()


def test_install_harness_aliases_copy_fallback_writes_copies_and_owned_marker(
    tmp_path, monkeypatch
):
    read_root = seed_source_tree(tmp_path / "src")
    alias_dir = tmp_path / "claude" / "skills"
    h = _alias_harness("claude", alias_dir)

    def _no_symlink(*args, **kwargs):
        raise OSError("symlink unsupported on this platform")

    monkeypatch.setattr(_common.os, "symlink", _no_symlink)

    actions, had_fatal = install._install_harness_aliases(
        h, read_root, read_root, dry=False, copy=False, force=False
    )
    assert had_fatal is False
    assert all("copy" in line for line in actions)
    for skill in ALL_SKILL_NAMES:
        dst = alias_dir / skill
        assert dst.is_dir() and not dst.is_symlink()
        assert (dst / "SKILL.md").read_text() == "ok"
        assert (dst / OWNED_MARKER).is_file()


def test_install_skill_target_refuses_unowned_without_force(tmp_path):
    read_root = seed_source_tree(tmp_path / "src")
    dst = tmp_path / "dst" / "ws-search"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("user content")
    err = io.StringIO()
    with redirect_stderr(err):
        action, failed = install._install_skill_target(
            "claude", "ws-search", dst, read_root, read_root,
            dry=True, copy=False, force=False,
        )
    assert failed is True
    assert action is None
    assert "refusing to overwrite unmarked-directory" in err.getvalue()


def test_install_skill_target_force_overrides_unowned(tmp_path):
    read_root = seed_source_tree(tmp_path / "src")
    dst = tmp_path / "dst" / "ws-search"
    dst.mkdir(parents=True)
    (dst / "SKILL.md").write_text("user content")
    action, failed = install._install_skill_target(
        "claude", "ws-search", dst, read_root, read_root,
        dry=True, copy=False, force=True,
    )
    assert failed is False
    assert action is not None and "would" in action


# ---------------------------------------------------------------------------
# main() argument validation — in-process, no writes.
# ---------------------------------------------------------------------------


def test_main_rejects_unknown_harness_key(capsys):
    rc = install.main(["--harness", "bogus", "--dry-run"])
    assert rc == 2
    assert "Unknown --harness key(s): bogus" in capsys.readouterr().err


def _hub_harness(key: str) -> Harness:
    return Harness(
        key=key,
        detect=(),
        reads_hub=True,
        alias_dirs=(),
        source_url="https://example.test/skills",
    )


def _wire_main(monkeypatch, tmp_path, *, harnesses, present=True):
    read_root = seed_source_tree(tmp_path / "src")
    hub_dir = tmp_path / "agents" / "skills"
    cfg = tmp_path / "config"
    monkeypatch.setattr(install, "_ensure_vendor_dev", lambda *, dry_run: None)
    monkeypatch.setattr(
        install, "_resolve_install_root", lambda *, dry_run: (read_root, read_root)
    )
    monkeypatch.setattr(install, "HARNESSES", harnesses)
    monkeypatch.setattr(install, "COMMON_SKILLS_DIR", hub_dir)
    monkeypatch.setattr(install, "harness_present", lambda h: present)
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    monkeypatch.setattr(install, "CONFIG_PATH", cfg)
    return hub_dir, cfg


def test_main_dry_run_all_plans_without_writing(tmp_path, monkeypatch, capsys):
    hub_dir, cfg = _wire_main(
        monkeypatch, tmp_path,
        harnesses=(_hub_harness("codex"), _alias_harness("claude", tmp_path / "claude")),
    )
    rc = install.main(["--dry-run", "--all"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert "would" in out
    assert not hub_dir.exists()
    assert not cfg.exists()


def test_main_all_writes_hub_alias_and_repo_config(tmp_path, monkeypatch, capsys):
    alias_dir = tmp_path / "claude" / "skills"
    hub_dir, cfg = _wire_main(
        monkeypatch, tmp_path,
        harnesses=(_hub_harness("codex"), _alias_harness("claude", alias_dir)),
    )
    rc = install.main(["--all"])
    assert rc == 0, capsys.readouterr().out
    for skill in ALL_SKILL_NAMES:
        assert (hub_dir / skill).exists()
        assert (alias_dir / skill).exists()
    assert "repo = " in cfg.read_text()


def test_main_no_harness_detected_still_writes_hub_and_repo(tmp_path, monkeypatch, capsys):
    hub_dir, cfg = _wire_main(
        monkeypatch, tmp_path,
        harnesses=(_hub_harness("codex"),),
        present=False,
    )
    rc = install.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "harnesses: none detected" in out
    # Hub-once is the v2 default model — written even when no harness is
    # detected, so a later-installed hub-reader (codex/gemini/...) finds
    # the skills already in place.
    assert hub_dir.is_dir()
    for skill in ALL_SKILL_NAMES:
        assert (hub_dir / skill).exists()
    assert "repo = " in cfg.read_text()


# ---------------------------------------------------------------------------
# Subprocess fake-HOME end-to-end coverage of the documented install flow.
# ---------------------------------------------------------------------------


def _hub_dir(home: Path) -> Path:
    return home / ".agents" / "skills"


def test_install_all_writes_hub_once_and_claude_kiro_aliases(tmp_path):
    home = seed_fake_home(tmp_path / "home")
    result = run_cli_subprocess(["install", "--all"], home)
    assert result.returncode == 0, result.stderr
    assert "hub: written" in result.stdout

    for skill in ALL_SKILL_NAMES:
        assert (_hub_dir(home) / skill).is_symlink()
        assert (home / ".claude" / "skills" / skill).is_symlink()
        assert (home / ".kiro" / "skills" / skill).is_symlink()

    # Hub-reading harnesses get served from the hub — never their own alias dir.
    assert not (home / ".gemini" / "skills").exists()
    assert not (home / ".codex" / "skills").exists()
    assert not (home / ".config" / "opencode" / "skills").exists()
    assert not (home / ".copilot" / "skills").exists()
    assert not (home / ".cursor" / "skills").exists()


def test_install_refuses_foreign_hub_symlink_then_force_overrides(tmp_path):
    home = seed_fake_home(tmp_path / "home")
    hub = _hub_dir(home)
    hub.mkdir(parents=True)
    foreign = tmp_path / "foreign-skill"
    foreign.mkdir()
    (hub / "ws-search").symlink_to(foreign)

    refused = run_cli_subprocess(["install", "--all"], home)
    assert refused.returncode == 1
    assert "refusing to overwrite foreign-symlink" in refused.stderr
    assert (hub / "ws-search").resolve() == foreign.resolve()

    forced = run_cli_subprocess(["install", "--all", "--force"], home)
    assert forced.returncode == 0, forced.stderr
    assert (hub / "ws-search").resolve() != foreign.resolve()
    assert (hub / "ws-search").resolve().name == "ws-search"


def test_install_single_non_hub_harness_skips_hub(tmp_path):
    home = seed_fake_home(tmp_path / "home")
    result = run_cli_subprocess(["install", "--harness", "claude"], home)
    assert result.returncode == 0, result.stderr
    assert "hub: skipped (no hub-reading harness selected)" in result.stdout
    assert not _hub_dir(home).exists()
    for skill in ALL_SKILL_NAMES:
        assert (home / ".claude" / "skills" / skill).is_symlink()


def test_install_explicit_harness_overrides_detection(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_cli_subprocess(["install", "--harness", "claude"], home)
    assert result.returncode == 0, result.stderr
    assert "harnesses: claude" in result.stdout
    for skill in ALL_SKILL_NAMES:
        assert (home / ".claude" / "skills" / skill).is_symlink()


def test_install_default_writes_hub_when_only_non_hub_reader_detected(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    result = run_cli_subprocess(["install"], home)
    assert result.returncode == 0, result.stderr
    assert "hub: written" in result.stdout
    for skill in ALL_SKILL_NAMES:
        assert (_hub_dir(home) / skill).is_symlink()
        assert (home / ".claude" / "skills" / skill).is_symlink()


def test_install_all_is_idempotent_on_reinstall(tmp_path):
    home = seed_fake_home(tmp_path / "home")
    first = run_cli_subprocess(["install", "--all"], home)
    assert first.returncode == 0, first.stderr

    second = run_cli_subprocess(["install", "--all"], home)
    assert second.returncode == 0, second.stderr
    assert "noop" in second.stdout
    assert "symlink" not in second.stdout.replace("noop", "")


def test_install_all_writes_repo_config(tmp_path):
    home = seed_fake_home(tmp_path / "home")
    result = run_cli_subprocess(["install", "--all"], home)
    assert result.returncode == 0, result.stderr
    config = home / ".config" / "wiki-spaces" / "config"
    assert config.is_file()
    assert "repo = " in config.read_text()


# Touch the imported skill-name tuples so the module's public mirrors are
# observably consistent with the package constants (guards silent drift).
def test_skill_name_mirrors_match_package_constants():
    assert WIKI_SKILL_NAMES == _common.WIKI_SKILLS
    assert KEPANO_SKILL_NAMES == _common.KEPANO_DEPS
