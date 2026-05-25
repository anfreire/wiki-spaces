---
name: wiki-update
description: Create or update content in the user's canonical wiki. Use when the user says "update wiki", "sync project", "save this", "capture this", "store this research", or wants to distill knowledge from a project, conversation, or research session.
---

# Wiki Update

Extract durable knowledge from the current source (project, conversation, or research) and place it in the user's canonical wiki. Apply the noise filter when the wiki is a knowledge-capture use case (per CONVENTIONS / Noise filter); merge before creating; respect whatever conventions the wiki has adopted.

## Defers to

- Spec: `AGENTS.md` at the wiki-spaces repo (path in `~/.config/wiki-spaces/config` `repo` key).
- Conventions: `CONVENTIONS.md` at the same repo. Cited sections below: Categorical layout, Frontmatter schema, Page template, Provenance markers, Linking rules, Noise filter, `.manifest.json`.
- Markdown syntax: kepano's `obsidian-markdown` skill — installed alongside this one in your harness's skills directory.
- Deeper docs: `references/SETUP.md` (initialization), `references/MOUNT.md` (mounting external wikis as spaces), `references/EXAMPLES.md` (canonical topology examples) — at the wiki-spaces repo.
- Traversal model: contract-first per AGENTS.md / The navigation contract. Use `wiki-spaces space list` / `wiki-spaces space files` for structured traversal — these reflect `## Spaces`, malformed-href policy, and external trust scope deterministically.

## Initialization

When step 1 of the procedure finds no usable wiki (config missing or wiki path invalid), drive the interactive scaffold inline before doing anything else. Follow [`references/SETUP.md` § Branch A](../../references/SETUP.md) — read it from the repo (the `repo` config key points at it once installed; the canonical raw GitHub URL is the fallback when `repo` is unknown). The full briefing is one page; follow it verbatim. Keep the interview to two exchanges (gather, confirm), run every command yourself, and return to step 1 of the Procedure once `wiki-spaces init` has registered the wiki.

## Procedure

1. **Resolve the target wiki** per [`CONVENTIONS.md` / Discovery via config](../../CONVENTIONS.md#discovery-via-config) — explicit path → `wiki` config key → nearest CWD ancestor whose `index.md` contains `## Spaces` → inline setup (run [`## Initialization`](#initialization), then resume). When CWD was the source (config missing), mention once in the confirmation: "Wrote to the wiki at `<path>` (found via CWD; no config registered). Run `wiki-spaces init <path>` to make this the default target."
2. **Detect adopted conventions at the SCOPE root** (the canonical wiki for default operation; the targeted space if the user named one): `_meta/taxonomy.md`, `.manifest.json`, frontmatter (scan content pages until one with frontmatter is found, or confirm none), categorical layout (any top-level folder at the SCOPE root other than `_meta/`, `_archives/`, `.git/`, or hidden directories). Templates (`_template.md`) are detected later, at write time, by walking up from the chosen destination — see step 7. Spaces are autonomous — never inherit detection from a parent. Skip steps that depend on absent conventions.
3. **Detect mode.** Mode comes from the user's intent and the content; CWD is a hint that disambiguates *which* project when the intent is already project-scoped, never a mode-detection trigger on its own.
   | Mode | Trigger | Input |
   |---|---|---|
   | Project sync | "update wiki for <project>", "sync this project", explicit project naming | project files, git log |
   | Conversation capture | "save this", "capture this", "file this" | current conversation |
   | Research capture | "store this research", "add to knowledge base" | research findings from the session |
4. **Extract knowledge** per mode:
   - **Project sync.** Identify the project from CWD. If `.manifest.json` is present and the project has a prior `last_commit_synced`, run `git log <last_commit>..HEAD --oneline` and only consider changed files. If nothing distillable, report "nothing to update" and stop. Scan for: architecture decisions and rationale, patterns discovered, tool/API wiring, key abstractions, trade-offs, experiment results.
   - **Conversation capture.** Extract durable conclusions, decisions, findings. Ignore logistics. Write conclusions directly; never summarize the chat ("X works by..." not "the user asked about X and we discussed...").
   - **Research capture.** Identify what was researched (tool, concept, technique). Extract findings that took effort and would be expensive to re-derive.
5. **Apply the noise filter** per CONVENTIONS / Noise filter — *only* for knowledge-capture wikis (research notes, developer notebooks, technical wikis where the goal is "store what was hard to derive"). Skip the filter entirely for content-store wikis (recipes, journals, runbooks, contact lists, curricula) where every entry is intentional regardless of derivation cost. In either case, **deduplicate against existing pages before creating new ones — but do not rely on `index.md` to enumerate them.** `## Items` is non-contractual (tools never write it), so `index.md` is an unreliable index of what's on disk. Run the `wiki-search` candidate pass instead — enumerate consumer-visible pages via `wiki-spaces space files --json` (the contract walker; respects `## Spaces`, hides unregistered drift) and rank by filename / path-segment / frontmatter overlap with the content being captured. When a near-match exists, prefer merging into the existing page (with an `updated:` bump) over creating a new one.
6. **Classify and place.** Compute placement candidates via the three-step classifier:

   1. **Registered owned spaces.** Run `wiki-spaces space list --json` to enumerate every owned space the contract knows about, with `label` + `description` per entry. Wiki-root-relative paths with no `/` are top-level candidates.
   2. **External paths to exclude.** Run `wiki-spaces space list --include-external --include-boundaries --json` and collect the `path` of every entry where `external` is true. This includes external boundary folders WITHOUT `index.md` (foreign submodules, escaping symlinks) — the deterministic exclusion list per CONCEPT trust-scope contract.
   3. **Plain top-level folders.** `ls -1d <wiki>/*/` (or the harness's directory listing). Exclude reserved names (`_meta/`, `_archives/`, `.git/`, `.obsidian/`, anything starting with `.`) AND every top-level path from step 2's external set.
   4. **Classify each remaining top-level folder.** Appears in step 1 → registered space (placement candidate with description). Otherwise contains `index.md` → drift (SKIP — `space audit` will report it). Otherwise → plain folder (placement candidate, name-only).

   Steps 1–4 are deterministic mechanics: when in doubt, follow them verbatim. The result is `(folder, description-or-name)` candidates from owned spaces only.

   Match the incoming content to a candidate by semantic fit, folder names first and descriptions to disambiguate. CWD is a hint, never a trigger: the user's intent and the content itself decide project-vs-global; CWD only resolves *which* project's name to use when the intent is already project-scoped.
   - **Project-scoped content** — when the user's intent makes the content clearly about a specific project, look for a project-grouping candidate (a folder whose description mentions per-project content, or whose name matches `projects/` / `clients/` / `work/` or similar). Place under `<that-folder>/<project-name>/...`, creating the project sub-space if missing. If no project-grouping folder exists in the layout, ask the user whether to create one (and which name) or to write the content globally.
   - **Global content** — pick the best-fitting candidate: a sourdough recipe → a folder named or described for recipes; a character bio → `characters/`; a Python typing pattern → `concepts/` or `notes/`, whichever the wiki uses. A global concept captured from a project CWD still goes here, not under the project folder.
   - **Multiple candidates equally plausible** — pick the more specific if descriptions disambiguate; otherwise surface the candidates to the user before writing.
   - **No candidate fits** — ask the user. If the content represents a recurring kind, offer to create a new folder; if that folder gets its own `index.md`, its `## Spaces` entry flows through step 8.
   - **Flat wiki (no folders at all)** — write at the wiki root or ask.

   Slugs are lowercase, hyphen-separated, ≤50 chars, descriptive. Mounting an external wiki as a space (e.g., `<wiki>/shared/team-foo/`) is a separate flow — see `references/MOUNT.md`.
7. **Size discipline (pre-write check).** Before writing any page, materialize the FULL projected post-write content as a string (for an Edit, apply the edit in memory first). Compute `projected_chars = len(text_after_stripping_frontmatter)`. Look up the cap for this path via `CONVENTIONS / _meta/limits.md` (defaults: `index.md` = 5,000; `log.md` = 100,000; any other `*.md` = 15,000). If `projected_chars > cap`, **reject the write** unless the projected size is strictly smaller than the current on-disk size (the "shrinking write" escape hatch for legacy bloat). On rejection, do NOT silently truncate; surface the projected size and the cap to the producer and pick a remediation in this order:
   1. **Split** — if the page has H2 boundaries that read as distinct topics, split sections out as new siblings (or children of a new space; see step 2). Move the relevant H2 sections verbatim; the original page becomes a hub with in-body wikilinks to the new pages.
   2. **Promote, then split by hand** — when the page is genuinely a hub of multiple distinct topics, run `wiki-spaces space promote <path>` to turn it into `<path>/index.md`, then move the H2 sections into sibling content files under the new space yourself. The CLI handles the mechanical move and link rewrites; splitting content is authorship judgment.
   3. **Summarize** — only when neither split nor promote applies (the page IS already dense). Identify entries to merge or remove; the rejection forces consolidation now, not "later." This is the size discipline that prevents day-30 bloat.

   For `index.md` rejection specifically: never use `space promote` (refused by the CLI on `index.md`). Instead, push detail down into a relevant child space's `index.md` or a new content page, or collapse verbose `## Items` entries to wikilink-only references.

8. **Write pages.**
   - **New pages.** Use the closest `_template.md` if any; otherwise the page template from CONVENTIONS if frontmatter is in use; otherwise plain markdown. Apply provenance markers (per CONVENTIONS) on inferred or ambiguous claims when in use. Add up to 2 relevant wikilinks per CONVENTIONS / Linking rules.
   - **Updates.** Merge new info; preserve manual content; update `updated:` timestamp; deduplicate `sources:`. Don't overwrite unrelated sections.
   - **Write cap.** If more than ~10 pages would change, summarize the plan and ask before writing.
9. **Update tracking.** Per CONVENTIONS / `index.md`, `## Spaces` is the exhaustive navigation contract:
   - **`## Spaces` (exhaustive, required).** If you created a new space (a folder with its own `index.md`), prefer the CLI: `wiki-spaces space add <relative-path>` creates the folder, writes a minimal `index.md` (with `## Spaces` from t=0), and updates the nearest ancestor's `## Spaces` automatically — auto-inserting the section into a bare-`index.md` ancestor when missing. Use `wiki-spaces space remove <relative-path>` to delete in symmetric fashion. No prior setup required: `space add`, `space remove`, `space mount`, and `space promote` all maintain the navigation contract for you. If the CLI is unavailable entirely, do it manually: find the **nearest ancestor space** — the wiki root, or an intermediate space whose folder carries an `index.md`. Plain grouping folders (no `index.md`) are skipped on the walk up. Add the entry under that ancestor's `## Spaces`. When you remove a contained space whose entry is listed, remove the entry.
   - **`.manifest.json` (opt-in; user must scaffold first).** When the file is present, update it via the inline `flock` snippet in [`CONVENTIONS.md` § How to safely update `.manifest.json`](../../CONVENTIONS.md#how-to-safely-update-manifestjson). The skill reads, validates, locks, writes-via-tempfile, and unlocks — same shape regardless of how many fields you set. Typed-field coercion (`pages_in_vault` → int, `last_commit_synced` → `null` literal when the source has no git) is the writer's responsibility; pass already-typed values to the helper. Refuse on absent: if `.manifest.json` does not exist, the user has not opted in — do NOT auto-create it. Refuse on schema-invalid: a malformed file is treated as absent for this run.
10. **Confirm.**

```
Updated wiki:
- Created: <paths>
- Updated: <paths>
- Mode: <project_sync|conversation|research>
```

## Promote to space

A `.md` file that has grown into multiple distinct topics, accreted siblings, or now represents a recurring kind is a candidate for promotion to its own space.

**When to consider promotion.** Any of:

- Page exceeds ~300 lines of body content.
- Page has 3+ H2 sections covering distinct sub-topics.
- Sibling pages have accreted around the topic (e.g. `strategy.md`, `strategy-backtest.md`, `strategy-screening.md`) that would read more naturally as children of a `strategy/` space.
- New content's intent suggests the existing page has become a hub.

**Procedure.**

1. Identify the file. Confirm it's not already an `index.md` and not in an external space.
2. Run `wiki-spaces space promote <path>` (wiki-root-relative). Preview with `--dry-run` first if the wiki has many cross-references.
3. The CLI:
   - moves the file to `<basename>/index.md`,
   - rewrites markdown links across the owned tree (path-aware: only links resolving to the promoted file are touched; hrefs recomputed relative to each linking file's directory so deep cross-links stay correct),
   - rewrites wikilinks pointing to the promoted file (all forms — bare, display, anchored, pathful — with display preserved),
   - adjusts the promoted file's outgoing relative links for its new depth (one extra `../`),
   - adds `aliases: [<basename>]` to the new `index.md` for forward-compatible wikilink resolution (skip with `--skip-aliases` if another page already claims the alias),
   - ensures the new `index.md` has `## Spaces` from t=0 — matches `space add`,
   - registers the new space's `## Spaces` entry in the nearest ancestor (uses the file's frontmatter `summary` for the description if present).
4. Read the new `index.md`. If sections read like standalone children, capture them as separate `.md` files under the new space in a follow-up `wiki-update` cycle. The CLI deliberately does not split content — that's authorship, not mechanics.

**Atomicity.** The CLI snapshots every affected file to a system tempdir (outside the wiki tree) before mutating disk and restores from the snapshot if anything fails. Works on both git-tracked and untracked wikis. The snapshot dir is always cleaned, success or failure.

**Refuses if.** Target dir exists with content; path is external (or descends from an external scope); another owned page already claims the alias `<basename>` case-insensitively (use `--skip-aliases` to bypass). When the parent's `index.md` lacks `## Spaces`, promote auto-inserts the heading inside the locked ancestor mutation — no refusal, no prior setup needed.

## Logging

Append a structured entry via the CLI when `log.md` exists at the **scope root** (the wiki for default operations; the targeted space if the user named one — per CONVENTIONS / Per-space convention auto-detection):

```sh
wiki-spaces space log UPDATE --field mode=<mode> --field project=<name|-> --field pages_updated=X --field pages_created=Y
```

The CLI prepends the ISO-8601 UTC timestamp; you supply only the operation name and the key=value pairs. Use `--raw "<full line>"` for custom shapes. `space log` wraps `_limits.append_log_with_rotation`, holding a `fcntl.flock` for the whole check-rotate-append sequence — concurrent skill invocations never lose lines, and rotation to `log.archive-<YYYYMMDD-HHMMSS>.md` happens automatically when the file would exceed its cap (default 100,000 chars). Logging is opt-in: when `log.md` is absent, the call refuses; pass `--create` only when the user explicitly opted into logging this session (or run `init <wiki> --with log.md` once). Add `--wiki <path>` when the scope is a named sub-space.
