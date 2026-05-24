"""End-to-end producer→consumer tests.

These tests exercise the full chain: a producer (CLI command or skill-equivalent
write) emits content, then a consumer (audit, search, etc.) operates on the
result. They catch the failure class that single-component unit tests cannot —
where each component is correct in isolation but the loop breaks at a seam.

Defect #1 (promote→audit producer/consumer break) was exactly such a bug:
promote emitted wikilinks audit could not resolve, and 274 unit tests passed
because no test combined the two.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from wiki_spaces import space


def _make_wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text(
        "# wiki\n\n## What this space is\n\nTest wiki\n\n## Spaces\n\n"
    )
    return root


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = space.main(args)
    return rc, out.getvalue(), err.getvalue()


def test_promote_then_audit_clean(tmp_path):
    """Defect #1 regression. Sequence:
      1. Build a wiki with a content page `concepts/foo.md` and a referencing page
         `notes/ref.md` containing `[[foo]]`.
      2. `space promote concepts/foo.md` — the producer rewrites the reference to
         `[[concepts/foo/index|foo]]` (wiki-root pathful).
      3. `space audit` — the consumer must resolve that pathful wikilink to the
         new `concepts/foo/index.md` and report zero broken links.

    Pre-fix: audit could only resolve base-relative + bare-name forms, so the
    rewritten link was flagged broken. Post-fix: unified resolver handles
    wiki-root pathful first, audit succeeds.
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
