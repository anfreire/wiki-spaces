---
name: ws-update
description: Create or update content in the user's wiki — and set the wiki up when none exists yet. Use when the user says "update wiki", "sync project", "save this", "capture this", "store this research", "set up my wiki", "create a wiki", or wants to distill knowledge from a project, conversation, or research session, or to adopt an existing folder of notes as a wiki. When a session produced durable knowledge worth keeping, offer once at a natural wrap-up — never per-turn.
---

# Wiki Update

Extract durable knowledge from the current source — a project, the conversation, a research session — and place it in the user's wiki. Merge before creating, respect the wiki's conventions, and keep every file inside its size cap.

<!-- ws:core -->
## The wiki model

A wiki is a folder whose `index.md` carries a `## Spaces` heading. `## Spaces` is the navigation contract: every space directly inside is listed there as `- [label](path/index.md) — description`, and tools traverse only what it lists. Spaces nest recursively; each space is itself a wiki one level down. Plain folders (no `index.md`) just group files. The markdown dialect is Obsidian — wikilinks, frontmatter, callouts, embeds; the companion `obsidian-markdown` and `obsidian-bases` skills cover the syntax.

## Resolving the wiki

Resolution order: an explicit path from the user → the nearest CWD-ancestor folder that is a wiki → the `wiki` key in `~/.config/wiki-spaces/config`. The user's words override the mechanics: "my wiki" means the configured one even when CWD sits inside another wiki (a company repo, say). When a CWD wiki and a different configured wiki both exist, announce which root you resolved; ask once if intent is ambiguous. Never silently operate on the wrong wiki.

## The bundled script

`scripts/ws.py` sits next to this SKILL.md — stdlib python3 (3.9+), zero dependencies, read-only. It parses the contract, never the content: structure — traversal, scope, caps, drift — is the script's side; reading and judging meaning is yours. Invoke it by absolute path (your working directory is usually elsewhere):

- `python3 <skill-dir>/scripts/ws.py list --wiki <root>` — spaces reachable via the `## Spaces` contract, each with its entry description (`--external` to cross mounts).
- `… files --wiki <root>` — markdown files reachable via the contract.
- `… grep <pattern> [-i] [-F] --wiki <root>` — regex line search over those files, `-F` for a literal string (a name carrying metacharacters sweeps exact); prints `rel:line: text`, exits 1 on no match. The sweep primitive: a link worklist, a tag inventory, an escaping-reference check are each a pattern plus your judgment on the hits.
- `… check-size <target> [--stdin] --wiki <root>` — cap verdict for a file; pipe planned content with `--stdin` to check before writing.
- `… audit --wiki <root>` — contract drift, entries crossing a space boundary, over-cap or unreadable files, unhealthy mounts. Findings name their repair where one is safe to name (a `missing entry` prints the exact line to add); apply repairs as ordinary edits and re-run the audit to verify — the script never writes.

Trust the script's output over re-deriving structure by hand. Stdout is data; stderr carries the resolved root (`audit` prints it as its stdout header instead) and `note:` advisories naming whatever a walk skipped (and the enclosing wiki when the root is nested inside one) — relay them when they could change the answer.

## Trust scope and size discipline

Owned vs external is relative to the resolved root: anything under a folder named `shared/` (at any depth), a git submodule, or a symlink escaping the tree is external. Reads cross owned spaces by default and enter external ones only when the user explicitly asks. Writes stay inside the targeted space; any other space — owned or external — is written only on explicit instruction.

Caps are UTF-8 bytes including frontmatter, keyed by basename: `index.md` 5000, `log.md` and `hot.md` 100000, any other `*.md` 15000.

- A `_meta/limits.md` of plain `basename: bytes` lines overrides them — the nearest one at or above the file wins, like `_template.md`, and the literal name `*.md` re-caps the content-page catch-all.
- Check caps with `check-size`: pipe planned new content via `--stdin` before writing it; after editing a file in place, check the file itself — a write that shrinks an over-cap file reports `ok`, progress, so repairs converge.
- An overflow is a signal about shape, not just size: distill the page or reshape the space — never truncate.
- The exception is `log.md`: an over-cap log rolls — move it whole to `_archives/log-<YYYYMMDD>.md` and start a fresh one — so the history archives, never shrinks.

Conventions are opt-in per wiki. Read the markers present at the scope root (the space the user targeted, else the resolved root) — `log.md`, `_meta/taxonomy.md`, `_meta/limits.md`, `_meta/ignore.md`, frontmatter on pages, `_template.md`, `hot.md`, `.git` — and degrade gracefully when one is absent. If `log.md` exists, append one line per operation:

```sh
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '<OP> <details>' >> <scope-root>/log.md
```
<!-- /ws:core -->

## Initialization

When nothing resolves — no explicit path, no CWD-ancestor wiki, no valid config — set one up inline: follow [references/init.md](references/init.md), then resume the request. A broken configured path (missing on disk, no `index.md`, not absolute) is a hard stop, not a setup trigger — surface it and let the user decide.

## Procedure

1. **Resolve the wiki** (core block) and announce the root when it came from CWD or could be ambiguous.
2. **Detect conventions at the scope root** (core block). Presence markers (`log.md`, taxonomy, `hot.md`) never inherit from a parent; `_meta/limits.md`, `_meta/ignore.md`, and `_template.md` follow their nearest-ancestor rule. Skip every step whose marker is absent.
3. **Detect the mode** from the user's intent — CWD only disambiguates *which* project, never the mode:
   | Mode | Trigger | Input |
   |---|---|---|
   | Direct add | "add this to the wiki", "save this recipe" — a concrete artifact in hand | the handed content |
   | Project sync | "sync this project", explicit project naming | project files, git log |
   | Conversation capture | "save this", "capture this" — the session itself, no single artifact | the current conversation |
   | Research capture | "store this research" | findings from the session |
4. **Extract knowledge.** Direct add: take the content as handed — your job is placement and dedup, not re-synthesis. Project sync: decisions and rationale, patterns, tool/API wiring, trade-offs, results — from the files and recent git history. Conversation: durable conclusions and decisions, written as facts ("X works by…"), never chat summary. Research: what took effort and would be expensive to re-derive.
5. **Filter and deduplicate.** Knowledge-capture wikis get the noise filter: answerable from the code or a quick search? skip; needed in 3 months? keep. Content-store wikis (recipes, journals, runbooks) skip the filter — every entry is intentional. Enumerate existing pages via `files` and rank by filename/path/frontmatter overlap: when a near-match exists, **merge into it instead of creating a sibling**.
6. **Place.** `list` prints each space with its entry description — the placement hints; check top-level plain folders too. Match semantically: project-scoped content goes under the project-grouping space (`projects/<name>/…` or whatever the wiki calls it); global concepts go to the topical folder even when captured inside a project. Several equally plausible candidates, or none → ask. Flat wiki → write at the root. Names follow the wiki's own pattern — match what existing pages do; where no pattern exists, default to lowercase, hyphenated, ≤50 chars.
7. **Check the cap on every write.** Pipe planned content before writing:
   ```sh
   python3 <skill-dir>/scripts/ws.py check-size <target> --stdin --wiki <root> <<'EOF'
   <planned content>
   EOF
   ```
   For an edit, apply it and `check-size` the file itself (a shrinking write reports `ok`; the audit backstops). On `over` — never truncate, never ignore. Re-read the page and its space's index, diagnose why it overflowed, then act:
   - **Bloat** — padding, near-duplicates, prose pasted in raw, entries you wouldn't re-create today → distill: merge, tighten, drop. The cap exists to force this judgment.
   - **Growth** — genuinely distinct topics sharing one file → reshape the space to what it would look like had it been designed for today's content: self-standing sibling pages under a hub, a promotion, or a regrouped layout ([Space operations](#space-operations) links both procedures).
   Both can apply — distill first, then reshape what remains. Update every index you touch. Filenames like `topic-2.md` or `topic-more.md` mean a file was split without rethinking the space — never ship them.
   An over-cap `index.md` is navigation debt: push detail into child pages, one line per entry.
8. **Write.** New pages: nearest ancestor `_template.md` if present, else the wiki's page shape (frontmatter only where the wiki already uses it). Mark non-source claims `%%inferred%%`, conflicting sources `%%ambiguous%%`. Add up to 2 wikilinks on first natural mentions. Updates: merge, preserve manual content, bump `updated:` if frontmatter is in use, never overwrite unrelated sections. On a project sync, record the repo path (`~`-contracted) on the project's hub page — `repo:`/`sources:` frontmatter where in use, else one plain line — so a later session maps the checkout to its page. More than ~10 pages changing → show the plan and ask first.
9. **Keep the contract.** Created a new space, removed one, or moved pages? Update `## Spaces` per [Space operations](#space-operations) below. `## Items` sections are optional human curation — maintain one where the index already has it. Then check the shape you're leaving: entry descriptions still true, no name that only makes sense historically — small fixes now; bigger reshapes become the close-out's `ws-tend` suggestion.
10. **Close out.** Run `audit`. Apply the safe structural repairs yourself, re-auditing between rounds until gone:
    <!-- ws:safe-repairs -->
    - Add the exact entry line a `missing entry` finding prints, then trail it with a `— description` — the placement hint every later operation reads.
    - Insert the `## Spaces` heading a *registered* bare child lacks — only where the finding's hint asks for the insert; a near-miss hint asks for a rename, which stays reported as author intent.
    - Remove an entry the audit says crosses another space's boundary — that space owns the deeper listing; register it there instead when it is missing.
    <!-- /ws:safe-repairs -->

    An undeclared bare index is a promotion decision — surface it, act only on the user's call. Other findings are reported, not auto-repaired; outside this sync's write scope, suggest a `ws-tend` pass — suggestion only, never run it yourself. Log an `UPDATE` line per the core block and confirm in one block: `Updated wiki (<root>)` with created paths, updated paths, mode, and audit outcome.

## Space operations

**Create a space** (a folder needing first-class navigation):

```sh
mkdir -p <root>/<path> && printf '# <Title>\n\n## Spaces\n' > <root>/<path>/index.md
```

Register it in the nearest ancestor space's `## Spaces` — `- [<name>/](<name>/index.md) — description`, href percent-encoded where the name demands it; the description is the placement hint later operations read. The audit's `missing entry` finding prints the exact line if in doubt.

**Remove a page**: sweep for what points at it first — `grep -F '<stem>' --wiki <root>` (every link form carries the stem; judge each hit, code examples aside) — rewrite or drop the real links, prefer a move to `_archives/` over deletion, then `audit`.

**Remove a space**: confirm with the user (prefer `_archives/` over deletion), delete the folder, remove its entry from the parent's `## Spaces`, then `audit` to verify nothing dangles.

**Mount someone else's space**: [references/mount.md](references/mount.md). **Share a space of yours**: [references/share.md](references/share.md).

**Promote a page into a space** (the riskiest manual operation; snapshot first): [references/promote.md](references/promote.md). **Rename, move, merge, or demote a space** (snapshot first): [references/restructure.md](references/restructure.md).
