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
        # An external match carries the same marker `files` prints.
        self.assertIn("shared/team/index.md:1: # Team [external]", r.stdout)

    def test_ignore_case_flag(self):
        self.assertEqual(self.grep("alpha notes").returncode, 1)
        r = self.grep("alpha notes", "-i")
        self.assertEqual(r.returncode, 0)
        self.assertIn("alpha/alpha-notes.md", r.stdout)

    def test_fixed_string_flag_takes_metacharacters_literally(self):
        # A page named `notes (2024)` read as a regex turns the parens
        # into a group and never matches its own literal links — the
        # silent miss a link inventory cannot afford. -F carries the
        # escaping (one right answer), and composes with -i.
        support.write(self.root / "wip.md",
                      "# WIP\n\nSee [[notes (2024)]] for the ledger.\n")
        self.assertEqual(self.grep("notes (2024)").returncode, 1)
        r = self.grep("notes (2024)", "-F")
        self.assertEqual(r.returncode, 0)
        self.assertIn("wip.md:3: See [[notes (2024)]] for the ledger.",
                      r.stdout)
        self.assertEqual(self.grep("NOTES (2024)", "-F", "-i").returncode, 0)

    def test_bad_pattern_cannot_operate(self):
        r = self.grep("[unclosed")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad pattern", r.stderr)


if __name__ == "__main__":
    unittest.main()
