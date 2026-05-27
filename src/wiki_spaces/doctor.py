"""Read-only audit of wiki-spaces installation state.

Reads ~/.config/wiki-spaces/config and reports:
- the configured wiki and repo paths (and whether they're valid)
- vendor/kepano/ pin and (if network available) drift vs upstream
- per-harness skill install state (symlink-ok / symlink-broken / copy-current /
  copy-stale / missing)

Exit status is 0 only when the config exists and both `wiki` and `repo`
validate OK; otherwise 1 — so `doctor` can gate setup scripts.
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
    _has_spaces_section,
    data_root,
    harness_present,
    installed_root,
    installed_state,
    read_config,
)

REPO_SENTINELS = (
    "AGENTS.md",
    "CONVENTIONS.md",
    "skills/ws-search/SKILL.md",
    "skills/ws-update/SKILL.md",
    "skills/ws-tend/SKILL.md",
    "references/SETUP.md",
    "vendor/kepano/obsidian-markdown/SKILL.md",
    "vendor/kepano/obsidian-bases/SKILL.md",
    # PR-O / §42: COMMIT pins the vendored kepano sha. Missing → the repo
    # is incomplete; we force reinstall rather than half-trusting the
    # vendored content. The dev-only `vendor-kepano` recovery hint
    # doesn't apply to packaged installs, so we don't surface it.
    "vendor/kepano/COMMIT",
)


def _validate_wiki(wiki: str) -> str:
    if not wiki.startswith("/"):
        return "NOT ABSOLUTE"
    p = Path(wiki)
    if not p.exists():
        return "MISSING ON DISK"
    if not (p / "index.md").is_file():
        return "no index.md"
    if not _has_spaces_section(p):
        return "no `## Spaces` section"
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


def check_config() -> bool:
    """Print config state. Return True only when the config exists and both
    `wiki` and `repo` are set and validate OK."""
    print(f"Config ({CONFIG_PATH}):")
    cfg = read_config()
    if not cfg:
        print("  ! missing — run `wiki-spaces install` and `wiki-spaces init`")
        print()
        return False
    wiki = cfg.get("wiki")
    repo = cfg.get("repo")
    ok = True
    if wiki:
        state = _validate_wiki(wiki)
        print(f"  wiki = {wiki}  ({state})")
        ok = ok and state == "OK"
    else:
        print("  wiki = (unset — run `wiki-spaces init` to scaffold or set manually)")
        ok = False
    if repo:
        state = _validate_repo(repo)
        print(f"  repo = {repo}  ({state})")
        ok = ok and state == "OK"
    else:
        print("  repo = (unset — run `wiki-spaces install` to set)")
        ok = False
    print()
    return ok


def check_vendor(net: bool) -> None:
    print("vendor/kepano:")
    vendor_dir = data_root() / "vendor" / "kepano"
    commit_file = vendor_dir / "COMMIT"
    if not commit_file.exists():
        # Packaged installs ship COMMIT with the wheel; missing means the
        # share dir is incomplete (force reinstall). Dev installs recover
        # via the in-tree script. Pick the message based on packaging.
        from ._common import is_packaged
        if is_packaged():
            print(
                "  ! COMMIT missing — reinstall wiki-spaces "
                "(`uv tool install --reinstall wiki-spaces` or "
                "`pip install --force-reinstall wiki-spaces`)."
            )
        else:
            print(
                "  ! COMMIT missing — run `./scripts/vendor_kepano.py` "
                "(dev-only)."
            )
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
    parser.add_argument(
        "--wiki",
        type=Path,
        help="validate this wiki path instead of the configured one. Checks "
        "that index.md is a file and contains a `## Spaces` section; does "
        "NOT read the config or validate the repo install. Exit 0 if the "
        "path resolves to a valid wiki, 1 otherwise.",
    )
    args = parser.parse_args(argv)

    if args.wiki is not None:
        wiki = args.wiki.expanduser().resolve()
        index = wiki / "index.md"
        if not index.is_file():
            print(f"  ! {wiki}: no index.md", file=sys.stderr)
            return 1
        if not _has_spaces_section(wiki):
            print(
                f"  ! {wiki}: index.md has no `## Spaces` section",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {wiki}")
        return 0

    print("=== wiki-spaces DOCTOR ===")
    src_root = data_root()
    inst_root = installed_root()
    print(f"  data root:      {src_root}")
    if inst_root != src_root:
        print(f"  install target: {inst_root}")
    print()
    config_ok = check_config()
    check_vendor(net=not args.no_net)
    for h in HARNESSES:
        check_harness(h)
    if not config_ok:
        print("doctor: config incomplete or invalid (see above).")
        return 1
    print("doctor: OK. For content health (sizes, drift, wikilinks), run `wiki-spaces space audit`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
