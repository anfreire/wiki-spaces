"""Unit tests for wiki_spaces.init_wiki."""

from __future__ import annotations

import functools

from wiki_spaces import _common, init_wiki

from tests.conftest import run_cli

_run = functools.partial(run_cli, entry=init_wiki.main)


# ---------- new_index_md ----------

def test_new_index_md():
    text = _common.new_index_md("MyWiki", "A description")
    assert text.startswith("# MyWiki")
    assert "## What this space is" in text
    assert "A description" in text
    assert "## Items" not in text
    assert "## Spaces" in text


def test_new_index_md_spaces_section_is_empty():
    """Empty `## Spaces` is spec-valid (the contract is 'exhaustive list,'
    not 'non-empty list'). A new wiki has no contained spaces yet."""
    text = _common.new_index_md("MyWiki", "A description")
    from wiki_spaces import _md
    assert _md.has_section(text, "Spaces")
    assert _md.parse_section_entries(text, "Spaces") == []


def test_new_index_md_omits_description_section_when_none():
    """When `description` is None or empty, `## What this space is` is omitted
    entirely — no placeholder text. `## Spaces` is always present."""
    text = _common.new_index_md("MyWiki")
    assert text.startswith("# MyWiki")
    assert "## What this space is" not in text
    assert "## Spaces" in text


def test_new_index_md_always_emits_spaces_section():
    """Every CLI-created wiki has `## Spaces` from t=0 — the navigation
    contract is part of what `init` produces, not an optional add-on."""
    text = _common.new_index_md("MyWiki", "A description")
    assert "## Spaces" in text


def test_init_with_no_description_writes_no_placeholder(monkeypatch, tmp_path):
    """Regression: `wiki-spaces init` without --description must NOT write
    the literal `<one paragraph describing this wiki>` placeholder into the
    user's index.md."""
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--no-config"])
    assert rc == 0
    body = (tmp_path / "wiki" / "index.md").read_text()
    assert "<one paragraph describing this wiki>" not in body
    assert "## Spaces" in body


def test_init_folders_created_but_not_listed_in_index(monkeypatch, tmp_path):
    """--folders creates directories on disk; index.md gets no `## Items`
    section — tools discover plain folders via the filesystem."""
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([
        str(tmp_path / "wiki"), "--folders", "concepts", "projects", "--no-config",
    ])
    assert rc == 0
    wiki = tmp_path / "wiki"
    assert (wiki / "concepts").is_dir() and (wiki / "projects").is_dir()
    assert "## Items" not in (wiki / "index.md").read_text()


def test_init_folders_reports_mkdir_failure_without_traceback(monkeypatch, tmp_path):
    """A filesystem failure creating a requested `--folders` dir (e.g. an
    existing read-only root) must surface as a clean stderr error + non-zero
    exit, not an uncaught traceback. Root creation is already wrapped; folder
    creation and `.gitkeep` must be too (HANDBOOK: handle failures at
    boundaries — filesystem)."""
    from pathlib import Path
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    real_mkdir = Path.mkdir

    def fake_mkdir(self, *a, **k):
        if self.name == "foo":
            raise PermissionError("simulated read-only parent")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    rc, out, err = _run([str(tmp_path / "wiki"), "--folders", "foo", "--no-config"])
    assert "Traceback" not in (out + err)
    assert rc == 1
    assert "could not create" in err
    assert not (tmp_path / "wiki" / "foo").exists()


# ---------- folder validation ----------

def test_init_rejects_dot_dot(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / "wiki"), "--folders", "../escape", "--no-config"])
    assert rc == 2


def test_init_rejects_absolute(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / "wiki"), "--folders", "/abs", "--no-config"])
    assert rc == 2


def test_init_rejects_hidden_segment(monkeypatch, tmp_path):
    """Reserved-folder contract is end-to-end (PR-G + 52ea345): hidden
    segments are skipped by every consumer walker per CONVENTIONS /
    Reserved top-level folder names. The producer side mirrors this —
    `space._validate_rel_path` already refused hidden segments for
    `space add`; `init --folders` must refuse them too or scaffolding
    creates content no skill can reach.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    for hidden in (".archive", ".config", ".cache", "projects/.cache"):
        rc, _, err = _run(
            [str(tmp_path / f"wiki-{hidden.replace('/', '-')}"),
             "--folders", hidden, "--no-config"]
        )
        assert rc == 2, f"hidden segment {hidden!r} accepted"
        assert "reserved" in err.lower() or "hidden" in err.lower() or "invalid" in err.lower()


def test_init_rejects_reserved_underscore_segments(monkeypatch, tmp_path):
    """`_archives` and `_meta` are excluded from consumer walks per
    CONVENTIONS / Reserved top-level folder names. `init --folders` must
    refuse them so the user can't accidentally scaffold a folder no skill
    will ever read.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    for bad in ("_archives", "_meta", "projects/_archives", "_meta/foo"):
        rc, _, err = _run(
            [str(tmp_path / f"wiki-{bad.replace('/', '-')}"),
             "--folders", bad, "--no-config"]
        )
        assert rc == 2, f"reserved segment {bad!r} accepted"
        assert "invalid" in err.lower() or "reserved" in err.lower()


def test_init_accepts_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "concepts/", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / "concepts").is_dir()


def test_init_rejects_reserved_wiki_root_basename(monkeypatch, tmp_path):
    """The wiki root basename can't be a reserved wiki-spaces name.
    Consumer walkers prune `_archives`, `_meta`, and `shared` as children
    regardless of context — so an init at any of these would silently
    bury the wiki if it ever ends up nested under another wiki. Refuse
    at producer time so the user gets a clear error instead of a buried,
    unreachable wiki later.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    for basename in ("_archives", "_meta", "shared"):
        rc, _, err = _run([str(tmp_path / basename), "--no-config"])
        assert rc == 2, f"reserved basename {basename!r} accepted as wiki root"
        assert "reserved" in err.lower()


def test_init_accepts_hidden_wiki_root_basename(monkeypatch, tmp_path):
    """Hidden basenames (`~/.notes/`) are a legitimate standalone-wiki UX
    pattern and are NOT refused at the wiki root — asymmetric with
    `_validate_rel_path` which refuses hidden child paths inside a wiki.
    The distinction reflects the producer/consumer contract: hidden names
    only have prune semantics as children of a wiki, not as wiki roots.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, err = _run([str(tmp_path / ".hidden"), "--no-config"])
    assert rc == 0, f"hidden wiki root refused (err={err})"
    assert (tmp_path / ".hidden" / "index.md").is_file()


def test_init_rejects_reserved_basename_via_symlink(monkeypatch, tmp_path):
    """The reserved-basename check uses the LEXICAL basename (pre-resolve)
    because walker pruning operates on lexical child names — a symlink at
    `<parent>/_archives` → `/real-wiki` is still pruned by parent walkers
    as `_archives`, regardless of where it resolves. Resolving first would
    let a symlink whose target has a non-reserved basename bypass the
    check.
    """
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    real_target = tmp_path / "real-wiki"
    real_target.mkdir()
    symlink = tmp_path / "_archives"
    symlink.symlink_to(real_target)
    rc, _, err = _run([str(symlink), "--no-config"])
    assert rc == 2, "symlink with reserved-name lexical basename should be refused"
    assert "reserved" in err.lower()


def test_init_refuses_when_non_directory_file_collides(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("preexisting file")
    rc, _, err = _run([str(wiki), "--folders", "log.md", "--no-config"])
    assert rc == 2


# ---------- scaffold output ----------

def test_init_writes_index_with_what_section(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--description", "Test", "--no-config"])
    assert rc == 0
    text = (tmp_path / "wiki" / "index.md").read_text()
    assert "# wiki" in text
    assert "Test" in text


def test_init_writes_optional_pack_files(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([
        str(tmp_path / "wiki"),
        "--with", "log.md", "_meta/taxonomy.md", ".manifest.json",
        "--no-config",
    ])
    assert rc == 0
    wiki = tmp_path / "wiki"
    assert (wiki / "log.md").is_file()
    assert (wiki / "_meta" / "taxonomy.md").is_file()
    assert (wiki / ".manifest.json").is_file()


def test_init_registers_default_when_no_no_config_flag(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    rc, _, _ = _run([str(tmp_path / "wiki"), "--description", "x"])
    assert rc == 0
    assert _common.read_config()["wiki"] == str((tmp_path / "wiki").resolve())


# ---------- nested --folders ----------

def test_init_accepts_nested_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "projects/foo", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / "projects" / "foo").is_dir()


def test_init_rejects_dotgit_segment(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "projects/.git", "--no-config"])
    assert rc == 2


def test_init_rejects_double_dot_segment(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "projects/../escape", "--no-config"])
    assert rc == 2


def test_init_deduplicates_normalized_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent-config")
    # Two args that normalize to the same path
    rc, _, _ = _run([str(tmp_path / "wiki"), "--folders", "concepts", "concepts/", "--no-config"])
    assert rc == 0
    assert (tmp_path / "wiki" / "concepts").is_dir()


# ---------- framework-write trust-boundary: escaping file symlinks ----------


def test_init_force_refuses_escaping_index_symlink(tmp_path):
    """`init` scaffolds `index.md` via `atomic_write`, which FOLLOWS a symlink to
    its realpath. If a pre-existing `index.md` is a symlink escaping the wiki tree,
    `--force` would clobber an EXTERNAL file (HANDBOOK: writes stay inside the
    trust boundary). Refuse, record the write error, and leave the target
    unchanged."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    outside = tmp_path / "outside_index.txt"
    outside.write_text("EXTERNAL INDEX\n", encoding="utf-8")
    (wiki / "index.md").symlink_to(outside)
    rc, _out, err = _run([str(wiki), "--force", "--no-config"])
    assert rc != 0
    assert "symlink" in err.lower()
    assert outside.read_text() == "EXTERNAL INDEX\n"


def test_init_rejects_control_chars_in_description_blocking_spaces_injection(monkeypatch, tmp_path):
    """`--description` is argv (a boundary input): a value carrying a newline +
    `## Spaces` would inject a SECOND `## Spaces` heading into the scaffold,
    ahead of the canonical one, corrupting the navigation contract a consumer
    reads first (HANDBOOK: distrust boundary inputs; producer=consumer). Refuse
    it; write nothing."""
    wiki = tmp_path / "wiki"
    rc, _out, err = _run([
        str(wiki), "--no-config",
        "--description", "real\n\n## Spaces\n\n- [evil](evil/index.md)\n",
    ])
    assert rc == 2
    assert "control" in err.lower() or "newline" in err.lower()
    assert not (wiki / "index.md").exists()


def test_init_rejects_control_chars_in_name(monkeypatch, tmp_path):
    """`--name` becomes the scaffold's `# title`; a newline could inject a
    heading. Refuse control chars."""
    wiki = tmp_path / "wiki"
    rc, _out, err = _run([str(wiki), "--no-config", "--name", "x\n## Spaces"])
    assert rc == 2
    assert not (wiki / "index.md").exists()


def test_init_rejects_nel_separator_in_description_blocking_spaces_injection(monkeypatch, tmp_path):
    """`\\x85` (NEL) is NOT below 0x20, so the original control-char guard let it
    through — yet `str.splitlines()` (the consumer in `_md.has_section`) splits
    on it. A `--description` carrying `\\x85## Spaces\\x85- [evil](...)` therefore
    injected a SECOND `## Spaces` ahead of the canonical one, and the consumer
    read the INJECTED entry first (HANDBOOK: distrust boundary inputs;
    producer=consumer). Refuse it; write nothing."""
    wiki = tmp_path / "wiki"
    rc, _out, err = _run([
        str(wiki), "--no-config",
        "--description", "real\x85\x85## Spaces\x85\x85- [evil](evil/index.md)",
    ])
    assert rc == 2
    assert "control" in err.lower() or "newline" in err.lower()
    assert not (wiki / "index.md").exists()


def test_init_rejects_nel_separator_in_name(monkeypatch, tmp_path):
    """The `\\x85` (NEL) twin of the `--name` newline guard: it splits a line
    for `str.splitlines()` but slips past an `ord(c) < 0x20` check."""
    wiki = tmp_path / "wiki"
    rc, _out, err = _run([
        str(wiki), "--no-config", "--name", "x\x85## Spaces\x85- [evil](evil/index.md)",
    ])
    assert rc == 2
    assert not (wiki / "index.md").exists()


def test_init_rejects_control_char_directory_basename(monkeypatch, tmp_path):
    """When `--name` is omitted, the wiki name falls back to the directory
    basename (`root.name`) — itself a boundary input. A directory named
    `proj\\u2028## Spaces` makes that fallback inject a SECOND `## Spaces`
    heading ahead of the canonical one, so the consumer (`str.splitlines()`)
    reads the injected section first and the real contract is shadowed
    (HANDBOOK: distrust boundary inputs; producer=consumer). Refuse; write
    nothing."""
    bad = tmp_path / "proj\u2028## Spaces\u2028stray"
    bad.mkdir()
    rc, _out, err = _run([str(bad), "--no-config"])
    assert rc == 2
    assert "control" in err.lower() or "newline" in err.lower()
    assert not (bad / "index.md").exists()


def test_adopt_does_not_register_control_char_named_nested_space(monkeypatch, tmp_path):
    """`init --adopt` registers every nested space it discovers, deriving each
    `## Spaces` entry from the on-disk directory name. A nested directory whose
    name carries a line-break char would produce an entry `str.splitlines()`
    splits, injecting stray `## Spaces` headings into the adopted root and
    shadowing the canonical contract (producer=consumer: a writer must never
    emit an unparseable entry). Such a directory is not a representable space,
    so discovery skips it: the root keeps exactly one `## Spaces` heading and a
    clean sibling space still registers (no collateral orphaning)."""
    from wiki_spaces import _md
    root = tmp_path / "notes"
    root.mkdir()
    (root / "index.md").write_text("# Notes\n\nmy notes\n", encoding="utf-8")
    (root / "clean").mkdir()
    (root / "clean" / "index.md").write_text("# Clean\n\n## Spaces\n\n", encoding="utf-8")
    bad = root / "topic\u2028## Spaces\u2028x"
    bad.mkdir()
    (bad / "index.md").write_text("# Topic\n\n## Spaces\n\n", encoding="utf-8")
    rc, _out, _err = _run([str(root), "--adopt", "--no-config"])
    root_text = (root / "index.md").read_text(encoding="utf-8")
    assert sum(1 for ln in root_text.splitlines() if ln.strip() == "## Spaces") == 1
    hrefs = [e.href for e in _md.parse_section_entries(root_text, "Spaces")]
    assert "clean/index.md" in hrefs
    assert all(h is None or "\u2028" not in h for h in hrefs)
