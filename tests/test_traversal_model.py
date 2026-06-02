"""Model traversal ↔ command consistency.

During the v1.2.0 traversal unification this file also held equivalence tests
comparing the new `_model` traversals to the legacy `space.py` walkers; those
walkers have since been retired, so the remaining check is the one that stays
meaningful: `_model.drift_from_nodes` must reproduce the drift the live
`space audit --json` command reports. The consumer/owned traversals' own
unit coverage now lives in `test_space.py` (pointed at `_model` via adapters)
and the command-level safety net in `test_traversal_gate.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wiki_spaces import _model

from tests.conftest import run_cli as _run, write_tree as _write


def _build_canonical(tmp_path: Path) -> Path:
    """Registered spaces + drift + bare index + stale + plain folders +
    shared/ external + reserved dirs (no symlinks/git)."""
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {
        "index.md": (
            "# wiki\n\n## Spaces\n\n"
            "- [Concepts](concepts/index.md) — ideas\n"
            "- [Projects](projects/index.md) — work\n"
            "- [Ghost](ghost/index.md) — stale\n"
            "- [Team](shared/team/index.md) — external\n"
        ),
        "concepts/index.md": "# Concepts\n\n## Spaces\n\n",
        "concepts/foo.md": "# Foo\n\nbody\n",
        "concepts/plain/bar.md": "# Bar\n\nbody\n",
        "projects/index.md": "# Projects\n\n## Spaces\n\n- [Acme](acme/index.md)\n",
        "projects/acme/index.md": "# Acme\n\n## Spaces\n\n",
        "projects/acme/notes.md": "# Notes\n\nnote\n",
        "drift/index.md": "# Drift\n\n## Spaces\n\n",
        "drift/d.md": "# D\n\nd\n",
        "bare/index.md": "# Bare\n\nprose\n",
        "_meta/limits.md": "# limits\n",
        "_archives/old.md": "# old\n",
        "shared/team/index.md": "# Team\n\n## Spaces\n\n",
        "shared/team/tnote.md": "# t\n\nx\n",
    })
    return root


def _build_symlinked(tmp_path: Path) -> Path:
    """Escaping symlink (with index), index-less escaping symlink, and a
    symlink cycle back to the root."""
    _write(tmp_path, {
        "outside/extspace/index.md": "# Ext\n\n## Spaces\n\n",
        "outside/extspace/e.md": "# E\n\ne\n",
        "outside_plain/readme.md": "x\n",
    })
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {
        "index.md": (
            "# wiki\n\n## Spaces\n\n"
            "- [Ext](ext/index.md)\n"
            "- [Real](realsub/index.md)\n"
        ),
        "realsub/index.md": "# Real\n\n## Spaces\n\n- [Self](self/index.md)\n",
    })
    os.symlink(tmp_path / "outside" / "extspace", root / "ext", target_is_directory=True)
    os.symlink(tmp_path / "outside_plain", root / "extplain", target_is_directory=True)
    os.symlink(root, root / "realsub" / "self", target_is_directory=True)
    return root


def _build_foreign_submodule(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    _write(root, {
        ".git/config": '[remote "origin"]\n\turl = https://github.com/me/wiki.git\n',
        ".gitmodules": (
            '[submodule "vendor"]\n'
            "\tpath = projects/vendor\n"
            "\turl = https://github.com/other/vendor.git\n"
        ),
        "index.md": "# wiki\n\n## Spaces\n\n- [Projects](projects/index.md)\n",
        "projects/index.md": "# Projects\n\n## Spaces\n\n- [Vendor](vendor/index.md)\n",
        "projects/vendor/index.md": "# Vendor\n\n## Spaces\n\n",
        "projects/vendor/top.md": "# v\n\ny\n",
        "projects/vendor/docs/nested.md": "# vn\n\nx\n",
    })
    return root


_BUILDERS = [_build_canonical, _build_symlinked, _build_foreign_submodule]


@pytest.fixture(params=_BUILDERS, ids=["canonical", "symlinked", "submodule"])
def wiki(request, tmp_path):
    return request.param(tmp_path)


@pytest.mark.parametrize("inc", [False, True])
def test_drift_from_nodes_matches_audit_json(wiki, inc):
    """drift_from_nodes reproduces the drift the live audit command reports."""
    args = ["--wiki", str(wiki), "audit", "--json"]
    if inc:
        args.append("--include-external")
    _, out, _ = _run(args)
    audit_drift = {
        d["ancestor"]: (d["missing"], d["stale"])
        for d in json.loads(out)["drift"]
    }
    nodes = _model.discover_nodes(wiki, include_external=inc)
    model_drift = {}
    for sd in _model.drift_from_nodes(nodes, include_external=inc):
        rel = sd.space.relative_to(wiki).as_posix()
        model_drift[rel] = (sd.missing, sd.stale)
    assert model_drift == audit_drift
