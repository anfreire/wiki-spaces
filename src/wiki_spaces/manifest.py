"""`wiki-spaces manifest` subcommand: read/write `.manifest.json`.

`.manifest.json` was the only opt-in
state file the framework READ but didn't provide a writer for —
consumers (skills) had to embed a 30-line `fcntl.flock` +
tempfile + os.replace snippet from CONVENTIONS.md to update it
safely. The CLI now wraps that pattern in three subcommands so
callers stop carrying parallel implementations.

Vocabulary is deliberately generic (`<entry-id>`, not `<project>`):
the documented schema today is one `projects` map, but the
framework's spec is use-case agnostic and the CLI shouldn't
hardcode domain terminology. Internally the CLI maps entry IDs
to the `projects` namespace (the only top-level map in the v1
schema); if the schema grows additional namespaces a future
`--namespace` flag drops in cleanly.

Subcommands:
  manifest list                          List all entry IDs (sorted).
  manifest get <entry-id>                Print one entry as JSON.
  manifest set <entry-id> key=value ...  Update or create an entry.

`.manifest.json` is opt-in per CONVENTIONS: when the file is
absent every subcommand refuses (no auto-scaffold). `set` requires
`--create` to scaffold an empty file on first use; without the
flag, absent-file is a hard error so the user makes the opt-in
gesture explicit.

Concurrency: `set` holds an `fcntl.flock` on the parent directory's
inode (stable across `os.replace` swaps; the file inode itself
changes). Reads are best-effort consistent (no lock).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from . import _common
from ._common import fcntl


_TOP_LEVEL_MAP = "projects"     # the only namespace in the v1 schema


def _reject_json_constant(token: str) -> NoReturn:
    """`parse_constant` hook that refuses the non-finite JSON extensions.

    Python's `json` accepts `NaN`, `Infinity`, and `-Infinity` by default —
    on both read and coerce — and `json.dumps` writes them back verbatim.
    Those tokens are not portable JSON (RFC 8259 / most non-Python parsers
    reject them), so `.manifest.json` must never carry them. Raising here
    routes a non-finite token down each caller's existing failure path:
    `_coerce_value` keeps it as a plain string, `_read_manifest` treats the
    file as malformed-and-refuse-to-overwrite.

    `parse_constant` fires only for the bareword tokens, NOT for a numeric
    literal that *overflows* to ±inf (e.g. `1e999`) — Python parses that as a
    float directly. `_has_nonfinite` is the companion guard that catches the
    overflow form on both paths.
    """
    raise ValueError(f"non-finite JSON constant {token!r} is not portable")


def _has_nonfinite(obj: Any) -> bool:
    """True when `obj` (or anything nested) is a non-finite float (NaN/±inf).

    Catches what `_reject_json_constant` can't: a numeric literal like `1e999`
    parses straight to `float('inf')` without ever hitting the `parse_constant`
    hook. Recurse so a non-finite buried in a nested map/list is still caught.
    """
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, dict):
        return any(_has_nonfinite(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_nonfinite(v) for v in obj)
    return False


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """Return the parsed manifest as a dict, or None on parse / schema
    failure.

    A top-level non-mapping (`[]`, `"string"`, `42`) is treated the same
    as malformed JSON: refuse rather than expose `.get` / `in` to a
    non-dict and crash downstream. Schema-invalid `projects` (non-map
    when present) is also rejected here so list/get/set all see a clean
    contract by the time they read.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        doc = json.loads(raw or b"{}", parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        return None
    ok, _err = _validate_manifest(doc)
    if not ok:
        return None
    # Reject numeric-overflow non-finites (`1e999` -> inf) that slip past the
    # token-only `parse_constant` guard, so a hand-edited file carrying one is
    # refused on read rather than round-tripped back out as `Infinity`.
    if _has_nonfinite(doc):
        return None
    return doc


def _validate_manifest(doc: Any) -> tuple[bool, str | None]:
    """Schema sanity: top-level must be a dict with a `projects` map, and
    each entry under that map must itself be a mapping."""
    if not isinstance(doc, dict):
        return False, "top-level JSON must be a mapping"
    namespace = doc.get(_TOP_LEVEL_MAP)
    if _TOP_LEVEL_MAP in doc and not isinstance(namespace, dict):
        return False, f"`{_TOP_LEVEL_MAP}` must be a mapping"
    if isinstance(namespace, dict):
        # Every entry value must itself be a mapping: the v1 schema is
        # `{"<id>": {<fields>}}` and `set` does `entry.update(...)`. A scalar
        # entry (hand-edited file) would crash that update; reject as malformed
        # here so read AND pre-write both refuse-and-report instead.
        for entry_id, entry in namespace.items():
            if not isinstance(entry, dict):
                return False, f"entry {entry_id!r} must be a mapping"
    return True, None


def _coerce_value(raw: str) -> Any:
    """Cheap type coercion for `key=value` strings.

    Numbers (int/float), booleans, null, and quoted strings are parsed
    via `json.loads` first; on failure the raw string is kept verbatim.
    This lets `pages_in_vault=12` land as int(12) without the caller
    having to wrap-quote everything.

    Non-finite values are NOT coerced to floats — they are not portable JSON.
    The bareword tokens (`NaN`, `Infinity`, `-Infinity`) route through
    `_reject_json_constant`; a numeric literal that overflows to ±inf (e.g.
    `1e999`) parses without hitting that hook, so `_has_nonfinite` catches it.
    Either way the raw string is kept verbatim (`ratio=NaN` and `ratio=1e999`
    both land as their string form, which round-trips portably) instead of a
    float the writer's `allow_nan=False` would later reject.
    """
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        return raw
    if _has_nonfinite(value):
        return raw
    return value


def _atomic_write_under_flock(
    manifest_path: Path,
    update: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    """Lock parent dir, read fresh, apply `update`, write atomically.

    `update` is called with the current document (always a dict) and
    must return the new document. The lock guarantees concurrent
    callers serialize without losing writes; the atomic replace
    guarantees crash safety.

    Lock target is the parent directory's inode (not the file's). The
    file's inode changes under `os.replace`; the directory inode is
    stable across the swap and is what serializes correctly.
    """
    dir_fd = os.open(str(manifest_path.parent), os.O_RDONLY)
    try:
        if fcntl is not None:
            fcntl.flock(dir_fd, fcntl.LOCK_EX)
        # Read fresh inside the lock — other writers may have committed.
        if manifest_path.is_file():
            doc = _read_manifest(manifest_path)
            if doc is None:
                raise ValueError(
                    f"{manifest_path} is malformed or has invalid "
                    "schema; refuse to overwrite"
                )
        else:
            doc = {}
        if _TOP_LEVEL_MAP not in doc:
            doc[_TOP_LEVEL_MAP] = {}
        new_doc = update(doc)
        ok, err = _validate_manifest(new_doc)
        if not ok:
            raise ValueError(err)
        # allow_nan=False so a non-finite float that slipped past the coerce
        # guard (e.g. computed by a future caller) fails loudly here rather
        # than writing non-portable `NaN`/`Infinity` into the file.
        new_text = json.dumps(
            new_doc, indent=2, sort_keys=True, allow_nan=False
        ) + "\n"
        # One durable-write primitive (temp + fsync + replace + parent-dir
        # fsync), reusing the lock fd already open on the parent directory.
        _common.durable_replace(manifest_path, new_text, parent_fd=dir_fd)
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(dir_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(dir_fd)


def cmd_list(args: argparse.Namespace) -> int:
    """Print every entry ID, sorted, one per line.

    With `--json`, prints `[ "id1", "id2", ... ]`. Refuses when
    `.manifest.json` is absent (opt-in convention).
    """
    wiki, _err = _common.resolve_wiki(args.wiki, repair=False)
    if wiki is None:
        print(_err, file=sys.stderr)
        return 2
    path = wiki / ".manifest.json"
    if not path.is_file():
        print(
            "  ! .manifest.json absent (opt-in convention). "
            "Run `manifest set <entry> --create` to scaffold.",
            file=sys.stderr,
        )
        return 2
    doc = _read_manifest(path)
    if doc is None:
        print(
            f"  ! {path} is malformed or has invalid schema",
            file=sys.stderr,
        )
        return 1
    entries = sorted((doc.get(_TOP_LEVEL_MAP) or {}).keys())
    if args.json:
        print(json.dumps(entries, indent=2))
    else:
        for e in entries:
            print(e)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    """Print one entry as pretty-printed JSON.

    Refuses on absent file or missing entry; both flip exit code.
    """
    wiki, _err = _common.resolve_wiki(args.wiki, repair=False)
    if wiki is None:
        print(_err, file=sys.stderr)
        return 2
    path = wiki / ".manifest.json"
    if not path.is_file():
        print(
            "  ! .manifest.json absent (opt-in convention)",
            file=sys.stderr,
        )
        return 2
    doc = _read_manifest(path)
    if doc is None:
        print(
            f"  ! {path} is malformed or has invalid schema",
            file=sys.stderr,
        )
        return 1
    entries = doc.get(_TOP_LEVEL_MAP) or {}
    if args.entry_id not in entries:
        print(
            f"  ! entry {args.entry_id!r} not found in .manifest.json",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(entries[args.entry_id], indent=2, sort_keys=True))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """Update or create an entry with one or more `key=value` pairs.

    Values are JSON-coerced when possible (`12` → int, `true` → bool,
    `null` → None, `"foo"` → str) so common typed fields don't need
    wrap-quoting. To force a string that looks numeric, pre-quote:
    `key='"42"'`.

    `--create` scaffolds an empty `.manifest.json` on first use so the
    user makes the opt-in gesture explicit; without it, absent-file is
    a hard error.
    """
    wiki, _err = _common.resolve_wiki(args.wiki, repair=False)
    if wiki is None:
        print(_err, file=sys.stderr)
        return 2
    path = wiki / ".manifest.json"
    if not path.is_file() and not args.create:
        print(
            "  ! .manifest.json absent. Pass --create to scaffold "
            "on first use (the convention is opt-in; this is the "
            "explicit opt-in gesture).",
            file=sys.stderr,
        )
        return 2
    pairs = list(args.fields or []) + list(args.field or [])
    if not pairs:
        print(
            "  ! pass at least one key=value (positional or --field)",
            file=sys.stderr,
        )
        return 2
    parsed: dict[str, Any] = {}
    for raw in pairs:
        if "=" not in raw:
            print(
                f"  ! expected key=value, got {raw!r}",
                file=sys.stderr,
            )
            return 2
        key, value = raw.split("=", 1)
        if not key.strip():
            print(f"  ! empty key in {raw!r}", file=sys.stderr)
            return 2
        parsed[key.strip()] = _coerce_value(value)

    def _update(doc: dict[str, Any]) -> dict[str, Any]:
        entry = doc[_TOP_LEVEL_MAP].setdefault(args.entry_id, {})
        entry.update(parsed)
        return doc

    try:
        _atomic_write_under_flock(path, _update)
    except ValueError as e:
        print(f"  ! {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wiki-spaces manifest",
        description="Read/write `.manifest.json` entries.",
    )
    parser.add_argument(
        "--wiki",
        type=Path,
        help="explicit wiki root (defaults to the configured wiki)",
    )
    sub = parser.add_subparsers(dest="op", required=True)

    p_list = sub.add_parser(
        "list", help="list every entry ID (sorted)",
    )
    p_list.add_argument(
        "--json", action="store_true",
        help="emit a JSON array instead of one ID per line",
    )
    p_list.set_defaults(func=cmd_list)

    p_get = sub.add_parser(
        "get", help="print one entry as pretty-printed JSON",
    )
    p_get.add_argument("entry_id", help="entry identifier")
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser(
        "set", help="update or create an entry with key=value pairs",
    )
    p_set.add_argument("entry_id", help="entry identifier")
    p_set.add_argument(
        "fields", nargs="*", metavar="KEY=VALUE",
        help="positional key=value pairs (values are JSON-coerced)",
    )
    p_set.add_argument(
        "--field", action="append", metavar="KEY=VALUE",
        help="repeatable key=value (alternative to positional args)",
    )
    p_set.add_argument(
        "--create", action="store_true",
        help="scaffold an empty .manifest.json on first use",
    )
    p_set.set_defaults(func=cmd_set)

    args = parser.parse_args(_common.normalize_wiki_flag(argv))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
