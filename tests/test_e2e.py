"""End-to-end producer→consumer tests.

These tests exercise the full chain: a producer (CLI command or skill-equivalent
write) emits content, then a consumer (audit, search, etc.) operates on the
result. They catch the failure class that single-component unit tests cannot —
where each component is correct in isolation but the loop breaks at a seam.
"""

from __future__ import annotations

import json

import pytest

from wiki_spaces import _md

from tests.conftest import (
    HAS_GIT,
    make_git_repo,
    make_space_dir,
    make_wiki as _make_wiki,
    run_cli as _run,
    write_tree,
)


def test_promote_then_audit_clean(tmp_path):
    """Promote→audit producer/consumer regression. Sequence:
      1. Build a wiki with a content page `concepts/foo.md` and a referencing page
         `notes/ref.md` containing `[[foo]]`.
      2. `space promote concepts/foo.md` — the producer rewrites the reference to
         `[[concepts/foo/index|foo]]` (wiki-root pathful).
      3. `space audit` — the consumer must resolve that pathful wikilink to the
         new `concepts/foo/index.md` and report zero broken links.

    The unified resolver handles wiki-root pathful first so the rewritten link
    is recognised by audit.
    """
    root = _make_wiki(tmp_path)
    (root / "concepts").mkdir()
    (root / "concepts" / "foo.md").write_text("# Foo\n\nstuff\n")
    (root / "notes").mkdir()
    (root / "notes" / "ref.md").write_text("# Ref\n\nSee [[foo]] for details.\n")

    rc, out, err = _run(["--wiki", str(root), "audit"])
    assert rc == 0, f"audit failed before promote: {out}\n{err}"

    rc, out, err = _run(["--wiki", str(root), "promote", "concepts/foo.md"])
    assert rc == 0, f"promote failed: {out}\n{err}"

    rewritten = (root / "notes" / "ref.md").read_text()
    assert "[[concepts/foo/index" in rewritten, (
        f"promote did not emit the expected wiki-root pathful wikilink; got: {rewritten!r}"
    )

    rc, out, err = _run(["--wiki", str(root), "audit"])
    assert rc == 0, (
        "audit reported broken wikilinks AFTER promote — this is the original "
        f"producer→consumer bug:\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert "! broken wikilink" not in out


def test_promote_then_audit_clean_pathful_no_md_suffix(tmp_path):
    """Variant of the regression where the rewritten wikilink form is exactly
    `[[<path>/index]]` (no `.md`) — promote's literal output. Ensures the
    resolver's `.md`-normalization on wiki-root pathful actually engages."""
    root = _make_wiki(tmp_path)
    (root / "projects").mkdir()
    (root / "projects" / "bar.md").write_text("# Bar\n\ncontent\n")
    (root / "ref.md").write_text("# Ref\n\nSee [[bar]].\n")

    rc, _, _ = _run(["--wiki", str(root), "promote", "projects/bar.md"])
    assert rc == 0

    rewritten = (root / "ref.md").read_text()
    assert "projects/bar/index" in rewritten

    rc, out, err = _run(["--wiki", str(root), "audit"])
    assert rc == 0, f"audit broken after promote:\n{out}\n{err}"


# ---------------------------------------------------------------------------
# add → readers agree.
# ---------------------------------------------------------------------------


def test_add_then_every_reader_agrees(tmp_path):
    """`add` (writer) → `list`/`files`/`audit` (readers) all see the new space,
    and its `## Spaces` entry parses back to the path the writer registered."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "projects", "--description", "work"])
    assert rc == 0, err
    assert (wiki / "projects" / "index.md").is_file()

    rc, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    listed = {e["path"]: e for e in json.loads(out)}
    assert "projects" in listed
    assert listed["projects"]["description"] == "work"

    rc, out, _ = _run(["--wiki", str(wiki), "files", "--json"])
    assert "projects/index.md" in {e["path"] for e in json.loads(out)}

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out

    # parse-back: the writer's `## Spaces` entry round-trips to the child path.
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "projects/index.md" in e.href for e in entries)


def test_add_deep_path_registers_leaf_in_nearest_space_then_audit_clean(tmp_path):
    """`add a/b/c` creates only the leaf space and registers it directly in the
    nearest existing ancestor space (root) — intermediate dirs stay plain
    folders (graceful, least-branchy: no forced intermediate spaces). The
    reader then sees the leaf reachable and reports zero drift."""
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "a/b/c"])
    assert rc == 0, err
    assert (wiki / "a" / "b" / "c" / "index.md").is_file()
    # intermediate dirs are plain folders, NOT promoted to spaces
    assert not (wiki / "a" / "index.md").exists()
    assert not (wiki / "a" / "b" / "index.md").exists()

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out

    rc, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    paths = {e["path"] for e in json.loads(out)}
    assert "a/b/c" in paths
    assert "a" not in paths and "a/b" not in paths, "intermediate folders wrongly listed as spaces"

    # parse-back: the writer registered the leaf directly under root.
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "a/b/c" in e.href for e in entries)


# ---------------------------------------------------------------------------
# promote → readers agree (extends the two regression tests above).
# ---------------------------------------------------------------------------


def test_promote_injected_alias_is_resolvable(tmp_path):
    """`promote` injects `aliases: [<basename>]` into the new index.md; a page
    authored AFTER the promote that links via that alias then resolves — audit
    reports no broken wikilink (producer writes the alias, reader resolves it)."""
    wiki = _make_wiki(tmp_path)
    write_tree(wiki, {"topics/widget.md": "# Widget\n\nbody\n"})

    rc, _, err = _run(["--wiki", str(wiki), "promote", "topics/widget.md"])
    assert rc == 0, err
    idx = (wiki / "topics" / "widget" / "index.md").read_text()
    assert "aliases:" in idx and "widget" in idx
    assert not (wiki / "topics" / "widget.md").exists(), "old path not removed"

    write_tree(wiki, {"later.md": "# Later\n\nlink via the injected alias [[widget]]\n"})
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, f"injected alias not resolvable:\n{out}"
    # the finding marker is `! broken wikilink [[...]]`; the clean summary says
    # "no broken wikilinks", so match the marker, not the summary word.
    assert "! broken wikilink" not in out


def test_promote_inserts_spaces_into_bare_parent_then_audit_clean(tmp_path):
    """`promote`'s chain helper inserts `## Spaces` into a parent index.md that
    lacks it before registering the promoted entry — so the reader goes from
    `missing ## Spaces` to clean in one writer step."""
    wiki = _make_wiki(tmp_path)
    write_tree(wiki, {
        "index.md": "# wiki\n\n## What this space is\n\nx\n\n## Spaces\n\n- [A](a/index.md)\n",
        "a/index.md": "# A\n\nprose only — no ## Spaces yet\n",
        "a/page.md": "# Page\n\nbody\n",
    })
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert "a" in json.loads(out)["missing_spaces_section"]

    rc, _, err = _run(["--wiki", str(wiki), "promote", "a/page.md"])
    assert rc == 0, err
    assert (wiki / "a" / "page" / "index.md").is_file()

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    entries = _md.parse_section_entries((wiki / "a" / "index.md").read_text(), "Spaces")
    assert any(e.href and "page/index.md" in e.href for e in entries)


# ---------------------------------------------------------------------------
# mount ↔ remove symmetry (writer pair) → readers agree.
# ---------------------------------------------------------------------------


def test_mount_symlink_roundtrip_then_remove_symmetry(tmp_path):
    """mount --mode symlink (writer) registers an external space the readers
    see; remove (its inverse writer) unlinks the symlink and de-registers it,
    leaving the upstream source byte-for-byte untouched and audit clean."""
    wiki = _make_wiki(tmp_path)
    src = make_space_dir(tmp_path / "ext-src", "team")
    write_tree(src, {"tnote.md": "# T\n\nupstream note\n"})

    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 0, err
    link = wiki / "shared" / "team"
    assert link.is_symlink()

    # reader: registered + classified external + audit clean.
    rc, out, _ = _run(["--wiki", str(wiki), "list", "--include-external", "--json"])
    assert any(e["path"] == "shared/team" and e["external"] for e in json.loads(out))
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out

    # inverse writer: remove unlinks + de-registers; upstream untouched.
    # --force because the linked source carries tnote.md beyond index.md.
    rc, _, err = _run(
        ["--wiki", str(wiki), "remove", "shared/team", "--force", "--force-external"]
    )
    assert rc == 0, err
    assert not link.exists(), "symlink not unlinked"
    assert (src / "index.md").is_file(), "upstream clobbered"
    assert (src / "tnote.md").is_file(), "upstream clobbered"

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert not any(e.href and "shared/team" in e.href for e in entries)


@pytest.mark.skipif(not HAS_GIT, reason="git not on PATH")
def test_mount_clone_roundtrip_then_remove_symmetry(tmp_path):
    """mount --mode clone (writer) → reader sees it; remove rmtrees the clone
    and de-registers, leaving the upstream repo intact and audit clean."""
    wiki = _make_wiki(tmp_path)
    repo = make_git_repo(tmp_path / "repo", "team")

    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(repo), "shared/team", "--mode", "clone"]
    )
    assert rc == 0, err
    clone = wiki / "shared" / "team"
    assert clone.is_dir() and not clone.is_symlink()

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out

    rc, _, err = _run(
        ["--wiki", str(wiki), "remove", "shared/team", "--force", "--force-external"]
    )
    assert rc == 0, err
    assert not clone.exists(), "clone not removed"
    assert (repo / "index.md").is_file(), "upstream repo clobbered"

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out


# ---------------------------------------------------------------------------
# audit --fix → re-audit clean + idempotent (the bounded self-correction).
# ---------------------------------------------------------------------------


def test_audit_fix_repairs_then_reaudit_clean_and_idempotent(tmp_path):
    """`audit --fix` (writer) inserts missing `## Spaces` and registers unlisted
    on-disk children; a fresh `audit` (reader) is then clean, and a SECOND
    `--fix` changes nothing — the exactly-once idempotency the self-maintenance
    loop relies on."""
    wiki = _make_wiki(tmp_path)
    write_tree(wiki, {
        "drift/index.md": "# Drift\n\n## Spaces\n\n",       # valid space, unregistered
        "bare/index.md": "# Bare\n\nno spaces section\n",   # index.md without ## Spaces
    })
    rc, _, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1, "expected drift before fix"

    rc, _, err = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 0, err

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, f"re-audit not clean after --fix:\n{out}"

    before = (wiki / "index.md").read_text()
    bare_before = (wiki / "bare" / "index.md").read_text()
    rc, _, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert (wiki / "index.md").read_text() == before, "second --fix mutated root"
    assert (wiki / "bare" / "index.md").read_text() == bare_before, "second --fix mutated bare"


# ---------------------------------------------------------------------------
# check-size verdict == the cap the framework writers enforce (one engine).
# ---------------------------------------------------------------------------


def test_check_size_boundary_and_writer_share_cap_engine(tmp_path):
    """`check-size` (reader) and `add` (writer) resolve the cap through the SAME
    engine. A tiny user-override cap makes the boundary observable: <= cap is
    OK, > cap is OVER, and `add` (whose barren index.md exceeds the cap) refuses
    — never silently truncating."""
    wiki = _make_wiki(tmp_path)
    write_tree(wiki, {
        "_meta/limits.md": (
            "# Size limits\n\n"
            "| Pattern        | Cap (chars) |\n"
            "|----------------|-------------|\n"
            "| tiny/index.md  |           1 |\n"
        ),
    })
    # reader: at-cap is OK, one char over is OVER (frontmatter-stripped body).
    rc, out, _ = _run(["--wiki", str(wiki), "check-size", "tiny/index.md"], stdin="x")
    assert rc == 0 and out.startswith("OK 1/1"), out
    rc, out, _ = _run(["--wiki", str(wiki), "check-size", "tiny/index.md"], stdin="xx")
    assert rc == 1 and out.startswith("OVER 2/1"), out

    # writer: `add tiny` projects a barren index.md well over the 1-char cap, so
    # the same engine refuses and writes nothing.
    rc, _, err = _run(["--wiki", str(wiki), "add", "tiny"])
    assert rc != 0, "add ignored the cap the reader enforces"
    assert "1" in err or "cap" in err.lower() or "size" in err.lower()
    assert not (wiki / "tiny").exists(), "add wrote despite the cap"


# ---------------------------------------------------------------------------
# log append → re-read parses (rotation is unit-covered in test_log.py).
# ---------------------------------------------------------------------------


def test_log_append_then_reread_parses_every_entry(tmp_path):
    """`space log` (writer) appends structured lines to log.md; re-reading
    (reader) recovers every appended entry — producer=consumer for the log."""
    wiki = _make_wiki(tmp_path)
    for i in range(3):
        rc, _, err = _run(
            ["--wiki", str(wiki), "log", "SEARCH", f"query=q{i}", "--create"]
        )
        assert rc == 0, err
    log = (wiki / "log.md").read_text()
    assert log.count("SEARCH") == 3
    assert "query=q0" in log and "query=q1" in log and "query=q2" in log


# ---------------------------------------------------------------------------
# Malformed `## Spaces` entry the consumer drops must not pass audit clean
# (the `)`-trailing-content gap — producer=consumer).
# ---------------------------------------------------------------------------


def test_audit_flags_trailing_content_after_close_paren(tmp_path):
    """producer=consumer (the `)` carve-out gap): a hand-authored
    `- [Foo](foo)bar/index.md)` carries trailing content after the first `)`,
    so it fails the anchored `_md.ENTRY_RE` and the consumer walker drops it —
    `space list` never surfaces the space. Audit must NOT report OK on an entry
    the consumer silently ignores; it flags the unparseable shape and flips the
    exit code. (Distinct from `- [foo)-bar/](foo)-bar/index.md)`, which DOES
    parse — `-bar…` as the description — and is left to the drift pass.)
    """
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    bad = "- [Foo](foo)bar/index.md)"
    idx.write_text(idx.read_text() + bad + "\n")

    # consumer: `list` does not surface `foo` / `foobar` — the line is dropped.
    rc, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    paths = {e["path"] for e in json.loads(out)}
    assert "foo" not in paths and "foobar" not in paths, out

    # audit (consumer): flags it malformed and flips the exit code — before the
    # fix this returned exit_code 0 with empty malformed_entries/drift.
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    payload = json.loads(out)
    assert payload["exit_code"] == 1, out
    assert any(bad in m["issue"] for m in payload["malformed_entries"]), (
        payload["malformed_entries"]
    )

    # asymmetry closed: the producer refuses the same dirname.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo)bar"])
    assert rc != 0


# ---------------------------------------------------------------------------
# A root audit honors each nested space's own `_meta/limits.md` (per-space
# autonomy) — not the audit root's caps applied to every file.
# ---------------------------------------------------------------------------


def test_root_audit_honors_nested_space_limits(tmp_path):
    """Per-space autonomy: a nested space's OWN `_meta/limits.md` governs its
    own files even during a ROOT audit. Before the fix, `cmd_audit` loaded one
    cap table at the audit root and applied it to every crossed file, so a
    root audit silently missed a child space's tighter cap (only an
    `audit --wiki <child>` caught it). Now both surfaces agree."""
    wiki = _make_wiki(tmp_path)
    write_tree(wiki, {
        "index.md": "# wiki\n\n## Spaces\n\n- [Child](child/index.md)\n",
        "child/index.md": "# child\n\n## Spaces\n\n",
        # The child declares its OWN tighter cap for `page.md`.
        "child/_meta/limits.md": (
            "# Size limits\n\n"
            "| Pattern | Cap (chars) |\n"
            "|---------|-------------|\n"
            "| page.md |           5 |\n"
        ),
        "child/page.md": "this body is far more than five characters\n",
    })

    # child-scoped audit flags the violation (baseline / the only surface that
    # used to catch it).
    rc, out, _ = _run(["--wiki", str(wiki / "child"), "audit", "--json"])
    child = json.loads(out)
    assert child["exit_code"] == 1
    assert any(v["path"] == "page.md" for v in child["size_violations"]), out

    # ROOT audit must ALSO flag it now, against the CHILD's cap (5), not the
    # root's 15K `*.md` default.
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    root = json.loads(out)
    assert root["exit_code"] == 1, out
    viol = next(
        (v for v in root["size_violations"] if v["path"] == "child/page.md"), None
    )
    assert viol is not None, out
    assert viol["cap"] == 5, viol


def test_root_audit_applies_root_pattern_to_nested_without_override(tmp_path):
    """Fix-#2 regression guard: a ROOT-level pattern targeting a nested path
    (the documented `projects/**/*.md` shape) STILL applies to a child space
    that declares no `_meta/limits.md` of its own. Nearest-config-wins must
    fall back to the root when the child has no override — the fix must not
    break root-level nested patterns."""
    wiki = _make_wiki(tmp_path)
    write_tree(wiki, {
        "index.md": "# wiki\n\n## Spaces\n\n- [Child](child/index.md)\n",
        "child/index.md": "# child\n\n## Spaces\n\n",
        "_meta/limits.md": (
            "# Size limits\n\n"
            "| Pattern        | Cap (chars) |\n"
            "|----------------|-------------|\n"
            "| child/page.md  |           5 |\n"
        ),
        "child/page.md": "way more than five characters here\n",
    })
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    payload = json.loads(out)
    assert payload["exit_code"] == 1, out
    viol = next(
        (v for v in payload["size_violations"] if v["path"] == "child/page.md"), None
    )
    assert viol is not None and viol["cap"] == 5, out
