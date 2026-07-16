"""The stderr advisory channel: the resolved root announced on every data
command, and `note:` lines naming what a walk skipped — unreachable spaces,
external paths, unreadable files — and the enclosing wiki when the root is
nested inside one. Stdout stays pure data throughout."""
import tempfile
import unittest
from pathlib import Path

import support


class NotesTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        support.build_demo(self.root)
        self.addCleanup(self._td.cleanup)

    def test_root_announced_on_stderr_for_data_commands(self):
        for cmd in (("list",), ("files",), ("grep", "Alpha")):
            r = support.run_ws(*cmd, "--wiki", str(self.root))
            self.assertIn(f"wiki: {self.root}", r.stderr, cmd)
        # audit already announces on stdout — no duplicate on stderr.
        r = support.run_ws("audit", "--wiki", str(self.root))
        self.assertNotIn("wiki:", r.stderr)

    def test_unreachable_spaces_are_named(self):
        r = support.run_ws("files", "--wiki", str(self.root))
        self.assertIn("unreachable via ## Spaces", r.stderr)
        self.assertIn("unregistered/", r.stderr)
        self.assertIn("bare/", r.stderr)
        # grep shares the walk, so a no-match answer names what it never
        # looked at instead of reading as "the wiki doesn't have it".
        r = support.run_ws("grep", "Unregistered", "--wiki", str(self.root))
        self.assertEqual(r.returncode, 1)
        self.assertIn("unregistered/", r.stderr)

    def test_stale_entries_are_named(self):
        # A registered space deleted on disk must not vanish silently
        # from list/files/grep — the one declined thing the advisory
        # channel never named. The audit's finding stays the repair
        # surface; the note points at it.
        for cmd in (("list",), ("files",)):
            r = support.run_ws(*cmd, "--wiki", str(self.root))
            self.assertIn("stale entries, skipped", r.stderr, cmd)
            self.assertIn("missing/", r.stderr, cmd)

    def test_skipped_externals_are_named(self):
        # A count would hide a user's own folder named `shared/` — the
        # skipped paths are named, so nothing is captured silently.
        r = support.run_ws("list", "--wiki", str(self.root))
        self.assertIn("external, skipped (--external to include): "
                      "shared/team/", r.stderr)
        r = support.run_ws("list", "--external", "--wiki", str(self.root))
        self.assertNotIn("external, skipped", r.stderr)

    def test_audit_names_what_its_walk_skipped(self):
        # Silence speaks for the walk's reach, never the whole disk — the
        # audit's walk emits the same advisories the data commands do.
        r = support.run_ws("audit", "--wiki", str(self.root))
        self.assertIn("external, skipped", r.stderr)
        self.assertIn("shared/", r.stderr)
        r = support.run_ws("audit", "--external", "--wiki", str(self.root))
        self.assertNotIn("external, skipped", r.stderr)

    def test_nested_root_names_the_enclosing_wiki(self):
        # Trust scope never looks above the resolved root, so a mount the
        # enclosing walk calls external can read as owned from a nested
        # one — the fact was held by prose alone; every command names it.
        for cmd in (("list",), ("audit",)):
            r = support.run_ws(*cmd, "--wiki", str(self.root / "alpha"))
            self.assertIn("note: root is nested inside a wiki "
                          f"({self.root})", r.stderr, cmd)
        # The top-level root has no enclosing wiki — no note.
        r = support.run_ws("list", "--wiki", str(self.root))
        self.assertNotIn("nested inside", r.stderr)

    def test_unreadable_files_are_named_by_grep(self):
        (self.root / "bad.md").write_bytes(b"\xff\xfe nope\n")
        r = support.run_ws("grep", "anything-at-all", "--wiki", str(self.root))
        self.assertIn("unreadable (not UTF-8), skipped: bad.md", r.stderr)

    def test_stdout_stays_pure(self):
        r = support.run_ws("files", "--wiki", str(self.root))
        for line in r.stdout.splitlines():
            self.assertNotIn("note:", line)
            self.assertFalse(line.startswith("wiki:"))


if __name__ == "__main__":
    unittest.main()
