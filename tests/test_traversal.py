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

    def test_symlink_mount_ignores_never_inherit_the_hosts_list(self):
        # `_meta/ignore.md` rides the same nearest-file lookup as limits,
        # and the same fence: the host's list stops at the mount.
        support.write(self.root / "_meta" / "ignore.md", "assets\n")
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside).resolve() / "elsewhere"
            support.write(target / "index.md", "# E\n\n## Spaces\n")
            support.write(target / "assets" / "page.md", "# P\n")
            os.symlink(target, self.root / "mounted")
            index = self.root / "index.md"
            support.write(index, index.read_text(encoding="utf-8")
                          + "- [mounted/](mounted/index.md)\n")
            rels = self.rels(ws.walk_files(self.root, include_external=True))
            self.assertIn("mounted/assets/page.md", rels)
            self.assertNotIn("alpha/assets/deep.md", rels)  # host list holds

    def test_entry_through_a_symlinked_middle_segment_is_external(self):
        # A multi-segment href may group through plain folders; when a
        # middle segment is a symlink out of the tree, the child is
        # external — opt-in only, like every other mount.
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside).resolve() / "elsewhere"
            support.write(target / "sub" / "index.md", "# S\n\n## Spaces\n")
            os.symlink(target, self.root / "tunnel")
            index = self.root / "index.md"
            support.write(index, index.read_text(encoding="utf-8")
                          + "- [tunnel/sub/](tunnel/sub/index.md)\n")
            self.assertNotIn("tunnel/sub",
                             self.rels(ws.walk_spaces(self.root)))
            marked = {rel: ext for rel, _p, ext
                      in ws.walk_spaces(self.root, include_external=True)}
            self.assertTrue(marked.get("tunnel/sub"))

    def test_nested_shared_is_external_at_any_depth(self):
        # Every space is a wiki one level down — its `shared/` mounts get
        # the same fence the root's do.
        inner = self.root / "alpha" / "shared" / "inner"
        support.write(inner / "index.md", "# Inner\n\n## Spaces\n")
        support.write(inner / "notes.md", "# N\n\nnested-mount-needle\n")
        index = self.root / "alpha" / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "\n- [shared/inner/](shared/inner/index.md)\n")
        self.assertNotIn("alpha/shared/inner",
                         self.rels(ws.walk_spaces(self.root)))
        self.assertNotIn("alpha/shared/inner/notes.md",
                         self.rels(ws.walk_files(self.root)))
        marked = {rel: ext for rel, _p, ext
                  in ws.walk_spaces(self.root, include_external=True)}
        self.assertTrue(marked.get("alpha/shared/inner"))

    def test_owned_symlink_alias_dedupes_to_the_real_file(self):
        os.symlink(self.root / "notes.md", self.root / "alias.md")
        walked = ws.walk_files(self.root)
        by_rel = {rel: ext for rel, _p, ext in walked}
        self.assertIn("notes.md", by_rel)
        self.assertFalse(by_rel["notes.md"])
        self.assertNotIn("alias.md", by_rel)

    def test_deep_nesting_is_bounded_by_the_filesystem_not_the_stack(self):
        # "Trees nest without limit" must not die on the interpreter's
        # recursion limit; 1200 levels exceed it comfortably. Teardown
        # gets the same courtesy: shutil.rmtree recurses before 3.12, so
        # the chain is removed iteratively, deepest first.
        self.addCleanup(self._unlink_chain, self.root / "beta" / "gamma" / "c")
        cur = self.root / "beta" / "gamma"
        support.write(cur / "index.md",
                      "# Gamma\n\n## Spaces\n\n- [c/](c/index.md)\n")
        for _ in range(1200):
            cur = cur / "c"
            support.write(cur / "index.md",
                          "# L\n\n## Spaces\n\n- [c/](c/index.md)\n")
        walked = ws.walk_spaces(self.root)
        self.assertGreater(len(walked), 1200)
        files = ws.walk_files(self.root)
        self.assertGreater(len(files), 1200)

    @staticmethod
    def _unlink_chain(top):
        chain = []
        cur = top
        while cur.is_dir():
            chain.append(cur)
            cur = cur / "c"
        for p in reversed(chain):
            (p / "index.md").unlink()
            p.rmdir()

    def test_entry_through_a_space_boundary_is_not_followed(self):
        # beta/ is a space, so only beta lists gamma; a deep entry at the
        # root crosses beta's boundary and is declined.
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [beta/gamma/](beta/gamma/index.md)\n")
        support.write(self.root / "beta" / "index.md",
                      "# Beta\n\n## Spaces\n")   # gamma now unlisted there
        rels = self.rels(ws.walk_spaces(self.root))
        self.assertNotIn("beta/gamma", rels)

    def test_entry_through_plain_and_bare_dirs_is_followed(self):
        # Plain folders and bare-index dirs group transparently: a space
        # beneath them registers at the nearest real ancestor.
        support.write(self.root / "docs" / "index.md", "# Docs, bare\n")
        support.write(self.root / "docs" / "guides" / "index.md",
                      "# Guides\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [docs/guides/](docs/guides/index.md)\n")
        self.assertIn("docs/guides", self.rels(ws.walk_spaces(self.root)))

    def test_nested_repo_submodule_is_external_at_any_depth(self):
        # projects/app is an owned checkout; its .gitmodules declares a
        # submodule. A submodule names another repository by definition,
        # so the fence holds from the outer root exactly as it does when
        # app itself resolves — judged against the declaring repo's
        # .gitmodules, at every git boundary on the path.
        app = self.root / "projects" / "app"
        support.write(app / "index.md",
                      "# App\n\n## Spaces\n\n- [third/](third/index.md)\n")
        (app / ".git").mkdir(parents=True)
        support.write(app / ".gitmodules",
                      '[submodule "third"]\n\tpath = third\n'
                      '\turl = https://example.com/stranger/wiki.git\n')
        support.write(app / "third" / "index.md", "# S\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [projects/app/](projects/app/index.md)\n")
        rels = self.rels(ws.walk_spaces(self.root))
        self.assertIn("projects/app", rels)
        self.assertNotIn("projects/app/third", rels)
        marked = {rel: ext for rel, _p, ext
                  in ws.walk_spaces(self.root, include_external=True)}
        self.assertTrue(marked.get("projects/app/third"))
        self.assertFalse(marked.get("projects/app"))

    def test_own_origin_submodule_is_still_external(self):
        # Even a submodule whose URL names the declaring repo's own
        # origin mounts external: its content answers to that repository,
        # not to this wiki. An owned mount of your own second repo is a
        # clone, not a submodule — and a clone (a .git boundary with no
        # declaring .gitmodules entry) stays owned.
        app = self.root / "projects" / "app"
        support.write(app / "index.md",
                      "# App\n\n## Spaces\n\n- [wikimod/](wikimod/index.md)\n")
        (app / ".git").mkdir(parents=True)
        support.write(app / ".gitmodules",
                      '[submodule "wikimod"]\n\tpath = wikimod\n'
                      '\turl = https://github.com/me/app.git\n')
        support.write(app / "wikimod" / "index.md", "# W\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [projects/app/](projects/app/index.md)\n")
        marked = {rel: ext for rel, _p, ext
                  in ws.walk_spaces(self.root, include_external=True)}
        self.assertTrue(marked.get("projects/app/wikimod"))
        self.assertFalse(marked.get("projects/app"))   # the clone stays owned
        self.assertNotIn("projects/app/wikimod",
                         self.rels(ws.walk_spaces(self.root)))

    def test_dot_files_are_reserved_like_dot_dirs(self):
        support.write(self.root / ".draft.md", "# Hidden\n")
        self.assertNotIn(".draft.md", self.rels(ws.walk_files(self.root)))

    def test_percent_encoded_href_reaches_the_space(self):
        # An href is a CommonMark destination — Obsidian writes
        # `my%20space/index.md` for the `my space` folder, and the
        # contract must read what the dialect writes.
        support.write(self.root / "my space" / "index.md",
                      "# M\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [my space/](my%20space/index.md)\n")
        self.assertIn("my space", self.rels(ws.walk_spaces(self.root)))


class SubmoduleDeclarationTests(unittest.TestCase):
    """`.gitmodules` is the one input behind the submodule fence: every
    declared path is external, quoted values are unwrapped, and a repo
    without the file declares nothing."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name).resolve()
        self.addCleanup(self._td.cleanup)

    def test_declared_paths_are_read_quotes_unwrapped(self):
        repo = self.base / "repo"
        support.write(repo / ".gitmodules", (
            '[submodule "a"]\n\tpath = shared-notes\n\turl = x\n'
            '[submodule "b"]\n\tpath = "quoted name"\n\turl = y\n'))
        self.assertEqual(ws._submodule_paths(repo),
                         frozenset({"shared-notes", "quoted name"}))

    def test_no_gitmodules_declares_nothing(self):
        repo = self.base / "bare-repo"
        repo.mkdir()
        self.assertEqual(ws._submodule_paths(repo), frozenset())


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

    def test_explicit_near_miss_root_gets_the_rename_hint(self):
        support.write(self.neutral / "index.md", "# W\n\n## Spaces ##\n")
        r = support.run_ws("list", "--wiki", str(self.neutral),
                           cwd=self.neutral)
        self.assertEqual(r.returncode, 2)
        self.assertIn('carries "## Spaces ##" — rename it', r.stderr)


if __name__ == "__main__":
    unittest.main()
