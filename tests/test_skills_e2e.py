"""Skill-procedure fixtures — does the *documented mechanical procedure* keep
the wiki contract-clean?

These do NOT test the LLM's free-form prose. They execute the **deterministic
CLI steps** the skills prescribe (the parts a skill says to run verbatim) and
assert the contract the skill promises: "if you follow the steps, the wiki
stays contract-clean, and what the writer produced the reader can find."

- ws-update "save this" (happy path): classify (`space list`) → dedup
  (`space files`) → size check (`check-size`) → write → audit. The saved page
  is discoverable and its wikilinks resolve; the final tree matches a snapshot.
- ws-update "save this" that OVERFLOWS a cap: the deterministic remediation
  order's first rung (split into a hub + siblings), each re-checked under cap,
  audit clean.
- ws-tend normalize: status (`space files`) → audit → ONE scope-only normalize
  pass → re-audit. The pass touches SCOPE pages only — never child spaces
  (autonomy) or externals (the CLI's owned-files walk excludes them: trust
  scope). Re-audit is clean.

This is the producer=consumer guarantee at the judgment layer: the skill never
instructs an action that the same tool's `audit` then condemns.
"""

from __future__ import annotations

import json

from tests.conftest import (
    assert_golden,
    make_wiki as _make_wiki,
    run_cli as _run,
    write_tree,
)


# ---------------------------------------------------------------------------
# ws-update — "save this" happy path.
# ---------------------------------------------------------------------------


def test_ws_update_save_this_happy_path(tmp_path):
    wiki = _make_wiki(tmp_path)
    # An existing placement candidate (the skill's step-6 classifier finds it).
    rc, _, err = _run(["--wiki", str(wiki), "add", "notes", "--description", "concepts"])
    assert rc == 0, err

    # Step 6: classify — `space list --json` enumerates owned candidates.
    _, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    assert "notes" in {e["path"] for e in json.loads(out)}

    # Step 5: dedup — `space files --json` shows no existing match.
    _, out, _ = _run(["--wiki", str(wiki), "files", "--json"])
    assert not any("python-typing" in e["path"] for e in json.loads(out))

    # Step 7: size discipline — materialize projected content, ask the CLI.
    page_rel = "notes/python-typing.md"
    content = (
        "---\ntags: [python]\n---\n"
        "# Python typing\n\nPrefer `X | None` over `Optional[X]` on 3.10+.\n"
    )
    rc, verdict, _ = _run(["--wiki", str(wiki), "check-size", page_rel], stdin=content)
    assert rc == 0 and verdict.startswith("OK"), verdict

    # Step 8: write the page (a content page in an existing space — no new
    # `## Spaces` entry needed, so no `space add`).
    write_tree(wiki, {page_rel: content})

    # Step 10: audit closes clean.
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out

    # The reader finds what the writer produced.
    _, out, _ = _run(["--wiki", str(wiki), "files", "--json"])
    assert page_rel in {e["path"] for e in json.loads(out)}

    # A sibling links to it; the wikilink resolves (audit still clean).
    write_tree(wiki, {"notes/ref.md": "# Ref\n\nsee [[python-typing]] for the rule\n"})
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "! broken wikilink" not in out

    # Final tree matches the committed snapshot.
    _, out, _ = _run(["--wiki", str(wiki), "files", "--json"])
    assert_golden("skill_save_files", json.loads(out))


# ---------------------------------------------------------------------------
# ws-update — "save this" that overflows a cap (split remediation).
# ---------------------------------------------------------------------------


def test_ws_update_save_overflows_cap_then_split_remediation(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "notes"])
    assert rc == 0, err
    write_tree(wiki, {
        "_meta/limits.md": (
            "# Size limits\n\n"
            "| Pattern       | Cap (chars) |\n"
            "|---------------|-------------|\n"
            "| notes/big.md  |         200 |\n"
        ),
    })

    section_a = "## Section A\n\n" + "alpha " * 40
    section_b = "## Section B\n\n" + "beta " * 40
    projected = f"# Big\n\n{section_a}\n\n{section_b}\n"

    # The rejection: check-size says OVER and exits 1 (never truncate).
    rc, verdict, _ = _run(["--wiki", str(wiki), "check-size", "notes/big.md"], stdin=projected)
    assert rc == 1 and verdict.startswith("OVER"), verdict

    # Remediation rung 1 — split: the page becomes a small hub linking to
    # siblings that carry the H2 sections verbatim. Each is re-checked.
    hub = "# Big\n\nSplit into:\n\n- [[big-a]]\n- [[big-b]]\n"
    big_a = f"# Big A\n\n{section_a}\n"
    big_b = f"# Big B\n\n{section_b}\n"
    for rel, text in (("notes/big.md", hub), ("notes/big-a.md", big_a), ("notes/big-b.md", big_b)):
        rc, verdict, _ = _run(["--wiki", str(wiki), "check-size", rel], stdin=text)
        assert rc == 0 and verdict.startswith("OK"), f"{rel}: {verdict}"

    write_tree(wiki, {"notes/big.md": hub, "notes/big-a.md": big_a, "notes/big-b.md": big_b})

    # Result: under cap AND audit-clean; the hub's wikilinks resolve and the
    # section content survived the split.
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "! broken wikilink" not in out
    assert "Section A" in (wiki / "notes" / "big-a.md").read_text()
    assert "Section B" in (wiki / "notes" / "big-b.md").read_text()


# ---------------------------------------------------------------------------
# ws-tend — normalize respects scope (children + externals untouched).
# ---------------------------------------------------------------------------


def _alias_to_canonical(taxonomy_text: str) -> dict[str, str]:
    """Parse `_meta/taxonomy.md`'s `| `canonical` | purpose | alias, … |` rows
    into an alias→canonical map (the mechanical input ws-tend normalizes by)."""
    mapping: dict[str, str] = {}
    for line in taxonomy_text.splitlines():
        if line.count("|") < 3 or "`" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        canonical = cells[0].strip("` ")
        if not canonical or canonical.lower() == "tag":
            continue
        for alias in cells[2].split(","):
            alias = alias.strip().strip("`").strip()
            if alias:
                mapping[alias] = canonical
    return mapping


def test_ws_tend_normalize_respects_scope_and_reaudits_clean(tmp_path):
    wiki = tmp_path / "wiki"
    taxonomy = (
        "# Tag Taxonomy\n\n"
        "## Domain Tags\n\n"
        "| Tag | Purpose | Aliases |\n"
        "|---|---|---|\n"
        "| `machine-learning` | ML topics | ml |\n"
    )
    write_tree(wiki, {
        # Root registers an owned child space and an external mount.
        "index.md": (
            "# wiki\n\n## What this space is\n\nroot\n\n## Spaces\n\n"
            "- [Child](child/index.md)\n"
            "- [Team](shared/team/index.md)\n"
        ),
        "_meta/taxonomy.md": taxonomy,
        # SCOPE-level page carrying the alias tag — the only one normalize may touch.
        "note.md": "---\ntags: [ml]\n---\n# Note\n\nscope-level page\n",
        # Child space (autonomous — its tags are its own).
        "child/index.md": "# Child\n\n## Spaces\n\n",
        "child/sub.md": "---\ntags: [ml]\n---\n# Sub\n\nchild-space page\n",
        # External mount under shared/ (trust scope — never written by default).
        "shared/team/index.md": "# Team\n\n## Spaces\n\n",
        "shared/team/ext.md": "---\ntags: [ml]\n---\n# Ext\n\nexternal page\n",
    })

    # Step 4 (status) + step 5 (audit), read-only over owned scope.
    _, files_out, _ = _run(["--wiki", str(wiki), "files", "--json"])
    owned = {e["path"] for e in json.loads(files_out)}
    # The CLI's owned walk excludes the external page entirely (trust scope).
    assert "shared/team/ext.md" not in owned
    assert {"note.md", "child/sub.md"} <= owned

    rc, _, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, "fixture should start contract-clean"

    # Step 6 (normalize: tags) — the deterministic scope-only file set:
    #   owned `.md` files, minus index.md, minus anything inside a registered
    #   child space (child autonomy). Externals are already gone (above).
    _, list_out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    child_spaces = [e["path"] for e in json.loads(list_out)]
    scope_pages = [
        p for p in owned
        if p.endswith(".md") and p != "index.md"
        and not any(p.startswith(cs + "/") for cs in child_spaces)
    ]
    assert scope_pages == ["note.md"], scope_pages

    # Apply the taxonomy mapping to SCOPE pages only (one safe normalize pass).
    mapping = _alias_to_canonical(taxonomy)
    assert mapping == {"ml": "machine-learning"}
    for rel in scope_pages:
        f = wiki / rel
        text = f.read_text()
        for alias, canonical in mapping.items():
            text = text.replace(f"tags: [{alias}]", f"tags: [{canonical}]")
        f.write_text(text)

    # Re-audit: clean.
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out

    # The contract the plan pins: SCOPE normalized, children + externals untouched.
    assert "tags: [machine-learning]" in (wiki / "note.md").read_text()
    assert "tags: [ml]" in (wiki / "child" / "sub.md").read_text(), "child space wrongly normalized"
    assert "tags: [ml]" in (wiki / "shared" / "team" / "ext.md").read_text(), "external wrongly normalized"
