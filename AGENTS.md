# AGENTS.md

The wiki-spaces spec. Vocabulary, structure, and the operating contract for an LLM working in a wiki-spaces wiki.

**Scope.** wiki-spaces targets LLMs running inside an AI coding harness with filesystem access (Claude Code, Codex, Cursor, Windsurf, Gemini CLI, Aider, and similar). Browser-only assistants are out of scope — they cannot read or write the wiki directly.

## What a wiki is

A wiki is a folder with `index.md`, and that `index.md` contains a `## Spaces` heading. Nothing else is required. Files of any kind live alongside; nested spaces (folders that themselves are wikis) recurse.

## Vocabulary

A **space** is a folder with `index.md` and a `## Spaces` heading. The unit; the building block.

A **wiki** is a space — the one at the top of your tree, the one that's yours. From your perspective, it's "the wiki." Embedded in someone else's wiki via clone / submodule / symlink, it's just a space inside theirs. The word changes with position; the thing doesn't.

Inside a space, three kinds of inhabitant:

- **Files** — leaf content (markdown, images, data, anything).
- **Folders** — plain folders (no `index.md`), used for grouping without first-class status (assets, drafts, attachments, raw payloads).
- **Spaces** — folders that themselves are valid wikis, recursively.

Zero contained spaces is a fine wiki — `## Spaces` is just empty. Deep nesting is a fine wiki. Your shape is your call.

## The navigation contract

`## Spaces` is the navigation contract. It is **exhaustive** — every space directly inside this one is listed there, no exceptions. Tools traverse via this list and rely on it being complete.

- **Write commands** (`space add`, `space remove`, `space mount`, `space promote`) maintain `## Spaces` automatically on every operation. When an ancestor's `index.md` is missing the heading, the CLI inserts it as the first step of the mutation. The user never edits `## Spaces` by hand.
- **Read commands** (`space audit`, `doctor`, the three skills) treat a folder with `index.md` but no `## Spaces` as **not a wiki** and refuse to operate. `audit --fix` is the repair surface: it inserts `## Spaces` into any owned folder with `index.md` but no heading, and registers any drift.

Two other `index.md` sections are common but carry no contract:

- **`## What this space is`** — opening paragraph in plain prose. Describes the space. Tools never read it for routing; preserved across regenerations.
- **`## Items`** — an optional, purely human-facing list of files or folders worth surfacing on the landing page. Hand-maintained; tools never read or write it.

Cross-space references go horizontal: `[label](relative/path.md)` or `[[wikilink]]` if surrounding tooling supports it. `index.md` handles parent ↔ child navigation only.

## Size discipline

Hard char caps at write time. The defaults are `index.md` 5,000, `log.md` 100,000 (auto-rotates), every other `*.md` 15,000 — configurable via `_meta/limits.md` (see [`CONVENTIONS.md`](CONVENTIONS.md)). Framework writers (`init`, `space add`, `space mount`, `space promote`, `space log`, the chain helper's ancestor mutations) enforce caps on the projected post-write size; errors on overflow, never silent truncation. A shrinking write (smaller than the existing on-disk body) is the only escape hatch from legacy bloat. Day-30 isn't worse than day-0 — more content invested means more payoff.

## Optional conventions

A wiki opts into one or more conventions from [`CONVENTIONS.md`](CONVENTIONS.md): `log.md`, `_meta/taxonomy.md`, `_meta/limits.md`, `.manifest.json`, frontmatter, `_template.md`, `hot.md`, `.obsidian/`, `.git`. Each marker is independent — adopt any subset that fits your wiki. The three reference skills (`wiki-search`, `wiki-update`, `wiki-tend`) read whatever markers are present and degrade where they're not.

`CONVENTIONS.md` describes what each marker enables. Per-file size discipline (`_meta/limits.md`) is on by default with sensible numbers — see that document's "Size limits" section.

## Sharing & nesting

What you share is always a space. Your whole wiki is just the top-most space; a single nested space is the same thing one level down. Sharing a space means sharing its folder — the receiver mounts it however they prefer (subdir, symlink, git submodule, clone, any filesystem mechanism) and it lands as a space inside their tree.

**Trust scope.** Tools distinguish *owned* spaces (yours — the wiki and spaces you created inside it) from *external* spaces (mounts you don't own — by convention, anything under `<wiki>/shared/`, any git submodule pointing at a foreign origin, or any symlink whose realpath resolves outside the wiki tree).

- **Read operations** (search, audit, status) cross owned spaces by default. External spaces are visited only when the user explicitly names one or asks to include all.
- **Write operations** stay within the targeted space by default. Other spaces — owned or external — are written to only with explicit instruction.

This makes "audit my wiki" reach project knowledge in `projects/<name>/` automatically (those are yours), while leaving a teammate's space at `shared/team-foo/` untouched until you ask for it explicitly.

**Caveat for clones placed outside `shared/`.** The owned/external classification is path-based, not metadata-based. A plain `git clone` placed under `<wiki>/projects/<name>/` (or any path other than `<wiki>/shared/`) is classified as **owned** — writes are allowed by default. If you want read-only / external semantics for a third-party repo, mount it under `<wiki>/shared/` or register it as a foreign-origin git submodule. Push permissions on the upstream remain the de facto upstream-publication gate; trust scope is the local write-time gate.

## Markdown flavor

Obsidian-flavored markdown is the wire format. Wikilinks (`[[page]]`), frontmatter, callouts (`> [!note]`), embeds (`![[page]]`), comments (`%% ... %%`), and Bases (`.base` files) all carry Obsidian semantics. Tools and skills assume this dialect; the vendored kepano skills (`obsidian-markdown`, `obsidian-bases`) are the canonical reference for syntax.

Plain CommonMark still works — wiki-spaces never *requires* Obsidian-specific syntax — but anything beyond basic markdown (links, headings, lists, code blocks, tables) lives in Obsidian's vocabulary. Choosing one dialect keeps tools, skills, and human readers speaking the same language.

## Outside the spec

No frontmatter, no required tags, no fixed top-level categories, no required content schema, no special files beyond `index.md`. Folder names come from your domain — `clients/`, `papers/`, `projects/`, `recipes/`, `drafts/`, `journal/`, whatever fits. The spec doesn't care what your wiki is for. Anything else you see is convention or tooling, layered on top — see [`CONVENTIONS.md`](CONVENTIONS.md).
