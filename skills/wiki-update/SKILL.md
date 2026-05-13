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

## Initialization

When step 1 of the procedure finds no usable wiki (config missing or wiki path invalid), drive an interactive scaffold before anything else. The full briefing lives in `references/SETUP.md` at the wiki-spaces repo; the short version:

1. **Ask what the wiki is for, in the user's own words.** A one- or two-sentence description. Don't show a menu — infer the layout from what the user describes. Use the patterns below as internal priors; when the description doesn't match cleanly, derive 3-6 folder names from the recurring kinds the user mentioned, default to no Standard Pack opt-ins (still offer them), and default git to "ask."

   | Pattern | Suggested layout | Standard Pack | Git |
   |---|---|---|---|
   | Developer notebook | `concepts/`, `entities/`, `skills/`, `projects/` | `log.md` + `_meta/taxonomy.md` + `.manifest.json` | yes |
   | Research wiki | `papers/`, `topics/`, `methods/`, `datasets/`, `projects/` | `log.md` + `_meta/taxonomy.md` | yes |
   | Writing project | `drafts/`, `characters/`, `worldbuilding/`, `notes/`, `archive/` | `hot.md` (current piece) | yes |
   | Recipe collection | `recipes/`, `ingredients/`, `techniques/`, `meal-plans/` | (none) | optional |
   | Personal knowledge | `journal/`, `learning/`, `contacts/`, `places/`, `interests/` | (none) | optional, often no |
   | Team reference | `runbooks/`, `decisions/`, `services/`, `people/`, `clients/` | `_meta/taxonomy.md` + `log.md` | yes |

   Recommendations, not rules — the user can deviate freely. Flat wikis (no folders) are fully valid.

2. **Ask where the wiki should live.** Absolute path. Default: `~/Wiki/`. Also ask the display name if it differs from the directory basename.

3. **Present the inferred proposal and accept adjustments in natural language.** Show the proposal in one block: folders + Standard Pack opt-ins + git. Don't enumerate internal opt-in files as a menu — the user shouldn't have to know what `log.md` or `.manifest.json` is to set up a wiki. If the user wants changes, take them in their own words (*"rename recipes to desserts"*, *"skip git"*, *"add tag tracking"*) and re-present. If they ask what an opt-in does (*"what's log.md?"*), answer in 1-2 sentences and continue. Flat wikis (no folders) are fully valid; the proposal can include "no folders" when the description warrants it.

Then run `uvx wiki-spaces init <wiki-path> [--name <display-name>] [--description <text>] [--with <opt-ins>] [--folders <names>] [--git]` (drop the `uvx` prefix if wiki-spaces was installed permanently via `uv tool install wiki-spaces` or `pip install wiki-spaces`). Pass the user's one-sentence description from step 1 as `--description` so their words land in `index.md`'s "What this space is" section. The command creates `index.md`, writes the opt-in files, creates each `--folders` directory at the wiki root (with a `.gitkeep` placeholder when `--git` is set), runs `git init -b main` when `--git` is set, and writes `wiki = <wiki-path>` to `~/.config/wiki-spaces/config` automatically. Omit `--folders` for a flat wiki. After init, return to step 1 of the Procedure.

## Procedure

1. **Read the config.** Open `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config`. Get the `wiki` path. If the config is missing, the `wiki` key is unset, or the path has no `index.md`, run [`## Initialization`](#initialization) first, then resume.
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
5. **Apply the noise filter** per CONVENTIONS / Noise filter — *only* for knowledge-capture wikis (research notes, developer notebooks, technical wikis where the goal is "store what was hard to derive"). Skip the filter entirely for content-store wikis (recipes, journals, runbooks, contact lists, curricula) where every entry is intentional regardless of derivation cost. In either case, merge into existing pages before creating new ones (check `index.md`).
6. **Classify and place.** Read the SCOPE root's `index.md` to learn the layout. Candidates come from `## Spaces` entries (sub-spaces, always directories) and from `## Items` entries that resolve to directories (file entries are not placement targets); bare top-level directories other than `_meta/`, `_archives/`, `.git/`, and hidden directories serve as the fallback when `index.md` lacks a map. **Exclude external spaces** from the candidate set — paths under `<wiki>/shared/`, foreign-origin submodules, and out-of-tree symlinks (per CONVENTIONS / Owned vs external) require explicit user opt-in before they receive writes. The result is `(folder, description-or-name)` candidates from owned spaces only.

   Match the incoming content to a candidate by semantic fit, folder names first and descriptions to disambiguate. CWD is a hint, never a trigger: the user's intent and the content itself decide project-vs-global; CWD only resolves *which* project's name to use when the intent is already project-scoped.
   - **Project-scoped content** — when the user's intent makes the content clearly about a specific project, look for a project-grouping candidate (a folder whose description mentions per-project content, or whose name matches `projects/` / `clients/` / `work/` or similar). Place under `<that-folder>/<project-name>/...`, creating the project sub-space if missing. If no project-grouping folder exists in the layout, ask the user whether to create one (and which name) or to write the content globally.
   - **Global content** — pick the best-fitting candidate: a sourdough recipe → a folder named or described for recipes; a character bio → `characters/`; a Python typing pattern → `concepts/` or `notes/`, whichever the wiki uses. A global concept captured from a project CWD still goes here, not under the project folder.
   - **Multiple candidates equally plausible** — pick the more specific if descriptions disambiguate; otherwise surface the candidates to the user before writing.
   - **No candidate fits** — ask the user. If the content represents a recurring kind, offer to create a new folder; that new folder's `## Spaces` entry (if it has its own `index.md`) flows through step 8. `## Items` entries are curated by the user and are mentioned in the confirmation message — never auto-added.
   - **Tier 1 wiki with no folders at all** — write at the wiki root or ask.

   Slugs are lowercase, hyphen-separated, ≤50 chars, descriptive. Mounting an external wiki as a space (e.g., `<wiki>/shared/team-foo/`) is a separate flow — see `references/MOUNT.md`.
7. **Write pages.**
   - **New pages.** Use the closest `_template.md` if any; otherwise the page template from CONVENTIONS if frontmatter is in use; otherwise plain markdown. Apply provenance markers (per CONVENTIONS) on inferred or ambiguous claims when in use. Add up to 2 relevant wikilinks per CONVENTIONS / Linking rules.
   - **Updates.** Merge new info; preserve manual content; update `updated:` timestamp; deduplicate `sources:`. Don't overwrite unrelated sections.
   - **Write cap.** If more than ~10 pages would change, summarize the plan and ask before writing.
8. **Update tracking.** Per CONVENTIONS / `index.md`, `## Items` is curated and `## Spaces` is exhaustive:
   - **`## Items` (curated).** Never auto-add new entries — the user owns this list. Only REMOVE entries pointing to files you deleted in this operation. When creating a notable new page the user likely wants surfaced, mention the path in your confirmation message and let the user decide whether to add it.
   - **`## Spaces` (exhaustive when present).** If you created a new space (a folder with its own `index.md`), find the **nearest ancestor space** — the wiki root, or an intermediate space whose folder carries an `index.md`. Plain grouping folders without `index.md` are not tier-bearing and are skipped on the walk up. Detect that ancestor's tier (per AGENTS.md / Tiers). If it has `## Spaces` (Tier 2 or above), add the new entry there in the same operation — the navigability contract holds. If it doesn't have `## Spaces` (Tier 1), it's not navigable; ask the user whether to upgrade it (creating `## Spaces` and adding the entry) or leave the new space unlisted. When you remove a contained space whose entry is listed, remove the entry.
   - **`.manifest.json` (if present):** update `source_cwd`, `last_synced`, `last_commit_synced`, `pages_in_vault`.
9. **Confirm.**

```
Updated wiki:
- Created: <paths>
- Updated: <paths>
- Mode: <project_sync|conversation|research>
```

## Logging

Append to `log.md` only if it exists at the **scope root** (the wiki for default operations; the targeted space if the user named one — per CONVENTIONS / Per-space convention auto-detection):

```
- [TIMESTAMP] UPDATE mode=<mode> project=<name|-> pages_updated=X pages_created=Y
```
