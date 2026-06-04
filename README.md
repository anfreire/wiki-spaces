# wiki-spaces

A minimal nestable wiki — a folder with `index.md` and a `## Spaces` navigation contract, for any use case. Research, recipes, code notes, writing, team docs, a personal life wiki — your shape, your call.

Markdown flavor is **Obsidian** — wikilinks, frontmatter, callouts, embeds, comments, Bases. One dialect across the spec, the skills, and the tools. Non-Obsidian renderers (GitHub preview, vanilla VS Code, plain markdown viewers) display the content but will not render Obsidian-specific syntax (provenance comments, embeds, `.base` files) the way Obsidian does — view your wiki in Obsidian for full fidelity.

## Audience

wiki-spaces is built for **AI coding harnesses with filesystem access** — Claude Code, Codex, Cursor, Copilot, Gemini CLI, OpenCode, and Kiro. Browser-only AI assistants (ChatGPT in a tab, Claude.ai web) are out of scope: they can't reach the filesystem to read or write your wiki. If your AI lives in a browser, this isn't the tool.

The *shape* of the wiki — research notes, recipes, journal, team reference, anything — is yours. The *harness* that drives it is what wiki-spaces assumes you have.

## Install

Installing gives your agent the reference skills plus a scaffolded, registered wiki. **Letting an agent do it is the recommended path** — setup is a short interview, and an agent runs the steps end to end without fat-fingering a path or a flag.

### Let your AI agent do it (recommended)

Paste this to your coding agent (Claude Code, Codex, Cursor, Copilot, Gemini CLI):

```
Install and set up wiki-spaces for me by following the instructions here:
https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md
```

The agent reads [`SETUP.md`](references/SETUP.md), asks what the wiki is for and where it should live, infers a layout, links the skills into your harness, scaffolds the wiki, and writes the config — confirming the plan with you before it runs anything.

### Manual

```bash
uvx wiki-spaces install                  # link skills into detected harnesses
uvx wiki-spaces init ~/Documents/Wiki              # scaffold a fresh wiki + register it
uvx wiki-spaces init ~/notes --adopt     # OR adopt an existing folder of notes
uvx wiki-spaces doctor --no-net          # verify
```

`install` writes every skill once into the hub directory (`~/.agents/skills/`) — this hub is read directly by Codex, Gemini CLI, OpenCode, Copilot, and Cursor. For harnesses without hub support — like Claude Code and Kiro — the installer creates per-skill aliases in their native directories (`~/.claude/skills/` and `~/.kiro/skills/` respectively). See [`HARNESS_INTEGRATION.md`](references/HARNESS_INTEGRATION.md) for details on supported harnesses. For a permanent install, run `pip install wiki-spaces` or `uv tool install wiki-spaces`, then drop the `uvx` prefix.

`init` always emits `## Spaces` in the new wiki's `index.md` from t=0 so the `space` subcommands work immediately. `--adopt` walks the existing folder, registers every nested folder containing `index.md` in its ancestor's `## Spaces`, and inserts `## Spaces` into any pre-existing index that lacks the heading (the v1 navigation contract — a wiki is a folder with `index.md` AND `## Spaces`). After adoption every folder carries both, so audit reports zero drift on day 1. External subtrees (`shared/`, foreign submodules, escaping symlinks) are skipped with a per-skip notice; pass `--include-external` to override.

Once a wiki exists, the `space` subcommands manage its structure:

```bash
uvx wiki-spaces space add projects/foo   # create a space + register it
uvx wiki-spaces space audit              # audit drift, broken links, orphans
uvx wiki-spaces space mount <url> --mode submodule              # mount an external space (default path: shared/<basename>/)
```

`space add`, `space remove`, `space mount`, and `space promote` auto-insert `## Spaces` when missing — the CLI maintains the contract for you. `wiki-spaces init` writes `## Spaces` from t=0 so the first write command lands clean.

### No tooling at all

```sh
mkdir -p ~/Documents/Wiki && printf '# My Wiki\n\n## Spaces\n\n' > ~/Documents/Wiki/index.md
```

A folder with `index.md` + `## Spaces` is already a complete wiki — the whole spec is one page, [`AGENTS.md`](AGENTS.md). The skills still work on it (they discover the wiki from your current directory), though they lean on the `wiki-spaces` CLI for cap checks, audit, and structural edits and fall back to manual procedures without it — installing it makes them most effective. Run `wiki-spaces init` later to register the wiki for config-based discovery.

### Skill source paths

Users with their own skill-management scripts bypass `install.py` and read the source directories directly. The two canonical roots are:

- `<repo>/skills/*/` — `ws-search`, `ws-update`, `ws-tend`
- `<repo>/vendor/kepano/*/` — `obsidian-markdown`, `obsidian-bases`

`<repo>` is the source checkout or the share dir written by `wiki-spaces install` (`~/.local/share/wiki-spaces/` for packaged installs). Copy, symlink, or aggregate however your tooling prefers.

## What you get

Three reference skills your AI agent uses to work with the wiki:

- `ws-search` — find content
- `ws-update` — capture / save / sync, with per-file size discipline (hard caps at write time, see [`CONVENTIONS.md` § `_meta/limits.md`](CONVENTIONS.md))
- `ws-tend` — audit, normalize tags, cross-link

## Search at scale

`ws-search` works out of the box with grep / ripgrep, which is fine for personal/team wikis up to a few hundred pages. For larger vaults, install [`qmd`](https://github.com/tobi/qmd) — the markdown-aware MCP backend Andrej Karpathy references in the canonical [LLM-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). See [`CONVENTIONS.md` § Recommended search backends](CONVENTIONS.md#recommended-search-backends).

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
