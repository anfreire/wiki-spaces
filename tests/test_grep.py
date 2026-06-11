"""The grep command: line matches over the contract-reachable file set,
trust scope and reserved dirs held, regex errors fail closed."""
import tempfile
import unittest
from pathlib import Path

import support


class GrepTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        support.build_demo(self.root)
        support.write(self.root / "_archives" / "old.md",
                      "# Old\n\narchived needle\n")
        self.addCleanup(self._td.cleanup)

    def grep(self, *args):
        return support.run_ws("grep", *args, "--wiki", str(self.root))

    def test_matches_print_rel_line_text_and_exit_zero(self):
        r = self.grep("Alpha Notes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("alpha/alpha-notes.md:1: # Alpha Notes", r.stdout)

    def test_no_match_exits_one(self):
        r = self.grep("definitely-not-present")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")

    def test_scope_excludes_external_archives_and_unreachable(self):
        r = self.grep("needle|Team|Unregistered")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        r = self.grep("Team", "--external")
        self.assertEqual(r.returncode, 0)
        self.assertIn("shared/team/index.md:1: # Team", r.stdout)

    def test_ignore_case_flag(self):
        self.assertEqual(self.grep("alpha notes").returncode, 1)
        r = self.grep("alpha notes", "-i")
        self.assertEqual(r.returncode, 0)
        self.assertIn("alpha/alpha-notes.md", r.stdout)

    def test_bad_pattern_cannot_operate(self):
        r = self.grep("[unclosed")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad pattern", r.stderr)


if __name__ == "__main__":
    unittest.main()
