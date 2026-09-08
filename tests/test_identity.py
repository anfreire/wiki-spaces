"""The three bundled ws.py copies must stay byte-identical, the shared
core block each SKILL.md carries must match verbatim across the skills
(dedup by compression, not by sharing machinery), the safe-repair set
both writing skills carry must agree line for line, and every prose
restatement — cap defaults, interpreter floor, the promised platforms,
the log's archive destination — must agree with the code where one
anchors it and with its twin where none does. Prose that states a
procedure answers the same way: the config block init.md carries must
feed the resolver it writes for."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import support

CORE_RE = re.compile(r"<!-- ws:core -->\n(.*?)<!-- /ws:core -->", re.DOTALL)
SAFE_RE = re.compile(
    r"<!-- ws:safe-repairs -->\n(.*?)<!-- /ws:safe-repairs -->", re.DOTALL)


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

    def test_safe_repair_sets_match_verbatim(self):
        """The safe repair set is the write-authority boundary — what a
        close-out may apply without asking. ws-update and ws-tend both
        carry it; the copies must agree line for line (indentation
        aside — the numbered steps they sit under differ)."""
        blocks = {}
        for p in support.SKILLS:
            if p.parent.name == "ws-search":
                continue                 # read-only skill, no close-out
            m = SAFE_RE.search(p.read_text(encoding="utf-8"))
            self.assertIsNotNone(m, f"{p} lacks the ws:safe-repairs markers")
            lines = [ln.strip() for ln in m.group(1).splitlines()
                     if ln.strip()]
            self.assertTrue(lines, f"{p}: empty safe-repairs block")
            blocks[p] = lines
        (p1, b1), (p2, b2) = blocks.items()
        self.assertEqual(
            b1, b2,
            f"{p1} and {p2} state different safe repair sets — the set is "
            "duplicated verbatim; edit both together")

    def test_skill_frontmatter_is_the_install_surface(self):
        """A harness reads SKILL.md frontmatter to decide invocation:
        `name` must match the skill's directory and `description` must
        exist — broken frontmatter ships a skill no harness ever calls."""
        for p in support.SKILLS:
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "---", f"{p} lacks frontmatter")
            end = lines[1:].index("---") + 1
            fm = {}
            for line in lines[1:end]:
                key, _, value = line.partition(":")
                fm[key.strip()] = value.strip()
            self.assertEqual(fm.get("name"), p.parent.name,
                             f"{p}: frontmatter name must match the "
                             "skill directory")
            self.assertTrue(fm.get("description"),
                            f"{p}: description is what the harness reads "
                            "to invoke the skill")

    def test_skill_docs_are_self_contained(self):
        """`npx skills add` copies one skill directory whole; every real
        relative link inside SKILL.md and its references must resolve
        within that directory, or the install ships dangling docs.
        Example links ride code spans and blocks — a local strip keeps
        them out (the script no longer reads content, so the test owns
        its own doc scanning)."""
        ws = support.load_ws()

        def strip_code(text):
            lines = text.splitlines()
            fenced = ws.fenced_mask(lines)
            return "\n".join(
                "" if fenced[i] else re.sub(r"`[^`\n]+`", "", line)
                for i, line in enumerate(lines))

        link_re = re.compile(r"\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")
        for skill in support.SKILLS:
            sdir = skill.parent.resolve()
            docs = [skill, *sorted(sdir.glob("references/*.md"))]
            for doc in docs:
                scan = strip_code(doc.read_text(encoding="utf-8"))
                for href in link_re.findall(scan):
                    if "://" in href or href.startswith(("mailto:", "#")):
                        continue
                    target = (doc.parent / href).resolve()
                    self.assertTrue(
                        target.is_relative_to(sdir),
                        f"{doc}: link ({href}) escapes the skill directory")
                    self.assertTrue(
                        target.exists(), f"{doc}: link ({href}) dangles")

    def test_core_block_states_the_tested_floor(self):
        """HANDBOOK One source of truth: CI tests exactly what the prose
        promises. The core block states the interpreter floor, the CI
        matrix must run it, and README must state the same floor."""
        core = CORE_RE.search(
            support.SKILLS[0].read_text(encoding="utf-8")).group(1)
        self.assertIn("stdlib python3 (3.9+), zero dependencies", core)
        ci = (support.REPO / ".github" / "workflows" / "ci.yml") \
            .read_text(encoding="utf-8")
        self.assertIn('"3.9"', ci,
                      "CI must test the floor the core block claims")
        readme = (support.REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 3.9+ standard library only", readme,
                      "README must state the floor the core block claims")

    def test_ci_runs_the_promised_platforms(self):
        """README promises the POSIX hosts by name (macOS, Linux); the CI
        matrix must run a leg for each — CI tests exactly what the prose
        promises."""
        readme = (support.REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("(macOS, Linux)", readme)
        ci = (support.REPO / ".github" / "workflows" / "ci.yml") \
            .read_text(encoding="utf-8")
        for runner in ("ubuntu-latest", "macos-latest"):
            self.assertIn(runner, ci,
                          f"CI must run {runner} — README promises the "
                          "platform")

    def test_log_roll_destination_agrees_across_prose(self):
        """The archive roll is pure convention — the script never rolls,
        so no code anchors it. The core block (the installed surface) and
        CONVENTIONS.md (the convention's home) both state the destination;
        pin them together so the copies can't drift apart silently."""
        dest = "`_archives/log-<YYYYMMDD>.md`"
        core = CORE_RE.search(
            support.SKILLS[0].read_text(encoding="utf-8")).group(1)
        self.assertIn(dest, core,
                      "the core block must state the roll destination")
        conventions = (support.REPO / "CONVENTIONS.md") \
            .read_text(encoding="utf-8")
        self.assertIn(dest, conventions,
                      "CONVENTIONS.md must state the same roll destination")

    def test_init_config_writer_feeds_the_resolver(self):
        """init.md's config-registration block is the producer of the
        `wiki` pointer and ws.py's resolver the consumer. The reference
        states the resolver takes the *first* valid line, so the block
        must replace a stale pointer, never append below it — run the
        block exactly as init.md states it, fresh and over a stale
        config alike: the resolver must take the pointer it wrote, and
        every other line must survive."""
        init = (support.REPO / "skills" / "ws-update" / "references"
                / "init.md").read_text(encoding="utf-8")
        blocks = [b for b in re.findall(r"```sh\n(.*?)```", init, re.DOTALL)
                  if "wiki-spaces/config" in b]
        self.assertEqual(len(blocks), 1,
                         "init.md must carry exactly one config block")
        block = blocks[0]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            wiki = base / "wiki"
            stale = base / "stale"
            for w in (wiki, stale):
                support.write(w / "index.md", "# W\n\n## Spaces\n")
            cfg_home = base / "xdg"
            cfg = cfg_home / "wiki-spaces" / "config"
            cases = {
                "fresh": None,
                "replace": f"# kept comment\nwiki = {stale}\nkey = kept\n",
            }
            for case, seed in cases.items():
                with self.subTest(case=case):
                    if cfg.exists():
                        cfg.unlink()
                    if seed is not None:
                        support.write(cfg, seed)
                    r = subprocess.run(
                        ["sh", "-c", block], capture_output=True, text=True,
                        env={**os.environ, "WIKI": str(wiki),
                             "XDG_CONFIG_HOME": str(cfg_home)})
                    self.assertEqual(r.returncode, 0, r.stderr)
                    text = cfg.read_text(encoding="utf-8")
                    self.assertIn(f"wiki = {wiki}", text)
                    if seed is not None:
                        self.assertIn("# kept comment", text)
                        self.assertIn("key = kept", text)
                        self.assertNotIn(str(stale), text)
                    out = support.run_ws(
                        "list", cwd=td,
                        env_extra={"XDG_CONFIG_HOME": str(cfg_home)})
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertIn(f"wiki: {wiki}", out.stderr)

    def test_own_space_setup_blocks_feed_the_resolver(self):
        """own-space.md's shell blocks are the producer of a folder's own
        space and ws.py's resolver the consumer. Run them exactly as the
        reference states them, placeholders filled: both shapes must
        resolve from inside the folder, the audit's `missing entry` line
        must register the link, git must ignore the wiki-held link, and
        a second run must change nothing the first made."""
        ws = support.load_ws()
        ref = (support.REPO / "skills" / "ws-update" / "references"
               / "own-space.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```sh\n(.*?)```", ref, re.DOTALL)
        self.assertEqual(len(blocks), 3, "own-space.md must carry three "
                         "blocks: create with the folder, link from the "
                         "wiki, create in the wiki")
        create_with, link_from_wiki, create_in = blocks
        skill_dir = support.REPO / "skills" / "ws-update"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            wiki = base / "wiki"
            support.write(wiki / "index.md", "# W\n\n## Spaces\n\n"
                          "- [projects/](projects/index.md)\n")
            support.write(wiki / "projects" / "index.md",
                          "# P\n\n## Spaces\n")
            cfg_home = base / "xdg"
            support.write(cfg_home / "wiki-spaces" / "config",
                          f"wiki = {wiki}\n")
            env = {"XDG_CONFIG_HOME": str(cfg_home)}

            def filled(block, folder, name):
                for placeholder, value in {
                    "/path/to/folder": str(folder),
                    "~/Documents/Wiki": str(wiki),
                    "<skill-dir>": str(skill_dir),
                    "<name>": name,
                    "<what the space holds — one sentence, in their words>":
                        f"What {name} knows.",
                    "<the space it belongs under, else the root>": "projects",
                }.items():
                    block = block.replace(placeholder, value)
                self.assertIsNone(re.search(r"<[^>\n]*>", block),
                                  f"placeholder left unfilled:\n{block}")
                return block

            def run(script):
                return subprocess.run(["sh", "-c", script],
                                      capture_output=True, text=True,
                                      env={**os.environ, **env})

            def paste_missing_entry(audit_stdout):
                lines = [line.split("add: ", 1)[1]
                         for line in audit_stdout.splitlines()
                         if "missing entry" in line]
                self.assertEqual(len(lines), 1, audit_stdout)
                index = wiki / "projects" / "index.md"
                support.write(index, index.read_text(encoding="utf-8")
                              + lines[0] + " — pasted\n")
                r = support.run_ws("audit", "--wiki", str(wiki),
                                   env_extra=env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            def resolves_to(folder, space):
                r = support.run_ws("audit", cwd=folder / "src", env_extra=env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"wiki: {space}\n", r.stdout)

            # Shape one: the space lives with the folder.
            folder = base / "one"
            (folder / "src").mkdir(parents=True)
            r = run(filled(create_with, folder, "one"))
            self.assertEqual(r.returncode, 0, r.stderr)
            index = folder / ws.OWN_SPACE / "index.md"
            made = index.read_text(encoding="utf-8")
            self.assertIn("## Spaces", made)
            r = run(filled(create_with, folder, "one"))
            self.assertNotEqual(r.returncode, 0, "a taken name must refuse")
            self.assertEqual(index.read_text(encoding="utf-8"), made)
            r = run(filled(link_from_wiki, folder, "one"))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            paste_missing_entry(r.stdout)
            resolves_to(folder, folder / ws.OWN_SPACE)
            run(filled(link_from_wiki, folder, "one"))
            self.assertEqual(sorted(os.listdir(folder / ws.OWN_SPACE)),
                             ["index.md"], "a second link must not land "
                             "inside the space")

            # Shape two: the space lives in the wiki, the folder links.
            folder = base / "two"
            (folder / "src").mkdir(parents=True)
            r = run(filled(create_in, folder, "two"))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            paste_missing_entry(r.stdout)
            space = wiki / "projects" / "two"
            made = (space / "index.md").read_text(encoding="utf-8")
            resolves_to(folder, space)
            run(filled(create_in, folder, "two"))
            self.assertEqual((space / "index.md").read_text(encoding="utf-8"),
                             made)
            self.assertEqual(sorted(os.listdir(space)), ["index.md"])
            ignore = (folder / ".gitignore").read_text(encoding="utf-8")
            self.assertEqual(ignore.count("/.wiki-spaces"), 1)
            if shutil.which("git") is None:
                self.skipTest("git not installed")
            git = ["git", "-C", str(folder)]
            subprocess.run([*git, "init", "-q"], check=True,
                           capture_output=True)
            r = subprocess.run([*git, "status", "--porcelain"],
                               capture_output=True, text=True)
            self.assertNotIn(ws.OWN_SPACE, r.stdout)
            r = subprocess.run([*git, "check-ignore", "-q", ws.OWN_SPACE])
            self.assertEqual(r.returncode, 0, "git must ignore the link")

    def test_prose_states_the_own_space_name(self):
        """The name of a folder's own space lives in code once
        (ws.OWN_SPACE) and is restated wherever prose teaches it —
        the spec, the core block, CONVENTIONS.md, README.md, ws-update's
        own steps, and the references that set one up or mount one. Pin
        every restatement to the code."""
        ws = support.load_ws()
        needle = f"`{ws.OWN_SPACE}/`"
        core = CORE_RE.search(
            support.SKILLS[0].read_text(encoding="utf-8")).group(1)
        self.assertIn(needle, core)
        for name in ("AGENTS.md", "CONVENTIONS.md", "README.md",
                     "skills/ws-update/SKILL.md",
                     "skills/ws-update/references/own-space.md",
                     "skills/ws-update/references/mount.md"):
            text = CORE_RE.sub("", (support.REPO / name).read_text(
                encoding="utf-8"))        # the core block is pinned above
            self.assertIn(needle, text,
                          f"{name} drifted from ws.OWN_SPACE")

    def test_prose_cap_tables_state_the_code_defaults(self):
        """The caps live in code once (ws.DEFAULT_CAPS) but are restated in
        the skill core block, AGENTS.md, CONVENTIONS.md, README.md, and
        init.md's wiki-local contract note — pin every restatement to the
        code so the numbers can't drift apart silently."""
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
                f"Default cap is {l:,} bytes",
            ]),
            (support.REPO / "README.md", None, [
                f"({i:,} for an `index.md`, {m:,} for a page)",
            ]),
            (support.REPO / "skills" / "ws-update" / "references"
             / "init.md", None, [
                f"`index.md` {i:,}; content pages {m:,}; "
                f"`log.md`/`hot.md` {l:,}",
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
