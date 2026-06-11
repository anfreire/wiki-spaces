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
        self.assertIn("contract bare/index.md: no ## Spaces heading", out)
        self.assertIn("drift index.md: missing entry for bare/", out)
        self.assertIn("drift index.md: missing entry for unregistered/", out)
        self.assertIn("drift index.md: stale entry missing/", out)
        self.assertIn("broken notes.md: [[nope]]", out)
        self.assertIn("over-cap big.md:", out)
        self.assertIn("over-cap tiny.md:", out)

    def test_orphans_are_informational(self):
        support.write(self.root / "missing" / "index.md", "# M\n\n## Spaces\n")
        support.write(self.root / "bare" / "index.md", "# B\n\n## Spaces\n")
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

    def test_fix_inserts_heading_and_registers_children(self):
        r = self.audit("--fix")
        self.assertIn("~ bare/index.md: inserted ## Spaces heading", r.stdout)
        self.assertIn("~ index.md: registered bare/", r.stdout)
        self.assertIn("~ index.md: registered unregistered/", r.stdout)
        bare = (self.root / "bare" / "index.md").read_text(encoding="utf-8")
        self.assertTrue(ws.has_spaces(bare))
        index = (self.root / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [bare/](bare/index.md)", index)
        self.assertIn("- [unregistered/](unregistered/index.md)", index)
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
        bare = self.root / "bare"
        bare.chmod(0o555)
        self.addCleanup(bare.chmod, 0o755)
        r = self.audit("--fix")
        self.assertIn("! bare/index.md: write failed:", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(
            ws.has_spaces((bare / "index.md").read_text(encoding="utf-8")))

    def test_fix_preserves_file_mode(self):
        index = self.root / "index.md"
        index.chmod(0o604)
        r = self.audit("--fix")
        self.assertIn("~ index.md: registered", r.stdout)
        self.assertEqual(index.stat().st_mode & 0o777, 0o604)

    def test_fix_respects_the_index_cap(self):
        limits = self.root / "_meta" / "limits.md"
        support.write(limits, "index.md: 50\n")
        index = self.root / "index.md"
        before = index.read_text(encoding="utf-8")
        r = self.audit("--fix")
        self.assertIn("would exceed cap", r.stdout)
        self.assertEqual(index.read_text(encoding="utf-8"), before)

    def test_clean_wiki_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            clean = Path(td) / "w"
            support.write(clean / "index.md",
                          "# W\n\n## Spaces\n\n- [a/](a/index.md)\n")
            support.write(clean / "a" / "index.md", "# A\n\n## Spaces\n")
            r = support.run_ws("audit", "--wiki", str(clean))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.splitlines()[-1], "ok")


if __name__ == "__main__":
    unittest.main()
