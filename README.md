# wiki-spaces

A minimal nestable wiki — a folder with `index.md`, for any use case. Research, recipes, code notes, writing, team docs, a personal life wiki — your shape, your call.

## Install

### Let an AI agent set it up (recommended)

Paste this to your AI agent:

```
Install and set up wiki-spaces for me by following the instructions here:
https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md
```

The agent asks what your wiki is for, picks tailored defaults, scaffolds it, links the skills into your harness, and writes the config. Done.

### Just start (no install, no dependencies)

```sh
mkdir -p ~/Wiki && echo "# My Wiki" > ~/Wiki/index.md
mkdir -p ~/.config/wiki-spaces
printf 'wiki = %s/Wiki\n' "$HOME" > ~/.config/wiki-spaces/config
```

That's a complete wiki. Add files. Install the skills later if you want them.

### Run it yourself

```bash
uvx wiki-spaces install                  # link skills into detected AI harnesses
uvx wiki-spaces init ~/Wiki --git        # scaffold a bare wiki
uvx wiki-spaces doctor --no-net          # verify
```

Or `pip install wiki-spaces` (or `uv tool install wiki-spaces`) and drop the `uvx` prefix.

## What you get

Three reference skills your AI agent uses to work with the wiki:

- `wiki-search` — find content
- `wiki-update` — capture / save / sync
- `wiki-tend` — audit, normalize tags, cross-link

For Cursor / Windsurf / Copilot / Aider (no skills concept), see [`references/HARNESS_INTEGRATION.md`](references/HARNESS_INTEGRATION.md).

## Learn more

- [`AGENTS.md`](AGENTS.md) — the spec, one page
- [`CONVENTIONS.md`](CONVENTIONS.md) — opt-in conventions catalog
- [`references/EXAMPLES.md`](references/EXAMPLES.md) — topology examples per use case
- [`references/MOUNT.md`](references/MOUNT.md) — embed external wikis as spaces

## Dependencies

Python `>=3.11`. [`uv`](https://docs.astral.sh/uv/) recommended (handles Python provisioning). `git` optional.

## Prior art

- Andrej Karpathy's [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki) — broader framework wiki-spaces extracts from
- [kepano](https://github.com/kepano) — vendored `obsidian-markdown` and `obsidian-bases` skills

## License

MIT. See [`LICENSE`](LICENSE).
