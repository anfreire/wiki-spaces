"""Unit tests for ws.py primitives: section parsing, href normalization,
cap tables, and the check-size verdict."""
import os
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

    def test_h1_closes_the_section_h3_stays_inside(self):
        # The dialect ends a section at the next heading of the same or
        # a higher level; a deeper ### grouping stays inside the body.
        lines = ("## Spaces\n\n- x\n\n### group\n\n- y\n\n"
                 "# Appendix\n- z\n").splitlines()
        self.assertEqual(ws.find_section(lines), (0, 1, 8))

    def test_fence_closes_only_on_same_char_and_length(self):
        lines = "````\n```\nstill fenced\n````\nout\n".splitlines()
        self.assertEqual(ws.fenced_mask(lines), [True, True, True, True, False])

    def test_parse_spaces_entries_and_malformed(self):
        # An entry rides any markdown bullet marker; a trailing quoted
        # link title is ignored; any other bullet-shaped line is
        # malformed, whatever its marker.
        text = (
            "## Spaces\n"
            "\n"
            "- [a/](a/index.md) — desc\n"
            "- [b/](b/index.md)   \n"
            "- [c/](c/index.md) – en-dash desc\n"
            "- [d/](d/index.md) - hyphen desc\n"
            "* [e/](e/index.md) — star bullet\n"
            "+ [f/](f/index.md)\n"
            '- [g/](g/index.md "G title") — titled\n'
            "- [my space/](my space/index.md)\n"
            "- [[wikilink-form]]\n"
            "- plain bullet\n"
            "* stray star\n"
            "prose line is fine\n"
        )
        entries, malformed = ws.parse_spaces(text)
        self.assertEqual([h for _l, h, _d in entries],
                         ["a/index.md", "b/index.md", "c/index.md",
                          "d/index.md", "e/index.md", "f/index.md",
                          "g/index.md", "my space/index.md"])
        # The description tail rides whichever dash the writer used and
        # is "" when absent — every consumer sees one entry shape.
        self.assertEqual([d for _l, _h, d in entries],
                         ["desc", "", "en-dash desc", "hyphen desc",
                          "star bullet", "", "titled", ""])
        self.assertEqual(malformed, ["- [[wikilink-form]]", "- plain bullet",
                                     "* stray star"])

    def test_heading_inside_frontmatter_is_not_the_contract(self):
        # A flush-left `## Spaces` line inside frontmatter is legal YAML
        # (a comment) — metadata, never contract. Every reader agrees on
        # where the body starts.
        self.assertFalse(ws.has_spaces("---\ntitle: x\n## Spaces\n---\nb\n"))
        self.assertTrue(ws.has_spaces("---\ntitle: x\n---\n\n## Spaces\n"))

    def test_near_miss_headings_are_detected_as_a_class(self):
        for text in ("# T\n\n## spaces\n", "# T\n\n## SPACES\n",
                     "# T\n\n## Spaces ##\n", "# T\n\n##Spaces\n",
                     "# T\n\n  ## spaces\n"):
            self.assertIsNotNone(ws._heading_near_miss(text), text)
        self.assertIsNone(ws._heading_near_miss("# T\n\n## Spaces\n"))
        self.assertIsNone(ws._heading_near_miss("# T\n\n  ## Spaces\n"))
        self.assertIsNone(ws._heading_near_miss("# T\n\n## Spacesuits\n"))

    def test_an_indented_heading_is_the_contract(self):
        # Up to 3 leading spaces still renders as a heading in the
        # dialect, so the file visibly shows ## Spaces — it must not
        # read as bare (the repair hint — add the heading — would have
        # created a visible duplicate). At 4 spaces it is a code block.
        self.assertTrue(ws.has_spaces("# T\n\n  ## Spaces\n"))
        self.assertFalse(ws.has_spaces("# T\n\n    ## Spaces\n"))
        self.assertEqual(ws.extra_spaces_headings(
            "## Spaces\n- a\n\n   ## Spaces\n- b\n"), 1)

    def test_extra_spaces_headings_are_counted(self):
        self.assertEqual(ws.extra_spaces_headings(
            "## Spaces\n- a\n\n## Spaces\n- b\n"), 1)
        self.assertEqual(ws.extra_spaces_headings("## Spaces\n"), 0)
        self.assertEqual(ws.extra_spaces_headings(
            "## Spaces\n```\n## Spaces\n```\n"), 0)

    def test_read_text_strips_a_bom(self):
        # A BOM is byte-order metadata, not content: with it stripped on
        # decode, a BOM'd index still reads frontmatter-then-contract.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "index.md"
            p.write_bytes(b"\xef\xbb\xbf---\ntitle: x\n---\n\n## Spaces\n")
            text = ws.read_text(p)
            self.assertFalse(text.startswith("\ufeff"))
            self.assertTrue(ws.has_spaces(text))
            # A contract-lookalike inside frontmatter stays metadata.
            p.write_bytes(b"\xef\xbb\xbf---\nt: x\n## Spaces\n---\nb\n")
            self.assertFalse(ws.has_spaces(ws.read_text(p)))


class HrefTests(unittest.TestCase):
    def test_normalizes_index_md_and_slashes(self):
        self.assertEqual(ws.normalize_href("a/index.md"), "a")
        self.assertEqual(ws.normalize_href("a/"), "a")
        self.assertEqual(ws.normalize_href("nested/b/index.md"), "nested/b")

    def test_rejects_unregistrable_shapes(self):
        for href in ("", "/abs", "../up", "_meta/x", ".hidden/y",
                     "self/..", ".", "./"):
            self.assertIsNone(ws.normalize_href(href), href)

    def test_a_colon_marks_a_uri_never_a_wiki_path(self):
        # URL and scheme hrefs once fell through to the stale channel,
        # whose named repair — remove the entry — would delete a
        # bookmark line. Obsidian forbids `:` in names, so no real
        # folder loses its entry to this rule.
        for href in ("https://example.com", "mailto:a@b.c",
                     "obsidian://open?vault=x", "a:b/index.md"):
            self.assertIsNone(ws.normalize_href(href), href)

    def test_a_raw_hash_marks_a_fragment_never_a_name(self):
        # `- [jump](#top)` is an anchor, `beta/index.md#top` a fragment
        # form — neither names a space, and Obsidian forbids `#` in
        # names. A name's `#` rides percent-encoded and still registers.
        for href in ("#top", "a#b", "beta/index.md#top"):
            self.assertIsNone(ws.normalize_href(href), href)
        self.assertEqual(ws.normalize_href("a%23b/"), "a#b")

    def test_trivial_equivalents_normalize(self):
        # `./x`, doubled or dotted separators, and a trailing slash are
        # the same path — normalized on read, never findings.
        self.assertEqual(ws.normalize_href("./x/index.md"), "x")
        self.assertEqual(ws.normalize_href("a//b/"), "a/b")
        self.assertEqual(ws.normalize_href("a/./b"), "a/b")
        self.assertEqual(ws.normalize_href("a/../b"), "b")

    def test_percent_encoding_decodes_to_the_disk_name(self):
        # Obsidian writes `my%20space/index.md` for the `my space` folder;
        # every check runs on the decoded name the filesystem knows.
        self.assertEqual(ws.normalize_href("my%20space/index.md"), "my space")
        self.assertEqual(ws.normalize_href("a%23b/"), "a#b")
        self.assertEqual(ws.normalize_href("notes%20%282024%29/index.md"),
                         "notes (2024)")
        self.assertEqual(ws.normalize_href("a%5Bb/"), "a[b")
        self.assertEqual(ws.normalize_href("a{b}"), "a{b}")
        # Decoding happens before validation — nothing smuggles through.
        for href in ("%2E%2E/up", "%2Fabs", "_meta%2Fx"):
            self.assertIsNone(ws.normalize_href(href), href)

    def test_encode_href_round_trips_through_the_parser(self):
        # Any folder name survives a contract entry: what would break the
        # grammar rides percent-encoded.
        for name in ("plain", "my space", "a#b", "50% off", "a%20b",
                     "notes (2024)", "a[b", "a]b", "x{y}"):
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

    def test_symlink_mount_caps_never_inherit_the_hosts_limits(self):
        # The third externality rule gets the same fence the other two
        # do: a symlink-mounted space answers to its own limits or the
        # defaults, never the host's.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            root = base / "host"
            support.write(root / "index.md", "# H\n\n## Spaces\n")
            support.write(root / "_meta" / "limits.md", "*.md: 90000\n")
            support.write(base / "m1" / "index.md", "# M1\n\n## Spaces\n")
            support.write(base / "m2" / "index.md", "# M2\n\n## Spaces\n")
            support.write(base / "m2" / "_meta" / "limits.md", "*.md: 50\n")
            os.symlink(base / "m1", root / "mnt")
            os.symlink(base / "m2", root / "mnt2")

            def cap(p):
                return ws.cap_for("p.md", ws.caps_for_path(p, root))

            self.assertEqual(cap(root / "p.md"), 90000)
            self.assertEqual(cap(root / "mnt" / "p.md"), 15000)
            self.assertEqual(cap(root / "mnt2" / "p.md"), 50)


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

    def test_verdicts_are_data_not_coaching(self):
        # The refusal states the invariant and nothing more — what an
        # overflow calls for is the skills' voice, not the tool's, so
        # wording can improve without touching three script copies.
        log = self.root / "log.md"
        support.write(log, "x" * 100001)
        r = support.run_ws("check-size", str(log), "--wiki", str(self.root))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(),
                         "over log.md: 100001 > 100000 bytes — never truncate")

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

    def test_directory_target_cannot_operate(self):
        # A directory is never a write target — both arms refuse alike.
        # The --stdin arm once fell through and statted the directory as
        # the "current size", feeding a nonsense shrinking-write verdict.
        d = self.root / "pages.md"       # a directory can wear an .md name
        d.mkdir()
        for args in ((), ("--stdin",)):
            with self.subTest(args=args):
                r = support.run_ws("check-size", str(d), *args,
                                   "--wiki", str(self.root), stdin="x")
                self.assertEqual(r.returncode, 2)
                self.assertIn("directory", r.stderr)

    def test_relative_target_resolves_from_the_wiki_root(self):
        # A relative target is a wiki path, wherever the caller stands —
        # resolving from CWD would misjudge externality and existence.
        support.write(self.root / "page.md", "x" * 20)
        r = support.run_ws("check-size", "page.md", "--wiki", str(self.root),
                           cwd=self.root.parent)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok page.md", r.stdout)
        self.assertNotIn("target is external", r.stderr)

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

    def test_symlink_mounted_verdict_is_root_independent(self):
        # A file inside a symlink mount is external however it is
        # reached: the mount's caps govern (not the host's), the note
        # fires, and the verdict matches the mount resolved as its own
        # root.
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside).resolve() / "elsewhere"
            support.write(target / "index.md", "# E\n\n## Spaces\n")
            support.write(target / "roomy.md", "x" * 17000)
            os.symlink(target, self.root / "mnt")
            r = support.run_ws("check-size", "mnt/roomy.md",
                               "--wiki", str(self.root))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("target is external", r.stderr)
            r = support.run_ws("check-size", "roomy.md",
                               "--wiki", str(target))
            self.assertEqual(r.returncode, 1)

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
