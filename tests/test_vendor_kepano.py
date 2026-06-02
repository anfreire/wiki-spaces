"""Boundary-failure tests for the dev-only `vendor-kepano` command."""

from __future__ import annotations

import subprocess

from wiki_spaces import vendor_kepano

from tests.conftest import run_cli as _run


def test_vendor_kepano_reports_missing_git_without_traceback(tmp_path, monkeypatch):
    """`vendor-kepano` shells out to git; a missing git binary must surface as
    a clean stderr message + non-zero exit, not a raw `FileNotFoundError`
    traceback (HANDBOOK: handle failures at boundaries — subprocess)."""
    monkeypatch.setattr(vendor_kepano, "is_packaged", lambda: False)
    monkeypatch.setattr(vendor_kepano, "data_root", lambda: tmp_path)

    def _raise(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(vendor_kepano.subprocess, "run", _raise)
    rc, _out, err = _run([], entry=vendor_kepano.main)
    assert rc == 1
    assert "git" in err.lower()
    assert "Traceback" not in err


def test_vendor_kepano_reports_git_failure_without_traceback(tmp_path, monkeypatch):
    """A failing git invocation (non-zero exit) must also surface cleanly
    instead of escaping as a `CalledProcessError` traceback."""
    monkeypatch.setattr(vendor_kepano, "is_packaged", lambda: False)
    monkeypatch.setattr(vendor_kepano, "data_root", lambda: tmp_path)

    def _raise(*_a, **_k):
        raise subprocess.CalledProcessError(128, ["git", "clone"])

    monkeypatch.setattr(vendor_kepano.subprocess, "run", _raise)
    rc, _out, err = _run([], entry=vendor_kepano.main)
    assert rc == 1
    assert "git" in err.lower()
    assert "Traceback" not in err
