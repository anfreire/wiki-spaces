# wiki-spaces

A wiki your AI agent keeps for you. The whole format is a folder whose `index.md` carries a `## Spaces` heading — spaces nest recursively, every page lives under a byte cap, and three skills run the lifecycle (find, capture, maintain) while you work. Research, recipes, code notes, writing, team docs, a personal life wiki: your shape, your call.

Built for AI coding harnesses with filesystem access — Claude Code, Codex, Cursor, Copilot, Gemini CLI, OpenCode, Kiro. Browser-only assistants can't reach your files and are out of scope. The markdown dialect is Obsidian — wikilinks, frontmatter, callouts, embeds, Bases — though plain CommonMark always works; view in Obsidian for full fidelity.

## Quick start

```bash
npx skills add anfreire/wiki-spaces
npx skills add kepano/obsidian-skills --skill obsidian-markdown --skill obsidian-bases
```

Then paste this to your agent:

```text
Set up my wiki with the ws-update skill.
```

A short interview, then it builds the wiki — location, folders, opt-ins, git — or adopts a folder of notes you already have. From then on the skills act on their own initiative: `ws-search` brings stored context when it would help, `ws-update` offers to capture durable work at wrap-ups, `ws-tend` keeps the structure healthy on request.

The first install line carries the three reference skills (72+ harnesses via [vercel-labs/skills](https://github.com/vercel-labs/skills)); the second adds [kepano](https://github.com/kepano)'s companions for Obsidian syntax depth — recommended, not required.

| Skill | Job |
|---|---|
| `ws-search` | Find content across your spaces; answer from what's stored, citing pages. |
| `ws-update` | Capture conversations, sync projects, save research — merge before create, reshape before overflow. |
| `ws-tend` | Audit structure, normalize tags, cross-link pages. |

Each skill bundles `scripts/ws.py` — Python 3.9+ standard library only, zero dependencies, read-only — for traversal, size checks, and an audit whose findings name their repair. The whole spec is one page — [AGENTS.md](AGENTS.md) — and the skills discover your wiki from the working directory or `~/.config/wiki-spaces/config`.

## How it works

- **Spaces are the unit.** A space is a folder with `index.md` and a `## Spaces` heading — and every space is itself a wiki one level down. Trees nest without limit; any folder anywhere (a repo root, a teammate's share) can be one. Sharing a space is sharing a folder: mount it as a subdirectory, symlink, submodule, or clone.
- **`## Spaces` is the navigation contract.** It exhaustively lists the spaces directly inside; tools traverse only what it lists. `ws.py audit` detects drift and names each repair; the agent applies it.
- **Size caps force curation.** Per-file byte caps (5,000 for an `index.md`, 15,000 for a page) make the agent distill the page or reshape the space instead of hoarding — never truncate. Day 30 is better than day 0.
- **Trust scope protects sharing.** Owned spaces are read freely; anything under `shared/`, a foreign-origin submodule, or an escaping symlink is external — read on request, written only on explicit instruction.

Opt-in conventions — frontmatter, taxonomy, templates, an append-only log, custom caps — are cataloged in [CONVENTIONS.md](CONVENTIONS.md).

## Search at scale

The bundled `ws.py grep` — deterministic and trust-scope-aware — carries a wiki to a few hundred pages. Past that, [qmd](https://github.com/tobi/qmd) adds markdown-aware BM25 + semantic search — the backend Andrej Karpathy references in his [LLM-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Prior art

- Andrej Karpathy's [LLM wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [kepano](https://github.com/kepano) — creator of the companion Obsidian skills

## License

MIT. See [LICENSE](LICENSE).
