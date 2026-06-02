from __future__ import annotations

import argparse
import sys
from .. import _model
from .._common import has_control_chars, resolve_wiki
from ._core import (
    SizeCapExceeded,
    enforce_size_cap,
)

def cmd_log(args: argparse.Namespace) -> int:
    """Append a structured line to <wiki>/log.md atomically.

    Two forms:
    - Structured (preferred): `space log <OPERATION> --field key=value ...`
      auto-prepends an ISO-8601 UTC timestamp and emits the canonical
      `- [TIMESTAMP] OPERATION key=value ...` format. The LLM never
      formats timestamps by hand.
    - Raw escape hatch: `space log --raw "<full line>"` for callers
      writing a custom shape (skill prose still recommends the structured
      form).

    Opt-in. `log.md` must already exist; pass `--create` on the first
    call if you want the CLI to scaffold it. Without `--create`, an
    absent `log.md` is a refusal — `log.md` is one of the optional
    conventions per CONVENTIONS.md, not a default.

    Race-safe: a single `fcntl.flock` on the log file's PARENT DIRECTORY
    covers the whole check-rotate-append sequence
    (`_log.append_log_with_rotation`) — the parent inode is stable across the
    helper's own create / unlink / replace of `log.md`, which locking the
    `log.md` inode itself is not.
    """
    wiki_root, _err = resolve_wiki(args.wiki, repair=False)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2

    from .. import _log

    log_path = wiki_root / "log.md"
    create_if_missing = bool(getattr(args, "create", False))
    if not log_path.is_file() and not create_if_missing:
        print(
            f"  ! {log_path.relative_to(wiki_root)} does not exist. "
            "Pass --create to scaffold it, or opt in by running "
            "`wiki-spaces init <wiki> --with log.md`.",
            file=sys.stderr,
        )
        return 2
    if create_if_missing and not log_path.is_file():
        # Framework write — enforce the per-file cap. The initial body
        # is tiny (6 bytes) but the v1 contract is "every framework write
        # enforces the cap": a degenerate user-configured `log.md` cap
        # below 6 chars would otherwise leak an over-cap scaffold file
        # onto disk. The actual scaffold write happens INSIDE
        # `append_log_with_rotation`'s lock (race-safe across concurrent
        # first-time --create calls); this is the pre-flight only.
        try:
            enforce_size_cap(log_path, "# Log\n", wiki_root)
        except SizeCapExceeded as e:
            print(f"  ! size cap: {e}", file=sys.stderr)
            return 2

    if _model.symlink_escapes_wiki(log_path, wiki_root):
        print(
            f"  ! refusing to write {log_path.name}: it is a symlink whose "
            "target resolves outside the wiki tree. `atomic_write` would follow "
            "it and mutate content beyond the trust boundary. Replace the "
            "symlink with a regular file.",
            file=sys.stderr,
        )
        return 2

    if args.raw is not None:
        # --raw bypasses the structured `OP k=v k=v` builder. An
        # operation or k=v args alongside --raw have nowhere to land;
        # silently dropping them violates the "every input has a
        # destination" rule. Refuse loudly.
        extra = list(args.fields or []) + list(args.field or [])
        if extra or args.operation:
            print(
                "  ! --raw is mutually exclusive with OPERATION, positional "
                "`key=value` pairs, and `--field` — pick one mode.",
                file=sys.stderr,
            )
            return 2
        message = args.raw
    else:
        if not args.operation:
            print(
                "  ! pass an OPERATION (e.g. `space log SEARCH --field "
                "query=...`) or use --raw \"<line>\".",
                file=sys.stderr,
            )
            return 2

        if has_control_chars(args.operation):
            print(
                "  ! OPERATION may not contain newline / control characters "
                "— the structured `- [TIMESTAMP] OP ...` entry must fit on "
                "one line. Use --raw if you really need multi-line content.",
                file=sys.stderr,
            )
            return 2

        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = [f"- [{ts}]", args.operation.upper()]
        # Accept both `--field k=v` (repeatable) and positional
        # `k=v` arguments. The two interleave; positionals come after
        # the operation. Same validation either way.
        for kv in list(args.fields or []) + list(args.field or []):
            if "=" not in kv:
                print(
                    f"  ! expected key=value, got {kv!r}",
                    file=sys.stderr,
                )
                return 2
            k, v = kv.split("=", 1)
            if has_control_chars(k) or has_control_chars(v):
                print(
                    f"  ! {k}=... may not contain newline / control "
                    "characters — the structured entry must fit on one line. "
                    "Use --raw if you really need multi-line content.",
                    file=sys.stderr,
                )
                return 2
            parts.append(f"{k}={v}")
        message = " ".join(parts)

    table = _model.load_limit_table(wiki_root)
    cap = _model.cap_for_path(log_path, wiki_root, table).cap
    try:
        archive = _log.append_log_with_rotation(
            log_path,
            message,
            cap=cap,
            wiki_root=wiki_root,
            table=table,
            create_if_missing=create_if_missing,
        )
    except FileNotFoundError as e:
        # Race: log.md was deleted between our check above and the lock
        # acquisition inside the helper. Surface and bail.
        print(f"  ! {e}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        # A non-UTF-8 log.md is a boundary input the append refuses rather than
        # corrupt — report it and bail instead of crashing (HANDBOOK: handle
        # failures at boundaries). MUST precede `except ValueError`:
        # UnicodeDecodeError subclasses ValueError, so the generic clause would
        # otherwise shadow this one and print the raw codec message.
        print(
            f"  ! could not read {log_path.name}: it is not valid UTF-8. "
            "Repair or remove the file before logging.",
            file=sys.stderr,
        )
        return 1
    except ValueError as e:
        # Rotation could not free enough space for the entry.
        # Surface to the user; rotation/trim is their decision.
        print(f"  ! {e}", file=sys.stderr)
        return 1
    except OSError as e:
        # Any other filesystem failure during the locked read/rotate/append
        # (archive rollback already ran inside the helper). Surface with a
        # cause and exit code rather than dumping a traceback.
        print(f"  ! log write failed: {e}", file=sys.stderr)
        return 1
    if archive is not None:
        print(f"  ~ {log_path.relative_to(wiki_root)} rotated → {archive.name}")
    return 0
