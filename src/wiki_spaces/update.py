"""Refresh installed wiki-spaces: re-vendor kepano (dev) and re-run install.

Idempotent superset of `install`. Useful after pulling new commits in the
wiki-spaces repo (dev) or after the user has edited the canonical share dir
manually (packaged).

In packaged installs the vendor step is a no-op (kepano content is shipped
with the wheel); use `uv tool upgrade wiki-spaces` to pick up new vendored
content.

Flags forwarded to `install`: --dry-run, --copy, --harness, --all.
"""

from __future__ import annotations

import sys

from ._common import is_packaged


def main(argv: list[str] | None = None) -> int:
    from . import install  # late import to keep --help path cheap

    forwarded = list(sys.argv[1:] if argv is None else argv)
    if "--help" in forwarded or "-h" in forwarded:
        return install.main(forwarded)

    dry_run = "--dry-run" in forwarded
    print("=== wiki-spaces UPDATE ===\n")
    if is_packaged():
        print("[1/2] (packaged) vendor step skipped; kepano ships with the wheel.\n")
    elif dry_run:
        print("[1/2] (dry-run) would re-vendor kepano (skipping network).\n")
    else:
        from . import vendor_kepano

        print("[1/2] Re-vendoring kepano...")
        rc = vendor_kepano.main([])
        if rc != 0:
            return rc

    print("\n[2/2] Re-running install...")
    return install.main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
