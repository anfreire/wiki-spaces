"""Audit findings and the bounded --fix: drift, contract violations, broken
wikilinks, over-cap files, orphan reporting, and what --fix may touch."""
import os
import tempfile
import unittest
from pathlib import Path

import support

ws = support.load_ws()


class AuditTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        support.build_demo(self.root)
        self.addCleanup(self._td.cleanup)

    def audit(self, *args):
        return support.run_ws("audit", "--wiki", str(self.root), *args)

    def test_findings_and_exit_code(self):
        r = self.audit()
        self.assertEqual(r.returncode, 1)
        out = r.stdout
        # Unregistered + bare = undeclared: reported, never a fix target.
        self.assertIn("contract bare/index.md: no ## Spaces heading (not a "
                      "space until it carries ## Spaces", out)
        self.assertNotIn("missing entry for bare/", out)
        # Registered + bare = half-declared: the completion-rule fix target.
        self.assertIn("contract halfway/index.md: no ## Spaces heading "
                      "(registered — audit --fix inserts the heading)", out)
        self.assertIn("drift index.md: missing entry for unregistered/", out)
        self.assertIn("drift index.md: stale entry missing/", out)
        self.assertIn("broken notes.md: [[nope]]", out)
        self.assertIn("over-cap big.md:", out)
        self.assertIn("over-cap tiny.md:", out)

    def test_orphans_are_informational(self):
        support.write(self.root / "missing" / "index.md", "# M\n\n## Spaces\n")
        support.write(self.root / "bare" / "index.md", "# B\n\n## Spaces\n")
        support.write(self.root / "halfway" / "index.md",
                      "# H\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [bare/](bare/index.md)\n"
                      + "- [unregistered/](unregistered/index.md)\n")
        (self.root / "big.md").unlink()
        (self.root / "tiny.md").unlink()
        notes = self.root / "notes.md"
        support.write(notes, "# Notes\n\nSee [[alpha-notes]].\n")
        r = self.audit()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("orphan.md", r.stdout)   # still reported
        self.assertIn("ok", r.stdout.splitlines()[-1])

    def test_embeds_and_code_spans_are_exempt(self):
        out = self.audit().stdout
        self.assertNotIn("photo", out)
        self.assertNotIn("fenced-ghost", out)
        self.assertNotIn("span-ghost", out)

    def test_spaces_section_is_contract_not_content(self):
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [[ghost-entry]]\n")
        out = self.audit().stdout
        self.assertIn("contract index.md: malformed entry: - [[ghost-entry]]",
                      out)
        self.assertNotIn("broken index.md", out)

    def test_markdown_links_count_as_incoming(self):
        # orphan.md gains one markdown link from notes.md and stops being
        # an orphan; wikilinks are not the only citation form.
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nAlso [the orphan](orphan.md).\n")
        self.assertNotIn("orphan.md", self.audit().stdout)

    def test_fix_completes_half_declared_spaces_only(self):
        r = self.audit("--fix")
        # Registered + bare: the heading is inserted (parent declared it).
        self.assertIn("fixed halfway/index.md: inserted ## Spaces heading",
                      r.stdout)
        self.assertTrue(ws.has_spaces(
            (self.root / "halfway" / "index.md").read_text(encoding="utf-8")))
        # Valid + unlisted: registered (the child declared itself).
        self.assertIn("fixed index.md: registered unregistered/", r.stdout)
        index = (self.root / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [unregistered/](unregistered/index.md)", index)
        # Undeclared (bare + unregistered): never promoted, never registered.
        self.assertNotIn("fixed bare/", r.stdout)
        self.assertEqual(
            (self.root / "bare" / "index.md").read_text(encoding="utf-8"),
            "# Bare\n")
        self.assertNotIn("- [bare/](bare/index.md)", index)
        # Stale entries and broken links are reported, never auto-removed.
        self.assertIn("drift index.md: stale entry missing/", r.stdout)
        self.assertIn("- [missing/](missing/index.md)", index)
        self.assertEqual(r.returncode, 1)

    def test_fix_refuses_registration_next_to_malformed_bullet(self):
        index = self.root / "index.md"
        before = index.read_text(encoding="utf-8") + "- broken bullet\n"
        support.write(index, before)
        r = self.audit("--fix")
        self.assertIn("malformed bullet", r.stdout)
        after = index.read_text(encoding="utf-8")
        self.assertNotIn("unregistered/index.md", after)

    def test_fix_never_writes_external_spaces(self):
        support.write(self.root / "shared" / "team" / "index.md", "# Team\n")
        before = (self.root / "shared" / "team" / "index.md").read_text(
            encoding="utf-8")
        r = self.audit("--fix", "--external")
        after = (self.root / "shared" / "team" / "index.md").read_text(
            encoding="utf-8")
        self.assertEqual(before, after)
        self.assertIn("contract shared/team/index.md: no ## Spaces heading",
                      r.stdout)

    @unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0,
                     "permission denial is a no-op as root")
    def test_fix_reports_a_failed_write_and_moves_on(self):
        # A write the filesystem refuses is a reported repair failure with
        # its cause, not a traceback; the run and its exit code survive.
        halfway = self.root / "halfway"
        halfway.chmod(0o555)
        self.addCleanup(halfway.chmod, 0o755)
        r = self.audit("--fix")
        self.assertIn("fix-skipped halfway/index.md: write failed:", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(ws.has_spaces(
            (halfway / "index.md").read_text(encoding="utf-8")))

    def test_fix_preserves_file_mode(self):
        index = self.root / "index.md"
        index.chmod(0o604)
        r = self.audit("--fix")
        self.assertIn("fixed index.md: registered", r.stdout)
        self.assertEqual(index.stat().st_mode & 0o777, 0o604)

    def test_fix_respects_the_index_cap(self):
        limits = self.root / "_meta" / "limits.md"
        support.write(limits, "index.md: 50\n")
        index = self.root / "index.md"
        before = index.read_text(encoding="utf-8")
        r = self.audit("--fix")
        self.assertIn("would exceed cap", r.stdout)
        self.assertEqual(index.read_text(encoding="utf-8"), before)

    def test_registered_mount_health_is_watched_by_default(self):
        # A registered mount that stops being a wiki surfaces in the
        # DEFAULT audit — the entry is ours to watch even though the
        # interior is not — and --fix never repairs it.
        support.write(self.root / "shared" / "nota" / "index.md",
                      "# Not a wiki\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [shared/nota/](shared/nota/index.md)\n")
        r = self.audit()
        self.assertIn("mount shared/nota/: registered but not a wiki",
                      r.stdout)
        self.audit("--fix")
        self.assertEqual(
            (self.root / "shared" / "nota" / "index.md")
            .read_text(encoding="utf-8"),
            "# Not a wiki\n")

    def test_broken_relative_markdown_link_is_reported(self):
        support.write(self.root / "_archives" / "old.md", "# Old\n")
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nSee [gone](gone.md) and [old](_archives/old.md).\n")
        out = self.audit().stdout
        self.assertIn("broken notes.md: (gone.md)", out)
        # On disk in a reserved dir — outside the walk but not dangling.
        self.assertNotIn("(_archives/old.md)", out)

    def test_unreadable_file_is_reported_and_counted(self):
        (self.root / "bad.md").write_bytes(b"\xff\xfe not utf-8\n")
        r = self.audit()
        self.assertIn("unreadable bad.md: not UTF-8", r.stdout)
        self.assertEqual(r.returncode, 1)

    def test_near_miss_heading_is_hinted_and_fix_defers(self):
        # A registered child carrying a near-miss heading is half-declared,
        # but the repair defers to a rename — the author almost certainly
        # meant the contract, and a second heading next to it helps nobody.
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [low/](low/index.md)\n- [hash/](hash/index.md)\n")
        support.write(self.root / "low" / "index.md", "# Low\n\n## spaces\n")
        support.write(self.root / "hash" / "index.md",
                      "# Hash\n\n## Spaces ##\n")
        r = self.audit()
        self.assertIn('contract low/index.md: no ## Spaces heading '
                      '(carries "## spaces" — rename it to ## Spaces)',
                      r.stdout)
        self.assertIn('contract hash/index.md: no ## Spaces heading '
                      '(carries "## Spaces ##" — rename it to ## Spaces)',
                      r.stdout)
        r = self.audit("--fix")
        self.assertIn('fix-skipped low/index.md: carries "## spaces"',
                      r.stdout)
        self.assertIn('fix-skipped hash/index.md: carries "## Spaces ##"',
                      r.stdout)
        for name in ("low", "hash"):
            text = (self.root / name / "index.md").read_text(encoding="utf-8")
            self.assertNotIn("\n## Spaces\n", text)

    def test_external_caps_never_inherit_the_hosts_limits(self):
        # The host's table caps tiny.md at 10 bytes; across the trust
        # boundary the mount's own limits (or the defaults) govern.
        support.write(self.root / "shared" / "team" / "_meta" / "limits.md",
                      "*.md: 30000\n")
        support.write(self.root / "shared" / "team" / "tiny.md",
                      "# Well over ten bytes, fine by their caps\n")
        support.write(self.root / "shared" / "team" / "wide.md",
                      "# W\n" + "y" * 16000 + "\n")
        out = self.audit("--external").stdout
        self.assertNotIn("over-cap shared/team/tiny.md", out)
        self.assertNotIn("over-cap shared/team/wide.md", out)

    def test_external_findings_are_marked(self):
        support.write(self.root / "shared" / "team" / "big.md",
                      "# B\n" + "z" * 16000 + "\n")
        out = self.audit("--external").stdout
        self.assertRegex(out, r"over-cap shared/team/big\.md: .*\[external\]")

    def test_clean_wiki_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            clean = Path(td) / "w"
            support.write(clean / "index.md",
                          "# W\n\n## Spaces\n\n- [a/](a/index.md)\n")
            support.write(clean / "a" / "index.md", "# A\n\n## Spaces\n")
            r = support.run_ws("audit", "--wiki", str(clean))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.splitlines()[-1], "ok")

    def test_unregistrable_child_name_is_named_and_fix_refuses(self):
        # A folder name the contract cannot carry is a rename-this finding,
        # not eternal drift — and --fix must never write an entry its own
        # parser rejects. Two runs converge: same findings, no writes.
        support.write(self.root / "notes (2024)" / "index.md",
                      "# N\n\n## Spaces\n")
        before = (self.root / "index.md").read_text(encoding="utf-8")
        first = self.audit("--fix")
        self.assertIn("contract index.md: unregistrable child name: "
                      "notes (2024)/", first.stdout)
        self.assertNotIn("registered notes (2024)/", first.stdout)
        self.assertNotIn("missing entry for notes (2024)/", first.stdout)
        self.assertNotIn("malformed", first.stdout)
        second = self.audit("--fix")
        self.assertNotIn("fixed ", second.stdout)   # converged in one pass
        self.assertIn("unregistrable child name: notes (2024)/",
                      second.stdout)
        self.assertNotIn("malformed", second.stdout)
        after = (self.root / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("notes (2024)", after)
        # The registrable sibling was not blocked by the odd name.
        self.assertIn("- [unregistered/](unregistered/index.md)", after)
        self.assertEqual(after.replace(
            "- [unregistered/](unregistered/index.md)\n", ""), before)

    def test_fix_registers_a_name_needing_encoding(self):
        # A folder name a raw CommonMark destination cannot carry is
        # registered percent-encoded — dialect-valid, and the entry
        # round-trips through the parser back to the disk name.
        support.write(self.root / "my space" / "index.md",
                      "# M\n\n## Spaces\n")
        r = self.audit("--fix")
        self.assertIn("fixed index.md: registered my space/", r.stdout)
        index = (self.root / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [my space/](my%20space/index.md)", index)
        self.assertNotIn("(my space/index.md)", index)
        # The written entry resolves: no stale, no missing, converged.
        out = self.audit().stdout
        self.assertNotIn("my space", out)
        self.assertNotIn("my%20space", out)

    def test_unregistrable_is_exactly_what_encoding_cannot_carry(self):
        # `a%20b` looks odd but registers (encoded once more); `a[b`
        # cannot survive an entry and stays a rename-this finding — the
        # audit asks the same round-trip the fix verifies before writing.
        support.write(self.root / "a%20b" / "index.md", "# A\n\n## Spaces\n")
        support.write(self.root / "a[b" / "index.md", "# B\n\n## Spaces\n")
        r = self.audit("--fix")
        self.assertIn("fixed index.md: registered a%20b/", r.stdout)
        self.assertIn("contract index.md: unregistrable child name: a[b/",
                      r.stdout)
        index = (self.root / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [a%20b/](a%2520b/index.md)", index)
        self.assertNotIn("a[b", index)

    def test_any_bullet_marker_carries_an_entry(self):
        # CommonMark bullets are `-`, `*`, `+` — an entry rides any of
        # them, so none silently drops a space off the contract.
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "* [starred/](starred/index.md)\n")
        support.write(self.root / "starred" / "index.md",
                      "# S\n\n## Spaces\n")
        out = self.audit().stdout
        self.assertNotIn("starred", out)   # listed, resolved, no drift

    def test_bare_dir_interior_is_audited_though_unreachable(self):
        # `files`/`grep` stop at a bare-index dir (not contract-reachable),
        # but the audit's filesystem walk still sees inside — an over-cap
        # page there must not hide.
        support.write(self.root / "bare" / "inner.md",
                      "# I\n\n" + "x" * 15100 + "\n")
        r = support.run_ws("files", "--wiki", str(self.root))
        self.assertNotIn("bare/inner.md", r.stdout)
        self.assertIn("over-cap bare/inner.md", self.audit().stdout)

    def test_duplicate_entry_is_flagged(self):
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [alpha/](alpha/index.md) — again\n")
        self.assertIn("contract index.md: duplicate entry: alpha/",
                      self.audit().stdout)

    def test_second_spaces_heading_is_flagged(self):
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "\n## Spaces\n\n- [beta/](beta/index.md)\n")
        self.assertIn("contract index.md: a second ## Spaces heading",
                      self.audit().stdout)

    def test_frontmatter_heading_is_not_the_contract(self):
        # `## Spaces` inside YAML frontmatter is metadata, not a heading:
        # the dir is bare (and undeclared here), so nothing promotes it.
        support.write(self.root / "fm" / "index.md",
                      "---\ntitle: x\n## Spaces\n---\n\n# Real body.\n")
        r = self.audit("--fix")
        self.assertIn("contract fm/index.md: no ## Spaces heading", r.stdout)
        self.assertNotIn("fixed fm/", r.stdout)
        text = (self.root / "fm" / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("# Real body.\n- [", text)

    def test_asset_wikilinks_are_exempt(self):
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nThe scan ([[report.pdf]]) names an asset.\n")
        out = self.audit().stdout
        self.assertNotIn("report.pdf", out)
        self.assertIn("broken notes.md: [[nope]]", out)  # pages still checked

    def test_dangling_upward_markdown_link_is_broken(self):
        outside = self.root.parent / "beside.md"
        support.write(outside, "# Beside\n")
        self.addCleanup(outside.unlink)
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nUp: [gone](../nowhere.md), [there](../beside.md).\n")
        out = self.audit().stdout
        self.assertIn("broken notes.md: (../nowhere.md)", out)
        self.assertNotIn("(../beside.md)", out)

    def test_indented_code_is_not_scanned(self):
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nAn example:\n\n    [[indent-ghost]] "
                      "[also](indent-ghost.md)\n")
        out = self.audit().stdout
        self.assertNotIn("indent-ghost", out)

    def test_ignored_names_are_invisible_to_the_walk(self):
        support.write(self.root / "_meta" / "ignore.md",
                      "# Ignore\n\nnode_modules\n")
        support.write(self.root / "node_modules" / "pkg" / "README.md",
                      "# Pkg\n\n[gone](CHANGELOG.md)\n" + "x" * 16000 + "\n")
        r = self.audit()
        self.assertNotIn("node_modules", r.stdout)
        files = support.run_ws("files", "--wiki", str(self.root))
        self.assertNotIn("node_modules", files.stdout)

    def test_crlf_registration_preserves_line_endings(self):
        index = self.root / "index.md"
        crlf = index.read_text(encoding="utf-8").replace("\n", "\r\n")
        index.write_bytes(crlf.encode("utf-8"))
        r = self.audit("--fix")
        self.assertIn("fixed index.md: registered unregistered/", r.stdout)
        raw = index.read_bytes().decode("utf-8")
        self.assertIn("- [unregistered/](unregistered/index.md)\r\n", raw)
        self.assertNotRegex(raw, r"[^\r]\n")

    def test_unregistered_external_mount_is_drift_under_external(self):
        support.write(self.root / "shared" / "loose" / "index.md",
                      "# Loose\n\n## Spaces\n")
        out = self.audit().stdout
        self.assertNotIn("shared/loose", out)   # default audit stays out
        r = self.audit("--external", "--fix")
        self.assertIn("drift index.md: missing entry for shared/loose/ "
                      "(register mounts by hand) [external]", r.stdout)
        self.assertNotIn("registered shared/loose/", r.stdout)
        index = (self.root / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("shared/loose", index)


if __name__ == "__main__":
    unittest.main()
