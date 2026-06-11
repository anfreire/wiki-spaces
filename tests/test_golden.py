"""Golden outputs: the demo fixture's list/files/audit output is pinned
byte-for-byte (the audit's absolute `wiki:` header line excluded). A diff
here means the traversal contract or report format changed — make sure
that's on purpose, then regenerate:

    python3 tests/test_golden.py --regenerate
"""
import sys
import tempfile
import unittest
from pathlib import Path

import support

GOLDEN = Path(__file__).resolve().parent / "golden"

CASES = {
    "list.txt": ("list",),
    "list_external.txt": ("list", "--external"),
    "files.txt": ("files",),
    "files_external.txt": ("files", "--external"),
    "audit.txt": ("audit",),
}


def capture(case_args, root):
    r = support.run_ws(*case_args, "--wiki", str(root))
    out = r.stdout
    if case_args[0] == "audit":
        lines = out.splitlines()
        self_check = lines and lines[0].startswith("wiki: ")
        assert self_check, f"unexpected audit header: {lines[:1]}"
        out = "\n".join(lines[1:]) + "\n"
    return out, r.returncode


class GoldenTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        support.build_demo(self.root)
        self.addCleanup(self._td.cleanup)

    def test_golden(self):
        for name, args in CASES.items():
            with self.subTest(case=name):
                expected = (GOLDEN / name).read_text(encoding="utf-8")
                actual, code = capture(args, self.root)
                self.assertEqual(actual, expected)
                self.assertEqual(code, 1 if name == "audit.txt" else 0)


def regenerate():
    GOLDEN.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        support.build_demo(root)
        for name, args in CASES.items():
            out, _code = capture(args, root)
            (GOLDEN / name).write_text(out, encoding="utf-8")
            print(f"wrote {GOLDEN / name}")


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        regenerate()
    else:
        unittest.main()
