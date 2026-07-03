---
name: ws-tend
description: Maintain the user's canonical wiki's health. Use when the user says "tend wiki", "clean wiki", "audit wiki", "fix tags", "normalize tags", "link pages", "cross-reference", "color graph", "wiki status", or wants a health check, tag audit, cross-linking pass, or graph colorization.
---

# Wiki Tend

One-shot maintenance: status, audit, normalize (tags + cross-links), colorize. Report first, repair only what's safe, and degrade gracefully wherever a convention the step depends on is absent.

<!-- ws:core -->
## The wiki model

A wiki is a folder whose `index.md` carries a `## Spaces` heading. `## Spaces` is the navigation contract: every space directly inside is listed there as `- [label](path/index.md) — description`, and tools traverse only what it lists. Spaces nest recursively; each space is itself a wiki one level down. Plain folders (no `index.md`) just group files. The markdown dialect is Obsidian — wikilinks, frontmatter, callouts, embeds; the companion `obsidian-markdown` and `obsidian-bases` skills cover the syntax.

## Resolving the wiki

Resolution order: an explicit path from the user → the nearest CWD-ancestor folder that is a wiki → the `wiki` key in `~/.config/wiki-spaces/config`. The user's words override the mechanics: "my wiki" means the configured one even when CWD sits inside another wiki (a company repo, say). When a CWD wiki and a different configured wiki both exist, announce which root you resolved; ask once if intent is ambiguous. Never silently operate on the wrong wiki.

## The bundled script

`scripts/ws.py` sits next to this SKILL.md — stdlib python3, zero dependencies. Invoke it by absolute path (your working directory is usually elsewhere):

- `python3 <skill-dir>/scripts/ws.py list --wiki <root>` — spaces reachable via the `## Spaces` contract (`--external` to cross mounts).
- `… files --wiki <root>` — markdown files reachable via the contract.
- `… grep <pattern> [-i] --wiki <root>` — regex line search over those files; prints `rel:line: text`, exits 1 on no match.
- `… check-size <target> [--stdin] --wiki <root>` — cap verdict for a file; pipe planned content with `--stdin` to check before writing.
- `… audit [--fix] --wiki <root>` — drift, broken links, over-cap or unreadable files, unhealthy mounts; `--fix` completes half-declared owned spaces (registers unlisted valid children, inserts the heading a registered child lacks) and never promotes an undeclared folder.

Trust the script's output over re-deriving structure by hand; it is the deterministic view of the contract. Stdout is data; stderr carries the resolved root and `note:` advisories naming whatever a walk skipped — relay them when they could change the answer.

## Trust scope and size discipline

Owned vs external is relative to the resolved root: anything under a folder named `shared/` (at any depth), a foreign-origin git submodule, or a symlink escaping the tree is external. Reads cross owned spaces by default and enter external ones only when the user explicitly asks. Writes stay inside the targeted space; any other space — owned or external — is written only on explicit instruction.

Caps are UTF-8 bytes including frontmatter, keyed by basename: `index.md` 5000, `log.md` and `hot.md` 100000, any other `*.md` 15000. A `_meta/limits.md` of plain `basename: bytes` lines overrides them — the nearest one at or above the file wins, like `_template.md`, and the literal name `*.md` re-caps the content-page catch-all. Run `check-size` before writing — a write that shrinks an over-cap file reports `ok`, progress. An overflow is a signal about shape, not just size: distill the page or reshape the space — never truncate.

Conventions are opt-in per wiki. Read the markers present at the scope root — `log.md`, `_meta/taxonomy.md`, `_meta/limits.md`, `_meta/ignore.md`, frontmatter on pages, `_template.md`, `hot.md`, `.obsidian/`, `.git` — and degrade gracefully when one is absent. If `log.md` exists, append one line per operation:

```sh
printf '%s <OP> <details>\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> <root>/log.md
```
<!-- /ws:core -->

## Procedure

1. **Resolve the wiki** (core block) and announce the root when it came from CWD or could be ambiguous. A freshly scaffolded wiki has nothing to tend — say so plainly rather than inventing work.
2. **Detect conventions at the scope root** — the wiki by default, the targeted space when the user named one. Spaces are autonomous; never inherit detection from a parent. Skip every mode whose marker is absent.
3. **Pick the mode** from the user's words:
   | User says | Mode |
   |---|---|
   | "wiki status", "what's in my wiki" | status |
   | "audit", "health check", "what needs fixing" | audit |
   | "fix tags", "normalize tags" | normalize: tags |
   | "link pages", "cross-reference" | normalize: links |
   | "color graph", "colorize" | colorize |
   | "tend wiki", "clean wiki" | full sweep: status → audit → ask → normalize → colorize |

   Status and audit only report. Normalize and colorize preview their changes and ask before writing — unless the user already said "fix" or "apply".
4. **Status.** `list` + `files` for the shape (add `--external` only when the user opted in). Report: spaces and their one-line descriptions, page counts per top-level folder, tag counts and the top 10 by usage (when frontmatter is in use), the last `log.md` line (when present), and git status (when `.git` exists — read-only; never commit or push).
5. **Audit.** Run `audit` and relay its findings in its own vocabulary — `contract` (a bare index and whether it is registered, a near-miss heading, a malformed / duplicate / unregistrable entry, a dead second heading), `drift` (an on-disk space not listed / a listed entry with nothing on disk), `mount` (a registered external mount that is no longer a wiki), `broken` links, `over-cap` files, `unreadable` files, and informational `orphans`. The script is the source of truth for those; don't re-derive them. Add the judgment it can't make:
   - **Remediation suggestions.** For each over-cap file, suggest what its diagnosis calls for — distill (bloat) or reshape the space (siblings under a hub, a promotion, a regrouped layout), per `ws-update`'s overflow procedure. For broken links, locate the nearest-named page as the likely target. Orphans are facts, not errors — flag only ones that look unintentional.
   - **Frontmatter completeness** — only on pages that already carry frontmatter, only when the wiki uses a schema; mixed adoption is allowed.
   - **Structural prompts** — a page ≥80% of its cap with several distinct H2 sections is outgrowing its shape and is reshape-ready; a space whose index lists many accreted siblings wants a sub-space; an opt-in `hot.md` near its cap wants distilling into cold pages.
6. **Bounded repair (close-out).** After reporting, run exactly one `audit --fix` for the safe structural repairs (register unlisted valid children, insert the heading a registered child lacks), then re-audit and report the delta. Once, no loop. Everything else is author intent — malformed, duplicate, or unregistrable entries, near-miss headings, undeclared bare indexes, broken links, stale entries, unhealthy mounts, over-cap or unreadable files — and stays reported for the user (offer to fix as a normal edit if the user says go; an undeclared bare index is a promotion decision).
7. **Normalize: tags.** Only when `_meta/taxonomy.md` exists at the scope root. Over the scope's own pages (children spaces keep their own taxonomies — leave them out): flag non-canonical tags, apply alias mappings the taxonomy defines, flag over-tagged (>5) and untagged pages. Unknown tags on 2+ pages: suggest adding to the taxonomy; one-offs: suggest the closest canonical tag.
8. **Normalize: cross-links.** Build a registry of scope-own pages (names, titles, frontmatter aliases when present). Scan bodies for unlinked first mentions of registry entries and wrap the strongest matches as wikilinks — at most 2 new links per page, never inside code blocks or frontmatter, no self-links. Prefer inline linking; a trailing `## Related` section is the fallback when no natural mention exists.
9. **Colorize.** Only when `.obsidian/` exists: [references/colorize.md](references/colorize.md).
10. **Log** one `TEND` line per the core block when `log.md` exists, and close with the report.

## Output

Per mode: a status table; an audit report quoting the script's findings plus your suggestions; normalize tables (tag → action, link added → where); a colorize summary (groups written, backup path). End the full sweep with the one-line delta: `issues before → after; left for you: <n>`.
