"""Tests for `wiki-spaces manifest set/get/list`.

Phase 5 / B1. The framework reads `.manifest.json` but used to
require consumers (skills) to embed a 30-line fcntl+atomic-replace
snippet to write it. The CLI wraps that pattern; tests pin both
the happy paths (write → read round-trip, set creates entry, list
sorts) and the contract refusals (absent file without --create,
malformed JSON refusal, missing entry on get).
"""

from __future__ import annotations

import functools
import json

from wiki_spaces import manifest

from tests.conftest import make_wiki, run_cli

# Minimal valid wiki written in place (no `wiki/` subdir): `index.md` with a
# bare `## Spaces` directly under the given path.
_make_wiki = functools.partial(make_wiki, subdir=None, what_this_space=None, title="W")
_run = functools.partial(run_cli, entry=manifest.main)


# ---------- set ----------


def test_set_creates_entry_with_flag(tmp_path):
    """`set <entry> k=v --create` scaffolds .manifest.json on first use."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "fpf-worldcup-2026",
        "source_cwd=/home/me/src/fpf",
        "pages_in_vault=12",
        "--create",
    ])
    assert rc == 0
    doc = json.loads((wiki / ".manifest.json").read_text())
    entry = doc["projects"]["fpf-worldcup-2026"]
    assert entry["source_cwd"] == "/home/me/src/fpf"
    assert entry["pages_in_vault"] == 12          # JSON-coerced to int
    assert isinstance(entry["pages_in_vault"], int)


def test_set_refuses_without_create_when_absent(tmp_path):
    """Opt-in convention: absent .manifest.json + no --create = refusal."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki),
        "set", "fpf", "source_cwd=/path",
    ])
    assert rc == 2
    assert "--create" in err
    assert not (wiki / ".manifest.json").is_file()


def test_set_updates_existing_entry(tmp_path):
    """A second `set` to the same entry merges into the existing record."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "fpf", "source_cwd=/p", "--create",
    ])
    assert rc == 0
    rc2, _, _ = _run([
        "--wiki", str(wiki),
        "set", "fpf", "last_commit_synced=abc123",
    ])
    assert rc2 == 0
    doc = json.loads((wiki / ".manifest.json").read_text())
    entry = doc["projects"]["fpf"]
    assert entry["source_cwd"] == "/p"
    assert entry["last_commit_synced"] == "abc123"


def test_set_coerces_booleans_and_null(tmp_path):
    """JSON-coerce values when they parse: `true` → True, `null` → None."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "fpf",
        "last_commit_synced=null",
        "archived=true",
        "--create",
    ])
    assert rc == 0
    entry = json.loads((wiki / ".manifest.json").read_text())["projects"]["fpf"]
    assert entry["last_commit_synced"] is None
    assert entry["archived"] is True


def test_set_requires_at_least_one_pair(tmp_path):
    """`set <entry> --create` with no fields is a usage error."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki), "set", "fpf", "--create",
    ])
    assert rc == 2
    assert "key=value" in err


def test_set_rejects_malformed_pair(tmp_path):
    """Pairs without `=` are usage errors, not silent drops."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run([
        "--wiki", str(wiki),
        "set", "fpf", "not-a-pair", "--create",
    ])
    assert rc == 2
    assert "key=value" in err


def test_set_refuses_to_overwrite_malformed_json(tmp_path):
    """A pre-existing broken JSON is NOT silently overwritten — surface
    the error so the user repairs it deliberately."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text("{ broken not json }")
    rc, _, err = _run([
        "--wiki", str(wiki),
        "set", "fpf", "x=1",
    ])
    assert rc == 1
    assert "malformed" in err.lower()


def test_set_refuses_scalar_entry_without_crashing(tmp_path):
    """A hand-edited entry whose value is a scalar (not a mapping) is
    rejected as malformed — `set` returns a clean non-zero, NOT a raised
    AttributeError, and leaves the file untouched (refuse to overwrite)."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({"projects": {"p": 42}}))
    rc, _, err = _run([
        "--wiki", str(wiki),
        "set", "p", "x=1",
    ])
    assert rc == 1
    assert "malformed" in err.lower()
    # Refuse-to-overwrite: the on-disk file is unchanged.
    assert json.loads((wiki / ".manifest.json").read_text()) == {
        "projects": {"p": 42},
    }


def test_set_accepts_field_flag_alongside_positional(tmp_path):
    """`--field k=v` works alongside positional `k=v`."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "fpf", "source_cwd=/p",
        "--field", "pages_in_vault=12",
        "--create",
    ])
    assert rc == 0
    entry = json.loads((wiki / ".manifest.json").read_text())["projects"]["fpf"]
    assert entry["source_cwd"] == "/p"
    assert entry["pages_in_vault"] == 12


# ---------- list ----------


def test_list_prints_sorted_ids(tmp_path):
    """`list` shows every entry ID, one per line, sorted."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({
        "projects": {
            "zeta": {"source_cwd": "/z"},
            "alpha": {"source_cwd": "/a"},
            "mu": {"source_cwd": "/m"},
        },
    }))
    rc, out, _ = _run(["--wiki", str(wiki), "list"])
    assert rc == 0
    assert out.strip().splitlines() == ["alpha", "mu", "zeta"]


def test_list_json_emits_array(tmp_path):
    """`list --json` emits a JSON array of sorted entry IDs."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({
        "projects": {"b": {}, "a": {}},
    }))
    rc, out, _ = _run(["--wiki", str(wiki), "list", "--json"])
    assert rc == 0
    assert json.loads(out) == ["a", "b"]


def test_list_refuses_when_absent(tmp_path):
    """Same opt-in contract as `set` — absent file is a refusal."""
    wiki = _make_wiki(tmp_path)
    rc, _, err = _run(["--wiki", str(wiki), "list"])
    assert rc == 2
    assert "absent" in err.lower() or "manifest" in err.lower()


# ---------- get ----------


def test_get_prints_entry_as_json(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({
        "projects": {
            "fpf": {"source_cwd": "/p", "pages_in_vault": 7},
        },
    }))
    rc, out, _ = _run(["--wiki", str(wiki), "get", "fpf"])
    assert rc == 0
    payload = json.loads(out)
    assert payload == {"source_cwd": "/p", "pages_in_vault": 7}


def test_get_refuses_scalar_entry_manifest(tmp_path):
    """The read path also treats a non-mapping entry as malformed
    (producer=consumer: the same validator that blocks the write blocks
    the read)."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({"projects": {"p": 42}}))
    rc, _, err = _run(["--wiki", str(wiki), "get", "p"])
    assert rc == 1
    assert "malformed" in err.lower()


def test_get_unknown_entry(tmp_path):
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({"projects": {}}))
    rc, _, err = _run(["--wiki", str(wiki), "get", "nope"])
    assert rc == 1
    assert "not found" in err.lower()


# ---------- argparse position ----------


def test_wiki_flag_accepted_after_subcommand(tmp_path):
    """B2 symmetry — `--wiki` works after `list` too."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(json.dumps({"projects": {}}))
    rc, _, _ = _run(["list", "--wiki", str(wiki)])
    assert rc == 0


# ---------- non-finite JSON constants (NaN / Infinity) ----------


def test_set_keeps_nonfinite_tokens_as_strings(tmp_path):
    """`NaN`/`Infinity`/`-Infinity` are not portable JSON. `set ratio=NaN`
    stores the string "NaN" (coercion declines the non-finite constants) so
    the written file stays parseable by a strict (non-Python) JSON reader."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "p", "ratio=NaN", "limit=Infinity", "neg=-Infinity",
        "--create",
    ])
    assert rc == 0
    raw = (wiki / ".manifest.json").read_text()

    def _strict(token):
        raise ValueError(f"non-finite {token!r}")

    # Would raise if a bare NaN/Infinity were written to the file.
    doc = json.loads(raw, parse_constant=_strict)
    assert doc["projects"]["p"] == {
        "ratio": "NaN", "limit": "Infinity", "neg": "-Infinity",
    }


def test_read_rejects_file_with_nonfinite_constants(tmp_path):
    """A hand-edited manifest containing a bare NaN is treated as malformed:
    get/list refuse, and set refuses to overwrite rather than round-tripping
    the non-portable constant back out."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text('{"projects": {"p": {"r": NaN}}}\n')
    rc, _, err = _run(["--wiki", str(wiki), "get", "p"])
    assert rc == 1
    assert "malformed" in err.lower()
    rc2, _, err2 = _run(["--wiki", str(wiki), "set", "p", "x=1"])
    assert rc2 == 1
    assert "malformed" in err2.lower()


def test_set_keeps_numeric_overflow_as_string(tmp_path):
    """A numeric literal that overflows to ±inf (`1e999`) is non-finite but
    parses as a float WITHOUT tripping `parse_constant` (which only fires for
    the bareword tokens). Coercion must still decline it — `set ratio=1e999`
    stores the string "1e999" like the `NaN` token, keeping the file portable —
    while a genuine finite float is preserved as a number."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "p", "ratio=1e999", "neg=-1e999", "finite=3.14",
        "--create",
    ])
    assert rc == 0
    raw = (wiki / ".manifest.json").read_text()

    def _strict(token):
        raise ValueError(f"non-finite {token!r}")

    doc = json.loads(raw, parse_constant=_strict)
    assert doc["projects"]["p"]["ratio"] == "1e999"
    assert doc["projects"]["p"]["neg"] == "-1e999"
    assert doc["projects"]["p"]["finite"] == 3.14


def test_set_keeps_nested_numeric_overflow_as_string(tmp_path):
    """The non-finite guard must recurse. `nested={"x":1e999}` parses as
    a dict containing `float('inf')`; coercion should decline the whole value
    and store the raw string instead of letting `json.dumps(allow_nan=False)`
    fail after the update was accepted."""
    wiki = _make_wiki(tmp_path)
    rc, _, _ = _run([
        "--wiki", str(wiki),
        "set", "p", "nested={\"x\":1e999}", "--create",
    ])
    assert rc == 0
    doc = json.loads((wiki / ".manifest.json").read_text())
    assert doc["projects"]["p"]["nested"] == '{"x":1e999}'


def test_read_rejects_file_with_numeric_overflow_nonfinite(tmp_path):
    """A hand-edited manifest whose numeric literal overflows to inf (`1e999`)
    is treated as malformed: get refuses rather than emitting `Infinity`
    (non-portable) back out. Mirrors the bare-`NaN` rejection for the overflow
    form that slips past the token-only `parse_constant` guard."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text('{"projects": {"p": {"r": 1e999}}}\n')
    rc, _, err = _run(["--wiki", str(wiki), "get", "p"])
    assert rc == 1
    assert "malformed" in err.lower()


def test_read_rejects_file_with_nested_numeric_overflow_nonfinite(tmp_path):
    """A nested overflow non-finite is still non-portable JSON and must make
    the whole manifest unreadable until repaired."""
    wiki = _make_wiki(tmp_path)
    (wiki / ".manifest.json").write_text(
        '{"projects": {"p": {"r": [1, {"x": -1e999}]}}}\n'
    )
    rc, _, err = _run(["--wiki", str(wiki), "get", "p"])
    assert rc == 1
    assert "malformed" in err.lower()


# ---------- atomic-write durability ----------


def test_set_fsyncs_parent_directory(tmp_path, monkeypatch):
    """The rename is made durable by an fsync on the parent DIRECTORY, not
    just the temp file. Regression guard for the atomic-replace pattern:
    fsync of the temp file alone leaves the directory entry swap unflushed."""
    import os
    import stat as _stat
    wiki = _make_wiki(tmp_path)
    fsynced_dirs: list[int] = []
    real_fsync = os.fsync

    def _spy(fd):
        try:
            if _stat.S_ISDIR(os.fstat(fd).st_mode):
                fsynced_dirs.append(fd)
        except OSError:
            pass
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy)
    rc, _, _ = _run(["--wiki", str(wiki), "set", "p", "x=1", "--create"])
    assert rc == 0
    assert fsynced_dirs, "expected a parent-directory fsync after os.replace"
