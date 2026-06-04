"""Shared module for wiki-spaces ops.

Stdlib-only. Imported by every subcommand module.

Owns:
- The HARNESSES matrix (which AI coding harnesses get skill installs).
- The link_or_copy() helper (cross-platform symlink-or-copy with fallback).
- The wiki-spaces config: ~/.config/wiki-spaces/config (or $XDG_CONFIG_HOME).
  Two keys: `wiki` (canonical wiki path) and `repo` (path to wiki-spaces data).
- Data-source detection: the path containing AGENTS.md, CONVENTIONS.md,
  references/, skills/. Differs between a dev checkout (the repo
  root) and an installed wheel (<site-packages>/wiki_spaces/data/).
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
import os
import shutil
import sys
import tempfile


def _load_fcntl() -> ModuleType | None:
    """Import `fcntl` if available (POSIX), else None.

    The single optional-import site for `fcntl` in the package. `_log`,
    `space`, and `manifest` import this name and guard their flock calls with
    `if fcntl is not None`, so locking degrades to best-effort on platforms
    without it instead of crashing on import — and the optional return type
    carries that fact, so no `# type: ignore` is needed at any consumer.
    """
    try:
        import fcntl
    except ImportError:
        return None
    return fcntl


fcntl: ModuleType | None = _load_fcntl()

HOME = Path.home()

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
CONFIG_PATH = XDG_CONFIG_HOME / "wiki-spaces" / "config"

WIKI_SKILLS = ("ws-search", "ws-update", "ws-tend")
KEPANO_DEPS = ("obsidian-markdown", "obsidian-bases")


def skill_rel(skill: str) -> str:
    """Relative path from a data root to a skill directory.

    The single definition of the skill-layout convention — install (producer)
    and doctor (consumer) both route through it so the paths cannot drift
    (HANDBOOK: one value, one definition; producer=consumer).
    """
    return ("skills" if skill in WIKI_SKILLS else "vendor/kepano") + f"/{skill}"

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
    """Return the directory containing AGENTS.md, CONVENTIONS.md, references/, skills/.

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


COMMON_SKILLS_DIR = HOME / ".agents" / "skills"


@dataclass(frozen=True)
class Harness:
    key: str
    detect: tuple[Path, ...]
    reads_hub: bool
    alias_dirs: tuple[Path, ...]
    source_url: str


# Hub-maximalism: install once into COMMON_SKILLS_DIR; only harnesses that do
# not read the hub get aliases into their native skills dirs.
# Live skills-doc confirmations for the HARNESSES matrix:
# claude: confirmed native ~/.claude/skills and no ~/.agents hub; duplicate names are precedence-ordered — "Where skills live" lists Personal `~/.claude/skills/<skill-name>/SKILL.md`; "enterprise overrides personal, and personal overrides project" — https://code.claude.com/docs/en/skills (confirmed 2026-06-04)
# codex: confirmed hub-only install target; developers doc lists USER `$HOME/.agents/skills` and says duplicate names are not merged — "Where to save skills" / "Codex reads skills from repository, user, admin, and system locations"; "If two skills share the same `name`, Codex doesn’t merge them" — https://developers.openai.com/codex/skills (confirmed 2026-06-04)
# codex: confirmed conflicting native-dir source but hub-read resolves alias need — "Installing Skills" says "Skills are stored in `$CODEX_HOME/skills/` (typically `~/.codex/skills/`)" — https://openai-codex.mintlify.app/features/skills (confirmed 2026-06-04)
# gemini: confirmed native ~/.gemini/skills plus ~/.agents hub; duplicate names are precedence-ordered — "Discovery tiers" lists "User skills: Located in `~/.gemini/skills/` or the `~/.agents/skills/` alias"; "Precedence and aliases" says `.agents/skills/` takes precedence — https://geminicli.com/docs/cli/skills (confirmed 2026-06-04)
# opencode: confirmed native ~/.config/opencode/skills plus documented compat hub; duplicate names must be unique/no precedence documented — "Place files" lists `~/.config/opencode/skills/<name>/SKILL.md` and "Global agent-compatible: `~/.agents/skills/<name>/SKILL.md`"; troubleshooting says "Ensure skill names are unique across all locations" — https://opencode.ai/docs/skills (confirmed 2026-06-04)
# copilot: confirmed native ~/.copilot/skills plus ~/.agents hub; duplicate-name precedence is not defined on the page — "Creating and adding a skill" says personal skills use "`~/.copilot/skills` or `~/.agents/skills`" and project skills include `.agents/skills` — https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills (confirmed 2026-06-04)
# cursor: confirmed native ~/.cursor/skills plus ~/.agents hub; duplicate-name precedence is not defined on the page — "How do I create a skill?" says skills are loaded from ".agents/skills/, .cursor/skills/, ~/.agents/skills/ (global), and ~/.cursor/skills/ (global)" — https://cursor.com/help/customization/skills (confirmed 2026-06-04)
# kiro: confirmed native ~/.kiro/skills and no ~/.agents hub; duplicate names are precedence-ordered — "Skill scope" says global skills reside under `~/.kiro/skills/`; "In case of conflicting names between global and workspace skills, Kiro will prioritize the workspace skill" — https://docs.kiro.dev/skills (confirmed 2026-06-04)
HARNESSES: tuple[Harness, ...] = (
    Harness(
        "claude",
        detect=(HOME / ".claude", Path(".claude")),
        reads_hub=False,
        alias_dirs=(HOME / ".claude" / "skills",),
        source_url="https://code.claude.com/docs/en/skills",
    ),
    Harness(
        "codex",
        detect=(HOME / ".codex/config.toml", HOME / ".codex"),
        reads_hub=True,
        alias_dirs=(),
        source_url="https://developers.openai.com/codex/skills",
    ),
    Harness(
        "gemini",
        detect=(HOME / ".gemini",),
        reads_hub=True,
        alias_dirs=(),
        source_url="https://geminicli.com/docs/cli/skills",
    ),
    Harness(
        "opencode",
        detect=(HOME / ".config/opencode", Path(".opencode")),
        reads_hub=True,
        alias_dirs=(),
        source_url="https://opencode.ai/docs/skills",
    ),
    Harness(
        "copilot",
        detect=(HOME / ".copilot",),
        reads_hub=True,
        alias_dirs=(),
        source_url=(
            "https://docs.github.com/en/copilot/how-tos/copilot-cli/"
            "customize-copilot/add-skills"
        ),
    ),
    Harness(
        "cursor",
        detect=(HOME / ".cursor", Path(".cursor")),
        reads_hub=True,
        alias_dirs=(),
        source_url="https://cursor.com/help/customization/skills",
    ),
    Harness(
        "kiro",
        detect=(Path(".kiro"), HOME / ".kiro"),
        reads_hub=False,
        alias_dirs=(HOME / ".kiro" / "skills",),
        source_url="https://docs.kiro.dev/skills",
    ),
)


def harness_present(h: Harness) -> bool:
    cwd = Path.cwd()
    return any((p if p.is_absolute() else cwd / p).exists() for p in h.detect)


def has_control_chars(value: str) -> bool:
    """True if `value` contains a char `str.splitlines()` treats as a line
    boundary (plus DEL), i.e. anything that would split a one-line field.

    The single definition of "would split a one-line field across lines",
    shared by the `## Spaces` entry validator, the `space log` entry builder,
    the path validator, and the scaffold input guards. Such a char in a
    name/description/path that lands in `index.md` could inject a second
    `## Spaces` heading and corrupt the navigation contract (HANDBOOK: distrust
    boundary inputs; producer=consumer).

    The boundary set MUST match the CONSUMER, `str.splitlines()` (used by
    `_md.has_section` / `parse_section_entries`): every char below 0x20, NEL
    (`\\x85`), LINE SEPARATOR (`\\u2028`), and PARAGRAPH SEPARATOR (`\\u2029`).
    `\\x85` sits above 0x20 yet still splits a line, so an `ord(c) < 0x20` check
    alone would accept a value the consumer then splits. DEL (`\\x7f`) is not a
    line break but is rejected too — a control char never belongs on one line.
    """
    return any(
        ord(c) < 0x20 or c in ("\x7f", "\x85", "\u2028", "\u2029") for c in value
    )


# ---------- Config ----------

class ConfigReadStatus(Enum):
    """Typed outcome of reading the config — distinguishes the three states a
    `{}` return would conflate (HANDBOOK: missing and malformed are typed
    values, not a special case)."""
    OK = "ok"
    MISSING = "missing"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ConfigRead:
    """One config read with provenance: `values` is populated only when
    `status is OK`; MISSING and UNREADABLE both carry `{}`."""
    status: ConfigReadStatus
    values: dict[str, str]


def _read_config() -> ConfigRead:
    """Read the config in ONE filesystem pass, returning a typed outcome.

    The single source the dict view (`read_config`), the unreadable probe
    (`config_exists_unreadable`), and the writer (`write_config`) all share, so
    none of them re-`read_text` the file to re-derive the same fact.

    Format: plain text, key = value per line. Blank lines ignored. Lines whose
    first non-whitespace character is '#' are comments; inline '#' is NOT a
    comment marker (paths may contain '#').
    """
    if not CONFIG_PATH.exists():
        return ConfigRead(ConfigReadStatus.MISSING, {})
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ConfigRead(ConfigReadStatus.UNREADABLE, {})
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return ConfigRead(ConfigReadStatus.OK, out)


def read_config() -> dict[str, str]:
    """Dict view of the config — `{}` for a missing OR unreadable file.

    Best-effort callers (doctor, install, `wiki_path`) that degrade gracefully;
    callers that must distinguish unreadable from absent use `_read_config`.
    """
    return _read_config().values


class ConfigUnreadableError(Exception):
    """An existing config could not be read, so a merge-write would lose keys.

    `write_config` merges over `read_config`, which collapses BOTH an absent
    and an unreadable config to `{}`. Merging one key into `{}` and writing
    would silently drop the other configured path — the same scope-unsafe
    data loss the RESOLVER already refuses via `config_exists_unreadable`
    (HANDBOOK: handle failures at boundaries). The producer must hard-stop on
    the condition the consumer does, not clobber.
    """


def write_config(updates: dict[str, str]) -> None:
    """Merge updates into the config file. Preserves only `wiki` and `repo` keys.

    Raises `ConfigUnreadableError` when an existing config exists but cannot be
    read: blindly merging over a `{}` fallback would drop the keys it could not
    parse. An ABSENT config still writes cleanly (a fresh `{}` is the truth
    then, not a parse failure). One read distinguishes the two.
    """
    existing = _read_config()
    if existing.status is ConfigReadStatus.UNREADABLE:
        raise ConfigUnreadableError(
            f"{CONFIG_PATH} exists but could not be read (non-UTF-8 or "
            "permissions); refusing to overwrite it and lose the existing "
            "wiki/repo entries. Inspect or remove the file, then retry."
        )
    current = dict(existing.values)
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
    atomic_write(CONFIG_PATH, "\n".join(lines) + "\n")


def wiki_path() -> Path | None:
    cfg = read_config()
    return Path(cfg["wiki"]) if "wiki" in cfg else None


def config_exists_unreadable() -> bool:
    """True when `CONFIG_PATH` exists but its bytes cannot be read.

    The dict view (`read_config`) collapses an ABSENT and an UNREADABLE config
    into the same `{}` so its best-effort consumers (doctor, install) degrade
    gracefully. The wiki RESOLVER must NOT: treating an unreadable configured
    wiki as "no config" silently falls back to a CWD wiki — operating on a
    DIFFERENT wiki than the user configured (HANDBOOK: handle failures at
    boundaries; scope-safety). The resolver calls this to hard-stop instead.
    """
    return _read_config().status is ConfigReadStatus.UNREADABLE


def _has_spaces_section(p: Path) -> bool:
    """True iff `p/index.md` exists and contains a `## Spaces` heading."""
    try:
        text = (p / "index.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError: a non-UTF-8 index.md is a legal Obsidian-wire
        # boundary input, not a crash — treat as no confirmable contract.
        return False
    # Late import — `_md` pulls in regex tables that are heavier than
    # `_common` should pay for on cold start.
    from . import _md
    return _md.has_section(text, "Spaces")


def _nearest_space_root(start: Path | None, *, require_section: bool) -> Path | None:
    """Walk up from `start` (or CWD) to the nearest folder with `index.md`.

    `require_section` gates the v1 navigation contract: when True the folder
    must also carry a `## Spaces` heading (strict, read-only callers); when
    False a bare `index.md` qualifies (repair callers that insert the
    heading on demand).
    """
    p = (start if start is not None else Path.cwd())
    if p.is_file():
        p = p.parent
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    for candidate in (p, *p.parents):
        if (candidate / "index.md").is_file() and (
            not require_section or _has_spaces_section(candidate)
        ):
            return candidate
    return None


def _resolve_wiki(explicit: Path | None, *, require_section: bool) -> Path | None:
    """Resolve the wiki root: explicit `--wiki` → absolute config → CWD ancestor.

    `require_section` gates the v1 `## Spaces` contract: strict callers
    (read-only) require it; repair callers (write commands that insert it on
    demand) accept a bare `index.md`. An explicit or configured path that
    fails the contract hard-stops rather than falling through to a CWD
    ancestor — a wiki the user named but that doesn't qualify is a real
    error, not a cue to silently pick a different one. A non-absolute config
    path is refused too: `.resolve()` would join it to CWD and pick a
    different wiki per invocation (doctor rejects relatives for the same
    reason).
    """
    if explicit is not None:
        p = explicit.expanduser().resolve()
        if (p / "index.md").is_file() and (
            not require_section or _has_spaces_section(p)
        ):
            return p
        return None
    cfg_wiki = wiki_path()
    if cfg_wiki is not None:
        cfg_expanded = cfg_wiki.expanduser()
        if not cfg_expanded.is_absolute():
            return None
        p = cfg_expanded.resolve()
        if (p / "index.md").is_file() and (
            not require_section or _has_spaces_section(p)
        ):
            return p
        return None
    return _nearest_space_root(None, require_section=require_section)


def _no_wiki_msg() -> str:
    # Name the path the tool ACTUALLY reads (the XDG-aware CONFIG_PATH), not a
    # hardcoded `~/.config/...` that differs under $XDG_CONFIG_HOME
    # (producer=consumer: the message must match the file the resolver reads).
    return (
        "  ! no wiki resolved. Pass --wiki <path>, set `wiki` in "
        f"{CONFIG_PATH}, or run from inside a wiki."
    )


_NO_SPACES_MSG = (
    "  ! wiki has an index.md but no `## Spaces` heading; not a wiki. "
    "Run a write command (`space add`/`remove`/`mount`/`promote`) or "
    "`space audit --fix` to insert it automatically."
)


def _config_unreadable_msg() -> str:
    return (
        f"  ! {CONFIG_PATH} exists but could not be read; refusing to silently "
        "fall back to a CWD wiki (it may not be the wiki you configured). Fix "
        "its permissions or pass --wiki <path>."
    )


def resolve_wiki(explicit: Path | None = None, *, repair: bool) -> tuple[Path | None, str | None]:
    """Resolve the wiki root, returning `(wiki, error_message)`.

    `repair=True` for write commands that insert `## Spaces` on demand (a
    bare `index.md` suffices); `repair=False` for read-only commands that
    require the navigation contract. Success returns `(wiki, None)`; a miss
    returns `(None, message)` — the caller prints `message` at the CLI layer,
    so this shared resolver builds the message but never presents it. On a
    strict miss we re-probe in repair mode so the message names the actual
    cause — "no wiki anywhere" vs. "found an index.md but it lacks
    `## Spaces`" — instead of one vague line.

    With no explicit `--wiki`, an existing-but-unreadable config hard-stops
    here (rather than via the CWD fallback `_resolve_wiki` would otherwise
    take) so the tool never silently operates on a wiki the user didn't
    configure.
    """
    if explicit is None and config_exists_unreadable():
        return None, _config_unreadable_msg()
    if repair:
        wiki = _resolve_wiki(explicit, require_section=False)
        return (wiki, None) if wiki is not None else (None, _no_wiki_msg())
    wiki = _resolve_wiki(explicit, require_section=True)
    if wiki is not None:
        return wiki, None
    if _resolve_wiki(explicit, require_section=False) is not None:
        return None, _NO_SPACES_MSG
    return None, _no_wiki_msg()


# ---------- Filesystem ----------

def durable_replace(dest: Path, content: str, *, parent_fd: int | None = None) -> None:
    """Crash-atomically replace `dest` with `content`.

    temp file in `dest.parent` -> write + flush + fsync -> `os.replace` ->
    fsync the parent directory (so the rename itself survives a crash). The
    temp file is unlinked if anything before the replace fails. When the
    caller already holds an fd on `dest.parent` (e.g. a `flock` fd), pass it
    as `parent_fd` to reuse it for the directory fsync; otherwise one is
    opened and closed here.

    The single durable-write primitive: `atomic_write` (symlink-aware text
    writes), `manifest._atomic_write_under_flock`, and
    `space._atomic_mutate_index` all route through it, so the crash-safety
    sequence lives in exactly one place.
    """
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.tmp-", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # The replace already committed; a dir-fsync failure must not turn a
    # successful write into an error (best-effort).
    if parent_fd is not None:
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
        return
    try:
        dir_fd = os.open(str(dest.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write(path: Path, content: str) -> None:
    """Write `content` to `path` via tempfile + `os.replace`, durably.

    A crash-safe, symlink-faithful drop-in for `path.write_text(...)`: a
    half-written file is never observable — on any interruption the reader
    sees either the old file intact or the complete new one.

    When `path` is a symlink the write lands on its resolved target, matching
    `write_text` (which follows links), and the temp file is created beside
    that target so the `os.replace` stays on one filesystem.

    Routes through `durable_replace` — the one crash-safety primitive shared
    with the lock-scoped writers (`manifest`, `space`'s `## Spaces` mutator).
    """
    dest = path.resolve() if path.is_symlink() else path
    durable_replace(dest, content)


class LinkResult(Enum):
    """Outcome of `link_or_copy`: a fresh symlink, a copy, or a no-op (dst
    already resolved to src)."""
    SYMLINK = "symlink"
    COPY = "copy"
    NOOP = "noop"


class InstalledState(Enum):
    """State of an installed skill destination, reported by `installed_state`.

    `WRONG_SHAPE` mirrors install's shape gate (a plain file at a skill path is
    refused by `_can_overwrite_skill` as `PLAIN_FILE`); without it the consumer
    would silently bless what the producer refused (HANDBOOK: producer=consumer).
    """
    MISSING = "missing"
    SYMLINK_OK = "symlink-ok"
    SYMLINK_EXTERNAL = "symlink-external"
    SYMLINK_BROKEN = "symlink-broken"
    COPY_CURRENT = "copy-current"
    COPY_STALE = "copy-stale"
    WRONG_SHAPE = "wrong-shape"


def link_or_copy(src: Path, dst: Path, *, prefer_copy: bool = False) -> LinkResult:
    """Materialize src at dst as symlink (preferred) or copy (fallback).

    Returns a `LinkResult`. Idempotent: replaces stale links/files and MIRRORS
    an existing destination directory on copy (a removed-upstream file does not
    linger). Short-circuits when src and dst resolve to the same path (would
    otherwise self-destruct). Refuses to mix file/directory types — if dst is a
    real directory and src is a file (or vice versa), the existing dst is
    removed first.
    """
    src_resolved = src.resolve()
    if dst.exists() or dst.is_symlink():
        try:
            if dst.resolve() == src_resolved:
                if not (prefer_copy and dst.is_symlink()):
                    return LinkResult.NOOP
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
            return LinkResult.SYMLINK
        except (OSError, NotImplementedError):
            pass
    if src_resolved.is_dir():
        # Mirror, not merge: replace an existing destination dir so a file
        # removed upstream cannot linger beside the current ones on reinstall
        # (HANDBOOK: one source of truth; delete superseded). Safe — the only
        # caller (`install`) gates on `can_overwrite_skill`/`--force` first,
        # and the symlink path already replaces the dir.
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        shutil.copytree(src_resolved, dst, symlinks=False)
    else:
        shutil.copy2(src_resolved, dst)
    return LinkResult.COPY


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


def installed_state(dst: Path, src: Path) -> InstalledState:
    """Return a one-word state: symlink-ok, symlink-external, symlink-broken,
    copy-current, copy-stale, wrong-shape, missing.

    symlink-external means the symlink resolves to an existing path that
    is not the expected source — typically because the skill was
    installed via a different mechanism (e.g. an aggregator directory).

    wrong-shape means dst exists as a plain file (or symlink to a file)
    where a skill directory was expected. Install's `_can_overwrite_skill`
    refuses that destination as `PLAIN_FILE`; the consumer must agree, or
    a refused-but-present plain file would read as `copy-current` and be
    silently accepted (HANDBOOK: producer=consumer).

    For directories, copy-current/stale compares the latest mtime of any
    file inside (recursively).
    """
    if not dst.exists() and not dst.is_symlink():
        return InstalledState.MISSING
    if dst.is_symlink():
        target = Path(os.readlink(dst))
        if not target.is_absolute():
            target = dst.parent / target
        target = target.resolve()
        if target == src.resolve() and src.exists():
            return InstalledState.SYMLINK_OK
        return (
            InstalledState.SYMLINK_EXTERNAL
            if target.exists()
            else InstalledState.SYMLINK_BROKEN
        )
    if not dst.is_dir():
        return InstalledState.WRONG_SHAPE
    return (
        InstalledState.COPY_CURRENT
        if _max_mtime(dst) >= _max_mtime(src)
        else InstalledState.COPY_STALE
    )


OWNED_MARKER = ".installed-by-wiki-spaces"


def write_owned_marker(dst: Path, src: Path) -> None:
    """Drop the OWNED_MARKER inside a freshly installed skill directory.

    Recorded source helps `doctor` and future installs identify provenance.
    """
    if not dst.is_dir():
        return
    atomic_write(
        dst / OWNED_MARKER,
        f"# Installed by wiki-spaces. Safe to overwrite on re-install.\n"
        f"source = {src.resolve()}\n",
    )


# ---------- CLI argv ----------

def normalize_wiki_flag(argv: list[str] | None) -> list[str] | None:
    """Lift `--wiki <path>` (or `--wiki=<path>`) to the front of argv.

    `--wiki` is registered on the top-level parser, so argparse requires it
    *before* the subcommand — yet `space audit --wiki ~/wiki` (the natural
    order) is what users type. Strip the flag from anywhere in argv and
    re-inject it at the front so both positions work. When `argv` is None,
    normalize `sys.argv[1:]` so direct module execution
    (`python -m wiki_spaces.space audit --wiki ~/wiki`) benefits too. Leaves
    argv unchanged when `--wiki` is absent.
    """
    if argv is None:
        argv = sys.argv[1:]
    i = 0
    new: list[str] = []
    wiki_args: list[str] = []
    while i < len(argv):
        token = argv[i]
        if token == "--wiki":
            if i + 1 < len(argv):
                wiki_args = ["--wiki", argv[i + 1]]
                i += 2
                continue
            # `--wiki` with no value — let argparse produce its own error.
            new.append(token)
            i += 1
            continue
        if token.startswith("--wiki="):
            wiki_args = [token]
            i += 1
            continue
        new.append(token)
        i += 1
    return wiki_args + new


# ---------- Index scaffold ----------

def new_index_md(name: str, description: str | None = None) -> str:
    """index.md body for a freshly created space (the wiki is the same shape).

    Always emits title + `## Spaces` — the navigation contract, present from
    t=0 on every CLI-created space so `space add foo/bar` works immediately on
    a fresh `foo`. When `description` is non-empty, also emits
    `## What this space is` with it; otherwise that section is skipped entirely
    rather than written as a placeholder the user would have to overwrite.

    The single producer of a new index body — shared by `init` (the wiki root)
    and `space add` (every nested space) so the two can't drift on the shape of
    the contract they create.
    """
    if description and description.strip():
        return (
            f"# {name}\n\n## What this space is\n\n"
            f"{description.strip()}\n\n## Spaces\n\n"
        )
    return f"# {name}\n\n## Spaces\n\n"
