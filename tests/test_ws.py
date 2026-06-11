"""Unit tests for ws.py primitives: section parsing, href normalization,
cap tables, and the check-size verdict."""
import tempfile
import unittest
from pathlib import Path

import support

ws = support.load_ws()


class SectionTests(unittest.TestCase):
    def test_find_section_basic(self):
        lines = "# T\n\n## Spaces\n\n- x\n\n## Other\n".splitlines()
        self.assertEqual(ws.find_section(lines), (2, 3, 6))

    def test_heading_inside_fence_is_not_the_contract(self):
        text = "# T\n\n```\n## Spaces\n```\n"
        self.assertFalse(ws.has_spaces(text))

    def test_fence_closes_only_on_same_char_and_length(self):
        lines = "````\n```\nstill fenced\n````\nout\n".splitlines()
        self.assertEqual(ws.fenced_mask(lines), [True, True, True, True, False])

    def test_parse_spaces_entries_and_malformed(self):
        text = (
            "## Spaces\n"
            "\n"
            "- [a/](a/index.md) — desc\n"
            "- [b/](b/index.md)   \n"
            "- [c/](c/index.md) – en-dash desc\n"
            "- [d/](d/index.md) - hyphen desc\n"
            "- [[wikilink-form]]\n"
            "- plain bullet\n"
            "prose line is fine\n"
        )
        entries, malformed = ws.parse_spaces(text)
        self.assertEqual([h for _l, h in entries],
                         ["a/index.md", "b/index.md", "c/index.md",
                          "d/index.md"])
        self.assertEqual(malformed, ["- [[wikilink-form]]", "- plain bullet"])

    def test_blank_spaces_section_preserves_other_sections(self):
        body = "## Items\n- [[kept]]\n## Spaces\n- [[gone]]\n"
        blanked = ws.blank_spaces_section(body)
        self.assertIn("[[kept]]", blanked)
        self.assertNotIn("[[gone]]", blanked)

    def test_body_after_frontmatter(self):
        text = "---\ntitle: x\n---\nbody\n"
        self.assertEqual(ws.body_after_frontmatter(text), "body")
        self.assertEqual(ws.body_after_frontmatter("no fm\n"), "no fm\n")


class HrefTests(unittest.TestCase):
    def test_normalizes_index_md_and_slashes(self):
        self.assertEqual(ws.normalize_href("a/index.md"), "a")
        self.assertEqual(ws.normalize_href("a/"), "a")
        self.assertEqual(ws.normalize_href("nested/b/index.md"), "nested/b")

    def test_rejects_unregistrable_shapes(self):
        for href in ("", "/abs", "../up", "a/../b", "_meta/x", ".hidden/y",
                     "a{b}", "self/..", ".", "./"):
            self.assertIsNone(ws.normalize_href(href), href)


class CapTests(unittest.TestCase):
    def test_defaults_are_basename_keyed(self):
        caps = dict(ws.DEFAULT_CAPS)
        self.assertEqual(ws.cap_for("index.md", caps), 5000)
        self.assertEqual(ws.cap_for("log.md", caps), 100000)
        self.assertEqual(ws.cap_for("hot.md", caps), 100000)
        self.assertEqual(ws.cap_for("anything.md", caps), 15000)
        self.assertIsNone(ws.cap_for("data.json", caps))

    def test_limits_md_overrides_and_ignores_noise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            support.write(root / "_meta" / "limits.md", (
                "# heading ignored\n"
                "index.md: 1234\n"
                "custom.md: 8000\n"
                "data.json: 99\n"
                "| table.md | 5 |\n"
                "path/keyed.md: 5\n"
                "notanumber.md: ten\n"
                "zero.md: 0\n"
            ))
            caps = ws.load_caps(root)
            self.assertEqual(ws.cap_for("index.md", caps), 1234)
            self.assertEqual(ws.cap_for("custom.md", caps), 8000)
            self.assertEqual(ws.cap_for("data.json", caps), 99)
            self.assertEqual(ws.cap_for("table.md", caps), 15000)
            self.assertEqual(ws.cap_for("keyed.md", caps), 15000)
            self.assertEqual(ws.cap_for("notanumber.md", caps), 15000)
            self.assertEqual(ws.cap_for("zero.md", caps), 15000)

    def test_star_md_recaps_the_catchall(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            support.write(root / "_meta" / "limits.md", "*.md: 9000\n")
            caps = ws.load_caps(root)
            self.assertEqual(ws.cap_for("anything.md", caps), 9000)
            # Basename entries still beat the catch-all; non-md stays uncapped.
            self.assertEqual(ws.cap_for("index.md", caps), 5000)
            self.assertIsNone(ws.cap_for("data.json", caps))


class CheckSizeTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        support.write(self.root / "index.md", "# W\n\n## Spaces\n")
        self.addCleanup(self._td.cleanup)

    def test_on_disk_ok_and_over(self):
        page = self.root / "page.md"
        support.write(page, "x" * 15000)
        r = support.run_ws("check-size", str(page), "--wiki", str(self.root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        support.write(page, "x" * 15001)
        r = support.run_ws("check-size", str(page), "--wiki", str(self.root))
        self.assertEqual(r.returncode, 1)
        self.assertIn("never truncate", r.stdout)

    def test_stdin_measures_planned_content(self):
        target = str(self.root / "new" / "index.md")
        r = support.run_ws("check-size", target, "--stdin",
                           "--wiki", str(self.root), stdin="x" * 5001)
        self.assertEqual(r.returncode, 1)
        self.assertIn("5001 > 5000", r.stdout)
        r = support.run_ws("check-size", target, "--stdin",
                           "--wiki", str(self.root), stdin="ok")
        self.assertEqual(r.returncode, 0)

    def test_no_cap_for_non_markdown(self):
        asset = self.root / "data.json"
        support.write(asset, "{}")
        r = support.run_ws("check-size", str(asset), "--wiki", str(self.root))
        self.assertEqual(r.returncode, 0)
        self.assertIn("no cap", r.stdout)

    def test_missing_file_without_stdin_cannot_operate(self):
        r = support.run_ws("check-size", str(self.root / "ghost.md"),
                           "--wiki", str(self.root))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
