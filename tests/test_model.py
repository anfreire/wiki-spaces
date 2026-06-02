"""Tests for the internal semantic model.

Covers the raw primitives (section/frontmatter/href parsing, trust
classification), node discovery, the page index + wikilink resolver,
and caps + size verdicts.

Heavy use of `tmp_path` so each test builds its own wiki tree. Tests
assert structural facts on the returned dataclasses, not display
strings — the dataclasses are the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wiki_spaces import _model
from wiki_spaces._model import (
    CapSourceKind,
    FrontmatterStatus,
    SizeOutcome,
    TrustScope,
    WikilinkStatus,
)

# `_make_wiki(tmp, files)` here is the dict tree-writer (returns the root) —
# the same primitive the gate file calls `_write`.
from tests.conftest import write_tree as _make_wiki


# ---------- parse_section_block ----------


def test_parse_section_block_returns_body_and_spans():
    text = "# Title\n\n## Spaces\n\n- [a](a/index.md)\n- [b](b/index.md)\n\n## Items\n- foo\n"
    block = _model.parse_section_block(text, "Spaces")
    assert block is not None
    assert block.heading_line == 2
    assert block.body_span == (3, 7)
    assert block.body_lines == ["", "- [a](a/index.md)", "- [b](b/index.md)", ""]


def test_parse_section_block_absent():
    assert _model.parse_section_block("# Title\n\nbody\n", "Spaces") is None


def test_parse_section_block_runs_to_eof():
    text = "# T\n\n## Spaces\n- [a](a)\n"
    block = _model.parse_section_block(text, "Spaces")
    assert block is not None
    assert block.body_lines == ["- [a](a)"]


# ---------- parse_frontmatter_result ----------


def test_frontmatter_absent():
    r = _model.parse_frontmatter_result("# Page\n\nbody\n")
    assert r.status == FrontmatterStatus.ABSENT
    assert r.data is None


def test_frontmatter_ok():
    text = "---\ntitle: Foo\naliases: [bar]\n---\nbody\n"
    r = _model.parse_frontmatter_result(text)
    assert r.status == FrontmatterStatus.OK
    assert r.data == {"title": "Foo", "aliases": ["bar"]}


def test_frontmatter_empty_returns_ok_with_empty_dict():
    """Frontmatter present but with only blank lines is OK with empty dict.
    The `---\\n---\\n` form without a separating newline is structurally
    ambiguous and parses as ABSENT (a separate test asserts that)."""
    r = _model.parse_frontmatter_result("---\n\n---\nbody\n")
    assert r.status == FrontmatterStatus.OK
    assert r.data == {}


def test_frontmatter_unseparated_dashes_treated_as_absent():
    """`---\\n---` with no body line between is not recognized as frontmatter
    by `_md.split_frontmatter`; we surface that as ABSENT rather than
    inventing a separate state for it."""
    r = _model.parse_frontmatter_result("---\n---\nbody\n")
    assert r.status == FrontmatterStatus.ABSENT


def test_frontmatter_malformed_carries_line():
    """Bad indentation, dangling colon, etc. land in MALFORMED with a line."""
    text = "---\ntitle: foo\n  bad: : :\n---\nbody\n"
    r = _model.parse_frontmatter_result(text)
    assert r.status == FrontmatterStatus.MALFORMED
    assert r.data is None
    assert r.error_message is not None


def test_frontmatter_non_mapping():
    """A top-level YAML list is not a mapping — NON_MAPPING, not OK."""
    text = "---\n- a\n- b\n---\nbody\n"
    r = _model.parse_frontmatter_result(text)
    assert r.status == FrontmatterStatus.NON_MAPPING


# ---------- normalize_spaces_href ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("foo", "foo"),
        ("foo/", "foo"),
        ("foo/index.md", "foo"),
        ("nested/bar/", "nested/bar"),
        ("nested/bar/index.md", "nested/bar"),
    ],
)
def test_normalize_spaces_href_ok(raw, expected):
    norm, err = _model.normalize_spaces_href(raw)
    assert err is None
    assert norm == expected


@pytest.mark.parametrize(
    "raw,fragment",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("/abs/path", "absolute"),
        ("../escape", ".."),
        (".hidden/foo", "reserved-name"),
        ("_meta/foo", "reserved-name"),
        ("_archives/old", "reserved-name"),
    ],
)
def test_normalize_spaces_href_rejects(raw, fragment):
    norm, err = _model.normalize_spaces_href(raw)
    assert norm is None
    assert err is not None
    assert fragment in err.lower()


# ---------- classify_external_scope ----------


def test_classify_owned_path(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    sub = root / "concepts"
    sub.mkdir()
    tc = _model.classify_external_scope(sub, root)
    assert tc.scope == TrustScope.OWNED
    assert tc.boundary is None


def test_classify_shared_path_external(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    shared = root / "shared" / "team"
    shared.mkdir(parents=True)
    tc = _model.classify_external_scope(shared, root)
    assert tc.scope == TrustScope.EXTERNAL
    assert tc.boundary == root / "shared"


def test_classify_descendant_of_external(tmp_path):
    """A nested folder under shared/ inherits external classification."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    deep = root / "shared" / "team" / "docs"
    deep.mkdir(parents=True)
    tc = _model.classify_external_scope(deep, root)
    assert tc.scope == TrustScope.EXTERNAL


def test_classify_cyclic_symlink_is_external_not_infinite_recursion(tmp_path):
    """A self-referential symlink resolves to itself, so `resolve()` returns a
    still-symlink rather than raising. The classifier must treat it as escaping
    (external) and terminate — not recurse on a path that resolves to itself."""
    import os

    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    os.symlink(root / "loop", root / "loop")  # cyclic self-link
    tc = _model.classify_external_scope(root / "loop", root)
    assert tc.scope == TrustScope.EXTERNAL
    assert "escapes" in (tc.reason or "")


# ---------- git origin resolution (worktree / submodule aware) ----------


def test_model_origin_url_reads_regular_repo(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/me/wiki.git\n'
    )
    assert _model._wiki_origin_url(root) == "https://github.com/me/wiki.git"


def test_model_origin_url_worktree_follows_commondir(tmp_path):
    """Regression: a worktree's `.git` file points at a gitdir that shares
    config via `commondir`. The model walker used to read `gitdir/config`
    directly (absent in a worktree) and return None — making every submodule
    look foreign. It must now follow `commondir` like `space` does."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    common = tmp_path / "main-repo" / ".git"
    common.mkdir(parents=True)
    (common / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/wt/shared.git\n'
    )
    worktree_gitdir = common / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")
    (worktree_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://example.invalid/ignored.git\n'
    )
    (root / ".git").write_text(f"gitdir: {worktree_gitdir}\n")
    assert _model._wiki_origin_url(root) == "https://github.com/wt/shared.git"


def test_model_and_space_origin_agree_in_worktree(tmp_path):
    """The whole point of consolidating: the two classifiers must return the
    same origin so they can't disagree about what's foreign."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    common = tmp_path / "main-repo" / ".git"
    common.mkdir(parents=True)
    (common / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/wt/shared.git\n'
    )
    worktree_gitdir = common / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")
    (root / ".git").write_text(f"gitdir: {worktree_gitdir}\n")
    assert _model._wiki_origin_url(root) == "https://github.com/wt/shared.git"


def test_model_same_origin_submodule_not_foreign_in_worktree(tmp_path):
    """Observable consequence of the bug: in a worktree, a submodule whose
    origin matches the wiki's own origin must NOT be classified foreign.
    Before the commondir fix, the wiki origin resolved to None and every
    submodule was treated as foreign (write-protection over recall)."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    common = tmp_path / "main-repo" / ".git"
    common.mkdir(parents=True)
    (common / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/me/wiki.git\n'
    )
    worktree_gitdir = common / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")
    (root / ".git").write_text(f"gitdir: {worktree_gitdir}\n")
    sub = root / "projects" / "mirror"
    sub.mkdir(parents=True)
    (root / ".gitmodules").write_text(
        '[submodule "mirror"]\n'
        "\tpath = projects/mirror\n"
        "\turl = https://github.com/me/wiki.git\n"
    )
    assert _model.is_foreign_submodule(sub, root) is False


def test_model_origin_url_survives_non_utf8_git_config(tmp_path):
    """A non-UTF-8 `.git/config` (e.g. a latin-1 author/remote line) is a
    boundary input; `_wiki_origin_url` must degrade to None, not crash with a
    raw `UnicodeDecodeError` (a `ValueError`, NOT an `OSError`) — the same
    boundary the `index.md` reads were hardened against."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    (root / ".git").mkdir()
    (root / ".git" / "config").write_bytes(
        b'[remote "origin"]\n\turl = https://x/y.git \xff\xfe\n'
    )
    assert _model._wiki_origin_url(root) is None


def test_model_is_foreign_submodule_survives_non_utf8_gitmodules(tmp_path):
    """A non-UTF-8 `.gitmodules` must degrade the foreign-submodule
    classification to False, not crash discovery with a `UnicodeDecodeError`."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    sub = root / "sub"
    sub.mkdir()
    (root / ".gitmodules").write_bytes(
        b'[submodule "sub"]\n\tpath = sub\n\turl = https://z/w.git \xff\n'
    )
    assert _model.is_foreign_submodule(sub, root) is False


# ---------- discover_nodes ----------


def test_discover_yields_wiki_root_first(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    nodes = _model.discover_nodes(root)
    assert nodes[0].path == root
    assert nodes[0].has_index
    assert nodes[0].has_spaces_section
    assert nodes[0].trust.scope == TrustScope.OWNED
    assert nodes[0].contract_reachable


def test_discover_marks_registered_and_reachable_child(tmp_path):
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n\n- [a](a/index.md)\n",
        "a/index.md": "# A\n\n## Spaces\n",
    })
    nodes = _model.discover_nodes(root)
    a = next(n for n in nodes if n.path.name == "a")
    assert a.registered_in_nearest_ancestor
    assert a.contract_reachable


def test_discover_marks_drift_unregistered_child(tmp_path):
    """A space-shaped child not in parent's ## Spaces is registered=False."""
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n",
        "drift/index.md": "# Drift\n\n## Spaces\n",
    })
    nodes = _model.discover_nodes(root)
    drift = next(n for n in nodes if n.path.name == "drift")
    assert drift.has_index and drift.has_spaces_section
    assert not drift.registered_in_nearest_ancestor
    assert not drift.contract_reachable


def test_discover_distinguishes_registered_from_reachable(tmp_path):
    """A7's structural fix: a child registered in an UNREGISTERED ancestor
    is registered_in_nearest_ancestor=True but contract_reachable=False.

    Root: lists nothing.
      a/: not listed in root, but has ## Spaces and lists b.
        b/: listed in a.

    `b` is locally registered but cannot be reached from root."""
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n",
        "a/index.md": "# A\n\n## Spaces\n\n- [b](b/index.md)\n",
        "a/b/index.md": "# B\n\n## Spaces\n",
    })
    nodes = _model.discover_nodes(root)
    a = next(n for n in nodes if n.path == root / "a")
    b = next(n for n in nodes if n.path == root / "a" / "b")
    assert not a.contract_reachable
    assert b.registered_in_nearest_ancestor
    assert not b.contract_reachable


def test_discover_registered_bare_child_not_contract_reachable(tmp_path):
    """A child registered in the root's `## Spaces` but whose own index.md
    lacks `## Spaces` (a bare/drift child) is locally registered yet NOT
    contract_reachable — it is not a consumer-visible space. Guards the new
    model traversal from inheriting reachability as the consumer predicate."""
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n\n- [a](a/index.md)\n",
        "a/index.md": "# A\n\njust prose, no spaces heading\n",
    })
    nodes = _model.discover_nodes(root)
    a = next(n for n in nodes if n.path.name == "a")
    assert a.has_index
    assert not a.has_spaces_section
    assert a.registered_in_nearest_ancestor
    assert not a.contract_reachable


def test_discover_reachability_does_not_propagate_through_bare_child(tmp_path):
    """Reachability must not flow through a bare child: root → a (bare) → b
    leaves b unreachable even though b is a valid space."""
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n\n- [a](a/index.md)\n",
        "a/index.md": "# A\n\nno spaces section here\n",
        "a/b/index.md": "# B\n\n## Spaces\n",
    })
    nodes = _model.discover_nodes(root)
    a = next(n for n in nodes if n.path == root / "a")
    b = next(n for n in nodes if n.path == root / "a" / "b")
    assert not a.contract_reachable
    assert not b.contract_reachable


def test_discover_skips_external_by_default(tmp_path):
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n",
        "shared/team/index.md": "# Team\n\n## Spaces\n",
    })
    nodes = _model.discover_nodes(root)
    # The boundary (`shared/`) is yielded so audit can report it.
    paths = {n.path for n in nodes}
    assert (root / "shared") in paths
    # The deeper team/ is not yielded without include_external.
    assert (root / "shared" / "team") not in paths


def test_discover_includes_external_with_flag(tmp_path):
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n",
        "shared/team/index.md": "# Team\n\n## Spaces\n",
    })
    nodes = _model.discover_nodes(root, include_external=True)
    deep = next(n for n in nodes if n.path == root / "shared" / "team")
    assert deep.trust.scope == TrustScope.EXTERNAL


# ---------- build_page_index + resolve_wikilink ----------


def test_page_index_basenames(tmp_path):
    root = _make_wiki(tmp_path, {"a.md": "# A\n", "b.md": "# B\n"})
    idx = _model.build_page_index([root / "a.md", root / "b.md"])
    assert idx.by_basename["a.md"] == [root / "a.md"]
    assert idx.by_basename["b.md"] == [root / "b.md"]


def test_page_index_aliases_indexed_casefold(tmp_path):
    root = _make_wiki(tmp_path, {
        "page.md": "---\naliases: [Foo, BAR]\n---\nbody\n",
    })
    idx = _model.build_page_index([root / "page.md"])
    assert "foo" in idx.by_alias
    assert "bar" in idx.by_alias
    assert idx.by_alias["foo"] == [root / "page.md"]


def test_page_index_duplicate_aliases_flagged(tmp_path):
    root = _make_wiki(tmp_path, {
        "x.md": "---\naliases: [shared]\n---\nbody\n",
        "y.md": "---\naliases: [shared]\n---\nbody\n",
    })
    idx = _model.build_page_index([root / "x.md", root / "y.md"])
    assert "shared" in idx.duplicate_aliases
    assert sorted(idx.duplicate_aliases["shared"]) == sorted([root / "x.md", root / "y.md"])


def test_page_index_malformed_frontmatter_recorded(tmp_path):
    root = _make_wiki(tmp_path, {
        "bad.md": "---\nbad: : :\n---\nbody\n",
    })
    idx = _model.build_page_index([root / "bad.md"])
    assert root / "bad.md" in idx.frontmatter_errors
    assert idx.frontmatter_errors[root / "bad.md"].status == FrontmatterStatus.MALFORMED


def test_resolve_wikilink_basename(tmp_path):
    root = _make_wiki(tmp_path, {"a.md": "# A\n", "sub/b.md": "# B\n"})
    idx = _model.build_page_index([root / "a.md", root / "sub" / "b.md"])
    res = _model.resolve_wikilink("a", root, idx, wiki_root=root)
    assert res.status == WikilinkStatus.RESOLVED
    assert res.target == root / "a.md"
    strategies = [a.strategy for a in res.attempts]
    assert strategies == ["basename"]


def test_resolve_wikilink_alias(tmp_path):
    root = _make_wiki(tmp_path, {
        "page.md": "---\naliases: [Foo]\n---\nbody\n",
    })
    idx = _model.build_page_index([root / "page.md"])
    res = _model.resolve_wikilink("foo", root, idx, wiki_root=root)
    assert res.status == WikilinkStatus.RESOLVED
    assert res.target == root / "page.md"
    strategies = [a.strategy for a in res.attempts]
    assert "alias" in strategies


def test_resolve_wikilink_ambiguous_alias(tmp_path):
    """Two pages claiming the same alias → AMBIGUOUS_ALIAS, never last-writer-wins."""
    root = _make_wiki(tmp_path, {
        "x.md": "---\naliases: [shared]\n---\n",
        "y.md": "---\naliases: [shared]\n---\n",
    })
    idx = _model.build_page_index([root / "x.md", root / "y.md"])
    res = _model.resolve_wikilink("shared", root, idx, wiki_root=root)
    assert res.status == WikilinkStatus.AMBIGUOUS_ALIAS
    assert res.target is None
    assert sorted(res.candidates) == sorted([root / "x.md", root / "y.md"])


def test_resolve_wikilink_unresolved_carries_attempts(tmp_path):
    root = _make_wiki(tmp_path, {"a.md": "# A\n"})
    idx = _model.build_page_index([root / "a.md"])
    res = _model.resolve_wikilink("nonexistent", root, idx, wiki_root=root)
    assert res.status == WikilinkStatus.UNRESOLVED
    outcomes = {a.strategy: a.outcome for a in res.attempts}
    assert outcomes["basename"] == "no_match"
    assert outcomes["alias"] == "no_match"


def test_resolve_wikilink_pathful_root_then_base(tmp_path):
    """When target contains `/`, wiki-root pathful tries first."""
    root = _make_wiki(tmp_path, {"concepts/foo.md": "# Foo\n"})
    idx = _model.build_page_index([root / "concepts" / "foo.md"])
    base = root / "projects" / "p"
    base.mkdir(parents=True)
    res = _model.resolve_wikilink("concepts/foo", base, idx, wiki_root=root)
    assert res.status == WikilinkStatus.RESOLVED
    assert res.target == root / "concepts" / "foo.md"
    assert res.attempts[0].strategy == "wiki_root_pathful"
    assert res.attempts[0].outcome == "matched"


# ---------- caps + size verdicts ----------


def test_cap_for_path_builtin_index(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    v = _model.cap_for_path(root / "index.md", root)
    assert v.cap == 5000
    assert v.source.kind == CapSourceKind.BUILTIN_DEFAULT
    assert v.source.pattern == "index.md"


def test_cap_for_path_user_override(tmp_path):
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n",
        "_meta/limits.md": "# Limits\n\n| Pattern | Cap |\n|---|---|\n| concepts/*.md | 8000 |\n",
        "concepts/foo.md": "# Foo\n",
    })
    v = _model.cap_for_path(root / "concepts" / "foo.md", root)
    assert v.cap == 8000
    assert v.source.kind == CapSourceKind.USER_OVERRIDE
    assert v.source.file == root / "_meta" / "limits.md"


def test_check_size_ok(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    v = _model.check_size(root / "index.md", "small\n", root)
    assert v.outcome == SizeOutcome.OK
    assert v.chars_projected == len("small\n")


def test_check_size_over(tmp_path):
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    # 6000 chars > 5000 cap for index.md
    v = _model.check_size(root / "index.md", "x" * 6000, root)
    assert v.outcome == SizeOutcome.OVER


def test_check_size_shrinking_allows_over(tmp_path):
    """A projected write smaller than current bytes is OK even if over cap."""
    root = _make_wiki(tmp_path, {
        "index.md": "# W\n\n## Spaces\n\n" + "x" * 6000,
    })
    # New content still > 5000 but < current
    v = _model.check_size(root / "index.md", "y" * 5500, root)
    assert v.outcome == SizeOutcome.OK_SHRINKING


def test_check_size_excludes_frontmatter_from_current_body(tmp_path):
    """`chars_current` — the on-disk size the shrinking-write hatch compares
    against — excludes frontmatter, matching the projection side, so adding or
    removing frontmatter alone never fakes a shrink."""
    root = _make_wiki(tmp_path, {"index.md": "# W\n\n## Spaces\n"})
    fm = "---\ntitle: x\ntags: [a, b, c]\n---\n"
    (root / "note.md").write_text(fm + "y" * 42, encoding="utf-8")
    v = _model.check_size(root / "note.md", "small\n", root)
    assert v.chars_current == 42  # frontmatter excluded from the on-disk body


def test_is_reserved_segment():
    """Hidden `.X` folders and reserved convention folders are skipped; ordinary
    names are not."""
    assert _model.is_reserved_segment(".git")
    assert _model.is_reserved_segment(".obsidian")
    assert _model.is_reserved_segment("_archives")
    assert _model.is_reserved_segment("_meta")
    assert not _model.is_reserved_segment("projects")
    assert not _model.is_reserved_segment("index.md")


# ---------- limit table + matcher (relocated from the log tests) ----------


def test_builtin_limits_ordering():
    """Most-specific patterns first; broadest last. `log.archive-*.md` and
    `hot.md` share the log's 100K cap (archives are log overflow, hot.md is a
    user-owned scratchpad — neither is a generic content page) and precede the
    broad `*.md` rule so they win for those files."""
    patterns = [p for p, _ in _model._BUILTIN_LIMITS]
    assert patterns == ["index.md", "log.md", "log.archive-*.md", "hot.md", "*.md"]


def test_log_archive_default_cap_matches_log():
    """`log.archive-*.md` inherits the log.md cap — rotating a 100K log isn't
    blocked by the generic 15K content-page cap."""
    caps = dict(_model._BUILTIN_LIMITS)
    assert caps["log.archive-*.md"] == caps["log.md"]


def test_hot_md_default_cap_matches_log():
    """hot.md is a user-owned scratchpad — same category as log.md (user-owned,
    tools never rewrite). Both get 100K by default."""
    caps = dict(_model._BUILTIN_LIMITS)
    assert caps["hot.md"] == caps["log.md"]


def test_load_limit_table_defaults_when_no_override(tmp_path):
    rules = _model.load_limit_table(tmp_path).rules
    assert [(p, c) for p, c, _ in rules] == _model._BUILTIN_LIMITS


def test_builtin_limits_match_conventions_doc():
    """`CONVENTIONS.md`'s default cap table is the human mirror of
    `_model._BUILTIN_LIMITS`; pin them in sync so the doc and the code can't
    drift into two cap tables (one source of truth)."""
    conventions = Path(__file__).resolve().parents[1] / "CONVENTIONS.md"
    lines = conventions.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("**Defaults"))
    row_re = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([\d,]+)")
    rows: list[tuple[str, int]] = []
    for ln in lines[start:]:
        m = row_re.match(ln)
        if m:
            rows.append((m.group(1), int(m.group(2).replace(",", ""))))
        elif rows and not ln.lstrip().startswith("|"):
            break
    assert rows == _model._BUILTIN_LIMITS


def test_load_limit_table_parses_user_table(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "# Size limits\n\n"
        "| Pattern         | Cap (chars) |\n"
        "|-----------------|-------------|\n"
        "| concepts/*.md   |        8000 |\n"
        "| projects/**/*.md|       20000 |\n"
    )
    rules = [(p, c) for p, c, _ in _model.load_limit_table(tmp_path).rules]
    # User patterns come first, in file order.
    assert rules[0] == ("concepts/*.md", 8000)
    assert rules[1] == ("projects/**/*.md", 20000)
    # Defaults are appended after.
    assert ("index.md", 5000) in rules
    assert ("*.md", 15000) in rules


def test_load_limit_table_user_overrides_default(tmp_path):
    """If the user redeclares a default pattern, the user value wins and the
    built-in is dropped (not appended) — no ghost rule that never fires."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern  | Cap (chars) |\n"
        "|----------|-------------|\n"
        "| index.md |        9999 |\n"
    )
    rules = [(p, c) for p, c, _ in _model.load_limit_table(tmp_path).rules]
    assert ("index.md", 9999) in rules
    assert ("index.md", 5000) not in rules


def test_load_limit_table_skips_malformed_rows(tmp_path):
    """Header rows, separator rows, and unparseable / non-positive caps are
    skipped — they never become rules."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n"
        "|---|---|\n"
        "| valid.md | 1234 |\n"
        "| bad-cap.md | not-a-number |\n"
        "| negative.md | -5 |\n"
        "| zero.md | 0 |\n"
    )
    rules = _model.load_limit_table(tmp_path).rules
    builtin = {p for p, _ in _model._BUILTIN_LIMITS}
    user_patterns = [p for p, _, _ in rules if p not in builtin]
    assert "valid.md" in user_patterns
    assert "bad-cap.md" not in user_patterns
    assert "negative.md" not in user_patterns
    assert "zero.md" not in user_patterns


def test_load_limit_table_keeps_duplicate_user_patterns(tmp_path):
    """Duplicate user patterns are NOT silently deduped: both rows survive in
    file order, and first-match-wins resolves the earlier one."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---------|-------------|\n"
        "| dup.md  |         100 |\n"
        "| dup.md  |         200 |\n"
    )
    rules = [(p, c) for p, c, _ in _model.load_limit_table(tmp_path).rules]
    assert [(p, c) for p, c in rules if p == "dup.md"] == [
        ("dup.md", 100),
        ("dup.md", 200),
    ]
    assert _model.cap_for_path(tmp_path / "dup.md", tmp_path).cap == 100


def test_cap_for_path_basename_matches_any_depth(tmp_path):
    """A pattern without `/` matches `path.name` at any depth."""
    assert _model.cap_for_path(tmp_path / "index.md", tmp_path).cap == 5000
    assert _model.cap_for_path(
        tmp_path / "projects" / "foo" / "index.md", tmp_path
    ).cap == 5000
    assert _model.cap_for_path(
        tmp_path / "deep" / "nested" / "path" / "index.md", tmp_path
    ).cap == 5000


def test_cap_for_path_basename_match_is_case_sensitive(monkeypatch, tmp_path):
    """Basename cap patterns match case-SENSITIVELY (fnmatchcase). `fnmatch`
    folds case via `os.path.normcase`, so on a case-insensitive FS an uppercase
    `HOT.md` rule would wrongly match `hot.md`. Simulate that FS by folding
    normcase, then assert the match stays case-sensitive."""
    import os as _os

    monkeypatch.setattr(_os.path, "normcase", str.lower)
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n|---|---|\n| HOT.md | 10 |\n| *.md | 15000 |\n"
    )
    # Lowercase `hot.md` must NOT match the uppercase `HOT.md` rule even when
    # normcase folds case — it falls through to `*.md`.
    assert _model.cap_for_path(tmp_path / "hot.md", tmp_path).cap == 15000
    # The exact-case basename still matches its own rule.
    assert _model.cap_for_path(tmp_path / "HOT.md", tmp_path).cap == 10


def test_cap_for_path_pathful_matches_relative_posix(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n|---|---|\n| concepts/*.md | 8000 |\n"
    )
    assert _model.cap_for_path(tmp_path / "concepts" / "foo.md", tmp_path).cap == 8000
    # `notes/foo.md` and root `foo.md` do NOT match `concepts/*.md`.
    assert _model.cap_for_path(tmp_path / "notes" / "foo.md", tmp_path).cap == 15000
    assert _model.cap_for_path(tmp_path / "foo.md", tmp_path).cap == 15000


def test_cap_for_path_glob_non_recursive(tmp_path):
    """`concepts/*.md` matches one segment under `concepts/` only — not deeper."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n|---|---|\n| concepts/*.md | 8000 |\n"
    )
    assert _model.cap_for_path(tmp_path / "concepts" / "foo.md", tmp_path).cap == 8000
    assert (
        _model.cap_for_path(tmp_path / "concepts" / "sub" / "foo.md", tmp_path).cap
        == 15000
    )


def test_cap_for_path_glob_recursive(tmp_path):
    """`concepts/**/*.md` matches every depth under `concepts/`."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n|---|---|\n| concepts/**/*.md | 8000 |\n"
    )
    assert _model.cap_for_path(tmp_path / "concepts" / "foo.md", tmp_path).cap == 8000
    assert (
        _model.cap_for_path(tmp_path / "concepts" / "sub" / "foo.md", tmp_path).cap
        == 8000
    )
    assert (
        _model.cap_for_path(
            tmp_path / "concepts" / "a" / "b" / "c" / "foo.md", tmp_path
        ).cap
        == 8000
    )


def test_cap_for_path_user_pattern_beats_default_glob(tmp_path):
    """The core regression: a narrow user rule must win over the broad default
    `*.md`, not be shadowed by it."""
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta" / "limits.md").write_text(
        "| Pattern       | Cap (chars) |\n"
        "|---------------|-------------|\n"
        "| concepts/*.md |        8000 |\n"
    )
    cap = _model.cap_for_path(tmp_path / "concepts" / "foo.md", tmp_path).cap
    assert cap == 8000, (
        f"default *.md cap (15000) shadowed the user's concepts/*.md (8000); got {cap}"
    )


def test_cap_for_path_root_md_matches_star_md_fallback(tmp_path):
    """Root-level `foo.md` falls through to the `*.md` default (the
    `**/*.md`-doesn't-match-root-files trap the split matcher fixes)."""
    assert _model.cap_for_path(tmp_path / "foo.md", tmp_path).cap == 15000


def test_cap_for_path_log_md_matches_specific_default(tmp_path):
    assert _model.cap_for_path(tmp_path / "log.md", tmp_path).cap == 100000


def test_check_size_under_cap_reports_cap_and_projection(tmp_path):
    v = _model.check_size(tmp_path / "foo.md", "short body\n", tmp_path)
    assert v.outcome == SizeOutcome.OK
    assert v.chars_projected == len("short body\n")
    assert v.cap.cap == 15000  # *.md default


def test_check_size_over_star_md_cap(tmp_path):
    v = _model.check_size(tmp_path / "foo.md", "x" * 16000, tmp_path)
    assert v.outcome == SizeOutcome.OVER
    assert v.chars_projected == 16000
    assert v.cap.cap == 15000


def test_check_size_excludes_frontmatter_from_projection(tmp_path):
    """Frontmatter doesn't count toward the projected size."""
    text = "---\n" + "k: v\n" * 100 + "---\n" + "body\n"
    v = _model.check_size(tmp_path / "foo.md", text, tmp_path)
    assert v.chars_projected == len("body\n")
    assert v.outcome == SizeOutcome.OK


def test_check_size_new_file_over_cap_is_over_not_shrinking(tmp_path):
    """A write to a path with no current file has zero current size, so an
    over-cap projected write is OVER — never mistaken for a shrinking write.
    `index.md` resolves the 5K cap at any depth."""
    page = tmp_path / "projects" / "foo" / "index.md"
    v = _model.check_size(page, "x" * 6000, tmp_path)
    assert v.cap.cap == 5000
    assert v.outcome == SizeOutcome.OVER


# ---------- symlink_escapes_wiki (framework-write trust-boundary guard) ----------


def test_symlink_escapes_wiki_true_for_escaping_link(tmp_path):
    """A symlink whose realpath leaves the wiki tree must be reported escaping:
    `atomic_write` would follow it and mutate content outside the trust boundary
    (HANDBOOK: writes stay inside the trust boundary)."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("external", encoding="utf-8")
    link = wiki / "log.md"
    link.symlink_to(outside)
    assert _model.symlink_escapes_wiki(link, wiki) is True


def test_symlink_escapes_wiki_false_for_internal_alias(tmp_path):
    """An internal alias whose realpath stays inside the wiki writes through
    normally — it is owned content, not an escape."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    real = wiki / "AGENTS.md"
    real.write_text("owned", encoding="utf-8")
    alias = wiki / "CLAUDE.md"
    alias.symlink_to(real)
    assert _model.symlink_escapes_wiki(alias, wiki) is False


def test_symlink_escapes_wiki_false_for_regular_file(tmp_path):
    """A plain (non-symlink) write target is always in-tree — never an escape."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    f = wiki / "log.md"
    f.write_text("regular", encoding="utf-8")
    assert _model.symlink_escapes_wiki(f, wiki) is False


def test_symlink_escapes_wiki_mirrors_external_reason_for_shared_target(tmp_path):
    """`symlink_escapes_wiki` must be the single producer-side mirror of the
    consumer classifier `external_reason_for`, not a forked reimplementation
    (HANDBOOK: unify, don't fork). A symlink whose realpath stays inside the
    tree but lands in external `shared/` scope is external to the classifier, so
    the write guard must refuse it too — a hand-rolled tree-escape-only check
    would wrongly allow it."""
    wiki = tmp_path / "wiki"
    (wiki / "shared" / "team").mkdir(parents=True)
    target = wiki / "shared" / "team" / "real.md"
    target.write_text("external mount content", encoding="utf-8")
    link = wiki / "log.md"
    link.symlink_to(target)
    # Producer guard and consumer classifier must agree.
    assert _model.external_reason_for(link, wiki) is not None
    assert _model.symlink_escapes_wiki(link, wiki) is True


def test_normalize_spaces_href_rejects_self_referential(tmp_path):
    """A `## Spaces` href that normalizes to the space's own dir (`.` — e.g.
    `./index.md` or `.`) is a no-op the consumer walker skips. The normalizer
    must REJECT it so a self-referential entry can't pose as a real child
    (producer=consumer; the tools traverse only a contract they can trust)."""
    for href in ("./index.md", ".", "./", "."):
        norm, err = _model.normalize_spaces_href(href)
        assert norm is None, f"{href!r} should be rejected, got {norm!r}"
        assert err is not None
