# AGENTS.md

The wiki-spaces spec. Vocabulary, structure, and the operating contract for an LLM working in a wiki-spaces wiki.

**Scope.** wiki-spaces targets LLMs running inside an AI coding harness with filesystem access (Claude Code, Codex, Cursor, Windsurf, Gemini CLI, Aider, and similar). Browser-only assistants are out of scope — they cannot read or write the wiki directly.

## What a wiki is

A wiki is a folder with `index.md`. That's it.

You can stop here — a folder, an `index.md`, and whatever files you want is a complete wiki. The rest of this doc describes what's *possible* on top of that, not what's required.

## Vocabulary

A **space** is a folder with `index.md`. The unit, the building block.

A **wiki** is a space — the one at the top of your tree, the one that's yours. From your perspective, it's "the wiki." Embedded in someone else's wiki via clone / submodule / symlink, it's just a space inside theirs. The word changes with position; the thing doesn't.

Inside a space, three kinds of inhabitant:

- **Files** — leaf content (markdown, images, data, anything).
- **Folders** — plain folders (no `index.md`), used for grouping without first-class status (assets, drafts, attachments, raw payloads).
- **Spaces** — folders that themselves have `index.md`, recursively.

Zero contained spaces is a fine wiki. Deep nesting is a fine wiki. Your shape is your call.

## Tiers

Three tiers of opt-in. Each tier adds capability and the small contract tools rely on *at that tier*. A wiki opts in by adding the marker; tools detect the tier from what's present and degrade where it isn't. Pick the highest tier that fits your needs.

### Tier 1 — Valid

A folder with `index.md`. Tools can find the wiki, read its `index.md`, and operate on the files within. No structural promises beyond "I'm a wiki." Search uses filesystem globs since there's no curated map.

### Tier 2 — Navigable

The opt-in marker is the presence of `## Spaces` in `index.md`:

- **`## Spaces`** — every space directly inside this one, listed once. **Contract:** this list is exhaustive. Adding a space inside means adding it here in the same change; removing a space means removing the entry. Tools traverse via this list and rely on it being complete.

When `## Spaces` is present, the contract holds: tools enforce exhaustiveness on writes (add/remove entries automatically) and flag missing entries on audit. When the marker is absent, the wiki is still valid — tools just fall back to filesystem discovery beyond the root `index.md`.

Two other `index.md` sections are common but **independent** of the Tier 2 contract — adopt them, or not, without affecting tier:

- **`## What this space is`** — opening paragraph in plain prose. Describes the space.
- **`## Items`** — an optional, purely human-facing list of files or folders worth surfacing on the landing page. Hand-maintained; tools never read or write it. (Tools find content through `## Spaces` and the filesystem — a wiki needs no `## Items`.)

Each space chooses its own tier independently. A wiki at Tier 2 may contain a space at Tier 1 and another at Tier 3.

Cross-space references go horizontal: `[label](relative/path.md)` or `[[wikilink]]` if surrounding tooling supports it. `index.md` handles parent ↔ child navigation only.

### Tier 3 — Conventional

A wiki opts into one or more conventions from [`CONVENTIONS.md`](CONVENTIONS.md): `log.md`, `_meta/taxonomy.md`, `.manifest.json`, frontmatter, `_template.md`, `hot.md`, `.obsidian/`, `.git`. Each marker is independent — adopt any subset that fits your wiki. The three reference skills (`wiki-search`, `wiki-update`, `wiki-tend`) read whatever markers are present and degrade where they're not.

`CONVENTIONS.md` describes what each marker enables, and groups the four knowledge-capture conventions (frontmatter schema, page template, provenance markers, noise filter) as a separate pack you can opt into when the wiki is a memory aid rather than a content store.

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
