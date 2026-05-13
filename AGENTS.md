# AGENTS.md

The wiki-spaces spec. Vocabulary, structure, and the operating contract for an LLM working in a wiki-spaces wiki.

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

`index.md` grows three sections so the wiki maps itself:

- **`## What this space is`** — opening paragraph in plain prose. The space's own description.
- **`## Items`** — curated list of files (and plain folders) worth surfacing. Not exhaustive; tools that need every file glob the filesystem.
- **`## Spaces`** — every space directly inside this one, listed once. **Contract:** this list is exhaustive. Adding a space inside means adding it here in the same change; removing a space means removing the entry. Tools traverse via this list and rely on it being complete.

The opt-in marker is the presence of `## Spaces`. When the marker is present, the contract holds: tools enforce exhaustiveness on writes (add/remove entries automatically) and flag missing entries on audit. When the marker is absent, the wiki is still valid — tools just don't navigate beyond the root `index.md`.

Each space chooses its own tier independently. A wiki at Tier 2 may contain a space at Tier 1 and another at Tier 3.

Cross-space references go horizontal: `[label](relative/path.md)` or `[[wikilink]]` if surrounding tooling supports it. `index.md` handles parent ↔ child navigation only.

### Tier 3 — Managed

Adopts the conventions catalog ([`CONVENTIONS.md`](CONVENTIONS.md)): `log.md`, `_meta/taxonomy.md`, `.manifest.json`, frontmatter schema, the categorical layout, optional `_template.md` and `hot.md`. The three reference skills (`wiki-search`, `wiki-update`, `wiki-tend`) can then fully exercise audit, normalize, cross-link, sync, log, and graph operations.

Each marker is independent — adopt only what you want. `CONVENTIONS.md` describes what each one enables.

## Sharing & nesting

Sharing a wiki is sharing its folder. The receiver mounts it however they prefer — subdir, symlink, git submodule, clone, any filesystem mechanism. From their perspective, your wiki becomes a space inside theirs. The same applies at any level: a single space can be extracted and shared, and it lands in the receiver's tree as a space.

**Trust scope.** Tools distinguish *owned* spaces (the wiki itself and spaces the user created inside it) from *external* spaces (mounts the user doesn't own — by convention, anything under `<wiki>/shared/`, any git submodule pointing at a foreign origin, or any symlink whose realpath resolves outside the wiki tree).

- **Read operations** (search, audit, status) cross owned spaces by default. External spaces are visited only when the user explicitly names one or asks to include all.
- **Write operations** stay within the targeted space by default. Other spaces — owned or external — are written to only with explicit instruction.

This makes "audit my wiki" reach project knowledge in `projects/<name>/` automatically (those are yours), while leaving a teammate's wiki at `shared/team-foo/` untouched until you ask for it explicitly.

## Outside the spec

No frontmatter, no required tags, no fixed top-level categories, no required content schema, no special files beyond `index.md`. Folder names come from your domain — `clients/`, `papers/`, `projects/`, `recipes/`, `drafts/`, `journal/`, whatever fits. The spec doesn't care what your wiki is for. Anything else you see is convention or tooling, layered on top — see [`CONVENTIONS.md`](CONVENTIONS.md).
