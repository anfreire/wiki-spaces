---
name: wiki-search
description: Search the user's canonical wiki for stored knowledge. Use when the user asks "what do I know about X", "find Y in the wiki", or before doing external research that the wiki may already cover.
---

# Wiki Search

Find content in the user's canonical wiki and answer using only what's stored. Cite pages with `[[wikilinks]]`. Report gaps explicitly when the wiki doesn't cover the topic.

## Defers to

- Spec: `AGENTS.md` at the wiki-spaces repo (path in `~/.config/wiki-spaces/config` `repo` key).
- Conventions: `CONVENTIONS.md` at the same repo. Cited sections below: `index.md`, Frontmatter schema, Retrieval primitives.
- Markdown syntax: kepano's `obsidian-markdown` skill — installed alongside this one in your harness's skills directory.
- Deeper docs: `references/` at the wiki-spaces repo for setup, examples, mounting playbooks.

## Procedure

1. **Read the config.** Open `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config`. Get the `wiki` path. If the config is missing, the `wiki` key is unset, or the path has no `index.md`, tell the user setup is needed and point them at the SETUP briefing — preferred path `<repo>/references/SETUP.md` if the `repo` key is set; fallback URL https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md if `repo` is also unknown. Then stop (this skill does not drive setup itself; `wiki-update` does).
2. **Detect adopted conventions at the SCOPE root** (the canonical wiki for default operation; the targeted space if the user named one) by presence: frontmatter schema (scan content pages until one with frontmatter is found, or confirm none), `_meta/taxonomy.md` (for tag matching), `log.md` (for logging). Spaces are autonomous — never inherit detection from a parent.
3. **Choose the search mode.**
   - **Quick lookup** — triggered by an agent checking before external research, or user says "quick answer", "just check", "do I have anything on X". Stops at step 4.1 (no page bodies read). Prefix the answer: `Quick lookup: summaries only; page bodies not read.`
   - **Deep query** — default for user questions. Full tiered retrieval below.
4. **Tiered retrieval** per CONVENTIONS / Retrieval primitives. Use the cheapest primitive that answers; escalate only when it cannot.
   1. **Index pass.** Scan `index.md` entries; grep frontmatter (`title`, `tags`, `aliases`, `summary`) if frontmatter is in use. Collect top 5–10 candidates: exact title/alias > tag match > summary match. **If the scope has no `## Items` and no frontmatter in use**, fall back to a filesystem glob (`**/*.md` minus excluded subtrees per step 5) and rank candidates by filename and path-segment match against the query. The fallback triggers on missing curated map, not on Tier label — a Tier 2 wiki with `## Spaces` but no `## Items` still falls back to glob. *(Quick lookup stops here and answers from candidates.)*
   2. **Section pass.** For each top candidate: grep with context (e.g., `grep -A 10 -B 2 "<term>" <file>` or your harness's equivalent grep tool). If this gives a clear answer, skip to step 6.
   3. **Full read.** At most 3 candidates. Follow one wikilink hop only when needed.
5. **Default scope is the wiki and the owned spaces inside it.** Per AGENTS.md trust scope (read operations cross owned spaces by default), search descends through owned spaces — typically `projects/<name>/` and anything else the user created. *External* spaces (anything under `<wiki>/shared/`, git submodules with foreign origins, symlinks resolving outside the wiki tree — see CONVENTIONS / Owned vs external) are excluded unless the user explicitly names one or asks to include all. CWD is a hint — if the user is in a project space and asks "what's been said here?", scope to that space.
6. **Answer.** Cite pages using `[[wikilinks]]`. If sources contradict, present both. If the wiki has no coverage, say so explicitly — never infer an answer from absence. Suggest external research only if appropriate.

Format:

```
Based on the wiki:
<answer with [[wikilinks]]>

Pages consulted: [[page-a]], [[page-b]]
Gaps: <what's missing>
```

## Logging

Append to `log.md` only if it exists at the **scope root** (the wiki for default operations; the targeted space if the user named one — per CONVENTIONS / Per-space convention auto-detection):

```
- [TIMESTAMP] SEARCH query="<the question>" result_pages=N mode=quick|deep
```
