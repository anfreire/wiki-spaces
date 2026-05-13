# wiki-spaces

A wiki is a folder with `index.md`. That's the spec. Inside, anything goes: files, plain folders, or other spaces (folders that themselves carry `index.md`) — recursively. Zero contained spaces is a fine wiki. Deep nesting is too. Your shape is your call.

Use it for whatever you want — research notes, writing drafts, recipes, project knowledge, a personal life wiki, a team reference. The spec is shape-agnostic; conventions are opt-in. `wiki-update` places new content by reading `index.md`'s `## Spaces` / `## Items` entries; `wiki-search` and `wiki-tend` operate against whatever shape the wiki has, with filesystem-glob fallbacks when the curated map is absent — see [`CONVENTIONS.md / Categorical layout`](CONVENTIONS.md#categorical-layout).

Three tiers of opt-in: stop at the floor (`index.md` only), grow into navigable (`## Items` + `## Spaces` so the wiki maps itself), or fully managed (the `CONVENTIONS.md` catalog — taxonomy, logging, frontmatter, etc.). Each tier adds capability and a small contract; tools degrade gracefully where you haven't opted in.

wiki-spaces ships:

- The **spec** ([`AGENTS.md`](AGENTS.md)) — what counts as a wiki.
- The **conventions catalog** ([`CONVENTIONS.md`](CONVENTIONS.md)) — opt-in markers tools detect.
- Three **reference skills** AI agents use to work with your wiki:
  - `wiki-search` — find content
  - `wiki-update` — capture / save / sync new content
  - `wiki-tend` — audit, normalize tags, cross-link, colorize

One canonical wiki per user, located via `~/.config/wiki-spaces/config`. Plain markdown, Obsidian-flavored, version-controllable.

## Just start (no install)

The spec is a folder + `index.md`. If you don't need the reference skills yet, that's the whole setup:

```sh
mkdir -p ~/Wiki
echo "# My Wiki" > ~/Wiki/index.md
mkdir -p ~/.config/wiki-spaces
printf 'wiki = %s/Wiki\n' "$HOME" > ~/.config/wiki-spaces/config
```

Start adding files. You now have a Tier 1 wiki. Install the skills later (steps below); they'll discover the wiki via the config you just wrote.

## Setup (with the reference skills)

### Paste this to your AI agent (recommended)

```
Install and set up wiki-spaces for me by following the instructions here:
https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md
```

The agent fetches the briefing, asks what your wiki is for, picks tailored defaults, runs the install / scaffold / config writes, and confirms when done.

### For LLM agents (non-interactive)

```bash
curl -s https://raw.githubusercontent.com/anfreire/wiki-spaces/main/references/SETUP.md
```

### For humans (manual)

[`uv`](https://docs.astral.sh/uv/) is strongly recommended (handles Python provisioning + on-demand runs). Run on demand with no install:

```bash
uvx wiki-spaces install                                  # detected harnesses; add --all to cover every supported one
uvx wiki-spaces init ~/Wiki --git                        # bare wiki at ~/Wiki; --with adds files, --folders adds dirs (see below)
uvx wiki-spaces doctor --no-net
```

Or install permanently and drop the `uvx` prefix:

```bash
uv tool install wiki-spaces
wiki-spaces install
wiki-spaces init ~/Wiki --git                            # bare wiki; --with / --folders see below
wiki-spaces doctor --no-net
```

No `uv`? Plain `pip` works too:

```bash
pip install --user wiki-spaces                           # or pipx install wiki-spaces
wiki-spaces install
wiki-spaces init ~/Wiki --git                            # bare wiki; --with / --folders see below
wiki-spaces doctor --no-net
```

`init --with` accepts `log.md`, `_meta/taxonomy.md`, `.manifest.json`, `_template.md`, `hot.md` — opt-in conventions from `CONVENTIONS.md`. `--folders <names...>` creates top-level categorical directories — `--folders concepts entities projects` for a developer notebook, `--folders recipes ingredients techniques` for a recipe collection, `--folders drafts characters worldbuilding` for a writing project, and so on (see [`references/EXAMPLES.md`](references/EXAMPLES.md) for full shape examples). For nothing extra, leave both flags off; `index.md` is the only required file.

After setup, your skills know everything via `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config` (two keys: `wiki` and `repo`). Invoke `wiki-search`, `wiki-update`, `wiki-tend` from anywhere.

For Cursor / Windsurf / GitHub Copilot / Aider (no skills concept), see [`references/HARNESS_INTEGRATION.md`](references/HARNESS_INTEGRATION.md).

### Working from a source checkout (dev)

```bash
git clone https://github.com/anfreire/wiki-spaces.git ~/src/wiki-spaces
~/src/wiki-spaces/scripts/install.py                     # same as `wiki-spaces install` but reads from the checkout
~/src/wiki-spaces/scripts/init_wiki.py ~/Wiki --git
~/src/wiki-spaces/scripts/doctor.py --no-net
```

The `scripts/*.py` shims are PEP 723 entries that forward to the `wiki_spaces` package — same code path as `uvx wiki-spaces`, just sourced from the local clone instead of PyPI.

## How it works

| | |
|---|---|
| **Discovery** | Skills read `~/.config/wiki-spaces/config` for the `wiki` path. CWD is a placement hint, never the discovery mechanism. |
| **Conventions** | Obsidian-flavored: wikilinks, frontmatter, tags, Bases, `.obsidian/` graph integration. All optional per space; presence-detected. |
| **Spaces** | Recursive (folders with their own `index.md`). Each is autonomous: own log, own taxonomy, own conventions. Shared/team spaces typically mount as git submodules under `shared/`. |
| **Trust scope** | Tools distinguish *owned* spaces (yours) from *external* mounts (under `shared/`, foreign submodules, out-of-tree symlinks). Reads cross owned spaces by default; writes stay in the targeted space; external mounts require explicit opt-in. |

## Read more

- [`AGENTS.md`](AGENTS.md) — the spec. One page.
- [`CONVENTIONS.md`](CONVENTIONS.md) — opt-in conventions catalog.
- [`references/`](references/) — agent-facing setup, examples, and mounting playbooks.
- [`skills/`](skills/) — the three reference skills.

## Dependencies

- **Python `>=3.11`** — the package's only hard runtime dep.
- [`uv`](https://docs.astral.sh/uv/) — strongly recommended. Runs `uvx wiki-spaces` ephemerally, `uv tool install wiki-spaces` permanently, and provisions Python automatically. Plain `pip install wiki-spaces` (or `pipx`) works as a fallback if you can't / won't use uv.
- `git` — optional. Recommended for backing up / sharing your wiki and for the dev-from-source flow.
- [`kepano/obsidian-skills`](https://github.com/kepano/obsidian-skills) — `obsidian-markdown` and `obsidian-bases` are vendored under [`vendor/kepano/`](vendor/kepano/) (shallow clone + sparse copy, pinned by SHA) and ship inside the wheel. The reference skills defer to `obsidian-markdown` for wikilink / frontmatter / callout / embed syntax. `obsidian-bases` is vendored for future `.base` view tooling; no current procedure uses it.

## Sharing wikis

Shared / collaborative spaces are best handled as git repositories embedded in your canonical wiki via submodules. Push access on the remote is the de facto write-permission layer. See [`CONVENTIONS.md` / Sharing & permissions](CONVENTIONS.md#sharing--permissions) and [`references/MOUNT.md`](references/MOUNT.md).

For local-only or private use, no git needed.

## Prior art

This project stands on three pieces of earlier work:

- **Andrej Karpathy's "LLM Wiki" gist** ([gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)) — the original idea: compile knowledge once into interconnected markdown files maintained by an LLM, instead of re-asking or re-RAG-ing.
- **[Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki)** — a broader framework implementing the LLM Wiki pattern with ~30 skills across many AI coding harnesses. wiki-spaces extracts a smaller spec, commits to a single canonical wiki, and adds nestability for shared content.
- **[kepano](https://github.com/kepano)** — Obsidian's lead designer, whose [`obsidian-skills`](https://github.com/kepano/obsidian-skills) are vendored as the syntax base.

The [AGENTS.md](https://agents.md/) standard is used for the root agent-instruction file, with symlinks for harnesses that prefer their own filename.

## License

MIT. See [`LICENSE`](LICENSE).
