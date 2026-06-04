"""Read-only audit of wiki-spaces installation state.

Reads ${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config and reports:
- the configured wiki and repo paths (and whether they're valid)
- vendor/kepano/ pin and (if network available) drift vs upstream
- per-harness skill install state (symlink-ok / symlink-external /
  symlink-broken / copy-current / copy-stale / missing)

Exit status is 0 only when the config exists and both `wiki` and `repo`
validate OK; otherwise 1 — so `doctor` can gate setup scripts.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ._common import (
    COMMON_SKILLS_DIR,
    CONFIG_PATH,
    HARNESSES,
    Harness,
    InstalledState,
    KEPANO_DEPS,
    WIKI_SKILLS,
    _has_spaces_section,
    config_exists_unreadable,
    data_root,
    harness_present,
    installed_root,
    installed_state,
    read_config,
    skill_rel,
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
    # COMMIT pins the vendored kepano sha. Missing → the repo
    # is incomplete; we force reinstall rather than half-trusting the
    # vendored content. The dev-only `vendor-kepano` recovery hint
    # doesn't apply to packaged installs, so we don't surface it.
    "vendor/kepano/COMMIT",
)


class ValidationState(Enum):
    """Typed outcome of a config-path check. The CLI renders `.value` (plus
    any `ValidationResult.detail`) at the print site; callers compare on the
    enum, never on a rendered string."""
    OK = "OK"
    NOT_ABSOLUTE = "NOT ABSOLUTE"
    MISSING_ON_DISK = "MISSING ON DISK"
    NO_INDEX = "no index.md"
    NO_SPACES_SECTION = "no `## Spaces` section"
    NOT_AN_INSTALL = "NOT A WIKI-SPACES INSTALL"


@dataclass(frozen=True)
class ValidationResult:
    """A `ValidationState` plus optional provenance `detail` (e.g. which repo
    sentinels were missing). `render()` is the one place the state becomes a
    human string; `ok` is the only success check callers need."""
    state: ValidationState
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.state is ValidationState.OK

    def render(self) -> str:
        if self.detail:
            return f"{self.state.value} ({self.detail})"
        return self.state.value


def _validate_wiki(wiki: str) -> ValidationResult:
    if not Path(wiki).is_absolute():
        return ValidationResult(ValidationState.NOT_ABSOLUTE)
    p = Path(wiki)
    if not p.exists():
        return ValidationResult(ValidationState.MISSING_ON_DISK)
    if not (p / "index.md").is_file():
        return ValidationResult(ValidationState.NO_INDEX)
    if not _has_spaces_section(p):
        return ValidationResult(ValidationState.NO_SPACES_SECTION)
    return ValidationResult(ValidationState.OK)


def _validate_repo(repo: str) -> ValidationResult:
    if not Path(repo).is_absolute():
        return ValidationResult(ValidationState.NOT_ABSOLUTE)
    p = Path(repo)
    if not p.exists():
        return ValidationResult(ValidationState.MISSING_ON_DISK)
    missing = [s for s in REPO_SENTINELS if not (p / s).exists()]
    if missing:
        return ValidationResult(
            ValidationState.NOT_AN_INSTALL,
            detail=f"missing: {', '.join(missing)}",
        )
    return ValidationResult(ValidationState.OK)


def check_config() -> bool:
    """Print config state. Return True only when the config exists and both
    `wiki` and `repo` are set and validate OK."""
    print(f"Config ({CONFIG_PATH}):")
    cfg = read_config()
    if not cfg:
        if config_exists_unreadable():
            print(
                "  ! exists but could not be read — check its permissions "
                "(an unreadable config is NOT the same as a missing one)"
            )
        else:
            print("  ! missing — run `wiki-spaces install` and `wiki-spaces init`")
        print()
        return False
    wiki = cfg.get("wiki")
    repo = cfg.get("repo")
    ok = True
    if wiki:
        result = _validate_wiki(wiki)
        print(f"  wiki = {wiki}  ({result.render()})")
        ok = ok and result.ok
    else:
        print("  wiki = (unset — run `wiki-spaces init` to scaffold or set manually)")
        ok = False
    if repo:
        result = _validate_repo(repo)
        print(f"  repo = {repo}  ({result.render()})")
        ok = ok and result.ok
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
    try:
        lines = commit_file.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        # A boundary diagnostic must not crash on an unreadable / non-UTF-8
        # packaged file — report it as an incomplete install (HANDBOOK: handle
        # failures at boundaries), mirroring the guarded subprocess below.
        print(
            f"  ! COMMIT unreadable ({e}) — reinstall wiki-spaces to restore "
            "the vendored skills."
        )
        return
    sha = lines[0] if lines else "?"
    date = lines[1] if len(lines) > 1 else "?"
    remote = lines[2] if len(lines) > 2 else "?"
    print(f"  pinned sha:  {sha[:12]}")
    print(f"  vendored at: {date}")
    for skill in KEPANO_DEPS:
        ok = (vendor_dir / skill / "SKILL.md").exists()
        print(f"  {skill}: {'present' if ok else 'MISSING'}")
    if net:
        try:
            head = subprocess.run(
                ["git", "ls-remote", remote, "HEAD"],
                capture_output=True, text=True, check=True, timeout=5,
            ).stdout.split("\t", 1)[0]
            drift = "current" if head == sha else f"DRIFT (upstream {head[:12]})"
            print(f"  upstream HEAD: {drift}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            print("  upstream HEAD: unknown (offline or git unavailable)")
    print()


def _skill_src(skill: str) -> Path:
    """Source path a skill destination should resolve to — `installed_root()`
    joined with the shared `skill_rel()` so install and doctor can never
    compute different relative paths (HANDBOOK: producer=consumer)."""
    return installed_root() / skill_rel(skill)


def check_hub() -> None:
    """Verify the shared hub (COMMON_SKILLS_DIR) holds every skill. install
    materializes each `(*WIKI_SKILLS, *KEPANO_DEPS)` skill here once; this is
    the consumer half. Flags the hub incomplete on any missing/broken skill."""
    print(f"hub ({COMMON_SKILLS_DIR}):")
    incomplete = False
    for skill in (*WIKI_SKILLS, *KEPANO_DEPS):
        src = _skill_src(skill)
        dst = COMMON_SKILLS_DIR / skill
        state = installed_state(dst, src)
        if state in (
            InstalledState.MISSING,
            InstalledState.SYMLINK_BROKEN,
            InstalledState.WRONG_SHAPE,
        ):
            incomplete = True
        print(f"  {skill:22s} -> {dst}: {state.value}")
    if incomplete:
        print("  ! hub incomplete — run `wiki-spaces install` to materialize skills")
    print()


def check_harness(h: Harness) -> None:
    present = harness_present(h)
    print(f"{h.key}: {'detected' if present else 'not detected'}")
    if h.reads_hub:
        msg = "served by hub" if present else "would be served by hub if present"
        print(f"  {msg}")
        print()
        return
    for alias_dir in h.alias_dirs:
        for skill in (*WIKI_SKILLS, *KEPANO_DEPS):
            src = _skill_src(skill)
            dst = alias_dir / skill
            print(f"  {skill:22s} -> {dst}: {installed_state(dst, src).value}")
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
    check_hub()
    for h in HARNESSES:
        check_harness(h)
    if not config_ok:
        print("doctor: config incomplete or invalid (see above).")
        return 1
    print("doctor: OK. For content health (sizes, drift, wikilinks), run `wiki-spaces space audit`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
