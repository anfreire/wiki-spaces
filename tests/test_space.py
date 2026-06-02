"""End-to-end tests for the space CLI against temp wikis."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wiki_spaces import _common, _log, _md, _model, init_wiki, space

from tests.conftest import (
    HAS_GIT as _HAS_GIT,
    make_git_config as _make_git_config,
    make_git_repo as _make_git_repo,
    make_git_repo_with_bare_index as _make_git_repo_with_bare_index,
    make_space_dir as _make_space_dir,
    make_wiki as _make_wiki,
    run_cli as _run,
)


# Adapters over the model-owned traversals, returning the same shapes the
# retired private `space.py` walkers did. The consumer/owned traversals now
# live in `_model`; these keep the long-standing unit tests below pointed at
# behaviour (drift hidden, external opt-in, reserved-dir pruning, …) rather
# than at a specific private function.
def _contract_spaces(wiki, *, include_external: bool = False):
    return [
        (cs.path, cs.external)
        for cs in _model.discover_consumer_spaces(
            wiki, include_external=include_external
        )
    ]


def _contract_md_files(wiki, *, include_external: bool = False):
    return [
        (cf.path, cf.external)
        for cf in _model.discover_md_files(
            wiki, include_external=include_external
        )
    ]


def _owned_spaces(wiki, *, include_external: bool = False):
    return [
        n.path
        for n in _model.discover_nodes(wiki, include_external=include_external)
        if _model.is_space_node(n, include_external)
    ]


# ---------- resolve_wiki (strict / repair) / _validate_rel_path ----------

def test_audit_strict_resolver_rejects_bare_index_via_explicit_path(tmp_path):
    """PR-D: audit is read-only and uses the strict resolver. A folder with
    `index.md` but no `## Spaces` is not a wiki — audit refuses to operate."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, _, err = _run(["--wiki", str(wiki), "audit"])
    assert rc == 2
    assert "Spaces" in err


def test_audit_strict_resolver_rejects_bare_index_via_cwd(tmp_path, monkeypatch):
    """Same contract through the CWD fallback: the strict resolver walks up
    looking for `index.md` + `## Spaces` together. An ancestor with
    `index.md` but no `## Spaces` is invisible to the read-only path."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    # Move into the wiki so the CWD fallback runs.
    monkeypatch.chdir(wiki)
    # Don't pass --wiki; clear any inherited config.
    monkeypatch.setattr(_common, "wiki_path", lambda: None)
    rc, _, err = _run(["audit"])
    assert rc == 2
    assert "Spaces" in err


def test_audit_strict_resolver_rejects_bare_index_via_config(tmp_path, monkeypatch):
    """Same contract through the `wiki` config key: the strict resolver
    rejects a configured wiki that has `index.md` but no `## Spaces`.
    Without this test, a config-pointed wiki missing `## Spaces` could silently pass
    audit even though no other resolution path accepts it."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    # Don't pass --wiki; have the config point at the bare wiki instead.
    monkeypatch.setattr(_common, "wiki_path", lambda: wiki)
    rc, _, err = _run(["audit"])
    assert rc == 2
    assert "Spaces" in err


def test_strict_resolver_refuses_relative_config_path(tmp_path, monkeypatch):
    """PR-D + OC r12: a relative `wiki` config path would silently join to
    CWD via `.resolve()`, picking a different wiki per-invocation. doctor
    rejects relatives upfront; the resolvers must too — otherwise a
    misconfigured wiki silently follows the agent's working directory."""
    monkeypatch.setattr(_common, "wiki_path", lambda: Path("relative/wiki"))
    monkeypatch.chdir(tmp_path)
    assert _common._resolve_wiki(None, require_section=True) is None


def test_repair_resolver_refuses_relative_config_path(tmp_path, monkeypatch):
    """Same hard-stop on the repair side. Without this, `space add foo`
    typed against a relative `wiki = Wiki` config would target whichever
    `Wiki/` directory happens to sit under the current working directory."""
    monkeypatch.setattr(_common, "wiki_path", lambda: Path("relative/wiki"))
    monkeypatch.chdir(tmp_path)
    assert _common._resolve_wiki(None, require_section=False) is None


def test_repair_resolver_refuses_invalid_config_does_not_fall_through_to_cwd(
    tmp_path, monkeypatch
):
    """PR-D: when `wiki` config points at a path with no `index.md`, the
    repair resolver MUST refuse — not silently fall through to a CWD
    ancestor wiki. The strict path (`_resolve_wiki(..., require_section=True)`)
    got this right at the start; repair must match. Otherwise a write
    command (`space add`, `space mount`, `space remove`, `audit --fix`,
    `init --adopt`) typed against a broken config would write to whatever
    wiki the user happened to be cd'd into — masking a real config-side
    spec violation."""
    (tmp_path / "cwd_wiki").mkdir()
    cwd_wiki = _make_wiki(tmp_path / "cwd_wiki")
    bad_cfg = tmp_path / "does_not_exist"
    monkeypatch.setattr(_common, "wiki_path", lambda: bad_cfg)
    monkeypatch.chdir(cwd_wiki)
    # No --wiki passed: resolver should hit the config path, see no index.md,
    # and refuse — not redirect to cwd_wiki.
    assert _common._resolve_wiki(None, require_section=False) is None


def test_validate_rel_path_rejects_dot_dot():
    ok, err = space._validate_rel_path("../escape")
    assert not ok
    assert err is not None


def test_validate_rel_path_rejects_absolute():
    ok, err = space._validate_rel_path("/absolute")
    assert not ok


def test_validate_rel_path_accepts_nested():
    ok, err = space._validate_rel_path("projects/foo")
    assert ok and err is None


def test_validate_rel_path_rejects_hidden_segments():
    """Hidden directories (dot-prefixed) are skipped by every consumer
    walker per CONVENTIONS / Reserved top-level folder names. The
    producer must not register them either, or the entry lands in
    `## Spaces` with no consumer to read it (producer/consumer break).
    Refuse every hidden segment, not just `.git`."""
    for bad in (".archive", ".config", ".cache", ".obsidian", ".hidden"):
        ok, err = space._validate_rel_path(bad)
        assert not ok, f"hidden segment {bad!r} accepted"
        assert "hidden" in err or ".git" in err
    # Nested hidden also rejected.
    ok, err = space._validate_rel_path("projects/.cache")
    assert not ok


def test_validate_rel_path_rejects_reserved_underscore_segments():
    """`_archives/` and `_meta/` carry tool-level behavior — `_archives`
    is excluded from audit walks; `_meta` holds config files like
    `_meta/limits.md`. Neither should be registered as a space; a
    `## Spaces` entry there would never be reachable by consumers."""
    for bad in ("_archives", "_meta", "projects/_archives", "_meta/foo"):
        ok, err = space._validate_rel_path(bad)
        assert not ok, f"reserved segment {bad!r} accepted"
        assert "reserved" in err


def test_validate_rel_path_rejects_dot_git():
    ok, err = space._validate_rel_path("projects/.git")
    assert not ok


def test_validate_rel_path_rejects_control_characters():
    """Newlines and other control characters split a `## Spaces` entry
    across markdown lines and break the parser. Refuse them in path
    segments — `space add "foo\\nbar"` should fail at validation."""
    for bad in ("foo\nbar", "foo\tbar", "foo\x00bar", "foo\rbar"):
        ok, err = space._validate_rel_path(bad)
        assert not ok, f"control-char path {bad!r} accepted"
        assert "control" in err or "newline" in err


def test_validate_rel_path_rejects_above_0x20_line_separators():
    """`\\x85` (NEL), `\\u2028` (LS), and `\\u2029` (PS) are line boundaries for
    `str.splitlines()` (the `## Spaces` consumer) but sit ABOVE 0x20, so an
    `ord(c) < 0x20` check let them through. A path segment carrying one becomes
    an href the producer writes but the consumer splits — a registered space the
    walker can never read (producer=consumer break). Refuse them."""
    for ch in ("\x85", "\u2028", "\u2029"):
        ok, err = space._validate_rel_path(f"foo{ch}bar")
        assert not ok, f"line-separator path foo{ch!r}bar accepted"
        assert "control" in err or "newline" in err


def test_validate_entry_text_rejects_link_metachars_and_newlines():
    """Direct test of the `_validate_entry_text` helper used by
    `cmd_mount` for `--name` / `--description`. `]` and `)` break the
    markdown link syntax; newlines split the entry across lines."""
    for bad in ("label]with-bracket", "label)with-paren", "with\nnewline"):
        ok, err = space._validate_entry_text(bad, field="--name")
        assert not ok, f"entry text {bad!r} accepted"
        assert "may not contain" in err
    # Empty / None pass through.
    ok, _ = space._validate_entry_text(None, field="--name")
    assert ok
    ok, _ = space._validate_entry_text("", field="--name")
    assert ok
    # Normal text passes.
    ok, _ = space._validate_entry_text("Team Foo's Wiki", field="--name")
    assert ok


def test_mount_refuses_name_with_link_metachar(tmp_path):
    """`space mount ... --name "broken]label"` would render
    `- [broken]label](...)` which the consumer parser truncates at the
    first `]`. Refuse at argparse-validation time."""
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "external"
    src.mkdir()
    (src / "index.md").write_text("# ext\n\n## Spaces\n\n")
    rc, _, err = _run([
        "--wiki", str(wiki),
        "mount", str(src), "shared/ext", "--mode", "symlink",
        "--name", "broken]label",
    ])
    assert rc == 2
    assert "may not contain" in err


def test_validate_rel_path_rejects_markdown_link_metacharacters():
    """A path segment with `)`, `]`, `(`, `[`, `{`, or `}` would write a
    `## Spaces` entry like `- [foo)bar/](foo)bar/index.md)` that the
    consumer parser's `ENTRY_RE` can't read. Refuse the path before any
    FS mutation so producers can't emit entries consumers ignore."""
    for c in space._SPACES_HREF_METACHARS:
        bad = "foo" + c + "bar"
        ok, err = space._validate_rel_path(bad)
        assert not ok, f"path {bad!r} accepted despite containing link metachars"
        assert "Markdown link metacharacters" in err
    # And in nested segments.
    ok, err = space._validate_rel_path("projects/foo)bar")
    assert not ok
    # Plain paths still pass.
    ok, _ = space._validate_rel_path("projects/foo-bar.baz")
    assert ok


def test_add_refuses_path_with_markdown_link_metacharacter(tmp_path):
    """`space add` integrates the validator — refuse before mkdir."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo)bar"])
    assert rc == 2
    assert "Markdown link metacharacters" in err
    assert not (wiki / "foo)bar").exists()


def test_validate_rel_path_uses_shared_metachar_constant():
    """Single-source-of-truth invariant: the producer's forbidden set lives
    in ONE module-level constant `_SPACES_HREF_METACHARS`, shared with the
    consumer/audit path. Pin the set and that the producer rejects every
    char in it, so a future edit to the policy propagates to both paths (or
    fails this test)."""
    assert set(space._SPACES_HREF_METACHARS) == set("[](){}")
    for c in space._SPACES_HREF_METACHARS:
        ok, err = space._validate_rel_path("foo" + c + "bar")
        assert not ok, f"producer accepted segment with metachar {c!r}"
        assert "Markdown link metacharacters" in err


# ---------- space add ----------

def test_add_creates_space_and_updates_parent(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo", "--description", "foo space"])
    assert rc == 0
    assert (wiki / "foo" / "index.md").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert len(entries) == 1
    assert entries[0].href == "foo/index.md"


def test_add_without_description_omits_what_this_space_is_section(tmp_path):
    """PR-A scrub: `space add foo` with no `--description` writes a child
    `index.md` that contains `# foo` + `## Spaces` only — no
    `## What this space is` header and no `<one paragraph describing this space>`
    placeholder string. Both come from `_common.new_index_md`."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0, err
    body = (wiki / "foo" / "index.md").read_text()
    assert "## What this space is" not in body
    assert "<one paragraph describing this space>" not in body
    assert "## Spaces" in body


def test_add_is_idempotent(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert len(entries) == 1


def test_add_inserts_spaces_and_registers_against_bare_ancestor(tmp_path):
    """`space add` against a bare-`index.md` ancestor inserts `## Spaces`
    into the ancestor as the first mutation step (via the chain helper)
    and registers the new child — no refuse, no manual setup step. Same
    contract as promote (PR-C) and mount; replaces the pre-v1 refusal
    behavior."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0, err
    # `foo/` exists with its own `## Spaces`.
    assert (wiki / "foo" / "index.md").is_file()
    assert "## Spaces" in (wiki / "foo" / "index.md").read_text()
    # The ancestor's bare-`index.md` got `## Spaces` inserted AND `foo/` registered.
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "foo/" in e.href for e in entries)


def test_add_upgrade_parent_flag_removed(tmp_path):
    """The --upgrade-parent flag was removed; argparse rejects it."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    with pytest.raises(SystemExit):
        _run(["--wiki", str(wiki), "add", "foo", "--upgrade-parent"])


def test_add_nested_path_walks_up_to_nearest_space(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    assert (wiki / "projects" / "foo" / "index.md").exists()
    # projects/ has no index.md; nearest ancestor space is wiki root
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries[0].href == "projects/foo/index.md"


def test_add_rejects_dot_dot(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "../escape"])
    assert rc == 2


# ---------- PR-D chain helper coverage ----------

def test_ensure_chain_walks_multi_level_and_registers_each_step(tmp_path):
    """`space add foo/bar` where `foo/index.md` exists bare AND wiki root
    has no `## Spaces`: the chain helper walks (bar, foo), (foo, wiki),
    inserting `## Spaces` and registering at each step."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")  # bare; no `## Spaces`
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc == 0, err
    # New leaf exists.
    assert (wiki / "foo" / "bar" / "index.md").is_file()
    # foo/index.md got `## Spaces` and `bar/` registered.
    foo_text = (wiki / "foo" / "index.md").read_text()
    assert "## Spaces" in foo_text
    foo_entries = _md.parse_section_entries(foo_text, "Spaces")
    assert any(e.href and "bar/" in e.href for e in foo_entries)
    # Wiki root got `## Spaces` and `foo/` registered.
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    root_entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "foo/" in e.href for e in root_entries)


def test_preflight_chain_external_unit(tmp_path):
    """Direct unit test of the new shared preflight. Pins producer=consumer:
    it walks the SAME (ancestor, child) edges as
    `_ensure_spaces_chain_and_register` / `_preflight_chain_caps` and refuses
    on the first EXTERNAL ancestor index it would write."""
    wiki = _make_wiki(tmp_path)
    # (1) An external space (under shared/) WITH `## Spaces`; a leaf under it.
    team = wiki / "shared" / "team"
    team.mkdir(parents=True)
    (team / "index.md").write_text("# team\n\n## Spaces\n\n")
    with pytest.raises(space.ChainExternalRefusal) as ei:
        space._preflight_chain_external(wiki, team / "nested")
    assert ei.value.ancestor == team
    # (2) force_external suppresses the raise.
    assert (
        space._preflight_chain_external(
            wiki, team / "nested", force_external=True
        )
        is None
    )
    # (3) A dest whose nearest ancestor space is the OWNED wiki root
    # (`shared/` here is just a plain folder) is allowed — no raise.
    assert space._preflight_chain_external(wiki, wiki / "shared" / "foo") is None
    # (4) leaf_space == wiki_root is a no-op.
    assert space._preflight_chain_external(wiki, wiki) is None


def test_ensure_chain_rolls_back_added_entries_on_mid_walk_failure(tmp_path, monkeypatch):
    """Wiki has `foo/index.md` (bare) and root has no `## Spaces`.
    Sabotage writes to the wiki root's `index.md` only — the deep edge
    (register `bar` in `foo`) succeeds via the real helper, the root edge
    fails, and the rollback path runs the real helper too (removing `bar`
    from foo). Assert: foo's `## Spaces` insertion stays (append-only,
    non-destructive); `bar` is rolled back out of foo's `## Spaces`."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")

    real = space._atomic_mutate_index
    root_index = (wiki / "index.md").resolve()

    def patched(ancestor, ancestor_index, mutate_fn):
        if ancestor_index.resolve() == root_index:
            return 1, "simulated second-edge failure"
        return real(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space._core, "_atomic_mutate_index", patched)

    rc, _, err = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc == 1
    assert "simulated second-edge failure" in err
    # bar/ FS creation was rolled back.
    assert not (wiki / "foo" / "bar").exists()
    # foo's `## Spaces` insertion survives the rollback (append-only).
    foo_text = (wiki / "foo" / "index.md").read_text()
    assert "## Spaces" in foo_text
    # `bar` was rolled back out of foo's `## Spaces`.
    foo_entries = _md.parse_section_entries(foo_text, "Spaces")
    assert not any(e.href and "bar" in e.href for e in foo_entries)


def test_ensure_section_at_only_touches_that_space(tmp_path):
    """`_ensure_section_at(wiki/foo)` mutates `wiki/foo/index.md` only;
    it does NOT walk up or touch `wiki/index.md`."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")
    root_before = (wiki / "index.md").read_text()
    space._ensure_section_at(wiki / "foo", wiki)
    # foo got `## Spaces`.
    assert "## Spaces" in (wiki / "foo" / "index.md").read_text()
    # root is untouched.
    assert (wiki / "index.md").read_text() == root_before


def test_cmd_add_existing_target_ensures_target_has_spaces_section(tmp_path):
    """`space add foo` against a pre-existing bare `foo/index.md` leaves
    foo with a `## Spaces` section so re-registered existing targets
    carry `## Spaces` after the call."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n")  # bare; no `## Spaces`
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0, err
    assert "## Spaces" in (wiki / "foo" / "index.md").read_text()


def test_add_dry_run_prints_plan_no_fs_changes(tmp_path):
    """`space add --dry-run` previews the chain-helper plan and touches
    nothing on disk — no new directory, no `## Spaces` insertion into a
    bare ancestor."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    before = (wiki / "index.md").read_text()
    rc, out, _ = _run(["--wiki", str(wiki), "add", "newproj", "--dry-run"])
    assert rc == 0
    assert "(dry-run)" in out
    assert not (wiki / "newproj").exists()
    # Ancestor's bare `index.md` was NOT touched (dry-run preview only).
    assert (wiki / "index.md").read_text() == before


def test_wiki_flag_accepted_after_subcommand(tmp_path):
    """LLM2.5 / B2: argparse subparser convention required `--wiki`
    before the subcommand, so every user typing `audit --wiki <path>`
    (the natural English order) hit an error. Pre-normalization lifts
    `--wiki <path>` to argv[0] so both positions work."""
    wiki = _make_wiki(tmp_path)
    # Natural English order: subcommand first.
    rc, _, err = _run(["audit", "--wiki", str(wiki)])
    assert rc == 0, err


def test_wiki_flag_eq_form_accepted_after_subcommand(tmp_path):
    """The `--wiki=<path>` form (equals sign) also normalizes."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["audit", f"--wiki={wiki}"])
    assert rc == 0, err


def test_space_caps_lists_rules_with_sources(tmp_path):
    """Phase 9: `space caps` lists the active cap table so the producer
    can see the discipline without hitting OVER. Built-in defaults
    have no file/line; user overrides carry the source location."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir(parents=True, exist_ok=True)
    (wiki / "_meta" / "limits.md").write_text(
        "# Limits\n\n| Pattern | Cap |\n|---|---|\n| concepts/*.md | 8000 |\n",
    )
    rc, out, _ = _run(["--wiki", str(wiki), "caps"])
    assert rc == 0
    assert "concepts/*.md" in out
    assert "8000" in out
    assert "user override" in out
    # Built-in defaults included too.
    assert "index.md" in out
    assert "5000" in out
    assert "built-in default" in out


def test_space_caps_json(tmp_path):
    """JSON output includes structured CapSource — kind, file, line —
    so machine consumers can programmatically inspect the cap table."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "caps", "--json"])
    assert rc == 0
    payload = _json.loads(out)
    assert payload["rules"]
    kinds = {r["kind"] for r in payload["rules"]}
    assert "builtin_default" in kinds


def test_space_add_prints_effective_cap_on_success(tmp_path):
    """Cap-on-success: `space add foo` prints the cap the new
    `foo/index.md` lives under so the user doesn't discover it by
    hitting OVER on a future write."""
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    assert "5000" in out                                  # index.md cap
    assert "cap:" in out                                  # cap source label


def test_space_add_prints_nested_scope_cap_not_root(tmp_path):
    """The cap-on-success line must report the cap actually enforced — the
    nearest `_meta/limits.md` scope, the same one `enforce_size_cap` and
    `space audit` use (HANDBOOK producer=consumer). When a child space
    declares its own caps, `add child/grand` enforces the child's cap but
    must also DISPLAY it; reporting the root built-in (5000) while enforcing
    the child's override is the same value from two sources.
    """
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    (wiki / "child" / "_meta").mkdir(parents=True)
    (wiki / "child" / "_meta" / "limits.md").write_text(
        "# Limits\n\n| pattern | cap |\n|---|---|\n| **/index.md | 1234 |\n"
    )
    rc, out, err = _run(["--wiki", str(wiki), "add", "child/grand"])
    assert rc == 0, out + err
    assert "1234" in out
    assert "5000" not in out


def test_cmd_add_from_template_renders_index(tmp_path):
    """Phase 7: `space add --from-template <path>` opts the new space
    into a parent-supplied template. Placeholders are substituted; the
    `## Spaces` navigation contract is guaranteed at the end if the
    template doesn't include it. Default (no flag) stays barren — the
    autonomy principle is preserved (conventions don't propagate
    silently; they're explicitly seeded)."""
    wiki = _make_wiki(tmp_path)
    template = wiki / "_template.md"
    template.write_text(
        "---\ntitle: >-\n  {{ title }}\ntags: []\ncreated: {{ now }}\n---\n"
        "\n# {{ title }}\n\n{{ description }}\n",
        encoding="utf-8",
    )
    rc, _, err = _run([
        "--wiki", str(wiki), "add", "concepts",
        "--description", "Cross-cutting concepts.",
        "--from-template", "_template.md",
    ])
    assert rc == 0, err
    body = (wiki / "concepts" / "index.md").read_text()
    assert "title: >-" in body                            # frontmatter present
    assert "concepts" in body                             # {{ title }} substituted
    assert "Cross-cutting concepts." in body              # {{ description }} substituted
    assert "{{ title }}" not in body                      # placeholder gone
    assert "## Spaces" in body                            # appended


def test_cmd_add_without_template_stays_barren(tmp_path):
    """Default `space add` (no template flag) writes a barren index —
    no frontmatter, no opt-in conventions. Phase 7 makes seeding
    explicit; without the flag, autonomy stays intact."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "concepts"])
    assert rc == 0
    body = (wiki / "concepts" / "index.md").read_text()
    # Barren: no frontmatter delimiters.
    assert not body.startswith("---")
    assert "## Spaces" in body


def test_cmd_add_summary_lands_in_template_frontmatter(tmp_path):
    """Phase 10: `--summary` substitutes the template's `{{ summary }}`
    placeholder so the value lands in frontmatter (which the audit's
    page-index reads). Closes LLM3.4's complaint that there was no
    explicit way to set frontmatter `summary:` via the CLI."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_template.md").write_text(
        "---\ntitle: >-\n  {{ title }}\nsummary: >-\n  {{ summary }}\n---\n"
        "\n# {{ title }}\n",
        encoding="utf-8",
    )
    rc, _, err = _run([
        "--wiki", str(wiki), "add", "concepts",
        "--from-template", "_template.md",
        "--summary", "Cross-cutting concepts the team uses.",
    ])
    assert rc == 0, err
    body = (wiki / "concepts" / "index.md").read_text()
    assert "Cross-cutting concepts the team uses." in body
    assert "{{ summary }}" not in body


def test_cmd_add_summary_without_template_refuses(tmp_path):
    """`--summary` without `--from-template` is structurally
    meaningless (no frontmatter to write into). Refuse loudly rather
    than silently dropping the value."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "add", "concepts",
        "--summary", "anything",
    ])
    assert rc == 2
    assert "from-template" in err
    assert not (wiki / "concepts").exists()


def test_cmd_add_from_template_missing_file(tmp_path):
    """A missing template path errors cleanly without creating the space."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "add", "concepts",
        "--from-template", "no-such-template.md",
    ])
    assert rc == 2
    assert "template" in err.lower()
    assert not (wiki / "concepts").exists()


def test_cmd_add_description_goes_to_child_and_parent_entry(tmp_path):
    """`space add foo --description X` writes X to BOTH the child's
    `## What this space is` body AND the parent's `## Spaces` entry
    note. Symmetric with `space mount --description`; ensures `space
    list --json description` downstream sees the same text the user
    typed (previously the entry note was empty, so list reported
    `description: null` even for spaces created with `--description`)."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo", "--description", "foo says hi"])
    assert rc == 0, err
    # Child's body carries the description.
    foo_text = (wiki / "foo" / "index.md").read_text()
    assert "## What this space is" in foo_text
    assert "foo says hi" in foo_text
    # Parent's `## Spaces` entry ALSO carries the description trailer.
    root_text = (wiki / "index.md").read_text()
    entries = _md.parse_section_entries(root_text, "Spaces")
    foo_entry = next(e for e in entries if e.href and "foo/" in e.href)
    assert (foo_entry.description or "").strip() == "foo says hi"


# ---------- space remove ----------

def test_remove_strips_entry_and_deletes(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    rc, out, _ = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0
    assert not (wiki / "foo").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries == []


def test_remove_dry_run_changes_nothing(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    before = (wiki / "index.md").read_text()
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo", "--dry-run"])
    assert rc == 0
    assert (wiki / "foo").exists()
    assert (wiki / "index.md").read_text() == before


def test_remove_refuses_nonempty_without_force(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    (wiki / "foo" / "extra.md").write_text("user content")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 2
    assert (wiki / "foo").exists()


def test_remove_with_force_strips_nonempty(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    (wiki / "foo" / "extra.md").write_text("user content")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo", "--force"])
    assert rc == 0
    assert not (wiki / "foo").exists()


def test_remove_refuses_wiki_root(tmp_path):
    wiki = _make_wiki(tmp_path)
    # Try to remove the wiki root itself — should refuse
    rc, _, err = _run(["--wiki", str(wiki), "remove", "."])
    # "." is rejected by validator before reaching the root check
    assert rc == 2


def test_remove_against_bare_ancestor_inserts_section_and_removes(tmp_path):
    """v1 behavior: when the ancestor lacks `## Spaces` AND the target
    space exists with no extra content AND we're not in dry-run, remove
    proceeds. `_ensure_section_at` inserts an empty `## Spaces` into the
    ancestor (so the entry-removal step has a section to operate on),
    then the target's entry-remove is a no-op (no entry was registered),
    then the target directory is deleted. Final state: ancestor has an
    empty `## Spaces`, target is gone."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0, err
    assert not (wiki / "foo").exists()
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text


def test_remove_does_not_mutate_on_nonempty_refusal(tmp_path):
    """Refusal paths (non-empty target without --force, external scope,
    etc.) must NOT mutate the ancestor. `_ensure_section_at` runs only
    after every refusal check. So a remove that bounces on the non-empty
    target check leaves the bare-`index.md` ancestor untouched."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    (wiki / "foo" / "extra.md").write_text("user content")
    before = (wiki / "index.md").read_text()
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 2
    assert "--force" in err  # non-empty target wins over any other error
    assert (wiki / "foo" / "extra.md").exists()
    # Ancestor untouched — no `## Spaces` insertion on a refused remove.
    assert (wiki / "index.md").read_text() == before


def test_remove_dry_run_does_not_mutate_bare_ancestor(tmp_path):
    """Dry-run must NOT insert `## Spaces` into a bare ancestor either."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    before = (wiki / "index.md").read_text()
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo", "--dry-run"])
    assert rc == 0
    assert (wiki / "foo").exists()
    assert (wiki / "index.md").read_text() == before


def test_remove_normalized_href_match(tmp_path):
    """`space remove foo` must remove an existing `- [foo/](foo/)` entry even
    though its href is `foo/`, not `foo/index.md`. Audit normalizes these
    forms; add/remove must match audit's semantics."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [foo/](foo/)\n")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0
    assert not (wiki / "foo").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries == []


def test_add_normalized_href_idempotent(tmp_path):
    """`space add foo` against `- [foo/](foo/)` must NOT duplicate. Different
    href forms (`foo/` vs `foo/index.md`) identify the same child space."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [foo/](foo/)\n")
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert len(entries) == 1


def test_add_creates_child_with_spaces_section(tmp_path):
    """A space created by `space add` must itself have `## Spaces` from t=0
    so that nested `space add foo/bar` works without a second step."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    child_text = (wiki / "foo" / "index.md").read_text()
    assert "## Spaces" in child_text
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc == 0
    assert (wiki / "foo" / "bar" / "index.md").exists()


# ---------- space audit ----------

def test_audit_reports_missing_direct_child(tmp_path):
    wiki = _make_wiki(tmp_path)
    # Create a space on disk without adding to ## Spaces
    (wiki / "orphan").mkdir()
    (wiki / "orphan" / "index.md").write_text("# orphan")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0
    assert "orphan" in out


def test_audit_clean_returns_zero(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    assert "OK" in out


def test_audit_skips_external_shared(tmp_path):
    wiki = _make_wiki(tmp_path)
    shared = wiki / "shared" / "team-foo"
    shared.mkdir(parents=True)
    (shared / "index.md").write_text("# team-foo")
    # shared/ is external; should NOT be flagged as missing entry
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0


def test_audit_summary_excludes_external_from_page_count(tmp_path):
    """The summary `pages` count must match audit's owned-only scope; pages
    under `shared/` are external and excluded from both drift detection and
    the summary."""
    wiki = _make_wiki(tmp_path)
    (wiki / "owned.md").write_text("# owned")
    shared = wiki / "shared" / "team"
    shared.mkdir(parents=True)
    (shared / "index.md").write_text("# team")
    (shared / "extra.md").write_text("# extra in external space")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    # 1 owned page (owned.md) + 1 index (wiki/index.md) = 2; external pages excluded
    assert "pages:  2" in out


def test_audit_summary_excludes_nested_foreign_submodule(tmp_path):
    """A foreign submodule at `projects/external/` is external even though
    `projects/` itself is owned. A naive top-level filter would count its
    pages; the directory-by-directory walk must prune at any depth."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    external = wiki / "projects" / "external"
    external.mkdir(parents=True)
    (external / "index.md").write_text("# external")
    (external / "leaked.md").write_text("# should not be counted")
    (wiki / ".gitmodules").write_text(
        '[submodule "external"]\n'
        "\tpath = projects/external\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    # wiki/index.md = 1 page; everything under projects/external/ is external
    assert "pages:  1" in out, out


def test_audit_terminates_with_in_tree_symlink_cycle(tmp_path):
    """An in-tree symlink cycle (`deep/loop -> wiki`) must not hang the audit
    summary header's page count. The owned-files walk
    (`_model.discover_owned_md_files`) guards this with realpath cycle
    detection or `audit` hangs before drift detection even runs. Isolates the
    cycle-vs-hang concern by registering `deep/` in `## Spaces` so the test
    asserts on termination, not drift."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "deep"])
    import os
    os.symlink(wiki, wiki / "deep" / "loop")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "pages:" in out
    assert "OK" in out
    assert rc == 0


def test_remove_strips_all_duplicate_entries(tmp_path):
    """A pre-corrupted wiki with multiple `## Spaces` entries for the same
    directory should be fully cleaned up in one `space remove` call. Without
    looping, only the first matching entry would be removed."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo")
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [foo/](foo/)\n"
        + "- [foo/](foo/index.md)\n"
    )
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 0
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert entries == []


def test_audit_accepts_bare_folder_href(tmp_path):
    """A `## Spaces` entry written `- [foo/](foo/)` (bare-folder href, no
    /index.md) must not be reported as a missing entry."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    # The child must satisfy the v1 navigation contract (`index.md` +
    # `## Spaces`); the test pins the bare-FOLDER-href format, not a
    # bare-INDEX child.
    (wiki / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [foo/](foo/)\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "OK" in out


def test_audit_reports_stale_entry(tmp_path):
    """A `## Spaces` entry with no space on disk is reported stale."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [ghost/](ghost/index.md)\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0
    assert "ghost" in out and "stale" in out


def test_audit_accepts_nested_space_entry(tmp_path):
    """`space add projects/foo` registers `projects/foo/index.md` in the
    root's ## Spaces (projects/ is a plain folder). Audit must treat that
    multi-segment entry as valid, not stale."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "OK" in out


def test_audit_reports_nested_orphan_missing(tmp_path):
    """A space nested below a plain folder, unregistered, is reported missing
    against its nearest ancestor space (the wiki root here)."""
    wiki = _make_wiki(tmp_path)
    nested = wiki / "projects" / "orphan"
    nested.mkdir(parents=True)
    (nested / "index.md").write_text("# orphan")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0
    assert "projects/orphan" in out


def test_audit_drift_not_hidden_by_external_symlink_alias(tmp_path):
    """B3: an external symlink whose realpath aliases an owned, unregistered
    space must NOT hide that space's drift. `_walk_filesystem` used to mark
    the external boundary's realpath visited before deciding to descend, so
    the real owned `z/foo` (which sorts after `shared`) was later skipped as
    "already visited" and its missing registration went unreported — audit
    printed "OK: no drift". The owned drift must still surface."""
    wiki = _make_wiki(tmp_path)
    # Owned, unregistered space => drift against the wiki root.
    foo = wiki / "z" / "foo"
    foo.mkdir(parents=True)
    (foo / "index.md").write_text("# foo\n\n## Spaces\n\n")
    # External boundary (`shared` is external by name) whose realpath aliases
    # that owned space; `shared` sorts before `z`, so it is visited first.
    os.symlink(wiki / "z" / "foo", wiki / "shared", target_is_directory=True)

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1, out
    assert "z/foo" in out


# ---------- symlink cycle safety ----------

def test_walk_owned_spaces_breaks_symlink_cycle(tmp_path):
    wiki = _make_wiki(tmp_path)
    sub = wiki / "deep"
    sub.mkdir()
    (sub / "index.md").write_text("# deep")
    # Create a cycle: deep/loop -> wiki
    import os
    os.symlink(wiki, sub / "loop")
    # Should terminate, not infinite-loop
    spaces = list(_owned_spaces(wiki))
    assert wiki in spaces
    assert sub in spaces


# ---------- .gitmodules foreign-origin check ----------

def test_wiki_origin_url_returns_url(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    assert _model._wiki_origin_url(wiki) == "https://github.com/me/mywiki.git"


def test_wiki_origin_url_returns_none_without_config(tmp_path):
    wiki = _make_wiki(tmp_path)
    assert _model._wiki_origin_url(wiki) is None


def test_wiki_origin_url_follows_gitdir_file(tmp_path):
    """Submodule layout: `<wiki>/.git` is a FILE pointing at the real gitdir."""
    wiki = _make_wiki(tmp_path)
    real_gitdir = tmp_path / "elsewhere" / "modules" / "mywiki"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/sub/wiki.git\n'
    )
    (wiki / ".git").write_text(f"gitdir: {real_gitdir}\n")
    assert _model._wiki_origin_url(wiki) == "https://github.com/sub/wiki.git"


def test_wiki_origin_url_worktree_follows_commondir(tmp_path):
    """Worktree layout: gitdir holds `commondir` pointing at the shared repo."""
    wiki = _make_wiki(tmp_path)
    common = tmp_path / "main-repo" / ".git"
    common.mkdir(parents=True)
    (common / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/wt/shared.git\n'
    )
    worktree_gitdir = common / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n")
    (worktree_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://example.invalid/should-be-ignored.git\n'
    )
    (wiki / ".git").write_text(f"gitdir: {worktree_gitdir}\n")
    assert _model._wiki_origin_url(wiki) == "https://github.com/wt/shared.git"


def test_wiki_origin_url_returns_none_for_broken_gitdir_file(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / ".git").write_text("gitdir: /nonexistent/path/does/not/exist\n")
    assert _model._wiki_origin_url(wiki) is None


def test_wiki_origin_url_relative_gitdir(tmp_path):
    """Common submodule shape: `gitdir: ../.git/modules/<name>` (relative)."""
    parent_repo = tmp_path / "parent"
    parent_repo.mkdir()
    real_gitdir = parent_repo / ".git" / "modules" / "sub"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/sub/wiki.git\n'
    )
    wiki = parent_repo / "sub"
    wiki.mkdir()
    (wiki / "index.md").write_text("# sub")
    (wiki / ".git").write_text("gitdir: ../.git/modules/sub\n")
    assert _model._wiki_origin_url(wiki) == "https://github.com/sub/wiki.git"


def test_is_foreign_submodule_no_gitmodules(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "projects" / "foo").mkdir(parents=True)
    assert _model.is_foreign_submodule(wiki / "projects" / "foo", wiki) is False


def test_is_foreign_submodule_different_origin(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "projects" / "external"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "external"]\n'
        "\tpath = projects/external\n"
        "\turl = https://github.com/someone-else/their-wiki.git\n"
    )
    assert _model.is_foreign_submodule(sub, wiki) is True


def test_is_foreign_submodule_same_origin(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "projects" / "self-mirror"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "self-mirror"]\n'
        "\tpath = projects/self-mirror\n"
        "\turl = https://github.com/me/mywiki.git\n"
    )
    assert _model.is_foreign_submodule(sub, wiki) is False


def test_is_external_marks_foreign_submodule(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "projects" / "foreign"
    sub.mkdir(parents=True)
    (sub / "index.md").write_text("# foreign")
    (wiki / ".gitmodules").write_text(
        '[submodule "foreign"]\n'
        "\tpath = projects/foreign\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    assert _model.external_reason_for(sub, wiki) is not None


# ---------- _is_external: lexical shared/ check ----------

def test_is_external_marks_plain_shared_dir(tmp_path):
    wiki = _make_wiki(tmp_path)
    d = wiki / "shared" / "team"
    d.mkdir(parents=True)
    assert _model.external_reason_for(d, wiki) is not None


def test_is_external_owned_for_normal_dir(tmp_path):
    wiki = _make_wiki(tmp_path)
    d = wiki / "projects" / "mine"
    d.mkdir(parents=True)
    assert _model.external_reason_for(d, wiki) is None


def test_is_external_marks_symlink_under_shared(tmp_path):
    """A symlink at <wiki>/shared/ is external even when it points back inside
    the wiki tree — the shared/ test must be lexical, not realpath-based."""
    wiki = _make_wiki(tmp_path)
    inside = wiki / "projects" / "real"
    inside.mkdir(parents=True)
    (inside / "index.md").write_text("# real")
    (wiki / "shared").mkdir()
    import os
    link = wiki / "shared" / "mirror"
    os.symlink(inside, link)
    assert _model.external_reason_for(link, wiki) is not None


# ---------- space audit: broken wikilinks + orphans ----------

def test_audit_reports_broken_wikilink(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\nlinks to [[ghost]] which is missing.\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "broken wikilink" in out
    assert "ghost" in out


def test_audit_resolved_wikilink_not_broken(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "alpha.md").write_text("# Alpha\n\nsee [[beta]].\n")
    (wiki / "beta.md").write_text("# Beta\n\nback to [[alpha]].\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "! broken wikilink" not in out
    assert rc == 0


def test_audit_wikilink_in_code_block_ignored(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text(
        "# Page\n\nlinked [[real]] here.\n\n```\n[[fake-in-code]]\n```\n"
    )
    (wiki / "real.md").write_text("# Real\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "fake-in-code" not in out
    assert "! broken wikilink" not in out


def test_audit_resolves_wikilink_via_alias(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "src.md").write_text("# Src\n\nsee [[the-bee]].\n")
    (wiki / "beta.md").write_text("---\ntitle: Beta\naliases: [the-bee]\n---\n\n# Beta\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "! broken wikilink" not in out, out
    assert rc == 0


def test_audit_reports_orphan_page(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "lonely.md").write_text("# Lonely\n\nnobody links here.\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "orphan" in out
    assert "lonely.md" in out


def test_audit_orphan_alone_does_not_fail_exit(tmp_path):
    """An orphan is a fact, not an error — it must not flip the exit code."""
    wiki = _make_wiki(tmp_path)
    (wiki / "lonely.md").write_text("# Lonely\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    assert "orphan" in out


def test_audit_linked_page_is_not_orphan(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "hub.md").write_text("# Hub\n\npoints at [[leaf]].\n")
    (wiki / "leaf.md").write_text("# Leaf\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "hub.md" in orphan_section
    assert "leaf.md" not in orphan_section


def test_audit_index_md_never_orphan(tmp_path):
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "sub"])
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "index.md" not in orphan_section


def test_audit_passes_on_init_scaffolded_template_and_hot(tmp_path):
    """B2/C4: the framework's own `_template.md` and `hot.md` must pass the
    framework's own audit — producer=consumer. The raw `_template.md` carries
    `{{ now }}` placeholders that are NOT valid YAML frontmatter, but it is a
    scaffold (substituted at render time), not a content page. Audit must
    neither flag it malformed (exit flip) nor list either file as an orphan,
    matching CONVENTIONS.md's special-file exemption. Uses init's own
    constants so the producer and consumer can never drift."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_template.md").write_text(init_wiki.TEMPLATE_MD)
    (wiki / "hot.md").write_text(init_wiki.HOT_MD)
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out + err
    assert "malformed frontmatter" not in out
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "_template.md" not in orphan_section
    assert "hot.md" not in orphan_section


def test_audit_flags_malformed_frontmatter_on_index_md(tmp_path):
    """Audit is the exhaustive release gate: malformed YAML frontmatter on a
    space's own `index.md` is a genuine finding — its aliases silently drop out
    of the page index, so `ws-search` misses them — and MUST be flagged. The
    special-file exemption is for the orphan check and for `_template.md`'s
    render-time `{{ … }}` placeholders ONLY; it must not silence real
    frontmatter errors on index.md/log.md/hot.md (the bug when both checks
    shared one set). A `_template.md` co-located in the same wiki stays exempt,
    proving the two exemptions are correctly distinct."""
    wiki = _make_wiki(tmp_path)
    sub = wiki / "sub"
    sub.mkdir()
    # `bad: : :` is a YAML syntax error ("mapping values are not allowed here").
    (sub / "index.md").write_text(
        "---\ntitle: Foo\n  bad: : :\n---\n\n# Sub\n\n## Spaces\n\n"
    )
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [sub/](sub/index.md)\n")
    # The render-time template (also malformed YAML) must stay exempt.
    (wiki / "_template.md").write_text(init_wiki.TEMPLATE_MD)

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1, out
    assert "malformed frontmatter" in out
    assert "sub/index.md" in out
    # The template's render-time placeholders are NOT reported as a finding:
    # exactly one malformed-frontmatter issue (sub/index.md), and no finding
    # line names the template. (`_template.md` still shows in the unrelated
    # "conventions at root" summary, so check the finding lines specifically.)
    assert "1 malformed frontmatter" in out
    assert not any(
        "_template.md" in line and "malformed frontmatter" in line
        for line in out.splitlines()
    )


def test_audit_summary_lists_meta_limits_as_a_convention(tmp_path):
    """`_meta/limits.md` is an opt-in convention (AGENTS.md) and it actively
    governs cap resolution, so the audit summary's `conventions at root` line
    must list it — otherwise a wiki whose only convention is a limits file
    reports `(none)` while limits silently shape every size verdict."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| pattern | cap |\n|---|---|\n| *.md | 9000 |\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    conv_line = next(ln for ln in out.splitlines() if "conventions at root:" in ln)
    assert "_meta/limits.md" in conv_line


def test_caps_reports_unreadable_limits_without_traceback(tmp_path):
    """A non-UTF-8 (or otherwise unreadable) `_meta/limits.md` must surface as a
    clean finding + non-zero exit, NOT crash `space caps` with a raw traceback.
    `load_limit_table` only caught `OSError`, but the Obsidian wire format can
    carry non-UTF-8 bytes, which raise `UnicodeDecodeError` (a `ValueError`)
    and escaped uncaught (HANDBOOK: handle failures at boundaries — parse)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_bytes(
        b"| pattern | cap |\n|---|---|\n| *.md | \xff\xfe 9000 |\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "caps"])
    assert rc == 1
    assert "could not read" in out


def test_audit_reports_unreadable_limits_without_traceback(tmp_path):
    """Same boundary failure on the audit gate: a non-UTF-8 `_meta/limits.md`
    is flagged with a non-zero exit, not a crash."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_bytes(
        b"| pattern | cap |\n|---|---|\n| *.md | \xff\xfe 9000 |\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "could not read" in out


def test_audit_broken_link_in_external_space_ignored(tmp_path):
    wiki = _make_wiki(tmp_path)
    shared = wiki / "shared" / "team"
    shared.mkdir(parents=True)
    (shared / "index.md").write_text("# team")
    (shared / "page.md").write_text("# P\n\nbad [[ghost-in-external]] link\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "ghost-in-external" not in out
    assert rc == 0


def test_audit_broken_and_drift_both_counted(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "orphan-space").mkdir()
    (wiki / "orphan-space" / "index.md").write_text("# orphan-space")
    (wiki / "p.md").write_text("# P\n\nlink [[nowhere]]\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "drift" in out and "broken wikilink" in out


# ---------- space audit: _archives + Obsidian embeds ----------

def test_audit_fix_inserts_spaces_into_bare_index_folder(tmp_path):
    """`audit --fix` is the repair surface: it inserts `## Spaces` into any
    owned folder that has `index.md` but no heading. After the fix, the
    same audit re-run with no `--fix` exits 0."""
    wiki = _make_wiki(tmp_path)
    bare = wiki / "bare"
    bare.mkdir()
    (bare / "index.md").write_text("# bare\n")  # no `## Spaces`
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    # The child was added without registering it → it shows as drift;
    # --fix repairs the bare section AND registers the missing entry.
    assert rc == 0, out
    bare_text = (bare / "index.md").read_text()
    assert "## Spaces" in bare_text
    root_text = (wiki / "index.md").read_text()
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "bare/" in e.href for e in entries)


def test_audit_fix_recomputes_drift_after_section_repair(tmp_path):
    """A nested space whose `index.md` lacks `## Spaces` is invisible to drift detection until the
    bare-section pass runs (because the parser skips entries when the
    section header is missing). `--fix` makes a single pass do both."""
    wiki = _make_wiki(tmp_path)
    # foo is registered in wiki root's `## Spaces` already (via space add).
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    # Strip foo's own `## Spaces` to mimic a pre-v1 adopted layout.
    foo_idx = wiki / "foo" / "index.md"
    foo_idx.write_text("# foo\n")  # bare
    # Now create foo/bar/ on disk — drift, but invisible until foo gets
    # `## Spaces` back.
    (wiki / "foo" / "bar").mkdir()
    (wiki / "foo" / "bar" / "index.md").write_text("# bar\n\n## Spaces\n\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 0, out
    assert "## Spaces" in foo_idx.read_text()
    foo_entries = _md.parse_section_entries(foo_idx.read_text(), "Spaces")
    assert any(e.href and "bar" in e.href for e in foo_entries)


def test_audit_fix_registers_missing_entry_without_creating_directory(tmp_path):
    """`--fix` registers existing on-disk children; it never creates a
    directory. A stale entry (target absent) is reported but only removed
    when `--remove-stale` is also passed."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    # foo is NOT in wiki root's `## Spaces` — drift.
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 0, out
    # Entry registered; no new dir created.
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "foo/" in e.href for e in entries)


def test_audit_reports_bare_index_child_without_fix(tmp_path):
    """Read-only audit: a REGISTERED nested space whose `index.md` is bare
    (no `## Spaces`) violates the v1 navigation contract ("No `## Spaces`
    means no wiki"). Without `--fix`, audit must surface this — the
    earlier silent-skip behavior let a registered child pass even though
    no other resolver accepts it. Exit code flips so CI catches it."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    # Mutate foo's index.md to remove its `## Spaces` (simulates drift).
    (wiki / "foo" / "index.md").write_text("# foo\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0, out
    assert "no `## Spaces`" in out


def test_audit_fix_skips_registering_when_child_section_repair_failed(tmp_path):
    """If pass 1 fails to insert `## Spaces` into a bare child (e.g.,
    the child's `index.md` is over cap and cannot fit the heading),
    pass 2 must NOT register the child in the parent's `## Spaces`.
    Otherwise the parent advertises a child that the contract walker
    will skip — the exact producer/consumer break the v1 contract
    exists to prevent. Set `index.md` cap to 50; plant a bare child
    whose body already exceeds 50 so the section-insert is refused;
    assert the parent's `## Spaces` does not register the bare child."""
    wiki = _make_wiki(tmp_path)
    # Tight `index.md` cap so a bare child near the cap can't get the
    # heading inserted (the insertion pushes it past).
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| index.md | 50 |\n"
    )
    # Plant a bare nested space whose body already nearly fills the cap.
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n\n" + ("x" * 45) + "\n")
    foo_before = (wiki / "foo" / "index.md").read_text()

    rc, _out, err = _run(["--wiki", str(wiki), "audit", "--fix"])

    # The bare child's pass-1 repair was refused by the size cap.
    assert (wiki / "foo" / "index.md").read_text() == foo_before
    assert "size cap" in err
    # And pass 2 MUST NOT have registered the bare child in root —
    # registering would create the producer/consumer break.
    root_entries = _md.parse_section_entries(
        (wiki / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "foo/" in e.href for e in root_entries), (
        "audit --fix registered a child whose `## Spaces` repair failed "
        "— parent's contract advertises a non-wiki the consumer walker skips"
    )
    # Refusal surfaces the rationale so the user can act.
    assert "still lacks `## Spaces`" in err
    # Exit non-zero — drift remains.
    assert rc != 0


def test_audit_fix_register_missing_entry_respects_size_cap(tmp_path, monkeypatch):
    """`audit --fix` is a framework writer — registering a missing entry
    in the ancestor's `## Spaces` must enforce the per-file cap. The
    registration path must go through `enforce_size_cap`; a direct call
    to `_atomic_register_in_spaces` would push the ancestor over its cap
    silently."""
    wiki = _make_wiki(tmp_path)
    # Bump root `index.md` so the next registration would exceed the cap.
    big = "# wiki\n\n## Spaces\n\n" + ("x" * 4990) + "\n"
    (wiki / "index.md").write_text(big)
    # Drift: `foo/` exists as a registered space on disk but isn't listed.
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    rc, _out, err = _run(["--wiki", str(wiki), "audit", "--fix"])
    # Audit returns 0 only on a fully repaired wiki; the cap rejection
    # leaves `foo` unregistered, so exit is non-zero AND the size-cap
    # diagnostic is surfaced on stderr.
    assert rc != 0
    assert "size cap" in err
    # The ancestor was not bloated past the cap by the registration.
    entries = _md.parse_section_entries(
        (wiki / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "foo/" in e.href for e in entries)


def test_audit_fix_remove_stale_refuses_external_without_include_external(tmp_path):
    """Stale entries pointing into externally-classified paths are NOT
    removed unless `--include-external --remove-stale` are passed together
    — guards against accidentally unregistering a legitimate external mount
    whose contents are temporarily unavailable."""
    wiki = _make_wiki(tmp_path)
    # Hand-edit wiki/index.md to declare a stale shared/team entry.
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text() + "- [shared/team/](shared/team/index.md)\n"
    )
    rc, _, err = _run(
        ["--wiki", str(wiki), "audit", "--fix", "--remove-stale"]
    )
    # The external stale entry stays in place; the audit still exits 1.
    assert rc != 0
    assert "shared/team" in (wiki / "index.md").read_text()
    assert "refusing to remove stale external" in err


def test_audit_fix_include_external_leaves_external_bare_index_unchanged(tmp_path):
    """`audit --fix --include-external` must NEVER cross the trust boundary:
    an external bare space (under `shared/`) with `index.md` and NO
    `## Spaces` is still REPORTED as drift, but the missing-section repair
    does not write into it. --include-external is a READ flag."""
    wiki = _make_wiki(tmp_path)
    ext = wiki / "shared" / "team"
    ext.mkdir(parents=True)
    ext_idx = ext / "index.md"
    ext_idx.write_text("# team\n")  # bare; no `## Spaces`
    rc, out, _ = _run(
        ["--wiki", str(wiki), "audit", "--fix", "--include-external"]
    )
    # (1) The external file's bytes are unchanged — repair did not cross.
    assert ext_idx.read_text() == "# team\n"
    assert "## Spaces" not in ext_idx.read_text()
    # (2) Still reported (so --include-external lists/reports it).
    assert "shared/team" in out
    assert "no `## Spaces`" in out
    # (3) Unrepaired external finding keeps exit non-zero.
    assert rc != 0


def test_audit_include_external_reports_malformed_frontmatter_in_symlinked_external_space(tmp_path):
    """Regression: `audit --include-external` must REPORT (not crash on) a
    malformed-frontmatter page inside a symlink-mounted external space whose
    realpath ESCAPES the wiki tree. `build_page_index` keys frontmatter errors
    by RESOLVED path, and the human renderer formatted them with
    `page.relative_to(wiki_root)` — which raised an uncaught `ValueError` for a
    resolved path outside the lexical wiki root, crashing the whole audit with a
    raw traceback (HANDBOOK: handle failures at boundaries). The
    duplicate-alias finding already maps such paths back to the lexical
    `md_files` via `raw_by_real`; malformed frontmatter must do the same
    (producer=consumer — both findings come from the one page index)."""
    wiki = _make_wiki(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "index.md").write_text("# ext\n\n## Spaces\n\n")
    (external / "page.md").write_text(
        "---\nbad: [unterminated\n---\n# p\n\nbody\n", encoding="utf-8"
    )
    os.symlink(external, wiki / "shared", target_is_directory=True)
    rc, out, err = _run(["--wiki", str(wiki), "audit", "--include-external"])
    assert rc != 0, out + err
    assert "malformed frontmatter" in out
    assert "shared/page.md" in out


def test_audit_json_include_external_reports_malformed_frontmatter_in_symlinked_external(tmp_path):
    """The `--json` surface must also survive a malformed-frontmatter page in a
    symlink-mounted external space (same resolved-path-escape as the human
    surface above) and report it with a wiki-relative `page` key."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "index.md").write_text("# ext\n\n## Spaces\n\n")
    (external / "page.md").write_text(
        "---\nbad: [unterminated\n---\n# p\n\nbody\n", encoding="utf-8"
    )
    os.symlink(external, wiki / "shared", target_is_directory=True)
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--include-external", "--json"])
    assert rc != 0
    payload = _json.loads(out)
    pages = [row["page"] for row in payload["malformed_frontmatter"]]
    assert "shared/page.md" in pages


def test_audit_fix_include_external_does_not_register_missing_into_external_ancestor(tmp_path):
    """`audit --fix --include-external` must not register a missing child
    entry into an EXTERNAL space's `## Spaces` either. The drift is still
    reported; only the write into external scope is suppressed — proving the
    per-space --fix gate (not just the bare-section pass) skips externals."""
    wiki = _make_wiki(tmp_path)
    # External space WITH `## Spaces` (valid), plus a registerable child not
    # yet listed in it — drift INSIDE an external space.
    ext = wiki / "shared" / "team"
    ext.mkdir(parents=True)
    ext_idx = ext / "index.md"
    ext_idx.write_text("# team\n\n## Spaces\n\n")
    sub = ext / "sub"
    sub.mkdir()
    (sub / "index.md").write_text("# sub\n\n## Spaces\n\n")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "audit", "--fix", "--include-external"]
    )
    # The external index.md `## Spaces` was NOT mutated (no `sub/` entry).
    entries = _md.parse_section_entries(ext_idx.read_text(), "Spaces")
    assert not any(e.href and "sub/" in e.href for e in entries)
    # The drift is still reported, and exit stays non-zero.
    assert "shared/team" in out
    assert rc != 0


def test_audit_remove_stale_requires_fix(tmp_path):
    """`--remove-stale` only makes sense with `--fix`; bare usage rejected."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "audit", "--remove-stale"])
    assert rc == 2
    assert "--fix" in err


def test_adopt_ancestor_registration_respects_size_cap(tmp_path, capsys):
    """`init --adopt` walks the existing tree and registers nested spaces
    in their ancestor's `## Spaces` via the chain helper. The chain
    helper's atomic mutation runs `enforce_size_cap` on the projected
    text — if a registration would push the ancestor's `index.md` past
    the 5K cap, the chain helper aborts via the abort tuple. Adopt
    surfaces the failure on stderr and leaves the ancestor untouched
    rather than silently bloating the index (best-effort batch; one
    failing registration doesn't abort the whole adoption)."""
    root = tmp_path / "wiki"
    root.mkdir()
    # Plant a root index near the 5K cap so the next entry would push over.
    root_idx = root / "index.md"
    root_idx.write_text("# wiki\n\n## Spaces\n\n" + ("x" * 4990) + "\n")
    # Pre-existing nested space (drift — not registered).
    (root / "foo").mkdir()
    (root / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    from wiki_spaces import init_wiki
    init_wiki.main([str(root), "--adopt", "--no-config"])
    err = capsys.readouterr().err
    # The cap rejection is surfaced (so the user sees what failed) AND
    # `foo` is NOT registered in the root (cap discipline holds).
    assert "size cap" in err
    entries = _md.parse_section_entries(root_idx.read_text(), "Spaces")
    assert not any(e.href and "foo/" in e.href for e in entries)


def test_adopt_inserts_spaces_in_existing_bare_indexes(tmp_path):
    """Root has `foo/index.md` with no `## Spaces` and no nested children.
    After `init --adopt`, foo's index gets `## Spaces` inserted (proves
    the leaf is repaired, not just the chain walk-up). Root's `## Spaces`
    registers foo."""
    root = tmp_path / "adopted"
    root.mkdir()
    (root / "index.md").write_text("# adopted\n")
    (root / "foo").mkdir()
    (root / "foo" / "index.md").write_text("# foo\n")  # bare; no children

    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--adopt", "--no-config"])
    assert rc == 0
    assert "## Spaces" in (root / "foo" / "index.md").read_text()
    root_entries = _md.parse_section_entries(
        (root / "index.md").read_text(), "Spaces"
    )
    assert any(e.href and "foo/" in e.href for e in root_entries)


def test_adopt_repairs_root_even_with_no_nested_spaces(tmp_path):
    """Bare root + zero children → `--adopt` still inserts `## Spaces`
    into the root. Otherwise the navigation contract is violated on day 1."""
    root = tmp_path / "empty"
    root.mkdir()
    (root / "index.md").write_text("# empty\n")
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--adopt", "--no-config"])
    assert rc == 0
    assert "## Spaces" in (root / "index.md").read_text()


def test_adopt_include_external_descends_into_external_spaces(tmp_path):
    """Positive case for `init --adopt --include-external`: an external
    space WITH `index.md` is descended into and registered upward, with
    its own `## Spaces` inserted if missing. Pairs with the negative
    skip-without-index test below — together they pin the §25 fix
    (pass `include_external` through to `_walk_classified` so externals
    are actually walked, not silently no-op'd)."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    # An external space (under `shared/`) with an existing `index.md`.
    ext = root / "shared" / "team"
    ext.mkdir(parents=True)
    (ext / "index.md").write_text("# team\n")  # bare; chain helper repairs
    from wiki_spaces import init_wiki
    rc = init_wiki.main(
        [str(root), "--adopt", "--include-external", "--no-config"]
    )
    assert rc == 0
    # External space registered in the root.
    entries = _md.parse_section_entries(
        (root / "index.md").read_text(), "Spaces"
    )
    hrefs = [e.href for e in entries if e.href]
    assert any("shared/team" in h for h in hrefs)
    # Leaf's own `## Spaces` was inserted by `_ensure_section_at`.
    assert "## Spaces" in (ext / "index.md").read_text()


def test_adopt_skips_externals_without_index_md(tmp_path):
    """With `--include-external`, the walker surfaces external boundary
    folders that aren't actually spaces (e.g. a stub `shared/foreign/`
    with no `index.md`). Adopt must skip those with a per-skip notice,
    not blow up trying to register a non-space."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    boundary = root / "shared" / "foreign"
    boundary.mkdir(parents=True)  # no index.md
    from wiki_spaces import init_wiki
    rc = init_wiki.main(
        [str(root), "--adopt", "--include-external", "--no-config"]
    )
    assert rc == 0
    # Boundary not registered (it isn't a space).
    entries = _md.parse_section_entries(
        (root / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "foreign" in e.href for e in entries)


def test_mount_refuses_bare_target(tmp_path):
    """A mounted target with `index.md` but no `## Spaces` is not a wiki
    under v1. Mount refuses and rolls back the mount rather than
    auto-inserting (auto-insert would mutate someone else's repo)."""
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.md").write_text("# external\n")  # bare; no `## Spaces`
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team",
         "--mode", "symlink"]
    )
    assert rc == 1
    assert "## Spaces" in err
    assert not (wiki / "shared" / "team").exists()


# ---------- PR-K: contract walker + inherited external + malformed entries + duplicate aliases ----------

def test_walk_via_spaces_contract_skips_unregistered(tmp_path):
    """The contract walker discovers spaces via `## Spaces` entries, not
    via the filesystem. An on-disk space not listed in its ancestor's
    `## Spaces` is INVISIBLE to it (audit's FS walker surfaces such drift
    separately — see the paired test below)."""
    wiki = _make_wiki(tmp_path)
    # Registered child.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "registered"])
    assert rc == 0
    # Drift: unregistered on-disk space.
    (wiki / "unregistered").mkdir()
    (wiki / "unregistered" / "index.md").write_text("# u\n\n## Spaces\n\n")

    paths = [str(p.relative_to(wiki)) for p, _ in _contract_spaces(wiki)]
    assert "registered" in paths
    assert "unregistered" not in paths


def test_walk_classified_yields_unregistered_for_audit(tmp_path):
    """The FS walker (`_walk_classified`) DOES yield unregistered spaces —
    that's how `space audit` detects drift. Paired with the contract test
    above to nail down the producer/consumer-vs-audit split."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "registered"])
    (wiki / "unregistered").mkdir()
    (wiki / "unregistered" / "index.md").write_text("# u\n\n## Spaces\n\n")

    paths = [
        str(p.relative_to(wiki))
        for p, classification, _ in space._walk_classified(wiki)
        if classification == _model.TrustScope.OWNED and p != wiki
    ]
    assert "registered" in paths
    assert "unregistered" in paths


def test_walk_classified_descendants_inherit_external(tmp_path):
    """A child folder under a foreign-submodule mount that isn't itself a
    symlink/submodule must STILL be classified external (§39). The bare
    `_is_external` heuristic only catches the boundary; inheritance
    catches descendants."""
    import subprocess
    wiki = _make_wiki(tmp_path)
    # Make the wiki itself a git repo (required for the foreign-submodule check).
    subprocess.run(["git", "init", "-q"], cwd=wiki, check=True)
    # Hand-craft a `.gitmodules` declaring shared/team with a foreign origin.
    (wiki / ".gitmodules").write_text(
        '[submodule "shared/team"]\n'
        '\tpath = shared/team\n'
        '\turl = https://github.com/other/wiki\n'
    )
    boundary = wiki / "shared" / "team"
    boundary.mkdir(parents=True)
    (boundary / "index.md").write_text("# team\n\n## Spaces\n\n")
    # Plain descendant — not itself a symlink or submodule.
    nested = boundary / "subspace"
    nested.mkdir()
    (nested / "index.md").write_text("# sub\n\n## Spaces\n\n")

    yielded = list(space._walk_classified(wiki, include_external=True))
    classes_by_rel = {
        str(p.relative_to(wiki)): cls for p, cls, _ in yielded if p != wiki
    }
    assert classes_by_rel.get("shared/team") == _model.TrustScope.EXTERNAL
    # Without inheritance, this would be "owned"; with inheritance it's external.
    assert classes_by_rel.get("shared/team/subspace") == _model.TrustScope.EXTERNAL


def test_contract_walker_skips_escaping_href(tmp_path):
    """A `## Spaces` entry pointing at `../outside/index.md` (escapes after
    resolution) is silently skipped by the contract walker — audit
    reports it via the malformed-entries pass."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [outside/](../outside/index.md)\n")
    paths = [str(p.relative_to(wiki)) for p, _ in _contract_spaces(wiki)]
    assert paths == ["."]


def test_contract_walker_skips_absolute_href(tmp_path):
    """An absolute href is invalid — silently skipped."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [abs](/etc/passwd)\n")
    paths = [str(p.relative_to(wiki)) for p, _ in _contract_spaces(wiki)]
    assert paths == ["."]


def test_contract_walker_skips_external_by_default(tmp_path):
    """A `## Spaces` entry resolving to `shared/team/` is external — the
    contract walker skips it without `include_external`."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# team\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")
    paths = [str(p.relative_to(wiki)) for p, _ in _contract_spaces(wiki)]
    assert "shared/team" not in paths


def test_contract_walker_includes_external_with_flag(tmp_path):
    """`include_external=True` exposes external mounts. The yielded tuple's
    `is_external` flag is True for them."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# team\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")
    yielded = list(_contract_spaces(wiki, include_external=True))
    paths = {str(p.relative_to(wiki)): is_ext for p, is_ext in yielded}
    assert paths.get("shared/team") is True


def test_md_files_walker_skips_shared_plain_folder_by_default(tmp_path):
    """A plain `shared/` directory at the wiki root (no `index.md`) carries
    external trust scope per CONVENTIONS / Reserved top-level folder
    names. The md-files walker must skip it under default scope and only
    surface its `.md` files when `include_external=True`."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").mkdir()  # plain (no index.md), external by convention
    (wiki / "shared" / "team-notes.md").write_text("# team\n")
    # Default scope: external is opted out — must NOT yield the file.
    default_files = list(
        _contract_md_files(wiki, include_external=False)
    )
    assert not any(
        "shared/team-notes.md" in str(p) for p, _ in default_files
    ), "md-files walker leaked a shared/ plain-folder `.md` under default scope"
    # Opt-in: yielded as external.
    inc_files = list(
        _contract_md_files(wiki, include_external=True)
    )
    matched = [
        (p, is_ext) for p, is_ext in inc_files
        if "shared/team-notes.md" in str(p)
    ]
    assert matched, "md-files walker did not yield shared/ content under include_external"
    assert matched[0][1] is True, (
        "shared/ plain-folder content yielded with is_external=False — "
        "the walker must propagate external classification down"
    )


def test_space_files_owned_scope_excludes_external_descendants_by_default(tmp_path):
    """Naming an OWNED scope (e.g. `projects/foo`) doesn't opt into the
    user's externals. Only naming the external scope itself counts as
    the per-scope opt-in. An escaping-symlink `notes-from-friend/`
    under projects/foo (external by realpath) should stay invisible
    unless `--include-external` is passed or the symlink itself is
    named."""
    import os
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    # Owned page inside the owned scope.
    (wiki / "projects" / "foo" / "owned.md").write_text("# owned\n")
    # External descendant: a symlink under projects/foo pointing OUTSIDE
    # the wiki tree (classified external by realpath escape).
    outside = tmp_path / "outside-tree"
    outside.mkdir()
    (outside / "secret.md").write_text("# external secret\n")
    notes_link = wiki / "projects" / "foo" / "notes-from-friend"
    os.symlink(outside, notes_link, target_is_directory=True)
    rc, out, err = _run([
        "--wiki", str(wiki), "files", "projects/foo",
    ])
    assert rc == 0, err
    # Owned content surfaces.
    assert "projects/foo/owned.md" in out
    # External descendant of the owned scope does NOT surface (no
    # `--include-external`, the named scope is owned).
    assert "projects/foo/notes-from-friend/secret.md" not in out, (
        "naming an owned scope leaked external (escaping-symlink) "
        "descendants — the per-scope opt-in must fire only when the "
        "named scope is itself external"
    )


def test_space_files_dot_scope_honors_default_external_opt_out(tmp_path):
    """`space files .` is naming the wiki root, NOT opting into externals.
    The default scope is owned-only per AGENTS.md trust scope; only an
    explicit non-root external scope counts as an opt-in. Regression
    for the r6 fix that auto-opted-in for any named scope and ended up
    leaking shared/ content under the root scope."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text(
        "# team\n\n## Spaces\n\n"
    )
    (wiki / "shared" / "team" / "external.md").write_text("# external\n")
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text() + "- [shared/team/](shared/team/index.md)\n"
    )
    # Also plant an owned page so the listing isn't empty.
    (wiki / "owned-note.md").write_text("# owned\n")
    rc, out, err = _run(["--wiki", str(wiki), "files", "."])
    assert rc == 0, err
    # Owned content surfaces.
    assert "owned-note.md" in out
    # External content does NOT surface (no --include-external).
    assert "shared/team/external.md" not in out, (
        "`space files .` leaked external content under the default "
        "scope — root scope must honor the global --include-external flag, "
        "not auto-opt-in"
    )


def test_space_files_explicit_external_scope_allowed_without_global_flag(tmp_path):
    """AGENTS.md trust scope: `External spaces are visited only when the
    user explicitly names one or asks to include all`. Naming
    `shared/team` IS the opt-in; `--include-external` shouldn't also be
    required when a single scope is named. Without this, the user has
    no way to read a single external space without opting into ALL
    externals across the wiki."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text(
        "# team\n\n## Spaces\n\n"
    )
    (wiki / "shared" / "team" / "page.md").write_text("# page\n")
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text() + "- [shared/team/](shared/team/index.md)\n"
    )
    # Name shared/team explicitly. No --include-external.
    rc, out, err = _run([
        "--wiki", str(wiki), "files", "shared/team",
    ])
    assert rc == 0, err
    assert "shared/team/page.md" in out


def test_walk_md_files_via_contract_skips_meta_directory(tmp_path):
    """The consumer md-files walker (`space files`) must also skip
    `_meta/` — `_meta/limits.md` and `_meta/taxonomy.md` are config,
    not content. Symmetric with the FS walker's `_meta/` skip; without
    this, `space files` would surface config files as consumer-visible
    content pages, breaking the reserved-folder contract."""
    wiki = _make_wiki(tmp_path)
    (wiki / "owned.md").write_text("# owned\n")
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| index.md | 5000 |\n"
    )
    files = list(_contract_md_files(wiki))
    paths = [str(p.relative_to(wiki)) for p, _ in files]
    assert "owned.md" in paths
    assert "_meta/limits.md" not in paths, (
        "contract md-files walker surfaced `_meta/limits.md` — `_meta/` "
        "is reserved config, not content"
    )


def test_walk_owned_md_files_skips_meta_directory(tmp_path):
    """`_meta/` holds config files (limits.md, taxonomy.md) per CONVENTIONS
    / Reserved top-level folder names. The owned-md walker feeds audit's
    content/size/broken-wikilink checks and promote's link-rewrite
    candidate collection — none of which should treat `_meta/*.md` as
    content. Skip the directory the same way `_archives/` is skipped."""
    wiki = _make_wiki(tmp_path)
    # An owned content page that the walker SHOULD surface.
    (wiki / "owned.md").write_text("# owned\n")
    # `_meta/*.md` files the walker MUST skip.
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| index.md | 5000 |\n"
    )
    (wiki / "_meta" / "taxonomy.md").write_text("# taxonomy\n")
    files = _model.discover_owned_md_files(wiki)
    paths = [str(p.relative_to(wiki)) for p in files]
    assert "owned.md" in paths
    for bad in ("_meta/limits.md", "_meta/taxonomy.md"):
        assert bad not in paths, (
            f"owned-md walker yielded `_meta/` file {bad!r} — "
            "the walker must skip `_meta/` so audit/promote don't "
            "treat config files as content"
        )


def test_audit_fix_does_not_register_meta_descendants_as_spaces(tmp_path):
    """`_meta/` is reserved for config files (limits.md, taxonomy.md);
    no space lives there. The FS walker (`_walk_classified`, used by
    `audit --fix` and `init --adopt`) MUST skip `_meta/`, so repair
    passes never register `_meta/...` descendants in a parent's
    `## Spaces`. Otherwise a producer/consumer break: audit --fix
    writes an entry the contract walker (which prunes `_meta/`)
    will never read.

    Setup: plant `_meta/whatever/index.md` (a hypothetical pre-v1
    layout). Run `audit --fix`. Assert the entry is NOT registered
    in root's `## Spaces`."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta" / "whatever").mkdir(parents=True)
    (wiki / "_meta" / "whatever" / "index.md").write_text(
        "# whatever\n\n## Spaces\n\n"
    )
    rc, _, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    # audit may return 0 (no drift visible) or non-zero (other issues);
    # the point is what's NOT in root's `## Spaces`.
    root_text = (wiki / "index.md").read_text()
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert not any(
        e.href and "_meta" in e.href for e in entries
    ), (
        "audit --fix registered a `_meta/` descendant — the FS walker "
        "must skip `_meta/` so the repair pass doesn't write entries "
        "the consumer prunes"
    )


def test_promote_does_not_rewrite_links_in_drift_files(tmp_path):
    """§S5 stance B: promote's link rewrite uses the contract walker,
    not the FS walker. A `.md` file inside an unregistered (drift)
    space is consumer-invisible — promote MUST NOT mutate it. The
    plan's split is: audit/`audit --fix` is the surface that touches
    drift; promote is a consumer-side write that respects the
    navigation contract."""
    wiki = _make_wiki(tmp_path)
    # Source: a content page at the wiki root referencing old-page.
    (wiki / "owner.md").write_text(
        "# owner\n\nsee [[old-page]] for context\n"
    )
    # The page we'll promote.
    (wiki / "old-page.md").write_text("# old-page\n")
    # Drift: an unregistered space with a `.md` referencing old-page.
    # `drift-folder/` is NOT in root's `## Spaces`. Its `notes.md`
    # is consumer-invisible per §S5 stance B.
    (wiki / "drift-folder").mkdir()
    (wiki / "drift-folder" / "index.md").write_text("# drift\n\n## Spaces\n\n")
    drift_note = wiki / "drift-folder" / "notes.md"
    drift_note.write_text("# notes\n\nsee [[old-page]] for context\n")
    drift_before = drift_note.read_text()
    rc, _, err = _run(["--wiki", str(wiki), "promote", "old-page.md"])
    assert rc == 0, err
    # Owner's link was rewritten (consumer-visible).
    owner_text = (wiki / "owner.md").read_text()
    assert "[[old-page]]" not in owner_text or "old-page/index" in owner_text
    # Drift file was NOT touched (consumer-invisible per §S5).
    assert drift_note.read_text() == drift_before, (
        "promote rewrote links inside a drift file — §S5 stance B says "
        "promote uses contract-walker; drift is audit's surface, not promote's"
    )


def test_init_returns_nonzero_when_any_with_extra_overflows_cap(tmp_path, capsys):
    """`init` should fail loud when a requested `--with` framework write
    is refused by the cap helper. Previously only `index.md` overflow
    aborted with non-zero — `--with log.md` could be refused but
    init still returned 0 (silent partial-success lie). v1 contract:
    every framework write enforces the cap; the exit code mirrors."""
    root = tmp_path / "wiki"
    root.mkdir()
    # Tight log.md cap so the initial `# Log\n` scaffold (6 chars)
    # blows past 5.
    (root / "_meta").mkdir()
    (root / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| log.md | 5 |\n"
    )
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--with", "log.md", "--no-config"])
    assert rc != 0, (
        "init returned 0 despite `--with log.md` being refused by the "
        "size cap — partial-success exit must signal failure"
    )
    err = capsys.readouterr().err
    assert "size cap" in err


def test_promote_refuses_reserved_folder_derived_target(tmp_path):
    """Promote derives the target directory from the source stem
    (`foo.md` -> `foo/index.md`). The CLI must validate the derived
    name against the reserved-folder rules — `promote _meta.md` would
    otherwise create `_meta/index.md`, a space every consumer walker
    prunes per CONVENTIONS / Reserved top-level folder names. Same
    producer/consumer break the `_validate_rel_path` refusal on `add`
    exists to prevent."""
    wiki = _make_wiki(tmp_path)
    for bad in ("_meta.md", "_archives.md", ".hidden.md"):
        page = wiki / bad
        page.write_text(f"# {bad}\n")
        rc, _, err = _run(["--wiki", str(wiki), "promote", bad])
        assert rc == 2, f"promote {bad} should refuse"
        # Source still in place; no derived target dir created.
        assert page.is_file()
        # Cleanup for next iteration.
        page.unlink()


def test_audit_flags_reserved_folder_entries_as_malformed(tmp_path):
    """The contract walker prunes `## Spaces` entries pointing at hidden,
    `_archives`, or `_meta` paths (consumer-invisible). Audit MUST flag
    such entries as malformed — otherwise a pre-v1 wiki carrying e.g.
    `- [_meta/internal/](_meta/internal/index.md)` produces a clean
    audit while `space list` and `space files` hide the entry. That's
    the producer/consumer break audit exists to surface."""
    wiki = _make_wiki(tmp_path)
    # Plant a v1-incompatible layout: registered `_meta/internal/` with an
    # actual index.md on disk (so it's NOT stale per existing checks).
    (wiki / "_meta" / "internal").mkdir(parents=True)
    (wiki / "_meta" / "internal" / "index.md").write_text(
        "# internal\n\n## Spaces\n\n"
    )
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [_meta/internal/](_meta/internal/index.md)\n"
    )
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    # Must flip exit code because the entry is malformed under v1.
    assert rc != 0, "audit passed a reserved-folder `## Spaces` entry clean"
    combined = out + err
    assert "_meta/internal" in combined
    assert "reserved" in combined or "malformed" in combined


def test_audit_flags_href_metacharacters_each_char(tmp_path):
    """producer=consumer: every Markdown link metacharacter `space add` /
    `_validate_rel_path` refuses (`_SPACES_HREF_METACHARS`) must ALSO be
    flagged by audit when hand-authored into a `## Spaces` href. Before the
    fix, a real `foo[bar/index.md` on disk registered as
    `- [foo[bar](foo[bar/index.md)` passed audit clean while
    `space add foo[bar` refused the same shape — writer rejects what reader
    accepts. Iterate per-char so a single regressing char is pinpointed.

    `)` is excluded: it closes the markdown link, so the consumer-parsed href
    can never contain one — that shape surfaces as drift instead and is pinned
    by `test_audit_flags_close_paren_in_tail_position_reproduction`."""
    for i, c in enumerate(c for c in space._SPACES_HREF_METACHARS if c != ")"):
        sub = tmp_path / f"c{i}"
        sub.mkdir()
        wiki = _make_wiki(sub)
        seg = f"foo{c}bar"
        # Build a genuine on-disk space so the entry is NOT stale — the only
        # thing wrong with it is the metacharacter in the href.
        (wiki / seg).mkdir()
        (wiki / seg / "index.md").write_text(f"# {seg}\n\n## Spaces\n\n")
        idx = wiki / "index.md"
        idx.write_text(
            idx.read_text() + f"- [{seg}]({seg}/index.md)\n"
        )
        rc, out, err = _run(["--wiki", str(wiki), "audit"])
        combined = out + err
        assert rc != 0, (
            f"audit passed href metachar {c!r} clean ({combined!r})"
        )
        assert "malformed" in combined or "metacharacter" in combined, combined
        assert seg in combined, combined


def test_audit_fix_does_not_repair_href_metacharacter_entry(tmp_path):
    """`audit --fix` flags a metachar href as malformed but does NOT
    auto-repair it — like the empty/absolute/reserved precedent, the
    intended path is author intent the framework can't reconstruct."""
    wiki = _make_wiki(tmp_path)
    seg = "foo[bar"
    (wiki / seg).mkdir()
    (wiki / seg / "index.md").write_text(f"# {seg}\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    bad_line = f"- [{seg}]({seg}/index.md)\n"
    idx.write_text(idx.read_text() + bad_line)
    rc, _, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc != 0  # still reported as malformed
    assert bad_line in (wiki / "index.md").read_text()


@pytest.mark.parametrize(
    "c", [c for c in space._SPACES_HREF_METACHARS if c != ")"]
)
def test_audit_flags_href_metacharacter_in_description_tail_each_char(
    tmp_path, c
):
    """producer=consumer: a metachar that lands inside the consumer-parsed
    href (everything up to the first `)`, per `_AUDIT_BULLET_RE` / `_md.
    ENTRY_RE`) must be flagged, matching `space add`'s refusal. `)` is excluded
    — it closes the link and cannot appear in a parsed href (see the
    close-paren reproduction below, which now surfaces as drift). Parametrized
    per-char so a single regressing char is pinpointed."""
    seg = f"foo{c}bar"
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + f"- [{seg}/]({seg}/index.md)\n")
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    combined = out + err
    assert rc != 0, (
        f"audit passed href metachar {c!r} clean ({combined!r})"
    )
    assert "malformed" in combined or "metacharacter" in combined, combined


def test_audit_flags_close_paren_in_tail_position_reproduction(tmp_path):
    """Reproduction shape `- [foo)-bar/](foo)-bar/index.md)`. `)` closes the
    markdown link, so BOTH the producer-side audit and the consumer walker
    (`_md.ENTRY_RE`) read the href as the truncated `foo`. Flagging the `)` as
    an href metacharacter (the prior fix's approach) made audit reject an entry
    that `space list` accepts — a producer=consumer break of its own. The
    faithful signal: the truncated target `foo` is absent on disk while the
    real `foo)-bar/` dir is unregistered, so audit fails via DRIFT (stale +
    missing), not a metachar finding. The producer still refuses the dirname."""
    wiki = _make_wiki(tmp_path)
    # The INTENDED dir exists; the truncated href `foo` does NOT.
    (wiki / "foo)-bar").mkdir()
    (wiki / "foo)-bar" / "index.md").write_text("# foo)-bar\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    bad_line = "- [foo)-bar/](foo)-bar/index.md)"
    idx.write_text(idx.read_text() + bad_line + "\n")
    rc, out, err = _run(["--wiki", str(wiki), "audit", "--json"])
    combined = out + err
    assert rc != 0, f"audit passed the tail-`)` reproduction clean ({combined!r})"
    payload = json.loads(out)
    drift = payload.get("drift", [])
    assert any("foo" in d.get("stale", []) for d in drift), combined
    assert any("foo)-bar" in d.get("missing", []) for d in drift), combined
    # Cross-check the asymmetry: the producer refuses the dirname.
    assert space._validate_rel_path("foo)-bar")[0] is False


def test_audit_allows_em_dash_and_hyphen_description(tmp_path):
    """REGRESSION GUARD (no false positive): a genuine em-dash / hyphen /
    parenthesized description must still parse and NOT be flagged. The
    producer always writes `) — desc` (whitespace before the separator), so
    the candidate is exactly the clean href."""
    wiki = _make_wiki(tmp_path)
    # Distinct real spaces so each entry resolves (no drift) and no two share
    # an href dir (the unrelated duplicate-dir check would otherwise fire).
    for seg in ("foo", "bar", "baz", "qux"):
        (wiki / seg).mkdir()
        (wiki / seg / "index.md").write_text(f"# {seg}\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [foo](foo/index.md) — my notes\n"
        + "- [bar](bar/index.md) - my notes\n"
        + "- [baz](baz/index.md) — see (here)\n"
        # No-space separator: `_md.ENTRY_RE` accepts `)(?:\\s*[—\\-]+...)`, so
        # `space list` reads this as href `qux/index.md` + description. Audit
        # must agree (this was the reported false-positive: the `)` close was
        # mis-read as an href metacharacter when no space preceded the dash).
        + "- [qux](qux/index.md)- my notes\n"
    )
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    combined = out + err
    assert rc == 0, combined
    assert "metacharacter" not in combined and "malformed" not in combined, (
        combined
    )


def test_mount_dry_run_refuses_external_ancestor(tmp_path):
    """`--dry-run` must predict the real command: a mount that would register
    into an EXTERNAL ancestor's index.md is refused (non-zero) on dry-run too,
    not previewed as a success. Regression for the dry-run bypass where the
    external-ancestor preflight ran only after the dry-run early-return."""
    wiki = _make_wiki(tmp_path)
    team = wiki / "shared" / "team"
    team.mkdir(parents=True)
    (team / "index.md").write_text("# team\n\n## Spaces\n\n")
    src = _make_space_dir(tmp_path / "src")
    rc, out, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team/nested",
         "--mode", "symlink", "--dry-run"]
    )
    assert rc != 0, out + err
    assert "external" in (out + err)
    assert "would register" not in (out + err)


def test_add_dry_run_refuses_external_ancestor(tmp_path):
    """`add --dry-run` must also predict the real refusal when a HIGHER
    ancestor space is external (the leaf is owned, but registration would
    write an external ancestor's index.md)."""
    wiki = _make_wiki(tmp_path)
    team = wiki / "shared" / "team"
    team.mkdir(parents=True)
    (team / "index.md").write_text("# team\n\n## Spaces\n\n")
    rc, out, err = _run(
        ["--wiki", str(wiki), "add", "shared/team/child", "--dry-run"]
    )
    assert rc != 0, out + err
    assert "external" in (out + err)
    assert "would create" not in (out + err)


def test_mount_refuses_dest_escaping_wiki_tree_via_symlink(tmp_path):
    """The reproduced HIGH bug: a path component that is an escaping symlink
    (`<wiki>/link` -> outside the tree) let `mount <src> link/new` materialize
    content OUTSIDE the wiki and register it as owned. `_preflight_chain_external`
    only guards ancestor SPACES (folders with index.md), so a non-space escaping
    symlink slipped through. Now refused before any FS mutation — physical
    tree-containment is checked independently of the `shared/` trust convention.
    Both the real command and `--dry-run` must refuse and touch nothing."""
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, wiki / "link")
    src = _make_space_dir(tmp_path / "src")
    root_before = (wiki / "index.md").read_text()
    for extra in ([], ["--dry-run"]):
        rc, out, err = _run(
            ["--wiki", str(wiki), "mount", str(src), "link/new",
             "--mode", "symlink", *extra]
        )
        assert rc != 0, (extra, out + err)
        assert "escapes the wiki tree" in (out + err), (extra, out + err)
        # Nothing materialized outside the wiki; root index untouched.
        assert not (outside / "new").exists() and not (outside / "new").is_symlink()
        assert (wiki / "index.md").read_text() == root_before


def test_mount_default_into_shared_still_allowed(tmp_path):
    """REGRESSION GUARD: the escaping-symlink check keys off PHYSICAL tree
    containment, not the `shared/` trust convention — `shared/` is a real
    in-tree dir, so the default mount must still succeed."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src")
    rc, out, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "--mode", "symlink"]
    )
    assert rc == 0, out + err
    assert (wiki / "shared" / "src").is_symlink()


def test_add_dry_run_predicts_section_insert_cap_refusal(tmp_path):
    """`add` on a bare existing space (no `## Spaces`) repairs it via
    `_ensure_section_at`, which enforces the target's cap. dry-run must run the
    same section-insert cap preflight so it predicts the refusal instead of
    previewing a success the real run can't deliver."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "# Limits\n\n| pattern | cap |\n|---|---|\n| child/index.md | 10 |\n"
    )
    (wiki / "child").mkdir()
    (wiki / "child" / "index.md").write_text(
        "# child heading well over the ten-char cap\n"
    )
    dry = _run(["--wiki", str(wiki), "add", "child", "--dry-run"])
    real = _run(["--wiki", str(wiki), "add", "child"])
    assert dry[0] != 0 and real[0] != 0, (dry, real)
    assert dry[0] == real[0], (dry, real)
    assert "size cap" in (dry[1] + dry[2])
    # dry-run touched nothing; child still has no `## Spaces`.
    assert "## Spaces" not in (wiki / "child" / "index.md").read_text()


def test_add_repairs_bare_existing_space_under_cap(tmp_path):
    """Happy-path guard: a bare existing space whose section insertion fits the
    cap is repaired (rc=0, `## Spaces` inserted) — the preflight must not
    over-refuse."""
    wiki = _make_wiki(tmp_path)
    (wiki / "child").mkdir()
    (wiki / "child" / "index.md").write_text("# child\n")
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    assert "## Spaces" in (wiki / "child" / "index.md").read_text()


def test_add_honors_nested_per_space_limits(tmp_path):
    """Writers must enforce the NEAREST `_meta/limits.md`, not only the wiki
    root's — the same per-space cap scope `space audit` enforces (CONVENTIONS /
    per-space autonomy; HANDBOOK producer=consumer). A nested space whose own
    `_meta/limits.md` caps a child index below the scaffold size must make
    `space add` refuse, so the writer and the auditor return the same verdict.

    Regression: `enforce_size_cap` resolved caps against the wiki root only, so
    `add child/new` succeeded (reporting the root built-in cap) while `audit`
    immediately flagged the file over the child's own cap — a producer=consumer
    break on cap values.
    """
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    # The child space declares its OWN tighter cap for new/index.md.
    (wiki / "child" / "_meta").mkdir(parents=True)
    (wiki / "child" / "_meta" / "limits.md").write_text(
        "# Limits\n\n| pattern | cap |\n|---|---|\n| new/index.md | 1 |\n"
    )
    rc, out, err = _run(["--wiki", str(wiki), "add", "child/new"])
    assert rc != 0, (out, err)
    assert "size cap" in (out + err)
    # Refused before any FS mutation: the new space was never created.
    assert not (wiki / "child" / "new").exists()


def test_add_summary_lands_via_scaffolded_template(tmp_path):
    """The init-scaffolded `_template.md` carries a `{{ summary }}` placeholder,
    so `add --from-template _template.md --summary X` writes X into the new
    page's frontmatter (the v1.2.0 feature) instead of silently dropping it."""
    assert "{{ summary }}" in init_wiki.TEMPLATE_MD
    wiki = _make_wiki(tmp_path)
    (wiki / "_template.md").write_text(init_wiki.TEMPLATE_MD)
    rc, out, err = _run([
        "--wiki", str(wiki), "add", "child",
        "--from-template", "_template.md", "--summary", "LANDS_HERE_42",
    ])
    assert rc == 0, out + err
    assert "LANDS_HERE_42" in (wiki / "child" / "index.md").read_text()


def test_add_summary_refused_when_template_lacks_placeholder(tmp_path):
    """A template without `{{ summary }}` cannot receive --summary; rather than
    silently drop the value, render refuses (dry-run == real, nothing created)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "t.md").write_text("# {{ title }}\n\nno summary placeholder here\n")
    real = _run(["--wiki", str(wiki), "add", "child",
                 "--from-template", "t.md", "--summary", "X"])
    dry = _run(["--wiki", str(wiki), "add", "child",
                "--from-template", "t.md", "--summary", "X", "--dry-run"])
    assert real[0] != 0 and dry[0] != 0, (real, dry)
    assert "summary" in (real[1] + real[2]).lower()
    assert not (wiki / "child").exists()  # refused before any FS mutation


def test_add_force_index_restores_overwritten_index_on_chain_failure(tmp_path):
    """`add --force-index` overwrites an existing index. If the subsequent
    ancestor registration fails, the pre-existing index content must be
    restored — not silently destroyed (read-before-write / no partial
    mutation)."""
    if os.geteuid() == 0:
        pytest.skip("chmod write-protection is a no-op for root")
    wiki = _make_wiki(tmp_path)
    # Unregistered existing bare space with distinctive content.
    (wiki / "child").mkdir()
    old = "# child\n\nOLD_CONTENT_KEEP_ME\n\n## Spaces\n\n"
    (wiki / "child" / "index.md").write_text(old)
    os.chmod(wiki, 0o555)  # registering child into root ## Spaces will fail
    try:
        rc, out, err = _run([
            "--wiki", str(wiki), "add", "child", "--force-index",
            "--description", "new",
        ])
    finally:
        os.chmod(wiki, 0o755)
    assert rc != 0, (out, err)
    # The pre-existing index content must be restored, not destroyed.
    assert (wiki / "child" / "index.md").read_text() == old


def test_add_dry_run_predicts_missing_template_refusal(tmp_path):
    """`--dry-run` must run deterministic read-only preflights: a missing
    `--from-template` refuses on the real command, so dry-run must refuse too
    (it previously previewed success and returned 0)."""
    wiki = _make_wiki(tmp_path)
    dry = _run(["--wiki", str(wiki), "add", "foo",
                "--from-template", "missing.md", "--dry-run"])
    real = _run(["--wiki", str(wiki), "add", "foo",
                 "--from-template", "missing.md"])
    assert dry[0] != 0 and real[0] != 0, (dry, real)
    assert "template file not found" in (dry[1] + dry[2])
    assert not (wiki / "foo").exists()  # dry-run touched nothing


def test_promote_dry_run_predicts_size_cap_refusal(tmp_path):
    """`promote --dry-run` must run the size-cap preflight: a promoted file
    whose projected `index.md` exceeds the cap refuses on the real command, so
    dry-run must refuse too (it previously returned 0 before the cap check)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "# Limits\n\n| pattern | cap |\n|---|---|\n| index.md | 20 |\n"
    )
    (wiki / "foo.md").write_text("# Foo\n\n" + "x" * 200 + "\n")
    dry = _run(["--wiki", str(wiki), "promote", "foo.md", "--dry-run"])
    real = _run(["--wiki", str(wiki), "promote", "foo.md"])
    assert dry[0] != 0 and real[0] != 0, (dry, real)
    assert "size cap" in (dry[1] + dry[2])
    assert (wiki / "foo.md").is_file()  # dry-run did not move it


def test_mount_refuses_into_foreign_submodule_dir(tmp_path):
    """mount must not write into a foreign submodule's working tree even when
    that submodule dir is not itself a space. The destination's deepest
    existing ancestor (`vendor`) is external (foreign-origin submodule), and it
    is not under `shared/`, so mount refuses before any FS mutation rather than
    materializing content inside someone else's repo."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    vendor = wiki / "vendor"
    vendor.mkdir()
    (vendor / "index.md").write_text("# vendor")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = vendor\n'
        "\turl = https://github.com/other/wiki.git\n"
    )
    src = _make_space_dir(tmp_path / "src")
    root_before = (wiki / "index.md").read_text()
    rc, out, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "vendor/new", "--mode", "symlink"]
    )
    assert rc != 0, out + err
    assert "external" in (out + err)
    assert not (vendor / "new").exists() and not (vendor / "new").is_symlink()
    assert (wiki / "index.md").read_text() == root_before


def test_mount_refuses_into_foreign_submodule_under_shared(tmp_path):
    """A foreign-origin submodule parked UNDER `shared/` must NOT be a mount
    target either: mounting `shared/vendor/new` materializes content inside a
    third party's checked-out repo working tree. The blanket `under shared/`
    trust exemption used to skip the external-scope arm, and a submodule dir
    without `index.md` is invisible to the chain-external pre-flight — so the
    mount silently wrote into the foreign repo and registered it in the wiki
    root. The foreign-submodule-ancestor check refuses it even under shared/,
    while a plain `shared/<name>` destination stays allowed (test 2172).

    Mirrors test_mount_refuses_into_foreign_submodule_dir relocated under
    shared/; covers the index.md-absent vendor and a deeper path, on both real
    and --dry-run (the guard precedes the mount mechanism, so it is
    mechanism-independent)."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    vendor = wiki / "shared" / "vendor"
    vendor.mkdir(parents=True)  # working-tree dir, NO index.md
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = shared/vendor\n'
        "\turl = https://github.com/other/their.git\n"
    )
    src = _make_space_dir(tmp_path / "src")
    root_before = (wiki / "index.md").read_text()
    for path in ("shared/vendor/new", "shared/vendor/sub/new"):
        for extra in ([], ["--dry-run"]):
            rc, out, err = _run(
                ["--wiki", str(wiki), "mount", str(src), path,
                 "--mode", "symlink", *extra]
            )
            assert rc != 0, (path, extra, out + err)
            assert "foreign-origin submodule" in (out + err), (path, extra, out + err)
    # Nothing materialized inside the foreign submodule; root untouched.
    assert not (vendor / "new").exists() and not (vendor / "new").is_symlink()
    assert (wiki / "index.md").read_text() == root_before


def test_first_foreign_submodule_ancestor(tmp_path):
    """`_first_foreign_submodule_ancestor` finds a foreign-submodule ancestor
    regardless of `shared/` placement, and returns None for owned chains."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    vendor = wiki / "shared" / "vendor"
    vendor.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = shared/vendor\n'
        "\turl = https://github.com/other/their.git\n"
    )
    assert space._first_foreign_submodule_ancestor(vendor / "deep" / "x", wiki) == vendor
    assert space._first_foreign_submodule_ancestor(wiki / "shared" / "plain", wiki) is None
    assert space._first_foreign_submodule_ancestor(wiki, wiki) is None


def test_mount_refuses_symlink_alias_into_foreign_submodule(tmp_path):
    """An owned-looking symlink that POINTS into a foreign submodule (or a
    descendant of one) must not be a mount/add target: materializing through it
    writes inside a third party's repo working tree. The foreign-submodule arm
    and the into-shared classifier only saw lexical / into-shared paths; a
    symlink laundering into a foreign sub bypassed both. Covers not-under-shared
    and under-shared, mount and add."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    vendor = wiki / "projects" / "vendor"
    (vendor / "subdir").mkdir(parents=True)
    (vendor / "subdir" / "index.md").write_text("# subdir\n\n## Spaces\n\n")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = projects/vendor\n'
        "\turl = https://github.com/other/their.git\n"
    )
    (wiki / "shared").mkdir()
    os.symlink(vendor / "subdir", wiki / "alias", target_is_directory=True)
    os.symlink(vendor / "subdir", wiki / "shared" / "salias", target_is_directory=True)
    src = _make_space_dir(tmp_path / "src")
    sub_before = (vendor / "subdir" / "index.md").read_text()
    root_before = (wiki / "index.md").read_text()
    for cmd in (
        ["mount", str(src), "alias/new", "--mode", "symlink"],
        ["add", "alias/new2"],
        ["mount", str(src), "shared/salias/new3", "--mode", "symlink"],
    ):
        rc, out, err = _run(["--wiki", str(wiki), *cmd])
        assert rc == 2, (cmd, out + err)
        assert "external" in (out + err) or "foreign" in (out + err), (cmd, out + err)
    # Nothing was written into the foreign submodule's working tree.
    for leaf in ("new", "new2", "new3"):
        assert not (vendor / "subdir" / leaf).exists(), leaf
    assert (vendor / "subdir" / "index.md").read_text() == sub_before
    assert (wiki / "index.md").read_text() == root_before


def test_classifier_symmetry_symlink_into_foreign_submodule(tmp_path):
    """`_model.external_reason_for` and `classify_external_scope` AGREE that
    an owned-looking symlink into a foreign submodule is external (producer=
    consumer), while a benign owned->owned symlink stays owned."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    vendor = wiki / "projects" / "vendor"
    (vendor / "subdir").mkdir(parents=True)
    (vendor / "subdir" / "index.md").write_text("# s\n\n## Spaces\n\n")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = projects/vendor\n'
        "\turl = https://github.com/other/their.git\n"
    )
    (wiki / "docs").mkdir()
    (wiki / "docs" / "index.md").write_text("# d\n\n## Spaces\n\n")
    os.symlink(vendor / "subdir", wiki / "into_foreign", target_is_directory=True)
    os.symlink(wiki / "docs", wiki / "into_owned", target_is_directory=True)
    assert _model.external_reason_for(wiki / "into_foreign", wiki) is not None
    assert (_model.classify_external_scope(wiki / "into_foreign", wiki).scope
            == _model.TrustScope.EXTERNAL)
    assert _model.external_reason_for(wiki / "into_owned", wiki) is None
    assert (_model.classify_external_scope(wiki / "into_owned", wiki).scope
            == _model.TrustScope.OWNED)


def test_mount_refuses_escape_then_reenter_path(tmp_path):
    """A destination that leaves the tree through an escaping symlink and
    re-enters (`escape -> outside`, `outside/back -> wiki/real`) registers a
    path the consumer walker treats as external — a producer=consumer break.
    The trust-scope arm (the escaping-symlink ancestor is external) refuses."""
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside"
    (wiki / "real").mkdir()
    outside.mkdir()
    os.symlink(outside, wiki / "escape")
    os.symlink(wiki / "real", outside / "back")
    src = _make_space_dir(tmp_path / "src")
    root_before = (wiki / "index.md").read_text()
    rc, out, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "escape/back/new",
         "--mode", "symlink"]
    )
    assert rc != 0, out + err
    assert (wiki / "index.md").read_text() == root_before


def test_mount_refuses_cyclic_symlink_ancestor_without_crashing(tmp_path):
    """A cyclic symlink ancestor (`cycle -> cycle`) must be refused gracefully
    on BOTH dry-run and real (no uncaught FileExistsError at mkdir), and the
    two must agree. The containment arm resolves the deepest present ancestor
    with strict=True, turning the ELOOP into a clean refusal."""
    wiki = _make_wiki(tmp_path)
    os.symlink(wiki / "cycle", wiki / "cycle")
    src = _make_space_dir(tmp_path / "src")
    for extra in (["--dry-run"], []):
        rc, out, err = _run(
            ["--wiki", str(wiki), "mount", str(src), "cycle/new",
             "--mode", "symlink", *extra]
        )
        assert rc == 2, (extra, out + err)  # clean refusal, not a crash
        assert "Traceback" not in err, (extra, err)
        assert "unresolvable" in (out + err) or "escapes" in (out + err)


def test_mount_refuses_owned_looking_symlink_into_shared(tmp_path):
    """An owned-LOOKING symlink that POINTS into `shared/` (realpath in-tree,
    lexical name owned) must be classified external: writing through it would
    mutate the external `shared/` space. The lexical `shared/` test only
    catches links placed AT `shared/`; this catches aliases into it. Both
    `mount` and `add` must refuse (dry-run == real), and the external
    `index.md` stays byte-identical."""
    wiki = _make_wiki(tmp_path)
    realproj_dir = wiki / "shared" / "realproj"
    realproj_dir.mkdir(parents=True)
    (realproj_dir / "index.md").write_text("# realproj\n\n## Spaces\n\n")
    os.symlink(realproj_dir, wiki / "proj", target_is_directory=True)
    ext_before = (realproj_dir / "index.md").read_text()
    src = _make_space_dir(tmp_path / "src")
    for cmd in (
        ["mount", str(src), "proj/new", "--mode", "symlink"],
        ["add", "proj/new"],
    ):
        for extra in ([], ["--dry-run"]):
            rc, out, err = _run(["--wiki", str(wiki), *cmd, *extra])
            assert rc == 2, (cmd, extra, out + err)
            assert "external" in (out + err), (cmd, extra, out + err)
    # The external space's index.md was never mutated through the alias.
    assert (realproj_dir / "index.md").read_text() == ext_before
    assert sorted(os.listdir(realproj_dir)) == ["index.md"]


def test_is_external_symlink_into_shared_but_not_into_owned(tmp_path):
    """`_is_external` (and the model's `external_reason_for`) flag a symlink
    whose realpath lands under `shared/`, but NOT one that resolves to an owned
    path — the fix must not over-classify owned in-tree aliases as external."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "realproj").mkdir(parents=True)
    (wiki / "shared" / "realproj" / "index.md").write_text("# r\n\n## Spaces\n\n")
    (wiki / "docs").mkdir()
    (wiki / "docs" / "index.md").write_text("# d\n\n## Spaces\n\n")
    os.symlink(wiki / "shared" / "realproj", wiki / "into_shared",
               target_is_directory=True)
    os.symlink(wiki / "docs", wiki / "into_owned", target_is_directory=True)
    assert _model.external_reason_for(wiki / "into_shared", wiki) is not None
    assert _model.external_reason_for(wiki / "into_owned", wiki) is None
    assert _model.external_reason_for(wiki / "into_shared", wiki) is not None
    assert _model.external_reason_for(wiki / "into_owned", wiki) is None


def test_external_reason_distinguishes_symlink_into_shared_from_escape(tmp_path):
    """`space._external_reason` and `_model.external_reason_for` agree: a
    symlink whose realpath lands under `shared/` reports an into-shared reason,
    while one that escapes the tree reports an escape reason — so a remove/audit
    names the actual external boundary, not a blanket "escapes"."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "realproj").mkdir(parents=True)
    (wiki / "shared" / "realproj" / "index.md").write_text("# r\n\n## Spaces\n\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(wiki / "shared" / "realproj", wiki / "into_shared",
               target_is_directory=True)
    os.symlink(outside, wiki / "escapes", target_is_directory=True)
    assert "shared" in space._external_reason(wiki / "into_shared", wiki)
    assert "escapes" in space._external_reason(wiki / "escapes", wiki)
    assert "shared" in _model.external_reason_for(wiki / "into_shared", wiki)
    assert "escapes" in _model.external_reason_for(wiki / "escapes", wiki)


def test_md_walkers_skip_symlink_file_into_external_by_default(tmp_path):
    """A `.md` symlink whose realpath lands in external scope (under shared/)
    must be excluded from the owned-by-default file walks and `space files`,
    and surfaced only with --include-external — kept in step with
    classify_external_scope (producer=consumer). A benign symlink to an OWNED
    `.md` file stays owned."""
    wiki = _make_wiki(tmp_path)
    team = wiki / "shared" / "team"
    team.mkdir(parents=True)
    (team / "index.md").write_text("# team\n\n## Spaces\n\n")
    (team / "ext.md").write_text("# ext under shared\n")
    (wiki / "owned.md").write_text("# owned\n")
    os.symlink(team / "ext.md", wiki / "into_shared.md")
    os.symlink(wiki / "owned.md", wiki / "into_owned.md")
    # Classifier: external for the into-shared alias, owned for into-owned.
    assert (_model.classify_external_scope(wiki / "into_shared.md", wiki).scope
            == _model.TrustScope.EXTERNAL)
    assert (_model.classify_external_scope(wiki / "into_owned.md", wiki).scope
            == _model.TrustScope.OWNED)
    # Default file walk: into_shared.md excluded, into_owned.md included.
    default = {str(cf.path.relative_to(wiki)) for cf in _model.discover_md_files(wiki)}
    assert "into_shared.md" not in default, default
    assert "into_owned.md" in default, default
    # include-external surfaces it, marked external.
    inc = {
        str(cf.path.relative_to(wiki)): cf.external
        for cf in _model.discover_md_files(wiki, include_external=True)
    }
    assert inc.get("into_shared.md") is True, inc
    # `space files` default omits it; --include-external lists it.
    _, out, _ = _run(["--wiki", str(wiki), "files"])
    assert "into_shared.md" not in out and "into_owned.md" in out, out
    _, out, _ = _run(["--wiki", str(wiki), "files", "--include-external"])
    assert "into_shared.md" in out, out


def test_mount_dry_run_predicts_chain_cap_refusal(tmp_path):
    """`mount --dry-run` must run the full-chain size-cap preflight: when
    registering the mount would push an ancestor `index.md` past its cap, the
    real command refuses, so dry-run must refuse too (it previously returned 0
    before the cap check)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "# Limits\n\n| pattern | cap |\n|---|---|\n| index.md | 60 |\n"
    )
    (wiki / "index.md").write_text(
        "# W\n\n## Spaces\n\n" + "".join(f"- [s{i}/](s{i}/index.md)\n" for i in range(6))
    )
    src = _make_space_dir(tmp_path / "src")
    dry = _run(["--wiki", str(wiki), "mount", str(src), "shared/x",
                "--mode", "symlink", "--dry-run"])
    real = _run(["--wiki", str(wiki), "mount", str(src), "shared/x",
                 "--mode", "symlink"])
    assert dry[0] != 0 and real[0] != 0, (dry, real)
    assert "size cap" in (dry[1] + dry[2])
    assert not (wiki / "shared" / "x").exists()  # dry-run touched nothing


def test_atomic_mutate_index_returns_error_on_tempfile_create_failure(tmp_path):
    """A read-only ancestor dir must make `_atomic_mutate_index` return a clean
    `(1, reason)`. The temp-file CREATE is now inside the same OSError guard as
    the write/replace; previously `NamedTemporaryFile(...)` raised an uncaught
    PermissionError that escaped the caller, so the write neither applied nor
    reported cleanly."""
    if os.geteuid() == 0:
        pytest.skip("chmod write-protection is a no-op for root")
    wiki = _make_wiki(tmp_path)
    anc_index = wiki / "index.md"
    before = anc_index.read_text()

    def mutate(fresh):
        return (fresh + "\nchanged\n", "added")

    os.chmod(wiki, 0o555)
    try:
        rc, info = space._atomic_mutate_index(wiki, anc_index, mutate)
    finally:
        os.chmod(wiki, 0o755)
    assert rc == 1, (rc, info)
    assert "could not write" in info
    assert anc_index.read_text() == before  # not partially applied


def test_mount_rolls_back_when_index_write_fails_no_orphan(tmp_path):
    """When the registration write fails (read-only ancestor index dir),
    cmd_mount must roll the materialized mount back: no orphaned symlink, no
    Traceback, clean nonzero rc. Regression for the create-failure escaping
    `except EnsureChainError` and orphaning the mount."""
    if os.geteuid() == 0:
        pytest.skip("chmod write-protection is a no-op for root")
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").mkdir()  # writable subdir so the symlink materializes
    src = _make_space_dir(tmp_path / "src")
    root_before = (wiki / "index.md").read_text()
    os.chmod(wiki, 0o555)  # registration write into <wiki>/index.md will fail
    try:
        rc, out, err = _run(
            ["--wiki", str(wiki), "mount", str(src), "shared/x", "--mode", "symlink"]
        )
    finally:
        os.chmod(wiki, 0o755)
    assert rc != 0, (out, err)
    assert "Traceback" not in err, err
    # The symlink was rolled back — no orphan residue inside shared/.
    assert not (wiki / "shared" / "x").exists()
    assert not (wiki / "shared" / "x").is_symlink()
    assert (wiki / "index.md").read_text() == root_before


def test_mount_read_only_parent_refuses_without_traceback(tmp_path):
    """An unwritable parent dir makes mount's `dest.parent.mkdir` fail. The
    physical-viability guard checks containment + type, not writability, so the
    mkdir must refuse-and-report (clean rc, no Traceback) rather than dump a
    stack trace, and leave no residue."""
    if os.geteuid() == 0:
        pytest.skip("chmod write-protection is a no-op for root")
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src")
    root_before = (wiki / "index.md").read_text()
    os.chmod(wiki, 0o555)  # shared/ absent -> dest.parent.mkdir must create it
    try:
        rc, out, err = _run(
            ["--wiki", str(wiki), "mount", str(src), "shared/team",
             "--mode", "symlink"]
        )
    finally:
        os.chmod(wiki, 0o755)
    assert rc != 0, (out, err)
    assert "Traceback" not in err, err
    assert "could not create" in (out + err)
    assert not (wiki / "shared").exists()  # no partial residue
    assert (wiki / "index.md").read_text() == root_before


def test_add_read_only_parent_refuses_without_traceback(tmp_path):
    """`add` under an unwritable parent must refuse-and-report cleanly (no
    Traceback) and undo any parents it partially created."""
    if os.geteuid() == 0:
        pytest.skip("chmod write-protection is a no-op for root")
    wiki = _make_wiki(tmp_path)
    (wiki / "ro").mkdir()
    os.chmod(wiki / "ro", 0o555)
    try:
        rc, out, err = _run(["--wiki", str(wiki), "add", "ro/deep/child"])
    finally:
        os.chmod(wiki / "ro", 0o755)
    assert rc != 0, (out, err)
    assert "Traceback" not in err, err
    assert "could not create" in (out + err)
    assert not (wiki / "ro" / "deep").exists()  # partial parents rolled back


def test_mount_refuses_file_ancestor_component_no_crash(tmp_path):
    """A destination whose ancestor component is a regular FILE (not a dir)
    must refuse cleanly on BOTH dry-run and real — no uncaught FileExistsError
    at `mkdir`. The physical-viability arm's dir-type check turns it into a
    refuse-and-report, and dry-run predicts the same refusal."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").write_text("i am a file, not a dir")
    src = _make_space_dir(tmp_path / "src")
    for extra in (["--dry-run"], []):
        rc, out, err = _run(
            ["--wiki", str(wiki), "mount", str(src), "shared/x",
             "--mode", "symlink", *extra]
        )
        assert rc == 2, (extra, out + err)
        assert "Traceback" not in err, (extra, err)
        assert "not a directory" in (out + err), (extra, out + err)
    assert (wiki / "shared").read_text() == "i am a file, not a dir"  # untouched


def test_mount_refuses_deep_file_component_not_a_directory(tmp_path):
    """`mount a/b/c` where `a` is a regular file: the NotADirectoryError path
    at `mkdir(parents=True)` is refused cleanly too, dry-run == real."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a").write_text("file")
    src = _make_space_dir(tmp_path / "src")
    dry = _run(["--wiki", str(wiki), "mount", str(src), "a/b/c",
                "--mode", "symlink", "--dry-run"])
    real = _run(["--wiki", str(wiki), "mount", str(src), "a/b/c",
                 "--mode", "symlink"])
    assert dry[0] == 2 and real[0] == 2, (dry, real)
    assert "Traceback" not in real[2], real[2]


def test_add_refuses_cyclic_symlink_component_no_crash(tmp_path):
    """`cmd_add` must refuse a cyclic-symlink path component cleanly, matching
    `cmd_mount`. Previously `add proj/x` fell through `_is_in_external_scope`
    (whose non-strict resolve treats a self-link as owned) straight into
    `mkdir`'s ELOOP crash. Covers dry-run==real and the --force-external
    variant (an unmountable path is unmountable regardless of trust override)."""
    wiki = _make_wiki(tmp_path)
    os.symlink(wiki / "proj", wiki / "proj")  # cyclic self-link
    for v in (["proj/x"], ["proj/x", "--dry-run"], ["proj/x", "--force-external"]):
        rc, out, err = _run(["--wiki", str(wiki), "add", *v])
        assert rc == 2, (v, out + err)
        assert "Traceback" not in err, (v, err)
    assert not (wiki / "proj" / "x").exists()  # nothing created


def test_add_refuses_file_ancestor_component_no_crash(tmp_path):
    """`add foo/x` where `foo` is a regular file must refuse cleanly (dir-type
    arm) instead of crashing at `mkdir(parents=True)`; dry-run == real."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo").write_text("file")
    dry = _run(["--wiki", str(wiki), "add", "foo/x", "--dry-run"])
    real = _run(["--wiki", str(wiki), "add", "foo/x"])
    assert dry[0] == 2 and real[0] == 2, (dry, real)
    assert "not a directory" in (real[1] + real[2]), real
    assert "Traceback" not in real[2], real[2]
    assert (wiki / "foo").read_text() == "file"  # untouched


def test_contract_walker_skips_reserved_folder_entries(tmp_path):
    """Belt-and-suspenders: even when `## Spaces` lists an entry under
    a reserved folder (hidden / `_archives` / `_meta`) — e.g. a pre-v1
    wiki that registered such paths before the producer-side validator
    refused them — the contract walker must skip them per CONVENTIONS /
    Reserved top-level folder names. Otherwise consumers surface
    content the rest of the toolchain (audit walks, ws-tend scans)
    deliberately excludes."""
    wiki = _make_wiki(tmp_path)
    # Manually create the (would-be-refused-by-producer) reserved layouts.
    (wiki / ".old-notes").mkdir()
    (wiki / ".old-notes" / "index.md").write_text("# old\n\n## Spaces\n\n")
    (wiki / "_archives" / "y2024").mkdir(parents=True)
    (wiki / "_archives" / "y2024" / "index.md").write_text(
        "# 2024\n\n## Spaces\n\n"
    )
    (wiki / "_meta" / "internal").mkdir(parents=True)
    (wiki / "_meta" / "internal" / "index.md").write_text(
        "# internal\n\n## Spaces\n\n"
    )
    # Hand-edit root's `## Spaces` to list all three (pre-v1 producer).
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [.old-notes/](.old-notes/index.md)\n"
        + "- [_archives/y2024/](_archives/y2024/index.md)\n"
        + "- [_meta/internal/](_meta/internal/index.md)\n"
    )
    yielded = [
        str(p.relative_to(wiki))
        for p, _ in _contract_spaces(wiki)
    ]
    for bad in (".old-notes", "_archives/y2024", "_meta/internal"):
        assert bad not in yielded, (
            f"contract walker yielded reserved-folder entry {bad!r} — "
            "the walker must prune hidden / `_archives` / `_meta` paths"
        )


def test_contract_walker_classifies_descendant_of_foreign_submodule_as_external(tmp_path):
    """A `## Spaces` entry that lists a deeper path under a foreign-origin
    submodule's mount must be classified external, even when the entry's
    exact path isn't itself the submodule directory. `_is_external`
    alone only checks the exact path; ancestry awareness comes from
    `_is_in_external_scope`. Without that, the walker would yield
    `projects/vendor/docs` as owned even though `.gitmodules` says
    `projects/vendor` is a foreign submodule."""
    import subprocess
    wiki = _make_wiki(tmp_path)
    # Configure the wiki as a git repo with a different origin than the
    # submodule we'll register.
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=wiki, check=True,
    )
    (wiki / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n'
        '[remote "origin"]\n\turl = https://example.invalid/parent.git\n'
    )
    # Register a foreign-origin submodule at projects/vendor in .gitmodules.
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n'
        '\tpath = projects/vendor\n'
        '\turl = https://other.invalid/external.git\n'
    )
    # Materialize content as if the submodule were checked out, with a
    # nested space at `projects/vendor/docs`.
    (wiki / "projects" / "vendor" / "docs").mkdir(parents=True)
    (wiki / "projects" / "vendor" / "docs" / "index.md").write_text(
        "# docs\n\n## Spaces\n\n"
    )
    # Register `projects/vendor/docs` directly in root's `## Spaces` —
    # the submodule boundary itself isn't listed, only the deeper path.
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [projects/vendor/docs/](projects/vendor/docs/index.md)\n"
    )
    # Default scope: external descendant must NOT surface.
    default_paths = [
        str(p.relative_to(wiki))
        for p, _ in _contract_spaces(wiki)
    ]
    assert "projects/vendor/docs" not in default_paths, (
        "contract walker yielded a deeper-than-boundary foreign-submodule "
        "descendant as owned — ancestry-aware external classification missing"
    )
    # Opt-in: yielded as external.
    inc_pairs = list(
        _contract_spaces(wiki, include_external=True)
    )
    paths = {str(p.relative_to(wiki)): is_ext for p, is_ext in inc_pairs}
    assert paths.get("projects/vendor/docs") is True


def test_contract_walker_reclassifies_resolution_escape_as_external(tmp_path):
    """`init --adopt --include-external` can descend a foreign submodule or
    escaping-symlink subtree and register `boundary/foo/index.md` in the
    wiki root's `## Spaces`. The contract walker must classify the
    yielded child as external (and emit it under `--include-external`)
    instead of treating "resolved path escapes the wiki tree" as a
    silent malformed-skip. Otherwise the producer (adopt) registers
    content the consumer (space list / files) cannot reach — the v6
    producer/consumer break OC r5 caught.

    Setup: `outside_tree/foo/` is a real folder with a valid space;
    `boundary` is a symlink at the wiki root pointing at `outside_tree`.
    Register `boundary/foo/index.md` directly in the root's `## Spaces`.
    Default walk omits `boundary/foo` (external scope opt-out);
    `include_external=True` yields it with `is_external=True`."""
    import os
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside_tree"
    (outside / "foo").mkdir(parents=True)
    (outside / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    boundary = wiki / "boundary"
    os.symlink(outside, boundary, target_is_directory=True)
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [boundary/foo/](boundary/foo/index.md)\n"
    )
    # Default scope: external is opted out, child is hidden.
    default_paths = [
        str(p.relative_to(wiki))
        for p, _ in _contract_spaces(wiki)
    ]
    assert "boundary/foo" not in default_paths, (
        "contract walker yielded a resolution-escaping child under default "
        "scope — it must classify as external and skip without opt-in"
    )
    # Opt-in: yielded as external.
    inc_pairs = list(
        _contract_spaces(wiki, include_external=True)
    )
    classification = {
        str(p.relative_to(wiki)): is_ext for p, is_ext in inc_pairs
    }
    assert classification.get("boundary/foo") is True, (
        "contract walker did not yield the resolution-escaping child even "
        "with include_external=True — producer (adopt) and consumer (list) "
        "diverge on what's reachable"
    )


def test_contract_walker_skips_registered_child_without_spaces_section(tmp_path):
    """v1 contract: a space requires `index.md` AND `## Spaces`. A registered
    child whose `index.md` lacks the heading is drift (audit's bare-section
    pass reports it). The consumer walker MUST NOT yield it — `space list`
    and `space files` would otherwise surface a non-wiki as a valid space."""
    wiki = _make_wiki(tmp_path)
    # Register `foo/` in root's `## Spaces` then strip foo's heading so the
    # child looks valid by inode-presence but fails the v1 contract.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    (wiki / "foo" / "index.md").write_text("# foo\n")  # no `## Spaces`
    yielded = list(_contract_spaces(wiki))
    paths = [str(p.relative_to(wiki)) for p, _ in yielded]
    assert "foo" not in paths, (
        "contract walker yielded a registered child whose index.md lacks "
        "`## Spaces` — consumer traversal must skip drift, not surface it"
    )
    # `.` (the wiki root) is yielded as always.
    assert "." in paths


def test_md_files_walker_descends_plain_folders_inside_registered_space(tmp_path):
    """Inside a registered space, plain folders (no `index.md`) are traversed
    for `.md` files — but child-space boundaries are skipped (those are
    owned by the contract walker)."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    # Plain folder inside the registered space.
    notes = wiki / "projects" / "foo" / "notes"
    notes.mkdir()
    (notes / "x.md").write_text("# x\n")
    # A nested registered space — the md-files walker must NOT yield its
    # content (the contract walker owns it).
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo/child"])
    assert rc == 0
    (wiki / "projects" / "foo" / "child" / "page.md").write_text("# child page\n")

    files = [
        str(f.relative_to(wiki))
        for f, _ in _contract_md_files(wiki)
    ]
    assert "projects/foo/notes/x.md" in files
    # child/page.md is yielded by visiting the child SPACE — the walker
    # iterates the child once via the contract, then iterates THAT space's
    # plain folders. So page.md DOES show up.
    assert "projects/foo/child/page.md" in files


def test_md_files_walker_skips_escaping_symlink_in_plain_folder(tmp_path):
    """An escaping symlink under a plain folder is external — skipped
    unless `include_external=True`."""
    wiki = _make_wiki(tmp_path)
    notes = wiki / "notes"
    notes.mkdir()
    (notes / "ok.md").write_text("# ok\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# secret\n")
    import os
    os.symlink(outside, notes / "link")

    files_default = [
        str(f.relative_to(wiki))
        for f, _ in _contract_md_files(wiki)
    ]
    assert "notes/ok.md" in files_default
    # The escaping symlink's content must NOT appear by default.
    assert not any("link/secret.md" in f for f in files_default)


def test_md_files_walker_skips_escaping_md_symlink_in_plain_folder(tmp_path):
    """A symlinked `.md` file whose target resolves outside the wiki is
    external content masquerading as an owned page. The walker must
    apply the same escape check it applies to symlinked directories:
    skip by default, mark external under `--include-external`.
    Without the file-level escape check, a `notes/alias.md` symlink
    pointing at `/etc/passwd` would be yielded as owned."""
    wiki = _make_wiki(tmp_path)
    notes = wiki / "notes"
    notes.mkdir()
    (notes / "ok.md").write_text("# ok\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("# secret\n")
    import os
    os.symlink(secret, notes / "alias.md")

    # Default scope: escaping `.md` symlink is invisible.
    files_default = [
        str(f.relative_to(wiki))
        for f, _ in _contract_md_files(wiki)
    ]
    assert "notes/ok.md" in files_default
    assert "notes/alias.md" not in files_default

    # With --include-external: surface it, marked external.
    files_ext = [
        (str(f.relative_to(wiki)), is_ext)
        for f, is_ext in _contract_md_files(
            wiki, include_external=True
        )
    ]
    aliases = [(p, x) for p, x in files_ext if p == "notes/alias.md"]
    assert aliases, files_ext
    assert aliases[0][1] is True


def test_promote_refuses_symlinked_target_dir(tmp_path):
    """Promote refuses when `<source>/` (the target directory) is a
    symlink. Even when empty, `source.rename(target)` follows the link
    and writes `index.md` at the symlink's resolved location — which
    for an escaping symlink means external content. Mirrors the
    symlinked-source refusal (which closes the source path); this
    closes the destination path."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    import os
    os.symlink(outside_dir, wiki / "page")  # target dir is a symlink
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "symlink" in err.lower()
    # No partial mutation: source untouched, external dir untouched.
    assert (wiki / "page.md").is_file()
    assert not (outside_dir / "index.md").exists()


def test_promote_refuses_symlinked_source_pointing_outside(tmp_path):
    """Promote moves the source file then writes new content via
    `target.write_text(...)`. If the source is a symlink, the rename
    moves the SYMLINK, and the subsequent write follows the link target.
    For an escaping `.md` symlink that target lives outside the wiki —
    promote would silently mutate external content. Refuse outright."""
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("# secret\n")
    import os
    os.symlink(secret, wiki / "alias.md")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "alias.md"])
    assert rc == 2
    assert "external" in err.lower() or "symlink" in err.lower()
    # External content stays untouched.
    assert secret.read_text() == "# secret\n"
    # No partial promote: no `alias/` directory created.
    assert not (wiki / "alias").exists()


def test_promote_refuses_malformed_frontmatter(tmp_path):
    """Promote rewrites the source (link adjust + alias), so it refuses a page
    whose frontmatter it can't parse rather than silently dropping it —
    consistent with audit surfacing malformed frontmatter. Read before write:
    the file is left untouched and no partial promote happens."""
    wiki = _make_wiki(tmp_path)
    page = wiki / "note.md"
    original = "---\ntitle: [unclosed\n---\n\n# Note\n\nbody\n"
    page.write_text(original, encoding="utf-8")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "note.md"])
    assert rc == 2
    assert "frontmatter" in err.lower() and "malformed" in err.lower()
    # Untouched source, no `note/` directory created.
    assert page.read_text() == original
    assert not (wiki / "note").exists()


def test_promote_reports_unreadable_source_without_traceback(tmp_path):
    """A non-UTF-8 source is a boundary failure the CLI must report cleanly
    (stderr + non-zero exit), not crash with a raw `UnicodeDecodeError`
    traceback (HANDBOOK: handle failures at boundaries — filesystem, parse).
    """
    wiki = _make_wiki(tmp_path)
    page = wiki / "note.md"
    page.write_bytes(b"# Note\n\xff\xfe not utf-8\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "note.md", "--skip-aliases"])
    assert rc == 1
    assert "could not read" in err
    assert not (wiki / "note").exists()


def test_promote_succeeds_with_unreadable_sibling_page(tmp_path):
    """A non-UTF-8 sibling page anywhere in the wiki must not crash a promote
    of an unrelated, valid source. The alias preflight and link-rewrite walks
    scan every owned page; an undecodable one is skipped (it can carry no
    parseable alias or wikilink), not allowed to escape as a traceback
    (HANDBOOK: handle failures at boundaries — filesystem, parse).
    """
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_bytes(b"# Sib\n\xff\xfe not utf-8\n")
    (wiki / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "note.md"])
    assert rc == 0, out + err
    assert (wiki / "note" / "index.md").is_file()


def test_promote_reports_unreadable_ancestor_index_without_traceback(tmp_path):
    """A corrupt (non-UTF-8) ancestor `index.md` must surface as a clean
    refusal, not a traceback, when promoting a page beneath it."""
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    (wiki / "child" / "index.md").write_bytes(b"# child\n\xff\xfe\n## Spaces\n\n")
    (wiki / "child" / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")
    rc, _, err = _run(
        ["--wiki", str(wiki), "promote", "child/note.md", "--skip-aliases"]
    )
    assert rc == 1
    assert "could not read" in err


def test_promote_snapshot_failure_rolls_back_without_unboundlocal(tmp_path, monkeypatch):
    """If a snapshot copy fails BEFORE `target_dir_pre_existed` is computed, the
    rollback handler must not raise `UnboundLocalError` reading it. The author
    bound `moved_via_git` before the `try` for exactly this reason but missed
    `target_dir_pre_existed` (HANDBOOK: writes are atomic and fail-closed)."""
    from wiki_spaces.space import promote as promote_mod
    wiki = _make_wiki(tmp_path)
    (wiki / "note.md").write_text("# Note\n\nbody\n", encoding="utf-8")

    def boom(_src, _dst, *a, **k):
        raise OSError("simulated snapshot copy failure")

    monkeypatch.setattr(promote_mod.shutil, "copy2", boom)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "note.md", "--skip-aliases"])
    assert "UnboundLocalError" not in err
    assert "Traceback" not in err
    assert rc == 2
    # Fail-closed: the source must survive (never promoted).
    assert (wiki / "note.md").exists()
    assert not (wiki / "note" / "index.md").exists()


def test_remove_reports_unreadable_target_without_traceback(tmp_path, monkeypatch):
    """If the target directory becomes unreadable between validation and the
    content scan, `remove` must report it cleanly instead of letting the
    `iterdir` `OSError` escape as a traceback."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "foo"])
    target = wiki / "foo"
    real_iterdir = Path.iterdir

    def fake_iterdir(self):
        if self == target:
            raise PermissionError("simulated unreadable target")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    rc, _, err = _run(["--wiki", str(wiki), "remove", "foo"])
    assert rc == 1
    assert "could not read" in err


def test_audit_survives_unreadable_log_without_traceback(tmp_path):
    """A non-UTF-8 `log.md` must not crash the whole audit: the summary's
    `last log` line is informational, so a failed read drops that line and
    the audit still completes."""
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_bytes(b"# Log\n\xff\xfe bad bytes\n")
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out + err
    assert "last log" not in out


def test_list_refuses_unreadable_root_index_without_traceback(tmp_path):
    """A non-UTF-8 root `index.md` is a boundary failure the strict resolver
    must refuse cleanly, not crash. `_has_spaces_section` read the contract
    under `except OSError`, but the Obsidian wire format can carry non-UTF-8
    bytes — a `UnicodeDecodeError` (a `ValueError`, NOT an `OSError`) that
    escaped uncaught. The same boundary `_meta/limits.md` and `promote`
    already harden (HANDBOOK: distrust boundary inputs; handle failures at
    boundaries)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "index.md").write_bytes(b"# wiki \xff\xfe\n\n## Spaces\n\n")
    rc, out, err = _run(["--wiki", str(wiki), "list"])
    assert rc == 2
    assert "Traceback" not in (out + err)


def test_files_skips_unreadable_child_index_without_traceback(tmp_path):
    """A non-UTF-8 child `index.md` must not crash the consumer-walk commands:
    the corrupt space is skipped by discovery (it can carry no parseable
    contract), not allowed to escape as a traceback."""
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    (wiki / "child" / "index.md").write_bytes(b"# child \xff\xfe\n\n## Spaces\n\n")
    rc, out, err = _run(["--wiki", str(wiki), "files"])
    assert rc == 0, out + err
    assert "Traceback" not in (out + err)


def test_audit_refuses_unreadable_root_index_without_traceback(tmp_path):
    """A non-UTF-8 root `index.md` must refuse cleanly at the strict resolver
    (rc 2, "not a wiki"), not crash. `_has_spaces_section` read the contract
    under `except OSError`, but the Obsidian wire format can carry non-UTF-8
    bytes (a `UnicodeDecodeError`, a `ValueError`, not an `OSError`)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "index.md").write_bytes(b"# wiki \xff\xfe\n\n## Spaces\n\n")
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    assert "Traceback" not in (out + err)
    assert rc == 2


def test_audit_survives_unreadable_child_index_without_traceback(tmp_path):
    """A non-UTF-8 child `index.md` registered in the root must not crash the
    audit gate: `discover_nodes` reads every folder's `index.md` and an
    undecodable one is treated as no readable contract (skipped), so the audit
    completes instead of escaping as a raw `UnicodeDecodeError` traceback."""
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    (wiki / "child" / "index.md").write_bytes(b"# child \xff\xfe\n\n## Spaces\n\n")
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    assert "Traceback" not in (out + err)
    assert rc == 0, out + err


def test_add_reports_unreadable_ancestor_index_without_traceback(tmp_path):
    """The chain-cap preflight reads every ancestor `index.md`; a non-UTF-8
    ancestor must refuse cleanly ("could not read"), not crash the writer
    with a raw `UnicodeDecodeError`."""
    wiki = _make_wiki(tmp_path)
    (wiki / "index.md").write_bytes(b"# wiki \xff\xfe\n\n## Spaces\n\n")
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 2
    assert "could not read" in err


def test_promote_refuses_symlinked_source_pointing_inside(tmp_path):
    """Even when the symlink target is inside the wiki, promote refuses:
    the mechanic of moving the link and rewriting through it is
    surprising. The user should resolve the symlink or operate on the
    canonical file directly."""
    wiki = _make_wiki(tmp_path)
    real = wiki / "real.md"
    real.write_text("# real\n")
    import os
    os.symlink(real, wiki / "alias.md")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "alias.md"])
    assert rc == 2
    assert "symlink" in err.lower()
    # No mutation: the real file and the alias stay as they were.
    assert real.read_text() == "# real\n"
    assert (wiki / "alias.md").is_symlink()


def test_walk_owned_md_files_skips_escaping_md_symlink(tmp_path):
    """The FS walker that audit/promote use also needs the .md symlink
    escape check — without it, a `notes/alias.md` symlinked at an
    outside target would be audited as owned and promoted, even though
    its content lives outside the wiki tree."""
    wiki = _make_wiki(tmp_path)
    notes = wiki / "notes"
    notes.mkdir()
    (notes / "ok.md").write_text("# ok\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("# secret\n")
    import os
    os.symlink(secret, notes / "alias.md")

    # Default scope: escaping `.md` symlink is invisible to the audit walker.
    files_default = [
        str(f.relative_to(wiki))
        for f in _model.discover_owned_md_files(wiki)
    ]
    assert "notes/ok.md" in files_default
    assert "notes/alias.md" not in files_default

    # With include_external=True: the file IS surfaced.
    files_ext = [
        str(f.relative_to(wiki))
        for f in _model.discover_owned_md_files(wiki, include_external=True)
    ]
    assert "notes/alias.md" in files_ext


def test_space_files_refuses_unregistered_symlink_alias_scope(tmp_path):
    """`space files <symlink-alias-to-registered-space>` must refuse —
    the contract is exhaustive, and a user-made symlink whose target is
    a registered space is still NOT in `## Spaces`. Comparing resolved
    paths would let the alias resolve to the registered space and pass
    reachability; lexical path comparison refuses the alias outright."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "notes"])
    assert rc == 0
    (wiki / "notes" / "page.md").write_text("# p\n")
    # Lexical alias to the registered space.
    import os
    os.symlink(wiki / "notes", wiki / "notes-alias")
    # Direct registered scope: success.
    rc_ok, _, _ = _run(["--wiki", str(wiki), "files", "notes"])
    assert rc_ok == 0
    # Alias scope: refused with the unregistered-scope diagnostic.
    rc_alias, _, err = _run(["--wiki", str(wiki), "files", "notes-alias"])
    assert rc_alias == 2
    assert "not reachable" in err


def test_audit_flags_malformed_spaces_entries(tmp_path):
    """Audit reports malformed `## Spaces` entries (empty href, absolute,
    `..`, escape, duplicate) and flips the exit code."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [empty]()\n"
        + "- [abs](/etc/passwd)\n"
        + "- [dotdot](../outside)\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "malformed" in out
    assert "empty href" in out
    assert "absolute href" in out
    assert "href contains `..`" in out


def test_spaces_href_with_braces_rejected_by_traversal_and_audit(tmp_path):
    """C2: a `{ }` in a `## Spaces` href must be rejected by the traversal
    consumer (`list`/`files`) exactly as the audit scanner rejects it — the
    two can't disagree (producer=consumer). `[ ] ( )` self-break the link
    parse, but `{ }` survived it: before the fix `list`/`files` traversed
    `foo{bar}` while `audit` flagged it malformed."""
    wiki = _make_wiki(tmp_path)
    weird = wiki / "foo{bar}"
    weird.mkdir()
    (weird / "index.md").write_text("# weird\n\n## Spaces\n\n")
    # Register the metachar entry by hand — the writer would refuse it.
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [bad](foo{bar}/index.md)\n")

    # Traversal consumers no longer surface it.
    rc_l, out_l, _ = _run(["--wiki", str(wiki), "list", "--json"])
    assert rc_l == 0
    assert "foo{bar}" not in out_l
    rc_f, out_f, _ = _run(["--wiki", str(wiki), "files"])
    assert "foo{bar}" not in out_f

    # Audit flags it malformed and flips the exit code.
    rc_a, out_a, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc_a == 1
    assert "malformed" in out_a


def test_wikilink_form_spaces_entry_consistent_across_surfaces(tmp_path):
    """C3: a `- [[child]]` entry under `## Spaces` carries no registrable href.
    One policy, applied consistently across surfaces:
      - the broken-wikilink scan must NOT flag `[[child]]` (it's a navigation
        entry, not a content cross-reference);
      - audit flags it as a malformed wikilink-form entry;
      - `audit --fix` must NOT append a redundant `- [child/](child/index.md)`
        beside it — it refuses, leaving the malformed line for repair.
    Before the fix audit reported BOTH a broken wikilink AND missing drift,
    and `--fix` created the redundant pair."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [[child]]\n")
    child = wiki / "child"
    child.mkdir()
    (child / "index.md").write_text("# child\n\n## Spaces\n\n")

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    # No broken-wikilink false positive for the navigation entry.
    assert "broken wikilink" not in out
    # Flagged as a malformed wikilink-form entry.
    assert "wikilink-form" in out

    before = idx.read_text()
    _run(["--wiki", str(wiki), "audit", "--fix"])
    after = idx.read_text()
    # --fix refused to register beside the malformed bullet: no redundant pair,
    # index unchanged.
    assert "child/index.md" not in after
    assert after == before


def test_audit_scans_wikilinks_under_spaces_heading_in_content_page(tmp_path):
    """Regression (codex verification of the C3 fix): `## Spaces` is the
    navigation contract ONLY in index.md. The broken-link-scan exclusion
    (`_blank_spaces_section`) must NOT blank a `## Spaces` heading that appears
    in a non-index CONTENT page — blanking it everywhere hid that page's broken
    wikilinks from audit, making the release gate non-exhaustive."""
    wiki = _make_wiki(tmp_path)
    # A content page that happens to use "## Spaces" as an ordinary heading,
    # with a broken wikilink under it.
    (wiki / "note.md").write_text(
        "# Note\n\nintro\n\n## Spaces\n\nsee [[missing-target]]\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "broken wikilink" in out
    assert "missing-target" in out


def test_audit_fix_does_not_repair_malformed_entries(tmp_path):
    """`audit --fix` repairs drift (insert section, register missing
    entries) but NOT malformed entries — those signal author intent the
    framework can't reconstruct."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    bad_line = "- [empty]()\n"
    idx.write_text(idx.read_text() + bad_line)
    rc, _, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 1  # malformed still reported as an error
    assert bad_line in (wiki / "index.md").read_text()


def test_audit_flags_unparseable_bullets(tmp_path):
    """Regression: bullets that LOOK LIKE `## Spaces` entries but don't
    match `_AUDIT_BULLET_RE` (unbalanced parens, missing parens entirely,
    half-formed wikilinks) used to slip past both the consumer parser AND
    the malformed scanner. Result: an unparseable line could sit in
    `index.md` forever; `audit --fix` would even add a SECOND, valid entry
    next to it. PR-K's contract says malformed entries are surfaced so the
    user can repair.
    """
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- [unbalanced/](child/index.md\n"      # missing closing `)`
        + "- [no-parens]\n"                        # no link/wikilink at all
        + "- [[half-wikilink]\n"                   # broken wikilink shape
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1, out
    assert "malformed" in out
    assert "unparseable bullet" in out
    # All three shapes must surface individually so the user knows what
    # to repair — a single combined "something's wrong" line would force
    # a manual diff.
    assert "[unbalanced/](child/index.md" in out
    assert "[no-parens]" in out
    assert "[[half-wikilink]" in out


def test_audit_ignores_fenced_spaces_example_in_index(tmp_path):
    """A `## Spaces` heading inside a fenced code block is an EXAMPLE, not the
    navigation contract. The consumer's section scanner (`find_section_bounds`)
    skips fenced headings, so the malformed-entry audit must too
    (producer=consumer) — otherwise a user documenting the `## Spaces` syntax in
    a code block gets spurious malformed-entry failures."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(
        "# w\n\n"
        "Example of the contract:\n\n"
        "```\n## Spaces\n\n- [Broken Example]()\n- [[half\n```\n\n"
        "## Spaces\n\n",
        encoding="utf-8",
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "malformed" not in out.lower()
    assert "Broken Example" not in out


def test_audit_does_not_flag_plain_bullets(tmp_path):
    """`_AUDIT_BULLET_SHAPE_RE` must not over-trigger on plain prose
    bullets that happen to live in `## Spaces`. Only bullets shaped
    like attempted entries (`- [...`) should be considered."""
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    idx.write_text(
        idx.read_text()
        + "- plain bullet, no link\n"
        + "  - nested bullet text\n"
    )
    rc, _, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0


def test_audit_fix_does_not_register_alongside_unparseable_bullet(tmp_path):
    """An unparseable bullet for `child/` must not let `audit --fix`
    add a SECOND valid entry next to it — that would leave the wiki
    with two bullets pointing at the same child, one broken and one
    new. Audit must flag the unparseable bullet (exit 1) and refuse to
    silently work around it.
    """
    wiki = _make_wiki(tmp_path)
    idx = wiki / "index.md"
    # Author broken entry pointing at a real on-disk space.
    (wiki / "child").mkdir()
    (wiki / "child" / "index.md").write_text("# child\n\n## Spaces\n\n")
    idx.write_text(idx.read_text() + "- [child/](child/index.md\n")  # no `)`
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix"])
    assert rc == 1, out  # unparseable bullet still flagged
    final = (wiki / "index.md").read_text()
    # The broken line stays — repair is judgment, not mechanical.
    assert "- [child/](child/index.md" in final
    # `audit --fix` MUST NOT have added a second `child/` entry alongside.
    # Counting occurrences of `child/index.md` is the simplest pin: the
    # broken bullet contributes one; a parallel registration would make
    # two.
    assert final.count("child/index.md") == 1, final


def test_audit_flags_duplicate_aliases(tmp_path):
    """When two owned pages declare the same alias, wikilink resolution is
    nondeterministic. Audit reports the collision."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a.md").write_text("---\naliases: [shared]\n---\n# a\n")
    (wiki / "b.md").write_text("---\naliases: [SHARED]\n---\n# b\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "duplicate alias" in out
    assert "shared" in out
    assert "a.md" in out and "b.md" in out


def test_audit_duplicate_alias_respects_include_external(tmp_path):
    """External pages aren't audited for duplicate-alias collisions unless
    `--include-external` opts in."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").mkdir()
    (wiki / "shared" / "team-page.md").write_text("---\naliases: [shared]\n---\n# t\n")
    (wiki / "owned.md").write_text("---\naliases: [shared]\n---\n# o\n")
    rc_default, out_default, _ = _run(["--wiki", str(wiki), "audit"])
    # Without --include-external, the external page isn't visible.
    assert rc_default == 0 or "duplicate alias" not in out_default

    rc_ext, out_ext, _ = _run(
        ["--wiki", str(wiki), "audit", "--include-external"]
    )
    assert rc_ext == 1
    assert "duplicate alias" in out_ext


# ---------- PR-L2: space list / space files / audit --json ----------

def test_space_list_shows_registered_only(tmp_path):
    """`space list` walks via `## Spaces` — registered children appear,
    unregistered on-disk content does not."""
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "registered"])
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# drift\n\n## Spaces\n\n")

    rc, out, _ = _run(["--wiki", str(wiki), "list"])
    assert rc == 0
    assert "registered" in out
    assert "drift" not in out


def test_space_list_include_external_shows_external_too(tmp_path):
    """`--include-external` opts external mounts into the listing."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# t\n\n## Spaces\n\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")
    rc_def, out_def, _ = _run(["--wiki", str(wiki), "list"])
    assert "shared/team" not in out_def
    rc_ext, out_ext, _ = _run(["--wiki", str(wiki), "list", "--include-external"])
    assert rc_ext == 0
    assert "shared/team\texternal" in out_ext


def test_space_list_json_emits_path_label_description_external(tmp_path):
    """JSON output carries label and description so the placement
    classifier in skills can disambiguate. Regression for LLM3.3 — the
    description field used to come back null for every space added with
    `--description`, because `cmd_add` only wrote the description to the
    child's body, never to the parent's `## Spaces` entry. Now it does
    both (symmetric with `space mount`), so this round-trips."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki), "add", "projects/foo",
        "--description", "per-project content",
    ])
    assert rc == 0
    rc2, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    assert rc2 == 0
    items = _json.loads(out)
    # Wiki root is excluded from JSON list.
    paths = {it["path"] for it in items}
    assert "." not in paths
    foo_entry = next(it for it in items if it["path"] == "projects/foo")
    assert foo_entry["label"]
    assert foo_entry["description"] == "per-project content"
    assert foo_entry["external"] is False


def test_space_list_include_boundaries_yields_external_folders_without_index_md(
    tmp_path,
):
    """`--include-external --include-boundaries` also surfaces external
    boundary folders WITHOUT `index.md` — the ws-update placement
    classifier needs the full exclusion set, not just spaces. Setup: a
    `shared/forge/` (external by path, no `index.md`) and a
    `notes-from-friend/` escaping symlink (external by symlink target,
    no `index.md`). Both should appear under `--include-boundaries`
    and only those WITH `index.md` should appear without it."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    # External-by-path boundary without `index.md` (would be a foreign
    # submodule in real use; the trust-scope test is path-based).
    (wiki / "shared" / "forge").mkdir(parents=True)  # no index.md
    # External-by-symlink boundary: target lives OUTSIDE the wiki tree.
    outside = tmp_path / "outside-tree"
    outside.mkdir()
    (outside / "page.md").write_text("# outside\n")
    (wiki / "notes-from-friend").symlink_to(outside)

    # Without --include-boundaries: spaces only (need `index.md`).
    rc1, out1, _ = _run([
        "--wiki", str(wiki), "list", "--include-external", "--json",
    ])
    assert rc1 == 0
    items1 = _json.loads(out1)
    paths1 = {it["path"] for it in items1}
    assert "shared/forge" not in paths1
    assert "notes-from-friend" not in paths1

    # With --include-boundaries: both external paths surface.
    rc2, out2, _ = _run([
        "--wiki", str(wiki),
        "list", "--include-external", "--include-boundaries", "--json",
    ])
    assert rc2 == 0
    items2 = _json.loads(out2)
    paths2 = {it["path"] for it in items2}
    assert "shared/forge" in paths2
    assert "notes-from-friend" in paths2
    # All boundary entries are marked external.
    for it in items2:
        if it["path"] in ("shared/forge", "notes-from-friend"):
            assert it["external"] is True


def test_space_list_include_boundaries_requires_include_external(tmp_path):
    """`--include-boundaries` alone is rejected — it's only meaningful when
    we're already opted into externals."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "list", "--include-boundaries"])
    assert rc == 2
    assert "--include-external" in err


def test_space_files_scope_restricts_to_subspace(tmp_path):
    """A `space` argument scopes output to files under that space only."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    (wiki / "projects" / "foo" / "page.md").write_text("# p\n")
    (wiki / "topnote.md").write_text("# top\n")
    rc2, out, _ = _run(["--wiki", str(wiki), "files", "projects/foo"])
    assert rc2 == 0
    assert "projects/foo/page.md" in out
    assert "topnote.md" not in out


def test_space_files_stops_at_unregistered_child_space(tmp_path):
    """Files inside an on-disk child space that is NOT in any ancestor's
    ``## Spaces`` must be invisible to ``space files`` — the contract
    walker never descends into unregistered spaces."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    (wiki / "projects" / "foo" / "visible.md").write_text("# v\n")
    unreg = wiki / "projects" / "foo" / "unreg"
    unreg.mkdir()
    (unreg / "index.md").write_text("# unreg\n\n## Spaces\n\n")
    (unreg / "hidden.md").write_text("# h\n")
    rc2, out, _ = _run(["--wiki", str(wiki), "files"])
    assert rc2 == 0
    assert "projects/foo/visible.md" in out
    assert "hidden.md" not in out


def test_space_files_refuses_unregistered_scope(tmp_path):
    """An on-disk folder that isn't in any `## Spaces` is invisible to the
    consumer — refusing here closes the back-door."""
    wiki = _make_wiki(tmp_path)
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# d\n\n## Spaces\n\n")
    (wiki / "drift" / "page.md").write_text("# d\n")
    rc, _, err = _run(["--wiki", str(wiki), "files", "drift"])
    assert rc == 2
    assert "not reachable" in err


def test_audit_json_emits_structured_payload(tmp_path):
    """`audit --json` emits one JSON document; exit code matches the
    human-text run."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    # Seed drift, broken wikilink, malformed entry.
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# d\n\n## Spaces\n\n")
    (wiki / "page.md").write_text("# p\n\n[[no-such-page]]\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [empty]()\n")

    rc_h, _, _ = _run(["--wiki", str(wiki), "audit"])
    rc_j, out_j, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc_j == rc_h
    payload = _json.loads(out_j)
    assert payload["exit_code"] == rc_j
    assert any(d["missing"] for d in payload["drift"])
    assert any("empty" in m["issue"] for m in payload["malformed_entries"])
    # LLM2.4 / D1: broken_wikilinks aggregated by target — one entry
    # per unique broken target, with `pages` listing every referring
    # page. LLM2.2: each entry still carries `tried` + `reason`.
    broken = next(b for b in payload["broken_wikilinks"] if b["target"] == "no-such-page")
    assert broken["pages"] == ["page.md"]
    assert isinstance(broken["tried"], list) and broken["tried"]
    strategies = {a["strategy"] for a in broken["tried"]}
    assert {"basename", "alias"} <= strategies
    assert all(a["outcome"] == "no_match" for a in broken["tried"])
    assert isinstance(broken["reason"], str)


def test_audit_json_broken_wikilinks_aggregates_multiple_pages(tmp_path):
    """When several pages reference the same broken target, the JSON
    consumer sees one entry with a `pages` list — not N near-identical
    entries. Locality is preserved via the sorted pages array."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    (wiki / "a.md").write_text("# a\n\n[[no-such-page]]\n")
    (wiki / "b.md").write_text("# b\n\n[[no-such-page]]\n")
    (wiki / "c.md").write_text("# c\n\n[[no-such-page]]\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    payload = _json.loads(out)
    broken = next(b for b in payload["broken_wikilinks"] if b["target"] == "no-such-page")
    assert broken["pages"] == ["a.md", "b.md", "c.md"]


def test_audit_summary_partitions_spaces_count_when_drift_present(tmp_path):
    """LLM1.8 / A7: when there's drift, the summary line shows
    `(N contract-reachable, M drift)` so the user sees both facts —
    not just one of the two counts the two underlying walkers used
    to disagree on. With no drift, the parenthetical is omitted."""
    wiki = _make_wiki(tmp_path)
    # Create an unregistered space-shaped child (drift).
    (wiki / "drift").mkdir()
    (wiki / "drift" / "index.md").write_text("# d\n\n## Spaces\n\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    # Drift flips the exit code; we just check the summary text.
    assert "contract-reachable" in out
    assert "drift" in out


def test_audit_flags_malformed_frontmatter(tmp_path):
    """A page with broken YAML frontmatter (was silently treated as
    ABSENT under Phase 1) now lands as an audit finding. The page's
    aliases are dropped — without this signal the producer can't tell
    why wikilinks pointing at those aliases come back broken."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    (wiki / "bad.md").write_text(
        "---\ntitle: Foo\n  bad: : :\n---\nbody\n", encoding="utf-8",
    )
    rc_h, _, _ = _run(["--wiki", str(wiki), "audit"])
    rc_j, out_j, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc_h == 1
    assert rc_j == 1
    payload = _json.loads(out_j)
    assert payload["malformed_frontmatter"]
    bad = payload["malformed_frontmatter"][0]
    assert bad["page"] == "bad.md"
    assert bad["status"] == "malformed"
    assert bad["error_message"]


def test_audit_malformed_frontmatter_line_basis_consistent(tmp_path):
    """C5: the JSON `error_line` must share the file-relative 1-based basis of
    the human `line N` surface (and of `malformed_limits`). It used to emit
    PyYAML's 0-based, frontmatter-relative line, so a consumer mapping it back
    to the file landed two lines too high — the two machine/human surfaces
    disagreed on basis."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    # The error is on file line 3 (the `bad: : :` line):
    #   1: ---   2: title: Foo   3:   bad: : :   4: ---   5: body
    (wiki / "bad.md").write_text(
        "---\ntitle: Foo\n  bad: : :\n---\nbody\n", encoding="utf-8",
    )
    _, out_h, _ = _run(["--wiki", str(wiki), "audit"])
    _, out_j, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert "(line 3)" in out_h
    bad = _json.loads(out_j)["malformed_frontmatter"][0]
    assert bad["error_line"] == 3


def test_audit_flags_malformed_limits_row(tmp_path):
    """A typo'd cap in `_meta/limits.md` (a malformed row that
    `_model.load_limit_table` surfaces) lands as an audit finding and flips
    the exit code — matching `space caps`. Producer/consumer read the same
    file through the one table and can't disagree."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n"
        "|---|---|\n"
        "| log.md | 20000 |\n"
        "| *.md | notanumber |\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "notanumber" in out
    assert "limits.md" in out
    assert "malformed limits row" in out
    # The malformed row is on the 4th line (1-based) of limits.md.
    assert "line 4" in out


def test_audit_json_emits_malformed_limits(tmp_path):
    """`audit --json` exposes malformed limits rows in a dedicated
    `malformed_limits` array with 1-based line provenance, mirroring the
    human+json double-check on malformed frontmatter."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n"
        "|---|---|\n"
        "| log.md | 20000 |\n"
        "| *.md | notanumber |\n"
    )
    rc, out_j, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc == 1
    payload = _json.loads(out_j)
    assert payload["exit_code"] == 1
    assert payload["malformed_limits"]
    entry = payload["malformed_limits"][0]
    assert "notanumber" in entry["raw"]
    assert isinstance(entry["line"], int)
    assert entry["line"] >= 1


def test_audit_and_caps_agree_on_malformed_limits(tmp_path):
    """Producer=consumer guard: `space caps` and `space audit` read the same
    cap parse, so the malformed rows reported by each (with line numbers) must
    be identical. Pins that the two surfaces can't drift apart again."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n"
        "|---|---|\n"
        "| log.md | 20000 |\n"
        "| *.md | notanumber |\n"
    )
    rc_caps, out_caps, _ = _run(["--wiki", str(wiki), "caps", "--json"])
    rc_audit, out_audit, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc_caps == 1
    assert rc_audit == 1
    caps_rows = {
        (r["line"], r["raw"])
        for r in _json.loads(out_caps)["malformed_rows"]
    }
    audit_rows = {
        (r["line"], r["raw"])
        for r in _json.loads(out_audit)["malformed_limits"]
    }
    assert caps_rows == audit_rows


def test_root_audit_flags_malformed_nested_limits(tmp_path):
    """Root `space audit` crosses owned spaces for size verdicts, so its gate
    must also surface a malformed `_meta/limits.md` in an owned NESTED space —
    else 'audit my wiki' passes while a child's own convention file is broken
    (producer=consumer). The child here has NO markdown of its own, so the row
    is found by proactively loading each declaring scope, not via the file scan.
    """
    wiki = _make_wiki(tmp_path)
    rc, out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc == 0, out + err
    (wiki / "child" / "_meta").mkdir(parents=True)
    (wiki / "child" / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n|---|---|\n| *.md | notanumber |\n"
    )
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1, out + err
    assert "child/_meta/limits.md" in out
    assert "malformed limits row" in out
    assert "notanumber" in out


def test_root_audit_json_reports_nested_malformed_limits_scope(tmp_path):
    """`audit --json` carries the owning scope on each malformed limits row so a
    machine consumer can locate which space's `_meta/limits.md` is broken."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    _run(["--wiki", str(wiki), "add", "child"])
    (wiki / "child" / "_meta").mkdir(parents=True)
    (wiki / "child" / "_meta" / "limits.md").write_text(
        "| Pattern | Cap |\n|---|---|\n| *.md | 0 |\n"
    )
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc == 1
    rows = _json.loads(out)["malformed_limits"]
    assert any(r.get("scope") == "child" for r in rows)


def test_audit_summary_omits_partition_when_no_drift(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    # Clean wiki: parenthetical not appended.
    assert "contract-reachable" not in out


def test_audit_json_emits_missing_spaces_section_for_bare_index_child(tmp_path):
    """`audit --json` exposes registered children whose `index.md` lacks
    `## Spaces` in a dedicated `missing_spaces_section` array. Without it,
    JSON consumers see `exit_code: 1` but every other category is empty —
    no actionable finding for a non-zero exit. (Human output reports
    these inline; JSON has to match.)"""
    import json as _json
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0
    # Strip foo's `## Spaces` so it's a registered child without `## Spaces`.
    (wiki / "foo" / "index.md").write_text("# foo\n")
    rc_j, out_j, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc_j == 1
    payload = _json.loads(out_j)
    assert payload["exit_code"] == 1
    assert "foo" in payload["missing_spaces_section"]


def test_audit_fix_json_drift_reflects_post_fix_state(tmp_path):
    """`audit --fix --json` repairs drift mid-pass, so its `drift` payload must
    report what REMAINS after the fix, not the pre-fix snapshot — otherwise the
    payload contradicts `exit_code`, which already counts only remaining drift.
    Registering the one missing entry must leave drift empty AND exit 0."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    # Unregistered valid space => the parent has missing-entry drift.
    (wiki / "beta").mkdir()
    (wiki / "beta" / "index.md").write_text("# beta\n\n## Spaces\n\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--fix", "--json"])
    payload = _json.loads(out)
    assert payload["exit_code"] == 0
    assert payload["drift"] == []
    assert rc == 0
    # Payload is empty because the drift was FIXED (beta now registered), not
    # because it was never detected.
    assert "beta/" in (wiki / "index.md").read_text()


def test_audit_json_read_only_still_reports_drift(tmp_path):
    """Guard that the post-fix recompute above did not suppress drift in
    READ-ONLY mode: without `--fix`, the same unregistered space must surface
    as missing-entry drift with a non-zero exit."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    (wiki / "beta").mkdir()
    (wiki / "beta" / "index.md").write_text("# beta\n\n## Spaces\n\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    payload = _json.loads(out)
    assert payload["exit_code"] == 1
    assert payload["drift"] == [
        {"ancestor": ".", "missing": ["beta"], "stale": []}
    ]
    assert rc == 1


# ---------- PR-L: size discipline + check-size + outgoing-link mask ----------

def test_check_size_ok(tmp_path):
    """`space check-size` returns OK for under-cap content."""
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "page.md",
    ], stdin="# short body\n")
    assert rc == 0
    assert out.startswith("OK ")


def test_check_size_prints_cap_source(tmp_path):
    """LLM2.2 / LLM1.2: the verdict line carries the cap's source so the
    producer can trace `OK 13/5000` back to the matching rule. Built-in
    `index.md` cap should be tagged as built-in default."""
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "index.md",
    ], stdin="# short\n")
    assert rc == 0
    assert "5000" in out
    assert "built-in default" in out
    assert "index.md" in out


def test_check_size_refuses_tty_stdin(tmp_path, monkeypatch):
    """Foot-gun guard: when no pipe is attached (stdin is a TTY),
    `check-size` refuses rather than reading empty stdin and reporting a
    misleading `OK 0/<cap>`."""
    wiki = _make_wiki(tmp_path)
    # Simulate "no pipe, terminal-attached stdin" by claiming isatty.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    rc, _, err = _run(["--wiki", str(wiki), "check-size", "page.md"])
    assert rc == 2
    assert "pipe" in err.lower() or "stdin" in err.lower()


def test_check_size_accepts_empty_pipe_as_zero_projection(tmp_path):
    """Empty piped content is legitimate ("zero this file"). The previous
    behavior of returning `OK 0/<cap>` for empty stdin is preserved when
    stdin is actually piped — only TTY-attached stdin is the refusal
    case. Closes the foot-gun without losing the zero-projection escape."""
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "check-size", "page.md"], stdin="")
    assert rc == 0
    assert out.startswith("OK 0/")


def test_check_size_reports_non_utf8_projected_file_without_traceback(tmp_path):
    """`--projected-file` is a raw user-supplied boundary input; a non-UTF-8
    file must refuse cleanly ("could not read"), not crash the CLI with a raw
    `UnicodeDecodeError` (a `ValueError`, NOT an `OSError`) — the same boundary
    the index.md/config/git reads were hardened against (HANDBOOK: distrust
    boundary inputs; handle failures at boundaries)."""
    wiki = _make_wiki(tmp_path)
    bad = tmp_path / "projected.bin"
    bad.write_bytes(b"body \xff\xfe not utf-8\n")
    rc, out, err = _run([
        "--wiki", str(wiki), "check-size", "page.md",
        "--projected-file", str(bad),
    ])
    assert "Traceback" not in (out + err)
    assert rc == 2
    assert "could not read" in err


def test_check_size_ignores_plain_folder_limits_scope(tmp_path):
    """`_meta/limits.md` is a per-SPACE convention — checks happen at a scope's
    root (CONVENTIONS). A plain folder (no `index.md`) is NOT a space, so its
    `_meta/limits.md` must not govern caps: otherwise the cap resolver honors a
    scope the malformed-limits audit (which only scans spaces) never checks — a
    producer=consumer break. The cap here must fall back to the nearest space
    (the wiki root) / built-in, not the plain folder's `*.md | 1`."""
    wiki = _make_wiki(tmp_path)
    meta = wiki / "plain" / "_meta"
    meta.mkdir(parents=True)
    (meta / "limits.md").write_text(
        "# limits\n\n| Pattern | Cap |\n|---|---|\n| *.md | 1 |\n", encoding="utf-8"
    )
    rc, out, _ = _run(
        ["--wiki", str(wiki), "check-size", "plain/page.md"], stdin="hello world"
    )
    assert rc == 0, out
    assert "built-in default" in out


def test_check_size_prints_user_override_source(tmp_path):
    """A cap from `_meta/limits.md` is tagged with the file + line."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir(parents=True, exist_ok=True)
    (wiki / "_meta" / "limits.md").write_text(
        "# Limits\n\n| Pattern | Cap |\n|---|---|\n| concepts/*.md | 8000 |\n",
    )
    (wiki / "concepts").mkdir()
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "concepts/foo.md",
    ], stdin="# short\n")
    assert rc == 0
    assert "8000" in out
    assert "user override" in out
    assert "_meta/limits.md" in out


def test_check_size_over_exits_1(tmp_path):
    """OVER outcome exits 1 — skills gate writes on this."""
    wiki = _make_wiki(tmp_path)
    huge = "x" * 16000
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "page.md",
    ], stdin=huge)
    assert rc == 1
    assert out.startswith("OVER ")


def test_check_size_rejects_absolute_path(tmp_path):
    """Path must be wiki-root-relative."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "check-size", "/etc/passwd",
    ], stdin="x")
    assert rc == 2
    assert "wiki-root-relative" in err


def test_check_size_rejects_dotdot_path(tmp_path):
    """`..` segments are also rejected — paths must stay inside the wiki."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "check-size", "../escape.md",
    ], stdin="x")
    assert rc == 2
    assert "wiki-root-relative" in err


def test_check_size_ok_shrinking_exits_0(tmp_path):
    """OK-SHRINKING is the legacy-bloat escape hatch: when the projected
    text is over the cap but smaller than the file already on disk, the
    write is allowed (you can never make legacy bloat worse, only better).
    Exit 0 — skills that gate on `check-size` should let it through."""
    wiki = _make_wiki(tmp_path)
    # Plant an over-cap page first (15K cap on `*.md`; write 16K).
    page = wiki / "huge.md"
    page.write_text("# huge\n\n" + ("x" * 16000) + "\n", encoding="utf-8")
    # Projected text: still over the 15K cap, but smaller than the on-disk
    # body. The shrinking-write hatch lets it through.
    smaller_but_still_over = "# huge\n\n" + ("x" * 15500) + "\n"
    rc, out, _ = _run([
        "--wiki", str(wiki), "check-size", "huge.md",
    ], stdin=smaller_but_still_over)
    assert rc == 0
    assert out.startswith("OK-SHRINKING ")


def test_add_refuses_over_cap_description_before_mkdir(tmp_path):
    """`space add --description <huge>` must refuse BEFORE creating any
    directory. Otherwise rejection leaves a stranded empty folder."""
    wiki = _make_wiki(tmp_path)
    huge = "x" * 6000  # over the 5000-char `index.md` cap
    rc, _, err = _run([
        "--wiki", str(wiki), "add", "newproj", "--description", huge,
    ])
    assert rc == 2
    assert "size cap" in err
    assert not (wiki / "newproj").exists(), \
        "new directory was created despite cap rejection"


def test_promote_preflights_size_caps_before_rename(tmp_path):
    """All projected writes are checked BEFORE the rename. An over-cap
    ancestor projection aborts the promote without moving the source."""
    wiki = _make_wiki(tmp_path)
    # Push the wiki root just under the cap so the entry-add pushes over.
    idx = wiki / "index.md"
    bulky = "x" * 4990
    idx.write_text(idx.read_text() + bulky + "\n")
    (wiki / "page.md").write_text("# page\n\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "size cap" in err
    # Source unmoved.
    assert (wiki / "page.md").is_file()
    assert not (wiki / "page").exists()


def test_promote_preflights_upper_chain_size_caps_before_any_fs_write(tmp_path):
    """PR-L: ALL planned writes — including upper-ancestor chain repair —
    must be preflighted BEFORE any FS mutation. The immediate ancestor's
    combined write was already preflighted by `cmd_promote`'s outer
    `enforce_size_cap` block; the chain helper that walks UP from
    `ancestor` to `wiki_root` was not. If grandparent's `index.md` is
    over cap when we try to register `ancestor/` in it, the failure must
    abort BEFORE `_ensure_section_at` mutates `ancestor/index.md`.
    Otherwise we'd half-mutate the chain.

    Setup: wiki/projects/foo.md to promote. `projects/index.md` is bare
    (no `## Spaces`) so the chain repair will need to (a) insert `##
    Spaces` into `projects/index.md` and (b) register `projects/` in
    the wiki root's `## Spaces`. Push the wiki root's `index.md` to just
    under cap so registering `projects/` exceeds it. Result: promote
    rejects with rc=2 AND `projects/index.md` retains its bare body
    (unchanged from before the call).
    """
    wiki = _make_wiki(tmp_path)
    (wiki / "projects").mkdir()
    bare_text = "# projects\n"
    (wiki / "projects" / "index.md").write_text(bare_text)
    (wiki / "projects" / "foo.md").write_text("# foo\n\nbody\n")

    # Push the wiki root to ~cap so registering `projects/` overflows
    # (registering adds `- [projects/](projects/index.md)\n` ~ 33 chars).
    idx = wiki / "index.md"
    bulky = "x" * 4990
    idx.write_text(idx.read_text() + bulky + "\n")

    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 2
    assert "size cap" in err.lower()
    # Producer side unchanged: bare body retained, source unmoved.
    assert (wiki / "projects" / "index.md").read_text() == bare_text
    assert (wiki / "projects" / "foo.md").is_file()
    assert not (wiki / "projects" / "foo").exists()


def test_promote_unaffected_by_chain_external_guard(tmp_path):
    """Regression: a normal promote of an owned root-level page.md still
    succeeds with the new defense-in-depth external-ancestor guard call
    present — it never fires on a valid (owned) promote."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err or out
    # Source became a space.
    assert (wiki / "page" / "index.md").is_file()
    assert "## Spaces" in (wiki / "page" / "index.md").read_text()
    # Registered in the owned wiki root.
    root_entries = _md.parse_section_entries(
        (wiki / "index.md").read_text(), "Spaces"
    )
    assert any(e.href and "page/" in e.href for e in root_entries)


def test_promote_outgoing_link_does_not_rewrite_code_block(tmp_path):
    """§29 — the outgoing-link adjustment must mask code spans, same as
    the cross-page rewriter. A `[label](sibling.md)` inside a fenced
    code block is documentation, not a real link."""
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_text("# sibling\n")
    page = wiki / "page.md"
    page.write_text(
        "# page\n\n"
        "```\n"
        "[sibling](sibling.md)\n"
        "```\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    moved = (wiki / "page" / "index.md").read_text()
    # The code block's `[sibling](sibling.md)` must survive unchanged —
    # NOT be rewritten to `(../sibling.md)`.
    assert "[sibling](sibling.md)" in moved
    assert "../sibling.md" not in moved


def test_promote_outgoing_link_does_not_rewrite_frontmatter(tmp_path):
    """§29 — frontmatter strings shaped like markdown links shouldn't be
    rewritten by the outgoing-link adjustment."""
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_text("# sibling\n")
    page = wiki / "page.md"
    page.write_text(
        "---\n"
        "source: \"[ref](sibling.md)\"\n"
        "---\n"
        "# page\n\n"
        "body\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    moved = (wiki / "page" / "index.md").read_text()
    # Frontmatter literal survives.
    assert "[ref](sibling.md)" in moved
    assert "[ref](../sibling.md)" not in moved


def test_promote_in_lock_size_check_catches_concurrent_growth(tmp_path, monkeypatch):
    """The outer preflight projects against the OUTER-read ancestor text.
    A concurrent writer can grow the ancestor between preflight and lock,
    so `_promote_mutate` re-checks inside the locked region against the
    actual projected text. Inject a concurrent write that pushes the
    ancestor over the cap right before the mutate fn runs; assert the
    helper aborts via the `(None, rc, reason)` tuple."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")

    real_atomic = space._atomic_mutate_index
    target_index = (wiki / "index.md").resolve()

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        # Only sabotage the ancestor write; other index writes (e.g. the
        # new space's own index.md) flow through unchanged.
        if ancestor_index.resolve() == target_index:
            existing = ancestor_index.read_text(encoding="utf-8")
            # Push the ancestor close to the cap so the entry add tips over.
            bulky = "x" * 4980
            ancestor_index.write_text(existing + bulky + "\n", encoding="utf-8")
        return real_atomic(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space._core, "_atomic_mutate_index", patched_atomic)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc != 0
    assert "size cap" in err
    # Source is restored (snapshot rollback ran).
    assert (wiki / "page.md").is_file()


def test_init_refuses_over_cap_description(tmp_path, capsys):
    """init's `--description` writes to `index.md` whose cap is 5000.
    A 6000-char description is refused: no `index.md` is left behind AND
    `init` exits non-zero so the user fixes the description before
    re-running. The config is NOT written either — pointing it at a
    non-wiki path would corrupt later resolves."""
    huge = "x" * 6000
    root = tmp_path / "wiki"
    from wiki_spaces import init_wiki
    rc = init_wiki.main([
        str(root), "--description", huge, "--no-config",
    ])
    # Exit non-zero so callers / CI can detect the failure.
    assert rc != 0, "init returned 0 despite refusing the wiki index"
    # The over-cap index.md is NOT on disk.
    assert not (root / "index.md").is_file(), \
        "init wrote an over-cap index.md despite the size-cap helper"
    # Stderr explains the failure so the user sees what to fix.
    err = capsys.readouterr().err
    assert "size cap" in err
    assert "init aborted" in err


def test_init_refuses_pre_existing_bare_index_without_adopt(tmp_path, capsys):
    """If `<path>/index.md` exists but lacks `## Spaces`, plain `init` must
    refuse rather than silently registering the path as canonical wiki.
    The default-skip write would leave the index.md bare while config
    still points at it — strict consumers (audit, doctor, skills) then
    reject the configured wiki. Refuse with a hint to run `--adopt`
    (inserts `## Spaces` via the chain helper) or `--force` (overwrite).
    Neither index nor config gets mutated on the refusal path."""
    root = tmp_path / "wiki"
    root.mkdir()
    bare_text = "# wiki\n\nlegacy notes\n"
    (root / "index.md").write_text(bare_text)
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--no-config"])
    assert rc != 0, "init silently registered a wiki without `## Spaces`"
    # Index is untouched.
    assert (root / "index.md").read_text() == bare_text
    err = capsys.readouterr().err
    assert "no `## Spaces`" in err
    assert "--adopt" in err
    assert "--force" in err


def test_init_with_adopt_repairs_pre_existing_bare_index(tmp_path):
    """The same setup as above, with `--adopt`, must succeed: the chain
    helper inserts `## Spaces` into the existing index. This is the
    documented upgrade path."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# wiki\n\nlegacy notes\n")
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--adopt", "--no-config"])
    assert rc == 0
    assert "## Spaces" in (root / "index.md").read_text()


def test_init_with_force_overwrites_pre_existing_bare_index(tmp_path):
    """`--force` is the alternative upgrade path: overwrite the existing
    index entirely with a fresh scaffolded one."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# legacy\n\nold notes\n")
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--force", "--no-config"])
    assert rc == 0
    assert "## Spaces" in (root / "index.md").read_text()


def test_init_log_md_extra_enforces_size_cap(tmp_path, capsys):
    """`init --with log.md` is a framework write — the v1 contract says
    every framework write enforces the cap. A degenerate configured
    `log.md` cap below the initial `# Log\\n` content (6 chars) must
    refuse rather than leak an over-cap scaffold file. This is edge,
    but the contract is uniform: no carve-outs."""
    root = tmp_path / "wiki"
    root.mkdir()
    # Tight log.md cap (5 chars — below `# Log\n` size of 6).
    (root / "_meta").mkdir()
    (root / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| log.md | 5 |\n"
    )
    from wiki_spaces import init_wiki
    init_wiki.main([str(root), "--with", "log.md", "--no-config"])
    # log.md must NOT have been written (cap rejection).
    assert not (root / "log.md").is_file(), (
        "init wrote an over-cap log.md despite the size-cap helper — "
        "every framework write must enforce the cap"
    )
    err = capsys.readouterr().err
    assert "size cap" in err


def test_space_log_accepts_positional_key_value(tmp_path):
    """Phase 11 / LLM3.5: `space log OP k=v k=v` works in addition to
    repeated `--field k=v`. Both forms produce the same entry."""
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("")
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "log", "SEARCH", "query=sourdough", "result_pages=3",
    ])
    assert rc == 0
    contents = (wiki / "log.md").read_text()
    assert "SEARCH" in contents
    assert "query=sourdough" in contents
    assert "result_pages=3" in contents


def test_space_log_positional_and_flag_mix(tmp_path):
    """Positional `k=v` and `--field k=v` interleave; both contribute."""
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("")
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "log", "UPDATE", "mode=conversation",
        "--field", "pages_updated=1",
    ])
    assert rc == 0
    contents = (wiki / "log.md").read_text()
    assert "mode=conversation" in contents
    assert "pages_updated=1" in contents


def test_space_log_create_enforces_size_cap(tmp_path):
    """`space log --create` scaffolds `log.md` before the first entry.
    Same contract: the scaffold write enforces the cap."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| log.md | 5 |\n"
    )
    rc, _, err = _run([
        "--wiki", str(wiki),
        "log", "--create", "--raw", "- [t] entry",
    ])
    assert rc != 0
    assert "size cap" in err
    assert not (wiki / "log.md").is_file()


def test_init_adopt_returns_nonzero_on_partial_failure(tmp_path, capsys):
    """`init --adopt` is best-effort batch — a single failing leaf doesn't
    abort the whole adoption. But the exit code MUST signal partial
    failure so callers/CI gating on rc don't treat the wiki as fully
    adopted while drift remains. Plant a wiki where one nested space's
    `index.md` would exceed the per-file cap; assert that adoption
    reports the failure on stderr AND returns non-zero, while still
    registering the spaces it COULD handle."""
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    # Tight cap so a bare child whose body is just over 50 chars can't
    # have `## Spaces` inserted by the chain helper.
    (root / "_meta").mkdir()
    (root / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| index.md | 50 |\n"
    )
    # foo is bare and oversized → adopt fails on it.
    (root / "foo").mkdir()
    (root / "foo" / "index.md").write_text("# foo\n\n" + ("x" * 45) + "\n")
    # bar is bare and small → adopt succeeds.
    (root / "bar").mkdir()
    (root / "bar" / "index.md").write_text("# bar\n")
    from wiki_spaces import init_wiki
    rc = init_wiki.main([str(root), "--adopt", "--no-config"])
    err = capsys.readouterr().err
    # foo's repair failed.
    assert "foo" in err
    # rc must signal partial failure even though bar succeeded.
    assert rc != 0, "init --adopt returned 0 despite at least one failure"
    # bar got `## Spaces` inserted (the partial success).
    assert "## Spaces" in (root / "bar" / "index.md").read_text()
    # foo stayed bare.
    assert "## Spaces" not in (root / "foo" / "index.md").read_text()


# ---------- PR-M: log rotation rejection + audit summary scope ----------

def test_log_rotation_rejects_when_still_over_cap(tmp_path):
    """A single log entry bigger than half the cap (or a cap small enough
    that even the kept half plus the new entry overflows) must be refused
    rather than silently committed. Preserves the no-silent-truncation
    guarantee from CONVENTIONS / Size discipline."""
    wiki = _make_wiki(tmp_path)
    log = wiki / "log.md"
    log.write_text("# Log\n")
    # Use --create to scaffold log.md, then push a huge --raw entry through
    # a tiny cap configured via `_meta/limits.md`.
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| log.md | 100 |\n"
    )
    huge_entry = "- [2026-01-01T00:00:00Z] " + ("x" * 500)
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", huge_entry])
    assert rc != 0
    assert "too large" in err or "cap" in err


def test_mount_preflights_ancestor_cap_before_running_mechanism(tmp_path):
    """`space mount` runs `git submodule add` / `git clone` / symlink
    BEFORE the chain helper attempts the registration. If the chain
    helper's in-lock cap check rejects the ancestor mutation, the mount
    mechanism has already mutated state — for submodules this means a
    staged gitlink + edited `.gitmodules` the user has to clean up by
    hand. The pre-flight cap check moves the rejection BEFORE any FS
    mutation, so a near-cap ancestor never causes a partial mount.

    Setup: a wiki with `index.md` near the per-file cap; mount a
    symlink (no git config needed) and verify it's refused with
    `size cap` on stderr and no symlink lands on disk."""
    wiki = _make_wiki(tmp_path)
    # Push root `index.md` near the 5K cap so the mount entry would
    # push past.
    big = "# wiki\n\n## Spaces\n\n" + ("x" * 4980) + "\n"
    (wiki / "index.md").write_text(big)
    # External source to mount.
    src = tmp_path / "external-src"
    src.mkdir()
    (src / "index.md").write_text("# ext\n\n## Spaces\n\n")
    rc, _, err = _run([
        "--wiki", str(wiki),
        "mount", str(src), "shared/external", "--mode", "symlink",
    ])
    assert rc != 0
    assert "size cap" in err
    # No mutation landed: no symlink, no entry, root index unchanged.
    assert not (wiki / "shared").exists()
    assert (wiki / "index.md").read_text() == big


def test_mount_preflights_full_chain_caps_not_just_nearest_ancestor(tmp_path):
    """OC phase-2 finding: the cmd_mount pre-flight only cap-checked the
    nearest ancestor. But the chain helper walks UP from leaf to wiki root,
    writing to every ancestor on the way. An upper-ancestor overflow that
    the nearest-ancestor check misses would only surface AFTER the destructive
    mount mechanism had already run (and for `--mode submodule`, rollback is
    only a manual-recovery notice). The full-chain pre-flight refuses BEFORE
    any FS mutation, regardless of mechanism.

    Setup: wiki has `## Spaces` near cap; nested `foo/` is a space with a
    generous cap and is NOT yet registered in the wiki root. Mounting into
    `foo/bar/` chains TWO edges: register `bar/` in `foo/index.md` (small,
    OK) AND register `foo/` in `wiki/index.md` (overflow). The old preflight
    saw only `foo`'s cap and let the mount proceed; the new full-chain
    preflight catches `wiki/index.md`'s cap and refuses.
    """
    wiki = _make_wiki(tmp_path)
    # Custom limits: `foo/index.md` is generous, root and every other
    # `index.md` cap at 100 chars. Pattern order matters; the path-glob
    # for `foo/index.md` must come first so the basename fallback doesn't
    # eat it.
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| foo/index.md | 5000 |\n"
        "| index.md | 100 |\n"
    )
    # Push root `index.md` just under its 100-char cap.
    root_body = "# wiki\n\n## Spaces\n\n" + ("x" * 70)
    (wiki / "index.md").write_text(root_body)
    # Nested space `foo/` exists with `## Spaces` but isn't registered up
    # in the root (drift — chain helper will walk through it).
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    foo_before = (wiki / "foo" / "index.md").read_text()
    # External source to mount.
    src = tmp_path / "external-src"
    src.mkdir()
    (src / "index.md").write_text("# ext\n\n## Spaces\n\n")
    rc, _, err = _run([
        "--wiki", str(wiki),
        "mount", str(src), "foo/bar", "--mode", "symlink",
    ])
    assert rc != 0, "preflight should refuse before any FS mutation"
    assert "size cap" in err
    # Mount mechanism never ran (no symlink at foo/bar) AND chain helper
    # never ran (foo/index.md unchanged, bar/ NOT registered in foo).
    assert not (wiki / "foo" / "bar").exists()
    assert (wiki / "foo" / "index.md").read_text() == foo_before
    # Root index.md untouched too.
    assert (wiki / "index.md").read_text() == root_body


def test_add_preflights_full_chain_caps_not_just_leaf_or_nearest_ancestor(tmp_path):
    """Mirror of the mount full-chain pre-flight, for `space add`. The leaf
    cap check at `cmd_add` covers the new child's own `index.md`; the new
    full-chain pre-flight covers every ancestor the chain helper would
    write. Without it, an upper-ancestor overflow would create the leaf
    directory + index.md before the chain helper raised `EnsureChainError`
    mid-walk, leaving the rollback to undo half-applied state.
    """
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| foo/index.md | 5000 |\n"
        "| index.md | 100 |\n"
    )
    root_body = "# wiki\n\n## Spaces\n\n" + ("x" * 70)
    (wiki / "index.md").write_text(root_body)
    (wiki / "foo").mkdir()
    (wiki / "foo" / "index.md").write_text("# foo\n\n## Spaces\n\n")
    foo_before = (wiki / "foo" / "index.md").read_text()
    rc, _, err = _run(["--wiki", str(wiki), "add", "foo/bar"])
    assert rc != 0, "preflight should refuse before any FS mutation"
    assert "size cap" in err
    # No leaf directory or index.md created; foo and root both untouched.
    assert not (wiki / "foo" / "bar").exists()
    assert (wiki / "foo" / "index.md").read_text() == foo_before
    assert (wiki / "index.md").read_text() == root_body


def test_log_append_size_check_includes_separator_newline(tmp_path):
    """The fit check must account for the conditional `\\n` separator
    the write path adds when the existing log lacks a trailing
    newline. Example: `current = "# Log"` (5 chars, no `\\n`),
    `entry_normalized = "- [t] x\\n"` (8 chars), `cap = 12`. The bare
    `len(current) + len(entry)` = 13 > 12 → would refuse. But the
    OFF-BY-ONE bug was the OTHER direction: tighten `current` to a
    length that just passes the bare check but fails when the
    separator newline is included. We pick `current = "# Log"` (5)
    + entry (7) + sep (1) = 13 vs cap=12: must REFUSE."""
    wiki = _make_wiki(tmp_path)
    log = wiki / "log.md"
    log.write_text("# Log")  # 5 chars, deliberately NO trailing newline
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| log.md | 12 |\n"
    )
    # Entry normalized would be `- [t]\n` (6 chars). Bare check:
    # 5 + 6 = 11 ≤ 12 → passes. WITH separator newline:
    # 5 + 1 + 6 = 12 ≤ 12 → still passes. Let's use a slightly larger
    # entry to push past.
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", "- [t] x"])
    # Entry normalized = 8 chars. 5 + 1 + 8 = 14 > 12 → must refuse.
    # Without the separator-aware fix: 5 + 8 = 13 > 12 → also refuses
    # in THIS case. So pick a tighter example that exposes the bug:
    # use a smaller entry that passes bare but fails with separator.
    # Reset:
    log.write_text("# Log")
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", "- t"])
    # Entry normalized = "- t\n" (4 chars). Bare check: 5 + 4 = 9 ≤ 12 → passes.
    # WITH sep: 5 + 1 + 4 = 10 ≤ 12 → also passes. Need cap=9.
    log.write_text("# Log")
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| log.md | 9 |\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", "- t"])
    # Entry normalized = 4 chars. Bare check: 5 + 4 = 9 ≤ 9 → passes.
    # WITH sep: 5 + 1 + 4 = 10 > 9 → must refuse.
    assert rc != 0, (
        "off-by-one in log fit check: separator newline not counted, "
        "wrote 1 byte over cap"
    )
    assert "too large" in err or "cap" in err


def test_log_create_refuses_atomically_when_scaffold_plus_entry_overflows(tmp_path):
    """`space log --create` is the first-call scaffold path. With a
    cap that admits the `# Log\\n` scaffold (6 chars) but NOT the
    scaffold + first entry, the helper must refuse BEFORE writing the
    scaffold — otherwise `log.md` exists on disk with just `# Log\\n`
    after the entry refusal (partial state on the first --create call).
    All-or-nothing scaffolding."""
    wiki = _make_wiki(tmp_path)
    # Cap = 10 chars: scaffold `# Log\n` (6) fits; scaffold + a 50-char
    # entry doesn't.
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| log.md | 10 |\n"
    )
    rc, _, err = _run([
        "--wiki", str(wiki),
        "log", "--create", "--raw",
        "- [2026-01-01T00:00:00Z] LARGE ENTRY THAT WILL NOT FIT",
    ])
    assert rc != 0
    assert "too large" in err or "cap" in err
    # All-or-nothing: log.md should NOT exist on disk after the
    # refusal (scaffold write was never committed).
    assert not (wiki / "log.md").is_file(), (
        "log.md exists after a refused --create — the scaffold + first "
        "entry must be all-or-nothing"
    )


def test_log_create_unlink_recreate_clean_after_failed_first_call(tmp_path):
    """OC phase-3 finding: locking `log.md`'s own inode races with the
    cap-overflow unlink path. A first `--create` caller that fails the
    scaffold-cap check unlinks the freshly-created empty file; a second
    caller that had already opened the same inode and was waiting on
    flock could then write to an inode no longer linked at `log.md`
    (data lost). The fix moves the lock to the parent directory's inode,
    which is stable across our own unlink/replace.

    Single-process regression covering the underlying mechanism: a refused
    `--create` must leave the parent directory in a state where a
    subsequent `--create` with a fit-sized entry scaffolds and persists
    cleanly. (The pure concurrent variant requires fault injection to
    trigger deterministically; this test pins the sequential primitive.)
    """
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| log.md | 20 |\n"
    )
    # First --create: huge entry overflows scaffold+entry → refuses + unlinks.
    rc1, _, _ = _run([
        "--wiki", str(wiki), "log", "--create", "--raw",
        "- [t] HUGE " + "x" * 100,
    ])
    assert rc1 != 0
    assert not (wiki / "log.md").is_file(), (
        "refused --create must unlink the empty scaffold"
    )
    # Relax the cap so a second --create with a small entry fits cleanly.
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n|---|---|\n| log.md | 200 |\n"
    )
    rc2, _, _ = _run([
        "--wiki", str(wiki), "log", "--create", "--raw", "- [t] OK",
    ])
    assert rc2 == 0
    assert (wiki / "log.md").is_file()
    body = (wiki / "log.md").read_text()
    assert "OK" in body


def _space_log_create_worker(args):
    """Module-level worker for the --create contention test (pickleable)."""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from wiki_spaces import space as _space
    wiki_str, i = args
    line = f"- [t] FIT n={i:03d}"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return _space.main([
            "--wiki", wiki_str, "log", "--create", "--raw", line,
        ])


def test_space_log_create_concurrent_callers_all_persist(tmp_path):
    """Concurrent `--create` callers that all fit: each successful worker's
    entry must land on disk. Under the old per-file flock design, the
    first caller's `os.open(O_CREAT|O_RDWR)` raced with the second
    caller's create-then-flock — if the cap check failed on caller A
    and A unlinked the empty file mid-flight, caller B's write went
    to an unlinked inode. With the parent-directory flock, the
    create/unlink decisions serialize so every fit-sized entry that
    reports rc=0 is present on disk afterward.
    """
    import multiprocessing
    import os as _os
    if _os.name == "nt":
        pytest.skip("flock-based atomicity not enforced on Windows")
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires fork-capable multiprocessing")
    wiki = _make_wiki(tmp_path)
    # No `_meta/limits.md` → default `log.md` cap (100K) admits every entry.
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(processes=8) as pool:
        args = [(str(wiki), i) for i in range(40)]
        results = pool.map(_space_log_create_worker, args)
    assert all(r == 0 for r in results), f"some workers failed: {results}"
    assert (wiki / "log.md").is_file(), "log.md missing after --create contention"
    body = (wiki / "log.md").read_text()
    found = sum(
        1 for i in range(40) if f"FIT n={i:03d}" in body
    )
    assert found == 40, (
        f"expected 40 lines on disk, got {found}; "
        "the --create race lost entries (parent-dir lock missing?)"
    )


def test_log_rotation_refuses_archive_write_when_archive_would_exceed_cap(tmp_path):
    """Archive writes are themselves framework writes; the v1 contract
    says every framework write enforces a cap. The default
    `log.archive-*.md` cap is 100K (same as `log.md`), but a user can
    configure a tighter archive cap. Rotation must refuse if the
    projected archive content would overflow its cap — otherwise a
    framework write puts an over-cap file on disk that `space audit`
    later flags."""
    wiki = _make_wiki(tmp_path)
    log = wiki / "log.md"
    # Seed 6 entries.
    seed_entries = "\n".join(
        f"- [2026-01-0{i+1}T00:00:00Z] OP n={i} payload={'x' * 60}"
        for i in range(6)
    ) + "\n"
    log.write_text("# Log\n" + seed_entries)
    # log.md = 1000 cap; archive cap = 50 (extremely tight). The kept
    # half is ~500 chars (so log.md fits), but the archive half is ~250
    # which exceeds the 50-char archive cap → must refuse.
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| log.md | 600 |\n"
        "| log.archive-*.md | 50 |\n"
    )
    huge_entry = "- [2026-01-10T00:00:00Z] " + ("y" * 200)
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", huge_entry])
    assert rc != 0
    assert "archive" in err
    # No archive landed on disk (refusal happened before write).
    archives = sorted(wiki.glob("log.archive-*.md"))
    assert archives == [], (
        f"rotation wrote an over-cap archive {archives} — the cap must "
        "be enforced before the archive write"
    )


def test_log_rotation_does_not_partially_commit_when_post_rotation_still_over_cap(tmp_path):
    """When rotation can't satisfy the cap (a single oversized entry against
    a tight cap), the rotation must refuse BEFORE writing the archive +
    truncating log.md — otherwise the user ends up with an archive file
    on disk, a truncated log, but no new entry committed (partial
    mutation). Pre-flight the projected post-rotation size against the
    cap; if still over, raise BEFORE any FS write."""
    wiki = _make_wiki(tmp_path)
    log = wiki / "log.md"
    # Seed with several real entries so rotation would split if allowed.
    seed_entries = "\n".join(
        f"- [2026-01-0{i+1}T00:00:00Z] OP n={i}" for i in range(6)
    ) + "\n"
    log.write_text("# Log\n" + seed_entries)
    log_size_before = log.stat().st_size
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern | Cap (chars) |\n"
        "|---|---|\n"
        "| log.md | 100 |\n"
    )
    # Each existing entry is ~30 chars; the kept half is ~90; adding
    # this entry (>50 chars) would still overflow. The rotation must
    # refuse without writing an archive or truncating the log.
    huge_entry = "- [2026-01-10T00:00:00Z] " + ("y" * 200)
    rc, _, err = _run(["--wiki", str(wiki), "log", "--raw", huge_entry])
    assert rc != 0
    # No archive file landed on disk.
    archives = sorted(wiki.glob("log.archive-*.md"))
    assert archives == [], (
        f"rotation wrote an archive ({archives}) despite refusing the "
        "post-rotation fit check — the rotation must be all-or-nothing"
    )
    # Log untouched.
    assert log.stat().st_size == log_size_before, (
        "log.md was truncated despite the rotation refusal"
    )


def test_audit_summary_respects_include_external(tmp_path):
    """`audit --include-external` summary's page count includes external
    pages — otherwise the report's claim "owned scope; excludes external"
    contradicts the per-page checks below it that DO walk external."""
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# t\n\n## Spaces\n\n")
    (wiki / "shared" / "team" / "page.md").write_text("# external page\n")
    idx = wiki / "index.md"
    idx.write_text(idx.read_text() + "- [shared/team/](shared/team/index.md)\n")

    rc_def, out_def, _ = _run(["--wiki", str(wiki), "audit"])
    rc_ext, out_ext, _ = _run(["--wiki", str(wiki), "audit", "--include-external"])
    assert rc_def == 0 and rc_ext == 0
    # The default scope counts only the owned `index.md`; the external-
    # inclusive scope counts the external `index.md` and `page.md` too.
    assert "1 markdown files (owned scope" in out_def
    # External scope: at least 3 (root, shared/team/index.md, shared/team/page.md)
    assert "owned + external scope" in out_ext


def test_audit_excludes_archives_space_from_drift(tmp_path):
    """A space under `_archives/` is retired content — not flagged as a
    missing `## Spaces` entry."""
    wiki = _make_wiki(tmp_path)
    archived = wiki / "_archives" / "old-space"
    archived.mkdir(parents=True)
    (archived / "index.md").write_text("# old-space")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0, out
    assert "old-space" not in out


def test_audit_embed_of_asset_not_broken(tmp_path):
    """`![[image.png]]` / `![[doc.pdf]]` embed non-page assets — never flagged
    as broken wikilinks."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\n![[diagram.png]] and ![[report.pdf]]\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert "! broken wikilink" not in out
    assert "diagram.png" not in out
    assert "report.pdf" not in out


def test_audit_embed_of_existing_note_counts_as_incoming(tmp_path):
    """An embed `![[note]]` of a real page is a reference — the embedded page
    is not an orphan."""
    wiki = _make_wiki(tmp_path)
    (wiki / "host.md").write_text("# Host\n\n![[embedded]]\n")
    (wiki / "embedded.md").write_text("# Embedded\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    orphan_section = out.split("orphans:", 1)[1] if "orphans:" in out else ""
    assert "embedded.md" not in orphan_section


def test_audit_plain_link_still_broken(tmp_path):
    """The embed exemption must not suppress genuine broken plain links."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\nplain [[missing]] link\n")
    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "! broken wikilink [[missing]]" in out


# ---------- space mount ----------


def test_mount_symlink_creates_link_and_registers(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "external-src", "team")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 0, out
    link = wiki / "shared" / "team"
    assert link.is_symlink()
    assert (link / "index.md").is_file()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "shared/team" in e.href for e in entries)


def test_remove_symlink_mounted_space_unmounts_cleanly(tmp_path):
    """B1: mount↔unmount symmetry — a `--mode symlink` mount removes cleanly.
    `remove` must `unlink()` the symlink rather than `rmtree` it (rmtree
    raises on a symlink, and the old code then failed to restore it), and the
    foreign source content stays put — only the link and its `## Spaces`
    entry go away."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "external-src", "team")
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 0
    link = wiki / "shared" / "team"
    assert link.is_symlink()

    rc, _, err = _run(
        ["--wiki", str(wiki), "remove", "shared/team", "--force-external"]
    )
    assert rc == 0, err
    # The symlink is gone (lexists is False even for a broken link).
    assert not os.path.lexists(link)
    # The foreign source survives — we removed the link, not the content.
    assert (src / "index.md").is_file()
    # The `## Spaces` entry is gone.
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert not any(e.href and "shared/team" in e.href for e in entries)


def test_remove_clone_mounted_space_deletes_tree(tmp_path):
    """B1 regression guard: the non-symlink branch still rmtrees a real cloned
    tree — branching the symlink delete must not break the directory path. The
    upstream repo is untouched."""
    wiki = _make_wiki(tmp_path)
    repo = _make_git_repo(tmp_path / "repo", "team")
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(repo), "shared/team", "--mode", "clone"]
    )
    assert rc == 0
    clone = wiki / "shared" / "team"
    assert clone.is_dir() and not clone.is_symlink()

    rc, _, err = _run(
        ["--wiki", str(wiki), "remove", "shared/team", "--force", "--force-external"]
    )
    assert rc == 0, err
    assert not clone.exists()
    assert (repo / "index.md").is_file()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert not any(e.href and "shared/team" in e.href for e in entries)


def test_mount_symlink_source_without_index_refused(tmp_path):
    """A mount target with no index.md is not a wiki-spaces space — refuse it
    and leave nothing behind."""
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "not-a-wiki"
    src.mkdir()
    (src / "notes.md").write_text("# notes")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/x", "--mode", "symlink"]
    )
    assert rc == 1
    assert "index.md" in err
    assert not (wiki / "shared" / "x").exists()
    assert not (wiki / "shared" / "x").is_symlink()


def test_mount_inserts_spaces_into_bare_ancestor_and_registers(tmp_path):
    """Like `space add`, mount auto-inserts `## Spaces` into a bare-ancestor
    `index.md` via the chain helper rather than refusing. Both operations
    walk through the same code path now; replaces the pre-v1 refusal."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    src = _make_space_dir(tmp_path / "src")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "team", "--mode", "symlink"]
    )
    assert rc == 0, err
    assert (wiki / "team").is_symlink()
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "team" in e.href for e in entries)


def test_mount_refuses_register_into_external_shared_ancestor(tmp_path):
    """The reproduced bug: `mount <src> shared/team/nested` where shared/team
    is an EXTERNAL space exited 0 and appended `- [nested/]` to the EXTERNAL
    shared/team/index.md. The chain helper walks UP and writes every ancestor;
    shared/team is external scope. Now refused before any FS mutation, naming
    the external ancestor — mutating NOTHING."""
    wiki = _make_wiki(tmp_path)
    # External space (under shared/) WITH `## Spaces`, built directly so it
    # bypasses add's own target-level guard.
    team = wiki / "shared" / "team"
    team.mkdir(parents=True)
    (team / "index.md").write_text("# team\n\n## Spaces\n\n")
    team_before = (team / "index.md").read_text()
    src = _make_space_dir(tmp_path / "src")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team/nested",
         "--mode", "symlink"]
    )
    assert rc != 0
    assert "external" in err
    assert "shared/team" in err
    # The EXTERNAL ancestor's index.md is byte-identical — nothing appended.
    assert (team / "index.md").read_text() == team_before
    team_entries = _md.parse_section_entries(
        (team / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "nested" in e.href for e in team_entries)
    # No symlink created at the leaf.
    assert not (team / "nested").exists()
    assert not (team / "nested").is_symlink()
    # Wiki root gained no entry either.
    root_entries = _md.parse_section_entries(
        (wiki / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "shared/team" in e.href for e in root_entries)


def test_mount_refuses_register_into_foreign_submodule_ancestor(tmp_path):
    """Second reproduced case: the external ancestor is a foreign-origin
    submodule. The guard reuses `_is_in_external_scope`, so it agrees with
    audit/list/remove on the foreign-submodule classification — refuse and
    mutate nothing."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    team = wiki / "team"
    team.mkdir()
    (team / "index.md").write_text("# team\n\n## Spaces\n\n")
    team_before = (team / "index.md").read_text()
    (wiki / ".gitmodules").write_text(
        '[submodule "team"]\n'
        "\tpath = team\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    src = _make_space_dir(tmp_path / "src")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "team/nested",
         "--mode", "symlink"]
    )
    assert rc != 0
    assert "external" in err
    assert "team" in err
    assert (team / "index.md").read_text() == team_before
    team_entries = _md.parse_section_entries(
        (team / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "nested" in e.href for e in team_entries)
    assert not (team / "nested").exists()
    assert not (team / "nested").is_symlink()
    root_entries = _md.parse_section_entries(
        (wiki / "index.md").read_text(), "Spaces"
    )
    assert not any(e.href and "team/" in e.href for e in root_entries)


def test_mount_default_shared_foo_still_registers_into_owned_root(tmp_path):
    """REGRESSION GUARD for the locked-decision critical case. The default
    `mount shared/foo` must keep working: `shared/` is a plain folder (no
    index.md), so the nearest ANCESTOR SPACE is the OWNED wiki root. The guard
    keys off the ancestor index being written (owned root), NOT off dest being
    under shared/."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/foo", "--mode", "symlink"]
    )
    assert rc == 0, out
    assert (wiki / "shared" / "foo").is_symlink()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "shared/foo" in e.href for e in entries)


def test_mount_refused_when_dest_exists(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "occupied").mkdir()
    src = _make_space_dir(tmp_path / "src")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "occupied", "--mode", "symlink"]
    )
    assert rc == 2
    assert "already exists" in err


def test_mount_rejects_dotdot_path(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src")
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "../escape", "--mode", "symlink"]
    )
    assert rc == 2


def test_mount_submodule_refused_when_wiki_not_git(tmp_path):
    """`--mode submodule` needs the wiki itself to be a git repo."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", "https://example.invalid/x.git",
         "shared/x", "--mode", "submodule"]
    )
    assert rc == 2
    assert "git repo" in err


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_mount_clone_copies_and_registers(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_git_repo(tmp_path / "src-repo", "team-wiki")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "clone"]
    )
    assert rc == 0, out
    assert (wiki / "shared" / "team" / "index.md").is_file()
    assert (wiki / "shared" / "team" / ".git").exists()
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.href and "shared/team" in e.href for e in entries)


def test_mount_symlink_bad_source_leaves_no_parent_dir(tmp_path):
    """A nonexistent symlink source is rejected before any directory is
    created — no empty `shared/` left behind."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(tmp_path / "nope"), "shared/team",
         "--mode", "symlink"]
    )
    assert rc == 2
    assert not (wiki / "shared").exists()


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_mount_clone_without_index_is_cleaned_up(tmp_path):
    """A clone of a repo with no index.md is not a wiki-spaces space — the
    command refuses it and removes the clone it just made."""
    wiki = _make_wiki(tmp_path)
    src = _make_git_repo(tmp_path / "src-repo", with_index=False)
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "clone"]
    )
    assert rc == 1
    assert "index.md" in err
    assert not (wiki / "shared" / "team").exists()


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_mount_submodule_bare_target_auto_rolls_back(tmp_path, monkeypatch):
    """A submodule that resolves to a bare `index.md` (no `## Spaces`) is
    refused per the v1 strict-target contract. The rollback runs the
    standard undo sequence (submodule deinit, git rm, prune .git/modules
    cache) — no leftover gitlink, .gitmodules entry, or modules cache."""
    import os
    import subprocess
    # Wiki itself must be a git repo for `git submodule add`.
    wiki = _make_wiki(tmp_path)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    }
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=wiki, check=True, env=env, capture_output=True)
    # Allow file:// submodule sources for the rest of the test (git refuses
    # them by default since 2.38 CVE-2022-39253). GIT_CONFIG_COUNT/KEY/VALUE
    # injects the override into every git subprocess _run_git spawns.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    src = _make_git_repo_with_bare_index(tmp_path / "src-repo")
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", f"file://{src}", "shared/team",
         "--mode", "submodule"]
    )
    # Mount strict-refuses the bare-`## Spaces` target.
    assert rc == 1, f"expected refusal exit 1, got {rc}: {err}"
    assert "`## Spaces` section" in err
    # Auto-rollback removed the submodule + cache + .gitmodules entry.
    assert not (wiki / "shared" / "team").exists(), \
        "submodule path should be gone after rollback"
    assert not (wiki / ".git" / "modules" / "shared" / "team").exists(), \
        ".git/modules cache should be pruned after rollback"
    gitmodules = wiki / ".gitmodules"
    if gitmodules.is_file():
        assert "shared/team" not in gitmodules.read_text(), \
            ".gitmodules should not reference the rolled-back submodule"
    # Index entry was never added (mount refused before chain helper ran).
    assert "shared/team" not in (wiki / "index.md").read_text()


# ---------- _derive_default_path (unit) ----------

def test_derive_default_https_with_dot_git():
    p, err = space._derive_default_path("https://github.com/foo/bar.git")
    assert err is None
    assert p == "shared/bar"


def test_derive_default_https_with_query_dropped_first():
    """Query is dropped BEFORE .git stripping, so .git?ref=main resolves to bar."""
    p, err = space._derive_default_path("https://github.com/foo/bar.git?ref=main")
    assert err is None
    assert p == "shared/bar"


def test_derive_default_scp_style_with_fragment():
    """Fragment dropped first; scp-style tail split after `:`."""
    p, err = space._derive_default_path("git@github.com:foo/bar.git#tag")
    assert err is None
    assert p == "shared/bar"


def test_derive_default_local_trailing_slash():
    p, err = space._derive_default_path("/home/me/notes/")
    assert err is None
    assert p == "shared/notes"


def test_derive_default_local_path_no_slash_in_tail():
    p, err = space._derive_default_path("/home/me/personal-notes")
    assert err is None
    assert p == "shared/personal-notes"


def test_derive_default_rejects_empty_source():
    p, err = space._derive_default_path("")
    assert p is None
    assert err is not None


def test_derive_default_rejects_just_root_slash():
    p, err = space._derive_default_path("/")
    assert p is None
    assert err is not None
    assert "empty" in err.lower() or "basename" in err.lower()


def test_derive_default_rejects_dot_git_basename():
    """An input whose tail IS `.git` (not just ends in it) is rejected to avoid
    pointing at a bare-repo directory."""
    p, err = space._derive_default_path("/home/me/.git")
    assert p is None
    assert err is not None
    assert "." in err  # mentions the dot-prefix problem


def test_derive_default_strips_dot_git_suffix_only_not_when_tail_is_dot_git():
    """`repo.git` → `repo`; but `.git` (tail equals `.git`) is rejected."""
    p1, _ = space._derive_default_path("https://x/y/repo.git")
    assert p1 == "shared/repo"
    p2, err2 = space._derive_default_path("https://x/y/.git")
    assert p2 is None
    assert err2 is not None


# ---------- mount: --mode ----------

def test_mount_mode_works_canonically(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 0, out
    assert (wiki / "shared" / "team").is_symlink()


def test_mount_missing_mode_fails(tmp_path):
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    with pytest.raises(SystemExit):
        _run(["--wiki", str(wiki), "mount", str(src), "shared/team"])


def test_mount_symlink_failure_removes_created_parent_dirs(tmp_path):
    """A mount that fails AFTER creating new parent dirs must remove them
    (HANDBOOK: writes are fail-closed), mirroring `cmd_add`'s created-dir
    rollback. Symlinking a non-space source (no `index.md`) into a fresh
    `shared/sub/` subtree passes the symlink step but fails validation and
    rolls the symlink back — the empty `shared/sub` and `shared` it created
    must not survive."""
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "notaspace"
    src.mkdir()
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/sub/team",
         "--mode", "symlink"]
    )
    assert rc == 1
    assert not (wiki / "shared" / "sub").exists()
    assert not (wiki / "shared").exists()


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_mount_clone_failure_removes_created_parent_dirs(tmp_path):
    """The mount-mechanism failure path is fail-closed too: a clone from a
    nonexistent source into a fresh `shared/sub/` subtree fails the `git
    clone` itself, and the parent dirs created for it must not be left
    behind."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(
        ["--wiki", str(wiki), "mount", str(tmp_path / "nope"),
         "shared/sub/team", "--mode", "clone"]
    )
    assert rc == 1
    assert not (wiki / "shared" / "sub").exists()
    assert not (wiki / "shared").exists()


# ---------- mount: optional path → default derivation ----------

def test_mount_optional_path_derives_shared_basename(tmp_path):
    wiki = _make_wiki(tmp_path)
    # The default-path basename is taken from the SOURCE path's tail, so
    # we name the source directory itself `my-team-wiki`.
    src = _make_space_dir(tmp_path / "my-team-wiki", "Team Wiki")
    rc, out, _ = _run(
        ["--wiki", str(wiki), "mount", str(src), "--mode", "symlink"]
    )
    assert rc == 0, out
    # Default-derived path is `shared/my-team-wiki/`.
    assert (wiki / "shared" / "my-team-wiki").is_symlink()


def test_mount_optional_path_rejects_when_basename_starts_with_dot(tmp_path):
    wiki = _make_wiki(tmp_path)
    src_parent = tmp_path / "src-parent"
    src_parent.mkdir()
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src_parent / ".git"), "--mode", "symlink"]
    )
    # Default derivation rejects `.git` basename before symlink validation.
    assert rc == 2
    assert "." in err


# ---------- mount: --dry-run ----------

def test_mount_dry_run_no_fs_mutation_symlink(tmp_path):
    """--dry-run prints the plan and touches nothing: no symlink, no parent
    dir created, parent index byte-identical."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    index_before = (wiki / "index.md").read_bytes()
    assert not (wiki / "shared").exists()  # baseline
    rc, out, _ = _run([
        "--wiki", str(wiki), "mount", str(src), "shared/team",
        "--mode", "symlink", "--dry-run",
    ])
    assert rc == 0
    assert "(dry-run)" in out
    assert not (wiki / "shared").exists()  # no mkdir
    assert not (wiki / "shared" / "team").exists()  # no symlink
    assert (wiki / "index.md").read_bytes() == index_before


def test_mount_dry_run_does_not_mutate_bare_ancestor(tmp_path):
    """Dry-run must not insert `## Spaces` into a bare-`index.md` ancestor —
    the section is inserted only when the mount actually commits."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    src = _make_space_dir(tmp_path / "src", "team")
    before = (wiki / "index.md").read_text()
    rc, _, err = _run([
        "--wiki", str(wiki), "mount", str(src), "team",
        "--mode", "symlink", "--dry-run",
    ])
    assert rc == 0, err
    assert (wiki / "index.md").read_text() == before
    assert not (wiki / "team").exists()


# ---------- mount: --name ----------

def test_mount_name_overrides_parent_label_only(tmp_path):
    """--name affects only the parent's ## Spaces entry label; the child's
    index.md is untouched."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "external-team")
    child_index_before = (src / "index.md").read_bytes()
    rc, out, _ = _run([
        "--wiki", str(wiki), "mount", str(src), "shared/team",
        "--mode", "symlink", "--name", "Team Wiki",
    ])
    assert rc == 0, out
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert any(e.label == "Team Wiki" for e in entries), \
        f"expected 'Team Wiki' label, got entries: {[(e.label, e.href) for e in entries]}"
    # Child untouched. (Symlink follows; reading via src is fine.)
    assert (src / "index.md").read_bytes() == child_index_before


# ---------- mount: atomicity ----------

def test_mount_rolls_back_when_index_write_fails(tmp_path, monkeypatch):
    """Force `os.replace` to fail during registration; assert the mount was
    rolled back (symlink removed) and the parent index is unchanged."""
    wiki = _make_wiki(tmp_path)
    src = _make_space_dir(tmp_path / "src", "team")
    index_before = (wiki / "index.md").read_bytes()

    real_replace = space.os.replace
    call_count = {"n": 0}

    def boom(src_path, dst_path, *a, **kw):
        # Only sabotage the index write (the .tmp → index.md replace),
        # not any unrelated os.replace call.
        if str(dst_path).endswith("index.md"):
            call_count["n"] += 1
            raise OSError("forced replace failure for atomicity test")
        return real_replace(src_path, dst_path, *a, **kw)

    monkeypatch.setattr(space.os, "replace", boom)
    rc, _, err = _run(
        ["--wiki", str(wiki), "mount", str(src), "shared/team", "--mode", "symlink"]
    )
    assert rc == 1
    assert call_count["n"] == 1
    assert "ensure-chain failed" in err
    # Symlink rolled back.
    assert not (wiki / "shared" / "team").exists()
    assert not (wiki / "shared" / "team").is_symlink()
    # Parent index byte-identical.
    assert (wiki / "index.md").read_bytes() == index_before
    # No leftover tempfile in the ancestor dir.
    leftover_tmps = [p for p in wiki.iterdir() if p.name.startswith(".index.") and p.name.endswith(".tmp")]
    assert leftover_tmps == [], f"tempfile leak: {leftover_tmps}"


def test_adopt_registers_nested_space_no_audit_drift(tmp_path):
    """Adopting a folder that already has a nested space: `init --adopt`
    walks the tree, registers every space in its nearest ancestor's
    `## Spaces`, so audit reports zero drift on day 1."""
    folder = tmp_path / "my-notes"
    nested = folder / "projects" / "foo"
    nested.mkdir(parents=True)
    (nested / "index.md").write_text("# foo")
    assert init_wiki.main([str(folder), "--no-config", "--adopt"]) == 0
    # `## Spaces` is always present (every CLI-created wiki has it from t=0),
    # AND the nested space has been registered in it.
    root_index = (folder / "index.md").read_text()
    assert "## Spaces" in root_index
    assert "projects/foo/" in root_index
    rc, out, _ = _run(["--wiki", str(folder), "audit"])
    assert rc == 0, out
    assert "missing entry" not in out


def test_adopt_skips_shared_with_stderr_notice(tmp_path):
    """`--adopt` does NOT register sub-spaces under `shared/` (classified
    external) and emits a per-skip notice to stderr unless --include-external."""
    folder = tmp_path / "my-notes"
    shared_nested = folder / "shared" / "baz"
    shared_nested.mkdir(parents=True)
    (shared_nested / "index.md").write_text("# baz")
    owned_nested = folder / "projects" / "foo"
    owned_nested.mkdir(parents=True)
    (owned_nested / "index.md").write_text("# foo")

    import io
    from contextlib import redirect_stdout, redirect_stderr

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = init_wiki.main([str(folder), "--no-config", "--adopt"])
    assert rc == 0
    root_index = (folder / "index.md").read_text()
    assert "projects/foo/" in root_index
    assert "shared/baz" not in root_index
    assert "shared/baz" in err.getvalue() or "shared/" in err.getvalue()
# ---------- _is_in_external_scope: ancestor-walking trust-scope preflight ----------

def _space_log_worker(args):
    """Module-level worker for the contention test (must be pickleable for
    multiprocessing.Pool — closures can't cross the fork boundary)."""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from wiki_spaces import space as _space
    wiki_str, i = args
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        # Structured form — CLI prepends the timestamp itself. log.md is
        # pre-created by the test setup, so no `--create` needed.
        return _space.main([
            "--wiki", wiki_str, "log",
            "CONTEND", "--field", f"value={i}",
        ])


def test_space_log_refuses_when_log_md_absent(tmp_path):
    """PR-H made logging opt-in: when `log.md` doesn't exist, `space log`
    refuses with a hint about `--create` / `init --with log.md`."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "log", "SEARCH", "--field", "query=foo",
    ])
    assert rc == 2
    assert "log.md" in err
    assert "--create" in err
    assert not (wiki / "log.md").exists()


def test_space_log_create_flag_creates_log_md(tmp_path):
    """`--create` is the documented opt-in: scaffolds `log.md` then appends."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki), "log", "SEARCH",
        "--field", "query=sourdough",
        "--create",
    ])
    assert rc == 0
    body = (wiki / "log.md").read_text()
    assert "SEARCH query=sourdough" in body


def test_space_log_structured_field_prepends_iso_timestamp(tmp_path):
    """Structured form: CLI emits `- [<ISO-8601 UTC>] OPERATION key=value`."""
    import re
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("# Log\n")
    rc, _, _ = _run([
        "--wiki", str(wiki), "log", "tend",  # lowercased → CLI uppercases
        "--field", "mode=audit",
        "--field", "issues_found=3",
    ])
    assert rc == 0
    body = (wiki / "log.md").read_text()
    last = [ln for ln in body.splitlines() if ln.startswith("- [")][-1]
    # Format: `- [YYYY-MM-DDTHH:MM:SSZ] TEND mode=audit issues_found=3`
    assert re.match(
        r"- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] TEND mode=audit issues_found=3",
        last,
    ), f"unexpected line shape: {last!r}"


def test_space_log_raw_form_does_not_format(tmp_path):
    """--raw bypasses the structured formatter; the line lands verbatim."""
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("# Log\n")
    rc, _, _ = _run([
        "--wiki", str(wiki), "log",
        "--raw", "- custom shape no timestamp",
    ])
    assert rc == 0
    body = (wiki / "log.md").read_text()
    assert "- custom shape no timestamp" in body


def test_space_log_structured_rejects_newlines_in_operation_and_fields(tmp_path):
    """The structured form (PR-H §13) exists to fence off LLM-formatting
    defects — `- [TIMESTAMP] OP key=value …` must fit on one bullet line.
    Newlines or other control chars in OPERATION / --field key / --field
    value would split the bullet across physical lines and break the
    log.md structure documented in CONVENTIONS / log.md. The CLI rejects;
    --raw stays as the escape hatch for callers that really need custom
    shapes.
    """
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("# Log\n")
    initial = (wiki / "log.md").read_text()

    # Newline in OPERATION
    rc, _, err = _run([
        "--wiki", str(wiki), "log", "SEARCH\ninjected",
        "--field", "q=ok",
    ])
    assert rc == 2
    assert "newline" in err.lower() or "control" in err.lower()
    assert (wiki / "log.md").read_text() == initial  # nothing written

    # Newline in --field value
    rc, _, err = _run([
        "--wiki", str(wiki), "log", "SEARCH",
        "--field", "q=line1\nline2",
    ])
    assert rc == 2
    assert "newline" in err.lower() or "control" in err.lower()
    assert (wiki / "log.md").read_text() == initial

    # Newline in --field key
    rc, _, err = _run([
        "--wiki", str(wiki), "log", "SEARCH",
        "--field", "bad\nkey=value",
    ])
    assert rc == 2
    assert (wiki / "log.md").read_text() == initial

    # --raw still allows multi-line content (escape hatch, caller takes
    # responsibility).
    rc, _, _ = _run([
        "--wiki", str(wiki), "log",
        "--raw", "- custom\nmulti-line",
    ])
    assert rc == 0
    assert "multi-line" in (wiki / "log.md").read_text()


def test_space_log_atomic_under_contention(tmp_path):
    """100 concurrent `space log` invocations via multiprocessing.Pool —
    every line lands in log.md with no losses. Exercises the flock-
    protected critical section in `_log.append_log_with_rotation`."""
    import multiprocessing
    import os
    if os.name == "nt":  # pragma: no cover
        pytest.skip("flock-based atomicity not enforced on Windows")
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires fork-capable multiprocessing")
    wiki = _make_wiki(tmp_path)
    # PR-H: pre-create log.md (the workers no longer auto-create).
    (wiki / "log.md").write_text("# Log\n")

    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(processes=8) as pool:
        args = [(str(wiki), i) for i in range(100)]
        results = pool.map(_space_log_worker, args)
    assert all(r == 0 for r in results), f"some workers failed: {results}"
    body = (wiki / "log.md").read_text()
    written = sum(1 for line in body.splitlines() if "CONTEND value=" in line)
    assert written == 100, f"expected 100 lines, got {written}"


def test_space_manifest_subcommand_removed(tmp_path):
    """PR-I demoted `.manifest.json` writes from a CLI subcommand to a
    CONVENTIONS.md snippet (use an inline `flock` block from the ws-update
    skill). `space manifest` should no longer exist in the argparse surface."""
    wiki = _make_wiki(tmp_path)
    with pytest.raises(SystemExit):
        _run(["--wiki", str(wiki), "manifest", "set", "p", "k", "v"])


def _force_chain_helper_failure(monkeypatch):
    """Make every ancestor write inside the chain helper fail. PR-D routes
    `cmd_add`'s registration through `_ensure_spaces_chain_and_register`,
    which calls `_atomic_mutate_index` per ancestor edge. Patching the
    helper itself is the cleanest way to exercise the rollback paths."""
    monkeypatch.setattr(
        space._core, "_atomic_mutate_index",
        lambda ancestor, ancestor_index, mutate_fn: (1, "simulated write failure"),
    )


def test_space_add_rollback_removes_created_dir_on_registration_failure(tmp_path, monkeypatch):
    """When the chain helper fails mid-registration, rollback must remove
    the dir and its index.md."""
    wiki = _make_wiki(tmp_path)
    _force_chain_helper_failure(monkeypatch)

    rc, _, err = _run(["--wiki", str(wiki), "add", "newproj"])
    assert rc == 1
    assert "simulated write failure" in err
    # The created directory must be gone after rollback.
    assert not (wiki / "newproj").exists()
    # The ancestor's ## Spaces should not have a stray entry.
    root_index = (wiki / "index.md").read_text()
    assert "newproj" not in root_index


def test_space_add_rollback_removes_created_dir_on_index_write_failure(tmp_path, monkeypatch):
    """HANDBOOK: writes are atomic and fail-closed. If the index write itself
    fails (disk-full / unwritable / a race after mkdir), `add` must roll back
    the directory it created and refuse cleanly — not leave an orphaned empty
    dir behind plus a raw traceback. The mkdir failure and the chain-helper
    failure were already rolled back; the `atomic_write` between them was not."""
    from wiki_spaces.space import add as add_mod
    wiki = _make_wiki(tmp_path)

    def boom(_path, _content):
        raise OSError("simulated index write failure")

    monkeypatch.setattr(add_mod, "atomic_write", boom)
    rc, _, err = _run(["--wiki", str(wiki), "add", "newproj"])
    assert "Traceback" not in err
    assert rc == 1
    assert "could not" in err.lower()
    # Fail-closed: the directory this call created must be gone.
    assert not (wiki / "newproj").exists()
    # And no stray ## Spaces entry was left in the ancestor.
    assert "newproj" not in (wiki / "index.md").read_text()


def test_space_add_rollback_preserves_preexisting_dir(tmp_path, monkeypatch):
    """If the target dir EXISTED before the call, rollback must NOT delete
    it — only the index.md we wrote (if we wrote it) should go away."""
    wiki = _make_wiki(tmp_path)
    (wiki / "newproj").mkdir()
    (wiki / "newproj" / "user-file.txt").write_text("user content")
    _force_chain_helper_failure(monkeypatch)

    rc, _, _ = _run(["--wiki", str(wiki), "add", "newproj"])
    assert rc == 1
    # Pre-existing user content must survive.
    assert (wiki / "newproj").exists()
    assert (wiki / "newproj" / "user-file.txt").read_text() == "user content"


def test_add_rollback_removes_created_parent_folders(tmp_path, monkeypatch):
    """`space add a/b/c/d` against an empty wiki creates a/, a/b/, a/b/c/,
    a/b/c/d/ via mkdir(parents=True). When registration fails, rollback must
    remove every parent it created — not just the leaf."""
    wiki = _make_wiki(tmp_path)
    _force_chain_helper_failure(monkeypatch)

    rc, _, err = _run(["--wiki", str(wiki), "add", "a/b/c/d"])
    assert rc == 1
    assert "simulated write failure" in err
    # Every directory created by this call must be removed (deepest-first).
    assert not (wiki / "a" / "b" / "c" / "d").exists()
    assert not (wiki / "a" / "b" / "c").exists()
    assert not (wiki / "a" / "b").exists()
    assert not (wiki / "a").exists()


def test_add_rollback_only_removes_empty_parents(tmp_path, monkeypatch):
    """If one of the parent dirs already had user content, rollback removes
    the ones it created but leaves a non-empty pre-existing parent intact."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a").mkdir()
    (wiki / "a" / "user-file.txt").write_text("user content")
    _force_chain_helper_failure(monkeypatch)

    rc, _, _ = _run(["--wiki", str(wiki), "add", "a/b/c"])
    assert rc == 1
    # `a/` pre-existed and has user content — must survive.
    assert (wiki / "a" / "user-file.txt").read_text() == "user content"
    # `a/b/` and `a/b/c/` were created by this call — must be gone.
    assert not (wiki / "a" / "b").exists()


def test_space_remove_rollback_restores_dir_on_rmtree_failure(tmp_path, monkeypatch):
    """When rmtree fails mid-delete (some files gone, others remain),
    rollback restores the directory byte-for-byte AND re-adds the index
    entry that was just removed."""
    wiki = _make_wiki(tmp_path)
    (wiki / "doomed").mkdir()
    (wiki / "doomed" / "index.md").write_text("# Doomed\n\nstuff\n")
    (wiki / "doomed" / "extra.md").write_text("user content here")
    # Pre-register so the ancestor's ## Spaces has the entry to remove.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "doomed"])
    assert rc == 0

    real_rmtree = space.shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        # Let the snapshot copytree call succeed (it goes elsewhere).
        if "wiki-spaces-remove-" in str(path):
            return real_rmtree(path, *args, **kwargs)
        raise OSError("simulated rmtree failure")

    monkeypatch.setattr(space.shutil, "rmtree", failing_rmtree)

    rc, _, err = _run([
        "--wiki", str(wiki), "remove", "doomed", "--force",
    ])
    assert rc == 2
    assert "rmtree failed" in err
    # Directory must be restored.
    assert (wiki / "doomed").exists()
    assert (wiki / "doomed" / "extra.md").read_text() == "user content here"
    # Index entry must be restored.
    root_index = (wiki / "index.md").read_text()
    assert "doomed/" in root_index


def test_space_remove_rollback_preserves_in_tree_symlink(tmp_path, monkeypatch):
    """On the rmtree-failure rollback path, an in-tree symlink child must be
    restored AS a symlink, not as a dereferenced regular-file copy. The
    snapshot/restore use copytree(symlinks=True); symlinks=False silently
    changed the on-disk shape of content the remove was meant to preserve."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "doomed"])
    assert rc == 0
    (wiki / "doomed" / "real.md").write_text("# real target\n")
    os.symlink(wiki / "doomed" / "real.md", wiki / "doomed" / "link.md")
    assert (wiki / "doomed" / "link.md").is_symlink()

    real_rmtree = space.shutil.rmtree
    state = {"n": 0}

    def fail_first(path, *args, **kwargs):
        if "wiki-spaces-remove-" in str(path):
            return real_rmtree(path, *args, **kwargs)  # snapshot tempdir
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("simulated rmtree failure on the main delete")
        return real_rmtree(path, *args, **kwargs)  # restore's pre-copy rmtree

    monkeypatch.setattr(space.shutil, "rmtree", fail_first)
    rc, _, err = _run(["--wiki", str(wiki), "remove", "doomed", "--force"])
    assert rc == 2, err
    link = wiki / "doomed" / "link.md"
    assert link.exists(), "rollback dropped the symlink child"
    assert link.is_symlink(), "rollback restored the symlink as a dereferenced regular file"
    assert (wiki / "doomed" / "real.md").read_text() == "# real target\n"


def test_promote_does_not_rewrite_links_inside_code_blocks(tmp_path):
    """Promote's link rewrite must NOT touch `[[wikilinks]]` that appear
    inside fenced code blocks — those are code examples, not real links.
    The offset-preserving mask hides them from the scanner."""
    wiki = _make_wiki(tmp_path)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "foo.md").write_text("# Foo\n\ncontent\n")
    # A page with TWO instances of `[[foo]]`: one real link, one in a code block.
    (wiki / "doc.md").write_text(
        "# Doc\n"
        "\n"
        "Real link: [[foo]]\n"
        "\n"
        "```\n"
        "Code example: [[foo]] should NOT be rewritten\n"
        "```\n"
    )

    rc, _, _ = _run(["--wiki", str(wiki), "promote", "concepts/foo.md"])
    assert rc == 0

    rewritten = (wiki / "doc.md").read_text()
    # The real link should be rewritten to the pathful form.
    assert "[[concepts/foo/index|foo]]" in rewritten
    # The code-block link should remain literal `[[foo]]`.
    assert "[[foo]] should NOT be rewritten" in rewritten


def test_promote_does_not_rewrite_links_inside_frontmatter(tmp_path):
    """Links inside YAML frontmatter (e.g., an aliases field that happens
    to contain `[[foo]]` syntax) are not real wikilinks and must not be
    touched."""
    wiki = _make_wiki(tmp_path)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "foo.md").write_text("# Foo\n\ncontent\n")
    (wiki / "doc.md").write_text(
        "---\n"
        "title: Doc\n"
        "aliases: [\"[[foo]]\"]\n"
        "---\n"
        "\n"
        "Real link: [[foo]]\n"
    )

    rc, _, _ = _run(["--wiki", str(wiki), "promote", "concepts/foo.md"])
    assert rc == 0

    rewritten = (wiki / "doc.md").read_text()
    # The body link should be rewritten.
    assert "Real link: [[concepts/foo/index|foo]]" in rewritten
    # The frontmatter `aliases: ["[[foo]]"]` should remain literal.
    assert 'aliases: ["[[foo]]"]' in rewritten


def test_audit_include_external_walks_shared_subtree(tmp_path):
    """`audit --include-external` opts the read path into externally-
    classified spaces. Without the flag, a page under `shared/` is invisible
    to audit; with the flag, it's scanned for size violations and broken
    links just like an owned page."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern   | Cap (chars) |\n"
        "|-----------|-------------|\n"
        "| big.md    |          50 |\n"
    )
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "index.md").write_text("# team\n\n## Spaces\n\n")
    (wiki / "shared" / "team" / "big.md").write_text("x" * 100 + "\n")

    rc_default, out_default, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc_default == 0, out_default
    # `shared/team/big.md` does NOT trigger a size violation under default scope.
    assert "shared/team/big.md" not in out_default

    rc_inc, out_inc, _ = _run(["--wiki", str(wiki), "audit", "--include-external"])
    assert rc_inc == 1
    assert "shared/team/big.md" in out_inc
    assert "size" in out_inc


def test_audit_reports_size_violations(tmp_path):
    """audit reports pages over their per-pattern cap and flips exit code.

    Uses a `_meta/limits.md` with a tiny cap for a specific pattern so the
    test doesn't depend on the 15K default. The default `index.md` cap of
    5K is also exercised by writing a small over-cap index.
    """
    wiki = _make_wiki(tmp_path)
    # Custom cap: 100 chars for any *.md in `bigpages/`
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern        | Cap (chars) |\n"
        "|----------------|-------------|\n"
        "| bigpages/*.md  |         100 |\n"
    )
    (wiki / "bigpages").mkdir()
    (wiki / "bigpages" / "small.md").write_text("tiny content\n")  # under cap
    (wiki / "bigpages" / "large.md").write_text("x" * 200 + "\n")  # over cap

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "size violation" in out
    assert "bigpages/large.md" in out
    assert "bigpages/small.md" not in out.split("size violation")[-1]


def test_audit_reports_approaching_cap_informationally(tmp_path):
    """A page at >=80% of its cap is reported as 'approaching' — informational,
    does not flip the exit code."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern   | Cap (chars) |\n"
        "|-----------|-------------|\n"
        "| notes.md  |         100 |\n"
    )
    (wiki / "notes.md").write_text("x" * 85 + "\n")  # 85/100 = 85% — approaching

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 0
    assert "approaching cap" in out
    assert "notes.md" in out


# ---------- cap-source provenance in size refusals + audit ----------

def testenforce_size_cap_message_names_builtin_source(tmp_path):
    """A refusal driven by a built-in default names that source. The
    `SizeCapExceeded` message must carry the numbers AND the provenance
    (built-in default, the matching `index.md` rule) so the refusal traces
    back to the rule that fired — not just an opaque cap number."""
    wiki = _make_wiki(tmp_path)
    over = "# wiki\n\n## Spaces\n\n" + ("x" * 6000) + "\n"
    with pytest.raises(space.SizeCapExceeded) as ei:
        space.enforce_size_cap(wiki / "index.md", over, wiki)
    msg = str(ei.value)
    assert "> cap 5000" in msg
    assert "built-in default" in msg
    assert "index.md" in msg
    assert ei.value.source.kind == _model.CapSourceKind.BUILTIN_DEFAULT


def testenforce_size_cap_message_names_user_override_source(tmp_path):
    """The headline of the locked decision: a user-override cap reaches the
    refusal message with its `_meta/limits.md` file:line provenance and the
    matching pattern, so the user can jump straight to the rule to edit."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern        | Cap (chars) |\n"
        "|----------------|-------------|\n"
        "| concepts/*.md  |          50 |\n"
    )
    (wiki / "concepts").mkdir()
    target = wiki / "concepts" / "foo.md"
    over = "y" * 200 + "\n"
    with pytest.raises(space.SizeCapExceeded) as ei:
        space.enforce_size_cap(target, over, wiki)
    msg = str(ei.value)
    assert "user override" in msg
    assert "concepts/*.md" in msg
    # The override lives on row 3 (1-based) of `_meta/limits.md`.
    assert "_meta/limits.md:3" in msg
    assert ei.value.source.kind == _model.CapSourceKind.USER_OVERRIDE


def test_space_add_overflow_refusal_carries_cap_source(tmp_path):
    """End-to-end: a `space add` whose projected ancestor `index.md` would
    exceed a user-override cap exits non-zero and the CLI stderr names the
    cap source. Proves the provenance flows through the catch site's
    `print(f"  ! size cap: {e}")`, not just the exception object."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    # Cap the root index.md tiny via a user override so the next registration
    # overflows. (The built-in default is 5000; this override fires first.)
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern   | Cap (chars) |\n"
        "|-----------|-------------|\n"
        "| index.md  |          20 |\n"
    )
    # Pad the root index just under the override cap so adding an entry tips
    # it over.
    (wiki / "index.md").write_text("# wiki\n\n## Spaces\n\n" + ("x" * 18) + "\n")
    rc, _out, err = _run(["--wiki", str(wiki), "add", "child"])
    assert rc != 0
    assert "size cap" in err
    assert "user override" in err
    assert "index.md" in err


def test_audit_size_violation_line_carries_cap_source(tmp_path):
    """The audit human size line names the matching cap rule. A user-override
    pattern reports `user override` + `_meta/limits.md`; an over-cap
    `index.md` under the built-in 5000 default reports `built-in default`."""
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern        | Cap (chars) |\n"
        "|----------------|-------------|\n"
        "| bigpages/*.md  |         100 |\n"
    )
    (wiki / "bigpages").mkdir()
    (wiki / "bigpages" / "large.md").write_text("x" * 200 + "\n")  # over cap
    # Over-cap index.md exercising the built-in 5000 default.
    (wiki / "index.md").write_text("# wiki\n\n## Spaces\n\n" + ("z" * 6000) + "\n")

    rc, out, _ = _run(["--wiki", str(wiki), "audit"])
    assert rc == 1
    assert "bigpages/large.md" in out
    assert "> cap 100" in out
    assert "user override" in out
    assert "_meta/limits.md" in out
    # The built-in-capped index.md violation names its source too.
    assert "built-in default" in out


def test_audit_json_size_violations_carry_cap_source(tmp_path):
    """`audit --json` size entries keep top-level chars/cap AND gain a nested
    `cap_source` mirroring `caps --json` (kind/pattern/file/1-based line).
    A user-override violation reports `user_override` with file:line; an
    approaching default-capped file reports `builtin_default`."""
    import json as _json
    wiki = _make_wiki(tmp_path)
    (wiki / "_meta").mkdir()
    (wiki / "_meta" / "limits.md").write_text(
        "| Pattern        | Cap (chars) |\n"
        "|----------------|-------------|\n"
        "| bigpages/*.md  |         100 |\n"
    )
    (wiki / "bigpages").mkdir()
    (wiki / "bigpages" / "large.md").write_text("x" * 200 + "\n")  # over cap
    # A page >= 80% of its built-in 15000 default cap → approaching, with a
    # builtin_default cap_source.
    (wiki / "notes.md").write_text("a" * 12500 + "\n")

    rc, out, _ = _run(["--wiki", str(wiki), "audit", "--json"])
    assert rc == 1
    payload = _json.loads(out)
    viol = payload["size_violations"]
    assert len(viol) == 1
    assert viol[0]["chars"] == 201  # 200 'x' + trailing newline
    assert viol[0]["cap"] == 100
    cs = viol[0]["cap_source"]
    assert cs["kind"] == "user_override"
    assert cs["pattern"] == "bigpages/*.md"
    assert cs["file"].endswith("_meta/limits.md")
    assert cs["line"] == 3  # 1-based row of the override

    appr = payload["approaching_cap"]
    near = [e for e in appr if e["path"] == "notes.md"]
    assert len(near) == 1
    appr_cs = near[0]["cap_source"]
    assert appr_cs["kind"] == "builtin_default"
    assert appr_cs["file"] is None
    assert appr_cs["line"] is None


def test_is_in_external_scope_returns_false_for_owned_path(tmp_path):
    wiki = _make_wiki(tmp_path)
    target = wiki / "projects" / "mine"
    is_external, reason = space._is_in_external_scope(target, wiki)
    assert is_external is False
    assert reason is None


def test_is_in_external_scope_catches_shared_root(tmp_path):
    wiki = _make_wiki(tmp_path)
    target = wiki / "shared" / "foo"
    is_external, reason = space._is_in_external_scope(target, wiki)
    assert is_external is True
    assert "shared" in (reason or "")


def test_is_in_external_scope_catches_shared_descendant(tmp_path):
    """`shared/team/sub` is external because `shared` is the first segment —
    _is_external itself catches this (lexical), but verify the ancestor
    walker reports it correctly via the same path."""
    wiki = _make_wiki(tmp_path)
    target = wiki / "shared" / "team" / "sub"
    is_external, _ = space._is_in_external_scope(target, wiki)
    assert is_external is True


def test_is_in_external_scope_catches_descendant_of_foreign_submodule(tmp_path):
    """Codex's blocker: `_is_external` only checks the exact path, so a path
    under a foreign-submodule mount slipped through. The ancestor walker
    must catch it."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "external" / "foreign"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "foreign"]\n'
        "\tpath = external/foreign\n"
        "\turl = https://github.com/someone-else/their-wiki.git\n"
    )
    descendant = sub / "deep" / "child"
    is_external, reason = space._is_in_external_scope(descendant, wiki)
    assert is_external is True
    assert "external/foreign" in (reason or "")


def test_is_in_external_scope_catches_descendant_of_escaping_symlink(tmp_path):
    """A symlink that escapes the wiki tree → any descendant of that symlink
    is in external scope. Same blocker class as the submodule case."""
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside-wiki"
    outside.mkdir()
    (outside / "child").mkdir()
    import os
    link = wiki / "mount"
    os.symlink(outside, link)
    descendant = link / "child"
    is_external, reason = space._is_in_external_scope(descendant, wiki)
    assert is_external is True
    assert "mount" in (reason or "")


def test_is_in_external_scope_path_outside_wiki(tmp_path):
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "elsewhere" / "stuff"
    is_external, reason = space._is_in_external_scope(outside, wiki)
    assert is_external is True
    assert "outside" in (reason or "")


# ---------- cmd_add: --force-external preflight ----------

def test_add_refuses_external_shared(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "shared/foo"])
    assert rc == 2
    assert "external scope" in err
    assert not (wiki / "shared" / "foo").exists()


def test_add_refuses_external_shared_descendant(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "add", "shared/team/sub"])
    assert rc == 2
    assert "external scope" in err


def test_add_refuses_under_escaping_symlink(tmp_path):
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    import os
    os.symlink(outside, wiki / "mount")
    rc, _, err = _run(["--wiki", str(wiki), "add", "mount/child"])
    assert rc == 2
    assert "external scope" in err


def test_add_refuses_under_foreign_submodule(tmp_path):
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    sub = wiki / "external" / "foreign"
    sub.mkdir(parents=True)
    (wiki / ".gitmodules").write_text(
        '[submodule "foreign"]\n'
        "\tpath = external/foreign\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "add", "external/foreign/child"])
    assert rc == 2
    assert "external scope" in err


def test_add_force_external_overrides(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, out, _ = _run(["--wiki", str(wiki), "add", "shared/foo", "--force-external"])
    assert rc == 0
    assert (wiki / "shared" / "foo" / "index.md").is_file()


# ---------- cmd_remove: --force-external preflight ----------

def test_remove_refuses_external_shared(tmp_path):
    """Construct the external space directly (bypassing add's guard) so we can
    verify remove also refuses to operate on it without --force-external."""
    wiki = _make_wiki(tmp_path)
    sub = wiki / "shared" / "foo"
    sub.mkdir(parents=True)
    (sub / "index.md").write_text("# foo")
    rc, _, err = _run(["--wiki", str(wiki), "remove", "shared/foo"])
    assert rc == 2
    assert "external scope" in err
    assert sub.exists()  # untouched


def test_remove_force_external_succeeds(tmp_path):
    wiki = _make_wiki(tmp_path)
    sub = wiki / "shared" / "foo"
    sub.mkdir(parents=True)
    (sub / "index.md").write_text("# foo")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "shared/foo", "--force-external"])
    assert rc == 0
    assert not sub.exists()


# ---------- cmd_remove: external-child subtree refusal ----------

def test_remove_force_refuses_when_external_submodule_child_inside_target(tmp_path):
    """`remove <owned> --force` must NOT let rmtree delete a foreign-origin
    submodule CHILD inside the owned target. The target-level external check
    only inspects the target; the subtree scan catches the descendant and
    refuses-and-reports unless --force-external is passed."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    # Owned space registered via add.
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    # Foreign-origin submodule child inside the owned target.
    vendor = foo / "vendor"
    vendor.mkdir()
    (vendor / "index.md").write_text("# vendor")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n'
        "\tpath = projects/foo/vendor\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "remove", "projects/foo", "--force"])
    assert rc == 2
    assert "external" in err
    assert "projects/foo/vendor" in err
    assert "--force-external" in err
    # Nothing deleted — read-before-write held.
    assert foo.exists()
    assert vendor.exists()


def test_remove_force_external_deletes_target_with_external_child(tmp_path):
    """Same fixture as the refusal test, but with --force-external the
    subtree refusal is overridden exactly like the target-level one —
    mirror semantics, no new vocabulary."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    vendor = foo / "vendor"
    vendor.mkdir()
    (vendor / "index.md").write_text("# vendor")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n'
        "\tpath = projects/foo/vendor\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, _, _ = _run(
        ["--wiki", str(wiki), "remove", "projects/foo", "--force", "--force-external"]
    )
    assert rc == 0
    assert not foo.exists()


def test_remove_force_refuses_when_escaping_symlink_child_inside_target(tmp_path):
    """`remove <owned> --force` must refuse when the target contains a
    symlink whose realpath escapes the wiki tree — the snapshot copytree
    and rmtree must not follow it into content outside the owned tree."""
    import os
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    outside = tmp_path / "outside_tree"
    outside.mkdir()
    (outside / "keep.md").write_text("# keep")
    link = foo / "link"
    os.symlink(outside, link, target_is_directory=True)
    rc, _, err = _run(["--wiki", str(wiki), "remove", "projects/foo", "--force"])
    assert rc == 2
    assert "projects/foo/link" in err
    assert "--force-external" in err
    # The outside directory is untouched (symlink not followed).
    assert outside.exists()
    assert (outside / "keep.md").exists()


def test_remove_force_refuses_external_submodule_under_hidden_dir(tmp_path):
    """Reproduces the reported data-loss: a foreign-origin submodule parked
    under a `.`-hidden dir was invisible to the pruning guard, so `remove
    --force` exited 0 and rmtree deleted content outside the owned tree with
    no --force-external. The guard now scans EXACTLY what rmtree deletes, so
    the refusal fires."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    # Foreign-origin submodule mount under a hidden dir.
    vendor = foo / ".hidden" / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "index.md").write_text("# vendor")
    (vendor / "data.txt").write_text("payload")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n'
        "\tpath = projects/foo/.hidden/vendor\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    rc, _, err = _run(["--wiki", str(wiki), "remove", "projects/foo", "--force"])
    assert rc == 2
    assert "external" in err
    assert "projects/foo/.hidden/vendor" in err
    assert "--force-external" in err
    # Nothing deleted — guard-walk now agrees with the delete-walk.
    assert foo.exists()
    assert vendor.exists()
    assert (vendor / "data.txt").exists()


def test_remove_force_refuses_external_submodule_under_underscore_meta(tmp_path):
    """An external mount parked under `_meta` / `_archives` was also pruned
    by the old guard. Both reserved names are now descended, so the refusal
    fires for either."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    for reserved in ("_meta", "_archives"):
        vendor = foo / reserved / "vendor"
        vendor.mkdir(parents=True)
        (vendor / "index.md").write_text("# vendor")
        (vendor / "data.txt").write_text("payload")
        (wiki / ".gitmodules").write_text(
            '[submodule "vendor"]\n'
            f"\tpath = projects/foo/{reserved}/vendor\n"
            "\turl = https://github.com/other/wiki.git\n"
        )
        rc, _, err = _run(["--wiki", str(wiki), "remove", "projects/foo", "--force"])
        assert rc == 2
        assert f"projects/foo/{reserved}/vendor" in err
        assert "--force-external" in err
        assert vendor.exists()
        assert (vendor / "data.txt").exists()
        # Reset for the next reserved-name case.
        import shutil as _shutil
        _shutil.rmtree(foo / reserved)


def test_remove_force_refuses_escaping_symlink_under_hidden_dir(tmp_path):
    """Mirror of the top-level escaping-symlink refusal, but the symlink lives
    under a hidden dir — the old prune hid it; the widened scan catches it."""
    import os
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    outside = tmp_path / "outside_tree"
    outside.mkdir()
    (outside / "keep.md").write_text("# keep")
    assets = foo / ".assets"
    assets.mkdir()
    link = assets / "link"
    os.symlink(outside, link, target_is_directory=True)
    rc, _, err = _run(["--wiki", str(wiki), "remove", "projects/foo", "--force"])
    assert rc == 2
    assert "projects/foo/.assets/link" in err
    assert "--force-external" in err
    # The outside directory is untouched (symlink not followed).
    assert outside.exists()
    assert (outside / "keep.md").exists()


def test_remove_force_deletes_owned_subtree_with_hidden_meta_archives(tmp_path):
    """Regression guard for the happy path: descending into hidden / `_meta` /
    `_archives` must NOT cause a false refusal. Owned content under those dirs
    is never external, so a purely-owned subtree still deletes cleanly."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "projects/foo"])
    assert rc == 0
    foo = wiki / "projects" / "foo"
    (foo / ".obsidian").mkdir()
    (foo / ".obsidian" / "app.json").write_text("{}")
    (foo / "_meta").mkdir()
    (foo / "_meta" / "limits.md").write_text("# limits")
    (foo / "_archives").mkdir()
    (foo / "_archives" / "old.md").write_text("# old")
    (foo / "notes.md").write_text("# notes")
    rc, _, _ = _run(["--wiki", str(wiki), "remove", "projects/foo", "--force"])
    assert rc == 0
    assert not foo.exists()


def test_first_external_descendant_finds_node_under_hidden_dir_unit(tmp_path):
    """Pins the producer=consumer contract at the function boundary: the guard
    finds an external mount under a hidden dir, and returns None for a
    purely-owned subtree whose only nesting is hidden / `_meta` / `_archives`."""
    wiki = _make_wiki(tmp_path)
    _make_git_config(wiki, "https://github.com/me/mywiki.git")
    foo = wiki / "projects" / "foo"
    foo.mkdir(parents=True)
    (foo / "index.md").write_text("# foo")
    vendor = foo / ".hidden" / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "index.md").write_text("# vendor")
    (wiki / ".gitmodules").write_text(
        '[submodule "vendor"]\n'
        "\tpath = projects/foo/.hidden/vendor\n"
        "\turl = https://github.com/other/wiki.git\n"
    )
    found = space._first_external_descendant(foo, wiki)
    assert found is not None
    assert found[0].resolve() == vendor.resolve()
    assert "submodule" in found[1]

    # Purely-owned hidden/_meta/_archives content → no external node.
    import shutil as _shutil
    _shutil.rmtree(foo / ".hidden")
    (wiki / ".gitmodules").unlink()
    (foo / "_meta").mkdir()
    (foo / "_meta" / "limits.md").write_text("# limits")
    (foo / "_archives").mkdir()
    (foo / "_archives" / "old.md").write_text("# old")
    (foo / ".obsidian").mkdir()
    (foo / ".obsidian" / "app.json").write_text("{}")
    assert space._first_external_descendant(foo, wiki) is None


# ---------- _model.discover_owned_md_files (audit's owned/FS walk) ----------

def test_walk_owned_md_files_descends_into_plain_folders(tmp_path):
    """Plain folders (no index.md) are valid per AGENTS.md and must be
    traversed — the owned-space enumeration misses them; the owned-files
    walk (`_model.discover_owned_md_files`) must not."""
    wiki = _make_wiki(tmp_path)
    plain = wiki / "drafts"  # no index.md → plain folder
    plain.mkdir()
    (plain / "page.md").write_text("# page")
    (wiki / "projects" / "spaced").mkdir(parents=True)
    (wiki / "projects" / "spaced" / "index.md").write_text("# spaced")
    (wiki / "projects" / "spaced" / "child.md").write_text("# child")
    files = sorted(p.relative_to(wiki).as_posix() for p in _model.discover_owned_md_files(wiki))
    assert "drafts/page.md" in files
    assert "projects/spaced/child.md" in files
    assert "projects/spaced/index.md" in files


def test_walk_owned_md_files_skips_externals(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    (wiki / "shared" / "team" / "team-page.md").write_text("# team")
    (wiki / "projects" / "mine").mkdir(parents=True)
    (wiki / "projects" / "mine" / "p.md").write_text("# p")
    files = sorted(p.relative_to(wiki).as_posix() for p in _model.discover_owned_md_files(wiki))
    assert "projects/mine/p.md" in files
    assert not any("shared/" in f for f in files)


def test_walk_owned_md_files_skips_excluded_dirs(tmp_path):
    wiki = _make_wiki(tmp_path)
    for d in (".obsidian", "_archives", ".git", "wiki-spaces-promote-leftover"):
        (wiki / d).mkdir()
        (wiki / d / "page.md").write_text("# x")
    (wiki / "ok.md").write_text("# ok")
    files = sorted(p.relative_to(wiki).as_posix() for p in _model.discover_owned_md_files(wiki))
    assert "ok.md" in files
    for d in (".obsidian", "_archives", ".git", "wiki-spaces-promote-leftover"):
        assert not any(f.startswith(f"{d}/") for f in files), f"{d} was walked"


# ---------- _find_alias_owners ----------

def test_find_alias_owners_case_insensitive(tmp_path):
    wiki = _make_wiki(tmp_path)
    a = wiki / "a.md"
    a.write_text("---\naliases:\n  - Foo\n---\n# a")
    b = wiki / "b.md"
    b.write_text("---\naliases: [bar, BAR]\n---\n# b")
    owners = space._find_alias_owners(wiki)
    assert "foo" in owners and owners["foo"] == [a]
    # case-folded "bar" collects both entries
    assert "bar" in owners and len(owners["bar"]) == 1
    assert owners["bar"][0] == b


# ---------- cmd_promote: happy path ----------

def test_promote_moves_file_and_creates_index_with_spaces(tmp_path):
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n\nbody text\n")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert not page.exists()
    new = wiki / "page" / "index.md"
    assert new.is_file()
    new_text = new.read_text()
    assert "# page" in new_text
    assert "body text" in new_text
    assert "## Spaces" in new_text  # navigation contract from t=0
    assert "aliases:" in new_text
    assert "page" in _md.parse_frontmatter_aliases(new_text)
    # Parent ## Spaces has the entry
    parent_text = (wiki / "index.md").read_text()
    assert "page/" in parent_text


def test_promote_uses_frontmatter_summary_for_parent_entry(tmp_path):
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("---\nsummary: A short summary.\n---\n# page\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    parent_text = (wiki / "index.md").read_text()
    assert "A short summary." in parent_text


# ---------- cmd_promote: refusals ----------

def test_promote_refuses_index_md(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "index.md"])
    assert rc == 2
    assert "cannot promote" in err


def test_promote_refuses_nonexistent_file(tmp_path):
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "missing.md"])
    assert rc == 2
    assert "does not exist" in err


def test_promote_refuses_non_md_file(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "thing.txt").write_text("x")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "thing.txt"])
    assert rc == 2
    assert ".md" in err


def test_promote_refuses_existing_target_dir(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "page").mkdir()
    (wiki / "page" / "preexisting.md").write_text("# preexisting")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "already exists" in err
    assert (wiki / "page.md").exists()  # source untouched


def test_promote_refuses_regular_file_at_target_dir_path(tmp_path):
    """Promote must REFUSE cleanly (exit 2) when the derived target dir path
    (`<source-stem>/`) already exists as a REGULAR FILE — not crash with a raw
    `FileExistsError` masked by an `UnboundLocalError` (HANDBOOK: handle
    failures at boundaries).

    The pre-existing `target_dir.exists()` guard only checked for a symlink
    or a non-empty directory: a plain file passed through (`iterdir()` raised
    `OSError`, swallowed to `entries = []`), the command did not refuse, and
    `target_dir.mkdir()` then raised `FileExistsError` before `moved_via_git`
    was bound — so the rollback handler crashed referencing it."""
    wiki = _make_wiki(tmp_path)
    (wiki / "foo.md").write_text("# foo\n")
    # A plain file named `foo` collides with the derived target dir `foo/`.
    (wiki / "foo").write_text("i am a regular file\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "foo.md"])
    assert rc == 2
    assert "not a directory" in err
    assert "Traceback" not in err and "UnboundLocalError" not in err
    # No mutation: source and the colliding file both untouched.
    assert (wiki / "foo.md").is_file()
    assert (wiki / "foo").read_text() == "i am a regular file\n"


def test_promote_inserts_spaces_when_ancestor_bare(tmp_path):
    """Promote against a bare-`index.md` ancestor inserts `## Spaces` and
    registers the new space — no refuse, no manual setup step. The atomic
    mutate fn does both under a single flock."""
    wiki = _make_wiki(tmp_path, with_spaces_section=False)
    (wiki / "page.md").write_text("# page")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    # Source moved.
    assert not (wiki / "page.md").exists()
    assert (wiki / "page" / "index.md").is_file()
    # Ancestor now has `## Spaces` and an entry for the promoted space.
    root_text = (wiki / "index.md").read_text()
    assert "## Spaces" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "page/" in e.href for e in entries)


def test_promote_propagates_chain_repair_up_to_wiki_root(tmp_path):
    """PR-C + OC pass #3 follow-up: `_promote_mutate` only repairs the
    IMMEDIATE ancestor. When a higher ancestor (or the wiki root itself)
    is bare or doesn't carry an entry for the immediate ancestor, the
    promoted space is unreachable via the navigation contract — strict
    consumers (audit, doctor, skills) can't traverse to it. The chain
    helper must walk from the immediate ancestor up to the wiki root,
    inserting `## Spaces` in bare ancestors and registering each step.
    v1 contract: every write command maintains `## Spaces` on every
    ancestor it crosses, not just the nearest."""
    wiki = _make_wiki(tmp_path)  # wiki/index.md has `## Spaces`
    # Hand-create an intermediate space that is NOT registered in the wiki's
    # `## Spaces` and itself lacks `## Spaces`. This mimics a hand-curated
    # wiki tree the user built up without going through `space add`.
    (wiki / "projects").mkdir()
    (wiki / "projects" / "index.md").write_text("# projects\n")
    (wiki / "projects" / "foo.md").write_text("# foo\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 0, err
    # Source moved to projects/foo/index.md.
    assert not (wiki / "projects" / "foo.md").exists()
    assert (wiki / "projects" / "foo" / "index.md").is_file()
    # Immediate ancestor gets `## Spaces` + `foo/` entry (PR-C / _promote_mutate).
    proj_text = (wiki / "projects" / "index.md").read_text()
    assert "## Spaces" in proj_text
    proj_entries = _md.parse_section_entries(proj_text, "Spaces")
    assert any(e.href and "foo/" in e.href for e in proj_entries)
    # Wiki root must now register `projects/` so strict consumers can
    # traverse: wiki → projects → foo. Without chain propagation this
    # entry would be missing and audit would report drift.
    root_text = (wiki / "index.md").read_text()
    root_entries = _md.parse_section_entries(root_text, "Spaces")
    assert any(e.href and "projects/" in e.href for e in root_entries)


def test_promote_repairs_ancestor_section_before_upward_registration(
    tmp_path, monkeypatch
):
    """OC convergence #5: when chain repair runs against a bare intermediate
    ancestor, the order must be (1) `_ensure_section_at(ancestor)` to insert
    `## Spaces` in ancestor's index.md, THEN (2) `_ensure_spaces_chain_and_register`
    to register ancestor upward. If we registered first and then promote
    failed, the rollback would leave `ancestor/` advertised in its parent's
    `## Spaces` while `ancestor/index.md` itself stayed bare — a producer/
    consumer break the contract walker would skip. Test pins the
    post-failure invariant: even when promote fails mid-mutation, the
    intermediate ancestor whose entry is now in the parent's contract
    actually has `## Spaces` itself."""
    wiki = _make_wiki(tmp_path)  # wiki/index.md has `## Spaces`
    (wiki / "projects").mkdir()
    (wiki / "projects" / "index.md").write_text("# projects\n")  # BARE
    (wiki / "projects" / "foo.md").write_text("# foo\n")
    # Trigger a mid-promote failure AFTER chain repair has registered
    # projects/ in wiki/index.md. Patch Path.rename so the source→target
    # move raises — `cmd_promote` uses `source.rename(target)` for the
    # source move on non-git wikis (the final rename step before
    # outgoing-link rewrites land).
    real_rename = Path.rename
    def patched_rename(self, target):
        if str(self).endswith("/projects/foo.md"):
            raise OSError("simulated post-chain failure")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", patched_rename)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc != 0, "promote should fail due to simulated move failure"
    # Producer/consumer invariant: if `projects/` is registered in the
    # wiki's `## Spaces` (chain succeeded), then `projects/index.md` must
    # carry `## Spaces` too — otherwise the contract walker would treat
    # `projects/` as drift even though the parent advertises it.
    root_text = (wiki / "index.md").read_text()
    root_entries = _md.parse_section_entries(root_text, "Spaces")
    if any(e.href and "projects/" in e.href for e in root_entries):
        proj_text = (wiki / "projects" / "index.md").read_text()
        assert "## Spaces" in proj_text, (
            "wiki advertised projects/ but projects/index.md is bare — "
            "producer/consumer break the contract walker would skip"
        )


def test_promote_ancestor_uses_atomic_mutate_index(tmp_path, monkeypatch):
    """The ancestor write must happen under flock against FRESH text — not
    against the text read at command start. Inject a concurrent modification
    via monkeypatch and assert the rewrite is applied against the fresh text,
    not the stale one. Otherwise a parallel `space add` could clobber our
    rewrites or our entry could clobber theirs."""
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n")
    # Wrap _atomic_mutate_index: inject a sibling `## Spaces` entry into the
    # ancestor's text BEFORE the mutate fn runs. The fn must see this fresh
    # text (containing the injection) and add `page/` AFTER it without losing
    # the injected entry.
    real_atomic = space._atomic_mutate_index

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        original = ancestor_index.read_text(encoding="utf-8")
        new = original.replace(
            "## Spaces\n", "## Spaces\n\n- [concurrent/](concurrent/index.md)\n"
        )
        ancestor_index.write_text(new, encoding="utf-8")
        return real_atomic(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space._core, "_atomic_mutate_index", patched_atomic)

    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    root_text = (wiki / "index.md").read_text()
    # The injection must survive (mutate fn saw fresh text) AND `page/` must
    # be added.
    assert "concurrent/index.md" in root_text
    entries = _md.parse_section_entries(root_text, "Spaces")
    hrefs = [e.href for e in entries if e.href]
    assert any("page/" in h for h in hrefs)
    assert any("concurrent/" in h for h in hrefs)


def test_promote_link_rewrite_uses_fresh_ancestor_text(tmp_path, monkeypatch):
    """The link rewrite in `_promote_mutate` runs against text re-read INSIDE
    the lock, not against the outer-read `ancestor_text`. Prove it: the outer
    read sees an ancestor with no `[[page]]` reference (so the planning pass
    finds nothing to rewrite), but a concurrent writer injects `[[page]]`
    into the ancestor between outer read and mutate. The mutate fn must
    rewrite the injected wikilink to `[[page/index]]`. If the rewrite ran
    against stale text, the injected link would survive unchanged."""
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n")
    real_atomic = space._atomic_mutate_index

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        # Inject a `[[page]]` wikilink into the ancestor body BEFORE the
        # mutate fn re-reads. This simulates a concurrent writer that
        # added a reference to the source after our outer read.
        original = ancestor_index.read_text(encoding="utf-8")
        new = original.replace(
            "## Spaces\n", "See [[page]] for details.\n\n## Spaces\n"
        )
        ancestor_index.write_text(new, encoding="utf-8")
        return real_atomic(ancestor, ancestor_index, mutate_fn)

    monkeypatch.setattr(space._core, "_atomic_mutate_index", patched_atomic)

    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    root_text = (wiki / "index.md").read_text()
    # Fresh-text proof: the injected `[[page]]` was rewritten to point at
    # the promoted index (`page/index`, with the original basename preserved
    # as the display alias per the wikilink rewrite contract). If the
    # rewrite ran against the outer (stale) `ancestor_text`, the injected
    # link would still read `[[page]]`.
    assert "[[page/index|page]]" in root_text
    assert "[[page]]" not in root_text


def test_promote_rollback_does_not_clobber_concurrent_ancestor_write(tmp_path, monkeypatch):
    """Promote takes a snapshot of every affected file at the start so it
    can roll back on failure. The snapshot is taken BEFORE the lock is
    acquired on the ancestor, so a concurrent process can commit a sibling
    entry to the ancestor's `## Spaces` after our snapshot but before our
    locked write. If `_atomic_mutate_index` then fails (after the concurrent
    write has landed), the rollback path must NOT restore the pre-snapshot
    ancestor text — doing so would silently overwrite the concurrent change.

    This regression test patches `_atomic_mutate_index` to (1) inject a
    concurrent `## Spaces` entry into the ancestor and (2) return a write
    failure. Assert the concurrent entry survives the resulting rollback."""
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page\n")

    def patched_atomic(ancestor, ancestor_index, mutate_fn):
        # Step 1: simulate a concurrent writer committing a sibling entry
        # between our outer ancestor_text read and our lock acquisition.
        original = ancestor_index.read_text(encoding="utf-8")
        new = original.replace(
            "## Spaces\n", "## Spaces\n\n- [concurrent/](concurrent/index.md)\n"
        )
        ancestor_index.write_text(new, encoding="utf-8")
        # Step 2: fail the locked write. Caller raises and rolls back.
        return 1, "simulated lock-time write failure"

    monkeypatch.setattr(space._core, "_atomic_mutate_index", patched_atomic)

    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc != 0
    # Source must be restored (other planned files use snapshot-restore).
    assert (wiki / "page.md").exists()
    # Ancestor must NOT have been restored from the stale snapshot — the
    # concurrent entry survives the rollback.
    root_text = (wiki / "index.md").read_text()
    assert "concurrent/index.md" in root_text
    # The promote entry was never committed (atomic helper returned rc=1).
    assert "page/index.md" not in root_text


def test_promote_refuses_external_root(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "shared").mkdir()
    page = wiki / "shared" / "page.md"
    page.write_text("# page")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "shared/page.md"])
    assert rc == 2
    assert "external scope" in err
    assert page.exists()


def test_promote_refuses_external_descendant(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "shared" / "team").mkdir(parents=True)
    page = wiki / "shared" / "team" / "page.md"
    page.write_text("# page")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "shared/team/page.md"])
    assert rc == 2
    assert "external scope" in err


def test_promote_refuses_alias_collision_case_insensitive(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "other.md").write_text("---\naliases:\n  - Page\n---\n# other")
    (wiki / "page.md").write_text("# page")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "alias collision" in err.lower() or "already declares" in err
    assert (wiki / "page.md").exists()


def test_promote_skip_aliases_bypasses_collision(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "other.md").write_text("---\naliases:\n  - page\n---\n# other")
    (wiki / "page.md").write_text("# page")
    rc, _, err = _run([
        "--wiki", str(wiki), "promote", "page.md", "--skip-aliases",
    ])
    assert rc == 0, err
    new = wiki / "page" / "index.md"
    assert new.is_file()
    assert "aliases:" not in new.read_text() or "page" not in _md.parse_frontmatter_aliases(new.read_text())


def test_promote_root_file(tmp_path):
    """A .md at the wiki root promotes correctly (ancestor is wiki_root)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "rootpage.md").write_text("# root\n\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "rootpage.md"])
    assert rc == 0, err
    assert (wiki / "rootpage" / "index.md").is_file()


# ---------- cmd_promote: link rewriting ----------

def test_promote_rewrites_markdown_link_in_sibling_page(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    sibling = wiki / "sibling.md"
    sibling.write_text("see [page](page.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    new = sibling.read_text()
    assert "[page](page/index.md)" in new


def test_promote_rewrites_markdown_link_with_anchor(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    sibling = wiki / "sibling.md"
    sibling.write_text("[a](page.md#section)")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[a](page/index.md#section)" in sibling.read_text()


def test_promote_rewrites_percent_encoded_space_markdown_link(tmp_path):
    """Obsidian filenames routinely contain spaces, and a markdown link to one
    is percent-encoded (`[x](my%20note.md)`) — a valid, working Obsidian link.
    Promote moves `my note.md` to `my note/index.md` and must rewrite that link,
    re-encoding the space so the result stays a valid Obsidian markdown link and
    round-trips through the resolver. Before the fix the encoded href resolved to
    a non-existent `my%20note.md`, so promote skipped it and left the link
    dangling while falsely reporting success."""
    wiki = _make_wiki(tmp_path)
    (wiki / "my note.md").write_text("# my note")
    sibling = wiki / "sibling.md"
    sibling.write_text("see [the note](my%20note.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "my note.md"])
    assert rc == 0, err
    new = sibling.read_text()
    assert "[the note](my%20note/index.md)" in new
    assert "my%20note.md" not in new


def test_promote_rewrites_markdown_link_from_nested_page(tmp_path):
    """Codex v3 named this as silent-corruption-risk: the rewrite must be
    relative to the LINKING file's directory, not wiki-root."""
    wiki = _make_wiki(tmp_path)
    (wiki / "projects").mkdir()
    target = wiki / "projects" / "foo.md"
    target.write_text("# foo")
    other_dir = wiki / "projects" / "other"
    other_dir.mkdir()
    notes = other_dir / "notes.md"
    notes.write_text("ref [foo](../foo.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 0, err
    new = notes.read_text()
    # The rewrite must be relative to projects/other/, not wiki root.
    assert "[foo](../foo/index.md)" in new
    assert "projects/foo/index.md" not in new


def test_promote_does_not_rewrite_unrelated_same_basename(tmp_path):
    """Two files named foo.md in different folders. Promoting one must not
    rewrite links pointing at the other."""
    wiki = _make_wiki(tmp_path)
    (wiki / "a").mkdir()
    (wiki / "a" / "foo.md").write_text("# foo a")
    (wiki / "b").mkdir()
    (wiki / "b" / "foo.md").write_text("# foo b")
    (wiki / "ref.md").write_text("link [a](a/foo.md) and [b](b/foo.md)")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "a/foo.md"])
    assert rc == 0, err
    new = (wiki / "ref.md").read_text()
    assert "[a](a/foo/index.md)" in new
    assert "[b](b/foo.md)" in new  # other foo untouched


def test_promote_rewrites_simple_wikilink(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "ref.md").write_text("see [[page]] here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[[page/index|page]]" in (wiki / "ref.md").read_text()


def test_promote_rewrites_wikilink_with_display(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "ref.md").write_text("see [[page|My Page]] here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[[page/index|My Page]]" in (wiki / "ref.md").read_text()


def test_promote_rewrites_wikilink_with_anchor(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    (wiki / "ref.md").write_text("[[page#section]]")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[[page/index#section|page]]" in (wiki / "ref.md").read_text()


def test_promote_rewrites_pathful_wikilink_from_remote_directory(tmp_path):
    """Codex v5 named this as silent-staleness blocker: the WS8-internal
    resolver must match pathful targets wiki-root-relative, not file-
    relative (the existing `_md.resolve_wikilink` doesn't)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "projects").mkdir()
    (wiki / "projects" / "foo.md").write_text("# foo")
    (wiki / "recipes").mkdir()
    (wiki / "recipes" / "dessert.md").write_text("see [[projects/foo]] here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 0, err
    assert "[[projects/foo/index|projects/foo]]" in (wiki / "recipes" / "dessert.md").read_text()


def test_promote_pathful_wikilink_different_file_not_touched(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / "a").mkdir()
    (wiki / "a" / "foo.md").write_text("# foo a")
    (wiki / "b").mkdir()
    (wiki / "b" / "foo.md").write_text("# foo b")
    (wiki / "ref.md").write_text("[[a/foo]] and [[b/foo]]")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "a/foo.md"])
    assert rc == 0, err
    new = (wiki / "ref.md").read_text()
    assert "[[a/foo/index|a/foo]]" in new
    assert "[[b/foo]]" in new


def test_promote_adjusts_promoted_files_outgoing_relative_links(tmp_path):
    """The promoted file moves one level deeper; its outgoing relative links
    must gain the extra ../ to keep resolving."""
    wiki = _make_wiki(tmp_path)
    (wiki / "sibling.md").write_text("# sibling")
    (wiki / "page.md").write_text("# page\n\nsee [sib](sibling.md) here")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    new = (wiki / "page" / "index.md").read_text()
    assert "[sib](../sibling.md)" in new


def test_promote_finds_links_in_plain_folder_pages(tmp_path):
    """A page in a plain folder (no index.md) must have its links rewritten —
    exercises plain-folder descent in the contract md-file walk."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    plain = wiki / "drafts"
    plain.mkdir()
    (plain / "scratch.md").write_text("ref [p](../page.md)")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    assert "[p](../page/index.md)" in (plain / "scratch.md").read_text()


# ---------- cmd_promote: dry-run + atomicity ----------

def test_promote_dry_run_no_side_effects(tmp_path):
    wiki = _make_wiki(tmp_path)
    page = wiki / "page.md"
    page.write_text("# page")
    sibling = wiki / "sib.md"
    sibling.write_text("[p](page.md)")
    before_page = page.read_text()
    before_sibling = sibling.read_text()
    rc, out, _ = _run(["--wiki", str(wiki), "promote", "page.md", "--dry-run"])
    assert rc == 0
    assert "(dry-run)" in out
    assert page.read_text() == before_page
    assert sibling.read_text() == before_sibling
    assert not (wiki / "page").exists()


def test_promote_snapshot_dir_cleaned_on_success(tmp_path):
    """Snapshot dir lives in /tmp; after success it must be deleted."""
    import tempfile
    sysroot = Path(tempfile.gettempdir())
    before = {p.name for p in sysroot.iterdir() if p.name.startswith("wiki-spaces-promote-")}
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page")
    rc, _, _ = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0
    after = {p.name for p in sysroot.iterdir() if p.name.startswith("wiki-spaces-promote-")}
    assert after == before, "snapshot dir not cleaned"


def test_promote_existing_spaces_section_not_duplicated(tmp_path):
    """Promoted file already had ## Spaces (rare but possible) ⇒ no second one added."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\n## Spaces\n\n- [a](a/index.md)\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    new = (wiki / "page" / "index.md").read_text()
    assert new.count("## Spaces") == 1


def test_promote_link_in_ancestor_index_is_not_clobbered_by_entry_add(tmp_path):
    """Regression for the clobber bug: when the ancestor's index.md contains
    a link to the promoted file, the link-rewrite pass updates the link,
    then `_add_space_entry` writes a NEW entry. Both edits must land — the
    entry-add must build on top of the rewritten ancestor text, not the
    pre-rewrite snapshot.
    """
    wiki = _make_wiki(tmp_path)
    # Ancestor's index.md links to the promoted file in body text.
    idx_text = (wiki / "index.md").read_text()
    (wiki / "index.md").write_text(
        idx_text + "\nSee [page](page.md) for more.\n"
    )
    (wiki / "page.md").write_text("# page\n\nbody\n")
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 0, err
    final = (wiki / "index.md").read_text()
    # Both edits must be present:
    assert "[page](page/index.md)" in final, "link rewrite was clobbered"
    assert "- [page/]" in final or "page/index.md" in final, "entry-add missing"


def test_promote_rewrites_sibling_link_in_soon_to_be_visible_ancestor(tmp_path):
    """Regression: when `space promote projects/foo.md` runs against a wiki
    where `projects/` is unregistered (bare or absent from the wiki root's
    `## Spaces`), the chain repair below makes `projects/` consumer-visible
    AFTER the rewrite plan was computed. A sibling `projects/sibling.md`
    that links to `foo.md` was previously invisible to the contract walker,
    so its link was not rewritten — leaving a broken stale link in a now-
    visible space.

    Fix: promote unions every `.md` under `ancestor` into the candidate
    set unconditionally, so soon-to-be-visible siblings get their links
    rewritten in the same operation.
    """
    wiki = _make_wiki(tmp_path)
    projects = wiki / "projects"
    projects.mkdir()
    # `projects/index.md` is bare — no `## Spaces`. The chain repair below
    # will insert it AND register `projects/` in the wiki root.
    (projects / "index.md").write_text("# projects\n")
    (projects / "foo.md").write_text("# foo\n\nbody\n")
    # Sibling links to `foo.md`. The contract walker visits it because
    # `projects/` gains `## Spaces` via the chain helper, making
    # `sibling.md` visible under `ancestor` and part of the candidate set.
    (projects / "sibling.md").write_text(
        "# sibling\n\nSee [foo](foo.md) and [[foo]] for details.\n"
    )

    rc, _, err = _run(["--wiki", str(wiki), "promote", "projects/foo.md"])
    assert rc == 0, err

    # Chain repair did happen.
    proj_idx = (projects / "index.md").read_text()
    assert "## Spaces" in proj_idx
    assert "foo/" in proj_idx or "foo/index.md" in proj_idx
    wiki_idx = (wiki / "index.md").read_text()
    assert "projects/" in wiki_idx or "projects/index.md" in wiki_idx

    # The promoted file landed at its new path.
    assert (projects / "foo" / "index.md").is_file()
    assert not (projects / "foo.md").exists()

    # Sibling's link was rewritten to point at the new location — no stale
    # link survives into the now-visible space.
    sibling_text = (projects / "sibling.md").read_text()
    assert "foo.md" not in sibling_text, (
        f"stale `foo.md` link survived in now-visible sibling: {sibling_text!r}"
    )


def test_promote_rollback_removes_orphaned_target_dir(tmp_path, monkeypatch):
    """When promote fails mid-mutation, the target/ dir created by mkdir must
    be cleaned up — not left as an empty folder polluting the wiki."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")
    # Sabotage AFTER the preflight: make `_atomic_mutate_index` fail on the
    # ancestor write so the outer try/except catches it and rolls back.
    monkeypatch.setattr(
        space._core, "_atomic_mutate_index",
        lambda *a, **k: (1, "simulated mid-promote failure"),
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "rolled back" in err or "failed" in err
    # The target directory must not be left behind.
    assert not (wiki / "page").exists(), "orphaned target dir not cleaned up"
    # Source must be restored.
    assert (wiki / "page.md").is_file()


def test_promote_rollback_preserves_preexisting_empty_target_dir(tmp_path, monkeypatch):
    """If the user mkdir'd `page/` before running promote (and the preflight
    tolerated it as empty), rollback must NOT delete that pre-existing
    directory — only directories WE created should be cleaned."""
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")
    (wiki / "page").mkdir()  # user pre-created the empty target dir
    monkeypatch.setattr(
        space._core, "_atomic_mutate_index",
        lambda *a, **k: (1, "simulated mid-promote failure"),
    )
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    # Pre-existing directory must survive.
    assert (wiki / "page").is_dir(), "pre-existing empty target dir was deleted on rollback"
    # Source must be restored.
    assert (wiki / "page.md").is_file()


def test_promote_preserves_snapshot_when_rollback_fails(tmp_path, monkeypatch):
    """Fail-closed: when the rollback ITSELF fails, the recovery snapshot must
    survive on disk — the error tells the user to recover from it, so the
    `finally` must not delete it. Mirrors `cmd_remove`'s snapshot guard."""
    import shutil as _shutil

    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# page\n\nbody\n")
    # Enter the rollback path: ancestor mutation fails...
    monkeypatch.setattr(
        space._core, "_atomic_mutate_index",
        lambda *a, **k: (1, "simulated mid-promote failure"),
    )

    # ...and the snapshot restore fails too (ROLLBACK ALSO FAILED branch).
    def _boom(*a, **k):
        raise OSError("simulated restore failure")

    monkeypatch.setattr(space.promote, "_restore_from_snapshot", _boom)
    rc, _, err = _run(["--wiki", str(wiki), "promote", "page.md"])
    assert rc == 2
    assert "ROLLBACK ALSO FAILED" in err
    marker = "Manual recovery from "
    assert marker in err
    snap = Path(err.split(marker, 1)[1].split(" may be required", 1)[0].strip())
    assert snap.is_dir(), "snapshot must be preserved when rollback fails"
    _shutil.rmtree(snap, ignore_errors=True)


# ---------- _atomic_mutate_index durability ----------


def test_atomic_mutate_index_fsyncs_parent_directory(tmp_path, monkeypatch):
    """`_atomic_mutate_index` makes the index rewrite durable by fsyncing the
    parent DIRECTORY after `os.replace`, not just the temp file. Driven here
    through `space add`, which routes its `## Spaces` registration through the
    helper. Regression guard for the atomic-replace durability pattern."""
    import os as _os
    import stat as _stat
    wiki = _make_wiki(tmp_path)
    fsynced_dirs: list[int] = []
    real_fsync = _os.fsync

    def _spy(fd):
        try:
            if _stat.S_ISDIR(_os.fstat(fd).st_mode):
                fsynced_dirs.append(fd)
        except OSError:
            pass
        return real_fsync(fd)

    monkeypatch.setattr(_os, "fsync", _spy)
    rc, _, _ = _run(["--wiki", str(wiki), "add", "concepts"])
    assert rc == 0
    assert fsynced_dirs, "expected a parent-directory fsync after os.replace"


def test_promote_alias_with_yaml_colon_roundtrips(tmp_path):
    """`promote` derives the injected alias from the source filename stem. A
    stem carrying a YAML `: ` indicator (`meeting: notes.md`) must be written
    as a quoted scalar so the consumer reads back exactly the alias promote
    reports adding — a bare `- meeting: notes` parses as a mapping and the
    alias is silently dropped (HANDBOOK producer=consumer).
    """
    wiki = _make_wiki(tmp_path)
    (wiki / "meeting: notes.md").write_text("# M\n\nbody\n", encoding="utf-8")
    rc, out, err = _run(["--wiki", str(wiki), "promote", "meeting: notes.md"])
    assert rc == 0, out + err
    idx = (wiki / "meeting: notes" / "index.md").read_text(encoding="utf-8")
    assert _md.parse_frontmatter_aliases(idx) == ["meeting: notes"]


def test_init_refuses_existing_file_target_without_traceback(tmp_path, capsys):
    """`init` onto a path that is an existing FILE is a filesystem boundary
    failure: it must surface as a clean stderr message + non-zero exit, not a
    raw `FileExistsError` escaping uncaught (HANDBOOK: handle failures at
    boundaries — errors to stderr with a cause and a meaningful exit code).
    """
    target = tmp_path / "afile"
    target.write_text("i am a file", encoding="utf-8")
    rc = init_wiki.main([str(target), "--no-config"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "could not create" in err


def test_init_refuses_readonly_parent_without_traceback(tmp_path, capsys):
    """`init` into a read-only parent directory must surface a clean refusal,
    not a raw `PermissionError` escaping uncaught."""
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        rc = init_wiki.main([str(ro / "sub"), "--no-config"])
    finally:
        ro.chmod(0o700)
    err = capsys.readouterr().err
    assert rc != 0
    assert "could not create" in err


# ---------- framework-write trust-boundary: escaping file symlinks ----------


def test_log_refuses_escaping_symlink_and_leaves_external_unchanged(tmp_path):
    """`space log` writes `<wiki>/log.md` via `atomic_write`, which FOLLOWS a
    symlink to its realpath. If `log.md` is a symlink escaping the wiki tree the
    write would mutate content OUTSIDE the trust boundary (HANDBOOK: writes stay
    inside the trust boundary). `space promote` already refuses this; `space log`
    must too. The external target must survive byte-for-byte."""
    wiki = _make_wiki(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    (wiki / "log.md").symlink_to(outside)
    rc, _out, err = _run(["--wiki", str(wiki), "log", "INJECT", "--field", "k=v"])
    assert rc != 0
    assert "symlink" in err.lower()
    assert outside.read_text() == "SECRET\n"


def test_log_internal_alias_symlink_still_appends(tmp_path):
    """Regression guard against over-refusal: a log.md alias whose realpath
    stays INSIDE the wiki is owned content and must still append normally."""
    wiki = _make_wiki(tmp_path)
    real = wiki / "log-real.md"
    real.write_text("# Log\n", encoding="utf-8")
    (wiki / "log.md").symlink_to(real)
    rc, _out, err = _run(["--wiki", str(wiki), "log", "OK", "--field", "k=v"])
    assert rc == 0, err
    assert "OK k=v" in real.read_text()


def test_add_force_index_refuses_escaping_symlink(tmp_path):
    """`space add --force-index` overwrites an existing `index.md` via
    `atomic_write`. If that index is a symlink escaping the wiki tree the write
    would clobber an external file — refuse and leave the target unchanged."""
    wiki = _make_wiki(tmp_path)
    rc, _out, err = _run(["--wiki", str(wiki), "add", "foo"])
    assert rc == 0, err
    foo_index = wiki / "foo" / "index.md"
    outside = tmp_path / "outside_index.txt"
    outside.write_text("EXTERNAL INDEX\n", encoding="utf-8")
    foo_index.unlink()
    foo_index.symlink_to(outside)
    rc, _out, err = _run(["--wiki", str(wiki), "add", "foo", "--force-index"])
    assert rc != 0
    assert "symlink" in err.lower()
    assert outside.read_text() == "EXTERNAL INDEX\n"


def test_log_refuses_non_utf8_file_via_cli(tmp_path):
    """`space log` on a non-UTF-8 `log.md` must exit non-zero with a clear error
    and leave the file untouched, not silently corrupt it."""
    wiki = _make_wiki(tmp_path)
    original = b"# Log\n\xff\xfe bad bytes"
    (wiki / "log.md").write_bytes(original)
    rc, _out, err = _run(["--wiki", str(wiki), "log", "OP", "--field", "k=v"])
    assert rc != 0
    assert "log.md" in err
    assert (wiki / "log.md").read_bytes() == original


def test_log_surfaces_oserror_as_clean_error_not_traceback(tmp_path, monkeypatch):
    """An `OSError` from the append helper must become a stderr message + exit 1,
    not an uncaught traceback (HANDBOOK: errors to stderr with a meaningful exit
    code; handle failures at boundaries)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "log.md").write_text("# Log\n", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(_log, "append_log_with_rotation", boom)
    rc, _out, err = _run(["--wiki", str(wiki), "log", "OP", "--field", "k=v"])
    assert rc == 1
    assert err.strip()


def test_audit_flags_self_referential_spaces_entry(tmp_path):
    """`space audit` must flag a self-referential `## Spaces` entry
    (`- [self](./index.md)`): the consumer walker skips it, so an audit that
    reports OK would pass a contract entry the consumer ignores (producer=consumer)."""
    wiki = _make_wiki(tmp_path)
    (wiki / "index.md").write_text(
        "# wiki\n\n## Spaces\n\n- [self](./index.md)\n", encoding="utf-8"
    )
    rc, out, err = _run(["--wiki", str(wiki), "audit"])
    assert rc != 0, f"audit should flag self-ref; out={out} err={err}"
    assert "self-referential" in (out + err).lower()


def test_add_rejects_control_chars_in_name(tmp_path):
    """`space add --name` becomes the child index's `# title`; a newline could
    inject a `## Spaces` heading into the child's contract. Refuse control
    chars (argv is a boundary input)."""
    wiki = _make_wiki(tmp_path)
    rc, _out, err = _run([
        "--wiki", str(wiki), "add", "foo", "--name", "Foo\n## Spaces\n- [e](e/index.md)",
    ])
    assert rc == 2
    assert not (wiki / "foo").exists()


def test_add_rejects_nel_separator_in_name(tmp_path):
    """`\\x85` (NEL) twin of the `--name` newline guard: it splits a line for the
    `## Spaces` consumer (`str.splitlines()`) yet sits above 0x20. Without
    closing the gap, the child index's first `## Spaces` is the injected one."""
    wiki = _make_wiki(tmp_path)
    rc, _out, err = _run([
        "--wiki", str(wiki), "add", "foo",
        "--name", "Foo\x85## Spaces\x85- [evil](evil/index.md)",
    ])
    assert rc == 2
    assert not (wiki / "foo").exists()


def test_add_rejects_nel_separator_in_description(tmp_path):
    """`--description` lands in BOTH the child body and the PARENT's `## Spaces`
    entry note. A bracket-free `\\x85## Spaces\\x85` (no link metachars, so the
    metachar guard passes it) splits those lines for the consumer and injects a
    stray `## Spaces` heading. `_validate_entry_text` routes through the same
    line-break guard, so closing the gap refuses it; nothing is written."""
    wiki = _make_wiki(tmp_path)
    rc, _out, err = _run([
        "--wiki", str(wiki), "add", "foo",
        "--description", "ok\x85## Spaces\x85more prose",
    ])
    assert rc == 2
    assert not (wiki / "foo").exists()


def test_add_rejects_line_separator_in_path_blocking_unreadable_space(tmp_path):
    """A `\\u2028` path segment is metachar-free, so the link-metachar guard
    passes it, but `str.splitlines()` (the consumer walker) splits the produced
    href across lines — the producer registers a `## Spaces` entry no consumer
    can read, and a stray `## Spaces` heading lands mid-index. Refuse it; the
    root `## Spaces` must hold zero entries afterward (producer=consumer)."""
    wiki = _make_wiki(tmp_path)
    rc, _out, err = _run([
        "--wiki", str(wiki), "add", "foo\u2028## Spaces\u2028bar",
    ])
    assert rc == 2
    root_text = (wiki / "index.md").read_text(encoding="utf-8")
    assert _md.parse_section_entries(root_text, "Spaces") == []


def test_audit_fix_does_not_register_control_char_named_drift_dir(tmp_path):
    """`audit --fix` registers discovered drift spaces by deriving the
    `## Spaces` entry from the on-disk directory name. A directory whose name
    carries a line-break char (never created by the CLI, but reachable on an
    externally-built tree) would produce an entry `str.splitlines()` splits,
    making the repair surface itself inject stray `## Spaces` headings and
    shadow the canonical contract (producer=consumer: a writer must never emit
    an unparseable entry). Such a directory is not a representable space, so
    discovery skips it: the root keeps exactly one `## Spaces` heading and a
    clean sibling drift dir still registers."""
    wiki = _make_wiki(tmp_path)
    (wiki / "clean").mkdir()
    (wiki / "clean" / "index.md").write_text("# Clean\n\n## Spaces\n\n", encoding="utf-8")
    bad = wiki / "topic\u2028## Spaces\u2028x"
    bad.mkdir()
    (bad / "index.md").write_text("# Bad\n\n## Spaces\n\n", encoding="utf-8")
    rc, _out, _err = _run(["--wiki", str(wiki), "audit", "--fix"])
    root_text = (wiki / "index.md").read_text(encoding="utf-8")
    assert sum(1 for ln in root_text.splitlines() if ln.strip() == "## Spaces") == 1
    hrefs = [e.href for e in _md.parse_section_entries(root_text, "Spaces")]
    assert "clean/index.md" in hrefs
    assert all(h is None or "\u2028" not in h for h in hrefs)


def test_promote_rejects_control_chars_in_frontmatter_summary(tmp_path):
    """`space promote` derives the parent `## Spaces` entry description from the
    promoted file's frontmatter `summary:` — a YAML boundary input. A summary
    with an embedded line break (here a blank line that PyYAML folds to `\\n`)
    splits the entry, injecting a sibling `## Spaces` entry the consumer reads
    as legitimate (HANDBOOK: distrust boundary inputs; producer=consumer).
    Refuse before any mutation: no injected entry, no promoted space."""
    wiki = _make_wiki(tmp_path)
    (wiki / "doc.md").write_text(
        '---\nsummary: "real\n\n- [evil](evil-target/index.md)"\n---\n# Doc\n\nbody\n',
        encoding="utf-8",
    )
    rc, _out, err = _run(["--wiki", str(wiki), "promote", "doc.md"])
    assert rc == 2
    entries = _md.parse_section_entries((wiki / "index.md").read_text(), "Spaces")
    assert all(e.href != "evil-target/index.md" for e in entries)
    assert not (wiki / "doc" / "index.md").exists()
