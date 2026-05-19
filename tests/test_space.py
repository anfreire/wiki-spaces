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


def test_add_refuses_tier1_parent(tmp_path):
    """Atomic refuse: ancestor has no ## Spaces → CLI errors, FS untouched.
    LLM/skill layer handles upgrading Tier 1 parents."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 2
    assert "## Spaces" in err
    assert not (wiki / "foo").exists()
    assert "## Spaces" not in (wiki / "index.md").read_text()


def test_add_upgrade_parent_flag_removed(tmp_path):
    """The --upgrade-parent flag was removed; argparse rejects it."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    with pytest.raises(SystemExit):
        _run(["--wiki", str(wiki), "add", "foo", "--upgrade-parent"])


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


def test_remove_refuses_tier1_parent(tmp_path):
    """Symmetric with add: ancestor has no ## Spaces → CLI errors, FS untouched."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 2
    assert "## Spaces" in err
    assert (wiki / "foo").exists()


def test_remove_tier1_parent_error_precedes_nonempty_check(tmp_path):
    """Tier 1 refusal must be the first-class error, NOT 'pass --force'.
    Without ordering the contract check before content check, a user with a
    Tier 1 parent + nonempty child would be told to add --force, then hit the
    Tier 1 error on retry."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    (wiki / "foo" / "extra.md").write_text("user content")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 2
    assert "## Spaces" in err
    assert "--force" not in err
    assert (wiki / "foo" / "extra.md").exists()


def test_remove_normalized_href_match(tmp_path):
    """`space remove foo` must remove an existing `- [foo/](foo/)` entry even
    though its href is `foo/`, not `foo/index.md`. Audit normalizes these
    forms; add/remove must match audit's semantics."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [foo/](foo/)\n")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0
    assert not (wiki / "foo").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries == []


def test_add_normalized_href_idempotent(tmp_path):
    """`space add foo` against `- [foo/](foo/)` must NOT duplicate. Different
    href forms (`foo/` vs `foo/index.md`) identify the same child space."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [foo/](foo/)\n")
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert len(entries) == 1


def test_add_creates_tier2_child(tmp_path):
    """A space created by `space add` must itself have `## Spaces` so that
    nested `space add foo/bar` works without a second upgrade step."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    child_text = (wiki / "foo" / "index.md").read_text()
    assert "## Spaces" in child_text
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc == 0
    assert (wiki / "foo" / "bar" / "index.md").exists()


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


def test_audit_summary_excludes_external_from_page_count(tmp_path):
    """The summary `pages` count must match audit's owned-only scope; pages
    under `shared/` are external and excluded from both drift detection and
    the summary."""
    wiki = _make_wiki(tmp_path)
    (wiki / "owned.md").write_text("# owned")
    shared = wiki / "shared" / "team"
    shared.mkdir(parents=True)
    (shared / "index.md").write_text("# team")
    (shared / "extra.md").write_text("# extra in external space")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    # 1 owned page (owned.md) + 1 index (wiki/index.md) = 2; external pages excluded
    assert "pages:  2" in out


def test_audit_summary_excludes_nested_foreign_submodule(tmp_path):
    """A foreign submodule at `projects/external/` is external even though
    `projects/` itself is owned. A naive top-level filter would count its
    pages; the directory-by-directory walk must prune at any depth."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    external = wiki / "projects" / "external"
    external.mkdir(parents=True)
    (external / "index.md").write_text("# external")
    (external / "leaked.md").write_text("# should not be counted")
    (wiki / ".gitmodules").write_text(
        '[submodule "external"]\n'
        "\tpath = projects/external\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    # wiki/index.md = 1 page; everything under projects/external/ is external
    assert "pages:  1" in out, out


def test_audit_terminates_with_in_tree_symlink_cycle(tmp_path):
    """An in-tree symlink cycle (`deep/loop -> wiki`) must not hang the audit
    summary header's page count. `_walk_owned_spaces` already guards this;
    `_count_owned_pages` must mirror the guard or `audit` hangs before drift
    detection even runs. Isolates the cycle-vs-hang concern by registering
    `deep/` in `## Spaces` so the test asserts on termination, not drift."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "deep"])
    import os
    os.symlink(wiki, wiki / "deep" / "loop")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "pages:" in out
    assert "OK" in out
    assert rc == 0


def test_remove_strips_all_duplicate_entries(tmp_path):
    """A pre-corrupted wiki with multiple `## Spaces` entries for the same
    directory should be fully cleaned up in one `space remove` call. Without
    looping, only the first matching entry would be removed."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [foo/](foo/)\n"
        + "- [foo/](foo/index.md)\n"
    )
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries == []


def test_audit_accepts_bare_folder_href(tmp_path):
    """A `## Spaces` entry written `- [foo/](foo/)` (bare-folder href, no
    /index.md) must not be reported as a missing entry."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [foo/](foo/)\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "OK" in out


def test_audit_reports_stale_entry(tmp_path):
    """A `## Spaces` entry with no space on disk is reported stale."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [ghost/](ghost/index.md)\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0
    assert "ghost" in out and "stale" in out


def test_audit_accepts_nested_space_entry(tmp_path):
    """`space add projects/foo` registers `projects/foo/index.md` in the
    root's ## Spaces (projects/ is a plain folder). Audit must treat that
    multi-segment entry as valid, not stale."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "OK" in out


def test_audit_reports_nested_orphan_missing(tmp_path):
    """A space nested below a plain folder, unregistered, is reported missing
    against its nearest ancestor space (the wiki root here)."""
    wiki = _make_wiki(tmp_path)
    nested = wiki / "projects" / "orphan"
    nested.mkdir(parents=True)
    (nested / "index.md").write_text("# orphan")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0
    assert "projects/orphan" in out


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


def test_wiki_origin_url_follows_gitdir_file(tmp_path):
    """Submodule layout: `<wiki>/.git` is a FILE pointing at the real gitdir."""
    wiki = _make_wiki(tmp_path)
    real_gitdir = tmp_path / "elsewhere" / "modules" / "mywiki"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/sub/wiki.git\n'
    )
    (wiki / ".git").write_text(f"gitdir: {real_gitdir}\n")
    assert space._wiki_origin_url(wiki) == "https://github.com/sub/wiki.git"


def test_wiki_origin_url_worktree_follows_commondir(tmp_path):
    """Worktree layout: gitdir holds `commondir` pointing at the shared repo."""
    wiki = _make_wiki(tmp_path)
    common = tmp_path / "main-repo" / ".git"
    common.mkdir(parents=True)
    (common / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/wt/shared.git\n'
    )
    worktree_gitdir = common / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")
    (worktree_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://example.invalid/should-be-ignored.git\n'
    )
    (wiki / ".git").write_text(f"gitdir: {worktree_gitdir}\n")
    assert space._wiki_origin_url(wiki) == "https://github.com/wt/shared.git"


def test_wiki_origin_url_returns_none_for_broken_gitdir_file(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / ".git").write_text("gitdir: /nonexistent/path/does/not/exist\n")
    assert space._wiki_origin_url(wiki) is None


def test_wiki_origin_url_relative_gitdir(tmp_path):
    """Common submodule shape: `gitdir: ../.git/modules/<name>` (relative)."""
    parent_repo = tmp_path / "parent"
    parent_repo.mkdir()
    real_gitdir = parent_repo / ".git" / "modules" / "sub"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/sub/wiki.git\n'
    )
    wiki = parent_repo / "sub"
    wiki.mkdir()
    (wiki / "index.md").write_text("# sub")
    (wiki / ".git").write_text("gitdir: ../.git/modules/sub\n")
    assert space._wiki_origin_url(wiki) == "https://github.com/sub/wiki.git"


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
