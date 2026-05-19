# wiki-spaces

A minimal nestable wiki — a folder with `index.md`, for any use case. Research, recipes, code notes, writing, team docs, a personal life wiki — your shape, your call.

Markdown flavor is **Obsidian** — wikilinks, frontmatter, callouts, embeds, comments, Bases. One dialect across the spec, the skills, and the tools. Non-Obsidian renderers (GitHub preview, vanilla VS Code, plain markdown viewers) display the content but will not render Obsidian-specific syntax (provenance comments, embeds, `.base` files) the way Obsidian does — view your wiki in Obsidian for full fidelity.

## Audience

wiki-spaces is built for **AI coding harnesses with filesystem access** — Claude Code, Codex, Cursor, Windsurf, Gemini CLI, Aider, and similar tools that read and write local files. Browser-only AI assistants (ChatGPT in a tab, Claude.ai web) are out of scope: they can't reach the filesystem to read or write your wiki. If your AI lives in a browser, this isn't the tool.

The *shape* of the wiki — research notes, recipes, journal, team reference, anything — is yours. The *harness* that drives it is what wiki-spaces assumes you have.

## Start

```sh
mkdir -p ~/Wiki && echo "# My Wiki" > ~/Wiki/index.md
```

That's a complete wiki. Add files, folders, anything. The whole spec is one page: [`AGENTS.md`](AGENTS.md).

## Optional tooling

For AI agents to find and act on your wiki, install the helper:

```bash
uvx wiki-spaces install                  # link skills into detected AI harnesses
uvx wiki-spaces init ~/Wiki              # scaffold + register as default target
uvx wiki-spaces doctor --no-net          # verify
```

Or `pip install wiki-spaces` (or `uv tool install wiki-spaces`) and drop the `uvx` prefix.

Also available:

```bash
uvx wiki-spaces space add projects/foo   # create a space + register it
uvx wiki-spaces space audit              # report ## Spaces drift
```

`space add` and `space remove` require a `## Spaces` section in the parent's `index.md` (the navigability contract). Wikis scaffolded via `wiki-spaces init` get this automatically. Hand-rolled `mkdir + echo` wikis are Tier 1 and need `## Spaces` added to `index.md` first — either by `wiki-spaces init` (which registers the wiki in your config) or by editing the file directly.

### Let an AI agent do the setup

Paste this to your agent:

```
Install and set up wiki-spaces for me by following the instructions here:
https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md
```

The agent picks tailored defaults, scaffolds, links the skills into your harness, and writes the config.

## What you get

Three reference skills your AI agent uses to work with the wiki:

- `wiki-search` — find content
- `wiki-update` — capture / save / sync
- `wiki-tend` — audit, normalize tags, cross-link

For Cursor / Windsurf / Copilot / Aider (no skills concept), see [`references/HARNESS_INTEGRATION.md`](references/HARNESS_INTEGRATION.md). The fastest path is `wiki-spaces install --bridge <key>` (emits the rule snippet to stdout — pipe it into your project's rules file).

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
- [kepano](https://github.com/kepano) — vendored `obsidian-markdown` and `obsidian-bases` skills

## License

MIT. See [`LICENSE`](LICENSE).
