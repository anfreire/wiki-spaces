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


def _run_main(monkeypatch, cfg, *, wiki_state="OK", repo_state="OK"):
    """Run doctor.main with config + validators stubbed; vendor/harness no-op'd."""
    monkeypatch.setattr(doctor, "read_config", lambda: cfg)
    monkeypatch.setattr(doctor, "_validate_wiki", lambda w: wiki_state)
    monkeypatch.setattr(doctor, "_validate_repo", lambda r: repo_state)
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
        monkeypatch, {"wiki": "/x", "repo": "/y"}, wiki_state="MISSING ON DISK"
    )
    assert rc == 1


def test_exits_nonzero_when_repo_invalid(monkeypatch):
    rc, _ = _run_main(
        monkeypatch, {"wiki": "/x", "repo": "/y"}, repo_state="NOT ABSOLUTE"
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
    monkeypatch.setattr(doctor, "read_config", lambda: {"wiki": "/x", "repo": "/y"})
    monkeypatch.setattr(doctor, "_validate_wiki", lambda w: "OK")
    monkeypatch.setattr(doctor, "_validate_repo", lambda r: "OK")
    with redirect_stdout(io.StringIO()):
        assert doctor.check_config() is True
    monkeypatch.setattr(doctor, "_validate_repo", lambda r: "MISSING ON DISK")
    with redirect_stdout(io.StringIO()):
        assert doctor.check_config() is False


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
    assert doctor._validate_repo(str(root)) == "OK"


@pytest.mark.parametrize("sentinel", list(doctor.REPO_SENTINELS))
def test_validate_repo_flags_any_missing_sentinel(tmp_path, sentinel):
    """Each sentinel is individually load-bearing. Missing any one ⇒ invalid
    repo. Parametrized so a new sentinel (e.g., the ws-update and ws-tend
    skill files we just added) is automatically covered."""
    root = _fake_install(tmp_path)
    (root / sentinel).unlink()
    state = doctor._validate_repo(str(root))
    assert "NOT A WIKI-SPACES INSTALL" in state
    assert sentinel in state


def test_repo_sentinels_includes_all_three_wiki_skills():
    """Codex blocker: doctor previously only checked ws-search/SKILL.md, so
    a missing ws-update or ws-tend would slip through. This pins the fix."""
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
    returns a non-OK state. v1 contract: a wiki needs `## Spaces`."""
    (tmp_path / "index.md").write_text("# bare\n")  # no `## Spaces`
    state = doctor._validate_wiki(str(tmp_path))
    assert state != "OK"
    assert "Spaces" in state


def test_doctor_validate_wiki_ok_with_spaces_section(tmp_path):
    (tmp_path / "index.md").write_text("# wiki\n\n## Spaces\n\n")
    assert doctor._validate_wiki(str(tmp_path)) == "OK"


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
    state = doctor._validate_repo(str(root))
    assert "NOT A WIKI-SPACES INSTALL" in state
    assert "vendor/kepano/COMMIT" in state


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
