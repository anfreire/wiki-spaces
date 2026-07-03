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
        # An entry rides any markdown bullet marker; any other
        # bullet-shaped line is malformed, whatever its marker.
        text = (
            "## Spaces\n"
            "\n"
            "- [a/](a/index.md) — desc\n"
            "- [b/](b/index.md)   \n"
            "- [c/](c/index.md) – en-dash desc\n"
            "- [d/](d/index.md) - hyphen desc\n"
            "* [e/](e/index.md) — star bullet\n"
            "+ [f/](f/index.md)\n"
            "- [[wikilink-form]]\n"
            "- plain bullet\n"
            "* stray star\n"
            "prose line is fine\n"
        )
        entries, malformed = ws.parse_spaces(text)
        self.assertEqual([h for _l, h in entries],
                         ["a/index.md", "b/index.md", "c/index.md",
                          "d/index.md", "e/index.md", "f/index.md"])
        self.assertEqual(malformed, ["- [[wikilink-form]]", "- plain bullet",
                                     "* stray star"])

    def test_blank_spaces_section_preserves_other_sections(self):
        body = "## Items\n- [[kept]]\n## Spaces\n- [[gone]]\n"
        blanked = ws.blank_spaces_section(body)
        self.assertIn("[[kept]]", blanked)
        self.assertNotIn("[[gone]]", blanked)

    def test_body_after_frontmatter(self):
        text = "---\ntitle: x\n---\nbody\n"
        self.assertEqual(ws.body_after_frontmatter(text), "body")
        self.assertEqual(ws.body_after_frontmatter("no fm\n"), "no fm\n")

    def test_heading_inside_frontmatter_is_not_the_contract(self):
        # A flush-left `## Spaces` line inside frontmatter is legal YAML
        # (a comment) — metadata, never contract. Every reader agrees on
        # where the body starts.
        self.assertFalse(ws.has_spaces("---\ntitle: x\n## Spaces\n---\nb\n"))
        self.assertTrue(ws.has_spaces("---\ntitle: x\n---\n\n## Spaces\n"))

    def test_near_miss_headings_are_detected_as_a_class(self):
        for text in ("# T\n\n## spaces\n", "# T\n\n## SPACES\n",
                     "# T\n\n## Spaces ##\n", "# T\n\n##Spaces\n"):
            self.assertIsNotNone(ws._heading_near_miss(text), text)
        self.assertIsNone(ws._heading_near_miss("# T\n\n## Spaces\n"))
        self.assertIsNone(ws._heading_near_miss("# T\n\n## Spacesuits\n"))

    def test_extra_spaces_headings_are_counted(self):
        self.assertEqual(ws.extra_spaces_headings(
            "## Spaces\n- a\n\n## Spaces\n- b\n"), 1)
        self.assertEqual(ws.extra_spaces_headings("## Spaces\n"), 0)
        self.assertEqual(ws.extra_spaces_headings(
            "## Spaces\n```\n## Spaces\n```\n"), 0)

    def test_strip_code_blanks_indented_blocks(self):
        stripped = ws.strip_code(
            "Prose [[kept]].\n\n    [[indent-ghost]]\n    more code\n\n"
            "- a list\n  - nested [[list-kept]]\n")
        self.assertIn("[[kept]]", stripped)
        self.assertIn("[[list-kept]]", stripped)   # 2-space list survives
        self.assertNotIn("indent-ghost", stripped)


class HrefTests(unittest.TestCase):
    def test_normalizes_index_md_and_slashes(self):
        self.assertEqual(ws.normalize_href("a/index.md"), "a")
        self.assertEqual(ws.normalize_href("a/"), "a")
        self.assertEqual(ws.normalize_href("nested/b/index.md"), "nested/b")

    def test_rejects_unregistrable_shapes(self):
        for href in ("", "/abs", "../up", "a/../b", "_meta/x", ".hidden/y",
                     "a{b}", "self/..", ".", "./"):
            self.assertIsNone(ws.normalize_href(href), href)

    def test_percent_encoding_decodes_to_the_disk_name(self):
        # The href is a CommonMark destination: Obsidian writes
        # `my%20space/index.md` for the `my space` folder.
        self.assertEqual(ws.normalize_href("my%20space/index.md"), "my space")
        self.assertEqual(ws.normalize_href("a%23b/"), "a#b")
        # Decoding happens before validation — nothing smuggles through.
        for href in ("%2E%2E/up", "%2Fabs", "a%5Bb", "_meta%2Fx"):
            self.assertIsNone(ws.normalize_href(href), href)

    def test_encode_href_round_trips_through_the_parser(self):
        for name in ("plain", "my space", "a#b", "50% off", "a%20b"):
            self.assertEqual(
                ws.normalize_href(ws.encode_href(name) + "/index.md"), name)


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
            caps = ws.caps_for_path(root / "x.md", root)
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
            caps = ws.caps_for_path(root / "x.md", root)
            self.assertEqual(ws.cap_for("anything.md", caps), 9000)
            # Basename entries still beat the catch-all; non-md stays uncapped.
            self.assertEqual(ws.cap_for("index.md", caps), 5000)
            self.assertIsNone(ws.cap_for("data.json", caps))

    def test_nearest_ancestor_limits_win_within_the_trust_domain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            support.write(root / "_meta" / "limits.md", "*.md: 20000\n")
            support.write(root / "a" / "_meta" / "limits.md", "*.md: 30000\n")
            (root / "a" / "b").mkdir(parents=True)
            (root / "c").mkdir()

            def cap(p):
                return ws.cap_for("p.md", ws.caps_for_path(p, root))

            self.assertEqual(cap(root / "p.md"), 20000)
            self.assertEqual(cap(root / "a" / "p.md"), 30000)
            # Nearest one found walking up wins — a space without its own
            # limits answers to the closest ancestor that has them.
            self.assertEqual(cap(root / "a" / "b" / "p.md"), 30000)
            self.assertEqual(cap(root / "c" / "p.md"), 20000)
            # The lookup never crosses a trust boundary: an external mount
            # answers to its own limits or the defaults, never the host's.
            (root / "shared" / "m").mkdir(parents=True)
            self.assertEqual(cap(root / "shared" / "m" / "p.md"), 15000)
            support.write(root / "shared" / "m2" / "_meta" / "limits.md",
                          "*.md: 50\n")
            self.assertEqual(cap(root / "shared" / "m2" / "p.md"), 50)

    def test_out_of_root_paths_answer_to_the_defaults(self):
        # No trust domain exists outside the tree — never the host's caps.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve() / "w"
            support.write(root / "_meta" / "limits.md", "*.md: 10\n")
            outside = root.parent / "elsewhere" / "p.md"
            caps = ws.caps_for_path(outside, root)
            self.assertEqual(ws.cap_for("p.md", caps), 15000)
            self.assertEqual(ws.cap_for("index.md", caps), 5000)


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

    def test_verdict_is_root_independent(self):
        # A nested space's own limits govern its files no matter which
        # root the check resolves from.
        support.write(self.root / "concepts" / "index.md",
                      "# C\n\n## Spaces\n")
        support.write(self.root / "concepts" / "_meta" / "limits.md",
                      "*.md: 30000\n")
        page = self.root / "concepts" / "roomy.md"
        support.write(page, "x" * 17000)
        for wiki in (self.root, self.root / "concepts"):
            r = support.run_ws("check-size", str(page), "--wiki", str(wiki))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_external_target_gets_a_note(self):
        target = str(self.root / "shared" / "team" / "new.md")
        r = support.run_ws("check-size", target, "--stdin",
                           "--wiki", str(self.root), stdin="x")
        self.assertEqual(r.returncode, 0)
        self.assertIn("target is external", r.stderr)

    def test_shrinking_write_toward_the_cap_is_progress(self):
        # CONVENTIONS: "Shrinking writes are always allowed." Over the cap
        # but below the file's current size, the verdict must not block.
        page = self.root / "page.md"
        support.write(page, "x" * 20000)
        r = support.run_ws("check-size", str(page), "--stdin",
                           "--wiki", str(self.root), stdin="x" * 18000)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("shrinking write is progress", r.stdout)
        # Growing, matching, or on-disk over-cap still refuses.
        r = support.run_ws("check-size", str(page), "--stdin",
                           "--wiki", str(self.root), stdin="x" * 20000)
        self.assertEqual(r.returncode, 1)
        r = support.run_ws("check-size", str(page), "--wiki", str(self.root))
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
