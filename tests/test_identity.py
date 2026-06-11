"""The three bundled ws.py copies must stay byte-identical, the shared
core block each SKILL.md carries must match verbatim across the skills
(dedup by compression, not by sharing machinery), and every prose cap
table must state ws.py's defaults."""
import re
import unittest

import support

CORE_RE = re.compile(r"<!-- ws:core -->\n(.*?)<!-- /ws:core -->", re.DOTALL)


class IdentityTests(unittest.TestCase):
    def test_ws_copies_are_byte_identical(self):
        blobs = {p: p.read_bytes() for p in support.WS_COPIES}
        for p, blob in blobs.items():
            self.assertTrue(blob, f"{p} is empty or missing")
        reference = blobs[support.WS_COPIES[0]]
        for p, blob in blobs.items():
            self.assertEqual(
                blob, reference,
                f"{p} differs from {support.WS_COPIES[0]} — re-copy ws.py "
                "into every skill's scripts/",
            )

    def test_skill_core_blocks_match_verbatim(self):
        cores = {}
        for p in support.SKILLS:
            text = p.read_text(encoding="utf-8")
            m = CORE_RE.search(text)
            self.assertIsNotNone(m, f"{p} lacks the ws:core block markers")
            cores[p] = m.group(1)
        reference = cores[support.SKILLS[0]]
        for p, core in cores.items():
            self.assertEqual(
                core, reference,
                f"{p} core block drifted — the shared block is duplicated "
                "verbatim; edit all three together",
            )

    def test_prose_cap_tables_state_the_code_defaults(self):
        """The caps live in code once (ws.DEFAULT_CAPS) but are restated in
        the skill core block, AGENTS.md, and CONVENTIONS.md — pin the prose
        to the code so the numbers can't drift apart silently."""
        ws = support.load_ws()
        i, l, m = (ws.DEFAULT_CAPS["index.md"], ws.DEFAULT_CAPS["log.md"],
                   ws.DEFAULT_MD_CAP)
        self.assertEqual(ws.DEFAULT_CAPS["hot.md"], l)
        core = CORE_RE.search(
            support.SKILLS[0].read_text(encoding="utf-8")).group(1)
        expectations = [
            (support.SKILLS[0], core, [
                f"keyed by basename: `index.md` {i}, `log.md` and "
                f"`hot.md` {l}, any other `*.md` {m}",
            ]),
            (support.REPO / "AGENTS.md", None, [
                f"- `index.md`: {i:,} bytes",
                f"- `log.md`: {l:,} bytes",
                f"- `hot.md`: {l:,} bytes",
                f"- Any other `*.md` file: {m:,} bytes",
            ]),
            (support.REPO / "CONVENTIONS.md", None, [
                f"| `index.md` | {i} |",
                f"| `log.md` | {l} |",
                f"| `hot.md` | {l} |",
                f"| `*.md` | {m} |",
            ]),
        ]
        for path, text, needles in expectations:
            haystack = text if text is not None \
                else path.read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, haystack,
                              f"{path} drifted from ws.py's cap defaults")


if __name__ == "__main__":
    unittest.main()
