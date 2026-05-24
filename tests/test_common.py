"""Unit tests for wiki_spaces._common."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wiki_spaces import _common


# ---------- read_config / write_config ----------

def test_read_config_returns_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent")
    assert _common.read_config() == {}


def test_read_config_parses_keys(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("wiki = /home/u/Wiki\nrepo = /home/u/repo\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki", "repo": "/home/u/repo"}


def test_read_config_strips_whitespace(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("  wiki  =   /home/u/Wiki  \n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki"}


def test_read_config_ignores_whole_line_comments(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("# a comment\nwiki = /home/u/Wiki\n# another\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki"}


def test_read_config_treats_inline_hash_as_part_of_value(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("wiki = /home/u/Wiki#sub\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config()["wiki"] == "/home/u/Wiki#sub"


def test_read_config_ignores_blank_lines(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("\n\nwiki = /home/u/Wiki\n\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki"}


def test_write_config_creates_parent_dirs(monkeypatch, tmp_path):
    cfg = tmp_path / "nested" / "deep" / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    _common.write_config({"wiki": "/home/u/Wiki"})
    assert cfg.exists()
    assert _common.read_config()["wiki"] == "/home/u/Wiki"


def test_write_config_merges_keys(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    _common.write_config({"wiki": "/w"})
    _common.write_config({"repo": "/r"})
    out = _common.read_config()
    assert out == {"wiki": "/w", "repo": "/r"}


# ---------- nearest_space_root_strict / nearest_space_root_for_repair ----------

def test_nearest_space_root_for_repair_finds_self_bare_index(tmp_path):
    """Repair resolver accepts bare-`index.md` (no `## Spaces`); write
    commands lean on the chain helper to insert the section atomically."""
    (tmp_path / "index.md").write_text("")
    assert _common.nearest_space_root_for_repair(tmp_path) == tmp_path.resolve()


def test_nearest_space_root_for_repair_walks_up(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("")
    deep = wiki / "projects" / "deep" / "nested"
    deep.mkdir(parents=True)
    assert _common.nearest_space_root_for_repair(deep) == wiki.resolve()


def test_nearest_space_root_for_repair_none_when_no_ancestor(tmp_path):
    sub = tmp_path / "no" / "wiki" / "here"
    sub.mkdir(parents=True)
    assert _common.nearest_space_root_for_repair(sub) is None


def test_nearest_space_root_for_repair_from_file(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("")
    page = wiki / "page.md"
    page.write_text("")
    assert _common.nearest_space_root_for_repair(page) == wiki.resolve()


def test_nearest_space_root_strict_requires_spaces_section(tmp_path):
    """Strict resolver returns None on a bare-`index.md` wiki — read-only
    commands (audit, skills, doctor) refuse to operate on a folder that
    lacks the v1 navigation contract."""
    (tmp_path / "index.md").write_text("# bare\n")  # no `## Spaces`
    assert _common.nearest_space_root_strict(tmp_path) is None


def test_nearest_space_root_strict_accepts_index_with_spaces(tmp_path):
    (tmp_path / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    assert _common.nearest_space_root_strict(tmp_path) == tmp_path.resolve()


def test_nearest_space_root_strict_walks_up_past_bare_index(tmp_path):
    """Strict resolver skips folders that lack `## Spaces` while walking up
    — the closest valid ancestor wins, not the closest bare-index folder."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "index.md").write_text("# outer\n\n## Spaces\n\n")
    inner = outer / "inner"
    inner.mkdir()
    (inner / "index.md").write_text("# inner\n")  # bare; no `## Spaces`
    assert _common.nearest_space_root_strict(inner) == outer.resolve()


# ---------- is_owned_install / write_owned_marker ----------

def test_is_owned_install_missing_dst(tmp_path):
    assert _common.is_owned_install(tmp_path / "absent", tmp_path / "src") is True


def test_is_owned_install_symlink_is_owned(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    os.symlink(src, dst)
    assert _common.is_owned_install(dst, src) is True


def test_is_owned_install_marker_present(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    _common.write_owned_marker(dst, src)
    assert _common.is_owned_install(dst, src) is True


def test_is_owned_install_unowned_dir(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "user-skill"
    dst.mkdir()
    (dst / "SKILL.md").write_text("user content")
    assert _common.is_owned_install(dst, src) is False


# ---------- HARNESSES matrix ----------

def test_harnesses_includes_antigravity():
    assert "antigravity" in {h.key for h in _common.HARNESSES}


def test_antigravity_harness_paths():
    ag = next(h for h in _common.HARNESSES if h.key == "antigravity")
    assert ag.skills_dir == _common.HOME / ".gemini/antigravity/skills"
    assert _common.HOME / ".gemini/antigravity" in ag.detect


def test_harness_keys_are_unique():
    keys = [h.key for h in _common.HARNESSES]
    assert len(keys) == len(set(keys))

# ---------- __version__ single-sourcing ----------

def test_version_matches_pyproject():
    """`wiki_spaces.__version__` must equal pyproject.toml's [project] version.

    Codex flagged version drift across __init__.py / pyproject.toml / uv.lock.
    pyproject.toml is the single source via importlib.metadata; uv.lock tracks
    it automatically on next `uv lock`. This test catches drift if anyone
    edits __init__.py and forgets pyproject (or vice versa).
    """
    import tomllib
    import wiki_spaces

    repo_root = Path(__file__).resolve().parent.parent
    with (repo_root / "pyproject.toml").open("rb") as f:
        meta = tomllib.load(f)
    assert wiki_spaces.__version__ == meta["project"]["version"]
