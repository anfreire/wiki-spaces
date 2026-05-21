# wiki-spaces

A minimal nestable wiki — a folder with `index.md`, for any use case. Research, recipes, code notes, writing, team docs, a personal life wiki — your shape, your call.

Markdown flavor is **Obsidian** — wikilinks, frontmatter, callouts, embeds, comments, Bases. One dialect across the spec, the skills, and the tools. Non-Obsidian renderers (GitHub preview, vanilla VS Code, plain markdown viewers) display the content but will not render Obsidian-specific syntax (provenance comments, embeds, `.base` files) the way Obsidian does — view your wiki in Obsidian for full fidelity.

## Audience

wiki-spaces is built for **AI coding harnesses with filesystem access** — Claude Code, Codex, Cursor, Windsurf, Gemini CLI, Aider, and similar tools that read and write local files. Browser-only AI assistants (ChatGPT in a tab, Claude.ai web) are out of scope: they can't reach the filesystem to read or write your wiki. If your AI lives in a browser, this isn't the tool.

The *shape* of the wiki — research notes, recipes, journal, team reference, anything — is yours. The *harness* that drives it is what wiki-spaces assumes you have.

## Install

Installing gives your agent the reference skills plus a scaffolded, registered wiki. **Letting an agent do it is the recommended path** — setup is a short interview, and an agent runs the steps end to end without fat-fingering a path or a flag.

### Let your AI agent do it (recommended)

Paste this to your coding agent (Claude Code, Codex, Cursor, Windsurf, Gemini CLI, Antigravity, …):

```
Install and set up wiki-spaces for me by following the instructions here:
https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md
```

The agent reads [`SETUP.md`](references/SETUP.md), asks what the wiki is for and where it should live, infers a layout, links the skills into your harness, scaffolds the wiki, and writes the config — confirming the plan with you before it runs anything.

### Manual

```bash
uvx wiki-spaces install                  # link skills into detected harnesses
uvx wiki-spaces init ~/Wiki              # scaffold a wiki + register it
uvx wiki-spaces doctor --no-net          # verify
```

`install` auto-detects and links the skills into whichever of **Claude Code, Codex, Gemini CLI, Antigravity, Hermes, and Kiro** are present (`--all` pre-positions for every one). Cursor, Windsurf, GitHub Copilot, and Aider have no global skills directory — they integrate via a rule-file snippet (`wiki-spaces install --bridge <key>`; see [`HARNESS_INTEGRATION.md`](references/HARNESS_INTEGRATION.md)). For a permanent install, `pip install wiki-spaces` or `uv tool install wiki-spaces`, then drop the `uvx` prefix.

Once a wiki exists, the `space` subcommands manage its structure:

```bash
uvx wiki-spaces space add projects/foo   # create a space + register it
uvx wiki-spaces space audit              # audit drift, broken links, orphans
uvx wiki-spaces space mount <url> shared/team --as submodule   # mount an external space
```

`space add`, `space remove`, and `space mount` need a `## Spaces` section in the parent's `index.md`; `wiki-spaces init` scaffolds that automatically.

### No tooling at all

```sh
mkdir -p ~/Wiki && echo "# My Wiki" > ~/Wiki/index.md
```

A folder with `index.md` is already a complete wiki — the whole spec is one page, [`AGENTS.md`](AGENTS.md). The skills still work on it (they discover the wiki from your current directory); run `wiki-spaces init` later to register it for config-based discovery.

## What you get

Three reference skills your AI agent uses to work with the wiki:

- `wiki-search` — find content
- `wiki-update` — capture / save / sync
- `wiki-tend` — audit, normalize tags, cross-link

Cursor / Windsurf / Copilot / Aider integration is covered under [Install](#install) above and in [`HARNESS_INTEGRATION.md`](references/HARNESS_INTEGRATION.md).

## Search at scale

`wiki-search` works out of the box with grep / ripgrep, which is fine for personal/team wikis up to a few hundred pages. For larger vaults, install [`qmd`](https://github.com/tobi/qmd) — the markdown-aware MCP backend Andrej Karpathy references in the canonical [LLM-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). See [`CONVENTIONS.md` § Recommended search backends](CONVENTIONS.md#recommended-search-backends).

## Learn more

- [`AGENTS.md`](AGENTS.md) — the spec, one page
- [`CONVENTIONS.md`](CONVENTIONS.md) — opt-in conventions catalog
- [`references/EXAMPLES.md`](references/EXAMPLES.md) — topology examples per use case
- [`references/MOUNT.md`](references/MOUNT.md) — mount external spaces into your wiki

## Dependencies

Python `>=3.11`. [`uv`](https://docs.astral.sh/uv/) recommended (handles Python provisioning). `git` optional.

## Prior art

- Andrej Karpathy's [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) — broader framework wiki-spaces extracts from
- [kepano](https://github.com/kepano) — vendored `obsidian-markdown` and `obsidian-bases` skills (MIT-licensed; see [`vendor/kepano/LICENSE`](vendor/kepano/LICENSE))

## License

MIT. See [`LICENSE`](LICENSE).
