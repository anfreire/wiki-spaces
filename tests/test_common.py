"""Unit tests for wiki_spaces._common."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wiki_spaces import _common


# ---------- read_config / write_config ----------

def test_read_config_returns_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(_common, "CONFIG_PATH", tmp_path / "absent")
    assert _common.read_config() == {}


def test_resolve_wiki_refuses_unreadable_config_instead_of_cwd_fallback(tmp_path, monkeypatch):
    """An existing-but-UNREADABLE config must hard-stop with a clear cause, not
    be swallowed as "no config" and silently fall back to a CWD wiki — that
    would operate on a DIFFERENT wiki than the user configured (HANDBOOK:
    handle failures at boundaries; scope-safety). A directory at CONFIG_PATH
    reproduces the unreadable case without depending on POSIX perms (root would
    bypass chmod 000)."""
    cwd_wiki = tmp_path / "cwd"
    cwd_wiki.mkdir()
    (cwd_wiki / "index.md").write_text("# w\n\n## Spaces\n\n")
    monkeypatch.chdir(cwd_wiki)
    unreadable = tmp_path / "config-as-dir"
    unreadable.mkdir()
    monkeypatch.setattr(_common, "CONFIG_PATH", unreadable)
    wiki, err = _common.resolve_wiki(None, repair=False)
    assert wiki is None
    assert err is not None
    assert "could not be read" in err


def test_no_wiki_message_names_resolved_config_path(monkeypatch, tmp_path):
    """C7 (codex follow-up): the no-wiki resolver message must name the
    XDG-aware CONFIG_PATH the tool actually reads, not a hardcoded
    `~/.config/...` that diverges under $XDG_CONFIG_HOME (producer=consumer)."""
    # A resolved config path NOT under ~/.config (as $XDG_CONFIG_HOME yields),
    # and absent so no wiki resolves from config.
    cfg = tmp_path / "xdg" / "wiki-spaces" / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    # CWD with no wiki ancestor so the CWD fallback also finds nothing.
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    wiki, err = _common.resolve_wiki(None, repair=True)
    assert wiki is None
    assert err is not None
    assert str(cfg) in err


def test_read_config_handles_non_utf8_without_crashing(monkeypatch, tmp_path):
    """A non-UTF-8 config file is a boundary input; `read_config` must fall back
    to {} like any unreadable config, not crash with a `UnicodeDecodeError` (a
    `ValueError`, NOT an `OSError`). `config_exists_unreadable` is what flags it
    (HANDBOOK: handle failures at boundaries — parse)."""
    cfg = tmp_path / "config"
    cfg.write_bytes(b"wiki = /x \xff\xfe\nrepo = /y\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {}


def test_config_exists_unreadable_true_for_non_utf8(monkeypatch, tmp_path):
    """A non-UTF-8 config exists but cannot be decoded — `config_exists_unreadable`
    must report it unreadable (True), not crash, so the resolver hard-stops
    instead of silently falling back to a CWD wiki (scope-safety)."""
    cfg = tmp_path / "config"
    cfg.write_bytes(b"\xff\xfe not utf-8\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.config_exists_unreadable() is True


def test_resolve_wiki_refuses_non_utf8_config_instead_of_crashing(tmp_path, monkeypatch):
    """A non-UTF-8 config must hard-stop with "could not be read", mirroring the
    OSError-unreadable case, not crash discovery with a raw `UnicodeDecodeError`."""
    cwd_wiki = tmp_path / "cwd"
    cwd_wiki.mkdir()
    (cwd_wiki / "index.md").write_text("# w\n\n## Spaces\n\n")
    monkeypatch.chdir(cwd_wiki)
    cfg = tmp_path / "config"
    cfg.write_bytes(b"wiki = /x \xff\xfe\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    wiki, err = _common.resolve_wiki(None, repair=False)
    assert wiki is None
    assert err is not None and "could not be read" in err
    assert "~/.config/wiki-spaces/config" not in err


def test_read_config_parses_keys(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("wiki = /home/u/Wiki\nrepo = /home/u/repo\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki", "repo": "/home/u/repo"}


def test_read_config_strips_whitespace(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("  wiki  =   /home/u/Wiki  \n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki"}


def test_read_config_ignores_whole_line_comments(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("# a comment\nwiki = /home/u/Wiki\n# another\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki"}


def test_read_config_treats_inline_hash_as_part_of_value(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("wiki = /home/u/Wiki#sub\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config()["wiki"] == "/home/u/Wiki#sub"


def test_read_config_ignores_blank_lines(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("\n\nwiki = /home/u/Wiki\n\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    assert _common.read_config() == {"wiki": "/home/u/Wiki"}


def test_write_config_creates_parent_dirs(monkeypatch, tmp_path):
    cfg = tmp_path / "nested" / "deep" / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    _common.write_config({"wiki": "/home/u/Wiki"})
    assert cfg.exists()
    assert _common.read_config()["wiki"] == "/home/u/Wiki"


def test_write_config_merges_keys(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    _common.write_config({"wiki": "/w"})
    _common.write_config({"repo": "/r"})
    out = _common.read_config()
    assert out == {"wiki": "/w", "repo": "/r"}


def test_read_config_result_distinguishes_missing_ok_unreadable(monkeypatch, tmp_path):
    """The config reader must model status as a TYPED value, not collapse
    ABSENT and UNREADABLE into the same `{}` (HANDBOOK: "Missing and malformed
    are typed values — not None, not a special case"). One filesystem pass
    yields the distinction, so `read_config` / `config_exists_unreadable` /
    `write_config` share a single read instead of re-`read_text`-ing to
    re-derive it."""
    cfg = tmp_path / "config"
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    # MISSING
    r = _common._read_config()
    assert r.status is _common.ConfigReadStatus.MISSING
    assert r.values == {}
    # OK
    cfg.write_text("wiki = /w\nrepo = /r\n", encoding="utf-8")
    r = _common._read_config()
    assert r.status is _common.ConfigReadStatus.OK
    assert r.values == {"wiki": "/w", "repo": "/r"}
    # UNREADABLE (non-UTF-8)
    cfg.write_bytes(b"wiki = /w\n\xff\xfe")
    r = _common._read_config()
    assert r.status is _common.ConfigReadStatus.UNREADABLE
    assert r.values == {}


def test_write_config_refuses_to_clobber_unreadable_config(monkeypatch, tmp_path):
    """`write_config` merges over `read_config`, which collapses an unreadable
    config to `{}`. A merge of one key into `{}` then writes ONLY that key,
    silently dropping the other configured path — exactly the data loss the
    resolver already guards against via `config_exists_unreadable` (HANDBOOK:
    handle failures at boundaries; scope-safety). The producer must hard-stop
    on the same condition the consumer does, not clobber. A non-UTF-8 config
    holding BOTH keys reproduces it: an `install`-style `{"repo": ...}` write
    must raise, and the original bytes must survive untouched."""
    cfg = tmp_path / "config"
    original = b"wiki = /real/wiki\nrepo = /real/repo\n\xff\xfe"
    cfg.write_bytes(original)
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    with pytest.raises(_common.ConfigUnreadableError):
        _common.write_config({"repo": "/new/repo"})
    assert cfg.read_bytes() == original  # not clobbered; wiki key preserved


# ---------- _nearest_space_root (require_section True/False) ----------

def test_nearest_space_root_for_repair_finds_self_bare_index(tmp_path):
    """Repair resolver accepts bare-`index.md` (no `## Spaces`); write
    commands lean on the chain helper to insert the section atomically."""
    (tmp_path / "index.md").write_text("")
    assert _common._nearest_space_root(tmp_path, require_section=False) == tmp_path.resolve()


def test_nearest_space_root_for_repair_walks_up(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("")
    deep = wiki / "projects" / "deep" / "nested"
    deep.mkdir(parents=True)
    assert _common._nearest_space_root(deep, require_section=False) == wiki.resolve()


def test_nearest_space_root_for_repair_none_when_no_ancestor(tmp_path):
    sub = tmp_path / "no" / "wiki" / "here"
    sub.mkdir(parents=True)
    assert _common._nearest_space_root(sub, require_section=False) is None


def test_nearest_space_root_for_repair_from_file(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("")
    page = wiki / "page.md"
    page.write_text("")
    assert _common._nearest_space_root(page, require_section=False) == wiki.resolve()


def test_nearest_space_root_strict_requires_spaces_section(tmp_path):
    """Strict resolver returns None on a bare-`index.md` wiki — read-only
    commands (audit, skills, doctor) refuse to operate on a folder that
    lacks the v1 navigation contract."""
    (tmp_path / "index.md").write_text("# bare\n")  # no `## Spaces`
    assert _common._nearest_space_root(tmp_path, require_section=True) is None


def test_nearest_space_root_strict_accepts_index_with_spaces(tmp_path):
    (tmp_path / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    assert _common._nearest_space_root(tmp_path, require_section=True) == tmp_path.resolve()


def test_nearest_space_root_strict_walks_up_past_missing_section(tmp_path):
    """Strict resolver skips folders that lack `## Spaces` while walking up
    — the closest valid ancestor wins, not the closest folder with
    `index.md` but no `## Spaces`."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "index.md").write_text("# outer\n\n## Spaces\n\n")
    inner = outer / "inner"
    inner.mkdir()
    (inner / "index.md").write_text("# inner\n")  # bare; no `## Spaces`
    assert _common._nearest_space_root(inner, require_section=True) == outer.resolve()


# ---------- link_or_copy ----------

def test_link_or_copy_prefer_copy_replaces_symlink(tmp_path):
    """prefer_copy=True must replace an existing symlink with a real copy."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("x")
    dst = tmp_path / "dst"
    assert _common.link_or_copy(src, dst) == _common.LinkResult.SYMLINK
    assert dst.is_symlink()
    assert _common.link_or_copy(src, dst, prefer_copy=True) == _common.LinkResult.COPY
    assert not dst.is_symlink()


def test_link_or_copy_copy_mode_mirrors_removing_stale_files(tmp_path):
    """Copy-mode install must MIRROR the source dir, not merge into it: a file
    removed upstream must not persist in the destination on reinstall —
    otherwise stale skill instructions linger beside the current ones
    (HANDBOOK: one source of truth; delete superseded). The symlink path
    already replaces the dir; the copy path must be consistent."""
    src = tmp_path / "src" / "skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("current", encoding="utf-8")
    dst = tmp_path / "dst" / "skill"
    assert _common.link_or_copy(src, dst, prefer_copy=True) == _common.LinkResult.COPY
    # A file that existed in a prior version but was removed upstream.
    (dst / "old_reference.md").write_text("STALE — removed upstream", encoding="utf-8")
    _common.link_or_copy(src, dst, prefer_copy=True)
    assert (dst / "SKILL.md").read_text() == "current"
    assert not (dst / "old_reference.md").exists()


# ---------- write_owned_marker ----------

def test_write_owned_marker_records_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    _common.write_owned_marker(dst, src)
    marker = dst / _common.OWNED_MARKER
    assert marker.is_file()
    body = marker.read_text(encoding="utf-8")
    assert "Installed by wiki-spaces" in body
    assert f"source = {src.resolve()}" in body


def test_write_owned_marker_is_noop_on_non_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "file.txt"
    dst.write_text("not a dir", encoding="utf-8")
    _common.write_owned_marker(dst, src)
    assert dst.read_text(encoding="utf-8") == "not a dir"
    assert not (tmp_path / _common.OWNED_MARKER).exists()


# ---------- HARNESSES matrix ----------

def test_harnesses_are_the_seven_verified():
    keys = sorted(h.key for h in _common.HARNESSES)
    assert keys == ["claude", "codex", "copilot", "cursor", "gemini", "kiro", "opencode"]


def test_only_claude_and_kiro_have_alias_dirs():
    aliased = sorted(h.key for h in _common.HARNESSES if h.alias_dirs)
    assert aliased == ["claude", "kiro"]


def test_hub_readers_have_no_alias_dirs():
    for h in _common.HARNESSES:
        if h.reads_hub:
            assert h.alias_dirs == ()


def test_claude_kiro_alias_paths():
    claude = next(h for h in _common.HARNESSES if h.key == "claude")
    kiro = next(h for h in _common.HARNESSES if h.key == "kiro")
    assert claude.alias_dirs == (_common.HOME / ".claude" / "skills",)
    assert kiro.alias_dirs == (_common.HOME / ".kiro" / "skills",)
    assert claude.reads_hub is False and kiro.reads_hub is False


def test_harness_keys_are_unique():
    keys = [h.key for h in _common.HARNESSES]
    assert len(keys) == len(set(keys))

# ---------- __version__ single-sourcing ----------

def test_version_matches_pyproject():
    """`wiki_spaces.__version__` must equal pyproject.toml's [project] version.

    Codex flagged version drift across __init__.py / pyproject.toml / uv.lock.
    pyproject.toml is the single source via importlib.metadata; uv.lock tracks
    it automatically on next `uv lock`. This test catches drift if anyone
    edits __init__.py and forgets pyproject (or vice versa).
    """
    import tomllib
    import wiki_spaces

    repo_root = Path(__file__).resolve().parent.parent
    with (repo_root / "pyproject.toml").open("rb") as f:
        meta = tomllib.load(f)
    assert wiki_spaces.__version__ == meta["project"]["version"]


# ---------- installed_state ----------

def test_installed_state_missing(tmp_path):
    assert _common.installed_state(tmp_path / "absent", tmp_path / "src") == _common.InstalledState.MISSING


def test_installed_state_symlink_ok(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text("ok")
    dst = tmp_path / "dst"
    os.symlink(src, dst)
    assert _common.installed_state(dst, src) == _common.InstalledState.SYMLINK_OK


def test_installed_state_symlink_broken(tmp_path):
    """Dangling symlink — target does not exist."""
    dst = tmp_path / "dst"
    os.symlink(tmp_path / "nonexistent", dst)
    src = tmp_path / "src"
    src.mkdir()
    assert _common.installed_state(dst, src) == _common.InstalledState.SYMLINK_BROKEN


def test_installed_state_symlink_external(tmp_path):
    """Symlink points at a valid path that is not the expected source —
    e.g. an aggregator directory with its own copy of the skill."""
    src = tmp_path / "src"
    src.mkdir()
    other = tmp_path / "aggregator"
    other.mkdir()
    dst = tmp_path / "dst"
    os.symlink(other, dst)
    assert _common.installed_state(dst, src) == _common.InstalledState.SYMLINK_EXTERNAL


def test_installed_state_copy_current(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text("ok")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "SKILL.md").write_text("ok")
    os.utime(dst / "SKILL.md", (9999999999, 9999999999))
    assert _common.installed_state(dst, src) == _common.InstalledState.COPY_CURRENT


def test_installed_state_copy_stale(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text("ok")
    os.utime(src / "SKILL.md", (9999999999, 9999999999))
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "SKILL.md").write_text("ok")
    os.utime(dst / "SKILL.md", (1000000000, 1000000000))
    assert _common.installed_state(dst, src) == _common.InstalledState.COPY_STALE


def test_installed_state_plain_file_is_wrong_shape(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text("ok")
    dst = tmp_path / "dst"
    dst.write_text("not a skill directory")
    assert _common.installed_state(dst, src) == _common.InstalledState.WRONG_SHAPE


# ---------- atomic_write ----------


def test_atomic_write_writes_content(tmp_path):
    target = tmp_path / "out.md"
    _common.atomic_write(target, "hello\n")
    assert target.read_text() == "hello\n"


def test_atomic_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    """atomic_write fsyncs both the temp file (bytes durable) and the parent
    directory (rename durable), so neither the new bytes nor the rename can be
    lost on a crash."""
    import stat

    saw_file: list[int] = []
    saw_dir: list[int] = []
    real_fsync = os.fsync

    def _spy(fd):
        try:
            mode = os.fstat(fd).st_mode
            (saw_dir if stat.S_ISDIR(mode) else saw_file).append(fd)
        except OSError:
            pass
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy)
    _common.atomic_write(tmp_path / "out.md", "x\n")
    assert saw_file, "expected the temp file to be fsynced before rename"
    assert saw_dir, "expected the parent directory to be fsynced after rename"


def test_atomic_write_interrupted_leaves_old_file_intact(tmp_path, monkeypatch):
    """A failure between temp-write and rename leaves the OLD file intact and
    drops no partial target — the fail-closed guarantee every content write
    now inherits. The reader sees the complete old file or the complete new
    one, never a half-written body."""
    target = tmp_path / "index.md"
    target.write_text("original\n", encoding="utf-8")

    def _boom(src, dst):
        raise OSError("simulated crash before the rename commits")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        _common.atomic_write(target, "new body that must never land\n")

    # Old content survives untouched...
    assert target.read_text() == "original\n"
    # ...and the temp file is cleaned up, not left orphaned in the directory.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "index.md"]
    assert leftovers == [], f"unexpected leftover temp files: {leftovers}"


def test_atomic_write_follows_symlink_to_real_target(tmp_path):
    """When the path is a symlink, the write lands on the resolved target
    (matching write_text) and the link itself is preserved — promote relies on
    this when atomically rewriting links in a symlinked sibling page."""
    real = tmp_path / "real.md"
    real.write_text("old\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(real)

    _common.atomic_write(link, "new\n")

    assert link.is_symlink(), "the symlink itself must be preserved"
    assert real.read_text() == "new\n", "content must land on the resolved target"
    assert link.read_text() == "new\n"


# ---------- normalize_wiki_flag ----------


def test_normalize_wiki_flag_lifts_spaced_form_to_front():
    # `space audit --wiki /w` (the natural order) → flag re-injected at front.
    assert _common.normalize_wiki_flag(["audit", "--wiki", "/w"]) == [
        "--wiki", "/w", "audit",
    ]


def test_normalize_wiki_flag_lifts_equals_form_to_front():
    assert _common.normalize_wiki_flag(["audit", "--wiki=/w", "x"]) == [
        "--wiki=/w", "audit", "x",
    ]


def test_normalize_wiki_flag_absent_is_unchanged():
    assert _common.normalize_wiki_flag(["audit", "x"]) == ["audit", "x"]


def test_normalize_wiki_flag_dangling_flag_left_for_argparse():
    # `--wiki` with no value is left in place so argparse emits its own error.
    assert _common.normalize_wiki_flag(["audit", "--wiki"]) == ["audit", "--wiki"]


# ---------- has_control_chars (producer guard must match the consumer) ----------

def test_has_control_chars_matches_splitlines_boundaries():
    """`has_control_chars` is the producer guard for "would split a one-line
    field across lines"; the CONSUMER that splits is `str.splitlines()`
    (`_md.has_section` / `parse_section_entries`). Producer=consumer demands the
    guard reject EVERY char `str.splitlines()` treats as a line boundary — else
    a value the producer accepts is split by the consumer, injecting a stray
    `## Spaces` heading. NEL (`\\x85`), LS (`\\u2028`), and PS (`\\u2029`) are the
    above-0x20 boundaries `splitlines()` honors."""
    for ch in ("\x85", "\u2028", "\u2029"):
        assert f"a{ch}b".splitlines() == ["a", "b"], (
            f"{ch!r} unexpectedly not a splitlines boundary"
        )
        assert _common.has_control_chars(f"a{ch}b"), (
            f"has_control_chars missed splitlines-boundary char {ch!r} "
            "(producer=consumer break: consumer str.splitlines() splits on it)"
        )


def test_has_control_chars_passes_clean_text():
    """Brackets, parens, em-dash, and ordinary unicode are NOT line breaks and
    must pass — the guard targets line-splitting control chars only."""
    for ok in ("Foo Bar", "title (v2)", "a — b", "café", "项目"):
        assert not _common.has_control_chars(ok), f"clean value {ok!r} rejected"
