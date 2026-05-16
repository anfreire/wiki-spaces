# Conventions

This is the opt-in catalog. Every section is independent. The [spec](AGENTS.md) defines three tiers — Valid (just `index.md`), Navigable (adds `## Spaces`), and Conventional (any marker from this catalog). Adopt only what you want; tools degrade where a marker is absent.

**"Tools" in this catalog** means the three reference skills (`wiki-search`, `wiki-update`, `wiki-tend`) — LLM-driven procedures that read these markers and degrade gracefully. The `wiki-spaces` CLI handles `install` / `init` / `doctor` / `space` / `vendor-kepano` only; runtime knowledge operations (search, capture, audit, normalize, colorize) live in the skills.

**Obsidian-flavored markdown is the wire format** — see [`AGENTS.md` / Markdown flavor](AGENTS.md#markdown-flavor). Syntax facts (wikilinks, frontmatter, callouts, embeds, comments, Bases) live in [`vendor/kepano/obsidian-markdown`](vendor/kepano/obsidian-markdown/SKILL.md) and [`vendor/kepano/obsidian-bases`](vendor/kepano/obsidian-bases/SKILL.md). Cite those skills, never restate their contents.

---

## Knowledge-capture pack

A recommended bundle for wikis used as a *memory aid* (research notes, developer notebooks, technical wikis) rather than a *content store* (recipes, journals, runbooks, contact lists, curricula). The four conventions below are designed to compose — they work best together, but each is independently usable; pick any subset.

The bundle:

- [Frontmatter schema](#frontmatter-schema) — `title`, `tags`, `summary`, `sources`, etc.
- [Page template](#page-template) — `## Key Ideas` / `## Open Questions` body shape
- [Provenance markers](#provenance-markers) — `%%inferred%%` / `%%ambiguous%%` on claims
- [Noise filter](#noise-filter) — "skip what code answers; capture what took 30 minutes"

Content-store wikis typically skip the whole bundle. Either flavor of wiki can still adopt the rest of the catalog (`log.md`, `_meta/taxonomy.md`, `.manifest.json`, `.obsidian/`, etc.) independently.

---

## Example opt-in bundles

A new wiki only needs `index.md` (per spec) to be valid. Beyond that, the bundle of opt-in markers that's useful varies by use case. Examples — none are required:

| Use case | Suggested bundle |
|---|---|
| Developer notebook | `log.md` + `_meta/taxonomy.md` + `.manifest.json` |
| Research wiki | `log.md` + `_meta/taxonomy.md` |
| Writing project | `hot.md` |
| Recipe collection | (none — pure content) |
| Personal knowledge | (none — pure content) |
| Team reference | `log.md` + `_meta/taxonomy.md` |

Each opt-in is independent — adopt any combination. Frontmatter (see § Frontmatter schema) is per-page rather than a bundle file; mixed adoption is permitted. The catalog below explains what each marker enables; skip any of them and the corresponding tooling step degrades. The scaffold command takes any subset: `wiki-spaces init <path> --with log.md _meta/taxonomy.md` (prefix with `uvx` for no-install runs).

---

## Discovery via config

Skills locate the user's canonical wiki by reading `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config`. Plain text, key = value, blank lines ignored; whole-line comments start with `#`. Inline `#` is not a comment marker — paths may legitimately contain `#`, so keep comments on their own lines:

```
# wiki-spaces config
# wiki: canonical wiki path (must contain index.md)
# repo: path to wiki-spaces install (share dir from `wiki-spaces install`, or source checkout)
wiki = /home/you/Wiki
repo = /home/you/.local/share/wiki-spaces
```

Both keys are absolute paths. `wiki` points to a folder containing `index.md`; `repo` points to the wiki-spaces install — the share dir written by `wiki-spaces install` (PyPI users) or a source checkout (dev users) — so skills can fetch `AGENTS.md`, `CONVENTIONS.md`, and `references/` on demand. If either path doesn't resolve, skills fail soft and tell the user what's missing.

**One canonical wiki per user.** wiki-spaces is built around a single wiki you call yours; the tooling assumes that. Users who want to switch between multiple wikis can swap configs manually. The single-wiki model is what makes global capture, cross-linking, and "open in Obsidian" all work coherently.

Bootstrap: `wiki-spaces install` writes the `repo` key automatically; `wiki-spaces init` writes the `wiki` key when scaffolding (unless `--no-config`).

**Resolution order.** Skills resolve the target wiki in three steps: (1) an explicit path or named space in the user's request; (2) the `wiki` value in the config, when it points at a folder containing `index.md`; (3) **CWD ancestor** — the nearest ancestor of the current working directory that contains `index.md`. Step 3 lets a no-install Tier 1 wiki (folder + `index.md`, no config) work whenever the agent runs from inside it; skills note once when step 3 was the source and suggest `wiki-spaces init` to register it.

When all three miss — no config *and* the CWD is not inside any wiki:
- `wiki-update` runs the Initialization flow (see `wiki-update/SKILL.md` § Initialization, which mirrors `references/SETUP.md`).
- `wiki-search` and `wiki-tend` fail soft: tell the user setup is needed and point them at `references/SETUP.md` (use the raw GitHub URL if the `repo` path is also unknown).

**CWD informs placement, not wiki choice.** Once a wiki is resolved by any step above, CWD and conversation context decide only *where within that wiki* a write goes — a project space vs global concepts — never which wiki to use. A configured `wiki` is never overridden by CWD; CWD acts as a discovery source only in step 3, the fallback when neither an explicit path nor a config is available.

## Per-space convention auto-detection

Each space is autonomous: optional conventions (frontmatter, `_meta/taxonomy.md`, `log.md`, etc.) are detected by file presence *within that space*. Parent conventions do not propagate to children. Each space picks its own tier independently — a Tier 3 root may contain a Tier 1 space and vice versa.

**Scope-root operations.** When a tool operates on a specific space (the wiki, or a space the user named), every convention check happens at *that scope's root* — not at an ancestor. The `log.md` appended to is the one at the scope being operated on. The taxonomy enforced is the one at that scope. The `.manifest.json` consulted is that scope's. Skip the marker at that scope and the corresponding step degrades for that scope only.

## Owned vs external

Per [`AGENTS.md / Sharing & nesting`](AGENTS.md#sharing--nesting), tools distinguish *owned* spaces (yours) from *external* spaces (mounts you don't own). Detection heuristic, in order:

1. Path is under `<wiki>/shared/` → external.
2. Path is a git submodule (in `.gitmodules`) whose origin URL doesn't match the wiki's own origin → external.
3. Path is a symlink whose realpath resolves outside the wiki tree → external.
4. Otherwise → owned.

**Read operations** (search, status, audit) cross owned spaces by default. External spaces are visited only when the user explicitly names one or asks to include all.

**Write operations** stay within the targeted space. Other spaces — owned or external — are written to only with explicit instruction.

**Traversal safeguards** (when crossing into other spaces):
- Track visited realpaths (resolve symlinks) to break cycles.
- Skip broken symlinks and uninitialized git submodules with a one-line notice; don't error out on them.
- Refuse `..` traversal above the wiki root. External spaces reached through a legitimate `## Spaces` entry ARE in scope when the user opts in — the prohibition is on ascending lexically, not on cross-mount realpaths.

---

## Sharing & permissions

For shared or collaborative spaces, the recommended mechanism is **git repositories**. Each shared space is its own git repo; embed it in a parent via git submodule, clone, or symlink (see [`AGENTS.md` / Sharing & nesting](AGENTS.md#sharing--nesting) — all FS mechanisms remain valid; this section recommends one).

**Two-gate write protection.** Tools should avoid accidental out-of-scope writes; sharing-aware tools should preflight ownership before mutating git-backed spaces they don't own.

1. **First gate (always on).** The trust scope clause in [`AGENTS.md`](AGENTS.md#sharing--nesting) defaults *write operations* to the targeted space only. Other spaces — owned or external — are written to only when the user's request explicitly includes them (naming a space, asking for all, or any clear instruction). Read operations follow their own rule: they cross owned spaces by default; external spaces require opt-in. Both rules are scope-based, not ownership-based — and together form the primary safety net.

2. **Second gate (only if backed by git).** Push access on the remote is the de facto upstream-publication permission. Without push access, local commits succeed but `git push` fails — changes never reach collaborators. This is a publication backstop, not a write-time check.

**Honest caveat.** Push-as-permissions is a *late* check: local commits succeed; only the push fails. For agent-driven workflows this can surprise the user hours later. Always rely on the trust scope (gate #1) as the primary protection; treat push permissions as the safety net.

**For local-only or private wikis,** git is not required. A folder with `index.md` is a complete wiki. Add git when you decide to share or back up.

**Submodules.** When nesting shared spaces as git submodules:
- Cloners need `git clone --recursive` (or `git submodule update --init` after a plain clone). Mention this in your wiki's `index.md` if you use them.
- Submodules pin a SHA; the parent sees the pinned version until `git submodule update --remote` advances the working tree.
- After advancing, the new SHA is only local until you commit the parent repo's updated submodule pointer (gitlink) and push.
- GitHub release ZIPs do NOT include submodule contents.

---

## `index.md`

**If present:** This folder is a space (per the [spec](AGENTS.md)). `index.md` typically grows three sections as the space grows:

- **`## What this space is`** — opening paragraph, plain prose. The space's own description; preserved across regenerations.
- **`## Items`** — optional, purely human-facing navigation: a hand-picked landing list of files or folders worth surfacing. Tools never read or write it.
- **`## Spaces`** — the navigation map: every space directly inside this one, listed once. The convention: the parent owns the link entry, the child owns its content. Adding a space inside means adding an entry here in the same change; removing a space means removing the entry. Tools rely on `## Spaces` being complete to traverse the tree.

Entries in `## Items` and `## Spaces` are markdown bullet lists, one per line. Order is the author's choice (typically navigational, not alphabetical):

- `## Items` — `[label](relative/path)` or `[[wikilink]]`, with an optional ` — short description`. Files and plain folders (folders without their own `index.md`) both belong here when worth surfacing. Hidden control files (`.manifest.json`, `_meta/...`, `_template.md`) are conventionally omitted unless a human reader needs to navigate to them.
- `## Spaces` — `[label](sub-folder/index.md)`, with an optional ` — short description`.

`## Items` carries no contract — it is human-maintained and tools never touch it. `## Spaces` is the opposite: meant to be exhaustive, so tools maintain it and flag sub-folders with `index.md` that aren't listed.

Skip any of these sections and `index.md` still marks the folder as a wiki. Tools that lean on the convention degrade where it isn't followed — your wiki is still your wiki.

**If absent:** This folder is not a wiki. Tools refuse to operate.

---

## `log.md`

**If present:** Tools append one line per operation. Format:

```
- [TIMESTAMP] OPERATION key=value key=value
```

`TIMESTAMP` is UTC ISO-8601. `OPERATION` is uppercase verb (`SEARCH`, `UPDATE`, `TEND`). Keys are operation-specific.

**If absent:** Tools skip logging. No log file is created automatically; create it (or run `wiki-spaces init <path> --with log.md`) to opt in.

---

## `hot.md`

**If present:** Free-form scratchpad for current active work. Tools may read it for context (e.g., `wiki-search` may surface its mentions) but never rewrite it. Users own its content.

**If absent:** Tools ignore. No proxy file is created.

---

## `.manifest.json`

**If present:** Project sync state. Schema:

```json
{
  "projects": {
    "<project-slug>": {
      "source_cwd": "/abs/path/to/source/repo",
      "last_synced": "2026-05-14T10:30:00Z",
      "last_commit_synced": "abc123",
      "pages_in_vault": 12
    }
  }
}
```

`last_commit_synced` is `null` when the source has no git. `wiki-update` reads it to skip unchanged sources and writes it after each sync.

**If malformed:** Tools warn once, treat the file as absent for this run, and refuse to overwrite it until the user repairs or removes it.

**If absent:** `wiki-update` performs a full scan on every sync. No project tracking.

---

## `_meta/taxonomy.md`

**If present:** Canonical tag vocabulary. Applies to YAML frontmatter `tags:` fields. `wiki-tend` normalizes those to the canonical list, suggests adding genuinely new tags that appear on 2+ pages, and rejects unknown one-off tags with a closest-match suggestion. `wiki-update` consults it before assigning tags. Inline `#tag` syntax outside frontmatter is not normalized.

Document shape:

```markdown
# Tag Taxonomy

Constraints: max 5 tags per page, lowercase/hyphenated.

## Domain Tags

| Tag | Purpose | Aliases |
|---|---|---|
| `python` | Python language, ecosystem | |
...

## Type Tags

| Tag | Purpose |
|---|---|
| `how-to` | Step-by-step procedure |
...
```

Aliases are mappings the normalizer uses to rewrite non-canonical tags to canonical form.

**If absent:** Tags are free-form. `wiki-tend` skips tag normalization with a notice; `wiki-update` does not enforce a vocabulary.

---

## `_template.md`

**If present in any folder:** New pages created by `wiki-update` in that folder use this file as their boilerplate (frontmatter + body skeleton). The closest ancestor `_template.md` wins.

**If absent:** New pages are created from the section "Page template" below if frontmatter is in use, otherwise as bare markdown.

---

## Frontmatter schema

*Part of the [Knowledge-capture pack](#knowledge-capture-pack).*

**If used (any content page in the wiki has YAML frontmatter):** The opt-in schema is:

```yaml
---
title: >-
  Page Title
category: <one of your categorical layout values>
tags: [tag1, tag2]
aliases: [alternate-name]        # optional
sources: [project-name, url]
summary: >-
  ≤200 chars; enough to decide whether to open the page.
created: 2026-05-14T00:00:00Z
updated: 2026-05-14T00:00:00Z
---
```

Timestamps are UTC ISO-8601. `>-` folded scalar avoids YAML quoting issues for `title` and `summary`. Frontmatter syntax is owned by [obsidian-markdown](vendor/kepano/obsidian-markdown/SKILL.md).

**Mixed adoption is allowed.** A wiki may have some content pages with frontmatter and some without; the convention is per-page, not per-wiki. `wiki-tend` audits required-field completeness only on pages that already have frontmatter — it never flags a page as "missing frontmatter." Special files (`index.md`, `log.md`, `hot.md`, `.manifest.json`, `_meta/taxonomy.md`, `_template.md`) are exempt.

**If absent (no page in the wiki has frontmatter):** `wiki-tend` skips frontmatter checks. `wiki-update` writes plain markdown pages.

---

## Page template

*Part of the [Knowledge-capture pack](#knowledge-capture-pack).*

**If used:** Body structure for content pages:

```markdown
# Page Title

One-paragraph summary.

## Key Ideas

- A fact explicitly stated by the source or codebase.
- A generalization drawn from the source. %%inferred%%
- A claim where sources disagree. %%ambiguous%%

## Open Questions

Unresolved items.
```

**If absent:** Pages are free-form.

---

## Provenance markers

*Part of the [Knowledge-capture pack](#knowledge-capture-pack).*

**If used:** Inline markers attached to claims indicate epistemic status. The syntax is Obsidian's comment form — invisible in rendered preview, parseable from raw markdown.

| State | Marker | Meaning |
|---|---|---|
| Extracted | *(no marker)* | Stated directly by source, docs, or code |
| Inferred | `%%inferred%%` | Synthesized or implied — not stated directly |
| Ambiguous | `%%ambiguous%%` | Sources disagree or evidence is unclear |

Place the marker at the end of the claim it qualifies (typically end of sentence or end of list item). Obsidian renders `%% ... %%` as nothing; tools parse it as a trailing tag.

Unmarked claims carry no enforced provenance — the convention treats them as extracted by default, but nothing in the tooling verifies the distinction. `wiki-update` applies markers when capturing; `wiki-tend` does not enforce them.

**If absent:** No provenance is tracked; all claims are unmarked.

---

## Categorical layout

**If used:** A folder convention for placing pages by kind. There's no canonical shape — your wiki's layout comes from what it's for. Pick what fits, mix shapes, or invent your own.

A few example shapes:

| Use case | Common top-level folders |
|---|---|
| **Developer notebook** | `concepts/`, `entities/`, `skills/`, `projects/<name>/` |
| **Research wiki** | `papers/`, `topics/`, `methods/`, `datasets/`, `projects/<name>/` |
| **Writing project** | `drafts/`, `characters/`, `worldbuilding/`, `notes/`, `archive/` |
| **Recipe collection** | `recipes/`, `ingredients/`, `techniques/`, `meal-plans/` |
| **Personal knowledge** | `journal/`, `learning/`, `contacts/`, `places/`, `interests/` |
| **Team reference** | `runbooks/`, `decisions/`, `services/`, `people/`, `clients/` |

Project-scoped content nests inside the wiki's project-grouping folder (commonly `projects/<name>/<sub-folder>/`, but `clients/<name>/` or `work/<name>/` follow the same pattern). Shared / external content typically lives under `shared/<name>/` (per `## Sharing & permissions`). `_archives/` is a conventional name for retired content / snapshots.

Slugs for new pages are lowercase, hyphen-separated, ≤50 chars, descriptive.

**Self-documenting layouts.** A child space (a folder with its own `index.md`) can carry a one-line "what goes here" description in its parent's `## Spaces` entry — `wiki-update` reads those descriptions when classifying new content. Plain folders (no `index.md`) have no such entry and route by folder name alone, so name them concretely (`recipes/` not `stuff/`) and routing still works; a description just makes it more precise. `## Items` is human-only and never consulted for routing.

`wiki-update`'s classification follows folder-name semantics first, then descriptions: a "sourdough recipe" matches `recipes/`, a "character bio" matches `characters/`, a "Python typing pattern" matches `concepts/` (or `notes/`, or whatever your wiki uses). When two folders are equally plausible the skill surfaces both options before writing; when none fit it asks, optionally creating a new folder for content that represents a recurring kind. Project-scoped content (identified by the user's intent — CWD is only a hint that disambiguates *which* project, never the trigger for project-vs-global) lands under whichever folder the wiki uses to group per-project content (`projects/`, `clients/`, `work/`, etc.).

**If absent (no top-level folders at the wiki root other than `_meta/`, `_archives/`, `.git/`, or hidden directories):** `wiki-update` writes pages flat at the wiki root or asks the user where to place. `wiki-tend`'s cross-category scoring is skipped.

---

## Retrieval primitives

Cost-tiered lookup table for `wiki-search`. Use the cheapest primitive that answers the question; escalate only when it cannot.

| Need | Primitive | Cost |
|---|---|---|
| Page exists? Title/tags? | `index.md` scan or grep frontmatter | Cheapest |
| 1–2 sentence preview | `summary:` frontmatter field | Cheap |
| Specific claim or section | `grep -A 10 -B 2 "<term>" <file>` (or your harness's grep tool) | Medium |
| Full page content | read the file | Expensive |

Grep-style search is preferred over full reads. Full reads are capped at 3 candidates per query.

### Recommended search backends

For wikis bigger than a few dozen pages, grep alone misses semantic matches and aliases. When the harness has a markdown-aware search tool available, `wiki-search` prefers it over raw grep:

| Backend | When to use | Notes |
|---|---|---|
| **[qmd](https://github.com/anteew/qmd) MCP** | Recommended primary. Local search over markdown with BM25 + semantic vectors + HyDE. | Used by Andrej Karpathy's [LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). MCP-installable into Claude Code, Codex, and similar harnesses. |
| Harness-native search MCP | Use what's already in the harness (e.g., the harness's built-in file search). | Beats grep when it understands markdown headings + frontmatter. |
| **Ripgrep** (`rg`) or the harness's grep tool | Universal fallback. Always available. | Fast keyword search; misses semantics and aliases. |

`wiki-search` checks for the recommended backends in order and uses the first one it finds; otherwise it falls back to grep. Tools should never *require* a specific backend — the wiki is plain markdown and any retrieval method that reads files works.

---

## Linking rules

Wikilink and markdown-link syntax: see [obsidian-markdown](vendor/kepano/obsidian-markdown/SKILL.md). This section covers usage convention, not syntax.

- Add up to 2 relevant wikilinks per page; never force irrelevant links.
- Link the first natural mention only. Skip mentions inside code blocks or frontmatter.
- Use the shortest link that resolves unambiguously.
- `wiki-tend`'s cross-link pass scores each candidate link: exact name match (+4), partial name match (+1), shared tags ≥2 (+2), same project (+2), cross-category (+2); a link is applied at score ≥3. This policy has one authoritative, tested implementation — `wiki_spaces._links.score_cross_link` / `should_link` — so the weights never drift between prose and behavior.

---

## Noise filter

*Part of the [Knowledge-capture pack](#knowledge-capture-pack).*

A heuristic for **knowledge-capture use cases** (research notes, technical wikis, developer notebooks) where the goal is "store what was hard to derive." Before writing a page, apply:

- **Code answers it?** Skip — wiki the reasoning, not what code says.
- **10-second search answers it?** Skip — wiki what took 30 minutes.
- **Needed in 3 months?** If you'd have to re-research, wiki it.
- **Already there?** Check `index.md`. Merge, don't duplicate.

**Skip this filter** for content-store use cases where every entry is intentional regardless of derivation cost — recipe collections, personal journals, contact lists, team runbooks, etc. The "would you re-derive this?" test doesn't apply when the wiki *is* the source of truth, not a memory aid.

---

## `.git`

Detection: a `.git` entry at the wiki root, whether a directory (regular repo) or a file (git submodule, worktree). Tools that need an authoritative answer should run `git -C <wiki-root> rev-parse --is-inside-work-tree`.

**If present:** The wiki is also a git repository. Tools may surface git context (current branch, uncommitted changes, ahead/behind upstream) in their reports when relevant. Tools NEVER auto-commit or auto-push; all git operations are user-driven. See [`Sharing & permissions`](#sharing--permissions) for the recommended sharing pattern.

**If absent:** No git context surfaced. Tools operate on the filesystem directly.

---

## `.obsidian/` integration

**If present:** The wiki is intended to be opened in Obsidian. `wiki-tend`'s colorize step writes graph color groups into `.obsidian/graph.json` (only the `colorGroups` key; everything else preserved; backup written).

Default colorize mode is `by-tag` (top 10 tags by usage, default palette below). `by-category` colors the categorical layout folders. `custom` honors user-provided mappings.

Default palette (RGB ints) — tools may override:
```
[5142951, 15896107, 14767961, 7780786, 5873999,
 15583048, 11565217, 16751527, 10253663, 12234924]
```

**If absent:** Colorize step is skipped. The wiki is treated as a plain markdown directory.
