"""Tests for `doctor` exit-code behavior.

`doctor` is the documented verify step (README, references/SETUP.md). It must
exit non-zero when the config is missing or invalid so setup scripts can gate
on it — these tests pin that contract.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

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
