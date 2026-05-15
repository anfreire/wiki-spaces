"""Unit tests for wiki_spaces.init_wiki."""

from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from wiki_spaces import _common, init_wiki


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = init_wiki.main(args)
    return rc, out.getvalue(), err.getvalue()


# ---------- build_index_md ----------

def test_build_index_md_flat():
    text = init_wiki.build_index_md("MyWiki", "A description", [])
    assert text.startswith("# MyWiki")
    assert "## What this space is" in text
    assert "A description" in text
    assert "## Items" not in text


def test_build_index_md_with_folders_emits_items():
    text = init_wiki.build_index_md("MyWiki", "desc", ["concepts", "projects"])
    assert "## Items" in text
    assert "[concepts/](concepts/)" in text
    assert "[projects/](projects/)" in text


# ---------- folder validation ----------

def test_init_rejects_dot_dot(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / "wiki"), "--folders", "../escape", "--no-config"])
    assert rc == 2


def test_init_rejects_absolute(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / "wiki"), "--folders", "/abs", "--no-config"])
    assert rc == 2


def test_init_accepts_hidden_non_git_segment(monkeypatch, tmp_path):
    # Only `.git` is reserved; other hidden names (`.archive`, `.config`, etc.)
    # are allowed.
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", ".archive", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / ".archive").is_dir()


def test_init_accepts_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "concepts/", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / "concepts").is_dir()


def test_init_refuses_when_non_directory_file_collides(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("preexisting file")
    rc, _, err = _run([str(wiki), "--folders", "log.md", "--no-config"])
    assert rc == 2


# ---------- scaffold output ----------

def test_init_writes_index_with_what_section(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--description", "Test", "--no-config"])
    assert rc == 0
    text = (tmp_path / "wiki" / "index.md").read_text()
    assert "# wiki" in text
    assert "Test" in text


def test_init_writes_optional_pack_files(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([
        str(tmp_path / "wiki"),
        "--with", "log.md", "_meta/taxonomy.md", ".manifest.json",
        "--no-config",
    ])
    assert rc == 0
    wiki = tmp_path / "wiki"
    assert (wiki / "log.md").is_file()
    assert (wiki / "_meta" / "taxonomy.md").is_file()
    assert (wiki / ".manifest.json").is_file()


def test_init_registers_default_when_no_no_config_flag(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    rc, _, _ = _run([str(tmp_path / "wiki"), "--description", "x"])
    assert rc == 0
    assert _common.read_config()["wiki"] == str((tmp_path / "wiki").resolve())


# ---------- nested --folders ----------

def test_init_accepts_nested_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "projects/foo", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / "projects" / "foo").is_dir()


def test_init_rejects_dotgit_segment(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "projects/.git", "--no-config"])
    assert rc == 2


def test_init_rejects_double_dot_segment(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "projects/../escape", "--no-config"])
    assert rc == 2


def test_init_deduplicates_normalized_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    # Two args that normalize to the same path
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "concepts", "concepts/", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / "concepts").is_dir()
