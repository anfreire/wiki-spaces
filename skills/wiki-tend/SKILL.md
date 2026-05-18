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

1. **Resolve the target wiki**, in this order:
   1. Explicit path or named space from the user's request.
   2. The `wiki` value in `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config`, if that path has `index.md`.
   3. **CWD discovery** — the nearest ancestor of the current working directory containing `index.md`. This makes Tier 1 no-install wikis work without a config.
   4. If none of the above resolves to a folder with `index.md`, **drive the setup flow inline** before doing maintenance: read `<repo>/references/SETUP.md` (or fall back to the canonical URL https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md when `repo` is unknown) and follow its Branch A "Fresh install + scaffold" steps. The shorter equivalent is in [`wiki-update/SKILL.md` § Initialization](../wiki-update/SKILL.md#initialization). Once `wiki-spaces init` has registered the wiki, resume from step 1 of this procedure with the user's original request. (A freshly scaffolded wiki has nothing to tend — say so plainly: `status` reports the empty layout, `audit` finds zero issues. No need to invent work.)

   When CWD discovery was the source used (config missing), say so once in the report.
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
   - **Index consistency.** `## Spaces` only (Tier 2 contract per AGENTS.md): audited when the space has a `## Spaces` section. When present, flag both directions — listed entries with no space on disk, AND sub-folders with `index.md` that aren't listed. `## Items` is human-maintained and not audited.
6. **Normalize: tags.** Only if `_meta/taxonomy.md` present at the SCOPE root. Scan pages within the same scope (same contained-space pruning as step 4). Extract `tags` from frontmatter. Flag non-canonical, alias-mapped, over-tagged (>5), untagged. Replace alias tags only when taxonomy defines an explicit mapping. Unknown tags appearing on 2+ pages: suggest adding to taxonomy. Unknown one-offs: suggest closest canonical.
7. **Normalize: cross-links.** Per CONVENTIONS / Linking rules. Build a page registry from frontmatter (or filenames if no frontmatter) within the SCOPE (same contained-space pruning): name, title, aliases, tags. Scan body text for unlinked mentions of registry entries. Score each candidate by the CONVENTIONS / Linking rules weights and apply a link at score ≥3. Inline (preferred) by wrapping the first natural mention; fallback to a `## Related` section appended at page bottom. No self-links.
8. **Colorize.** Only if `.obsidian/` present. Default `by-tag` (top 10 tags by usage, default palette per CONVENTIONS / `.obsidian/` integration). Read `.obsidian/graph.json`; if missing, instruct the user to open the vault in Obsidian first. Back up to `.obsidian/graph.json.backup-<YYYYMMDD-HHMM>`. Replace **only** the `colorGroups` key; preserve everything else. Remind the user to reload Obsidian.
9. **Scope defaults.** Read operations (status, audit) descend through owned spaces by default; external spaces (shared mounts, foreign submodules, out-of-tree symlinks) are excluded unless named. Write operations (normalize, colorize) stay within the targeted scope; other spaces require explicit instruction. Trust scope per AGENTS.md / CONVENTIONS / Owned vs external.

## Output

Structured per mode: status table, audit report with counts and file paths, normalize tables (tags normalized, links added), colorize summary (mode, group count, backup path).

## Logging

Append to `log.md` only if it exists at the **scope root** (the wiki for default operations; the targeted space if the user named one — per CONVENTIONS / Per-space convention auto-detection):

```
- [TIMESTAMP] TEND mode=<status|audit|normalize|colorize|full> issues_found=N fixed=M
```
