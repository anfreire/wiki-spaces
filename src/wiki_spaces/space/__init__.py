"""`wiki-spaces space` subcommands: add, remove, mount, promote, audit, list, files, log, check-size.

Maintains the `## Spaces` exhaustiveness contract automatically so users
never edit ancestor `index.md` files by hand to track child spaces.

Operations:
- `space add <rel-path>`     create a new space and register it in the
                             nearest ancestor's `## Spaces`. Auto-inserts
                             `## Spaces` into any bare-`index.md` ancestor
                             it encounters as the first mutation step
                             (chain helper, atomic under `flock`).
- `space remove <rel-path>`  remove a registered space and its directory.
                             Refuses without `--force` when the space
                             contains content beyond `index.md`. Inserts
                             `## Spaces` into the ancestor if missing
                             before removing the entry, so the contract
                             stays consistent.
- `space mount <src> [path]` mount an external space — git clone, git
                             submodule, or symlink (`--mode`) — verify it is a
                             wiki-spaces space (`index.md` with `## Spaces`),
                             and register it in the nearest ancestor's
                             `## Spaces`. Same chain-helper
                             auto-insert as `space add`. `path` is optional;
                             defaults to `shared/<basename-of-source>/`. Use
                             `--dry-run` to preview; `--name` to override the
                             registered label.
- `space promote <path>`     turn `foo.md` into `foo/index.md` (a child
                             space), rewriting links across the wiki and
                             registering the new space in the ancestor's
                             `## Spaces` atomically under flock.
- `space audit`              walk owned spaces; report `## Spaces` drift,
                             broken `[[wikilinks]]`, size violations, and
                             orphan pages. Read-only — uses the strict
                             resolver and refuses on a bare-`index.md`
                             wiki (no `## Spaces`).

Trust scope: writes stay inside the wiki tree. External spaces (per the
heuristic in CONVENTIONS.md / Owned vs external) are skipped on traversal.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from .._common import normalize_wiki_flag
from . import _core, promote
from .add import cmd_add
from .audit import PROMOTE_MIN_HUB_PAGES, PROMOTE_MIN_SPLIT_H2, AdoptResult, adopt_tree, cmd_audit
from .log import cmd_log
from .mount import cmd_mount
from .promote import _find_alias_owners, cmd_promote
from .query import cmd_caps, cmd_check_size, cmd_files, cmd_list
from .remove import cmd_remove
from ._core import (
    ChainExternalRefusal,
    SizeCapExceeded,
    _atomic_mutate_index,
    _derive_default_path,
    _ensure_section_at,
    _external_reason,
    _first_external_descendant,
    _first_foreign_submodule_ancestor,
    _is_in_external_scope,
    _preflight_chain_external,
    _SPACES_HREF_METACHARS,
    _validate_entry_text,
    _validate_rel_path,
    _walk_classified,
    enforce_size_cap,
)

__all__ = [
    "main",
    "adopt_tree",
    "AdoptResult",
    "enforce_size_cap",
    "SizeCapExceeded",
    "ChainExternalRefusal",
    "_atomic_mutate_index",
    "_derive_default_path",
    "_ensure_section_at",
    "_external_reason",
    "_find_alias_owners",
    "_first_external_descendant",
    "_first_foreign_submodule_ancestor",
    "_is_in_external_scope",
    "_preflight_chain_external",
    "_SPACES_HREF_METACHARS",
    "_validate_entry_text",
    "_validate_rel_path",
    "_walk_classified",
    "PROMOTE_MIN_HUB_PAGES",
    "PROMOTE_MIN_SPLIT_H2",
    "_core",
    "promote",
    "os",
    "shutil",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wiki-spaces space",
        description="Manage spaces and the ## Spaces navigation contract.",
    )
    parser.add_argument(
        "--wiki",
        type=Path,
        help="explicit wiki root (defaults to the configured wiki)",
    )
    sub = parser.add_subparsers(dest="op", required=True)

    p_add = sub.add_parser("add", help="create a space and register it with the nearest ancestor")
    p_add.add_argument("path", help="path relative to the wiki root (e.g. projects/acme)")
    p_add.add_argument("--name", help="display name (default: directory basename)")
    p_add.add_argument(
        "--description",
        help="navigation description for the new space — lands in BOTH "
        "the parent's `## Spaces` entry note and the child's "
        "`## What this space is` body section. Distinct from "
        "frontmatter `summary:` (use --summary for that, with "
        "--from-template).",
    )
    p_add.add_argument(
        "--summary",
        help="value for the frontmatter `summary:` field (per "
        "CONVENTIONS / Frontmatter schema). Only meaningful with "
        "--from-template — without a template there is no frontmatter "
        "to set. Substituted into the template's `{{ summary }}` "
        "placeholder.",
    )
    p_add.add_argument(
        "--force-index",
        action="store_true",
        help="overwrite an existing index.md at the target",
    )
    p_add.add_argument(
        "--force-external",
        action="store_true",
        help="permit operating on a space outside the owned tree "
        "(under shared/, a foreign-origin submodule, or a symlink "
        "that escapes the wiki). Default: refuse.",
    )
    p_add.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing. Includes the chain-helper "
        "preview (which ancestors would have `## Spaces` inserted and which "
        "would gain the new entry).",
    )
    p_add.add_argument(
        "--from-template",
        help="render the new space's index.md from a template file "
        "(wiki-root-relative or absolute). Placeholders `{{ title }}`, "
        "`{{ now }}`, `{{ description }}`, `{{ summary }}` are substituted. "
        "The default "
        "is a barren index — opt in explicitly when you want the new "
        "space to start with the parent's adopted conventions "
        "(frontmatter, opt-in fields). `## Spaces` is appended if "
        "the template doesn't include it.",
    )
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="delete a space and unregister it")
    p_remove.add_argument("path", help="path relative to the wiki root")
    p_remove.add_argument(
        "--force",
        action="store_true",
        help="remove even when the space contains files other than index.md",
    )
    p_remove.add_argument(
        "--force-external",
        action="store_true",
        help="permit operating on a space outside the owned tree "
        "(under shared/, a foreign-origin submodule, or a symlink "
        "that escapes the wiki). Default: refuse.",
    )
    p_remove.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing",
    )
    p_remove.set_defaults(func=cmd_remove)

    p_audit = sub.add_parser(
        "audit",
        help="report ## Spaces drift, broken wikilinks, size violations, and orphan pages",
    )
    p_audit.add_argument(
        "--include-external",
        action="store_true",
        help="also walk externally-classified spaces (under shared/, foreign "
        "submodules, escaping symlinks). Trust scope per AGENTS.md says read "
        "ops can opt in to externals; this flag is the opt-in. Plumbed "
        "through both the drift walker and the broken-link walker so the two "
        "checks always agree on scope.",
    )
    p_audit.add_argument(
        "--fix",
        action="store_true",
        help="repair drift in place: insert `## Spaces` into any owned space "
        "missing it, then register every on-disk child that's not yet listed "
        "in its ancestor's `## Spaces`. Touches the filesystem. Use without "
        "`--fix` to preview the same report read-only.",
    )
    p_audit.add_argument(
        "--remove-stale",
        action="store_true",
        help="with `--fix`: also remove `## Spaces` entries whose target "
        "doesn't exist on disk. Externally-classified entries require "
        "`--include-external` together with `--remove-stale` so legitimate "
        "external mounts are not silently unregistered.",
    )
    p_audit.add_argument(
        "--json",
        action="store_true",
        help="emit findings as JSON (drift, missing `## Spaces` section, "
        "broken wikilinks, size violations, approaching cap, orphans, "
        "malformed entries, duplicate aliases). Skills consume this "
        "rather than parsing the human-readable output.",
    )
    p_audit.set_defaults(func=cmd_audit)

    p_mount = sub.add_parser(
        "mount",
        help="mount an external space (clone/submodule/symlink) and register it",
    )
    p_mount.add_argument("source", help="git URL, or local path, of the space to mount")
    p_mount.add_argument(
        "path",
        nargs="?",
        default=None,
        help="destination path relative to the wiki root (e.g. shared/team-foo). "
        "Optional; default is shared/<basename-of-source>/ — the shared/ prefix "
        "opts the mount into external trust-scope semantics.",
    )
    p_mount.add_argument(
        "--mode",
        dest="mechanism",
        required=True,
        choices=("submodule", "clone", "symlink"),
        help="mount mechanism: submodule (collaborative, push changes back), "
        "clone (read-only one-time copy), symlink (local folder)",
    )
    p_mount.add_argument(
        "--description", help="one-line description for the `## Spaces` entry"
    )
    p_mount.add_argument(
        "--name",
        help="override the `## Spaces` entry label (default: <relative-path>/)",
    )
    p_mount.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan; touch nothing",
    )
    p_mount.set_defaults(func=cmd_mount)

    p_caps = sub.add_parser(
        "caps",
        help="list the effective size-cap rules at the wiki root, with sources",
    )
    p_caps.add_argument(
        "--json",
        action="store_true",
        help="emit structured JSON instead of human-readable text",
    )
    p_caps.set_defaults(func=cmd_caps)

    p_check_size = sub.add_parser(
        "check-size",
        help="print a size-cap verdict for a projected post-write text",
    )
    p_check_size.add_argument(
        "path",
        help="wiki-root-relative path of the target file (used to look up "
        "the per-pattern cap from `_meta/limits.md`).",
    )
    p_check_size.add_argument(
        "--projected-file",
        help="read the projected post-write content from this file.",
    )
    p_check_size.set_defaults(func=cmd_check_size)

    p_list = sub.add_parser(
        "list",
        help="list spaces reachable via the `## Spaces` contract",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        help="emit `{path, label, description, external}` per space.",
    )
    p_list.add_argument(
        "--include-external",
        action="store_true",
        help="include externally-classified spaces (under shared/, foreign "
        "submodules, escaping symlinks).",
    )
    p_list.add_argument(
        "--include-boundaries",
        action="store_true",
        help="also surface external boundary folders that lack `index.md` "
        "(foreign submodules without one, escaping symlinks). Requires "
        "--include-external. Used by the placement classifier to enumerate "
        "every external path to exclude.",
    )
    p_list.set_defaults(func=cmd_list)

    p_files = sub.add_parser(
        "files",
        help="list .md files reachable via the `## Spaces` contract",
    )
    p_files.add_argument(
        "space",
        nargs="?",
        help="wiki-root-relative space to scope to (must be contract-"
        "reachable). Omit to list every file in the wiki.",
    )
    p_files.add_argument(
        "--json",
        action="store_true",
        help="emit `{path, external}` per file.",
    )
    p_files.add_argument(
        "--include-external",
        action="store_true",
        help="include files inside externally-classified spaces.",
    )
    p_files.set_defaults(func=cmd_files)

    p_promote = sub.add_parser(
        "promote",
        help="promote a .md file to a nested space (foo.md -> foo/index.md)",
    )
    p_promote.add_argument(
        "path",
        help="wiki-root-relative path to a .md file (not index.md)",
    )
    p_promote.add_argument(
        "--skip-aliases",
        action="store_true",
        help="do not inject aliases: [<basename>] into the new index.md "
        "(escape hatch when another page already claims the alias)",
    )
    p_promote.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan + link-rewrite counts; touch nothing",
    )
    p_promote.set_defaults(func=cmd_promote)

    p_log = sub.add_parser(
        "log",
        help="append a structured line to <wiki>/log.md atomically",
    )
    p_log.add_argument(
        "operation",
        nargs="?",
        help="operation name (uppercased; e.g. SEARCH, UPDATE, TEND). "
        "Required unless --raw is passed.",
    )
    p_log.add_argument(
        "fields",
        nargs="*",
        metavar="KEY=VALUE",
        help="positional `key=value` pairs (alternative to repeated "
        "--field). Example: `space log SEARCH query=sourdough "
        "result_pages=3`.",
    )
    p_log.add_argument(
        "--field",
        action="append",
        metavar="KEY=VALUE",
        help="repeatable `key=value` pair appended to the entry. "
        "Equivalent to passing positionals.",
    )
    p_log.add_argument(
        "--raw",
        help="bypass structured formatting; append the given string verbatim "
        "as the entry (CLI does NOT prepend a timestamp).",
    )
    p_log.add_argument(
        "--create",
        action="store_true",
        help="scaffold an empty `log.md` if one does not exist yet. Without "
        "this flag an absent `log.md` is a refusal — logging is opt-in.",
    )
    p_log.set_defaults(func=cmd_log)

    args = parser.parse_args(normalize_wiki_flag(argv))
    return args.func(args)
