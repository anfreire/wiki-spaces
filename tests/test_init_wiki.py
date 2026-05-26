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

def test_build_index_md():
    text = init_wiki.build_index_md("MyWiki", "A description")
    assert text.startswith("# MyWiki")
    assert "## What this space is" in text
    assert "A description" in text
    assert "## Items" not in text
    assert "## Spaces" in text


def test_build_index_md_spaces_section_is_empty():
    """Empty `## Spaces` is spec-valid (the contract is 'exhaustive list,'
    not 'non-empty list'). A new wiki has no contained spaces yet."""
    text = init_wiki.build_index_md("MyWiki", "A description")
    from wiki_spaces import _md
    assert _md.has_section(text, "Spaces")
    assert _md.parse_section_entries(text, "Spaces") == []


def test_build_index_md_omits_description_section_when_none():
    """When `description` is None or empty, `## What this space is` is omitted
    entirely — no placeholder text. `## Spaces` is always present."""
    text = init_wiki.build_index_md("MyWiki")
    assert text.startswith("# MyWiki")
    assert "## What this space is" not in text
    assert "## Spaces" in text


def test_build_index_md_always_emits_spaces_section():
    """Every CLI-created wiki has `## Spaces` from t=0 — the navigation
    contract is part of what `init` produces, not an optional add-on."""
    text = init_wiki.build_index_md("MyWiki", "A description")
    assert "## Spaces" in text


def test_init_with_no_description_writes_no_placeholder(monkeypatch, tmp_path):
    """Regression: `wiki-spaces init` without --description must NOT write
    the literal `<one paragraph describing this wiki>` placeholder into the
    user's index.md."""
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--no-config"])
    assert rc == 0
    body = (tmp_path / "wiki" / "index.md").read_text()
    assert "<one paragraph describing this wiki>" not in body
    assert "## Spaces" in body


def test_init_folders_created_but_not_listed_in_index(monkeypatch, tmp_path):
    """--folders creates directories on disk; index.md gets no `## Items`
    section — tools discover plain folders via the filesystem."""
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([
        str(tmp_path / "wiki"), "--folders", "concepts", "projects", "--no-config",
    ])
    assert rc == 0
    wiki = tmp_path / "wiki"
    assert (wiki / "concepts").is_dir() and (wiki / "projects").is_dir()
    assert "## Items" not in (wiki / "index.md").read_text()


# ---------- folder validation ----------

def test_init_rejects_dot_dot(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / "wiki"), "--folders", "../escape", "--no-config"])
    assert rc == 2


def test_init_rejects_absolute(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / "wiki"), "--folders", "/abs", "--no-config"])
    assert rc == 2


def test_init_rejects_hidden_segment(monkeypatch, tmp_path):
    """Reserved-folder contract is end-to-end (PR-G + 52ea345): hidden
    segments are skipped by every consumer walker per CONVENTIONS /
    Reserved top-level folder names. The producer side mirrors this —
    `space._validate_rel_path` already refused hidden segments for
    `space add`; `init --folders` must refuse them too or scaffolding
    creates content no skill can reach.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    for hidden in (".archive", ".config", ".cache", "projects/.cache"):
        rc, _, err = _run(
            [str(tmp_path / f"wiki-{hidden.replace('/', '-')}"),
             "--folders", hidden, "--no-config"]
        )
        assert rc == 2, f"hidden segment {hidden!r} accepted"
        assert "reserved" in err.lower() or "hidden" in err.lower() or "invalid" in err.lower()


def test_init_rejects_reserved_underscore_segments(monkeypatch, tmp_path):
    """`_archives` and `_meta` are excluded from consumer walks per
    CONVENTIONS / Reserved top-level folder names. `init --folders` must
    refuse them so the user can't accidentally scaffold a folder no skill
    will ever read.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    for bad in ("_archives", "_meta", "projects/_archives", "_meta/foo"):
        rc, _, err = _run(
            [str(tmp_path / f"wiki-{bad.replace('/', '-')}"),
             "--folders", bad, "--no-config"]
        )
        assert rc == 2, f"reserved segment {bad!r} accepted"
        assert "invalid" in err.lower() or "reserved" in err.lower()


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
