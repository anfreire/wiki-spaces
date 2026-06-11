"""Traversal contract: walk_spaces / walk_files semantics, trust scope,
cycle guards."""
import os
import tempfile
import unittest
from pathlib import Path

import support

ws = support.load_ws()


class TraversalTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        support.build_demo(self.root)
        self.addCleanup(self._td.cleanup)

    def rels(self, walked):
        return [rel for rel, _path, _ext in walked]

    def test_list_follows_the_contract_only(self):
        rels = self.rels(ws.walk_spaces(self.root))
        self.assertEqual(rels, [".", "alpha", "beta", "beta/gamma"])
        self.assertNotIn("unregistered", rels)  # on disk but unlisted
        self.assertNotIn("bare", rels)          # listed nowhere, no heading

    def test_external_requires_opt_in_and_is_marked(self):
        rels = self.rels(ws.walk_spaces(self.root))
        self.assertNotIn("shared/team", rels)
        walked = ws.walk_spaces(self.root, include_external=True)
        marked = {rel: ext for rel, _path, ext in walked}
        self.assertIn("shared/team", marked)
        self.assertTrue(marked["shared/team"])
        self.assertFalse(marked["alpha"])

    def test_files_stop_at_space_boundaries_and_descend_plain_folders(self):
        rels = self.rels(ws.walk_files(self.root))
        self.assertIn("alpha/assets/deep.md", rels)       # plain folder
        self.assertIn("beta/gamma/index.md", rels)        # via gamma's walk
        self.assertNotIn("unregistered/index.md", rels)   # not contract-reachable
        self.assertNotIn("_meta/limits.md", rels)         # reserved
        before_gamma = rels.index("beta/index.md")
        self.assertLess(before_gamma, rels.index("beta/gamma/index.md"))

    def test_malformed_href_is_skipped_not_fatal(self):
        index = self.root / "index.md"
        text = index.read_text(encoding="utf-8")
        support.write(index, text + "- [bad{}](bad{}/index.md)\n- [up](../up)\n")
        rels = self.rels(ws.walk_spaces(self.root))
        self.assertEqual(rels, [".", "alpha", "beta", "beta/gamma"])

    def test_registered_bare_child_is_not_a_space(self):
        index = self.root / "index.md"
        text = index.read_text(encoding="utf-8")
        support.write(index, text + "- [bare/](bare/index.md)\n")
        self.assertNotIn("bare", self.rels(ws.walk_spaces(self.root)))

    def test_symlink_cycle_terminates(self):
        loop = self.root / "alpha" / "loop"
        os.symlink(self.root / "alpha", loop)
        index = self.root / "alpha" / "index.md"
        text = index.read_text(encoding="utf-8")
        support.write(index, text + "\n- [loop/](loop/index.md)\n")
        rels = self.rels(ws.walk_spaces(self.root))
        self.assertEqual(rels, [".", "alpha", "beta", "beta/gamma"])

    def test_escaping_symlink_is_external(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "elsewhere"
            support.write(target / "index.md", "# E\n\n## Spaces\n")
            os.symlink(target, self.root / "mounted")
            index = self.root / "index.md"
            text = index.read_text(encoding="utf-8")
            support.write(index, text + "- [mounted/](mounted/index.md)\n")
            self.assertNotIn("mounted", self.rels(ws.walk_spaces(self.root)))
            rels = self.rels(ws.walk_spaces(self.root, include_external=True))
            self.assertIn("mounted", rels)

    def test_owned_symlink_alias_dedupes_to_the_real_file(self):
        os.symlink(self.root / "notes.md", self.root / "alias.md")
        walked = ws.walk_files(self.root)
        by_rel = {rel: ext for rel, _p, ext in walked}
        self.assertIn("notes.md", by_rel)
        self.assertFalse(by_rel["notes.md"])
        self.assertNotIn("alias.md", by_rel)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name).resolve()
        self.wiki = self.base / "wiki"
        support.build_demo(self.wiki)
        self.neutral = self.base / "neutral"
        self.neutral.mkdir()
        self.addCleanup(self._td.cleanup)

    def config_env(self, wiki_path):
        cfg = self.base / "cfg"
        support.write(cfg / "wiki-spaces" / "config",
                      f"# wiki-spaces config\nwiki = {wiki_path}\n")
        return {"XDG_CONFIG_HOME": str(cfg)}

    def test_explicit_wiki_flag_wins(self):
        r = support.run_ws("list", "--wiki", str(self.wiki), cwd=self.neutral)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines()[0], ".")

    def test_nearest_cwd_ancestor_resolves(self):
        r = support.run_ws("list", cwd=self.wiki / "alpha" / "assets")
        self.assertEqual(r.returncode, 0)
        # Nearest ancestor wiki is alpha itself, not the demo root.
        self.assertEqual(r.stdout.splitlines(), ["."])

    def test_cwd_beats_config(self):
        other = self.base / "other"
        support.build_demo(other)
        support.write(other / "only-in-other.md", "# Marker\n")
        env = self.config_env(other)
        r = support.run_ws("files", cwd=self.wiki, env_extra=env)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("only-in-other.md", r.stdout)  # CWD wiki won
        r2 = support.run_ws("files", cwd=self.neutral, env_extra=env)
        self.assertEqual(r2.returncode, 0)
        self.assertIn("only-in-other.md", r2.stdout)    # config fallback

    def test_config_is_the_fallback(self):
        env = self.config_env(self.wiki)
        r = support.run_ws("list", cwd=self.neutral, env_extra=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("beta/gamma", r.stdout.splitlines())

    def test_nothing_resolves_exits_2(self):
        r = support.run_ws("list", cwd=self.neutral)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no wiki found", r.stderr)

    def test_broken_config_is_surfaced_not_silent(self):
        env = self.config_env(self.base / "gone")
        r = support.run_ws("list", cwd=self.neutral, env_extra=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("config `wiki` ignored", r.stderr)
        self.assertIn("missing on disk", r.stderr)
        env = self.config_env("relative/path")
        r = support.run_ws("list", cwd=self.neutral, env_extra=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not absolute", r.stderr)

    def test_explicit_non_wiki_exits_2(self):
        r = support.run_ws("list", "--wiki", str(self.neutral), cwd=self.neutral)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a wiki", r.stderr)


if __name__ == "__main__":
    unittest.main()
