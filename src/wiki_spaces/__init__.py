"""wiki-spaces — minimal nestable wiki for AI coding agents.

See `wiki_spaces.cli` for the console-script entry point. Each subcommand
(install, init, doctor, space, vendor-kepano) is also importable as a
standalone module with a `main()` entry.
"""

from importlib.metadata import PackageNotFoundError, version as _v

try:
    __version__ = _v("wiki-spaces")
except PackageNotFoundError:
    __version__ = "0+unknown"

del _v, PackageNotFoundError
