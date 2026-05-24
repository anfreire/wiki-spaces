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
    repo. Parametrized so a new sentinel (e.g., the wiki-update and wiki-tend
    skill files we just added) is automatically covered."""
    root = _fake_install(tmp_path)
    (root / sentinel).unlink()
    state = doctor._validate_repo(str(root))
    assert "NOT A WIKI-SPACES INSTALL" in state
    assert sentinel in state


def test_repo_sentinels_includes_all_three_wiki_skills():
    """Codex blocker: doctor previously only checked wiki-search/SKILL.md, so
    a missing wiki-update or wiki-tend would slip through. This pins the fix."""
    assert "skills/wiki-search/SKILL.md" in doctor.REPO_SENTINELS
    assert "skills/wiki-update/SKILL.md" in doctor.REPO_SENTINELS
    assert "skills/wiki-tend/SKILL.md" in doctor.REPO_SENTINELS


def test_repo_sentinels_includes_both_kepano_skills():
    """AGENTS.md names both kepano skills as the syntax reference, so doctor
    must require both to consider the install valid."""
    assert "vendor/kepano/obsidian-markdown/SKILL.md" in doctor.REPO_SENTINELS
    assert "vendor/kepano/obsidian-bases/SKILL.md" in doctor.REPO_SENTINELS
