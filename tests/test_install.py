"""Unit tests for wiki_spaces.install (focused on the --bridge stdout flow)."""

from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr

import pytest

from wiki_spaces import install


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = install.main(args)
    return rc, out.getvalue(), err.getvalue()


def test_bridge_cursor_emits_exact_file_content():
    """`--bridge cursor` must emit the packaged bridge file byte-for-byte. The
    user pipes this into their rules file via shell redirection; any mutation
    here would silently corrupt the rule snippet."""
    from wiki_spaces._common import data_root
    expected = (data_root() / "bridges" / install.BRIDGES["cursor"]).read_text(encoding="utf-8")
    rc, out, _ = _run(["--bridge", "cursor"])
    assert rc == 0
    assert out == expected


def test_bridge_windsurf_emits_exact_file_content():
    from wiki_spaces._common import data_root
    expected = (data_root() / "bridges" / install.BRIDGES["windsurf"]).read_text(encoding="utf-8")
    rc, out, _ = _run(["--bridge", "windsurf"])
    assert rc == 0
    assert out == expected


def test_bridge_unknown_key_rejected_by_argparse():
    """Argparse `choices=` enforces the bridge key whitelist before main()
    runs — invalid keys terminate via SystemExit, not return code."""
    with pytest.raises(SystemExit):
        _run(["--bridge", "bogus"])


def test_bridge_short_circuits_install_writes(tmp_path, monkeypatch):
    """`--bridge` returns before harness install logic runs; it must not
    touch the config file or harness skill dirs. Combined with --dry-run
    or any install flag, --bridge wins."""
    from wiki_spaces import _common
    fake_config = tmp_path / "absent-config"
    monkeypatch.setattr(_common, "CONFIG_PATH", fake_config)
    rc, out, _ = _run(["--dry-run", "--bridge", "cursor"])
    assert rc == 0
    assert out.startswith("---")
    assert not fake_config.exists()
