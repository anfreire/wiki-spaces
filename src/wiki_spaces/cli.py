"""wiki-spaces console entry point.

Dispatches to a subcommand module's `main()`:

  wiki-spaces install [--all] [--dry-run] [--copy] [--harness <key>]
  wiki-spaces init <path> [--with ...] [--folders ...] [--git] [--name <n>]
  wiki-spaces doctor [--no-net]
  wiki-spaces update [flags forwarded to install]
  wiki-spaces vendor-kepano [--ref <git-ref>]    # dev only

Each subcommand owns its own argparse — pass --help to any subcommand for
its flag list. Modules are also importable directly:
  python -m wiki_spaces.install
  python -m wiki_spaces.init_wiki
  python -m wiki_spaces.doctor
"""

from __future__ import annotations

import importlib
import sys

COMMANDS: dict[str, str] = {
    "install": "wiki_spaces.install",
    "init": "wiki_spaces.init_wiki",
    "doctor": "wiki_spaces.doctor",
    "update": "wiki_spaces.update",
    "space": "wiki_spaces.space",
    "vendor-kepano": "wiki_spaces.vendor_kepano",
}


def _help_text() -> str:
    return (
        "usage: wiki-spaces <command> [args...]\n"
        "\n"
        "Commands:\n"
        "  install         install skills into detected harnesses; set repo path\n"
        "  init            scaffold a new wiki and register it as canonical\n"
        "  doctor          audit config + harness installs + vendored kepano\n"
        "  update          re-vendor (dev) + re-run install\n"
        "  space           add/remove/audit spaces; maintains ## Spaces contract\n"
        "  vendor-kepano   re-vendor kepano upstream (dev only)\n"
        "\n"
        "Pass --help to any subcommand for its flags."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_help_text())
        return 0
    cmd, rest = args[0], args[1:]
    if cmd not in COMMANDS:
        print(f"wiki-spaces: unknown command {cmd!r}", file=sys.stderr)
        print(_help_text(), file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[cmd])
    return module.main(rest)


if __name__ == "__main__":
    sys.exit(main())
