---
name: wiki-tend
description: Maintain the user's canonical wiki's health. Use when the user says "tend wiki", "clean wiki", "audit wiki", "fix tags", "normalize tags", "link pages", "cross-reference", "color graph", "wiki status", or wants a health check, tag audit, cross-linking pass, or graph colorization.
---

# Wiki Tend

One-shot maintenance for the user's canonical wiki: status, audit (read-only), normalize (tags + cross-links), and colorize. Each step degrades gracefully when the convention it depends on is absent.

## Defers to

- Spec: `AGENTS.md` at the wiki-spaces repo (path in `~/.config/wiki-spaces/config` `repo` key).
- Conventions: `CONVENTIONS.md` at the same repo; this skill enforces nothing the wiki hasn't opted into.
- Markdown syntax: kepano's `obsidian-markdown` skill — installed alongside this one in your harness's skills directory.

## Procedure

1. **Read the config.** Open `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config`. Get the `wiki` path. If the config is missing, the `wiki` key is unset, or the path has no `index.md`, tell the user setup is needed and point them at the SETUP briefing — preferred path `<repo>/references/SETUP.md` if the `repo` key is set; fallback URL https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md if `repo` is also unknown. Then stop (this skill does not drive setup itself; `wiki-update` does).
2. **Detect adopted conventions at the SCOPE root** (the canonical wiki for default operation; the targeted space if the user named one): `log.md`, `_meta/taxonomy.md`, `.manifest.json`, frontmatter (scan content pages until one with frontmatter is found, or confirm none), categorical layout, `.obsidian/`. Spaces are autonomous — never inherit detection from a parent. Skip every mode whose required marker is absent.
3. **Mode detection.**
   | User says | Mode |
   |---|---|
   | "wiki status", "what's in my wiki" | `status` |
   | "audit wiki", "health check", "what needs fixing" | `audit` |
   | "fix tags", "normalize tags" | `normalize:tags` |
   | "link pages", "cross-reference" | `normalize:links` |
   | "normalize" | `normalize:all` |
   | "color graph", "colorize" | `colorize` |
   | "tend wiki" / "clean wiki" | full sweep: status → audit → ask → normalize → colorize |

   For `audit` and `status`: report only. For `normalize`, `colorize`, and full sweep: preview changes and ask before modifying unless the user explicitly said "fix" or "apply".
4. **Status.** Glob `**/*.md` within the SCOPE. For *read operations* (status, audit), descend through owned spaces; exclude *external* spaces (under `<wiki>/shared/`, git submodules with foreign origins, symlinks resolving outside the wiki tree — see CONVENTIONS / Owned vs external) unless the user opts in. For *write operations* (normalize, colorize), stay within the targeted scope; other spaces are written to only with explicit instruction. Always exclude `.obsidian/` and `_archives/`. Count pages per top-level folder; report `.manifest.json` synced projects (if present); count tags and top 10 by usage (if frontmatter in use); show last `log.md` entry (if present).
5. **Audit (report only).**
   - **Orphaned pages.** Pages with zero incoming wikilinks. Exclude `index.md` and `log.md` from both the orphan list and from incoming-link counts.
   - **Broken wikilinks.** Grep for `\[\[.*?\]\]` across the scope (use your harness's grep tool or `grep -rE`). Resolve by stripping `|display` and `#heading`. Check if a matching `.md` file or alias exists.
   - **Frontmatter field completeness.** On pages that already have frontmatter, check required fields per CONVENTIONS / Frontmatter schema. Pages without frontmatter are NOT flagged (mixed adoption is allowed). Special files exempt.
   - **Stale content.** Only if `.manifest.json` is present. Compare `updated:` to `last_synced`; flag project-scoped pages stale > 30 days.
   - **Index consistency.** Asymmetric per CONVENTIONS / `index.md`:
     - `## Items` (curated): flag only entries pointing to missing files; do NOT flag files-without-entries (the list is curated, not exhaustive).
     - `## Spaces` (Tier 2 contract per AGENTS.md): only audited if the parent has the `## Spaces` section (i.e., is at Tier 2 or above). When present, flag both directions — entries without folders AND sub-folders with `index.md` that aren't listed.
6. **Normalize: tags.** Only if `_meta/taxonomy.md` present at the SCOPE root. Scan pages within the same scope (same contained-space pruning as step 4). Extract `tags` from frontmatter. Flag non-canonical, alias-mapped, over-tagged (>5), untagged. Replace alias tags only when taxonomy defines an explicit mapping. Unknown tags appearing on 2+ pages: suggest adding to taxonomy. Unknown one-offs: suggest closest canonical.
7. **Normalize: cross-links.** Per CONVENTIONS / Linking rules. Build a page registry from frontmatter (or filenames if no frontmatter) within the SCOPE (same contained-space pruning): name, title, aliases, tags. Scan body text for unlinked mentions of registry entries. Score per the linking rules; apply links with score ≥3. Inline (preferred) by wrapping the first natural mention; fallback to a `## Related` section appended at page bottom. No self-links.
8. **Colorize.** Only if `.obsidian/` present. Default `by-tag` (top 10 tags by usage, default palette per CONVENTIONS / `.obsidian/` integration). Read `.obsidian/graph.json`; if missing, instruct the user to open the vault in Obsidian first. Back up to `.obsidian/graph.json.backup-<YYYYMMDD-HHMM>`. Replace **only** the `colorGroups` key; preserve everything else. Remind the user to reload Obsidian.
9. **Scope defaults.** Read operations (status, audit) descend through owned spaces by default; external spaces (shared mounts, foreign submodules, out-of-tree symlinks) are excluded unless named. Write operations (normalize, colorize) stay within the targeted scope; other spaces require explicit instruction. Trust scope per AGENTS.md / CONVENTIONS / Owned vs external.

## Output

Structured per mode: status table, audit report with counts and file paths, normalize tables (tags normalized, links added), colorize summary (mode, group count, backup path).

## Logging

Append to `log.md` only if it exists at the **scope root** (the wiki for default operations; the targeted space if the user named one — per CONVENTIONS / Per-space convention auto-detection):

```
- [TIMESTAMP] TEND mode=<status|audit|normalize|colorize|full> issues_found=N fixed=M
```
