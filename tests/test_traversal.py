"""Traversal contract: walk_spaces / walk_files semantics, trust scope,
cycle guards."""
import os
import sys
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

    def test_symlink_out_of_the_tree_answers_to_its_placement(self):
        # A target outside the tree has no position to judge, so the link
        # is owned or external by where it sits, like a clone: outside
        # `shared/` (a folder's own space mounted from the wiki) it
        # is owned and walked by default; under `shared/` it is external.
        with tempfile.TemporaryDirectory() as outside:
            mine = Path(outside) / "mine"
            theirs = Path(outside) / "theirs"
            for t in (mine, theirs):
                support.write(t / "index.md", "# T\n\n## Spaces\n")
            os.symlink(mine, self.root / "mounted")
            os.symlink(theirs, self.root / "shared" / "mounted")
            index = self.root / "index.md"
            text = index.read_text(encoding="utf-8")
            support.write(index, text + "- [mounted/](mounted/index.md)\n"
                          "- [shared/mounted/](shared/mounted/index.md)\n")
            default = self.rels(ws.walk_spaces(self.root))
            self.assertIn("mounted", default)
            self.assertNotIn("shared/mounted", default)
            marked = {rel: ext for rel, _p, ext
                      in ws.walk_spaces(self.root, include_external=True)}
            self.assertFalse(marked["mounted"])
            self.assertTrue(marked["shared/mounted"])

    def test_symlink_mount_ignores_follow_the_fence(self):
        # `_meta/ignore.md` rides the same nearest-file lookup as limits,
        # and the same fence: the host's list reaches an owned mount and
        # stops at an external one, which answers to its own (here none).
        support.write(self.root / "_meta" / "ignore.md", "assets\n")
        with tempfile.TemporaryDirectory() as outside:
            mine = Path(outside).resolve() / "mine"
            theirs = Path(outside).resolve() / "theirs"
            for t in (mine, theirs):
                support.write(t / "index.md", "# T\n\n## Spaces\n")
                support.write(t / "assets" / "page.md", "# P\n")
            os.symlink(mine, self.root / "mounted")
            os.symlink(theirs, self.root / "shared" / "mounted")
            index = self.root / "index.md"
            support.write(index, index.read_text(encoding="utf-8")
                          + "- [mounted/](mounted/index.md)\n"
                          + "- [shared/mounted/](shared/mounted/index.md)\n")
            rels = self.rels(ws.walk_files(self.root, include_external=True))
            self.assertNotIn("mounted/assets/page.md", rels)   # owned: silenced
            self.assertIn("shared/mounted/assets/page.md", rels)
            self.assertNotIn("alpha/assets/deep.md", rels)     # host list holds

    def test_entry_through_a_symlinked_middle_segment_follows_placement(self):
        # A multi-segment href may group through plain folders; a middle
        # segment that is a symlink out of the tree is judged by where it
        # sits, like every mount: owned outside `shared/`, external under.
        with tempfile.TemporaryDirectory() as outside:
            mine = Path(outside).resolve() / "mine"
            theirs = Path(outside).resolve() / "theirs"
            for t in (mine, theirs):
                support.write(t / "sub" / "index.md", "# S\n\n## Spaces\n")
            os.symlink(mine, self.root / "tunnel")
            os.symlink(theirs, self.root / "shared" / "tunnel")
            index = self.root / "index.md"
            support.write(index, index.read_text(encoding="utf-8")
                          + "- [tunnel/sub/](tunnel/sub/index.md)\n"
                          + "- [shared/tunnel/sub/]"
                          "(shared/tunnel/sub/index.md)\n")
            default = self.rels(ws.walk_spaces(self.root))
            self.assertIn("tunnel/sub", default)
            self.assertNotIn("shared/tunnel/sub", default)
            marked = {rel: ext for rel, _p, ext
                      in ws.walk_spaces(self.root, include_external=True)}
            self.assertFalse(marked["tunnel/sub"])
            self.assertTrue(marked["shared/tunnel/sub"])

    def test_alias_into_external_scope_stays_external(self):
        # A symlink whose realpath stays inside the tree answers to the
        # target's own position: an alias into `shared/` cannot lift the
        # fence, wherever it is placed.
        os.symlink(self.root / "shared" / "team", self.root / "alias")
        self.assertEqual(ws.external_reason(self.root / "alias", self.root),
                         "symlink into external scope")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [alias/](alias/index.md)\n")
        self.assertNotIn("alias", self.rels(ws.walk_spaces(self.root)))

    def test_alias_into_a_symlink_mounted_shared_space_stays_external(self):
        # The position a link names is judged as spelled: an alias into
        # `shared/` cannot lift the fence even when the `shared/` mount is
        # itself a link to a folder outside the tree — by realpath alone
        # the alias would be an outside target and placement would own it.
        with tempfile.TemporaryDirectory() as outside:
            theirs = Path(outside).resolve() / "theirs"
            support.write(theirs / "index.md", "# T\n\n## Spaces\n")
            os.symlink(theirs, self.root / "shared" / "mnt")
            os.symlink(self.root / "shared" / "mnt", self.root / "alias")
            self.assertEqual(
                ws.external_reason(self.root / "alias", self.root),
                "symlink into external scope")
            index = self.root / "index.md"
            support.write(index, index.read_text(encoding="utf-8")
                          + "- [alias/](alias/index.md)\n")
            self.assertNotIn("alias", self.rels(ws.walk_spaces(self.root)))

    def test_symlink_loops_are_inert(self):
        # A link that resolves through itself has nothing on disk behind
        # it. Python 3.13+ resolves a loop "as far as possible" instead of
        # raising, so scope must answer without recursing — a link asking
        # about itself reads its parent's state — and the walk names the
        # entry stale, on every interpreter the CI matrix promises.
        os.symlink(self.root / "b", self.root / "a")
        os.symlink(self.root / "a", self.root / "b")
        os.symlink(self.root / "c" / "sub", self.root / "c")
        for name in ("a", "c"):
            self.assertIsNone(ws.external_reason(self.root / name, self.root))
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [a/](a/index.md)\n- [c/](c/index.md)\n")
        notes = ws.WalkNotes()
        rels = self.rels(ws.walk_spaces(self.root, notes=notes))
        self.assertNotIn("a", rels)
        self.assertNotIn("c", rels)
        self.assertLessEqual({"a/", "c/"}, set(notes.stale))

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
        # recursion limit — but raw depth cannot prove that portably:
        # macOS caps a path at 1024 bytes where Linux allows 4096. So
        # the chain stays inside every POSIX path limit and the walks
        # run under a recursion limit clamped to the live stack plus
        # headroom: a walker that recursed per level would die long
        # before the bottom; the iterative walkers reach it. Teardown
        # gets the same courtesy: shutil.rmtree recurses before 3.12, so
        # the chain is removed iteratively, deepest first.
        depth = 400
        self.addCleanup(self._unlink_chain, self.root / "beta" / "gamma" / "c")
        cur = self.root / "beta" / "gamma"
        support.write(cur / "index.md",
                      "# Gamma\n\n## Spaces\n\n- [c/](c/index.md)\n")
        for _ in range(depth):
            cur = cur / "c"
            support.write(cur / "index.md",
                          "# L\n\n## Spaces\n\n- [c/](c/index.md)\n")
        live = 0
        frame = sys._getframe()
        while frame is not None:
            live += 1
            frame = frame.f_back
        clamp = live + 80
        self.assertLess(clamp, depth,
                        "premise: per-level recursion must overflow")
        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(clamp)
        try:
            walked = ws.walk_spaces(self.root)
            files = ws.walk_files(self.root)
        finally:
            sys.setrecursionlimit(limit)
        deepest = "beta/gamma" + "/c" * depth
        self.assertIn(deepest, self.rels(walked))
        self.assertGreater(len(files), depth)

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

    def test_own_space_resolves_from_inside_the_folder(self):
        # A folder's own space is a dot-folder at its root: the wiki every
        # command resolves from anywhere inside the folder, ahead of the
        # configured wiki — the CWD rule, one more candidate per ancestor.
        folder = self.base / "folder"
        space = folder / ws.OWN_SPACE
        support.write(space / "index.md", "# F\n\n## Spaces\n")
        (folder / "src" / "deep").mkdir(parents=True)
        env = self.config_env(self.wiki)
        for cwd in (folder / "src" / "deep", folder, space):
            r = support.run_ws("list", cwd=cwd, env_extra=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"wiki: {space}", r.stderr, cwd)

    def test_a_folder_that_is_a_wiki_beats_its_own_space(self):
        # The ancestor's own contract wins over the dot-folder it carries
        # — a repo-root wiki stays one.
        folder = self.base / "folder"
        support.write(folder / "index.md", "# F\n\n## Spaces\n")
        support.write(folder / ws.OWN_SPACE / "index.md",
                      "# Inner\n\n## Spaces\n")
        (folder / "src").mkdir()
        r = support.run_ws("list", cwd=folder / "src")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"wiki: {folder}\n", r.stderr)

    def test_own_space_link_resolves_to_the_space_it_names(self):
        # The other shape: the space lives in the wiki and the folder's
        # dot-folder is a link to it. The root is the space itself — one
        # announcement from anywhere inside the folder — and the
        # enclosing-wiki advisory names the wiki around it.
        folder = self.base / "folder"
        space = self.wiki / "alpha" / "held"
        support.write(space / "index.md", "# Held\n\n## Spaces\n")
        (folder / "src").mkdir(parents=True)
        os.symlink(space, folder / ws.OWN_SPACE)
        for cwd in (folder / "src", folder / ws.OWN_SPACE):
            r = support.run_ws("list", cwd=cwd)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"wiki: {space}\n", r.stderr, cwd)
            self.assertIn(f"nested inside a wiki ({self.wiki / 'alpha'})",
                          r.stderr, cwd)

    def test_explicit_path_to_a_folder_answers_as_its_own_space(self):
        # --wiki names the folder, not the dot-folder inside it: a folder
        # answers by its own contract, else by the space it carries — the
        # rule the CWD walk applies, applied to the explicit path too.
        folder = self.base / "folder"
        space = folder / ws.OWN_SPACE
        support.write(space / "index.md", "# F\n\n## Spaces\n")
        r = support.run_ws("list", "--wiki", str(folder), cwd=self.neutral)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"wiki: {space}\n", r.stderr)

    def test_a_broken_own_space_is_named_before_the_fall_through(self):
        # A dot-folder on disk that is not a wiki — a link gone stale after
        # the space it named moved, an index without the heading — is
        # noted, and resolution goes on to the next candidate: the folder
        # meant to keep a space, so it never loses it in silence.
        env = self.config_env(self.wiki)
        cases = {
            "dangling": lambda own: os.symlink(self.base / "gone", own),
            "unheaded": lambda own: support.write(own / "index.md", "# F\n"),
        }
        for case, make in cases.items():
            with self.subTest(case=case):
                folder = self.base / case
                (folder / "src").mkdir(parents=True)
                own = folder / ws.OWN_SPACE
                make(own)
                r = support.run_ws("list", cwd=folder / "src", env_extra=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"wiki: {self.wiki}\n", r.stderr)
                self.assertIn(f"note: {own} is not a wiki (", r.stderr)
                r = support.run_ws("list", "--wiki", str(folder),
                                   cwd=self.neutral)
                self.assertEqual(r.returncode, 2)
                self.assertIn(f"note: {own} is not a wiki (", r.stderr)
                self.assertIn(f"not a wiki: {folder}", r.stderr)

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
