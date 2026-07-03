---
name: ws-search
description: Search the user's canonical wiki for stored knowledge. Use when the user asks "what do I know about X", "find Y in the wiki", before doing external research the wiki may already cover, or when stored context could help the current task and the user hasn't asked — offer once.
---

# Wiki Search

Find content in the user's wiki and answer from what's stored. Cite pages so the user can jump to them. Report gaps explicitly when the wiki doesn't cover the topic — never pad the answer with knowledge the wiki doesn't hold.

<!-- ws:core -->
## The wiki model

A wiki is a folder whose `index.md` carries a `## Spaces` heading. `## Spaces` is the navigation contract: every space directly inside is listed there as `- [label](path/index.md) — description`, and tools traverse only what it lists. Spaces nest recursively; each space is itself a wiki one level down. Plain folders (no `index.md`) just group files. The markdown dialect is Obsidian — wikilinks, frontmatter, callouts, embeds; the companion `obsidian-markdown` and `obsidian-bases` skills cover the syntax.

## Resolving the wiki

Resolution order: an explicit path from the user → the nearest CWD-ancestor folder that is a wiki → the `wiki` key in `~/.config/wiki-spaces/config`. The user's words override the mechanics: "my wiki" means the configured one even when CWD sits inside another wiki (a company repo, say). When a CWD wiki and a different configured wiki both exist, announce which root you resolved; ask once if intent is ambiguous. Never silently operate on the wrong wiki.

## The bundled script

`scripts/ws.py` sits next to this SKILL.md — stdlib python3 (3.9+), zero dependencies. Invoke it by absolute path (your working directory is usually elsewhere):

- `python3 <skill-dir>/scripts/ws.py list --wiki <root>` — spaces reachable via the `## Spaces` contract (`--external` to cross mounts).
- `… files --wiki <root>` — markdown files reachable via the contract.
- `… grep <pattern> [-i] --wiki <root>` — regex line search over those files; prints `rel:line: text`, exits 1 on no match.
- `… check-size <target> [--stdin] --wiki <root>` — cap verdict for a file; pipe planned content with `--stdin` to check before writing.
- `… audit --wiki <root>` — drift, entries crossing a space boundary, broken links, over-cap or unreadable files, unhealthy mounts. Findings name the repair (a `missing entry` prints the exact line to add); apply repairs as ordinary edits and re-run the audit to verify — the script never writes.

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

1. **Resolve the wiki** (core block above). Announce the root when it came from CWD or could be ambiguous. No wiki anywhere → say so and offer to set one up via `ws-update`.
2. **Map the terrain.** Run `list` for the space shape and `files` for the page inventory. Both are cheap; run them before any content search. External mounts stay out unless the user asked — the stderr `note:` lines say how many were skipped and which spaces are unreachable; when a skipped or unreachable space could hold the answer, say so and offer to cross or repair it.
3. **Pick the depth.** "Just check", "do I have anything on X", or an agent pre-research probe → quick lookup: rank candidates structurally (step 4), read nothing, answer from names/summaries and say so. A real question → deep query: continue through step 6.
4. **Rank candidates structurally.** Match the query against space labels and descriptions (`## Spaces` entries), file names, and path segments. When frontmatter is in use, `summary`, `aliases`, and `tags` rank pages without reading bodies.
5. **Search content** when structure alone doesn't settle it, cheapest first:
   1. A markdown-aware search backend when available (e.g. the qmd MCP — BM25 + semantic over markdown).
   2. `… grep '<term>' -i --wiki <root>` — the universal fallback, always bundled; searches exactly the trust-scoped page set (no `shared/`, foreign submodules, or `_archives/`), `--external` only when the user asked.
6. **Read the top hits fully.** Pages are cap-bounded, so whole files are affordable. Prefer 2–4 full pages over a dozen snippets.
7. **Answer from the wiki only.** Cite every claim's page with a wikilink (`[[path/to/page]]`). Carry over `%%inferred%%` / `%%ambiguous%%` provenance markers when the wiki uses them. Name what the wiki lacks as a gap, then offer to research and capture it via `ws-update`.
8. **Log** one `SEARCH` line per the core block when `log.md` exists.

## Output

```
From your wiki (<root>):
- <finding> — [[page]]
- <finding> — [[page]]
Gaps: <topics the wiki doesn't cover, or "none">
```
