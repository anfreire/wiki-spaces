"""Tests for `doctor` exit-code behavior.

`doctor` is the documented verify step (README, references/SETUP.md). It must
exit non-zero when the config is missing or invalid so setup scripts can gate
on it — these tests pin that contract.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from wiki_spaces import doctor
from wiki_spaces._common import Harness

from tests.conftest import (
    ALL_SKILL_NAMES,
    run_cli_subprocess,
    seed_fake_home,
)


def _run_main(monkeypatch, cfg, *, wiki_state=None, repo_state=None):
    """Run doctor.main with config + validators stubbed; vendor/harness no-op'd.

    `wiki_state`/`repo_state` are `ValidationState` members (default OK);
    the stubs wrap them in `ValidationResult` so `check_config` reads them
    via the enum API it now uses."""
    wiki_state = wiki_state or doctor.ValidationState.OK
    repo_state = repo_state or doctor.ValidationState.OK
    monkeypatch.setattr(doctor, "read_config", lambda: cfg)
    monkeypatch.setattr(
        doctor, "_validate_wiki", lambda w: doctor.ValidationResult(wiki_state)
    )
    monkeypatch.setattr(
        doctor, "_validate_repo", lambda r: doctor.ValidationResult(repo_state)
    )
    monkeypatch.setattr(doctor, "check_vendor", lambda net: None)
    monkeypatch.setattr(doctor, "check_harness", lambda h: None)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = doctor.main(["--no-net"])
    return rc, out.getvalue()


def test_exits_nonzero_when_config_missing(monkeypatch):
    rc, out = _run_main(monkeypatch, {})
    assert rc == 1
    assert "config incomplete or invalid" in out


def test_exits_nonzero_when_wiki_invalid(monkeypatch):
    rc, _ = _run_main(
        monkeypatch, {"wiki": "/x", "repo": "/y"},
        wiki_state=doctor.ValidationState.MISSING_ON_DISK,
    )
    assert rc == 1


def test_exits_nonzero_when_repo_invalid(monkeypatch):
    rc, _ = _run_main(
        monkeypatch, {"wiki": "/x", "repo": "/y"},
        repo_state=doctor.ValidationState.NOT_ABSOLUTE,
    )
    assert rc == 1


def test_exits_nonzero_when_wiki_unset(monkeypatch):
    rc, _ = _run_main(monkeypatch, {"repo": "/y"})
    assert rc == 1


def test_exits_nonzero_when_repo_unset(monkeypatch):
    rc, _ = _run_main(monkeypatch, {"wiki": "/x"})
    assert rc == 1


def test_exits_zero_when_config_valid(monkeypatch):
    rc, _ = _run_main(monkeypatch, {"wiki": "/x", "repo": "/y"})
    assert rc == 0


def test_check_config_returns_bool(monkeypatch):
    """check_config reports validity as its return value, not just stdout."""
    ok = doctor.ValidationResult(doctor.ValidationState.OK)
    monkeypatch.setattr(doctor, "read_config", lambda: {"wiki": "/x", "repo": "/y"})
    monkeypatch.setattr(doctor, "_validate_wiki", lambda w: ok)
    monkeypatch.setattr(doctor, "_validate_repo", lambda r: ok)
    with redirect_stdout(io.StringIO()):
        assert doctor.check_config() is True
    monkeypatch.setattr(
        doctor, "_validate_repo",
        lambda r: doctor.ValidationResult(doctor.ValidationState.MISSING_ON_DISK),
    )
    with redirect_stdout(io.StringIO()):
        assert doctor.check_config() is False


def test_check_config_reports_unreadable_distinctly_from_missing(monkeypatch):
    """An existing-but-unreadable config must be diagnosed as unreadable, not
    collapsed into "missing". `read_config` returns {} for both, but doctor's
    job is accurate diagnosis at this boundary — and the resolver was already
    hardened with `config_exists_unreadable`, so doctor must use it too."""
    monkeypatch.setattr(doctor, "read_config", lambda: {})
    monkeypatch.setattr(doctor, "config_exists_unreadable", lambda: True)
    out = io.StringIO()
    with redirect_stdout(out):
        assert doctor.check_config() is False
    text = out.getvalue()
    assert "could not be read" in text
    # Not the missing-branch guidance — that would mis-diagnose an unreadable
    # config as absent and send the user to re-install rather than fix perms.
    assert "wiki-spaces install" not in text


def test_check_config_survives_non_utf8_config_file(monkeypatch, tmp_path):
    """A real non-UTF-8 config file must not crash `doctor`: `read_config` falls
    back to {} and `config_exists_unreadable` flags it, so check_config reports
    "could not be read" instead of a raw `UnicodeDecodeError` (HANDBOOK: handle
    failures at boundaries — parse)."""
    from wiki_spaces import _common
    cfg = tmp_path / "config"
    cfg.write_bytes(b"wiki = /x \xff\xfe\nrepo = /y\n")
    monkeypatch.setattr(_common, "CONFIG_PATH", cfg)
    out = io.StringIO()
    with redirect_stdout(out):
        assert doctor.check_config() is False
    assert "could not be read" in out.getvalue()


def _fake_install(tmp_path: Path) -> Path:
    """Materialize a minimal valid wiki-spaces install: every REPO_SENTINEL
    file present, content irrelevant."""
    root = tmp_path / "install"
    for sentinel in doctor.REPO_SENTINELS:
        target = root / sentinel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    return root


def test_validate_repo_ok_for_complete_install(tmp_path):
    """All sentinels present ⇒ valid."""
    root = _fake_install(tmp_path)
    assert doctor._validate_repo(str(root)).state is doctor.ValidationState.OK


@pytest.mark.parametrize("sentinel", list(doctor.REPO_SENTINELS))
def test_validate_repo_flags_any_missing_sentinel(tmp_path, sentinel):
    """Each sentinel is individually load-bearing. Missing any one ⇒ invalid
    repo. Parametrized so new sentinels are automatically covered."""
    root = _fake_install(tmp_path)
    (root / sentinel).unlink()
    result = doctor._validate_repo(str(root))
    assert result.state is doctor.ValidationState.NOT_AN_INSTALL
    assert sentinel in (result.detail or "")


def test_validators_return_typed_state_not_strings(tmp_path):
    """The validators return a typed `ValidationState`, never a rendered
    string — callers compare on the enum (`.ok` / `.state`) and the human
    text is produced only at the print site via `.render()`."""
    (tmp_path / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    assert doctor._validate_wiki(str(tmp_path)).state is doctor.ValidationState.OK
    assert (
        doctor._validate_wiki("rel/path").state
        is doctor.ValidationState.NOT_ABSOLUTE
    )
    assert (
        doctor._validate_wiki(str(tmp_path / "absent")).state
        is doctor.ValidationState.MISSING_ON_DISK
    )
    # render() folds detail into the human line; ok mirrors the OK state.
    missing = doctor._validate_repo(str(tmp_path / "bare"))
    assert not missing.ok
    assert missing.render() == "MISSING ON DISK"


def test_repo_sentinels_includes_all_three_wiki_skills():
    """Codex blocker: doctor previously only checked wiki-search/SKILL.md, so
    a missing wiki-update or wiki-tend would slip through. This pins the fix."""
    assert "skills/ws-search/SKILL.md" in doctor.REPO_SENTINELS
    assert "skills/ws-update/SKILL.md" in doctor.REPO_SENTINELS
    assert "skills/ws-tend/SKILL.md" in doctor.REPO_SENTINELS


def test_repo_sentinels_includes_both_kepano_skills():
    """AGENTS.md names both kepano skills as the syntax reference, so doctor
    must require both to consider the install valid."""
    assert "vendor/kepano/obsidian-markdown/SKILL.md" in doctor.REPO_SENTINELS
    assert "vendor/kepano/obsidian-bases/SKILL.md" in doctor.REPO_SENTINELS


# ---------- PR-D: --wiki flag + `## Spaces` validation ----------

def test_doctor_validate_wiki_rejects_bare_index_folder(tmp_path):
    """Direct call to _validate_wiki: bare `index.md` (no `## Spaces`)
    returns the typed NO_SPACES_SECTION state. v1 contract: a wiki needs
    `## Spaces`."""
    (tmp_path / "index.md").write_text("# bare\n")  # no `## Spaces`
    result = doctor._validate_wiki(str(tmp_path))
    assert result.state is doctor.ValidationState.NO_SPACES_SECTION
    assert not result.ok


def test_doctor_validate_wiki_ok_with_spaces_section(tmp_path):
    (tmp_path / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    assert doctor._validate_wiki(str(tmp_path)).state is doctor.ValidationState.OK


def test_doctor_wiki_flag_accepts_valid_path(tmp_path, capsys):
    (tmp_path / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    rc = doctor.main(["--wiki", str(tmp_path)])
    assert rc == 0


def test_doctor_wiki_flag_rejects_bare_index(tmp_path, capsys):
    """`doctor --wiki <bare>` exits 1 — the path resolves to a folder with
    `index.md` but no navigation contract."""
    (tmp_path / "index.md").write_text("# bare\n")
    rc = doctor.main(["--wiki", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Spaces" in err


def test_doctor_wiki_flag_rejects_missing_index(tmp_path, capsys):
    rc = doctor.main(["--wiki", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no index.md" in err


def test_doctor_missing_commit_flags_repo_invalid(tmp_path):
    """PR-O / §42: a packaged install with missing `vendor/kepano/COMMIT`
    must fail repo validation — that's the actual fix (force reinstall),
    not the old "run vendor-kepano" message which packaged installs
    refuse to run."""
    # Materialize a fake install missing only COMMIT.
    root = tmp_path / "install"
    for sentinel in doctor.REPO_SENTINELS:
        if sentinel == "vendor/kepano/COMMIT":
            continue  # deliberately absent
        target = root / sentinel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
    result = doctor._validate_repo(str(root))
    assert result.state is doctor.ValidationState.NOT_AN_INSTALL
    assert "vendor/kepano/COMMIT" in (result.detail or "")


# ---------- PR-F: CLI help surface ----------

def test_cli_help_hides_vendor_kepano_when_packaged(monkeypatch, capsys):
    """`vendor-kepano` is a dev-only subcommand — the install refuses it on
    packaged wheels (`vendor_kepano.main` checks `is_packaged()`). It must
    not appear in the packaged `--help` text either; users would otherwise
    see a command they cannot actually run."""
    from wiki_spaces import cli, _common
    monkeypatch.setattr(_common, "is_packaged", lambda: True)
    assert "vendor-kepano" not in cli._help_text()


def test_cli_help_includes_vendor_kepano_in_dev(monkeypatch, capsys):
    """In a dev checkout (`is_packaged()` False), `vendor-kepano` IS
    listed — that's the audience for the command."""
    from wiki_spaces import cli, _common
    monkeypatch.setattr(_common, "is_packaged", lambda: False)
    assert "vendor-kepano" in cli._help_text()


def test_cli_help_no_longer_mentions_update():
    """`update` was removed in PR-F; the help text should not advertise it."""
    from wiki_spaces import cli
    help_text = cli._help_text()
    # Match the column "update" line specifically; "updated" / "updating"
    # prose elsewhere would be fine.
    assert "  update " not in help_text


def test_check_vendor_handles_non_utf8_commit_without_crashing(tmp_path, monkeypatch, capsys):
    """`doctor` is a boundary diagnostic: an unreadable / non-UTF-8 vendored
    `COMMIT` file must produce a clear finding, not a raw traceback (HANDBOOK:
    handle failures at boundaries). Mirrors how the rest of the tool treats a
    non-UTF-8 config / index.md."""
    vendor = tmp_path / "vendor" / "kepano"
    vendor.mkdir(parents=True)
    (vendor / "COMMIT").write_bytes(b"\xff\xfe not utf-8\n")
    monkeypatch.setattr(doctor, "data_root", lambda: tmp_path)
    doctor.check_vendor(net=False)  # must not raise
    out = capsys.readouterr().out
    assert "COMMIT" in out


# ---------- hub + alias reporting (in-process, no HOME dependence) ----------


def _harness(key: str, *, reads_hub: bool, alias_dir: Path | None = None) -> Harness:
    return Harness(
        key=key,
        detect=(),
        reads_hub=reads_hub,
        alias_dirs=((alias_dir,) if alias_dir is not None else ()),
        source_url="https://example.test/skills",
    )


def test_check_harness_hub_reader_reports_served_by_hub(capsys):
    doctor.check_harness(_harness("codex", reads_hub=True))
    out = capsys.readouterr().out
    assert "codex:" in out
    assert "served by hub" in out


def test_check_harness_undetected_hub_reader_uses_conditional_wording(capsys):
    doctor.check_harness(_harness("codex", reads_hub=True))
    out = capsys.readouterr().out
    assert "codex: not detected" in out
    assert "would be served by hub if present" in out


def test_check_harness_non_hub_lists_each_skill(tmp_path, capsys):
    alias_dir = tmp_path / "claude" / "skills"
    doctor.check_harness(_harness("claude", reads_hub=False, alias_dir=alias_dir))
    out = capsys.readouterr().out
    assert "served by hub" not in out
    for skill in ALL_SKILL_NAMES:
        assert skill in out


# ---------- subprocess fake-HOME verification of the install + doctor flow ----------

_HUB_READER_KEYS = ("codex", "gemini", "opencode", "copilot", "cursor")
_ALIAS_KEYS = ("claude", "kiro")


@pytest.fixture(scope="module")
def doctor_after_install(tmp_path_factory):
    home = seed_fake_home(tmp_path_factory.mktemp("home"))
    install = run_cli_subprocess(["install", "--all"], home)
    assert install.returncode == 0, install.stderr
    init = run_cli_subprocess(["init", str(home / "wiki")], home)
    assert init.returncode == 0, init.stderr
    result = run_cli_subprocess(["doctor", "--no-net"], home)
    return result


def test_doctor_green_after_install_with_seeded_wiki(doctor_after_install):
    assert doctor_after_install.returncode == 0, doctor_after_install.stderr
    assert "doctor: OK" in doctor_after_install.stdout


@pytest.mark.parametrize("key", _HUB_READER_KEYS)
def test_doctor_reports_served_by_hub_for_hub_readers(doctor_after_install, key):
    parts = doctor_after_install.stdout.split(f"{key}: detected", 1)
    assert len(parts) == 2, f"{key} section absent"
    block = parts[1].split("\n\n", 1)[0]
    assert "served by hub" in block


@pytest.mark.parametrize("key", _ALIAS_KEYS)
def test_doctor_reports_symlink_ok_aliases_for_non_hub_harnesses(
    doctor_after_install, key
):
    assert f"{key}: detected" in doctor_after_install.stdout
    alias_section = doctor_after_install.stdout.split(f"{key}: detected", 1)[1]
    for skill in ALL_SKILL_NAMES:
        assert f"{skill:22s} -> " in alias_section


def test_doctor_reports_no_false_drift(doctor_after_install):
    out = doctor_after_install.stdout
    assert "hub incomplete" not in out
    assert "symlink-broken" not in out
    assert "symlink-external" not in out
    assert ": missing" not in out


@pytest.mark.parametrize("skill", ALL_SKILL_NAMES)
def test_doctor_flags_incomplete_hub_when_skill_deleted(tmp_path, skill):
    home = seed_fake_home(tmp_path / "home")
    assert run_cli_subprocess(["install", "--all"], home).returncode == 0
    assert run_cli_subprocess(["init", str(home / "wiki")], home).returncode == 0

    (home / ".agents" / "skills" / skill).unlink()

    result = run_cli_subprocess(["doctor", "--no-net"], home)
    assert f"{skill:22s} -> " in result.stdout
    assert "missing" in result.stdout
    assert "hub incomplete" in result.stdout
