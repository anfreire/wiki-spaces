"""Shared module for wiki-spaces ops.

Stdlib-only. Imported by every subcommand module.

Owns:
- The HARNESSES matrix (which AI coding harnesses get skill installs).
- The link_or_copy() helper (cross-platform symlink-or-copy with fallback).
- The wiki-spaces config: ~/.config/wiki-spaces/config (or $XDG_CONFIG_HOME).
  Two keys: `wiki` (canonical wiki path) and `repo` (path to wiki-spaces data).
- Data-source detection: the path containing AGENTS.md, CONVENTIONS.md,
  references/, skills/, vendor/. Differs between a dev checkout (the repo
  root) and an installed wheel (<site-packages>/wiki_spaces/data/).
"""

from dataclasses import dataclass
from pathlib import Path
import os
import shutil

HOME = Path.home()

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
CONFIG_PATH = XDG_CONFIG_HOME / "wiki-spaces" / "config"

WIKI_SKILLS = ("wiki-search", "wiki-update", "wiki-tend")
KEPANO_DEPS = ("obsidian-markdown", "obsidian-bases")

DATA_SENTINELS = ("AGENTS.md", "CONVENTIONS.md", "references", "skills")


def _packaged_data_dir() -> Path | None:
    """Return the wheel-packaged data dir if present, else None."""
    candidate = Path(__file__).resolve().parent / "data"
    if all((candidate / s).exists() for s in DATA_SENTINELS):
        return candidate
    return None


def _dev_repo_root() -> Path | None:
    """Walk up from this file looking for a wiki-spaces source checkout."""
    # src/wiki_spaces/_common.py → ../../.. is the repo root in a normal layout.
    candidate = Path(__file__).resolve().parent.parent.parent
    if all((candidate / s).exists() for s in DATA_SENTINELS):
        return candidate
    return None


def data_root() -> Path:
    """Return the directory containing AGENTS.md, references/, skills/, vendor/.

    Two cases:
    - Installed wheel: <site-packages>/wiki_spaces/data/
    - Dev source checkout: the repo root.
    """
    packaged = _packaged_data_dir()
    if packaged is not None:
        return packaged
    dev = _dev_repo_root()
    if dev is not None:
        return dev
    raise RuntimeError(
        "wiki-spaces data not locatable; expected either "
        f"{Path(__file__).resolve().parent / 'data'} (installed) or a "
        "source checkout containing AGENTS.md, CONVENTIONS.md, references/, skills/."
    )


def share_dir() -> Path:
    """Stable filesystem location for installed wiki-spaces data.

    Used by `install` when sourcing from a packaged wheel: data is copied here
    so harness symlinks point at a stable path even after the wheel's
    site-packages location vanishes (e.g. ephemeral `uvx` runs).
    """
    return HOME / ".local" / "share" / "wiki-spaces"


def is_packaged() -> bool:
    """True when running from an installed wheel (data shipped inside the package)."""
    return _packaged_data_dir() is not None


def installed_root() -> Path:
    """Where `install` materializes data to — the path symlinks target.

    Packaged: share_dir() (data copied here so harness symlinks survive after
    the wheel's site-packages location vanishes).
    Dev: data_root() (the repo root; symlinks point at the live checkout).

    Doctor uses this to compare against the actual symlink targets.
    """
    if is_packaged():
        return share_dir()
    return data_root()


@dataclass(frozen=True)
class Harness:
    key: str
    detect: tuple[Path, ...]
    skills_dir: Path


HARNESSES: tuple[Harness, ...] = (
    Harness(
        "claude",
        detect=(HOME / ".claude", Path(".claude")),
        skills_dir=HOME / ".claude/skills",
    ),
    Harness(
        "codex",
        detect=(HOME / ".codex/config.toml", HOME / ".codex"),
        skills_dir=HOME / ".codex/skills",
    ),
    Harness(
        "gemini",
        detect=(HOME / ".gemini",),
        skills_dir=HOME / ".gemini/skills",
    ),
    Harness(
        # Google Antigravity nests its skills under ~/.gemini/antigravity/;
        # detecting that subdir (not ~/.gemini) keeps it distinct from the
        # Gemini CLI harness above. An Antigravity-only machine still trips
        # gemini's ~/.gemini detect — harmless (an unused ~/.gemini/skills/).
        "antigravity",
        detect=(HOME / ".gemini/antigravity",),
        skills_dir=HOME / ".gemini/antigravity/skills",
    ),
    Harness(
        "hermes",
        detect=(HOME / ".hermes",),
        skills_dir=HOME / ".hermes/skills",
    ),
    Harness(
        "kiro",
        detect=(Path(".kiro"), HOME / ".kiro"),
        skills_dir=HOME / ".kiro/skills",
    ),
)


def harness_present(h: Harness) -> bool:
    cwd = Path.cwd()
    return any((p if p.is_absolute() else cwd / p).exists() for p in h.detect)


# ---------- Config ----------

def read_config() -> dict[str, str]:
    """Read ~/.config/wiki-spaces/config. Returns {} if missing.

    Format: plain text, key = value per line. Blank lines ignored.
    Lines whose first non-whitespace character is '#' are comments;
    inline '#' is NOT treated as a comment marker (paths may contain '#').
    """
    if not CONFIG_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def write_config(updates: dict[str, str]) -> None:
    """Merge updates into the config file. Preserves only `wiki` and `repo` keys."""
    current = read_config()
    current.update(updates)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# wiki-spaces config",
        "# Two keys: `wiki` (canonical wiki path) and `repo` (path to wiki-spaces data).",
        "",
    ]
    if "wiki" in current:
        lines.append(f"wiki = {current['wiki']}")
    if "repo" in current:
        lines.append(f"repo = {current['repo']}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def wiki_path() -> Path | None:
    cfg = read_config()
    return Path(cfg["wiki"]) if "wiki" in cfg else None


def repo_path() -> Path | None:
    cfg = read_config()
    return Path(cfg["repo"]) if "repo" in cfg else None


def nearest_space_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or CWD) returning the nearest folder with index.md.

    Used as the CWD-based fallback when the config has no `wiki` key: lets
    the agent operate on whatever wiki it's currently inside without forcing
    a setup step first.
    """
    p = (start if start is not None else Path.cwd())
    if p.is_file():
        p = p.parent
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    for candidate in (p, *p.parents):
        if (candidate / "index.md").is_file():
            return candidate
    return None


# ---------- Filesystem ----------

def link_or_copy(src: Path, dst: Path, *, prefer_copy: bool = False) -> str:
    """Materialize src at dst as symlink (preferred) or copy (fallback).

    Returns 'symlink', 'copy', or 'noop'. Idempotent: replaces stale links/files;
    merges into existing directories on copy. Short-circuits when src and dst
    resolve to the same path (would otherwise self-destruct). Refuses to mix
    file/directory types — if dst is a real directory and src is a file (or
    vice versa), the existing dst is removed first.
    """
    src_resolved = src.resolve()
    if dst.exists() or dst.is_symlink():
        try:
            if dst.resolve() == src_resolved:
                return "noop"
        except (OSError, RuntimeError):
            pass
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.is_dir() and not src_resolved.is_dir():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not prefer_copy:
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        try:
            os.symlink(src_resolved, dst, target_is_directory=src_resolved.is_dir())
            return "symlink"
        except (OSError, NotImplementedError):
            pass
    if src_resolved.is_dir():
        shutil.copytree(src_resolved, dst, symlinks=False, dirs_exist_ok=True)
    else:
        shutil.copy2(src_resolved, dst)
    return "copy"


def _max_mtime(p: Path) -> float:
    if not p.exists():
        return 0.0
    if p.is_file():
        return p.stat().st_mtime
    if p.is_dir():
        return max(
            (f.stat().st_mtime for f in p.rglob("*") if f.is_file()),
            default=p.stat().st_mtime,
        )
    return 0.0


def installed_state(dst: Path, src: Path) -> str:
    """Return a one-word state: symlink-ok, symlink-broken, copy-current,
    copy-stale, missing.

    For directories, copy-current/stale compares the latest mtime of any
    file inside (recursively).
    """
    if not dst.exists() and not dst.is_symlink():
        return "missing"
    if dst.is_symlink():
        target = Path(os.readlink(dst))
        if not target.is_absolute():
            target = (dst.parent / target).resolve()
        return "symlink-ok" if target == src.resolve() and src.exists() else "symlink-broken"
    return "copy-current" if _max_mtime(dst) >= _max_mtime(src) else "copy-stale"


OWNED_MARKER = ".installed-by-wiki-spaces"


def is_owned_install(dst: Path, src: Path) -> bool:
    """True when dst is safe for wiki-spaces to overwrite.

    Ownership signals:
    - dst does not exist (nothing to overwrite).
    - dst is any symlink (we make symlinks by default; treat as ours).
    - dst is a directory containing the OWNED_MARKER file.

    A plain directory or file at dst without the marker is treated as
    user-owned content; install must refuse without --force.
    """
    if not dst.exists() and not dst.is_symlink():
        return True
    if dst.is_symlink():
        return True
    if dst.is_dir() and (dst / OWNED_MARKER).is_file():
        return True
    return False


def write_owned_marker(dst: Path, src: Path) -> None:
    """Drop the OWNED_MARKER inside a freshly installed skill directory.

    Recorded source helps `doctor` and future installs identify provenance.
    """
    if not dst.is_dir():
        return
    (dst / OWNED_MARKER).write_text(
        f"# Installed by wiki-spaces. Safe to overwrite on re-install.\n"
        f"source = {src.resolve()}\n",
        encoding="utf-8",
    )
