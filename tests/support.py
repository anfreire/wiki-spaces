"""Shared helpers for the ws.py test suite.

Loads the canonical ws.py copy (ws-search's) as a module for unit tests and
builds the demo wiki fixture the traversal, audit, and golden tests share.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WS = REPO / "skills" / "ws-search" / "scripts" / "ws.py"
WS_COPIES = [
    REPO / "skills" / name / "scripts" / "ws.py"
    for name in ("ws-search", "ws-update", "ws-tend")
]
SKILLS = [
    REPO / "skills" / name / "SKILL.md"
    for name in ("ws-search", "ws-update", "ws-tend")
]


def load_ws():
    spec = importlib.util.spec_from_file_location("ws", WS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_ws(*args, cwd=None, env_extra=None, stdin=None):
    """Run ws.py in a subprocess with an isolated config location."""
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = "/nonexistent-config"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(WS), *args],
        cwd=cwd, env=env, input=stdin,
        capture_output=True, text=True,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_demo(root: Path) -> None:
    """The shared fixture: nested spaces, drift, a stale entry, a
    half-declared space (registered, bare index), an external mount, a
    broken wikilink, over-cap files, and an orphan."""
    write(root / "index.md", (
        "# Demo wiki\n"
        "\n"
        "## Items\n"
        "\n"
        "- [notes.md](notes.md) — top notes\n"
        "\n"
        "## Spaces\n"
        "\n"
        "- [alpha/](alpha/index.md) — first space\n"
        "- [beta/](beta/index.md)\n"
        "- [missing/](missing/index.md) — points nowhere\n"
        "- [halfway/](halfway/index.md) — registered, heading pending\n"
        "- [shared/team/](shared/team/index.md) — external mount\n"
    ))
    write(root / "notes.md", (
        "# Notes\n"
        "\n"
        "See [[alpha-notes]] and [[nope]]. Embeds are exempt: ![[photo.png]].\n"
        "\n"
        "A fenced example stays out of the scan:\n"
        "\n"
        "```\n"
        "[[fenced-ghost]]\n"
        "```\n"
        "\n"
        "Inline `[[span-ghost]]` too.\n"
    ))
    write(root / "orphan.md", "# Orphan\n\nNothing links here.\n")
    write(root / "big.md", "# Big\n\n" + "x" * 15000 + "\n")
    write(root / "tiny.md", "# Tiny page over its custom cap\n")
    write(root / "_meta" / "limits.md", (
        "# Limits\n"
        "\n"
        "tiny.md: 10\n"
        "ignored | table row | 99\n"
    ))
    write(root / "alpha" / "index.md", (
        "# Alpha\n"
        "\n"
        "## Items\n"
        "\n"
        "- [alpha-notes.md](alpha-notes.md)\n"
        "\n"
        "## Spaces\n"
    ))
    write(root / "alpha" / "alpha-notes.md",
          "# Alpha Notes\n\nBack to [[notes]], down to [[deep]].\n")
    write(root / "alpha" / "assets" / "deep.md", "# Deep\n\nIn a plain folder.\n")
    write(root / "beta" / "index.md",
          "# Beta\n\n## Spaces\n\n- [gamma/](gamma/index.md)\n")
    write(root / "beta" / "gamma" / "index.md", "# Gamma\n\n## Spaces\n")
    write(root / "unregistered" / "index.md", "# Unregistered\n\n## Spaces\n")
    write(root / "bare" / "index.md", "# Bare\n")
    write(root / "halfway" / "index.md", "# Halfway\n")
    write(root / "shared" / "team" / "index.md", "# Team\n\n## Spaces\n")
