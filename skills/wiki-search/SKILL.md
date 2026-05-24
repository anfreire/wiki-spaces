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

1. **Resolve the target wiki**, in this order:
   1. Explicit path or named space from the user's request.
   2. The `wiki` value in `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config`, if that path has `index.md`.
   3. **CWD discovery** — the nearest ancestor of the current working directory containing `index.md`. This makes no-install wikis (folder + `index.md`, no config) work whenever the agent runs from inside one.
   4. If none of the above resolves to a folder with `index.md`, **drive the setup flow inline** before answering: read `<repo>/references/SETUP.md` (or fall back to the canonical URL https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md when `repo` is unknown) and follow its Branch A "Fresh install + scaffold" steps. The shorter equivalent is in [`wiki-update/SKILL.md` § Initialization](../wiki-update/SKILL.md#initialization). Once `wiki-spaces init` has registered the wiki, resume from step 1 of this procedure with the user's original query.

   When CWD discovery was the source used (config missing), say so once: "Operating on the wiki at `<path>` (found via CWD; no config registered)."
2. **Detect adopted conventions at the SCOPE root** (the canonical wiki for default operation; the targeted space if the user named one) by presence: frontmatter schema (scan content pages until one with frontmatter is found, or confirm none), `_meta/taxonomy.md` (for tag matching), `log.md` (for logging). Spaces are autonomous — never inherit detection from a parent.
3. **Choose the search mode.**
   - **Quick lookup** — triggered by an agent checking before external research, or user says "quick answer", "just check", "do I have anything on X". Stops at step 5.1 (no page bodies read). Prefix the answer: `Quick lookup: summaries only; page bodies not read.`
   - **Deep query** — default for user questions. Full tiered retrieval below.
4. **Pick a search backend** per CONVENTIONS / Retrieval primitives § Recommended search backends. Prefer in this order:
   - A markdown-aware search MCP installed in the harness (**qmd** is the recommended primary — BM25 + semantic + hybrid + rerank, MCP and CLI surfaces, referenced by Andrej Karpathy in the canonical LLM-wiki gist).
   - The harness's native file-search tool, when it understands markdown structure.
   - Ripgrep (`rg`) or the harness's grep tool as a universal fallback.

   Don't gate retrieval on any specific backend. If qmd is available, use it for the index and section passes below; otherwise grep is fine. **At scale, the choice of backend changes the procedure** — see step 5.1's conditional glob.

5. **Cost-ordered retrieval** per CONVENTIONS / Retrieval primitives. Use the cheapest primitive that answers; escalate only when it cannot.
   1. **Index pass.** Build the candidate set. Where frontmatter is in use, grep the frontmatter fields (`title`, `tags`, `aliases`, `summary`) across pages — a fast signal that surfaces and ranks likely candidates. **Always also glob `**/*.md`** (minus the subtrees excluded in step 6) as the completeness backstop, ranking globbed files by filename and path-segment match. The glob is unconditional: frontmatter adoption is per-page (not per-wiki), so a "uniform adoption" check is a judgment call the LLM cannot reliably make, and a single page without frontmatter would be invisible to a no-glob index pass — that's exactly the producer→consumer break v0.3.0 fixed. The glob is cheap (filename scan only; bodies not read), so the cost of always running it is negligible compared to the cost of missing a page the producer wrote. Collect the top 5–10 candidates overall: exact title/alias > tag match > summary/path match. *(Quick lookup stops here — candidates only, no page bodies read.)*
   2. **Section pass.** For each top candidate: grep with context (e.g., `grep -A 10 -B 2 "<term>" <file>` or your harness's equivalent grep tool). If this gives a clear answer, skip to step 7.
   3. **Full read.** At most 3 candidates. Follow one wikilink hop only when needed.
6. **Default scope is the wiki and the owned spaces inside it.** Per AGENTS.md trust scope (read operations cross owned spaces by default), search descends through owned spaces — typically `projects/<name>/` and anything else the user created. *External* spaces (anything under `<wiki>/shared/`, git submodules with foreign origins, symlinks resolving outside the wiki tree — see CONVENTIONS / Owned vs external) are excluded unless the user explicitly names one or asks to include all. CWD is a hint — if the user is in a project space and asks "what's been said here?", scope to that space.
7. **Answer.** Cite pages using `[[wikilinks]]`. If sources contradict, present both. If the wiki has no coverage, say so explicitly — never infer an answer from absence. Suggest external research only if appropriate.

Format:

```
Based on the wiki:
<answer with [[wikilinks]]>

Pages consulted: [[page-a]], [[page-b]]
Gaps: <what's missing>
```

## Logging

When `log.md` exists at the **scope root** (the wiki for default operations; the targeted space if the user named one — per CONVENTIONS / Per-space convention auto-detection), append a line via the CLI:

```sh
wiki-spaces space log "- [TIMESTAMP] SEARCH query=\"<the question>\" result_pages=N mode=quick|deep"
```

Use the CLI rather than writing `log.md` directly. The `space log` command wraps `_limits.append_log_with_rotation`, holding a `fcntl.flock` across the whole check-rotate-append sequence — concurrent skill invocations never lose lines. Add `--wiki <path>` when the scope is a named sub-space.
