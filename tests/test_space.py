"""End-to-end tests for the space CLI against temp wikis."""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from wiki_spaces import _md, init_wiki, space


def _make_wiki(tmp_path: Path, with_spaces_section: bool = True) -> Path:
    """Scaffold a minimal wiki at tmp_path/wiki."""
    root = tmp_path / "wiki"
    root.mkdir()
    body = "# wiki\n\n## What this space is\n\nTest wiki\n"
    if with_spaces_section:
        body += "\n## Spaces\n\n"
    (root / "index.md").write_text(body)
    return root


def _run(args: list[str], *, stdin: str | None = None) -> tuple[int, str, str]:
    import sys as _sys
    out, err = io.StringIO(), io.StringIO()
    if stdin is not None:
        saved_stdin = _sys.stdin
        _sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = space.main(args)
    finally:
        if stdin is not None:
            _sys.stdin = saved_stdin
    return rc, out.getvalue(), err.getvalue()


# ---------- _resolve_wiki / _validate_rel_path ----------

def test_audit_strict_resolver_rejects_bare_index_via_explicit_path(tmp_path):
    """PR-D: audit is read-only and uses the strict resolver. A folder with
    `index.md` but no `## Spaces` is not a wiki — audit refuses to operate."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, _, err = _run(["--wiki", str(wiki), "audit"])
    assert rc == 2
    assert "Spaces" in err


def test_audit_strict_resolver_rejects_bare_index_via_cwd(tmp_path, monkeypatch):
    """Same contract through the CWD fallback: the strict resolver walks up
    looking for `index.md` + `## Spaces` together. A bare-index ancestor is
    invisible to the read-only path."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    # Move into the wiki so the CWD fallback runs.
    monkeypatch.chdir(wiki)
    # Don't pass --wiki; clear any inherited config.
    monkeypatch.setattr(space, "wiki_path", lambda: None)
    rc, _, err = _run(["audit"])
    assert rc == 2
    assert "Spaces" in err


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


def test_add_inserts_spaces_and_registers_against_bare_ancestor(tmp_path):
    """`space add` against a bare-`index.md` ancestor inserts `## Spaces`
    into the ancestor as the first mutation step (via the chain helper)
    and registers the new child — no refuse, no manual setup step. Same
    contract as promote (PR-C) and mount; replaces the pre-v1 refusal
    behavior."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0, err
    # `foo/` exists with its own `## Spaces`.
    assert (wiki / "foo" / "index.md").is_file()
    assert "## Spaces" in (wiki / "foo" / "index.md").read_text()
    # The ancestor's bare-`index.md` got `## Spaces` inserted AND `foo/` registered.
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "foo/" in e.href for e in entries)


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


# ---------- PR-D chain helper coverage ----------

def test_ensure_chain_walks_multi_level_and_registers_each_step(tmp_path):
    """`space add foo/bar` where `foo/index.md` exists bare AND wiki root
    has no `## Spaces`: the chain helper walks (bar, foo), (foo, wiki),
    inserting `## Spaces` and registering at each step."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")  # bare; no `## Spaces`
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc == 0, err
    # New leaf exists.
    assert (wiki / "foo" / "bar" / "index.md").is_file()
    # foo/index.md got `## Spaces` and `bar/` registered.
    foo_text = (wiki / "foo" / "index.md").read_text()
    assert "## Spaces" in foo_text
    foo_entries = _md.parse_section_entries(foo_text, "Spaces")
    assert any(e.href and "bar/" in e.href for e in foo_entries)
    # Wiki root got `## Spaces` and `foo/` registered.
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    root_entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "foo/" in e.href for e in root_entries)


def test_ensure_chain_rolls_back_added_entries_on_mid_walk_failure(tmp_path, monkeypatch):
    """Wiki has `foo/index.md` (bare) and root has no `## Spaces`.
    Sabotage writes to the wiki root's `index.md` only — the deep edge
    (register `bar` in `foo`) succeeds via the real helper, the root edge
    fails, and the rollback path runs the real helper too (removing `bar`
    from foo). Assert: foo's `## Spaces` insertion stays (append-only,
    non-destructive); `bar` is rolled back out of foo's `## Spaces`."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")

    real = space._atomic_mutate_index
    root_index = (wiki / "index.md").resolve()

    def patched(ancestor, ancestor_index, mutate_fn):
        if ancestor_index.resolve() == root_index:
            return 1, "simulated second-edge failure"
        return real(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space, "_atomic_mutate_index", patched)

    rc, _, err = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc == 1
    assert "simulated second-edge failure" in err
    # bar/ FS creation was rolled back.
    assert not (wiki / "foo" / "bar").exists()
    # foo's `## Spaces` insertion survives the rollback (append-only).
    foo_text = (wiki / "foo" / "index.md").read_text()
    assert "## Spaces" in foo_text
    # `bar` was rolled back out of foo's `## Spaces`.
    foo_entries = _md.parse_section_entries(foo_text, "Spaces")
    assert not any(e.href and "bar" in e.href for e in foo_entries)


def test_ensure_section_at_only_touches_that_space(tmp_path):
    """`_ensure_section_at(wiki/foo)` mutates `wiki/foo/index.md` only;
    it does NOT walk up or touch `wiki/index.md`."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")
    root_before = (wiki / "index.md").read_text()
    space._ensure_section_at(wiki / "foo", wiki)
    # foo got `## Spaces`.
    assert "## Spaces" in (wiki / "foo" / "index.md").read_text()
    # root is untouched.
    assert (wiki / "index.md").read_text() == root_before


def test_cmd_add_existing_target_ensures_target_has_spaces_section(tmp_path):
    """`space add foo` against a pre-existing bare `foo/index.md` leaves
    foo with a `## Spaces` section so re-registered existing targets
    aren't bare-index after the call."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")  # bare; no `## Spaces`
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0, err
    assert "## Spaces" in (wiki / "foo" / "index.md").read_text()


def test_add_dry_run_prints_plan_no_fs_changes(tmp_path):
    """`space add --dry-run` previews the chain-helper plan and touches
    nothing on disk — no new directory, no `## Spaces` insertion into a
    bare ancestor."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    before = (wiki / "index.md").read_text()
    rc, out, _ = _run(["--wiki", str(wiki), "add", "newproj", "--dry-run"])
    assert rc == 0
    assert "(dry-run)" in out
    assert not (wiki / "newproj").exists()
    # Ancestor's bare `index.md` was NOT touched (dry-run preview only).
    assert (wiki / "index.md").read_text() == before


def test_cmd_add_description_goes_to_child_not_parent_entry(tmp_path):
    """v1 behavior: `space add foo --description X` writes X into foo's
    `## What this space is`, NOT into the parent's `## Spaces` entry
    description. The parent entry uses the derived label and no
    description trailer."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo", "--description", "foo says hi"])
    assert rc == 0, err
    # Child's body carries the description.
    foo_text = (wiki / "foo" / "index.md").read_text()
    assert "## What this space is" in foo_text
    assert "foo says hi" in foo_text
    # Parent's `## Spaces` entry has NO description trailer.
    root_text = (wiki / "index.md").read_text()
    entries = _md.parse_section_entries(root_text, "Spaces")
    foo_entry = next(e for e in entries if e.href and "foo/" in e.href)
    assert (foo_entry.description or "").strip() == ""


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


def test_remove_against_bare_ancestor_inserts_section_and_removes(tmp_path):
    """v1 behavior: when the ancestor lacks `## Spaces` AND the target
    space exists with no extra content AND we're not in dry-run, remove
    proceeds. `_ensure_section_at` inserts an empty `## Spaces` into the
    ancestor (so the entry-removal step has a section to operate on),
    then the target's entry-remove is a no-op (no entry was registered),
    then the target directory is deleted. Final state: ancestor has an
    empty `## Spaces`, target is gone."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0, err
    assert not (wiki / "foo").exists()
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text


def test_remove_does_not_mutate_on_nonempty_refusal(tmp_path):
    """Refusal paths (non-empty target without --force, external scope,
    etc.) must NOT mutate the ancestor. `_ensure_section_at` runs only
    after every refusal check. So a remove that bounces on the non-empty
    target check leaves the bare-`index.md` ancestor untouched."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    (wiki / "foo" / "extra.md").write_text("user content")
    before = (wiki / "index.md").read_text()
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 2
    assert "--force" in err  # non-empty target wins over any other error
    assert (wiki / "foo" / "extra.md").exists()
    # Ancestor untouched — no `## Spaces` insertion on a refused remove.
    assert (wiki / "index.md").read_text() == before


def test_remove_dry_run_does_not_mutate_bare_ancestor(tmp_path):
    """Dry-run must NOT insert `## Spaces` into a bare ancestor either."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    before = (wiki / "index.md").read_text()
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo", "--dry-run"])
    assert rc == 0
    assert (wiki / "foo").exists()
    assert (wiki / "index.md").read_text() == before


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


def test_add_creates_child_with_spaces_section(tmp_path):
    """A space created by `space add` must itself have `## Spaces` from t=0
    so that nested `space add foo/bar` works without a second step."""
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


# ---------- space audit: broken wikilinks + orphans ----------

def test_audit_reports_broken_wikilink(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\nlinks to [[ghost]] which is missing.\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "broken wikilink" in out
    assert "ghost" in out


def test_audit_resolved_wikilink_not_broken(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "alpha.md").write_text("# Alpha\n\nsee [[beta]].\n")
    (wiki / "beta.md").write_text("# Beta\n\nback to [[alpha]].\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "! broken wikilink" not in out
    assert rc == 0


def test_audit_wikilink_in_code_block_ignored(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text(
        "# Page\n\nlinked [[real]] here.\n\n```\n[[fake-in-code]]\n```\n"
    )
    (wiki / "real.md").write_text("# Real\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "fake-in-code" not in out
    assert "! broken wikilink" not in out


def test_audit_resolves_wikilink_via_alias(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "src.md").write_text("# Src\n\nsee [[the-bee]].\n")
    (wiki / "beta.md").write_text("---\ntitle: Beta\naliases: [the-bee]\n---\n\n# Beta\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "! broken wikilink" not in out, out
    assert rc == 0


def test_audit_reports_orphan_page(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "lonely.md").write_text("# Lonely\n\nnobody links here.\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "orphan" in out
    assert "lonely.md" in out


def test_audit_orphan_alone_does_not_fail_exit(tmp_path):
    """An orphan is a fact, not an error — it must not flip the exit code."""
    wiki = _make_wiki(tmp_path)
    (wiki / "lonely.md").write_text("# Lonely\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    assert "orphan" in out


def test_audit_linked_page_is_not_orphan(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "hub.md").write_text("# Hub\n\npoints at [[leaf]].\n")
    (wiki / "leaf.md").write_text("# Leaf\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "hub.md" in orphan_section
    assert "leaf.md" not in orphan_section


def test_audit_index_md_never_orphan(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "sub"])
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "index.md" not in orphan_section


def test_audit_broken_link_in_external_space_ignored(tmp_path):
    wiki = _make_wiki(tmp_path)
    shared = wiki / "shared" / "team"
    shared.mkdir(parents=True)
    (shared / "index.md").write_text("# team")
    (shared / "page.md").write_text("# P\n\nbad [[ghost-in-external]] link\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "ghost-in-external" not in out
    assert rc == 0


def test_audit_broken_and_drift_both_counted(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "orphan-space").mkdir()
    (wiki / "orphan-space" / "index.md").write_text("# orphan-space")
    (wiki / "p.md").write_text("# P\n\nlink [[nowhere]]\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "drift" in out and "broken wikilink" in out


# ---------- space audit: _archives + Obsidian embeds ----------

def test_audit_fix_inserts_spaces_into_bare_index_folder(tmp_path):
    """`audit --fix` is the repair surface: it inserts `## Spaces` into any
    owned folder that has `index.md` but no heading. After the fix, the
    same audit re-run with no `--fix` exits 0."""
    wiki = _make_wiki(tmp_path)
    bare = wiki / "bare"
    bare.mkdir()
    (bare / "index.md").write_text("# bare\n")  # no `## Spaces`
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    # We added a child without registering it pre-fix → it shows as drift;
    # the fix repairs the bare section AND registers the missing entry.
    assert rc == 0, out
    bare_text = (bare / "index.md").read_text()
    assert "## Spaces" in bare_text
    root_text = (wiki / "index.md").read_text()
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "bare/" in e.href for e in entries)


def test_audit_fix_recomputes_drift_after_section_repair(tmp_path):
    """A nested bare-index space is invisible to drift detection until the
    bare-section pass runs (because the parser skips entries when the
    section header is missing). `--fix` makes a single pass do both."""
    wiki = _make_wiki(tmp_path)
    # foo is registered in wiki root's `## Spaces` already (via space add).
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    # Strip foo's own `## Spaces` to mimic a pre-v1 adopted layout.
    foo_idx = wiki / "foo" / "index.md"
    foo_idx.write_text("# foo\n")  # bare
    # Now create foo/bar/ on disk — drift, but invisible until foo gets
    # `## Spaces` back.
    (wiki / "foo" / "bar").mkdir()
    (wiki / "foo" / "bar" / "index.md").write_text("# bar\n\n## Spaces\n\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 0, out
    assert "## Spaces" in foo_idx.read_text()
    foo_entries = _md.parse_section_entries(foo_idx.read_text(), "Spaces")
    assert any(e.href and "bar" in e.href for e in foo_entries)


def test_audit_fix_registers_missing_entry_without_creating_directory(tmp_path):
    """`--fix` registers existing on-disk children; it never creates a
    directory. A stale entry (target absent) is reported but only removed
    when `--remove-stale` is also passed."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    # foo is NOT in wiki root's `## Spaces` — drift.
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 0, out
    # Entry registered; no new dir created.
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "foo/" in e.href for e in entries)


def test_audit_fix_remove_stale_refuses_external_without_include_external(tmp_path):
    """Stale entries pointing into externally-classified paths are NOT
    removed unless `--include-external --remove-stale` are passed together
    — guards against accidentally unregistering a legitimate external mount
    whose contents are temporarily unavailable."""
    wiki = _make_wiki(tmp_path)
    # Hand-edit wiki/index.md to declare a stale shared/team entry.
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text() + "- [shared/team/](shared/team/index.md)\n"
    )
    rc, _, err = _run(
        ["--wiki", str(wiki), "audit", "--fix", "--remove-stale"]
    )
    # The external stale entry stays in place; the audit still exits 1.
    assert rc != 0
    assert "shared/team" in (wiki / "index.md").read_text()
    assert "refusing to remove stale external" in err


def test_audit_remove_stale_requires_fix(tmp_path):
    """`--remove-stale` only makes sense with `--fix`; bare usage rejected."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "audit", "--remove-stale"])
    assert rc == 2
    assert "--fix" in err


def test_adopt_inserts_spaces_in_existing_bare_indexes(tmp_path):
    """Root has `foo/index.md` with no `## Spaces` and no nested children.
    After `init --adopt`, foo's index gets `## Spaces` inserted (proves
    the leaf is repaired, not just the chain walk-up). Root's `## Spaces`
    registers foo."""
    root = tmp_path / "adopted"
    root.mkdir()
    (root / "index.md").write_text("# adopted\n")
    (root / "foo").mkdir()
    (root / "foo" / "index.md").write_text("# foo\n")  # bare; no children

    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--adopt", "--no-config"])
    assert rc == 0
    assert "## Spaces" in (root / "foo" / "index.md").read_text()
    root_entries = _md.parse_section_entries(
        (root / "index.md").read_text(), "Spaces"
    )
    assert any(e.href and "foo/" in e.href for e in root_entries)


def test_adopt_repairs_root_even_with_no_nested_spaces(tmp_path):
    """Bare root + zero children → `--adopt` still inserts `## Spaces`
    into the root. Otherwise the spec floor is violated on day 1."""
    root = tmp_path / "empty"
    root.mkdir()
    (root / "index.md").write_text("# empty\n")
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--adopt", "--no-config"])
    assert rc == 0
    assert "## Spaces" in (root / "index.md").read_text()


def test_adopt_skips_externals_without_index_md(tmp_path):
    """With `--include-external`, the walker surfaces external boundary
    folders that aren't actually spaces (e.g. a stub `shared/foreign/`
    with no `index.md`). Adopt must skip those with a per-skip notice,
    not blow up trying to register a non-space."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    boundary = root / "shared" / "foreign"
    boundary.mkdir(parents=True)  # no index.md
    from wiki_spaces import init_wiki
    rc = init_wiki.main(
        [str(root), "--adopt", "--include-external", "--no-config"]
    )
    assert rc == 0
    # Boundary not registered (it isn't a space).
    entries = _md.parse_section_entries(
        (root / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "foreign" in e.href for e in entries)


def test_mount_refuses_bare_target(tmp_path):
    """A mounted target with `index.md` but no `## Spaces` is not a wiki
    under v1. Mount refuses and rolls back the mount rather than
    auto-inserting (auto-insert would mutate someone else's repo)."""
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.md").write_text("# external\n")  # bare; no `## Spaces`
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team",
         "--mode", "symlink"]
    )
    assert rc == 1
    assert "## Spaces" in err
    assert not (wiki / "shared" / "team").exists()


# ---------- PR-K: contract walker + inherited external + malformed entries + duplicate aliases ----------

def test_walk_via_spaces_contract_skips_unregistered(tmp_path):
    """The contract walker discovers spaces via `## Spaces` entries, not
    via the filesystem. An on-disk space not listed in its ancestor's
    `## Spaces` is INVISIBLE to it (audit's FS walker surfaces such drift
    separately — see the paired test below)."""
    wiki = _make_wiki(tmp_path)
    # Registered child.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "registered"])
    assert rc == 0
    # Drift: unregistered on-disk space.
    (wiki / "unregistered").mkdir()
    (wiki / "unregistered" / "index.md").write_text("# u\n\n## Spaces\n\n")

    paths = [str(p.relative_to(wiki)) for p, _ in space._walk_via_spaces_contract(wiki)]
    assert "registered" in paths
    assert "unregistered" not in paths


def test_walk_classified_yields_unregistered_for_audit(tmp_path):
    """The FS walker (`_walk_classified`) DOES yield unregistered spaces —
    that's how `space audit` detects drift. Paired with the contract test
    above to nail down the producer/consumer-vs-audit split."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "registered"])
    (wiki / "unregistered").mkdir()
    (wiki / "unregistered" / "index.md").write_text("# u\n\n## Spaces\n\n")

    paths = [
        str(p.relative_to(wiki))
        for p, classification, _ in space._walk_classified(wiki)
        if classification == "owned" and p != wiki
    ]
    assert "registered" in paths
    assert "unregistered" in paths


def test_walk_classified_descendants_inherit_external(tmp_path):
    """A child folder under a foreign-submodule mount that isn't itself a
    symlink/submodule must STILL be classified external (§39). The bare
    `_is_external` heuristic only catches the boundary; inheritance
    catches descendants."""
    import subprocess
    wiki = _make_wiki(tmp_path)
    # Make the wiki itself a git repo (required for the foreign-submodule check).
    subprocess.run(["git", "init", "-q"], cwd=wiki, check=True)
    # Hand-craft a `.gitmodules` declaring shared/team with a foreign origin.
    (wiki / ".gitmodules").write_text(
        '[submodule "shared/team"]\n'
        '\tpath = shared/team\n'
        '\turl = https://github.com/other/wiki\n'
    )
    boundary = wiki / "shared" / "team"
    boundary.mkdir(parents=True)
    (boundary / "index.md").write_text("# team\n\n## Spaces\n\n")
    # Plain descendant — not itself a symlink or submodule.
    nested = boundary / "subspace"
    nested.mkdir()
    (nested / "index.md").write_text("# sub\n\n## Spaces\n\n")

    yielded = list(space._walk_classified(wiki, include_external=True))
    classes_by_rel = {
        str(p.relative_to(wiki)): cls for p, cls, _ in yielded if p != wiki
    }
    assert classes_by_rel.get("shared/team") == "external"
    # Without inheritance, this would be "owned"; with inheritance it's external.
    assert classes_by_rel.get("shared/team/subspace") == "external"


def test_contract_walker_skips_escaping_href(tmp_path):
    """A `## Spaces` entry pointing at `../outside/index.md` (escapes after
    resolution) is silently skipped by the contract walker — audit
    reports it via the malformed-entries pass."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [outside/](../outside/index.md)\n")
    paths = [str(p.relative_to(wiki)) for p, _ in space._walk_via_spaces_contract(wiki)]
    assert paths == ["."]


def test_contract_walker_skips_absolute_href(tmp_path):
    """An absolute href is invalid — silently skipped."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [abs](/etc/passwd)\n")
    paths = [str(p.relative_to(wiki)) for p, _ in space._walk_via_spaces_contract(wiki)]
    assert paths == ["."]


def test_contract_walker_skips_external_by_default(tmp_path):
    """A `## Spaces` entry resolving to `shared/team/` is external — the
    contract walker skips it without `include_external`."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# team\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")
    paths = [str(p.relative_to(wiki)) for p, _ in space._walk_via_spaces_contract(wiki)]
    assert "shared/team" not in paths


def test_contract_walker_includes_external_with_flag(tmp_path):
    """`include_external=True` exposes external mounts. The yielded tuple's
    `is_external` flag is True for them."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# team\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")
    yielded = list(space._walk_via_spaces_contract(wiki, include_external=True))
    paths = {str(p.relative_to(wiki)): is_ext for p, is_ext in yielded}
    assert paths.get("shared/team") is True


def test_md_files_walker_descends_plain_folders_inside_registered_space(tmp_path):
    """Inside a registered space, plain folders (no `index.md`) are traversed
    for `.md` files — but child-space boundaries are skipped (those are
    owned by the contract walker)."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    # Plain folder inside the registered space.
    notes = wiki / "projects" / "foo" / "notes"
    notes.mkdir()
    (notes / "x.md").write_text("# x\n")
    # A nested registered space — the md-files walker must NOT yield its
    # content (the contract walker owns it).
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo/child"])
    assert rc == 0
    (wiki / "projects" / "foo" / "child" / "page.md").write_text("# child page\n")

    files = [
        str(f.relative_to(wiki))
        for f, _ in space._walk_md_files_via_contract(wiki)
    ]
    assert "projects/foo/notes/x.md" in files
    # child/page.md is yielded by visiting the child SPACE — the walker
    # iterates the child once via the contract, then iterates THAT space's
    # plain folders. So page.md DOES show up.
    assert "projects/foo/child/page.md" in files


def test_md_files_walker_skips_escaping_symlink_in_plain_folder(tmp_path):
    """An escaping symlink under a plain folder is external — skipped
    unless `include_external=True`."""
    wiki = _make_wiki(tmp_path)
    notes = wiki / "notes"
    notes.mkdir()
    (notes / "ok.md").write_text("# ok\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# secret\n")
    import os
    os.symlink(outside, notes / "link")

    files_default = [
        str(f.relative_to(wiki))
        for f, _ in space._walk_md_files_via_contract(wiki)
    ]
    assert "notes/ok.md" in files_default
    # The escaping symlink's content must NOT appear by default.
    assert not any("link/secret.md" in f for f in files_default)


def test_audit_flags_malformed_spaces_entries(tmp_path):
    """Audit reports malformed `## Spaces` entries (empty href, absolute,
    `..`, escape, duplicate) and flips the exit code."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [empty]()\n"
        + "- [abs](/etc/passwd)\n"
        + "- [dotdot](../outside)\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "malformed" in out
    assert "empty href" in out
    assert "absolute href" in out
    assert "href contains `..`" in out


def test_audit_fix_does_not_repair_malformed_entries(tmp_path):
    """`audit --fix` repairs drift (insert section, register missing
    entries) but NOT malformed entries — those signal author intent the
    framework can't reconstruct."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    bad_line = "- [empty]()\n"
    idx.write_text(idx.read_text() + bad_line)
    rc, _, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 1  # malformed still reported as an error
    assert bad_line in (wiki / "index.md").read_text()


def test_audit_flags_duplicate_aliases(tmp_path):
    """When two owned pages declare the same alias, wikilink resolution is
    nondeterministic. Audit reports the collision."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a.md").write_text("---\naliases: [shared]\n---\n# a\n")
    (wiki / "b.md").write_text("---\naliases: [SHARED]\n---\n# b\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "duplicate alias" in out
    assert "shared" in out
    assert "a.md" in out and "b.md" in out


def test_audit_duplicate_alias_respects_include_external(tmp_path):
    """External pages aren't audited for duplicate-alias collisions unless
    `--include-external` opts in."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").mkdir()
    (wiki / "shared" / "team-page.md").write_text("---\naliases: [shared]\n---\n# t\n")
    (wiki / "owned.md").write_text("---\naliases: [shared]\n---\n# o\n")
    rc_default, out_default, _ = _run(["--wiki", str(wiki), "audit"])
    # Without --include-external, the external page isn't visible.
    assert rc_default == 0 or "duplicate alias" not in out_default

    rc_ext, out_ext, _ = _run(
        ["--wiki", str(wiki), "audit", "--include-external"]
    )
    assert rc_ext == 1
    assert "duplicate alias" in out_ext


# ---------- PR-L2: space list / space files / audit --json ----------

def test_space_list_shows_registered_only(tmp_path):
    """`space list` walks via `## Spaces` — registered children appear,
    unregistered on-disk content does not."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "registered"])
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# drift\n\n## Spaces\n\n")

    rc, out, _ = _run(["--wiki", str(wiki), "list"])
    assert rc == 0
    assert "registered" in out
    assert "drift" not in out


def test_space_list_include_external_shows_external_too(tmp_path):
    """`--include-external` opts external mounts into the listing."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# t\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")
    rc_def, out_def, _ = _run(["--wiki", str(wiki), "list"])
    assert "shared/team" not in out_def
    rc_ext, out_ext, _ = _run(["--wiki", str(wiki), "list", "--include-external"])
    assert rc_ext == 0
    assert "shared/team\texternal" in out_ext


def test_space_list_json_emits_path_label_description_external(tmp_path):
    """JSON output carries label and description so the placement
    classifier in skills can disambiguate."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki), "add", "projects/foo",
        "--description", "per-project content",
    ])
    assert rc == 0
    rc2, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    assert rc2 == 0
    items = _json.loads(out)
    # Wiki root is excluded from JSON list.
    paths = {it["path"] for it in items}
    assert "." not in paths
    foo_entry = next(it for it in items if it["path"] == "projects/foo")
    assert foo_entry["label"]
    assert foo_entry["external"] is False


def test_space_list_include_boundaries_requires_include_external(tmp_path):
    """`--include-boundaries` alone is rejected — it's only meaningful when
    we're already opted into externals."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "list", "--include-boundaries"])
    assert rc == 2
    assert "--include-external" in err


def test_space_files_scope_restricts_to_subspace(tmp_path):
    """A `space` argument scopes output to files under that space only."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    (wiki / "projects" / "foo" / "page.md").write_text("# p\n")
    (wiki / "topnote.md").write_text("# top\n")
    rc2, out, _ = _run(["--wiki", str(wiki), "files", "projects/foo"])
    assert rc2 == 0
    assert "projects/foo/page.md" in out
    assert "topnote.md" not in out


def test_space_files_refuses_unregistered_scope(tmp_path):
    """An on-disk folder that isn't in any `## Spaces` is invisible to the
    consumer — refusing here closes the back-door."""
    wiki = _make_wiki(tmp_path)
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# d\n\n## Spaces\n\n")
    (wiki / "drift" / "page.md").write_text("# d\n")
    rc, _, err = _run(["--wiki", str(wiki), "files", "drift"])
    assert rc == 2
    assert "not reachable" in err


def test_audit_json_emits_structured_payload(tmp_path):
    """`audit --json` emits one JSON document; exit code matches the
    human-text run."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    # Seed drift, broken wikilink, malformed entry.
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# d\n\n## Spaces\n\n")
    (wiki / "page.md").write_text("# p\n\n[[no-such-page]]\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [empty]()\n")

    rc_h, _, _ = _run(["--wiki", str(wiki), "audit"])
    rc_j, out_j, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc_j == rc_h
    payload = _json.loads(out_j)
    assert payload["exit_code"] == rc_j
    assert any(d["missing"] for d in payload["drift"])
    assert any("no-such-page" in b["target"] for b in payload["broken_wikilinks"])
    assert any("empty" in m["issue"] for m in payload["malformed_entries"])


# ---------- PR-L: size discipline + check-size + outgoing-link mask ----------

def test_check_size_ok(tmp_path):
    """`space check-size` returns OK for under-cap content."""
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "page.md",
        "--projected-stdin",
    ], stdin="# short body\n")
    assert rc == 0
    assert out.startswith("OK ")


def test_check_size_over_exits_1(tmp_path):
    """OVER outcome exits 1 — skills gate writes on this."""
    wiki = _make_wiki(tmp_path)
    huge = "x" * 16000
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "page.md",
        "--projected-stdin",
    ], stdin=huge)
    assert rc == 1
    assert out.startswith("OVER ")


def test_check_size_rejects_absolute_path(tmp_path):
    """Path must be wiki-root-relative."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "check-size", "/etc/passwd",
        "--projected-stdin",
    ], stdin="x")
    assert rc == 2
    assert "wiki-root-relative" in err


def test_add_refuses_over_cap_description_before_mkdir(tmp_path):
    """`space add --description <huge>` must refuse BEFORE creating any
    directory. Otherwise rejection leaves a stranded empty folder."""
    wiki = _make_wiki(tmp_path)
    huge = "x" * 6000  # over the 5000-char `index.md` cap
    rc, _, err = _run([
        "--wiki", str(wiki), "add", "newproj", "--description", huge,
    ])
    assert rc == 2
    assert "size cap" in err
    assert not (wiki / "newproj").exists(), \
        "new directory was created despite cap rejection"


def test_promote_preflights_size_caps_before_rename(tmp_path):
    """All projected writes are checked BEFORE the rename. An over-cap
    ancestor projection aborts the promote without moving the source."""
    wiki = _make_wiki(tmp_path)
    # Push the wiki root just under the cap so the entry-add pushes over.
    idx = wiki / "index.md"
    bulky = "x" * 4990
    idx.write_text(idx.read_text() + bulky + "\n")
    (wiki / "page.md").write_text("# page\n\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "size cap" in err
    # Source unmoved.
    assert (wiki / "page.md").is_file()
    assert not (wiki / "page").exists()


def test_promote_outgoing_link_does_not_rewrite_code_block(tmp_path):
    """§29 — the outgoing-link adjustment must mask code spans, same as
    the cross-page rewriter. A `[label](sibling.md)` inside a fenced
    code block is documentation, not a real link."""
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_text("# sibling\n")
    page = wiki / "page.md"
    page.write_text(
        "# page\n\n"
        "```\n"
        "[sibling](sibling.md)\n"
        "```\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    moved = (wiki / "page" / "index.md").read_text()
    # The code block's `[sibling](sibling.md)` must survive unchanged —
    # NOT be rewritten to `(../sibling.md)`.
    assert "[sibling](sibling.md)" in moved
    assert "../sibling.md" not in moved


def test_promote_outgoing_link_does_not_rewrite_frontmatter(tmp_path):
    """§29 — frontmatter strings shaped like markdown links shouldn't be
    rewritten by the outgoing-link adjustment."""
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_text("# sibling\n")
    page = wiki / "page.md"
    page.write_text(
        "---\n"
        "source: \"[ref](sibling.md)\"\n"
        "---\n"
        "# page\n\n"
        "body\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    moved = (wiki / "page" / "index.md").read_text()
    # Frontmatter literal survives.
    assert "[ref](sibling.md)" in moved
    assert "[ref](../sibling.md)" not in moved


def test_promote_in_lock_size_check_catches_concurrent_growth(tmp_path, monkeypatch):
    """The outer preflight projects against the OUTER-read ancestor text.
    A concurrent writer can grow the ancestor between preflight and lock,
    so `_promote_mutate` re-checks inside the locked region against the
    actual projected text. Inject a concurrent write that pushes the
    ancestor over the cap right before the mutate fn runs; assert the
    helper aborts via the `(None, rc, reason)` tuple."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")

    real_atomic = space._atomic_mutate_index
    target_index = (wiki / "index.md").resolve()

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        # Only sabotage the ancestor write; other index writes (e.g. the
        # new space's own index.md) flow through unchanged.
        if ancestor_index.resolve() == target_index:
            existing = ancestor_index.read_text(encoding="utf-8")
            # Push the ancestor close to the cap so the entry add tips over.
            bulky = "x" * 4980
            ancestor_index.write_text(existing + bulky + "\n", encoding="utf-8")
        return real_atomic(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space, "_atomic_mutate_index", patched_atomic)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc != 0
    assert "size cap" in err
    # Source is restored (snapshot rollback ran).
    assert (wiki / "page.md").is_file()


def test_init_refuses_over_cap_description(tmp_path):
    """init's `--description` writes to `index.md` whose cap is 5000.
    A 6000-char description is refused; no `index.md` is left behind."""
    huge = "x" * 6000
    root = tmp_path / "wiki"
    from wiki_spaces import init_wiki
    rc = init_wiki.main([
        str(root), "--description", huge, "--no-config",
    ])
    # init's write helper logs the size-cap skip but exits 0 (other writes
    # may still happen). The critical check: the over-cap index.md is NOT
    # on disk after the call.
    assert not (root / "index.md").is_file(), \
        "init wrote an over-cap index.md despite the size-cap helper"


# ---------- PR-M: log rotation rejection + audit summary scope ----------

def test_log_rotation_rejects_when_still_over_cap(tmp_path):
    """A single log entry bigger than half the cap (or a cap small enough
    that even the kept half plus the new entry overflows) must be refused
    rather than silently committed. Preserves the no-silent-truncation
    guarantee from CONVENTIONS / Size discipline."""
    wiki = _make_wiki(tmp_path)
    log = wiki / "log.md"
    log.write_text("# Log\n")
    # Use --create to scaffold log.md, then push a huge --raw entry through
    # a tiny cap configured via `_meta/limits.md`.
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| log.md | 100 |\n"
    )
    huge_entry = "- [2026-01-01T00:00:00Z] " + ("x" * 500)
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", huge_entry])
    assert rc != 0
    assert "too large" in err or "cap" in err


def test_audit_summary_respects_include_external(tmp_path):
    """`audit --include-external` summary's page count includes external
    pages — otherwise the report's claim "owned scope; excludes external"
    contradicts the per-page checks below it that DO walk external."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# t\n\n## Spaces\n\n")
    (wiki / "shared" / "team" / "page.md").write_text("# external page\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")

    rc_def, out_def, _ = _run(["--wiki", str(wiki), "audit"])
    rc_ext, out_ext, _ = _run(["--wiki", str(wiki), "audit", "--include-external"])
    assert rc_def == 0 and rc_ext == 0
    # The default scope counts only the owned `index.md`; the external-
    # inclusive scope counts the external `index.md` and `page.md` too.
    assert "1 markdown files (owned scope" in out_def
    # External scope: at least 3 (root, shared/team/index.md, shared/team/page.md)
    assert "owned + external scope" in out_ext


def test_audit_excludes_archives_space_from_drift(tmp_path):
    """A space under `_archives/` is retired content — not flagged as a
    missing `## Spaces` entry."""
    wiki = _make_wiki(tmp_path)
    archived = wiki / "_archives" / "old-space"
    archived.mkdir(parents=True)
    (archived / "index.md").write_text("# old-space")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "old-space" not in out


def test_audit_embed_of_asset_not_broken(tmp_path):
    """`![[image.png]]` / `![[doc.pdf]]` embed non-page assets — never flagged
    as broken wikilinks."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\n![[diagram.png]] and ![[report.pdf]]\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "! broken wikilink" not in out
    assert "diagram.png" not in out
    assert "report.pdf" not in out


def test_audit_embed_of_existing_note_counts_as_incoming(tmp_path):
    """An embed `![[note]]` of a real page is a reference — the embedded page
    is not an orphan."""
    wiki = _make_wiki(tmp_path)
    (wiki / "host.md").write_text("# Host\n\n![[embedded]]\n")
    (wiki / "embedded.md").write_text("# Embedded\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "embedded.md" not in orphan_section


def test_audit_plain_link_still_broken(tmp_path):
    """The embed exemption must not suppress genuine broken plain links."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\nplain [[missing]] link\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "! broken wikilink [[missing]]" in out


# ---------- space mount ----------

import shutil as _shutil

_HAS_GIT = _shutil.which("git") is not None


def _make_space_dir(path: Path, title: str = "mounted") -> Path:
    """A plain external space: a folder with `index.md` carrying `## Spaces`
    (the v1 spec floor — mounted targets must satisfy it for mount to accept)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.md").write_text(f"# {title}\n\n## Spaces\n\n")
    return path


def _make_git_repo(path: Path, title: str = "cloned", *, with_index: bool = True) -> Path:
    """A real local git repo with one commit (for clone tests).

    With `with_index` (default) the repo contains an `index.md` with
    `## Spaces` (the v1 spec floor); otherwise only `notes.md` — used to
    exercise the not-a-wiki mount path.
    """
    import os
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    if with_index:
        (path / "index.md").write_text(f"# {title}\n\n## Spaces\n\n")
    else:
        (path / "notes.md").write_text(f"# {title} notes\n")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    }
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=path, check=True, env=env, capture_output=True)
    return path


def test_mount_symlink_creates_link_and_registers(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "external-src", "team")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 0, out
    link = wiki / "shared" / "team"
    assert link.is_symlink()
    assert (link / "index.md").is_file()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "shared/team" in e.href for e in entries)


def test_mount_symlink_source_without_index_refused(tmp_path):
    """A mount target with no index.md is not a wiki-spaces space — refuse it
    and leave nothing behind."""
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "not-a-wiki"
    src.mkdir()
    (src / "notes.md").write_text("# notes")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/x", "--mode", "symlink"]
    )
    assert rc == 1
    assert "index.md" in err
    assert not (wiki / "shared" / "x").exists()
    assert not (wiki / "shared" / "x").is_symlink()


def test_mount_inserts_spaces_into_bare_ancestor_and_registers(tmp_path):
    """Like `space add`, mount auto-inserts `## Spaces` into a bare-ancestor
    `index.md` via the chain helper rather than refusing. Both operations
    walk through the same code path now; replaces the pre-v1 refusal."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    src = _make_space_dir(tmp_path / "src")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "team", "--mode", "symlink"]
    )
    assert rc == 0, err
    assert (wiki / "team").is_symlink()
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "team" in e.href for e in entries)


def test_mount_refused_when_dest_exists(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "occupied").mkdir()
    src = _make_space_dir(tmp_path / "src")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "occupied", "--mode", "symlink"]
    )
    assert rc == 2
    assert "already exists" in err


def test_mount_rejects_dotdot_path(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src")
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "../escape", "--mode", "symlink"]
    )
    assert rc == 2


def test_mount_submodule_refused_when_wiki_not_git(tmp_path):
    """`--mode submodule` needs the wiki itself to be a git repo."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", "https://example.invalid/x.git",
         "shared/x", "--mode", "submodule"]
    )
    assert rc == 2
    assert "git repo" in err


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_mount_clone_copies_and_registers(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_git_repo(tmp_path / "src-repo", "team-wiki")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "clone"]
    )
    assert rc == 0, out
    assert (wiki / "shared" / "team" / "index.md").is_file()
    assert (wiki / "shared" / "team" / ".git").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "shared/team" in e.href for e in entries)


def test_mount_symlink_bad_source_leaves_no_parent_dir(tmp_path):
    """A nonexistent symlink source is rejected before any directory is
    created — no empty `shared/` left behind."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(tmp_path / "nope"), "shared/team",
         "--mode", "symlink"]
    )
    assert rc == 2
    assert not (wiki / "shared").exists()


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_mount_clone_without_index_is_cleaned_up(tmp_path):
    """A clone of a repo with no index.md is not a wiki-spaces space — the
    command refuses it and removes the clone it just made."""
    wiki = _make_wiki(tmp_path)
    src = _make_git_repo(tmp_path / "src-repo", with_index=False)
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "clone"]
    )
    assert rc == 1
    assert "index.md" in err
    assert not (wiki / "shared" / "team").exists()


# ---------- _derive_default_path (unit) ----------

def test_derive_default_https_with_dot_git():
    p, err = space._derive_default_path("https://github.com/foo/bar.git")
    assert err is None
    assert p == "shared/bar"


def test_derive_default_https_with_query_dropped_first():
    """Query is dropped BEFORE .git stripping, so .git?ref=main resolves to bar."""
    p, err = space._derive_default_path("https://github.com/foo/bar.git?ref=main")
    assert err is None
    assert p == "shared/bar"


def test_derive_default_scp_style_with_fragment():
    """Fragment dropped first; scp-style tail split after `:`."""
    p, err = space._derive_default_path("git@github.com:foo/bar.git#tag")
    assert err is None
    assert p == "shared/bar"


def test_derive_default_local_trailing_slash():
    p, err = space._derive_default_path("/home/me/notes/")
    assert err is None
    assert p == "shared/notes"


def test_derive_default_local_path_no_slash_in_tail():
    p, err = space._derive_default_path("/home/me/personal-notes")
    assert err is None
    assert p == "shared/personal-notes"


def test_derive_default_rejects_empty_source():
    p, err = space._derive_default_path("")
    assert p is None
    assert err is not None


def test_derive_default_rejects_just_root_slash():
    p, err = space._derive_default_path("/")
    assert p is None
    assert err is not None
    assert "empty" in err.lower() or "basename" in err.lower()


def test_derive_default_rejects_dot_git_basename():
    """An input whose tail IS `.git` (not just ends in it) is rejected to avoid
    pointing at a bare-repo directory."""
    p, err = space._derive_default_path("/home/me/.git")
    assert p is None
    assert err is not None
    assert "." in err  # mentions the dot-prefix problem


def test_derive_default_strips_dot_git_suffix_only_not_when_tail_is_dot_git():
    """`repo.git` → `repo`; but `.git` (tail equals `.git`) is rejected."""
    p1, _ = space._derive_default_path("https://x/y/repo.git")
    assert p1 == "shared/repo"
    p2, err2 = space._derive_default_path("https://x/y/.git")
    assert p2 is None
    assert err2 is not None


# ---------- mount: --mode ----------

def test_mount_mode_works_canonically(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 0, out
    assert (wiki / "shared" / "team").is_symlink()


def test_mount_missing_mode_fails(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    with pytest.raises(SystemExit):
        _run(["--wiki", str(wiki), "mount", str(src), "shared/team"])


# ---------- mount: optional path → default derivation ----------

def test_mount_optional_path_derives_shared_basename(tmp_path):
    wiki = _make_wiki(tmp_path)
    # The default-path basename is taken from the SOURCE path's tail, so
    # we name the source directory itself `my-team-wiki`.
    src = _make_space_dir(tmp_path / "my-team-wiki", "Team Wiki")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "--mode", "symlink"]
    )
    assert rc == 0, out
    # Default-derived path is `shared/my-team-wiki/`.
    assert (wiki / "shared" / "my-team-wiki").is_symlink()


def test_mount_optional_path_rejects_when_basename_starts_with_dot(tmp_path):
    wiki = _make_wiki(tmp_path)
    src_parent = tmp_path / "src-parent"
    src_parent.mkdir()
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src_parent / ".git"), "--mode", "symlink"]
    )
    # Default derivation rejects `.git` basename before symlink validation.
    assert rc == 2
    assert "." in err


# ---------- mount: --dry-run ----------

def test_mount_dry_run_no_fs_mutation_symlink(tmp_path):
    """--dry-run prints the plan and touches nothing: no symlink, no parent
    dir created, parent index byte-identical."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    index_before = (wiki / "index.md").read_bytes()
    assert not (wiki / "shared").exists()  # baseline
    rc, out, _ = _run([
        "--wiki", str(wiki), "mount", str(src), "shared/team",
        "--mode", "symlink", "--dry-run",
    ])
    assert rc == 0
    assert "(dry-run)" in out
    assert not (wiki / "shared").exists()  # no mkdir
    assert not (wiki / "shared" / "team").exists()  # no symlink
    assert (wiki / "index.md").read_bytes() == index_before


def test_mount_dry_run_does_not_mutate_bare_ancestor(tmp_path):
    """Dry-run must not insert `## Spaces` into a bare-`index.md` ancestor —
    the section is inserted only when the mount actually commits."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    src = _make_space_dir(tmp_path / "src", "team")
    before = (wiki / "index.md").read_text()
    rc, _, err = _run([
        "--wiki", str(wiki), "mount", str(src), "team",
        "--mode", "symlink", "--dry-run",
    ])
    assert rc == 0, err
    assert (wiki / "index.md").read_text() == before
    assert not (wiki / "team").exists()


# ---------- mount: --name ----------

def test_mount_name_overrides_parent_label_only(tmp_path):
    """--name affects only the parent's ## Spaces entry label; the child's
    index.md is untouched."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "external-team")
    child_index_before = (src / "index.md").read_bytes()
    rc, out, _ = _run([
        "--wiki", str(wiki), "mount", str(src), "shared/team",
        "--mode", "symlink", "--name", "Team Wiki",
    ])
    assert rc == 0, out
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.label == "Team Wiki" for e in entries), \
        f"expected 'Team Wiki' label, got entries: {[(e.label, e.href) for e in entries]}"
    # Child untouched. (Symlink follows; reading via src is fine.)
    assert (src / "index.md").read_bytes() == child_index_before


# ---------- mount: atomicity ----------

def test_mount_rolls_back_when_index_write_fails(tmp_path, monkeypatch):
    """Force `os.replace` to fail during registration; assert the mount was
    rolled back (symlink removed) and the parent index is unchanged."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    index_before = (wiki / "index.md").read_bytes()

    real_replace = space.os.replace
    call_count = {"n": 0}

    def boom(src_path, dst_path, *a, **kw):
        # Only sabotage the index write (the .tmp → index.md replace),
        # not any unrelated os.replace call.
        if str(dst_path).endswith("index.md"):
            call_count["n"] += 1
            raise OSError("forced replace failure for atomicity test")
        return real_replace(src_path, dst_path, *a, **kw)

    monkeypatch.setattr(space.os, "replace", boom)
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 1
    assert call_count["n"] == 1
    assert "ensure-chain failed" in err
    # Symlink rolled back.
    assert not (wiki / "shared" / "team").exists()
    assert not (wiki / "shared" / "team").is_symlink()
    # Parent index byte-identical.
    assert (wiki / "index.md").read_bytes() == index_before
    # No leftover tempfile in the ancestor dir.
    leftover_tmps = [p for p in wiki.iterdir() if p.name.startswith(".index.") and p.name.endswith(".tmp")]
    assert leftover_tmps == [], f"tempfile leak: {leftover_tmps}"


def test_adopt_registers_nested_space_no_audit_drift(tmp_path):
    """Adopting a folder that already has a nested space: `init --adopt`
    walks the tree, registers every space in its nearest ancestor's
    `## Spaces`, so audit reports zero drift on day 1."""
    folder = tmp_path / "my-notes"
    nested = folder / "projects" / "foo"
    nested.mkdir(parents=True)
    (nested / "index.md").write_text("# foo")
    assert init_wiki.main([str(folder), "--no-config", "--adopt"]) == 0
    # `## Spaces` is always present (every CLI-created wiki has it from t=0),
    # AND the nested space has been registered in it.
    root_index = (folder / "index.md").read_text()
    assert "## Spaces" in root_index
    assert "projects/foo/" in root_index
    rc, out, _ = _run(["--wiki", str(folder), "audit"])
    assert rc == 0, out
    assert "missing entry" not in out


def test_adopt_skips_shared_with_stderr_notice(tmp_path):
    """`--adopt` does NOT register sub-spaces under `shared/` (classified
    external) and emits a per-skip notice to stderr unless --include-external."""
    folder = tmp_path / "my-notes"
    shared_nested = folder / "shared" / "baz"
    shared_nested.mkdir(parents=True)
    (shared_nested / "index.md").write_text("# baz")
    owned_nested = folder / "projects" / "foo"
    owned_nested.mkdir(parents=True)
    (owned_nested / "index.md").write_text("# foo")

    import io
    from contextlib import redirect_stdout, redirect_stderr

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = init_wiki.main([str(folder), "--no-config", "--adopt"])
    assert rc == 0
    root_index = (folder / "index.md").read_text()
    assert "projects/foo/" in root_index
    assert "shared/baz" not in root_index
    assert "shared/baz" in err.getvalue() or "shared/" in err.getvalue()
# ---------- _is_in_external_scope: ancestor-walking trust-scope preflight ----------

def _space_log_worker(args):
    """Module-level worker for the contention test (must be pickleable for
    multiprocessing.Pool — closures can't cross the fork boundary)."""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from wiki_spaces import space as _space
    wiki_str, i = args
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        # Structured form — CLI prepends the timestamp itself. log.md is
        # pre-created by the test setup, so no `--create` needed.
        return _space.main([
            "--wiki", wiki_str, "log",
            "CONTEND", "--field", f"value={i}",
        ])


def test_space_log_refuses_when_log_md_absent(tmp_path):
    """PR-H made logging opt-in: when `log.md` doesn't exist, `space log`
    refuses with a hint about `--create` / `init --with log.md`."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "log", "SEARCH", "--field", "query=foo",
    ])
    assert rc == 2
    assert "log.md" in err
    assert "--create" in err
    assert not (wiki / "log.md").exists()


def test_space_log_create_flag_creates_log_md(tmp_path):
    """`--create` is the documented opt-in: scaffolds `log.md` then appends."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki), "log", "SEARCH",
        "--field", "query=sourdough",
        "--create",
    ])
    assert rc == 0
    body = (wiki / "log.md").read_text()
    assert "SEARCH query=sourdough" in body


def test_space_log_structured_field_prepends_iso_timestamp(tmp_path):
    """Structured form: CLI emits `- [<ISO-8601 UTC>] OPERATION key=value`."""
    import re
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("# Log\n")
    rc, _, _ = _run([
        "--wiki", str(wiki), "log", "tend",  # lowercased → CLI uppercases
        "--field", "mode=audit",
        "--field", "issues_found=3",
    ])
    assert rc == 0
    body = (wiki / "log.md").read_text()
    last = [ln for ln in body.splitlines() if ln.startswith("- [")][-1]
    # Format: `- [YYYY-MM-DDTHH:MM:SSZ] TEND mode=audit issues_found=3`
    assert re.match(
        r"- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] TEND mode=audit issues_found=3",
        last,
    ), f"unexpected line shape: {last!r}"


def test_space_log_raw_form_does_not_format(tmp_path):
    """--raw bypasses the structured formatter; the line lands verbatim."""
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("# Log\n")
    rc, _, _ = _run([
        "--wiki", str(wiki), "log",
        "--raw", "- custom shape no timestamp",
    ])
    assert rc == 0
    body = (wiki / "log.md").read_text()
    assert "- custom shape no timestamp" in body


def test_space_log_atomic_under_contention(tmp_path):
    """100 concurrent `space log` invocations via multiprocessing.Pool —
    every line lands in log.md with no losses. Exercises the flock-
    protected critical section in `_limits.append_log_with_rotation`."""
    import multiprocessing
    import os
    if os.name == "nt":  # pragma: no cover
        pytest.skip("flock-based atomicity not enforced on Windows")
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires fork-capable multiprocessing")
    wiki = _make_wiki(tmp_path)
    # PR-H: pre-create log.md (the workers no longer auto-create).
    (wiki / "log.md").write_text("# Log\n")

    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(processes=8) as pool:
        args = [(str(wiki), i) for i in range(100)]
        results = pool.map(_space_log_worker, args)
    assert all(r == 0 for r in results), f"some workers failed: {results}"
    body = (wiki / "log.md").read_text()
    written = sum(1 for line in body.splitlines() if "CONTEND value=" in line)
    assert written == 100, f"expected 100 lines, got {written}"


def test_space_manifest_subcommand_removed(tmp_path):
    """PR-I demoted `.manifest.json` writes from a CLI subcommand to a
    CONVENTIONS.md snippet (use an inline `flock` block from the wiki-update
    skill). `space manifest` should no longer exist in the argparse surface."""
    wiki = _make_wiki(tmp_path)
    with pytest.raises(SystemExit):
        _run(["--wiki", str(wiki), "manifest", "set", "p", "k", "v"])


def _force_chain_helper_failure(monkeypatch):
    """Make every ancestor write inside the chain helper fail. PR-D routes
    `cmd_add`'s registration through `_ensure_spaces_chain_and_register`,
    which calls `_atomic_mutate_index` per ancestor edge. Patching the
    helper itself is the cleanest way to exercise the rollback paths."""
    monkeypatch.setattr(
        space, "_atomic_mutate_index",
        lambda ancestor, ancestor_index, mutate_fn: (1, "simulated write failure"),
    )


def test_space_add_rollback_removes_created_dir_on_registration_failure(tmp_path, monkeypatch):
    """Defect #2 + PR-D: when the chain helper fails mid-registration,
    rollback must remove the dir and its index.md."""
    wiki = _make_wiki(tmp_path)
    _force_chain_helper_failure(monkeypatch)

    rc, _, err = _run(["--wiki", str(wiki), "add", "newproj"])
    assert rc == 1
    assert "simulated write failure" in err
    # The created directory must be gone after rollback.
    assert not (wiki / "newproj").exists()
    # The ancestor's ## Spaces should not have a stray entry.
    root_index = (wiki / "index.md").read_text()
    assert "newproj" not in root_index


def test_space_add_rollback_preserves_preexisting_dir(tmp_path, monkeypatch):
    """If the target dir EXISTED before the call, rollback must NOT delete
    it — only the index.md we wrote (if we wrote it) should go away."""
    wiki = _make_wiki(tmp_path)
    (wiki / "newproj").mkdir()
    (wiki / "newproj" / "user-file.txt").write_text("user content")
    _force_chain_helper_failure(monkeypatch)

    rc, _, _ = _run(["--wiki", str(wiki), "add", "newproj"])
    assert rc == 1
    # Pre-existing user content must survive.
    assert (wiki / "newproj").exists()
    assert (wiki / "newproj" / "user-file.txt").read_text() == "user content"


def test_add_rollback_removes_created_parent_folders(tmp_path, monkeypatch):
    """`space add a/b/c/d` against an empty wiki creates a/, a/b/, a/b/c/,
    a/b/c/d/ via mkdir(parents=True). When registration fails, rollback must
    remove every parent it created — not just the leaf."""
    wiki = _make_wiki(tmp_path)
    _force_chain_helper_failure(monkeypatch)

    rc, _, err = _run(["--wiki", str(wiki), "add", "a/b/c/d"])
    assert rc == 1
    assert "simulated write failure" in err
    # Every directory created by this call must be removed (deepest-first).
    assert not (wiki / "a" / "b" / "c" / "d").exists()
    assert not (wiki / "a" / "b" / "c").exists()
    assert not (wiki / "a" / "b").exists()
    assert not (wiki / "a").exists()


def test_add_rollback_only_removes_empty_parents(tmp_path, monkeypatch):
    """If one of the parent dirs already had user content, rollback removes
    the ones it created but leaves a non-empty pre-existing parent intact."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a").mkdir()
    (wiki / "a" / "user-file.txt").write_text("user content")
    _force_chain_helper_failure(monkeypatch)

    rc, _, _ = _run(["--wiki", str(wiki), "add", "a/b/c"])
    assert rc == 1
    # `a/` pre-existed and has user content — must survive.
    assert (wiki / "a" / "user-file.txt").read_text() == "user content"
    # `a/b/` and `a/b/c/` were created by this call — must be gone.
    assert not (wiki / "a" / "b").exists()


def test_space_remove_rollback_restores_dir_on_rmtree_failure(tmp_path, monkeypatch):
    """Defect #2 part 2: when rmtree fails mid-delete (some files gone,
    others remain), rollback restores the directory byte-for-byte AND
    re-adds the index entry that was just removed."""
    wiki = _make_wiki(tmp_path)
    (wiki / "doomed").mkdir()
    (wiki / "doomed" / "index.md").write_text("# Doomed\n\nstuff\n")
    (wiki / "doomed" / "extra.md").write_text("user content here")
    # Pre-register so the ancestor's ## Spaces has the entry to remove.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "doomed"])
    assert rc == 0

    real_rmtree = space.shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        # Let the snapshot copytree call succeed (it goes elsewhere).
        if "wiki-spaces-remove-" in str(path):
            return real_rmtree(path, *args, **kwargs)
        raise OSError("simulated rmtree failure")

    monkeypatch.setattr(space.shutil, "rmtree", failing_rmtree)

    rc, _, err = _run([
        "--wiki", str(wiki), "remove", "doomed", "--force",
    ])
    assert rc == 2
    assert "rmtree failed" in err
    # Directory must be restored.
    assert (wiki / "doomed").exists()
    assert (wiki / "doomed" / "extra.md").read_text() == "user content here"
    # Index entry must be restored.
    root_index = (wiki / "index.md").read_text()
    assert "doomed/" in root_index


def test_promote_does_not_rewrite_links_inside_code_blocks(tmp_path):
    """Defect #3: promote's link rewrite must NOT touch `[[wikilinks]]` that
    appear inside fenced code blocks — those are code examples, not real
    links. Pre-fix, the scanner saw them; post-fix, the offset-preserving
    mask hides them."""
    wiki = _make_wiki(tmp_path)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "foo.md").write_text("# Foo\n\ncontent\n")
    # A page with TWO instances of `[[foo]]`: one real link, one in a code block.
    (wiki / "doc.md").write_text(
        "# Doc\n"
        "\n"
        "Real link: [[foo]]\n"
        "\n"
        "```\n"
        "Code example: [[foo]] should NOT be rewritten\n"
        "```\n"
    )

    rc, _, _ = _run(["--wiki", str(wiki), "promote", "concepts/foo.md"])
    assert rc == 0

    rewritten = (wiki / "doc.md").read_text()
    # The real link should be rewritten to the pathful form.
    assert "[[concepts/foo/index|foo]]" in rewritten
    # The code-block link should remain literal `[[foo]]`.
    assert "[[foo]] should NOT be rewritten" in rewritten


def test_promote_does_not_rewrite_links_inside_frontmatter(tmp_path):
    """Defect #3 part 2: links inside YAML frontmatter (e.g., an aliases
    field that happens to contain `[[foo]]` syntax) are not real wikilinks
    and must not be touched."""
    wiki = _make_wiki(tmp_path)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "foo.md").write_text("# Foo\n\ncontent\n")
    (wiki / "doc.md").write_text(
        "---\n"
        "title: Doc\n"
        "aliases: [\"[[foo]]\"]\n"
        "---\n"
        "\n"
        "Real link: [[foo]]\n"
    )

    rc, _, _ = _run(["--wiki", str(wiki), "promote", "concepts/foo.md"])
    assert rc == 0

    rewritten = (wiki / "doc.md").read_text()
    # The body link should be rewritten.
    assert "Real link: [[concepts/foo/index|foo]]" in rewritten
    # The frontmatter `aliases: ["[[foo]]"]` should remain literal.
    assert 'aliases: ["[[foo]]"]' in rewritten


def test_audit_include_external_walks_shared_subtree(tmp_path):
    """`audit --include-external` opts the read path into externally-
    classified spaces. Without the flag, a page under `shared/` is invisible
    to audit; with the flag, it's scanned for size violations and broken
    links just like an owned page."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern   | Cap (chars) |\n"
        "|-----------|-------------|\n"
        "| big.md    |          50 |\n"
    )
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# team\n\n## Spaces\n\n")
    (wiki / "shared" / "team" / "big.md").write_text("x" * 100 + "\n")

    rc_default, out_default, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc_default == 0, out_default
    # `shared/team/big.md` does NOT trigger a size violation under default scope.
    assert "shared/team/big.md" not in out_default

    rc_inc, out_inc, _ = _run(["--wiki", str(wiki), "audit", "--include-external"])
    assert rc_inc == 1
    assert "shared/team/big.md" in out_inc
    assert "size" in out_inc


def test_audit_reports_size_violations(tmp_path):
    """audit reports pages over their per-pattern cap and flips exit code.

    Uses a `_meta/limits.md` with a tiny cap for a specific pattern so the
    test doesn't depend on the 15K default. The default `index.md` cap of
    5K is also exercised by writing a small over-cap index.
    """
    wiki = _make_wiki(tmp_path)
    # Custom cap: 100 chars for any *.md in `bigpages/`
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern        | Cap (chars) |\n"
        "|----------------|-------------|\n"
        "| bigpages/*.md  |         100 |\n"
    )
    (wiki / "bigpages").mkdir()
    (wiki / "bigpages" / "small.md").write_text("tiny content\n")  # under cap
    (wiki / "bigpages" / "large.md").write_text("x" * 200 + "\n")  # over cap

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "size violation" in out
    assert "bigpages/large.md" in out
    assert "bigpages/small.md" not in out.split("size violation")[-1]


def test_audit_reports_approaching_cap_informationally(tmp_path):
    """A page at >=80% of its cap is reported as 'approaching' — informational,
    does not flip the exit code."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern   | Cap (chars) |\n"
        "|-----------|-------------|\n"
        "| notes.md  |         100 |\n"
    )
    (wiki / "notes.md").write_text("x" * 85 + "\n")  # 85/100 = 85% — approaching

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    assert "approaching cap" in out
    assert "notes.md" in out


def test_is_in_external_scope_returns_false_for_owned_path(tmp_path):
    wiki = _make_wiki(tmp_path)
    target = wiki / "projects" / "mine"
    is_external, reason = space._is_in_external_scope(target, wiki)
    assert is_external is False
    assert reason is None


def test_is_in_external_scope_catches_shared_root(tmp_path):
    wiki = _make_wiki(tmp_path)
    target = wiki / "shared" / "foo"
    is_external, reason = space._is_in_external_scope(target, wiki)
    assert is_external is True
    assert "shared" in (reason or "")


def test_is_in_external_scope_catches_shared_descendant(tmp_path):
    """`shared/team/sub` is external because `shared` is the first segment —
    _is_external itself catches this (lexical), but verify the ancestor
    walker reports it correctly via the same path."""
    wiki = _make_wiki(tmp_path)
    target = wiki / "shared" / "team" / "sub"
    is_external, _ = space._is_in_external_scope(target, wiki)
    assert is_external is True


def test_is_in_external_scope_catches_descendant_of_foreign_submodule(tmp_path):
    """Codex's blocker: `_is_external` only checks the exact path, so a path
    under a foreign-submodule mount slipped through. The ancestor walker
    must catch it."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "external" / "foreign"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "foreign"]\n'
        "\tpath = external/foreign\n"
        "\turl = https://github.com/someone-else/their-wiki.git\n"
    )
    descendant = sub / "deep" / "child"
    is_external, reason = space._is_in_external_scope(descendant, wiki)
    assert is_external is True
    assert "external/foreign" in (reason or "")


def test_is_in_external_scope_catches_descendant_of_escaping_symlink(tmp_path):
    """A symlink that escapes the wiki tree → any descendant of that symlink
    is in external scope. Same blocker class as the submodule case."""
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside-wiki"
    outside.mkdir()
    (outside / "child").mkdir()
    import os
    link = wiki / "mount"
    os.symlink(outside, link)
    descendant = link / "child"
    is_external, reason = space._is_in_external_scope(descendant, wiki)
    assert is_external is True
    assert "mount" in (reason or "")


def test_is_in_external_scope_path_outside_wiki(tmp_path):
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "elsewhere" / "stuff"
    is_external, reason = space._is_in_external_scope(outside, wiki)
    assert is_external is True
    assert "outside" in (reason or "")


# ---------- cmd_add: --force-external preflight ----------

def test_add_refuses_external_shared(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "shared/foo"])
    assert rc == 2
    assert "external scope" in err
    assert not (wiki / "shared" / "foo").exists()


def test_add_refuses_external_shared_descendant(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "shared/team/sub"])
    assert rc == 2
    assert "external scope" in err


def test_add_refuses_under_escaping_symlink(tmp_path):
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    import os
    os.symlink(outside, wiki / "mount")
    rc, _, err = _run(["--wiki", str(wiki), "add", "mount/child"])
    assert rc == 2
    assert "external scope" in err


def test_add_refuses_under_foreign_submodule(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "external" / "foreign"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "foreign"]\n'
        "\tpath = external/foreign\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "add", "external/foreign/child"])
    assert rc == 2
    assert "external scope" in err


def test_add_force_external_overrides(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "shared/foo", "--force-external"])
    assert rc == 0
    assert (wiki / "shared" / "foo" / "index.md").is_file()


# ---------- cmd_remove: --force-external preflight ----------

def test_remove_refuses_external_shared(tmp_path):
    """Construct the external space directly (bypassing add's guard) so we can
    verify remove also refuses to operate on it without --force-external."""
    wiki = _make_wiki(tmp_path)
    sub = wiki / "shared" / "foo"
    sub.mkdir(parents=True)
    (sub / "index.md").write_text("# foo")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "shared/foo"])
    assert rc == 2
    assert "external scope" in err
    assert sub.exists()  # untouched


def test_remove_force_external_succeeds(tmp_path):
    wiki = _make_wiki(tmp_path)
    sub = wiki / "shared" / "foo"
    sub.mkdir(parents=True)
    (sub / "index.md").write_text("# foo")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "shared/foo", "--force-external"])
    assert rc == 0
    assert not sub.exists()


# ---------- _walk_owned_md_files ----------

def test_walk_owned_md_files_descends_into_plain_folders(tmp_path):
    """Plain folders (no index.md) are valid per AGENTS.md and must be
    traversed — _walk_owned_spaces misses them; _walk_owned_md_files must not."""
    wiki = _make_wiki(tmp_path)
    plain = wiki / "drafts"  # no index.md → plain folder
    plain.mkdir()
    (plain / "page.md").write_text("# page")
    (wiki / "projects" / "spaced").mkdir(parents=True)
    (wiki / "projects" / "spaced" / "index.md").write_text("# spaced")
    (wiki / "projects" / "spaced" / "child.md").write_text("# child")
    files = sorted(p.relative_to(wiki).as_posix() for p in space._walk_owned_md_files(wiki))
    assert "drafts/page.md" in files
    assert "projects/spaced/child.md" in files
    assert "projects/spaced/index.md" in files


def test_walk_owned_md_files_skips_externals(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "team-page.md").write_text("# team")
    (wiki / "projects" / "mine").mkdir(parents=True)
    (wiki / "projects" / "mine" / "p.md").write_text("# p")
    files = sorted(p.relative_to(wiki).as_posix() for p in space._walk_owned_md_files(wiki))
    assert "projects/mine/p.md" in files
    assert not any("shared/" in f for f in files)


def test_walk_owned_md_files_skips_excluded_dirs(tmp_path):
    wiki = _make_wiki(tmp_path)
    for d in (".obsidian", "_archives", ".git", "wiki-spaces-promote-leftover"):
        (wiki / d).mkdir()
        (wiki / d / "page.md").write_text("# x")
    (wiki / "ok.md").write_text("# ok")
    files = sorted(p.relative_to(wiki).as_posix() for p in space._walk_owned_md_files(wiki))
    assert "ok.md" in files
    for d in (".obsidian", "_archives", ".git", "wiki-spaces-promote-leftover"):
        assert not any(f.startswith(f"{d}/") for f in files), f"{d} was walked"


# ---------- _find_alias_owners ----------

def test_find_alias_owners_case_insensitive(tmp_path):
    wiki = _make_wiki(tmp_path)
    a = wiki / "a.md"
    a.write_text("---\naliases:\n  - Foo\n---\n# a")
    b = wiki / "b.md"
    b.write_text("---\naliases: [bar, BAR]\n---\n# b")
    owners = space._find_alias_owners(wiki)
    assert "foo" in owners and owners["foo"] == [a]
    # case-folded "bar" collects both entries
    assert "bar" in owners and len(owners["bar"]) == 1
    assert owners["bar"][0] == b


# ---------- cmd_promote: happy path ----------

def test_promote_moves_file_and_creates_index_with_spaces(tmp_path):
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n\nbody text\n")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert not page.exists()
    new = wiki / "page" / "index.md"
    assert new.is_file()
    new_text = new.read_text()
    assert "# page" in new_text
    assert "body text" in new_text
    assert "## Spaces" in new_text  # navigation contract from t=0
    assert "aliases:" in new_text
    assert "page" in _md.parse_frontmatter_aliases(new_text)
    # Parent ## Spaces has the entry
    parent_text = (wiki / "index.md").read_text()
    assert "page/" in parent_text


def test_promote_uses_frontmatter_summary_for_parent_entry(tmp_path):
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("---\nsummary: A short summary.\n---\n# page\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    parent_text = (wiki / "index.md").read_text()
    assert "A short summary." in parent_text


# ---------- cmd_promote: refusals ----------

def test_promote_refuses_index_md(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "index.md"])
    assert rc == 2
    assert "cannot promote" in err


def test_promote_refuses_nonexistent_file(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "missing.md"])
    assert rc == 2
    assert "does not exist" in err


def test_promote_refuses_non_md_file(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "thing.txt").write_text("x")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "thing.txt"])
    assert rc == 2
    assert ".md" in err


def test_promote_refuses_existing_target_dir(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "page").mkdir()
    (wiki / "page" / "preexisting.md").write_text("# preexisting")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "already exists" in err
    assert (wiki / "page.md").exists()  # source untouched


def test_promote_inserts_spaces_when_ancestor_bare(tmp_path):
    """Promote against a bare-`index.md` ancestor inserts `## Spaces` and
    registers the new space — no refuse, no manual setup step. The atomic
    mutate fn does both under a single flock."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "page.md").write_text("# page")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    # Source moved.
    assert not (wiki / "page.md").exists()
    assert (wiki / "page" / "index.md").is_file()
    # Ancestor now has `## Spaces` and an entry for the promoted space.
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "page/" in e.href for e in entries)


def test_promote_ancestor_uses_atomic_mutate_index(tmp_path, monkeypatch):
    """The ancestor write must happen under flock against FRESH text — not
    against the text read at command start. Inject a concurrent modification
    via monkeypatch and assert the rewrite is applied against the fresh text,
    not the stale one. Otherwise a parallel `space add` could clobber our
    rewrites or our entry could clobber theirs."""
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n")
    # Wrap _atomic_mutate_index: inject a sibling `## Spaces` entry into the
    # ancestor's text BEFORE the mutate fn runs. The fn must see this fresh
    # text (containing the injection) and add `page/` AFTER it without losing
    # the injected entry.
    real_atomic = space._atomic_mutate_index

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        original = ancestor_index.read_text(encoding="utf-8")
        new = original.replace(
            "## Spaces\n", "## Spaces\n\n- [concurrent/](concurrent/index.md)\n"
        )
        ancestor_index.write_text(new, encoding="utf-8")
        return real_atomic(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space, "_atomic_mutate_index", patched_atomic)

    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    root_text = (wiki / "index.md").read_text()
    # The injection must survive (mutate fn saw fresh text) AND `page/` must
    # be added.
    assert "concurrent/index.md" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    hrefs = [e.href for e in entries if e.href]
    assert any("page/" in h for h in hrefs)
    assert any("concurrent/" in h for h in hrefs)


def test_promote_rollback_does_not_clobber_concurrent_ancestor_write(tmp_path, monkeypatch):
    """Promote takes a snapshot of every affected file at the start so it
    can roll back on failure. The snapshot is taken BEFORE the lock is
    acquired on the ancestor, so a concurrent process can commit a sibling
    entry to the ancestor's `## Spaces` after our snapshot but before our
    locked write. If `_atomic_mutate_index` then fails (after the concurrent
    write has landed), the rollback path must NOT restore the pre-snapshot
    ancestor text — doing so would silently overwrite the concurrent change.

    This regression test patches `_atomic_mutate_index` to (1) inject a
    concurrent `## Spaces` entry into the ancestor and (2) return a write
    failure. Assert the concurrent entry survives the resulting rollback."""
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n")

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        # Step 1: simulate a concurrent writer committing a sibling entry
        # between our outer ancestor_text read and our lock acquisition.
        original = ancestor_index.read_text(encoding="utf-8")
        new = original.replace(
            "## Spaces\n", "## Spaces\n\n- [concurrent/](concurrent/index.md)\n"
        )
        ancestor_index.write_text(new, encoding="utf-8")
        # Step 2: fail the locked write. Caller raises and rolls back.
        return 1, "simulated lock-time write failure"

    monkeypatch.setattr(space, "_atomic_mutate_index", patched_atomic)

    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc != 0
    # Source must be restored (other planned files use snapshot-restore).
    assert (wiki / "page.md").exists()
    # Ancestor must NOT have been restored from the stale snapshot — the
    # concurrent entry survives the rollback.
    root_text = (wiki / "index.md").read_text()
    assert "concurrent/index.md" in root_text
    # The promote entry was never committed (atomic helper returned rc=1).
    assert "page/index.md" not in root_text


def test_promote_refuses_external_root(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").mkdir()
    page = wiki / "shared" / "page.md"
    page.write_text("# page")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "shared/page.md"])
    assert rc == 2
    assert "external scope" in err
    assert page.exists()


def test_promote_refuses_external_descendant(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    page = wiki / "shared" / "team" / "page.md"
    page.write_text("# page")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "shared/team/page.md"])
    assert rc == 2
    assert "external scope" in err


def test_promote_refuses_alias_collision_case_insensitive(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "other.md").write_text("---\naliases:\n  - Page\n---\n# other")
    (wiki / "page.md").write_text("# page")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "alias collision" in err.lower() or "already declares" in err
    assert (wiki / "page.md").exists()


def test_promote_skip_aliases_bypasses_collision(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "other.md").write_text("---\naliases:\n  - page\n---\n# other")
    (wiki / "page.md").write_text("# page")
    rc, _, err = _run([
        "--wiki", str(wiki), "promote", "page.md", "--skip-aliases",
    ])
    assert rc == 0, err
    new = wiki / "page" / "index.md"
    assert new.is_file()
    assert "aliases:" not in new.read_text() or "page" not in _md.parse_frontmatter_aliases(new.read_text())


def test_promote_root_file(tmp_path):
    """A .md at the wiki root promotes correctly (ancestor is wiki_root)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "rootpage.md").write_text("# root\n\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "rootpage.md"])
    assert rc == 0, err
    assert (wiki / "rootpage" / "index.md").is_file()


# ---------- cmd_promote: link rewriting ----------

def test_promote_rewrites_markdown_link_in_sibling_page(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    sibling = wiki / "sibling.md"
    sibling.write_text("see [page](page.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    new = sibling.read_text()
    assert "[page](page/index.md)" in new


def test_promote_rewrites_markdown_link_with_anchor(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    sibling = wiki / "sibling.md"
    sibling.write_text("[a](page.md#section)")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[a](page/index.md#section)" in sibling.read_text()


def test_promote_rewrites_markdown_link_from_nested_page(tmp_path):
    """Codex v3 named this as silent-corruption-risk: the rewrite must be
    relative to the LINKING file's directory, not wiki-root."""
    wiki = _make_wiki(tmp_path)
    (wiki / "projects").mkdir()
    target = wiki / "projects" / "foo.md"
    target.write_text("# foo")
    other_dir = wiki / "projects" / "other"
    other_dir.mkdir()
    notes = other_dir / "notes.md"
    notes.write_text("ref [foo](../foo.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 0, err
    new = notes.read_text()
    # The rewrite must be relative to projects/other/, not wiki root.
    assert "[foo](../foo/index.md)" in new
    assert "projects/foo/index.md" not in new


def test_promote_does_not_rewrite_unrelated_same_basename(tmp_path):
    """Two files named foo.md in different folders. Promoting one must not
    rewrite links pointing at the other."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a").mkdir()
    (wiki / "a" / "foo.md").write_text("# foo a")
    (wiki / "b").mkdir()
    (wiki / "b" / "foo.md").write_text("# foo b")
    (wiki / "ref.md").write_text("link [a](a/foo.md) and [b](b/foo.md)")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "a/foo.md"])
    assert rc == 0, err
    new = (wiki / "ref.md").read_text()
    assert "[a](a/foo/index.md)" in new
    assert "[b](b/foo.md)" in new  # other foo untouched


def test_promote_rewrites_simple_wikilink(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "ref.md").write_text("see [[page]] here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[[page/index|page]]" in (wiki / "ref.md").read_text()


def test_promote_rewrites_wikilink_with_display(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "ref.md").write_text("see [[page|My Page]] here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[[page/index|My Page]]" in (wiki / "ref.md").read_text()


def test_promote_rewrites_wikilink_with_anchor(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "ref.md").write_text("[[page#section]]")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[[page/index#section|page]]" in (wiki / "ref.md").read_text()


def test_promote_rewrites_pathful_wikilink_from_remote_directory(tmp_path):
    """Codex v5 named this as silent-staleness blocker: the WS8-internal
    resolver must match pathful targets wiki-root-relative, not file-
    relative (the existing `_md.resolve_wikilink` doesn't)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "projects").mkdir()
    (wiki / "projects" / "foo.md").write_text("# foo")
    (wiki / "recipes").mkdir()
    (wiki / "recipes" / "dessert.md").write_text("see [[projects/foo]] here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 0, err
    assert "[[projects/foo/index|projects/foo]]" in (wiki / "recipes" / "dessert.md").read_text()


def test_promote_pathful_wikilink_different_file_not_touched(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "a").mkdir()
    (wiki / "a" / "foo.md").write_text("# foo a")
    (wiki / "b").mkdir()
    (wiki / "b" / "foo.md").write_text("# foo b")
    (wiki / "ref.md").write_text("[[a/foo]] and [[b/foo]]")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "a/foo.md"])
    assert rc == 0, err
    new = (wiki / "ref.md").read_text()
    assert "[[a/foo/index|a/foo]]" in new
    assert "[[b/foo]]" in new


def test_promote_adjusts_promoted_files_outgoing_relative_links(tmp_path):
    """The promoted file moves one level deeper; its outgoing relative links
    must gain the extra ../ to keep resolving."""
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_text("# sibling")
    (wiki / "page.md").write_text("# page\n\nsee [sib](sibling.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    new = (wiki / "page" / "index.md").read_text()
    assert "[sib](../sibling.md)" in new


def test_promote_finds_links_in_plain_folder_pages(tmp_path):
    """A page in a plain folder (no index.md) must have its links rewritten —
    exercises the _walk_owned_md_files correctness gap."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    plain = wiki / "drafts"
    plain.mkdir()
    (plain / "scratch.md").write_text("ref [p](../page.md)")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[p](../page/index.md)" in (plain / "scratch.md").read_text()


# ---------- cmd_promote: dry-run + atomicity ----------

def test_promote_dry_run_no_side_effects(tmp_path):
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page")
    sibling = wiki / "sib.md"
    sibling.write_text("[p](page.md)")
    before_page = page.read_text()
    before_sibling = sibling.read_text()
    rc, out, _ = _run(["--wiki", str(wiki), "promote", "page.md", "--dry-run"])
    assert rc == 0
    assert "(dry-run)" in out
    assert page.read_text() == before_page
    assert sibling.read_text() == before_sibling
    assert not (wiki / "page").exists()


def test_promote_snapshot_dir_cleaned_on_success(tmp_path):
    """Snapshot dir lives in /tmp; after success it must be deleted."""
    import tempfile
    sysroot = Path(tempfile.gettempdir())
    before = {p.name for p in sysroot.iterdir() if p.name.startswith("wiki-spaces-promote-")}
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    rc, _, _ = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0
    after = {p.name for p in sysroot.iterdir() if p.name.startswith("wiki-spaces-promote-")}
    assert after == before, "snapshot dir not cleaned"


def test_promote_existing_spaces_section_not_duplicated(tmp_path):
    """Promoted file already had ## Spaces (rare but possible) ⇒ no second one added."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\n## Spaces\n\n- [a](a/index.md)\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    new = (wiki / "page" / "index.md").read_text()
    assert new.count("## Spaces") == 1


def test_promote_link_in_ancestor_index_is_not_clobbered_by_entry_add(tmp_path):
    """Regression for the clobber bug: when the ancestor's index.md contains
    a link to the promoted file, the link-rewrite pass updates the link,
    then `_add_space_entry` writes a NEW entry. Both edits must land — the
    entry-add must build on top of the rewritten ancestor text, not the
    pre-rewrite snapshot.
    """
    wiki = _make_wiki(tmp_path)
    # Ancestor's index.md links to the promoted file in body text.
    idx_text = (wiki / "index.md").read_text()
    (wiki / "index.md").write_text(
        idx_text + "\nSee [page](page.md) for more.\n"
    )
    (wiki / "page.md").write_text("# page\n\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    final = (wiki / "index.md").read_text()
    # Both edits must be present:
    assert "[page](page/index.md)" in final, "link rewrite was clobbered"
    assert "- [page/]" in final or "page/index.md" in final, "entry-add missing"


def test_promote_rollback_removes_orphaned_target_dir(tmp_path, monkeypatch):
    """When promote fails mid-mutation, the target/ dir created by mkdir must
    be cleaned up — not left as an empty folder polluting the wiki."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")
    # Sabotage AFTER the preflight: make `_atomic_mutate_index` fail on the
    # ancestor write so the outer try/except catches it and rolls back.
    monkeypatch.setattr(
        space, "_atomic_mutate_index",
        lambda *a, **k: (1, "simulated mid-promote failure"),
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "rolled back" in err or "failed" in err
    # The target directory must not be left behind.
    assert not (wiki / "page").exists(), "orphaned target dir not cleaned up"
    # Source must be restored.
    assert (wiki / "page.md").is_file()


def test_promote_rollback_preserves_preexisting_empty_target_dir(tmp_path, monkeypatch):
    """If the user mkdir'd `page/` before running promote (and the preflight
    tolerated it as empty), rollback must NOT delete that pre-existing
    directory — only directories WE created should be cleaned."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")
    (wiki / "page").mkdir()  # user pre-created the empty target dir
    monkeypatch.setattr(
        space, "_atomic_mutate_index",
        lambda *a, **k: (1, "simulated mid-promote failure"),
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    # Pre-existing directory must survive.
    assert (wiki / "page").is_dir(), "pre-existing empty target dir was deleted on rollback"
    # Source must be restored.
    assert (wiki / "page.md").is_file()
