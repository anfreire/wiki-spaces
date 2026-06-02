"""Self-maintenance signals — `audit --json` promote/prune candidates, and the
bounded self-correction (`audit --fix`) contract.

The signals are INFORMATIONAL: they surface structural reflection ("this page
has grown into a space"; "this hot buffer needs draining") without flipping the
audit exit code, so the release gate stays stable. These pin each trigger AND
its non-trigger (so the signal stays sharp, not noisy), plus the exactly-once /
idempotent / never-touch-malformed contract of `--fix`.
"""

from __future__ import annotations

import json

from wiki_spaces import _md
from wiki_spaces.space import PROMOTE_MIN_HUB_PAGES, PROMOTE_MIN_SPLIT_H2

from tests.conftest import make_wiki, run_cli, write_tree


def _audit(wiki):
    rc, out, _ = run_cli(["--wiki", str(wiki), "audit", "--json"])
    return rc, json.loads(out)


# ---------------------------------------------------------------------------
# promote_candidates — hub (accreted siblings).
# ---------------------------------------------------------------------------


def test_promote_hub_fires_at_threshold_not_below(tmp_path):
    # Below threshold: PROMOTE_MIN_HUB_PAGES - 1 direct pages → no hub.
    under = make_wiki(tmp_path, subdir="under")
    assert run_cli(["--wiki", str(under), "add", "notes"])[0] == 0
    write_tree(under, {
        f"notes/p{i}.md": f"# P{i}\n\nbody\n" for i in range(PROMOTE_MIN_HUB_PAGES - 1)
    })
    _, a = _audit(under)
    assert not any(c["kind"] == "hub" for c in a["promote_candidates"])

    # At threshold: PROMOTE_MIN_HUB_PAGES direct pages → hub fires.
    at = make_wiki(tmp_path, subdir="at")
    assert run_cli(["--wiki", str(at), "add", "notes"])[0] == 0
    write_tree(at, {
        f"notes/p{i}.md": f"# P{i}\n\nbody\n" for i in range(PROMOTE_MIN_HUB_PAGES)
    })
    _, a = _audit(at)
    hubs = [c for c in a["promote_candidates"] if c["kind"] == "hub"]
    assert [c["path"] for c in hubs] == ["notes"]
    assert hubs[0]["pages"] == PROMOTE_MIN_HUB_PAGES


# ---------------------------------------------------------------------------
# promote_candidates — split_ready (size pressure + clean H2 boundaries).
# ---------------------------------------------------------------------------


def test_promote_split_ready_requires_both_size_and_h2(tmp_path):
    wiki = make_wiki(tmp_path)
    assert run_cli(["--wiki", str(wiki), "add", "notes"])[0] == 0
    write_tree(wiki, {
        "_meta/limits.md": (
            "# Size limits\n\n"
            "| Pattern        | Cap (chars) |\n"
            "|----------------|-------------|\n"
            "| notes/big.md   |         200 |\n"
            "| notes/noh2.md  |         200 |\n"
        ),
    })
    # big: >= 80% of 200 AND 2 H2 sections → split_ready.
    big = "# Big\n\n## Section A\n\n" + "x" * 80 + "\n\n## Section B\n\n" + "y" * 80 + "\n"
    assert PROMOTE_MIN_SPLIT_H2 == 2  # this fixture assumes the 2-section threshold
    # noh2: >= 80% of its cap but ZERO H2 → size pressure, nowhere to split.
    noh2 = "# Flat\n\n" + "z" * 175
    # tiny: 2 H2 but default 15K cap, nowhere near 80% → no size pressure.
    tiny = "# Tiny\n\n## A\n\none\n\n## B\n\ntwo\n"
    write_tree(wiki, {"notes/big.md": big, "notes/noh2.md": noh2, "notes/tiny.md": tiny})

    rc, a = _audit(wiki)
    split = {c["path"] for c in a["promote_candidates"] if c["kind"] == "split_ready"}
    assert "notes/big.md" in split
    assert "notes/noh2.md" not in split, "size without H2 boundaries must not fire"
    assert "notes/tiny.md" not in split, "H2 without size pressure must not fire"


# ---------------------------------------------------------------------------
# prune_candidates — hot.md distill.
# ---------------------------------------------------------------------------


def test_prune_hot_distill_fires_only_when_full(tmp_path):
    # Full hot buffer (>= 80% of a small override cap) → distill prompt.
    full = make_wiki(tmp_path, subdir="full")
    write_tree(full, {
        "_meta/limits.md": (
            "# Size limits\n\n| Pattern | Cap (chars) |\n|---|---|\n| hot.md | 100 |\n"
        ),
        "hot.md": "# Hot\n\n" + "x" * 90,
    })
    _, a = _audit(full)
    hd = [c for c in a["prune_candidates"] if c["kind"] == "hot_distill"]
    assert [c["path"] for c in hd] == ["hot.md"]
    assert hd[0]["cap"] == 100

    # Near-empty hot buffer → no prune candidate.
    empty = make_wiki(tmp_path, subdir="empty")
    write_tree(empty, {
        "_meta/limits.md": (
            "# Size limits\n\n| Pattern | Cap (chars) |\n|---|---|\n| hot.md | 100 |\n"
        ),
        "hot.md": "# Hot\n\njust a couple notes\n",
    })
    _, a = _audit(empty)
    assert a["prune_candidates"] == []


# ---------------------------------------------------------------------------
# The release-gate invariant: signals never flip the exit code.
# ---------------------------------------------------------------------------


def test_signals_present_but_never_flip_exit_code(tmp_path):
    wiki = make_wiki(tmp_path)
    assert run_cli(["--wiki", str(wiki), "add", "notes"])[0] == 0
    write_tree(wiki, {
        "_meta/limits.md": (
            "# Size limits\n\n| Pattern | Cap (chars) |\n|---|---|\n"
            "| hot.md | 100 |\n| notes/big.md | 200 |\n"
        ),
        "hot.md": "# Hot\n\n" + "x" * 90,  # hot_distill
        "notes/big.md": "# Big\n\n## A\n\n" + "x" * 80 + "\n\n## B\n\n" + "y" * 80 + "\n",  # split_ready
    })
    # …plus enough sibling pages to make notes a hub.
    write_tree(wiki, {
        f"notes/p{i}.md": f"# P{i}\n\nbody\n" for i in range(PROMOTE_MIN_HUB_PAGES)
    })

    rc, a = _audit(wiki)
    # All three signals fire…
    kinds = {c["kind"] for c in a["promote_candidates"]} | {c["kind"] for c in a["prune_candidates"]}
    assert {"hub", "split_ready", "hot_distill"} <= kinds
    # …yet the wiki is contract-clean, so the exit code stays 0.
    assert a["exit_code"] == 0, a
    assert rc == 0


# ---------------------------------------------------------------------------
# Bounded self-correction — `audit --fix` repairs only the SAFE structural
# drift, never the author-intent findings, exactly once and idempotently.
# ---------------------------------------------------------------------------


def test_audit_fix_repairs_safe_but_leaves_malformed_untouched(tmp_path):
    wiki = make_wiki(tmp_path)
    write_tree(wiki, {
        # `host` is registered; `bare` is the safe drift the fix should repair.
        "index.md": (
            "# wiki\n\n## What this space is\n\nx\n\n## Spaces\n\n"
            "- [Host](host/index.md)\n"
        ),
        # author-intent findings the fix must NOT touch:
        "host/index.md": "# Host\n\n## Spaces\n\n- [[half\n",          # malformed entry
        "host/page.md": "---\ntags: [oops\n---\n# P\n\nbad YAML frontmatter\n",  # malformed fm
        # safe structural drift the fix SHOULD repair:
        "bare/index.md": "# Bare\n\nno ## Spaces heading\n",
    })

    rc, a = _audit(wiki)
    assert rc == 1
    assert a["malformed_entries"] and a["malformed_frontmatter"]
    assert "bare" in a["missing_spaces_section"]

    host_idx_before = (wiki / "host" / "index.md").read_text()
    page_before = (wiki / "host" / "page.md").read_text()

    # One bounded --fix pass. It still exits 1: the safe drift is repaired but
    # the author-intent malformed findings remain (the fix never masks them).
    rc, _, err = run_cli(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 1, err

    # Author-intent findings are LEFT for human repair (never auto-rewritten).
    assert (wiki / "host" / "index.md").read_text() == host_idx_before
    assert (wiki / "host" / "page.md").read_text() == page_before
    # …but the safe structural drift WAS repaired.
    assert _md.has_section((wiki / "bare" / "index.md").read_text(), "Spaces")

    # Re-audit: the malformed findings are still reported (exactly-once — the
    # fix didn't silently swallow them); the safe drift is gone.
    rc, a2 = _audit(wiki)
    assert rc == 1
    assert a2["malformed_entries"] and a2["malformed_frontmatter"]
    assert a2["missing_spaces_section"] == []
    assert a2["drift"] == [] or all(not d["missing"] and not d["stale"] for d in a2["drift"])

    # Idempotent: a SECOND --fix changes nothing on disk.
    tracked = ["index.md", "host/index.md", "host/page.md", "bare/index.md"]
    snapshot = {p: (wiki / p).read_text() for p in tracked}
    rc, _, _ = run_cli(["--wiki", str(wiki), "audit", "--fix"])
    for p, text in snapshot.items():
        assert (wiki / p).read_text() == text, f"second --fix mutated {p}"
