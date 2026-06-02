"""Command-level traversal gate — the safety net for the traversal migration.

These tests pin the OBSERVABLE OUTPUT of the consumer/contract and owned/FS
traversals as exercised through the CLI surface skills actually consume:
`space list`, `space files`, `space audit`, and `space promote --dry-run`.

They were written FIRST (before the `_model`-owned traversals replace the
private `space.py` walkers) so the migration can be verified behaviour-for-
behaviour. Private-walker equivalence is a helper, not the safety net — this
file is. Every assertion here captures behaviour that is INTENTIONALLY
PRESERVED across the migration; a bug fix that changes one of these gets an
explicit expected-output edit in the same commit that lands the fix, never a
silent drift.

Fixture matrix (per the v1.2.0 plan): registered spaces, drift (unregistered
valid space), bare `index.md` (no `## Spaces`), stale entry, malformed /
reserved hrefs, `shared/` external mount, foreign-submodule descendant,
escaping symlink, symlink cycle, plain folders, `_meta/`, `_archives/`,
named external scope, `--include-boundaries`, output ordering, and the
label/description provenance carried in `list --json`.

Output ordering is asserted exactly where it is a contract: the consumer
walkers are stack-driven (LIFO), so e.g. `shared/team` lands BETWEEN
`projects` and `projects/acme` under `--include-external`. Skills parse this
order; the migration must keep it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.conftest import run_cli as _run, write_tree as _write


# ---------------------------------------------------------------------------
# Canonical fixture: registered spaces + drift + bare-index + stale + plain
# folders + shared/ external + reserved dirs.
# ---------------------------------------------------------------------------


def _build_main_wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {
        "index.md": (
            "# wiki\n\n## What this space is\n\nRoot.\n\n## Spaces\n\n"
            "- [Concepts](concepts/index.md) — ideas\n"
            "- [Projects](projects/index.md) — work\n"
            "- [Ghost](ghost/index.md) — stale, no dir\n"
            "- [Team](shared/team/index.md) — external mount\n"
        ),
        "concepts/index.md": "# Concepts\n\n## Spaces\n\n",
        "concepts/foo.md": "# Foo\n\nbody [[bar]]\n",
        "concepts/plain/bar.md": "# Bar\n\nbody\n",
        "projects/index.md": "# Projects\n\n## Spaces\n\n- [Acme](acme/index.md)\n",
        "projects/acme/index.md": "# Acme\n\n## Spaces\n\n",
        "projects/acme/notes.md": "# Notes\n\nnote\n",
        "drift/index.md": "# Drift\n\n## Spaces\n\n",       # valid space, unregistered
        "drift/d.md": "# D\n\nd\n",
        "bare/index.md": "# Bare\n\njust prose\n",            # index.md, no ## Spaces
        "_meta/limits.md": "# limits\n",
        "_archives/old.md": "# old\n",
        "shared/team/index.md": "# Team\n\n## Spaces\n\n",     # external mount
        "shared/team/tnote.md": "# Tnote\n\nx\n",
    })
    return root


# ---------- space list ----------


def test_list_owned_json_order_and_fields(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "list", "--json"])
    assert rc == 0
    assert json.loads(out) == [
        {"path": "concepts", "label": "Concepts", "description": "ideas", "external": False},
        {"path": "projects", "label": "Projects", "description": "work", "external": False},
        {"path": "projects/acme", "label": "Acme", "description": None, "external": False},
    ]


def test_list_include_external_interleaves_in_lifo_order(tmp_path):
    """`shared/team` lands BETWEEN `projects` and `projects/acme` — the
    stack-driven discovery order. This exact interleave is the contract."""
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "list", "--include-external", "--json"])
    assert rc == 0
    assert json.loads(out) == [
        {"path": "concepts", "label": "Concepts", "description": "ideas", "external": False},
        {"path": "projects", "label": "Projects", "description": "work", "external": False},
        {"path": "shared/team", "label": "Team", "description": "external mount", "external": True},
        {"path": "projects/acme", "label": "Acme", "description": None, "external": False},
    ]


def test_list_text_includes_root_as_dot(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "list"])
    assert rc == 0
    assert out.splitlines() == [
        ".\towned",
        "concepts\towned",
        "projects\towned",
        "projects/acme\towned",
    ]


def test_list_include_boundaries_requires_include_external(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(root), "list", "--include-boundaries"])
    assert rc == 2
    assert "--include-boundaries requires --include-external" in err


# ---------- space files ----------


def test_files_owned_json_order(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "files", "--json"])
    assert rc == 0
    assert [e["path"] for e in json.loads(out)] == [
        "index.md",
        "concepts/foo.md",
        "concepts/index.md",
        "concepts/plain/bar.md",
        "projects/index.md",
        "projects/acme/index.md",
        "projects/acme/notes.md",
    ]
    assert all(e["external"] is False for e in json.loads(out))


def test_files_include_external_json_order(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "files", "--include-external", "--json"])
    assert rc == 0
    payload = json.loads(out)
    assert [e["path"] for e in payload] == [
        "index.md",
        "concepts/foo.md",
        "concepts/index.md",
        "concepts/plain/bar.md",
        "projects/index.md",
        "shared/team/index.md",
        "shared/team/tnote.md",
        "projects/acme/index.md",
        "projects/acme/notes.md",
    ]
    ext = {e["path"] for e in payload if e["external"]}
    assert ext == {"shared/team/index.md", "shared/team/tnote.md"}


def test_files_scope_filters_lexically(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "files", "projects", "--json"])
    assert rc == 0
    assert [e["path"] for e in json.loads(out)] == [
        "projects/index.md",
        "projects/acme/index.md",
        "projects/acme/notes.md",
    ]


def test_files_scope_unregistered_drift_refused(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(root), "files", "drift"])
    assert rc == 2
    assert "not reachable via parent `## Spaces`" in err


def test_files_named_external_scope_opts_in(tmp_path):
    """Naming an external scope explicitly opts the consumer in (trust scope)."""
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "files", "shared/team", "--json"])
    assert rc == 0
    payload = json.loads(out)
    assert [e["path"] for e in payload] == [
        "shared/team/index.md",
        "shared/team/tnote.md",
    ]
    assert all(e["external"] is True for e in payload)


# ---------- space audit ----------


def test_audit_json_structure(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "audit", "--json"])
    assert rc == 1
    payload = json.loads(out)
    assert payload["summary"] == {"spaces": 6, "include_external": False}
    # Drift: MISSING includes the bare-index folder `bare` AND the
    # unregistered valid space `drift`; STALE includes `ghost`.
    assert payload["drift"] == [
        {"ancestor": ".", "missing": ["bare", "drift"], "stale": ["ghost"]}
    ]
    assert payload["missing_spaces_section"] == ["bare"]
    assert payload["orphans"] == [
        "concepts/foo.md",
        "drift/d.md",
        "projects/acme/notes.md",
    ]
    for empty in (
        "broken_wikilinks", "size_violations", "approaching_cap",
        "malformed_entries", "duplicate_aliases", "malformed_frontmatter",
    ):
        assert payload[empty] == [], empty
    assert payload["exit_code"] == 1


def test_audit_text_summary_and_findings(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "audit"])
    assert rc == 1
    # The partition counts only spaces carrying `## Spaces` (so `bare` is in
    # the total 6 but in neither the reachable nor drift partition).
    assert "spaces: 6 (4 contract-reachable, 1 drift)" in out
    assert "pages:  10 markdown files" in out
    assert "+ missing entry for bare/" in out
    assert "+ missing entry for drift/" in out
    assert "- stale entry ghost/ (no index.md on disk)" in out
    assert "! no `## Spaces` section" in out
    # missing entries are reported sorted: bare before drift.
    assert out.index("missing entry for bare/") < out.index("missing entry for drift/")


# ---------- space promote (dry-run / alias) ----------


def test_promote_dry_run_plan(tmp_path):
    root = _build_main_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(root), "promote", "concepts/foo.md", "--dry-run"])
    assert rc == 0
    assert out.splitlines() == [
        "  . (dry-run) would move concepts/foo.md -> concepts/foo/index.md",
        "  . (dry-run) would rewrite links in 0 file(s)",
        "  . (dry-run) would add alias 'foo' to concepts/foo/index.md",
        "  . (dry-run) would register entry under <wiki>/concepts/index.md ## Spaces",
    ]
    # dry-run touches nothing.
    assert (root / "concepts" / "foo.md").is_file()
    assert not (root / "concepts" / "foo").exists()


def test_promote_alias_collision_refused(tmp_path):
    root = _build_main_wiki(tmp_path)
    # Another contract-visible page already claims alias 'notes'.
    _write(root, {"concepts/other.md": "---\naliases: [notes]\n---\n# Other\n\nx\n"})
    rc, _, err = _run(["--wiki", str(root), "promote", "projects/acme/notes.md", "--dry-run"])
    assert rc == 2
    assert "alias collision" in err
    assert "concepts/other.md already declares alias 'notes'" in err


# ---------------------------------------------------------------------------
# Malformed / reserved `## Spaces` entries.
# ---------------------------------------------------------------------------


def test_audit_malformed_and_reserved_entries(tmp_path):
    root = tmp_path / "mal"
    root.mkdir()
    _write(root, {
        "index.md": (
            "# w\n\n## Spaces\n\n"
            "- [Good](good/index.md)\n"
            "- [Empty]()\n"
            "- [Reserved](_meta/x/index.md)\n"
            "- [[half\n"
        ),
        "good/index.md": "# Good\n\n## Spaces\n\n",
    })
    rc, out, _ = _run(["--wiki", str(root), "audit", "--json"])
    assert rc == 1
    payload = json.loads(out)
    issues = [m["issue"] for m in payload["malformed_entries"]]
    assert issues == [
        "empty href: - [Empty]()",
        "reserved-folder href: _meta/x/index.md (hidden / `_archives` / "
        "`_meta` paths are pruned by the consumer walker; remove the entry "
        "or move the content to a non-reserved path)",
        "unparseable bullet: - [[half",
    ]
    # The reserved href also normalizes to a stale entry (`_meta/x` has no dir).
    assert payload["drift"] == [
        {"ancestor": ".", "missing": [], "stale": ["_meta/x"]}
    ]


# ---------------------------------------------------------------------------
# Symlinks: escaping symlink, index-less boundary, symlink cycle.
# ---------------------------------------------------------------------------


def test_escaping_symlink_is_external(tmp_path):
    outside = tmp_path / "outside" / "extspace"
    _write(tmp_path, {
        "outside/extspace/index.md": "# Ext\n\n## Spaces\n\n",
        "outside/extspace/e.md": "# E\n\ne\n",
    })
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {"index.md": "# wiki\n\n## Spaces\n\n- [Ext](ext/index.md)\n"})
    os.symlink(outside, root / "ext", target_is_directory=True)

    # Owned files exclude the escaping symlink subtree.
    rc, out, _ = _run(["--wiki", str(root), "files", "--json"])
    assert rc == 0
    assert [e["path"] for e in json.loads(out)] == ["index.md"]

    # --include-external pulls it in, marked external.
    rc, out, _ = _run(["--wiki", str(root), "files", "--include-external", "--json"])
    assert rc == 0
    payload = json.loads(out)
    assert [e["path"] for e in payload] == ["index.md", "ext/e.md", "ext/index.md"]
    assert {e["path"] for e in payload if e["external"]} == {"ext/e.md", "ext/index.md"}


def test_include_boundaries_surfaces_index_less_external(tmp_path):
    """`--include-boundaries` surfaces external dirs that lack `index.md`
    (so the contract walker can't see them) with a derived `<name>/` label."""
    _write(tmp_path, {"outside_plain/readme.md": "just a file\n"})
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {"index.md": "# w\n\n## Spaces\n\n"})
    os.symlink(tmp_path / "outside_plain", root / "extplain", target_is_directory=True)

    rc, out, _ = _run([
        "--wiki", str(root), "list",
        "--include-external", "--include-boundaries", "--json",
    ])
    assert rc == 0
    assert json.loads(out) == [
        {"path": "extplain", "label": "extplain/", "description": None, "external": True},
    ]


def test_symlink_cycle_terminates(tmp_path):
    """A `## Spaces` entry pointing back at an ancestor must not hang the walk."""
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {
        "index.md": "# wiki\n\n## Spaces\n\n- [Real](realsub/index.md)\n",
        "realsub/index.md": "# Real\n\n## Spaces\n\n- [Self](self/index.md)\n",
    })
    os.symlink(root, root / "realsub" / "self", target_is_directory=True)

    rc, out, _ = _run(["--wiki", str(root), "files", "--json"])
    assert rc == 0
    assert [e["path"] for e in json.loads(out)] == ["index.md", "realsub/index.md"]


# ---------------------------------------------------------------------------
# Foreign submodule descendant: classified external, no default leak.
# ---------------------------------------------------------------------------


def test_foreign_submodule_descendant_no_default_leak(tmp_path):
    """A nested `.md` inside a foreign-origin submodule is NOT surfaced under
    default scope, and IS surfaced (marked external) with --include-external.

    The classifier parses `.git/config` + `.gitmodules` only — no real
    submodule checkout is needed to exercise the path."""
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {
        ".git/config": '[remote "origin"]\n\turl = https://github.com/me/wiki.git\n',
        ".gitmodules": (
            '[submodule "vendor"]\n'
            "\tpath = projects/vendor\n"
            "\turl = https://github.com/other/vendor.git\n"
        ),
        "index.md": "# wiki\n\n## Spaces\n\n- [Projects](projects/index.md)\n",
        "projects/index.md": "# Projects\n\n## Spaces\n\n- [Vendor](vendor/index.md)\n",
        "projects/vendor/index.md": "# Vendor\n\n## Spaces\n\n",
        "projects/vendor/top.md": "# Vtop\n\ny\n",
        "projects/vendor/docs/nested.md": "# Vnested\n\nx\n",
    })

    rc, out, _ = _run(["--wiki", str(root), "files", "--json"])
    assert rc == 0
    assert [e["path"] for e in json.loads(out)] == ["index.md", "projects/index.md"]

    rc, out, _ = _run(["--wiki", str(root), "files", "--include-external", "--json"])
    assert rc == 0
    payload = json.loads(out)
    assert [e["path"] for e in payload] == [
        "index.md",
        "projects/index.md",
        "projects/vendor/index.md",
        "projects/vendor/top.md",
        "projects/vendor/docs/nested.md",
    ]
    assert {e["path"] for e in payload if e["external"]} == {
        "projects/vendor/index.md",
        "projects/vendor/top.md",
        "projects/vendor/docs/nested.md",
    }
