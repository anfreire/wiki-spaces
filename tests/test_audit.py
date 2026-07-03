"""Audit findings: drift, contract violations (including entries crossing
a space boundary), broken wikilinks, over-cap files, orphan reporting —
and the read-only guarantee: no command writes to a wiki."""
import os
import tempfile
import unittest
from pathlib import Path

import support

ws = support.load_ws()


class AuditTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name).resolve()
        support.build_demo(self.root)
        self.addCleanup(self._td.cleanup)

    def audit(self, *args):
        return support.run_ws("audit", "--wiki", str(self.root), *args)

    def test_findings_and_exit_code(self):
        r = self.audit()
        self.assertEqual(r.returncode, 1)
        out = r.stdout
        # Unregistered + bare = undeclared: a promotion decision, reported.
        self.assertIn("contract bare/index.md: no ## Spaces heading (not a "
                      "space until it carries ## Spaces", out)
        self.assertNotIn("missing entry for bare/", out)
        # Registered + bare = half-declared: the heading is the named repair.
        self.assertIn("contract halfway/index.md: no ## Spaces heading "
                      "(registered — add the ## Spaces heading to complete "
                      "it)", out)
        self.assertIn("drift index.md: missing entry for unregistered/ — "
                      "add: - [unregistered/](unregistered/index.md)", out)
        self.assertIn("drift index.md: stale entry missing/", out)
        self.assertIn("broken notes.md: [[nope]]", out)
        self.assertIn("over-cap big.md:", out)
        self.assertIn("over-cap tiny.md:", out)

    def test_orphans_are_informational(self):
        support.write(self.root / "missing" / "index.md", "# M\n\n## Spaces\n")
        support.write(self.root / "bare" / "index.md", "# B\n\n## Spaces\n")
        support.write(self.root / "halfway" / "index.md",
                      "# H\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [bare/](bare/index.md)\n"
                      + "- [unregistered/](unregistered/index.md)\n")
        (self.root / "big.md").unlink()
        (self.root / "tiny.md").unlink()
        notes = self.root / "notes.md"
        support.write(notes, "# Notes\n\nSee [[alpha-notes]].\n")
        r = self.audit()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("orphan.md", r.stdout)   # still reported
        self.assertIn("ok", r.stdout.splitlines()[-1])

    def test_embeds_and_code_spans_are_exempt(self):
        out = self.audit().stdout
        self.assertNotIn("photo", out)
        self.assertNotIn("fenced-ghost", out)
        self.assertNotIn("span-ghost", out)

    def test_spaces_section_is_contract_not_content(self):
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [[ghost-entry]]\n")
        out = self.audit().stdout
        self.assertIn("contract index.md: malformed entry: - [[ghost-entry]]",
                      out)
        self.assertNotIn("broken index.md", out)

    def test_markdown_links_count_as_incoming(self):
        # orphan.md gains one markdown link from notes.md and stops being
        # an orphan; wikilinks are not the only citation form.
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nAlso [the orphan](orphan.md).\n")
        self.assertNotIn("orphan.md", self.audit().stdout)

    def test_boundary_crossing_entry_is_reported(self):
        # The walk declines an entry through another space's boundary; the
        # audit judges entries through the same rule and names the repair.
        # This exact shape was once manufactured by an automated repair
        # and blessed as "ok" — it must never be invisible again.
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [beta/gamma/](beta/gamma/index.md)\n")
        r = self.audit()
        self.assertEqual(r.returncode, 1)
        self.assertIn("contract index.md: entry beta/gamma/ crosses the "
                      "boundary of beta/ — remove it; beta/index.md owns "
                      "the deeper listing", r.stdout)

    def test_adoption_repairs_converge_in_any_order(self):
        # Registered-bare dir over a valid deep space: the adoption shape.
        # Applying BOTH round-1 findings at once (the worst order) leaves
        # a state round 2 names precisely; following it converges.
        support.write(self.root / "docs" / "index.md", "# Docs\n")
        support.write(self.root / "docs" / "guide" / "index.md",
                      "# Guide\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [docs/](docs/index.md)\n")
        one = self.audit().stdout
        self.assertIn("contract docs/index.md: no ## Spaces heading "
                      "(registered", one)
        self.assertIn("drift index.md: missing entry for docs/guide/ — "
                      "add: - [docs/guide/](docs/guide/index.md)", one)
        # Apply both at once: heading into docs, deep entry at the root.
        support.write(self.root / "docs" / "index.md",
                      "# Docs\n\n## Spaces\n")
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [docs/guide/](docs/guide/index.md)\n")
        two = self.audit().stdout
        self.assertIn("entry docs/guide/ crosses the boundary of docs/", two)
        self.assertIn("drift docs/index.md: missing entry for guide/ — "
                      "add: - [guide/](guide/index.md)", two)
        # Apply round 2: drop the crossing entry, register guide in docs.
        support.write(index, index.read_text(encoding="utf-8").replace(
            "- [docs/guide/](docs/guide/index.md)\n", ""))
        support.write(self.root / "docs" / "index.md",
                      "# Docs\n\n## Spaces\n\n- [guide/](guide/index.md)\n")
        final = self.audit()
        self.assertNotIn("docs", final.stdout)   # converged; demo findings remain
        rels = support.run_ws("list", "--wiki", str(self.root)).stdout
        self.assertIn("docs/guide", rels)

    def test_missing_entry_suggestion_pastes_to_convergence(self):
        # The printed entry is computed by the same encode/decode round-trip
        # the parser applies — pasting it verbatim resolves the drift,
        # whatever the folder name.
        for name in ("notes (2024)", "a[b", "my space", "50% off"):
            with self.subTest(name=name):
                support.write(self.root / name / "index.md",
                              "# N\n\n## Spaces\n")
                out = self.audit().stdout
                tag = f"missing entry for {name}/ — add: "
                line = next(l for l in out.splitlines() if tag in l)
                entry = line.split(" — add: ", 1)[1]
                index = self.root / "index.md"
                support.write(index, index.read_text(encoding="utf-8")
                              + entry + "\n")
                self.assertNotIn(name, self.audit().stdout)
                r = support.run_ws("list", "--wiki", str(self.root))
                self.assertIn(name, r.stdout)

    def test_dotslash_and_title_hrefs_resolve(self):
        # `./x/index.md` and a CommonMark link title are trivial dialect:
        # normalized on read, never findings.
        support.write(self.root / "dot" / "index.md", "# D\n\n## Spaces\n")
        support.write(self.root / "titled" / "index.md",
                      "# T\n\n## Spaces\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + '- [dot/](./dot/index.md)\n'
                      + '- [titled/](titled/index.md "Titled")\n')
        out = self.audit().stdout
        self.assertNotIn("dot", out)
        self.assertNotIn("titled", out)
        rels = support.run_ws("list", "--wiki", str(self.root)).stdout
        self.assertIn("dot", rels)
        self.assertIn("titled", rels)

    def test_a_dir_named_index_md_is_unregistrable(self):
        # The one name no entry can carry: its href would read as a page.
        support.write(self.root / "plain" / "index.md" / "index.md",
                      "# X\n\n## Spaces\n")
        self.assertIn("contract index.md: unregistrable child name: "
                      "plain/index.md/ — the name does not survive a "
                      "contract entry; rename it", self.audit().stdout)

    def test_fix_flag_is_gone(self):
        # The write path was removed whole; the flag must not linger.
        r = self.audit("--fix")
        self.assertEqual(r.returncode, 2)

    @unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0,
                     "permission checks are a no-op as root")
    def test_every_command_runs_on_a_sealed_tree(self):
        # The subtraction's contract: no command writes to a wiki. Seal
        # the whole tree read-only; every command still answers, and no
        # file changes.
        before = {p: p.stat().st_mtime_ns
                  for p in self.root.rglob("*") if p.is_file()}
        self.addCleanup(self._unseal)
        self._seal()
        for cmd in (("audit",), ("audit", "--external"), ("list",),
                    ("files",), ("grep", "Alpha")):
            r = support.run_ws(*cmd, "--wiki", str(self.root))
            self.assertIn(r.returncode, (0, 1), cmd)
            self.assertNotIn("Traceback", r.stderr, cmd)
        self._unseal()
        after = {p: p.stat().st_mtime_ns
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def _dirs(self):
        return [self.root, *[p for p in self.root.rglob("*") if p.is_dir()]]

    def _seal(self):
        for d in self._dirs():
            d.chmod(0o555)

    def _unseal(self):
        for d in self._dirs():
            try:
                d.chmod(0o755)
            except OSError:
                pass

    def test_registered_mount_health_is_watched_by_default(self):
        # A registered mount that stops being a wiki surfaces in the
        # DEFAULT audit — the entry is ours to watch even though the
        # interior is not.
        support.write(self.root / "shared" / "nota" / "index.md",
                      "# Not a wiki\n")
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [shared/nota/](shared/nota/index.md)\n")
        self.assertIn("mount shared/nota/: registered but not a wiki",
                      self.audit().stdout)

    def test_broken_relative_markdown_link_is_reported(self):
        support.write(self.root / "_archives" / "old.md", "# Old\n")
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nSee [gone](gone.md) and [old](_archives/old.md).\n")
        out = self.audit().stdout
        self.assertIn("broken notes.md: (gone.md)", out)
        # On disk in a reserved dir — outside the walk but not dangling.
        self.assertNotIn("(_archives/old.md)", out)

    def test_unreadable_file_is_reported_and_counted(self):
        (self.root / "bad.md").write_bytes(b"\xff\xfe not utf-8\n")
        r = self.audit()
        self.assertIn("unreadable bad.md: not UTF-8", r.stdout)
        self.assertEqual(r.returncode, 1)

    def test_near_miss_heading_gets_the_rename_hint(self):
        # A registered child carrying a near-miss heading is half-declared,
        # and the repair is a rename — the author almost certainly meant
        # the contract.
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [low/](low/index.md)\n- [hash/](hash/index.md)\n")
        support.write(self.root / "low" / "index.md", "# Low\n\n## spaces\n")
        support.write(self.root / "hash" / "index.md",
                      "# Hash\n\n## Spaces ##\n")
        r = self.audit()
        self.assertIn('contract low/index.md: no ## Spaces heading '
                      '(carries "## spaces" — rename it to ## Spaces)',
                      r.stdout)
        self.assertIn('contract hash/index.md: no ## Spaces heading '
                      '(carries "## Spaces ##" — rename it to ## Spaces)',
                      r.stdout)

    def test_external_caps_never_inherit_the_hosts_limits(self):
        # The host's table caps tiny.md at 10 bytes; across the trust
        # boundary the mount's own limits (or the defaults) govern.
        support.write(self.root / "shared" / "team" / "_meta" / "limits.md",
                      "*.md: 30000\n")
        support.write(self.root / "shared" / "team" / "tiny.md",
                      "# Well over ten bytes, fine by their caps\n")
        support.write(self.root / "shared" / "team" / "wide.md",
                      "# W\n" + "y" * 16000 + "\n")
        out = self.audit("--external").stdout
        self.assertNotIn("over-cap shared/team/tiny.md", out)
        self.assertNotIn("over-cap shared/team/wide.md", out)

    def test_symlink_mount_caps_never_inherit_the_hosts_limits(self):
        # Same fence, third rule: a symlink-mounted space answers to its
        # own limits (m1) or the defaults (m2, whose tiny.md the host's
        # 10-byte override must not reach) — never the host's table.
        with tempfile.TemporaryDirectory() as outside:
            m1 = Path(outside).resolve() / "m1"
            support.write(m1 / "index.md", "# M1\n\n## Spaces\n")
            support.write(m1 / "_meta" / "limits.md", "*.md: 30000\n")
            support.write(m1 / "wide.md", "# W\n" + "y" * 16000 + "\n")
            m2 = Path(outside).resolve() / "m2"
            support.write(m2 / "index.md", "# M2\n\n## Spaces\n")
            support.write(m2 / "tiny.md",
                          "# Well over ten bytes, fine by the defaults\n")
            os.symlink(m1, self.root / "mnt")
            os.symlink(m2, self.root / "mnt2")
            out = self.audit("--external").stdout
            self.assertNotIn("over-cap mnt/wide.md", out)
            self.assertNotIn("over-cap mnt2/tiny.md", out)

    def test_external_findings_are_marked(self):
        support.write(self.root / "shared" / "team" / "big.md",
                      "# B\n" + "z" * 16000 + "\n")
        out = self.audit("--external").stdout
        self.assertRegex(out, r"over-cap shared/team/big\.md: .*\[external\]")

    def test_clean_wiki_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            clean = Path(td) / "w"
            support.write(clean / "index.md",
                          "# W\n\n## Spaces\n\n- [a/](a/index.md)\n")
            support.write(clean / "a" / "index.md", "# A\n\n## Spaces\n")
            r = support.run_ws("audit", "--wiki", str(clean))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.splitlines()[-1], "ok")

    def test_any_bullet_marker_carries_an_entry(self):
        # CommonMark bullets are `-`, `*`, `+` — an entry rides any of
        # them, so none silently drops a space off the contract.
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "* [starred/](starred/index.md)\n")
        support.write(self.root / "starred" / "index.md",
                      "# S\n\n## Spaces\n")
        out = self.audit().stdout
        self.assertNotIn("starred", out)   # listed, resolved, no drift

    def test_bare_dir_interior_is_audited_though_unreachable(self):
        # `files`/`grep` stop at a bare-index dir (not contract-reachable),
        # but the audit's filesystem walk still sees inside — an over-cap
        # page there must not hide.
        support.write(self.root / "bare" / "inner.md",
                      "# I\n\n" + "x" * 15100 + "\n")
        r = support.run_ws("files", "--wiki", str(self.root))
        self.assertNotIn("bare/inner.md", r.stdout)
        self.assertIn("over-cap bare/inner.md", self.audit().stdout)

    def test_duplicate_entry_is_flagged(self):
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "- [alpha/](alpha/index.md) — again\n")
        self.assertIn("contract index.md: duplicate entry: alpha/",
                      self.audit().stdout)

    def test_second_spaces_heading_is_flagged(self):
        index = self.root / "index.md"
        support.write(index, index.read_text(encoding="utf-8")
                      + "\n## Spaces\n\n- [beta/](beta/index.md)\n")
        self.assertIn("contract index.md: a second ## Spaces heading",
                      self.audit().stdout)

    def test_frontmatter_heading_is_not_the_contract(self):
        # `## Spaces` inside YAML frontmatter is metadata, not a heading:
        # the dir is bare (and undeclared here).
        support.write(self.root / "fm" / "index.md",
                      "---\ntitle: x\n## Spaces\n---\n\n# Real body.\n")
        self.assertIn("contract fm/index.md: no ## Spaces heading",
                      self.audit().stdout)

    def test_asset_wikilinks_are_exempt(self):
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nThe scan ([[report.pdf]]) names an asset.\n")
        out = self.audit().stdout
        self.assertNotIn("report.pdf", out)
        self.assertIn("broken notes.md: [[nope]]", out)  # pages still checked

    def test_dangling_upward_markdown_link_is_broken(self):
        outside = self.root.parent / "beside.md"
        support.write(outside, "# Beside\n")
        self.addCleanup(outside.unlink)
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nUp: [gone](../nowhere.md), [there](../beside.md).\n")
        out = self.audit().stdout
        self.assertIn("broken notes.md: (../nowhere.md)", out)
        self.assertNotIn("(../beside.md)", out)

    def test_path_qualified_wikilink_resolves_by_suffix(self):
        # `[[assets/deep]]` from the root names alpha/assets/deep.md by
        # its path tail — resolution is index-backed, not a page sweep.
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nDown: [[assets/deep]].\n")
        out = self.audit().stdout
        self.assertNotIn("assets/deep", out)   # resolved, not broken

    def test_wikilink_case_mismatch_resolves_like_obsidian(self):
        # The dialect resolves wikilinks case-insensitively: [[readme]]
        # names README.md, stem and path-qualified forms alike. Relative
        # markdown links keep filesystem semantics — the disk decides.
        support.write(self.root / "README.md", "# Readme\n")
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nSee [[readme]] and [[Assets/DEEP]].\n")
        out = self.audit().stdout
        self.assertNotIn("[[readme]]", out)            # resolved, not broken
        self.assertNotIn("DEEP", out)
        self.assertNotIn("README.md", out)             # credited, not orphan
        self.assertIn("broken notes.md: [[nope]]", out)  # missing still breaks

    def test_indented_code_is_not_scanned(self):
        notes = self.root / "notes.md"
        support.write(notes, notes.read_text(encoding="utf-8")
                      + "\nAn example:\n\n    [[indent-ghost]] "
                      "[also](indent-ghost.md)\n")
        out = self.audit().stdout
        self.assertNotIn("indent-ghost", out)

    def test_ignored_names_are_invisible_to_the_walk(self):
        support.write(self.root / "_meta" / "ignore.md",
                      "# Ignore\n\nnode_modules\n")
        support.write(self.root / "node_modules" / "pkg" / "README.md",
                      "# Pkg\n\n[gone](CHANGELOG.md)\n" + "x" * 16000 + "\n")
        r = self.audit()
        self.assertNotIn("node_modules", r.stdout)
        files = support.run_ws("files", "--wiki", str(self.root))
        self.assertNotIn("node_modules", files.stdout)

    def test_ignore_md_names_folders_not_files(self):
        # The convention's contract is folder names; a page whose basename
        # matches an entry stays visible to files, grep, and the audit.
        support.write(self.root / "_meta" / "ignore.md",
                      "notes.md\nassets\n")
        files = support.run_ws("files", "--wiki", str(self.root)).stdout
        self.assertIn("notes.md", files.splitlines())       # file: kept
        self.assertNotIn("alpha/assets/deep.md",
                         files.splitlines())                # folder: skipped

    def test_unregistered_external_mount_is_drift_under_external(self):
        support.write(self.root / "shared" / "loose" / "index.md",
                      "# Loose\n\n## Spaces\n")
        out = self.audit().stdout
        self.assertNotIn("shared/loose", out)   # default audit stays out
        r = self.audit("--external")
        self.assertIn("drift index.md: missing entry for shared/loose/ "
                      "(register mounts by hand) [external]", r.stdout)


if __name__ == "__main__":
    unittest.main()
