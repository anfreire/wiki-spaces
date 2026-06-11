# AGENTS.md

The wiki-spaces spec. This document defines the vocabulary, structure, and operating contract for an LLM working in a wiki-spaces wiki.

**Scope.** wiki-spaces targets LLMs running inside an AI coding harness with filesystem access, such as Claude Code, Codex, Cursor, Copilot, Gemini CLI, OpenCode, or Kiro. Browser-only assistants are out of scope. They can't read or write the wiki directly.

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

`## Spaces` is the navigation contract. It's **exhaustive**, meaning every space directly inside this one must be listed there. Tools traverse via this list and rely on it being complete.

- **Maintenance**: The skills maintain `## Spaces` when creating, removing, mounting, or promoting spaces. No CLI commands exist to perform writes.
- **Repair**: The bundled `scripts/ws.py audit [--fix]` serves as the repair surface. It detects drift, broken links, orphans, and over-cap files. Running it with `--fix` inserts missing headings and registers unlisted owned child spaces only.

## Size discipline

Caps are measured in UTF-8 bytes, including frontmatter. The default limits are basename-keyed:

- `index.md`: 5,000 bytes
- `log.md`: 100,000 bytes
- `hot.md`: 100,000 bytes
- Any other `*.md` file: 15,000 bytes

You can override these defaults via `_meta/limits.md` using plain `basename: bytes` lines. The literal name `*.md` re-caps the catch-all for content pages — it is a reserved name, not a glob; patterns and paths are not supported.

Skills check file sizes before writing by running `scripts/ws.py check-size`. An overflow is a signal to split, promote, or trim the content, never to truncate it. A write that shrinks an over-cap file toward its cap is progress, not a new violation. The audit tool catches any size violations that slip through. This is a detect-and-repair model, not write-time CLI enforcement.

## Discovery

Skills locate the active wiki using a specific resolution order:

1. An explicit path provided by the user.
2. The nearest ancestor of the current working directory that contains a wiki.
3. The optional `wiki` key in `~/.config/wiki-spaces/config`, which defines your canonical personal wiki.

When a current working directory wiki and the canonical wiki both exist but differ, skills announce the resolved root. They'll ask for clarification if there's ambiguity.

## Sharing & nesting

What you share is always a space. Your whole wiki is just the top-most space, and a single nested space is the same thing one level down. Sharing a space means sharing its folder. The receiver mounts it however they prefer, such as a subdirectory, symlink, git submodule, or clone. It then lands as a space inside their tree.

Detached spaces are first-class. Any folder anywhere can be a wiki, such as a company repository keeping one at its root. Wikis relate via mounts.

**Trust scope.** Trust scope is relative to the resolved root. Tools distinguish owned spaces from external spaces. External spaces are defined as anything under `shared/`, a foreign-origin git submodule, or a symlink whose realpath escapes the resolved root.

- **Read operations**: Search, audit, and status cross owned spaces by default. External spaces are visited only when the user explicitly asks.
- **Write operations**: Writes stay within the targeted space by default. Other spaces, whether owned or external, are written to only with explicit instruction.

This makes auditing reach project knowledge in `projects/<name>/` automatically, while leaving a teammate's space at `shared/team-foo/` untouched. The same company repository is owned when resolved as your root, but external when reached through a mount from your personal wiki.

## Optional conventions

A wiki opts into one or more conventions from [`CONVENTIONS.md`](CONVENTIONS.md). Each marker is independent, allowing you to adopt any subset that fits your wiki. The three reference skills (`ws-search`, `ws-update`, `ws-tend`) read whatever markers are present and degrade where they are not.

The conventions catalog includes:

- `log.md`: Optional append-only notes. It records one ISO-8601-timestamped line per operation, with no rotation and no structured-field promises.
- `_meta/taxonomy.md`: Taxonomy definitions.
- `_meta/limits.md`: Custom size limits.
- `frontmatter`: Metadata block at the top of files.
- `_template.md`: Template for new files.
- `hot.md`: Frequently updated notes.
- `.obsidian/`: Obsidian configuration folder.
- `.git`: Git repository metadata.

`CONVENTIONS.md` describes what each marker enables.

## Markdown flavor

Obsidian-flavored markdown is the wire format. Wikilinks (`[[page]]`), frontmatter, callouts (`> [!note]`), embeds (`![[page]]`), comments (`%% ... %%`), and Bases (`.base` files) all carry Obsidian semantics. Skills assume this dialect. The companion skills `obsidian-markdown` and `obsidian-bases` are installed via `npx skills add kepano/obsidian-skills` rather than being vendored.

Plain CommonMark still works. Wiki-spaces never requires Obsidian-specific syntax, but anything beyond basic markdown lives in Obsidian's vocabulary. Choosing one dialect keeps skills and human readers speaking the same language.

## Reference skills

Three reference skills are available for working with the wiki:

- `ws-search`: Search for content.
- `ws-update`: Capture, save, and sync content.
- `ws-tend`: Audit, normalize tags, and cross-link.

Each skill is self-contained and includes its own bundled `scripts/ws.py` script.

## Outside the spec

No frontmatter, no required tags, no fixed top-level categories, no required content schema, and no special files beyond `index.md` are mandated. Folder names come from your domain, such as `clients/`, `papers/`, `projects/`, `recipes/`, `drafts/`, or `journal/`. The spec doesn't care what your wiki is for. Anything else you see is convention or tooling layered on top, as described in [`CONVENTIONS.md`](CONVENTIONS.md).
