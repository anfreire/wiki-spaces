---
name: ws-tend
description: Maintain the user's wiki's health. Use when the user says "tend wiki", "clean wiki", "audit wiki", "fix tags", "normalize tags", "link pages", "cross-reference", "color graph", "wiki status", or wants a health check, tag audit, cross-linking pass, or graph colorization.
---

# Wiki Tend

One-shot maintenance: status, audit, normalize (tags + cross-links), colorize. Report first, repair only what's safe, and degrade gracefully wherever a convention the step depends on is absent.

<!-- ws:core -->
## The wiki model

A wiki is a folder whose `index.md` carries a `## Spaces` heading. `## Spaces` is the navigation contract: every space directly inside is listed there as `- [label](path/index.md) — description`, and tools traverse only what it lists. Spaces nest recursively; each space is itself a wiki one level down. Plain folders (no `index.md`) just group files. The markdown dialect is Obsidian — wikilinks, frontmatter, callouts, embeds; the companion `obsidian-markdown` and `obsidian-bases` skills cover the syntax.

## Resolving the wiki

Resolution order: an explicit path from the user → the nearest CWD-ancestor folder that is a wiki → the `wiki` key in `~/.config/wiki-spaces/config`. The user's words override the mechanics: "my wiki" means the configured one even when CWD sits inside another wiki (a company repo, say). When a CWD wiki and a different configured wiki both exist, announce which root you resolved; ask once if intent is ambiguous. Never silently operate on the wrong wiki.

## The bundled script

`scripts/ws.py` sits next to this SKILL.md — stdlib python3 (3.9+), zero dependencies, read-only. It parses the contract, never the content: structure — traversal, scope, caps, drift — is the script's side; reading and judging meaning is yours. Invoke it by absolute path (your working directory is usually elsewhere):

- `python3 <skill-dir>/scripts/ws.py list --wiki <root>` — spaces reachable via the `## Spaces` contract, each with its entry description (`--external` to cross mounts).
- `… files --wiki <root>` — markdown files reachable via the contract.
- `… grep <pattern> [-i] --wiki <root>` — regex line search over those files; prints `rel:line: text`, exits 1 on no match. The sweep primitive: a link worklist, a tag inventory, an escaping-reference check are each a pattern plus your judgment on the hits.
- `… check-size <target> [--stdin] --wiki <root>` — cap verdict for a file; pipe planned content with `--stdin` to check before writing.
- `… audit --wiki <root>` — contract drift, entries crossing a space boundary, over-cap or unreadable files, unhealthy mounts. Findings name their repair where one is safe to name (a `missing entry` prints the exact line to add); apply repairs as ordinary edits and re-run the audit to verify — the script never writes.

Trust the script's output over re-deriving structure by hand. Stdout is data; stderr carries the resolved root (`audit` prints it as its stdout header instead) and `note:` advisories naming whatever a walk skipped — relay them when they could change the answer. Shell examples throughout are POSIX — on Windows, substitute `python` for `python3` and the platform's equivalents for the one-liners.

## Trust scope and size discipline

Owned vs external is relative to the resolved root: anything under a folder named `shared/` (at any depth), a git submodule, or a symlink escaping the tree is external. Reads cross owned spaces by default and enter external ones only when the user explicitly asks. Writes stay inside the targeted space; any other space — owned or external — is written only on explicit instruction.

Caps are UTF-8 bytes including frontmatter, keyed by basename: `index.md` 5000, `log.md` and `hot.md` 100000, any other `*.md` 15000. A `_meta/limits.md` of plain `basename: bytes` lines overrides them — the nearest one at or above the file wins, like `_template.md`, and the literal name `*.md` re-caps the content-page catch-all. Check caps with `check-size`: pipe planned new content via `--stdin` before writing it; after editing a file in place, check the file itself — a write that shrinks an over-cap file reports `ok`, progress, so repairs converge. An overflow is a signal about shape, not just size: distill the page or reshape the space — never truncate.

Conventions are opt-in per wiki. Read the markers present at the scope root — `log.md`, `_meta/taxonomy.md`, `_meta/limits.md`, `_meta/ignore.md`, frontmatter on pages, `_template.md`, `hot.md`, `.obsidian/`, `.git` — and degrade gracefully when one is absent. If `log.md` exists, append one line per operation:

```sh
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '<OP> <details>' >> <root>/log.md
```
<!-- /ws:core -->

## Procedure

1. **Resolve the wiki** (core block) and announce the root when it came from CWD or could be ambiguous. A freshly scaffolded wiki has nothing to tend — say so plainly rather than inventing work.
2. **Detect conventions at the scope root** — the wiki by default, the targeted space when the user named one. Presence markers (`log.md`, taxonomy, `hot.md`) never inherit from a parent; `_meta/limits.md`, `_meta/ignore.md`, and `_template.md` follow their nearest-ancestor rule. Skip every mode whose marker is absent.
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
4. **Status.** `list` + `files` for the shape (add `--external` only when the user opted in). Report: spaces and their one-line descriptions, page counts per top-level folder, tag counts and the top 10 by usage (from a `grep '^tags:'` sweep, when frontmatter is in use), the last `log.md` line (when present), and git status (when `.git` exists — read-only; never commit or push).
5. **Audit.** Run `audit` and relay its findings in its own vocabulary — `contract` (a bare index and whether it is registered, a near-miss heading, a malformed / duplicate / unregistrable entry, an entry naming a file rather than a space, an entry crossing another space's boundary, a dead second heading), `drift` (an on-disk space not listed / a listed entry with nothing on disk), `mount` (a registered external mount that is no longer a wiki), `over-cap` files, and `unreadable` files. The script is the source of truth for those; don't re-derive them. Add the judgment it can't make:
   - **Remediation suggestions.** For each over-cap file, suggest what its diagnosis calls for — distill (bloat) or reshape the space (siblings under a hub, a promotion, a regrouped layout), per `ws-update`'s overflow procedure.
   - **Link integrity** — content, so yours: sweep the link shapes (`grep '\[\[' …`, `grep '\]\(' …`), judge each hit against the `files` inventory and the disk, and report dead ones with the nearest-named page as the likely target. On a large wiki, sweep the spaces this pass touches and say what you skipped.
   - **Frontmatter completeness** — only on pages that already carry frontmatter, only when the wiki uses a schema; mixed adoption is allowed.
   - **Structural prompts** — a page pressing its cap (`check-size` any page you suspect) with several distinct H2 sections is outgrowing its shape and is reshape-ready; a space whose index lists many accreted siblings wants a sub-space; a long-grown opt-in `hot.md` wants distilling into cold pages; a space holding one small page and no sub-spaces reads as over-structure — suggest demoting it to a page (`ws-update`'s restructure reference).
6. **Structural repair (close-out).** Apply the safe structural repairs yourself, one edit each, re-auditing between rounds until the structural findings are gone: add the exact entry line a `missing entry` finding prints, insert the `## Spaces` heading a *registered* bare child lacks, and remove an entry the audit says crosses another space's boundary (that space owns the deeper listing — register it there instead when it is missing). Each re-audit re-derives findings from disk, so the rounds converge. Everything else is author intent — malformed, duplicate, or unregistrable entries, entries naming files, near-miss headings, undeclared bare indexes, stale entries, unhealthy mounts, over-cap or unreadable files, and any dead links your own sweep surfaced — and stays reported for the user (offer to fix as a normal edit if the user says go; an undeclared bare index is a promotion decision).
7. **Normalize: tags.** Only when `_meta/taxonomy.md` exists at the scope root. Sweep the inventory with `grep '^tags:' --wiki <root>` and read the frontmatter of anything the sweep leaves ambiguous — YAML shapes are yours to judge (untagged pages = `files` minus the sweep, less the frontmatter-exempt special files — indexes, `log.md`, `hot.md`, `_template.md`); over the scope's own pages (children spaces keep their own taxonomies — leave them out): flag non-canonical tags, apply alias mappings the taxonomy defines, flag over-tagged (>5) and untagged pages. Unknown tags on 2+ pages: suggest adding to the taxonomy; one-offs: suggest the closest canonical tag.
8. **Normalize: cross-links.** Build a registry of scope-own pages (names, titles, frontmatter aliases when present). Scan bodies for unlinked first mentions of registry entries and wrap the strongest matches as wikilinks — at most 2 new links per page, never inside code blocks or frontmatter, no self-links. Prefer inline linking; a trailing `## Related` section is the fallback when no natural mention exists.
9. **Colorize.** Only when `.obsidian/` exists: [references/colorize.md](references/colorize.md).
10. **Log** one `TEND` line per the core block when `log.md` exists, and close with the report.

## Output

Per mode: a status table; an audit report quoting the script's findings plus your suggestions; normalize tables (tag → action, link added → where); a colorize summary (groups written, backup path). End the full sweep with the one-line delta: `issues before → after; left for you: <n>`.
