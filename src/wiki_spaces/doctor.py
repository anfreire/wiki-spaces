"""Read-only audit of wiki-spaces installation state.

Reads ~/.config/wiki-spaces/config and reports:
- the configured wiki and repo paths (and whether they're valid)
- vendor/kepano/ pin and (if network available) drift vs upstream
- per-harness skill install state (symlink-ok / symlink-broken / copy-current /
  copy-stale / missing)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ._common import (
    CONFIG_PATH,
    HARNESSES,
    KEPANO_DEPS,
    WIKI_SKILLS,
    data_root,
    harness_present,
    installed_root,
    installed_state,
    read_config,
)

REPO_SENTINELS = (
    "AGENTS.md",
    "CONVENTIONS.md",
    "skills/wiki-search/SKILL.md",
    "references/SETUP.md",
    "vendor/kepano/obsidian-markdown/SKILL.md",
)


def _validate_wiki(wiki: str) -> str:
    if not wiki.startswith("/"):
        return "NOT ABSOLUTE"
    p = Path(wiki)
    if not p.exists():
        return "MISSING ON DISK"
    if not (p / "index.md").exists():
        return "no index.md"
    return "OK"


def _validate_repo(repo: str) -> str:
    if not repo.startswith("/"):
        return "NOT ABSOLUTE"
    p = Path(repo)
    if not p.exists():
        return "MISSING ON DISK"
    missing = [s for s in REPO_SENTINELS if not (p / s).exists()]
    if missing:
        return f"NOT A WIKI-SPACES INSTALL (missing: {', '.join(missing)})"
    return "OK"


def check_config() -> None:
    print(f"Config ({CONFIG_PATH}):")
    cfg = read_config()
    if not cfg:
        print("  ! missing — run `wiki-spaces install` and `wiki-spaces init`")
        print()
        return
    wiki = cfg.get("wiki")
    repo = cfg.get("repo")
    if wiki:
        print(f"  wiki = {wiki}  ({_validate_wiki(wiki)})")
    else:
        print("  wiki = (unset — run `wiki-spaces init` to scaffold or set manually)")
    if repo:
        print(f"  repo = {repo}  ({_validate_repo(repo)})")
    else:
        print("  repo = (unset — run `wiki-spaces install` to set)")
    print()


def check_vendor(net: bool) -> None:
    print("vendor/kepano:")
    vendor_dir = data_root() / "vendor" / "kepano"
    commit_file = vendor_dir / "COMMIT"
    if not commit_file.exists():
        print("  ! COMMIT file missing — run `wiki-spaces vendor-kepano`")
        return
    lines = commit_file.read_text().strip().splitlines()
    sha = lines[0] if lines else "?"
    date = lines[1] if len(lines) > 1 else "?"
    repo = lines[2] if len(lines) > 2 else "?"
    print(f"  pinned sha:  {sha[:12]}")
    print(f"  vendored at: {date}")
    for skill in KEPANO_DEPS:
        ok = (vendor_dir / skill / "SKILL.md").exists()
        print(f"  {skill}: {'present' if ok else 'MISSING'}")
    if net:
        try:
            head = subprocess.run(
                ["git", "ls-remote", repo, "HEAD"],
                capture_output=True, text=True, check=True, timeout=5,
            ).stdout.split("\t", 1)[0]
            drift = "current" if head == sha else f"DRIFT (upstream {head[:12]})"
            print(f"  upstream HEAD: {drift}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            print("  upstream HEAD: unknown (offline or git unavailable)")
    print()


def check_harness(h) -> None:
    present = harness_present(h)
    print(f"{h.key}: {'detected' if present else 'not detected'}")
    root = installed_root()
    for skill in (*WIKI_SKILLS, *KEPANO_DEPS):
        src = root / ("skills" if skill in WIKI_SKILLS else "vendor/kepano") / skill
        dst = h.skills_dir / skill
        print(f"  {skill:22s} -> {dst}: {installed_state(dst, src)}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-net", action="store_true", help="skip upstream drift check")
    args = parser.parse_args(argv)

    print("=== wiki-spaces DOCTOR ===")
    src_root = data_root()
    inst_root = installed_root()
    print(f"  data root:      {src_root}")
    if inst_root != src_root:
        print(f"  install target: {inst_root}")
    print()
    check_config()
    check_vendor(net=not args.no_net)
    for h in HARNESSES:
        check_harness(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
