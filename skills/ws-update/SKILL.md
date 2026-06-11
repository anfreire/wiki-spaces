---
name: ws-update
description: Create or update content in the user's canonical wiki. Use when the user says "update wiki", "sync project", "save this", "capture this", "store this research", or wants to distill knowledge from a project, conversation, or research session. When a session produced durable knowledge worth keeping, offer once at a natural wrap-up — never per-turn.
---

# Wiki Update

Extract durable knowledge from the current source — a project, the conversation, a research session — and place it in the user's wiki. Merge before creating, respect the conventions the wiki has adopted, and keep every file inside its size cap.

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
- `… audit [--fix] --wiki <root>` — drift, broken wikilinks, over-cap files; `--fix` only inserts missing `## Spaces` headings and registers unlisted owned child spaces.

Trust the script's output over re-deriving structure by hand; it is the deterministic view of the contract.

## Trust scope and size discipline

Owned vs external is relative to the resolved root: anything under `shared/`, a foreign-origin git submodule, or a symlink escaping the tree is external. Reads cross owned spaces by default and enter external ones only when the user explicitly asks. Writes stay inside the targeted space; any other space — owned or external — is written only on explicit instruction.

Caps are UTF-8 bytes including frontmatter, keyed by basename: `index.md` 5000, `log.md` and `hot.md` 100000, any other `*.md` 15000; a wiki overrides them in `_meta/limits.md` with plain `basename: bytes` lines (the literal name `*.md` re-caps the content-page catch-all). Run `check-size` before writing. An overflow is a signal to split, promote, or trim — never to truncate.

Conventions are opt-in per wiki. Read the markers present at the scope root — `log.md`, `_meta/taxonomy.md`, `_meta/limits.md`, frontmatter on pages, `_template.md`, `hot.md`, `.obsidian/`, `.git` — and degrade gracefully when one is absent. If `log.md` exists, append one line per operation:

```sh
printf '%s <OP> <details>\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> <root>/log.md
```
<!-- /ws:core -->

## Initialization

When nothing resolves — no explicit path, no CWD-ancestor wiki, no valid config — set one up inline before doing anything else: follow [references/init.md](references/init.md). Keep the interview to two exchanges (gather, confirm), run every command yourself, and resume the original request once the wiki exists. A configured path that is broken (missing on disk, no `index.md`, not absolute) is a hard stop, not a setup trigger — surface it and let the user decide.

## Procedure

1. **Resolve the wiki** (core block). Announce the root when it came from CWD or could be ambiguous; "save to my wiki" from inside a company repo means the configured wiki, not the repo.
2. **Detect conventions at the scope root** — the wiki root by default, the targeted space if the user named one. Spaces are autonomous; never inherit detection from a parent. Skip every step whose marker is absent.
3. **Detect the mode** from the user's intent — CWD only disambiguates *which* project when intent is already project-scoped:
   | Mode | Trigger | Input |
   |---|---|---|
   | Project sync | "sync this project", explicit project naming | project files, git log |
   | Conversation capture | "save this", "capture this" | the current conversation |
   | Research capture | "store this research" | findings from the session |
4. **Extract knowledge.** Project sync: architecture decisions and their rationale, patterns, tool/API wiring, trade-offs, results — from the project's files and recent git history. Conversation: durable conclusions and decisions, written as facts ("X works by…"), never chat summary. Research: what took effort and would be expensive to re-derive.
5. **Filter and deduplicate.** For knowledge-capture wikis apply the noise filter: answerable from the code? skip; answerable by a quick search? skip; needed in 3 months? keep. Content-store wikis (recipes, journals, runbooks) skip the filter — every entry is intentional. Either way, enumerate existing pages via `files` and rank by filename / path / frontmatter overlap: when a near-match exists, **merge into it instead of creating a sibling**.
6. **Place.** Run `list` for registered spaces (labels + descriptions are placement hints) and check top-level plain folders. Match content to a candidate semantically: project-scoped content goes under the project-grouping space (`projects/<name>/…` or whatever the wiki calls it); global concepts go to the topical folder even when captured inside a project. Several equally plausible candidates, or none → ask. Flat wiki → write at the root. Slugs: lowercase, hyphenated, ≤50 chars.
7. **Check size before every write.** Materialize the full projected content (for an edit, apply it in memory first), then:
   ```sh
   python3 <skill-dir>/scripts/ws.py check-size <target> --stdin --wiki <root> <<'EOF'
   <projected content>
   EOF
   ```
   On `over`, pick the remediation in order — never truncate, never ignore:
   1. **Split** — the page has H2 sections that read as distinct topics → move sections out to siblings; the page becomes a hub linking them.
   2. **Promote** — the page has become a hub of sub-topics or accreted siblings → turn it into a space: [references/promote.md](references/promote.md).
   3. **Trim** — the page is genuinely dense → merge or drop the weakest entries now, not "later".
   An over-cap `index.md` is different: push detail down into child pages and keep entries to one line each — an index is navigation, not content.
8. **Write.** New pages: nearest ancestor `_template.md` if present, else the wiki's page shape (frontmatter only where the wiki already uses it). Mark non-source claims `%%inferred%%`, conflicting sources `%%ambiguous%%`. Add up to 2 wikilinks on first natural mentions. Updates: merge, preserve manual content, bump `updated:` if frontmatter is in use, never overwrite unrelated sections. On a project sync, ensure the project's hub page records the repo path in `~`-contracted form — `repo:` or an entry under `sources:` where the wiki uses frontmatter, else a single plain line — so a later session can map the checkout back to its page. More than ~10 pages changing → show the plan and ask first.
9. **Keep the contract.** Created a new space, removed one, or moved pages? Update `## Spaces` per [Space operations](#space-operations) below. `## Items` sections are optional human curation — maintain one where the index already has it.
10. **Close out.** Run `audit`. If it reports only safe structural drift (a missing heading, an unregistered owned child), run exactly one `audit --fix`, re-audit, and report the delta — once, no loop. Other findings are reported, not auto-repaired; when they fall outside this sync's write scope, suggest a `ws-tend` pass to the user — suggestion only, never run it yourself. Then log an `UPDATE` line per the core block and confirm:
    ```
    Updated wiki (<root>):
    - Created: <paths>   - Updated: <paths>
    - Mode: <mode>       - Audit: <clean | findings>
    ```

## Space operations

**Create a space** (a folder that needs first-class navigation status):

```sh
mkdir -p <root>/<path> && printf '# <Title>\n\n## Spaces\n' > <root>/<path>/index.md
python3 <skill-dir>/scripts/ws.py audit --fix --wiki <root>   # registers it up the chain
```

`audit --fix` registers the new space in its nearest ancestor's `## Spaces` (inserting the heading where missing). Add a ` — description` to the generated entry by hand — descriptions are placement hints for every later operation.

**Remove a space**: confirm with the user (prefer moving content to `_archives/` over deletion), delete the folder, remove its entry from the parent's `## Spaces`, then `audit` to verify nothing dangles.

**Mount someone else's space** (clone / submodule / symlink, lands under `shared/` by convention): [references/mount.md](references/mount.md).

**Promote a page into a space** (the riskiest manual operation — snapshot first): [references/promote.md](references/promote.md). Triggers: ~3+ H2 sections covering distinct sub-topics, accreted siblings (`strategy.md`, `strategy-backtest.md`…), or an over-cap hub page.
