"""Shallow-clone kepano/obsidian-skills and copy obsidian-markdown +
obsidian-bases plus the upstream LICENSE into vendor/kepano/. Writes
vendor/kepano/COMMIT with the
source SHA + ISO date. Idempotent: re-running with no upstream change
produces no diff. Aborts (without updating COMMIT) if any required skill
is missing upstream.

Dev-only command: writes into the wiki-spaces source tree. In a packaged
install (wheel), the vendored content is already shipped and immutable —
this command refuses to run.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ._common import KEPANO_DEPS, data_root, is_packaged

KEPANO_REPO = "https://github.com/kepano/obsidian-skills.git"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="git ref to vendor (default: HEAD)")
    args = parser.parse_args(argv)

    if is_packaged():
        print(
            "  ! vendor-kepano is a dev-only command; the installed wheel ships "
            "vendored kepano content already.",
            file=sys.stderr,
        )
        return 2

    vendor_dir = data_root() / "vendor" / "kepano"
    vendor_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="wiki-spaces-vendor-") as td:
        clone_dir = Path(td) / "obsidian-skills"
        print(f"Cloning {KEPANO_REPO} (depth 1)...")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", KEPANO_REPO, str(clone_dir)],
            check=True,
        )
        if args.ref != "HEAD":
            subprocess.run(["git", "-C", str(clone_dir), "fetch", "--depth", "1", "origin", args.ref], check=True)
            subprocess.run(["git", "-C", str(clone_dir), "checkout", args.ref], check=True)

        sha = subprocess.run(
            ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        commit_file = vendor_dir / "COMMIT"
        existing = commit_file.read_text().splitlines() if commit_file.exists() else []
        if (
            existing
            and existing[0] == sha
            and all((vendor_dir / s / "SKILL.md").exists() for s in KEPANO_DEPS)
            and (vendor_dir / "LICENSE").is_file()
        ):
            print(f"\nvendor/kepano/COMMIT unchanged  sha={sha[:12]}  (skipping copy)")
            return 0

        for skill in KEPANO_DEPS:
            src = clone_dir / "skills" / skill
            if not src.exists():
                print(
                    f"  ! ABORT: required skill {skill!r} not found upstream at {args.ref}; "
                    f"COMMIT not updated.",
                    file=sys.stderr,
                )
                return 1
        for skill in KEPANO_DEPS:
            src = clone_dir / "skills" / skill
            dst = vendor_dir / skill
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"  vendored {skill}")

        # Redistributing upstream content carries an obligation to retain its
        # license. kepano/obsidian-skills is MIT — copy LICENSE alongside it.
        license_src = clone_dir / "LICENSE"
        if license_src.is_file():
            shutil.copy2(license_src, vendor_dir / "LICENSE")
            print("  vendored LICENSE")
        else:
            print(
                "  ! WARNING: no LICENSE found upstream; vendor/kepano/LICENSE "
                "not refreshed (vendored content must retain its license).",
                file=sys.stderr,
            )

    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit_file.write_text(f"{sha}\n{iso}\n{KEPANO_REPO}\n")
    print(f"\nwrote vendor/kepano/COMMIT  sha={sha[:12]}  date={iso}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
