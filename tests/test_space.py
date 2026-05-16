"""End-to-end tests for the space CLI against temp wikis."""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from wiki_spaces import _md, space


def _make_wiki(tmp_path: Path, with_spaces_section: bool = True) -> Path:
    """Scaffold a minimal wiki at tmp_path/wiki."""
    root = tmp_path / "wiki"
    root.mkdir()
    body = "# wiki\n\n## What this space is\n\nTest wiki\n"
    if with_spaces_section:
        body += "\n## Spaces\n\n"
    (root / "index.md").write_text(body)
    return root


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = space.main(args)
    return rc, out.getvalue(), err.getvalue()


# ---------- _resolve_wiki / _validate_rel_path ----------

def test_validate_rel_path_rejects_dot_dot():
    ok, err = space._validate_rel_path("../escape")
    assert not ok
    assert err is not None


def test_validate_rel_path_rejects_absolute():
    ok, err = space._validate_rel_path("/absolute")
    assert not ok


def test_validate_rel_path_accepts_nested():
    ok, err = space._validate_rel_path("projects/foo")
    assert ok and err is None


def test_validate_rel_path_accepts_hidden_non_git():
    # Matches `init --folders` policy: only .git is reserved.
    ok, err = space._validate_rel_path(".archive")
    assert ok and err is None


def test_validate_rel_path_rejects_dot_git():
    ok, err = space._validate_rel_path("projects/.git")
    assert not ok


# ---------- space add ----------

def test_add_creates_space_and_updates_parent(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo", "--description", "foo space"])
    assert rc == 0
    assert (wiki / "foo" / "index.md").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert len(entries) == 1
    assert entries[0].href == "foo/index.md"


def test_add_is_idempotent(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert len(entries) == 1


def test_add_leaves_tier1_parent_alone(tmp_path):
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    assert "## Spaces" not in (wiki / "index.md").read_text()


def test_add_upgrade_parent_creates_section(tmp_path):
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo", "--upgrade-parent"])
    assert rc == 0
    text = (wiki / "index.md").read_text()
    assert "## Spaces" in text
    entries = _md.parse_section_entries(text, "Spaces")
    assert entries[0].href == "foo/index.md"


def test_add_nested_path_walks_up_to_nearest_space(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    assert (wiki / "projects" / "foo" / "index.md").exists()
    # projects/ has no index.md; nearest ancestor space is wiki root
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries[0].href == "projects/foo/index.md"


def test_add_rejects_dot_dot(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "../escape"])
    assert rc == 2


# ---------- space remove ----------

def test_remove_strips_entry_and_deletes(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    rc, out, _ = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0
    assert not (wiki / "foo").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries == []


def test_remove_dry_run_changes_nothing(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    before = (wiki / "index.md").read_text()
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo", "--dry-run"])
    assert rc == 0
    assert (wiki / "foo").exists()
    assert (wiki / "index.md").read_text() == before


def test_remove_refuses_nonempty_without_force(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    (wiki / "foo" / "extra.md").write_text("user content")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 2
    assert (wiki / "foo").exists()


def test_remove_with_force_strips_nonempty(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    (wiki / "foo" / "extra.md").write_text("user content")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo", "--force"])
    assert rc == 0
    assert not (wiki / "foo").exists()


def test_remove_refuses_wiki_root(tmp_path):
    wiki = _make_wiki(tmp_path)
    # Try to remove the wiki root itself — should refuse
    rc, _, err = _run(["--wiki", str(wiki), "remove", "."])
    # "." is rejected by validator before reaching the root check
    assert rc == 2


# ---------- space audit ----------

def test_audit_reports_missing_direct_child(tmp_path):
    wiki = _make_wiki(tmp_path)
    # Create a space on disk without adding to ## Spaces
    (wiki / "orphan").mkdir()
    (wiki / "orphan" / "index.md").write_text("# orphan")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0
    assert "orphan" in out


def test_audit_clean_returns_zero(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    assert "OK" in out


def test_audit_skips_external_shared(tmp_path):
    wiki = _make_wiki(tmp_path)
    shared = wiki / "shared" / "team-foo"
    shared.mkdir(parents=True)
    (shared / "index.md").write_text("# team-foo")
    # shared/ is external; should NOT be flagged as missing entry
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0


# ---------- symlink cycle safety ----------

def test_walk_owned_spaces_breaks_symlink_cycle(tmp_path):
    wiki = _make_wiki(tmp_path)
    sub = wiki / "deep"
    sub.mkdir()
    (sub / "index.md").write_text("# deep")
    # Create a cycle: deep/loop -> wiki
    import os
    os.symlink(wiki, sub / "loop")
    # Should terminate, not infinite-loop
    spaces = list(space._walk_owned_spaces(wiki))
    assert wiki in spaces
    assert sub in spaces


# ---------- .gitmodules foreign-origin check ----------

def _make_git_config(wiki: Path, origin_url: str) -> None:
    """Write a minimal .git/config with origin remote."""
    git_dir = wiki / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "config").write_text(
        f'[remote "origin"]\n\turl = {origin_url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )


def test_wiki_origin_url_returns_url(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    assert space._wiki_origin_url(wiki) == "https://github.com/me/mywiki.git"


def test_wiki_origin_url_returns_none_without_config(tmp_path):
    wiki = _make_wiki(tmp_path)
    assert space._wiki_origin_url(wiki) is None


def test_is_foreign_submodule_no_gitmodules(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "projects" / "foo").mkdir(parents=True)
    assert space._is_foreign_submodule(wiki / "projects" / "foo", wiki) is False


def test_is_foreign_submodule_different_origin(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "projects" / "external"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "external"]\n'
        "\tpath = projects/external\n"
        "\turl = https://github.com/someone-else/their-wiki.git\n"
    )
    assert space._is_foreign_submodule(sub, wiki) is True


def test_is_foreign_submodule_same_origin(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "projects" / "self-mirror"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "self-mirror"]\n'
        "\tpath = projects/self-mirror\n"
        "\turl = https://github.com/me/mywiki.git\n"
    )
    assert space._is_foreign_submodule(sub, wiki) is False


def test_is_external_marks_foreign_submodule(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "projects" / "foreign"
    sub.mkdir(parents=True)
    (sub / "index.md").write_text("# foreign")
    (wiki / ".gitmodules").write_text(
        '[submodule "foreign"]\n'
        "\tpath = projects/foreign\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    assert space._is_external(sub, wiki) is True


# ---------- _is_external: lexical shared/ check ----------

def test_is_external_marks_plain_shared_dir(tmp_path):
    wiki = _make_wiki(tmp_path)
    d = wiki / "shared" / "team"
    d.mkdir(parents=True)
    assert space._is_external(d, wiki) is True


def test_is_external_owned_for_normal_dir(tmp_path):
    wiki = _make_wiki(tmp_path)
    d = wiki / "projects" / "mine"
    d.mkdir(parents=True)
    assert space._is_external(d, wiki) is False


def test_is_external_marks_symlink_under_shared(tmp_path):
    """A symlink at <wiki>/shared/ is external even when it points back inside
    the wiki tree — the shared/ test must be lexical, not realpath-based."""
    wiki = _make_wiki(tmp_path)
    inside = wiki / "projects" / "real"
    inside.mkdir(parents=True)
    (inside / "index.md").write_text("# real")
    (wiki / "shared").mkdir()
    import os
    link = wiki / "shared" / "mirror"
    os.symlink(inside, link)
    assert space._is_external(link, wiki) is True
