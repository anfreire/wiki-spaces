"""Shared test fixtures and builders — the single source of truth the suite
consumes.

Before this module the suite carried four divergent `_make_wiki`s and seven
copies of `_run`, each a slightly different idea of "what a wiki is" / "how you
invoke the CLI." That is a producer=consumer break *in the test infrastructure
itself*: a test that builds its wiki one way and asserts against a wiki built
another way is testing a fiction. Everything here is the one oracle every test
builds on.

These are plain importable functions, not fixtures: pytest auto-discovers this
file, and `tests/__init__.py` makes it importable as `tests.conftest`. Test
modules alias them to their historical private names so call sites stay
unchanged:

    from tests.conftest import run_cli as _run, make_wiki as _make_wiki
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from wiki_spaces import space

HAS_GIT = shutil.which("git") is not None
"""True when `git` is on PATH — the gate for the constructed git-repo builders."""


# ---------------------------------------------------------------------------
# CLI invocation — the single `_run`.
# ---------------------------------------------------------------------------


def run_cli(
    args: list[str], *, entry=None, stdin: str | None = None
) -> tuple[int, str, str]:
    """Invoke a CLI entry point in-process, capturing ``(rc, stdout, stderr)``.

    ``entry`` defaults to ``space.main``; pass ``manifest.main`` /
    ``init_wiki.main`` / ``install.main`` to drive the other binaries (the
    historical ``_run`` copies differed only in which ``main`` they bound).
    ``stdin``, when given, feeds the entry point a fake stdin for the duration
    of the call.
    """
    if entry is None:
        entry = space.main
    out, err = io.StringIO(), io.StringIO()
    saved_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = entry(args)
    finally:
        if stdin is not None:
            sys.stdin = saved_stdin
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Fake-HOME subprocess harness — the ONLY correct way to drive install/doctor
# end to end against an isolated home.
#
# `_common.py` freezes `HOME = Path.home()` at IMPORT, and HARNESSES /
# COMMON_SKILLS_DIR / CONFIG_PATH derive from it. `monkeypatch.setenv("HOME")`
# fired *after* the module is imported does NOT redirect any of them — the
# values are already bound. A fresh subprocess re-imports `_common` with the
# fake HOME already in the environment, so every derived path lands under the
# temp home. This keeps the test's writes off the developer's real ~/.agents,
# ~/.claude, ~/.config, etc.
# ---------------------------------------------------------------------------

WIKI_SKILL_NAMES = ("ws-search", "ws-update", "ws-tend")
"""The three first-party skills install materializes (mirror of WIKI_SKILLS)."""

KEPANO_SKILL_NAMES = ("obsidian-markdown", "obsidian-bases")
"""The two vendored kepano deps install materializes (mirror of KEPANO_DEPS)."""

ALL_SKILL_NAMES = WIKI_SKILL_NAMES + KEPANO_SKILL_NAMES
"""Every skill name install writes, in the install order (wiki skills first)."""

# Detection markers, one per harness, mkdir'd into the fake home so a
# subprocess `install --all` / `doctor` detects all seven deterministically.
_HARNESS_MARKER_DIRS = (
    ".claude", ".codex", ".gemini", ".config/opencode",
    ".copilot", ".cursor", ".kiro",
)

_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
"""Absolute path to the package's `src/` — injected as PYTHONPATH so the
subprocess imports the in-tree package even without an editable install."""


def seed_fake_home(home: Path) -> Path:
    """Create every harness detection marker under `home` and return it.

    `install --all` ignores detection, but `doctor` and a bare `install`
    report per-harness state, so seeding all markers makes the subprocess see
    the full HARNESSES matrix as "detected" — deterministic across machines.
    """
    for d in _HARNESS_MARKER_DIRS:
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "config.toml").touch()  # codex's file-form detect marker
    return home


def run_cli_subprocess(
    args: list[str], home: Path, *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `python -m wiki_spaces.cli <args>` in a subprocess with a fake HOME.

    Returns the `CompletedProcess` (`.returncode`, `.stdout`, `.stderr`). The
    env pins HOME / USERPROFILE / XDG_CONFIG_HOME at the fake home so every
    frozen `_common` path resolves under it, and PYTHONPATH at the in-tree
    `src/` so the subprocess imports this checkout's package.
    """
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": str(_REPO_SRC),
    }
    return subprocess.run(
        [sys.executable, "-m", "wiki_spaces.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else str(home),
    )


def seed_source_tree(read_root: Path) -> Path:
    """Materialize a minimal install source tree under `read_root`.

    Lays down `skills/<wiki-skill>/SKILL.md` and
    `vendor/kepano/<dep>/SKILL.md` for every install skill so the lower-level
    `install_hub` / `install_harness_aliases` / `_install_skill_target`
    functions find a source for each (mirrors `_common.skill_rel`). Returns
    `read_root`.
    """
    for skill in WIKI_SKILL_NAMES:
        d = read_root / "skills" / skill
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("ok", encoding="utf-8")
    for skill in KEPANO_SKILL_NAMES:
        d = read_root / "vendor" / "kepano" / skill
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("ok", encoding="utf-8")
    return read_root


# ---------------------------------------------------------------------------
# Tree builders.
# ---------------------------------------------------------------------------


def write_tree(base: Path, files: dict[str, str]) -> Path:
    """Write ``files`` (relative path → text) under ``base``, creating parents.

    Returns ``base`` so callers can both ``root = write_tree(tmp, {...})`` and
    ``write_tree(root, {...})`` (return ignored). Markdown/text only.
    """
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return base


def make_wiki(
    base: Path,
    with_spaces_section: bool = True,
    *,
    subdir: str | None = "wiki",
    title: str = "wiki",
    what_this_space: str | None = "Test wiki",
) -> Path:
    """Scaffold a minimal valid wiki and return its root.

    Defaults reproduce the dominant historical ``_make_wiki(tmp_path)``:
    ``<base>/wiki/index.md`` carrying ``## What this space is`` plus an (empty)
    ``## Spaces``. ``with_spaces_section=False`` drops the contract heading (the
    "not a wiki" fixtures); ``subdir=None`` writes ``index.md`` directly under
    ``base`` (the in-place manifest variant); ``what_this_space=None`` omits the
    prose section.
    """
    root = (base / subdir) if subdir else base
    root.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n"
    if what_this_space is not None:
        body += f"\n## What this space is\n\n{what_this_space}\n"
    if with_spaces_section:
        body += "\n## Spaces\n\n"
    (root / "index.md").write_text(body, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# External / git / symlink builders (constructed at setup, not committable).
# Moved verbatim from test_space.py; the git-init loop is factored into one
# helper since both repo builders ran it identically.
# ---------------------------------------------------------------------------


def make_space_dir(path: Path, title: str = "mounted") -> Path:
    """A plain external space: a folder with `index.md` carrying `## Spaces`
    (the v1 navigation contract — mounted targets must satisfy it for mount to accept)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.md").write_text(f"# {title}\n\n## Spaces\n\n")
    return path


def _git_init_commit(path: Path) -> None:
    """`git init && git add -A && git commit` with deterministic identity."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    }
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=path, check=True, env=env, capture_output=True)


def make_git_repo(path: Path, title: str = "cloned", *, with_index: bool = True) -> Path:
    """A real local git repo with one commit (for clone tests).

    With `with_index` (default) the repo contains an `index.md` with
    `## Spaces` (the v1 navigation contract); otherwise only `notes.md` — used to
    exercise the not-a-wiki mount path.
    """
    path.mkdir(parents=True, exist_ok=True)
    if with_index:
        (path / "index.md").write_text(f"# {title}\n\n## Spaces\n\n")
    else:
        (path / "notes.md").write_text(f"# {title} notes\n")
    _git_init_commit(path)
    return path


def make_git_repo_with_bare_index(path: Path, title: str = "bare") -> Path:
    """A real git repo containing `index.md` WITHOUT `## Spaces` — used to
    exercise mount strict-refusal + rollback on bare external targets."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.md").write_text(f"# {title}\n\nno spaces section here.\n")
    _git_init_commit(path)
    return path


def make_git_config(wiki: Path, origin_url: str) -> None:
    """Write a minimal .git/config with origin remote."""
    git_dir = wiki / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "config").write_text(
        f'[remote "origin"]\n\turl = {origin_url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )


# ---------------------------------------------------------------------------
# The golden wiki — one canonical, deterministic tree that is the union of
# every wiki SHAPE and every committable traversal EDGE, with exact-match
# committed snapshots (see `assert_golden`). The constructed-at-setup edges
# that vary cross-platform (real `os.symlink`, real `git submodule add`) are
# NOT here — they would break exact-match determinism; the symlink/clone/
# submodule traversal edges are covered by `test_traversal_gate.py` and the
# mount tests in `test_space.py`. The foreign-submodule classification IS
# here because it is driven purely by parsing `.git/config` + `.gitmodules`
# text (no git binary, fully deterministic).
#
# Each edge is tagged `# EDGE: <name>`; `tests/golden/INVENTORY.md` catalogues
# them and `test_golden.py::test_golden_wiki_covers_every_edge` asserts each
# one actually surfaces in a snapshot, so coverage can't silently rot.
# ---------------------------------------------------------------------------

GOLDEN_SUBMODULE_ORIGIN = "https://github.com/other/vendor.git"
"""Foreign origin for the golden wiki's `projects/vendor` submodule — distinct
from the wiki's own origin, so the classifier marks the subtree external."""


def build_golden_wiki(root: Path) -> Path:
    """Construct the canonical golden wiki at ``root`` and return it.

    Pure file writes plus a fake `.git/config`/`.gitmodules` pair — no symlinks
    and no git binary, so the resulting `list`/`files`/`audit` JSON is identical
    on every POSIX platform. See the module-level note for the full rationale
    and `tests/golden/INVENTORY.md` for the edge catalogue.
    """
    write_tree(root, {
        # The wiki. Registers every owned child space, one stale entry
        # (ghost, no dir), and one external mount (shared/team).
        "index.md": (
            "# Golden Wiki\n\n"
            "## What this space is\n\n"
            "The canonical test fixture: every shape, every deterministic edge.\n\n"
            "## Spaces\n\n"
            "- [Concepts](concepts/index.md) — ideas and notes\n"
            "- [Projects](projects/index.md) — work, incl. a foreign submodule\n"
            "- [Notebook](notebook/index.md) — dev-notebook shape\n"
            "- [Alias Dup](aliasdup/index.md) — duplicate-alias hosts\n"
            "- [Malformed Host](malformed-host/index.md) — bad ## Spaces entries\n"
            "- [Flat](flat/index.md)\n"                              # EDGE: no-description-entry + flat-branch
            "- [Ghost](ghost/index.md) — stale entry, no dir\n"      # EDGE: stale-entry
            "- [Team](shared/team/index.md) — external mount\n"      # EDGE: shared-external
        ),

        # EDGE: registered-space + deep-nesting (concepts/sub) + plain-folder
        #       (concepts/plain) + valid-wikilink (foo -> bar by basename).
        "concepts/index.md":
            "# Concepts\n\n## Spaces\n\n- [Sub](sub/index.md) — leaf subspace\n",
        "concepts/foo.md": "# Foo\n\nbody linking to [[bar]] (resolves by basename)\n",  # EDGE: valid-wikilink
        "concepts/bar.md": "# Bar\n\ntarget of the foo link\n",
        "concepts/plain/asset.md": "# Asset\n\nin a plain folder (no index.md)\n",         # EDGE: plain-folder
        "concepts/sub/index.md": "# Sub\n\n## Spaces\n\n",                                  # EDGE: leaf-space (empty ## Spaces)
        "concepts/sub/note.md": "# Note\n\nleaf orphan\n",

        # EDGE: hub-space with a nested owned space (acme) and a FOREIGN
        #       SUBMODULE (vendor) — external via .gitmodules below.
        "projects/index.md": (
            "# Projects\n\n## Spaces\n\n"
            "- [Acme](acme/index.md) — owned subproject\n"
            "- [Vendor](vendor/index.md) — foreign submodule\n"
        ),
        "projects/acme/index.md": "# Acme\n\n## Spaces\n\n",
        "projects/acme/notes.md": "# Notes\n\nacme orphan\n",
        "projects/vendor/index.md": "# Vendor\n\n## Spaces\n\n",        # EDGE: foreign-submodule
        "projects/vendor/top.md": "# Vendor top\n\nexternal content\n",
        "projects/vendor/docs/nested.md": "# Vendor nested\n\ndeep external content\n",

        # EDGE: valid-frontmatter, malformed-frontmatter (both flavours),
        #       ## Spaces-as-content-heading, broken-wikilink.
        "notebook/index.md": "# Notebook\n\n## Spaces\n\n",
        "notebook/daily.md": (
            "---\ntags: [journal, daily]\naliases: [today]\n---\n"
            "# Daily\n\nvalid frontmatter page\n"
        ),                                                              # EDGE: valid-frontmatter
        "notebook/snippets.md": (
            "---\ntags: [oops\n---\n# Snippets\n\nunterminated YAML flow seq\n"
        ),                                                              # EDGE: malformed-frontmatter (yaml.YAMLError)
        "notebook/nonmap.md": (
            "---\n- just\n- a\n- list\n---\n# Non-mapping\n\ntop-level YAML is not a dict\n"
        ),                                                              # EDGE: nonmapping-frontmatter
        "notebook/about-spaces.md": (
            "# About Spaces\n\n## Spaces\n\nThis H2 is ordinary prose, not a "
            "navigation contract — the consumer must ignore it on a non-index page.\n"
        ),                                                              # EDGE: spaces-heading-as-content
        "notebook/brokenlink.md": "# Broken\n\nsee [[definitely-missing-target]]\n",  # EDGE: broken-wikilink

        # EDGE: duplicate-aliases across two contract-visible pages.
        "aliasdup/index.md": "# Alias Dup\n\n## Spaces\n\n",
        "aliasdup/a.md": "---\naliases: [shared-alias]\n---\n# A\n\nfirst claimant\n",
        "aliasdup/b.md": "---\naliases: [shared-alias]\n---\n# B\n\nsecond claimant\n",

        # EDGE: malformed-entry (empty href, unparseable bullet) + reserved-href
        #       (_meta/x normalises to a stale entry) + one good child.
        "malformed-host/index.md": (
            "# Malformed Host\n\n## Spaces\n\n"
            "- [Good](good/index.md)\n"
            "- [Empty]()\n"
            "- [Reserved](_meta/x/index.md)\n"
            "- [[half\n"
        ),
        "malformed-host/good/index.md": "# Good\n\n## Spaces\n\n",

        # EDGE: flat-branch / minimum-viable shape; registered with NO description.
        "flat/index.md": "# Flat\n\n## Spaces\n\n",

        # EDGE: drift — a VALID space not registered in any parent ## Spaces.
        "drift/index.md": "# Drift\n\n## Spaces\n\n",
        "drift/d.md": "# D\n\nunregistered-space orphan\n",

        # EDGE: bare-index — index.md WITHOUT ## Spaces; surfaces as
        #       missing_spaces_section AND as a missing drift entry under root.
        "bare/index.md": "# Bare\n\njust prose, no ## Spaces heading\n",

        # EDGE: reserved-dir (_archives) pruned by the consumer walker.
        "_archives/old.md": "# Archived\n\nreserved, pruned\n",

        # EDGE: shared-external mount (path-classified external by `shared/`).
        "shared/team/index.md": "# Team\n\n## Spaces\n\n",
        "shared/team/tnote.md": "# Team note\n\nexternal content\n",
    })

    # EDGE: approaching-cap — a page wired to a small USER_OVERRIDE cap in
    #       _meta/limits.md (so the fixture stays tiny) sitting at >= 80% but
    #       < 100% of that cap. The exact `chars` is locked by the snapshot.
    approaching = (
        "# Approaching\n\n"
        + "Filler prose to sit between 80% and 100% of the custom cap. " * 6
    )
    write_tree(root, {
        "notebook/approaching.md": approaching,
        "_meta/limits.md": (
            "# Size limits\n\n"
            "| Pattern                  | Cap (chars) |\n"
            "|--------------------------|-------------|\n"
            "| notebook/approaching.md  |         400 |\n"
        ),
    })

    # EDGE: foreign-submodule classification — parsed from .git/config +
    #       .gitmodules text only (no git binary, deterministic).
    write_tree(root, {
        ".git/config": '[remote "origin"]\n\turl = https://github.com/me/golden.git\n',
        ".gitmodules": (
            '[submodule "vendor"]\n'
            "\tpath = projects/vendor\n"
            f"\turl = {GOLDEN_SUBMODULE_ORIGIN}\n"
        ),
    })
    return root


# ---------------------------------------------------------------------------
# Snapshot harness.
# ---------------------------------------------------------------------------

GOLDEN_DIR = Path(__file__).parent / "golden"
"""Directory holding the committed `*.json` snapshots."""


def _canonical(obj) -> str:
    """Canonical JSON: keys sorted, 2-space indent, trailing newline. Sorting
    keys normalises cosmetic emission-order differences while leaving LIST
    order intact — so the LIFO traversal contract is still pinned exactly."""
    return json.dumps(obj, sort_keys=True, indent=2) + "\n"


def normalize_audit_payload(payload: dict) -> dict:
    """Strip the non-deterministic bits of an `audit --json` payload so it can
    be snapshotted: the absolute `wiki` path, and the pyyaml-version-coupled
    `error_line`/`error_message` of MALFORMED frontmatter (a yaml.YAMLError's
    text/position is the library's, not ours). NON_MAPPING keeps its message —
    that string is ours and is stable."""
    out = dict(payload)
    out["wiki"] = "<wiki>"
    out["malformed_frontmatter"] = [
        {**e, "error_line": "<line>", "error_message": "<yaml-error>"}
        if e.get("status") == "malformed"
        else e
        for e in payload.get("malformed_frontmatter", [])
    ]
    return out


def assert_golden(name: str, actual_obj) -> None:
    """Compare ``actual_obj`` against ``tests/golden/<name>.json`` with exact
    canonical equality. ``REGEN_GOLDEN=1 pytest …`` rewrites the file instead
    of asserting, so golden drift is always a reviewed diff — never silent."""
    path = GOLDEN_DIR / f"{name}.json"
    actual = _canonical(actual_obj)
    if os.environ.get("REGEN_GOLDEN"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), (
        f"missing golden snapshot {path}; run `REGEN_GOLDEN=1 pytest` to create it"
    )
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"golden mismatch for {name!r}. If this change is intended, re-run with "
        f"`REGEN_GOLDEN=1 pytest` and review the diff.\n"
        f"--- expected ---\n{expected}\n--- actual ---\n{actual}"
    )
