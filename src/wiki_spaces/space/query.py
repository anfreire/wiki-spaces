from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from .. import _model
from .._common import resolve_wiki
from ._core import _format_cap_source, _rel_or_str, scoped_size_verdict

def cmd_caps(args: argparse.Namespace) -> int:
    """List the effective size caps at the wiki root.

    Built-in defaults (`index.md` 5K, `*.md` 15K, etc.) are otherwise
    discoverable only by hitting `OVER`. This command makes the active
    cap table visible without writing anything.

    Output: tab-separated `pattern\\tcap\\tsource` lines for shell use.
    With `--json`, structured `[{pattern, cap, kind, file, line}]` for
    machine consumers. Includes both user overrides from
    `_meta/limits.md` (first, in file order) and built-in defaults
    (after, in match order). Malformed rows in `_meta/limits.md` are
    surfaced with their line so the user can repair.
    """
    wiki_root, _err = resolve_wiki(args.wiki, repair=False)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2
    table = _model.load_limit_table(wiki_root)
    if getattr(args, "json", False):
        payload = {
            "rules": [
                {
                    "pattern": pattern,
                    "cap": cap,
                    "kind": source.kind.value,
                    "file": _rel_or_str(source.file, wiki_root),
                    "line": source.line + 1 if source.line is not None else None,
                }
                for pattern, cap, source in table.rules
            ],
            "malformed_rows": [
                {"line": line + 1, "raw": raw}
                for line, raw in table.malformed_rows
            ],
        }
        print(json.dumps(payload, indent=2))
        return 1 if table.malformed_rows else 0
    print(f"wiki: {wiki_root}")
    print()
    print("pattern\tcap\tsource")
    for pattern, cap, source in table.rules:
        print(f"{pattern}\t{cap}\t{_format_cap_source(source)}")
    if table.malformed_rows:
        print()
        print("malformed rows in _meta/limits.md:")
        for line, raw in table.malformed_rows:
            print(f"  ! line {line + 1}: {raw}")
        return 1
    return 0


def cmd_check_size(args: argparse.Namespace) -> int:
    """Print a size-cap verdict for a projected post-write text.

    Usage:
      cat new-content.md | wiki-spaces space check-size <rel-path>
      wiki-spaces space check-size <rel-path> --projected-file <text-file>

    Prints `OK <chars>/<cap>`, `OK-SHRINKING <chars>/<cap>`, or
    `OVER <chars>/<cap>` followed by the cap source. Exit 0 for OK
    and OK-SHRINKING (the shrinking-write hatch from legacy bloat);
    exit 1 for OVER.

    Reads the projected content from a pipe on stdin, or from
    `--projected-file`. On a TTY with nothing piped it refuses rather than
    returning a misleading `OK 0/<cap>` for empty input. Empty piped content
    is valid (e.g. a "zero this file" projection) and lands as `OK 0/<cap>`.
    `--projected-file <path>` is an alternative source for content that is
    not easy to pipe.
    """
    wiki_root, _err = resolve_wiki(args.wiki, repair=False)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2
    if Path(args.path).is_absolute() or ".." in Path(args.path).parts:
        print("  ! path must be wiki-root-relative", file=sys.stderr)
        return 2
    target = wiki_root / args.path
    if args.projected_file:
        try:
            projected = Path(args.projected_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(
                f"  ! could not read {args.projected_file}: {e}",
                file=sys.stderr,
            )
            return 2
    elif sys.stdin.isatty():
        print(
            "  ! pipe projected content into stdin, or pass "
            "--projected-file <path>",
            file=sys.stderr,
        )
        return 2
    else:
        projected = sys.stdin.read()
    verdict = scoped_size_verdict(target, projected, wiki_root)
    label = {
        _model.SizeOutcome.OK: "OK",
        _model.SizeOutcome.OK_SHRINKING: "OK-SHRINKING",
        _model.SizeOutcome.OVER: "OVER",
    }[verdict.outcome]
    print(
        f"{label} {verdict.chars_projected}/{verdict.cap.cap} "
        f"({_format_cap_source(verdict.cap.source)})"
    )
    return 1 if verdict.outcome == _model.SizeOutcome.OVER else 0




def cmd_list(args: argparse.Namespace) -> int:
    """List spaces reachable via the `## Spaces` contract.

    Default: tab-separated `path\\tclassification` lines for shell use.
    With `--json`: structured `{path, label, description, external}` per
    space (the root is excluded — placement classifiers want children
    only). With `--include-boundaries --include-external`: also surfaces
    external boundary folders without `index.md` (foreign submodules,
    escaping symlinks). The placement classifier in `ws-update` uses
    that combination to enumerate every external path to exclude.
    """
    wiki_root, _err = resolve_wiki(args.wiki, repair=False)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2

    if args.include_boundaries and not args.include_external:
        print(
            "  ! --include-boundaries requires --include-external",
            file=sys.stderr,
        )
        return 2

    # One model traversal carries path, external flag, AND the registering
    # entry's label/description — no second contract walk for the label map.
    consumer = _model.discover_consumer_spaces(
        wiki_root, include_external=args.include_external
    )
    # rows: (path, external, label, description) in discovery order, root first.
    rows: list[tuple[Path, bool, str | None, str | None]] = [
        (cs.path, cs.external, cs.label, cs.description) for cs in consumer
    ]
    if args.include_boundaries:
        # Surface external boundary folders that the contract walk missed
        # because they lack `index.md` + `## Spaces` (foreign submodules,
        # escaping symlinks). Emit the lexical path so callers can do
        # `.relative_to(wiki_root)` (an escaping symlink's resolved path is
        # outside the tree); label falls back to the derived `<rel>/`.
        nodes = _model.discover_nodes(wiki_root, include_external=True)
        for eb in _model.external_boundaries(nodes, consumer):
            rel = eb.path.relative_to(wiki_root).as_posix()
            rows.append((eb.path, True, f"{rel}/", None))

    if args.json:
        out: list[dict] = []
        for path, is_ext, label, description in rows:
            if path == wiki_root:
                continue  # placement classifiers want children only
            rel = path.relative_to(wiki_root).as_posix()
            out.append({
                "path": rel,
                "label": label,
                "description": description,
                "external": is_ext,
            })
        print(json.dumps(out, indent=2))
    else:
        for path, is_ext, _label, _description in rows:
            rel = "." if path == wiki_root else path.relative_to(wiki_root).as_posix()
            tag = "external" if is_ext else "owned"
            print(f"{rel}\t{tag}")
    return 0


def cmd_files(args: argparse.Namespace) -> int:
    """List `.md` files reachable via the `## Spaces` contract.

    Walks owned scope by default; `--include-external` opts external
    subtrees in. With a `space` argument, scopes output to files under
    that wiki-root-relative space (which must be contract-reachable —
    unregistered subspaces are refused with a hint to run `space audit`).

    The walker runs from `wiki_root` even when a scope is given; output
    is filtered lexically afterward so an opted-in external symlink
    whose resolved path leaves the tree is still included.
    """
    wiki_root, _err = resolve_wiki(args.wiki, repair=False)
    if wiki_root is None:
        print(_err, file=sys.stderr)
        return 2

    scope_root: Path | None = None
    if args.space:
        if Path(args.space).is_absolute() or ".." in Path(args.space).parts:
            print("  ! space must be wiki-root-relative", file=sys.stderr)
            return 2
        scope_root = wiki_root / args.space
        if not scope_root.is_dir():
            print(f"  ! {args.space}: not a directory", file=sys.stderr)
            return 2
        # Contract-reachable check: refuse on unregistered scopes. Compare
        # LEXICAL paths, not resolved ones — a user-made symlink alias
        # (`notes-alias/` -> registered `notes/`) resolves to the same path
        # as the registered space, but its lexical path doesn't appear in
        # the contract walker's output. Without the lexical check, an alias
        # would "pass" reachability and then lexical filtering would return
        # an empty list (because the walker yielded the canonical path).
        # The consumer-side contract is exhaustive: only paths the contract
        # walker actually emitted are valid scopes.
        #
        # Naming an external scope explicitly opts the consumer in per
        # AGENTS.md / trust scope: "External spaces are visited only when
        # the user explicitly names one or asks to include all." The
        # opt-in is THE NAMED SCOPE specifically — not "any non-root
        # scope". So:
        #   - Named scope is external (under shared/, foreign submodule,
        #     escaping symlink, or any ancestor thereof): opt-in.
        #   - Named scope is owned (e.g. `projects/foo`): default — owned
        #     external descendants under projects/foo are NOT surfaced
        #     unless the global --include-external flag is set.
        #   - Named scope is the wiki root: default; honor the global flag.
        scope_is_external = (
            _model.classify_external_scope(scope_root, wiki_root).scope
            == _model.TrustScope.EXTERNAL
        )
        scope_include_external = (
            scope_is_external or args.include_external
        )
        reachable = {
            cs.path
            for cs in _model.discover_consumer_spaces(
                wiki_root, include_external=scope_include_external
            )
        }
        if scope_root not in reachable:
            print(
                f"  ! {args.space}: not reachable via parent `## Spaces`; "
                "unregistered spaces are not consumer-visible. Run "
                "`wiki-spaces space audit` to surface drift.",
                file=sys.stderr,
            )
            return 2

    # Traverse with the same external-opt-in semantics: only when the
    # NAMED scope is itself external (or the global flag is on). For an
    # owned named scope, default behavior — external descendants stay
    # hidden.
    if scope_root is not None:
        scope_is_external = (
            _model.classify_external_scope(scope_root, wiki_root).scope
            == _model.TrustScope.EXTERNAL
        )
        traverse_external = scope_is_external or args.include_external
    else:
        traverse_external = args.include_external
    all_files = [
        (cf.path, cf.external)
        for cf in _model.discover_md_files(
            wiki_root, include_external=traverse_external
        )
    ]
    if scope_root is None or scope_root == wiki_root:
        files = all_files
    else:
        # Filter lexically — external symlinks live lexically under
        # wiki_root even when their resolved path doesn't.
        scope_str = str(scope_root) + os.sep
        files = [
            (f, is_ext)
            for f, is_ext in all_files
            if str(f).startswith(scope_str) or f == scope_root
        ]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "path": f.relative_to(wiki_root).as_posix(),
                        "external": is_ext,
                    }
                    for f, is_ext in files
                ],
                indent=2,
            )
        )
    else:
        for f, is_ext in files:
            tag = "\texternal" if is_ext else ""
            print(f"{f.relative_to(wiki_root)}{tag}")
    return 0
