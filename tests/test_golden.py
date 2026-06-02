"""The golden wiki: exact-match committed snapshots + an inventory guard.

These pin the OBSERVABLE OUTPUT of every read traversal — `list`, `files`,
`audit`, each in default and `--include-external` form — over ONE canonical
deterministic tree (`build_golden_wiki`). Exact-match against committed JSON,
not substring `in` checks, so a regression that adds spurious output, reorders
a list, or drifts a count fails here. Golden drift is only ever a reviewed
`REGEN_GOLDEN=1 pytest` diff — never silent (matching the discipline
`test_traversal_gate.py` already states).

`test_golden_wiki_covers_every_edge` is the inventory guard: it asserts each
edge catalogued in `build_golden_wiki` / `golden/INVENTORY.md` actually
surfaces in the live output, so the fixture can't silently lose coverage.
"""

from __future__ import annotations

import json

from tests.conftest import (
    assert_golden,
    build_golden_wiki,
    normalize_audit_payload,
    run_cli,
)


def _golden(tmp_path):
    return build_golden_wiki(tmp_path / "golden")


# ---------------------------------------------------------------------------
# Exact-match snapshots.
# ---------------------------------------------------------------------------


def test_golden_list_owned(tmp_path):
    wiki = _golden(tmp_path)
    rc, out, _ = run_cli(["--wiki", str(wiki), "list", "--json"])
    assert rc == 0
    assert_golden("list", json.loads(out))


def test_golden_list_include_external(tmp_path):
    wiki = _golden(tmp_path)
    rc, out, _ = run_cli(["--wiki", str(wiki), "list", "--include-external", "--json"])
    assert rc == 0
    assert_golden("list_external", json.loads(out))


def test_golden_files_owned(tmp_path):
    wiki = _golden(tmp_path)
    rc, out, _ = run_cli(["--wiki", str(wiki), "files", "--json"])
    assert rc == 0
    assert_golden("files", json.loads(out))


def test_golden_files_include_external(tmp_path):
    wiki = _golden(tmp_path)
    rc, out, _ = run_cli(["--wiki", str(wiki), "files", "--include-external", "--json"])
    assert rc == 0
    assert_golden("files_external", json.loads(out))


def test_golden_audit(tmp_path):
    wiki = _golden(tmp_path)
    rc, out, _ = run_cli(["--wiki", str(wiki), "audit", "--json"])
    # The golden wiki intentionally carries drift / broken links / etc → exit 1.
    assert rc == 1
    assert_golden("audit", normalize_audit_payload(json.loads(out)))


# ---------------------------------------------------------------------------
# Inventory guard — every catalogued edge must surface somewhere.
# ---------------------------------------------------------------------------


def test_golden_wiki_covers_every_edge(tmp_path):
    wiki = _golden(tmp_path)

    def js(*args):
        rc, out, _ = run_cli(["--wiki", str(wiki), *args, "--json"])
        return rc, json.loads(out)

    _, audit = js("audit")
    _, list_owned = js("list")
    _, list_ext = js("list", "--include-external")
    _, files_ext = js("files", "--include-external")

    owned_paths = {e["path"] for e in list_owned}
    list_ext_by = {e["path"]: e for e in list_ext}
    files_ext_by = {e["path"]: e for e in files_ext}
    drift_by = {d["ancestor"]: d for d in audit["drift"]}

    # registered-space + deep-nesting
    assert "concepts" in owned_paths
    assert "concepts/sub" in owned_paths, "deep nesting missing"
    assert "malformed-host/good" in owned_paths

    # flat-branch / no-description entry
    flat = next(e for e in list_owned if e["path"] == "flat")
    assert flat["description"] is None
    # description provenance is carried
    concepts = next(e for e in list_owned if e["path"] == "concepts")
    assert concepts["description"] == "ideas and notes"

    # stale-entry + bare-index + drift-missing
    assert "ghost" in drift_by["."]["stale"], "stale entry missing"
    assert "bare" in drift_by["."]["missing"], "bare-index not flagged missing"
    assert "drift" in drift_by["."]["missing"], "unregistered drift space missing"
    assert "bare" in audit["missing_spaces_section"]

    # malformed entries: empty href, reserved-folder href, unparseable bullet
    mal_issues = " ".join(m["issue"] for m in audit["malformed_entries"])
    assert "empty href" in mal_issues
    assert "reserved-folder href" in mal_issues
    assert "unparseable bullet" in mal_issues
    # the reserved href also normalises to a stale entry
    assert any("_meta/x" in s for d in audit["drift"] for s in d["stale"])

    # duplicate aliases
    assert any(d["alias"] == "shared-alias" for d in audit["duplicate_aliases"])

    # frontmatter: both malformed flavours surface, valid one does not
    fm_status = {e["page"]: e["status"] for e in audit["malformed_frontmatter"]}
    assert fm_status.get("notebook/snippets.md") == "malformed"
    assert fm_status.get("notebook/nonmap.md") == "non_mapping"
    assert "notebook/daily.md" not in fm_status, "valid frontmatter wrongly flagged"

    # broken wikilink surfaces; the valid [[bar]] does not
    broken_targets = {b["target"] for b in audit["broken_wikilinks"]}
    assert "definitely-missing-target" in broken_targets
    assert "bar" not in broken_targets, "valid basename wikilink wrongly broken"

    # approaching-cap driven by a USER_OVERRIDE limit
    approaching = {a["path"]: a for a in audit["approaching_cap"]}
    assert "notebook/approaching.md" in approaching
    assert approaching["notebook/approaching.md"]["cap_source"]["kind"] == "user_override"
    assert approaching["notebook/approaching.md"]["cap"] == 400

    # orphans (incl. the ## Spaces-as-content-heading page, which is NOT a space)
    assert "concepts/foo.md" in audit["orphans"]
    assert "notebook/about-spaces.md" in audit["orphans"]
    assert "notebook/about-spaces" not in owned_paths

    # shared-external + foreign-submodule classified external
    assert list_ext_by["shared/team"]["external"] is True
    assert files_ext_by["projects/vendor/index.md"]["external"] is True
    assert files_ext_by["projects/vendor/docs/nested.md"]["external"] is True
    # …and NONE of those leak into owned scope
    assert "shared/team" not in owned_paths
    assert "projects/vendor" not in owned_paths

    # reserved dirs are pruned: _archives never appears as a file
    assert "_archives/old.md" not in files_ext_by

    # self-maintenance signals (informational): notebook is a hub promote
    # candidate (6 direct content pages); no prune candidates (no hot.md).
    hubs = {c["path"]: c for c in audit["promote_candidates"] if c["kind"] == "hub"}
    assert "notebook" in hubs and hubs["notebook"]["pages"] == 6
    assert audit["prune_candidates"] == []
