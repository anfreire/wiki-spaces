# AGENTS.md

The wiki-spaces spec. This document defines the vocabulary, structure, and operating contract for an LLM working in a wiki-spaces wiki. Working on wiki-spaces itself? [HANDBOOK.md](HANDBOOK.md) is the bar every change answers to.

**Scope.** wiki-spaces targets LLMs running inside an AI coding harness with filesystem access, such as Claude Code, Codex, Cursor, Copilot, Gemini CLI, OpenCode, or Kiro. The host is POSIX; Windows is out of scope. So are browser-only assistants — they can't read or write the wiki directly.

## What a wiki is

A wiki is a folder with `index.md` containing a `## Spaces` heading. Nothing else is required. Files of any kind live alongside. Nested spaces, which are folders that themselves are wikis, recurse.

## Vocabulary

A **space** is a folder with `index.md` and a `## Spaces` heading. It's the basic building block.

A **wiki** is a space, specifically the one at the top of your tree. From your perspective, it's the wiki. When embedded in another wiki via clone, submodule, or symlink, it's just a space inside that wiki. The word changes with position, but the underlying structure remains the same.

Inside a space, three kinds of inhabitant exist:

- **Files**: Leaf content, such as markdown, images, data, or other assets.
- **Folders**: Plain folders without `index.md`, used for grouping without first-class status.
- **Spaces**: Folders that themselves are valid wikis, recursively.

Zero contained spaces is a fine wiki, meaning `## Spaces` is just empty. Deep nesting is also supported. Your shape is your call.

**When something becomes a space.** A file grows into a space by promotion: `topic.md` becomes `topic/index.md`, gaining the right to hold children. A folder grows into a space by adding `index.md` with a `## Spaces` heading. Triggers are structural, such as accreted siblings, hub-like content, or distinct sub-topics, rather than simple size overflow. The skills carry the promotion procedure.

## The navigation contract

`## Spaces` is the navigation contract. It's **exhaustive**: every space directly inside this one must be listed there. Plain folders and heading-less `index.md` dirs group transparently, so a space beneath them lists at its nearest real ancestor. An entry never reaches through another space's boundary — the space on the way owns the deeper listing. Tools traverse via this list and rely on it being complete. A heading-less `index.md` dir is itself an undecided space: its contents stay out of file walks until it is promoted or silenced — the audit names the choice. An entry is a markdown bullet (any marker; skills write `-`), its href a percent-encoded relative path — encoded where the name demands it, as in `my%20space/index.md` or `notes%20%282024%29/index.md` — so any folder name registers. An entry may trail a `— description`: skills read descriptions as placement hints and preserve them on rewrite.

- **Maintenance**: The skills maintain `## Spaces` when creating, removing, mounting, or promoting spaces. No CLI commands exist to perform writes.
- **Repair**: The bundled `scripts/ws.py audit` is the detection surface. It reports drift, entries crossing a space boundary, over-cap and unreadable files, and registered mounts that stopped being wikis — findings name their repair wherever one is safe to name, down to the exact entry line a `missing entry` asks for, while author-intent findings (a stale or malformed entry, say) name the problem and leave the edit to judgment. The LLM applies repairs as ordinary edits and re-runs the audit to verify; because every round re-derives from disk, the repairs converge in any order. The script itself never writes: an undeclared folder is never treated as a space, and a coincidental `index.md` (a docs site inside a repo-root wiki, say) is reported as a promotion decision, not rewritten.

## Size discipline

Caps are measured in UTF-8 bytes, including frontmatter. The default limits are basename-keyed:

- `index.md`: 5,000 bytes
- `log.md`: 100,000 bytes
- `hot.md`: 100,000 bytes
- Any other `*.md` file: 15,000 bytes

You can override these defaults via `_meta/limits.md` using plain `basename: bytes` lines. Any space can carry its own `_meta/limits.md`; the nearest one at or above a file governs it — closest ancestor wins, the same rule `_template.md` uses — and the lookup never crosses a trust boundary, so an external space answers to its own limits or the defaults, never the host's, and a path outside the wiki answers to the defaults alone. The literal name `*.md` re-caps the catch-all for content pages — it is a reserved name, not a glob; patterns and paths are not supported.

Skills check caps with `scripts/ws.py check-size` — planned content piped via `--stdin` before a write, or the file on disk right after an edit; convention appends (`log.md`) lean on the audit backstop instead. An overflow is a signal about shape, not just size: distill the page or reshape the space that holds it — never truncate. A write that shrinks an over-cap file toward its cap is progress, not a new violation — `check-size` reports it `ok` and says so. The audit tool catches any size violations that slip through. This is a detect-and-repair model, not write-time CLI enforcement.

## Discovery

Skills locate the active wiki using a specific resolution order:

1. An explicit path provided by the user.
2. The nearest ancestor of the current working directory that contains a wiki.
3. The optional `wiki` key in `~/.config/wiki-spaces/config` (under `$XDG_CONFIG_HOME` when that is set), which defines your canonical personal wiki.

When a current working directory wiki and the canonical wiki both exist but differ, skills announce the resolved root. They'll ask for clarification if there's ambiguity.

## Sharing & nesting

What you share is always a space. Your whole wiki is just the top-most space, and a single nested space is the same thing one level down. Sharing a space means sharing its folder. The receiver mounts it however they prefer, such as a subdirectory, symlink, git submodule, or clone. It then lands as a space inside their tree. The skills carry both procedures: mounting someone's space and sharing out one of yours.

Detached spaces are first-class. Any folder anywhere can be a wiki, such as a company repository keeping one at its root. Wikis relate via mounts.

**Trust scope.** Trust scope is relative to the resolved root. Tools distinguish owned spaces from external spaces. External spaces are defined as anything under a folder named exactly `shared/` (lowercase) at any depth, a git submodule (a submodule names another repository by definition; an owned mount of your own second repo is a clone), or a symlink whose realpath escapes the resolved root or lands in external scope. All three rules apply at any depth — every space is a wiki one level down, so a nested space's mounts get the same fence the root's do.

- **Read operations**: Search, audit, and status cross owned spaces by default. External spaces are visited only when the user explicitly asks.
- **Write operations**: Writes stay within the targeted space by default. Other spaces, whether owned or external, are written to only with explicit instruction.

This makes auditing reach project knowledge in `projects/<name>/` automatically, while leaving a teammate's space at `shared/team-foo/` untouched. The same company repository is owned when resolved as your root, but external when reached through a mount from your personal wiki.

## Optional conventions

A wiki opts into one or more conventions from [`CONVENTIONS.md`](CONVENTIONS.md). Each convention is independent, allowing you to adopt any subset that fits your wiki. The three reference skills (`ws-search`, `ws-update`, `ws-tend`) read whatever the wiki adopted and degrade where it hasn't.

The conventions catalog includes:

- `log.md`: Optional append-only notes. It records one ISO-8601-timestamped line per operation, with no structured-field promises; an over-cap log rolls into `_archives/` rather than ever being truncated.
- `_meta/taxonomy.md`: Taxonomy definitions.
- `_meta/limits.md`: Custom size limits.
- `_meta/ignore.md`: Folder names the walk skips.
- `frontmatter`: Metadata block at the top of files.
- `_template.md`: Template for new files.
- `hot.md`: Frequently updated notes.
- `.git`: Git repository metadata.

`CONVENTIONS.md` describes what each enables.

## Markdown flavor

Obsidian-flavored markdown is the wire format. Wikilinks (`[[page]]`), frontmatter, callouts (`> [!note]`), embeds (`![[page]]`), comments (`%% ... %%`), and Bases (`.base` files) all carry Obsidian semantics. Skills assume this dialect. The companion skills `obsidian-markdown` and `obsidian-bases` are installed via `npx skills add kepano/obsidian-skills` rather than being vendored.

Plain CommonMark still works. Wiki-spaces never requires Obsidian-specific syntax, but anything beyond basic markdown lives in Obsidian's vocabulary. Choosing one dialect keeps skills and human readers speaking the same language.

## Reference skills

Three reference skills are available for working with the wiki:

- `ws-search`: Search for content.
- `ws-update`: Capture, save, and sync content; set up or adopt a wiki when none exists.
- `ws-tend`: Audit, normalize tags, and cross-link.

Each skill is self-contained and includes its own bundled `scripts/ws.py` script. The division of labor is fixed: the script parses the contract, never the content — structure (traversal, trust scope, caps, drift) is the tool's; meaning (what a page says, links, tags, what is worth keeping) is the LLM's to read and judge, with `grep` as the sweep that feeds the judgment.

## Outside the spec

No frontmatter, no required tags, no fixed top-level categories, no required content schema, and no special files beyond `index.md` are mandated. Folder names come from your domain, such as `clients/`, `papers/`, `projects/`, `recipes/`, `drafts/`, or `journal/`. The spec doesn't care what your wiki is for. Anything else you see is convention or tooling layered on top, as described in [`CONVENTIONS.md`](CONVENTIONS.md).
