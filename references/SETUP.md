# wiki-spaces setup briefing

You are an AI agent helping a user set up wiki-spaces — a minimal personal wiki for any use case. Drive the conversation. Ask the questions. Execute the operations. Don't dump this entire doc on the user; use it to know what to do.

## What wiki-spaces is

A wiki is a folder with `index.md`. The user maintains one canonical wiki — a folder of theirs, anywhere on disk. Inside, anything goes: files, plain folders, or other spaces (folders that themselves carry `index.md`). What the wiki is *for* is the user's choice — developer notes, research, writing, recipes, personal knowledge, team reference, anything. The tooling adapts to whatever shape the user picks.

Three reference skills (`wiki-search`, `wiki-update`, `wiki-tend`) operate on this canonical wiki. They locate it via `${XDG_CONFIG_HOME:-~/.config}/wiki-spaces/config` (i.e., `$XDG_CONFIG_HOME/wiki-spaces/config` if `XDG_CONFIG_HOME` is set, otherwise `~/.config/wiki-spaces/config`).

The full spec is in `AGENTS.md` (bundled with the install or accessible via `repo` below); the conventions catalog is `CONVENTIONS.md`.

## The two-key config

`~/.config/wiki-spaces/config` is plain text:

```
# wiki-spaces config
wiki = /home/<user>/Wiki
repo = /home/<user>/.local/share/wiki-spaces
```

- `wiki` — absolute path to the canonical wiki folder; must contain `index.md`.
- `repo` — absolute path to the wiki-spaces install (the share dir written by `wiki-spaces install`, or a source checkout when running from one); lets skills locate `AGENTS.md`, `CONVENTIONS.md`, and `references/`.

If the config is missing or `wiki` is unset, the user has not set up yet — drive the setup.

## Preflight (before anything)

This briefing drives the **full installation** (skills + scaffold). For a no-install Tier 1 start — folder + `index.md` + config — see the README's "Just start (no install)" section; no prerequisites needed there beyond a shell to create the files.

For the full installation, verify the user's machine has one of:
- **Recommended:** [`uv`](https://docs.astral.sh/uv/) on PATH (`curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`). Used for `uvx wiki-spaces …` (no install) or `uv tool install wiki-spaces` (permanent). uv provisions Python automatically.
- **Fallback:** plain Python `>=3.11` + `pip` (or `pipx`). Use `pip install --user wiki-spaces` (or `pipx install wiki-spaces`) if uv is unavailable.

`git` is **optional** — recommended for backing up the wiki and for the dev-from-source flow, but not required for install. If the user only wants the Tier 1 path, redirect them to the README; preflight isn't needed.

## Default path

Default to **Branch A** below — fresh install + scaffold. That's what almost every user wants. Switch to **Branch B** *only* if the user's request explicitly names an existing external wiki to mount as a space (e.g. *"add the team wiki at github.com/foo/wiki as a space"*). Don't show a menu — pick the branch from the user's words.

## Branch A: Fresh install + scaffold

The commands below show the recommended `uvx` form (no install). If the user has run `uv tool install wiki-spaces` or `pip install wiki-spaces`, drop the `uvx` prefix — every `uvx wiki-spaces …` line is equivalent to `wiki-spaces …` once installed.

1. **Install skills.** Run `uvx wiki-spaces install` (default — installs only into harnesses detected on disk). Add `--all` only if the user wants skills pre-positioned for every supported harness regardless of current installation. The command installs `wiki-search`, `wiki-update`, `wiki-tend`, plus vendored kepano skills (`obsidian-markdown`, `obsidian-bases`) into each selected harness's skills directory; it copies the bundled `AGENTS.md`/`CONVENTIONS.md`/`references/` to `~/.local/share/wiki-spaces/` and writes that path as `repo` in the config. Verify it printed "Wrote repo path to ...".

2. **Ask what the wiki is for, in the user's own words.** A one- or two-sentence description: *"I'll keep recipes I'm tweaking, plus notes on techniques and ingredient substitutions."* or *"Notes for my homeschool curriculum across four kids."* Don't show a menu — the user describes, you infer the layout.

   Use the patterns below as **internal priors**, never as a user-facing list. When the description matches a pattern (cleanly or partially), propose that pattern's folders, Standard Pack opt-ins, and git default. When it doesn't map (e.g., *"game design notes," "law firm casebook"*), derive 3-6 folder names from the recurring kinds of content the user mentioned, default to no Standard Pack (still offer the opt-ins), and default git to "ask."

   | Pattern | Suggested layout | Standard Pack | Git |
   |---|---|---|---|
   | **Developer notebook** | `concepts/`, `entities/`, `skills/`, `projects/` | `log.md` + `_meta/taxonomy.md` + `.manifest.json` | yes |
   | **Research wiki** | `papers/`, `topics/`, `methods/`, `datasets/`, `projects/` | `log.md` + `_meta/taxonomy.md` | yes |
   | **Writing project** | `drafts/`, `characters/`, `worldbuilding/`, `notes/`, `archive/` | `hot.md` (current piece) | yes |
   | **Recipe collection** | `recipes/`, `ingredients/`, `techniques/`, `meal-plans/` | (none recommended) | optional |
   | **Personal knowledge** | `journal/`, `learning/`, `contacts/`, `places/`, `interests/` | (none recommended) | optional, often no (privacy) |
   | **Team reference** | `runbooks/`, `decisions/`, `services/`, `people/`, `clients/` | `_meta/taxonomy.md` + `log.md` | yes |

   Recommendations, not rules — the proposal you'll present in step 4 is the user's starting point, not a final decision. See `references/EXAMPLES.md` for the full shape examples. Flat wikis (no folders) are fully valid; `index.md` is the only required file.

3. **Ask where the wiki should live.** Default: `~/Wiki/`. Confirm absolute path. While here, also ask the display name (shown in `index.md`) if the user wants something other than the directory basename.

4. **Present the inferred proposal and accept adjustments in natural language.** Show the user a summary derived from their description: *"Based on what you described, I'll scaffold `<folders>`, set up `<Standard Pack files in plain terms — e.g. 'tag vocabulary and an audit log'>`, and `<initialize git / skip git>`. Sound right, or do you want to adjust?"* Don't enumerate internal opt-in files as menu items — the user shouldn't need to know what `log.md` or `.manifest.json` is. Take adjustments in the user's own words (*"rename recipes to desserts"*, *"skip git"*, *"don't bother with tags yet"*) and re-present until confirmed. If they ask what an opt-in is for (*"what's the audit log?"*), answer in 1-2 sentences and continue. Flat wikis (no folders at all) are fully valid; the proposal can be just `index.md` when the description warrants it.

5. **Scaffold the wiki.** Run `uvx wiki-spaces init <wiki-path> [--name <display-name>] [--description "<one-sentence purpose>"] [--with <opt-ins>] [--folders <names>] [--git]`. Pass the user's one-sentence purpose (from step 2) as `--description` so it lands in `index.md`'s "What this space is" section verbatim, instead of leaving the placeholder. `init` creates `index.md`, writes any `--with` opt-in files, creates each `--folders` directory at the wiki root (with a `.gitkeep` placeholder when `--git` is set, so the empty dirs survive commit/clone), runs `git init -b main` when `--git` is set, and writes `wiki = <wiki-path>` to the config. Omit `--folders` for a flat wiki. Verify it printed "registered as canonical wiki in ...".

6. **Verify.** Run `uvx wiki-spaces doctor --no-net`. Both `wiki` and `repo` should be `OK`.

7. **Confirm.** "Setup complete. Your wiki is at `<wiki-path>`. Try invoking `wiki-search`, `wiki-update`, `wiki-tend` from anywhere — they'll find the wiki via the config."

## Branch B: Mount an external wiki as a space

See [`MOUNT.md`](MOUNT.md). Quick summary:

1. The user must already have a canonical wiki set up (Branch A above). Confirm `wiki` is set in the config and valid.
2. Identify the external wiki: a git URL, a local clone, or a folder with `index.md`.
3. Decide the mount mechanism: git submodule (collaborative; recommended), clone (read-only one-time fetch), or symlink (local convenience).
4. Decide the path inside the canonical wiki: typically `<wiki>/shared/<name>/` for team wikis, or `<wiki>/projects/<name>/` for project-scoped.
5. Execute the mount per `MOUNT.md`.
6. Update the parent `<wiki>/index.md`'s `## Spaces` section to add an entry for the new space.

## Edge cases

- **Wiki path in config but folder doesn't exist on disk.** Tell the user, ask if they want to (a) re-scaffold (here or at a different path — `wiki-spaces init` updates the config) or (b) restore the folder from a backup.
- **Wiki path exists but no `index.md`.** Same — re-scaffold or restore.
- **`repo` key in config but path doesn't exist.** Re-run `uvx wiki-spaces install --all` (or `wiki-spaces install --all`) to refresh the share dir and rewrite `repo`. For dev-from-source users, ensure the clone is back at the recorded path then run `scripts/install.py --all`.
- **`wiki-spaces install` (default detection) reports "No harnesses selected".** The user has none of the 5 supported harnesses on disk. Either ask whether to pre-position skills via `--all` (creates skill dirs for every supported harness), or — if they only use Cursor / Windsurf / GitHub Copilot / Aider — point them at [`HARNESS_INTEGRATION.md`](HARNESS_INTEGRATION.md) for manual snippets and skip the skills install entirely.
- **Description doesn't cleanly match a canonical pattern.** Don't force the user into one. Identify the recurring kinds of content they mentioned and translate those into 3-6 folder names directly. Default to no Standard Pack opt-ins (offer them, but let the user opt in later as the wiki grows). Default git to "ask."
- **User wants a flat wiki (no folders).** Omit `--folders` from the `wiki-spaces init` invocation. `wiki-update` will write pages at the wiki root or ask where to place. Fully valid; `index.md` is the only required file.
- **User gives a description so short it doesn't suggest folders** (e.g., *"general notes"*). Ask one follow-up: "What recurring kinds of content do you expect — even a rough list?" If still vague, propose a flat wiki and offer to grow folders later.
- **User wants to work from a source checkout (dev).** They `git clone https://github.com/anfreire/wiki-spaces.git ~/src/wiki-spaces`, then use `~/src/wiki-spaces/scripts/install.py`, `…/init_wiki.py`, `…/doctor.py` (same code path as the console script; reads/writes from the checkout instead of `~/.local/share/wiki-spaces/`).

## After setup

The user can invoke skills via their AI coding harness:
- "What do I know about X?" → `wiki-search`
- "Save this conversation as a note" → `wiki-update`
- "Audit my wiki" → `wiki-tend`

Skills always read the config first; they always know which wiki to operate on.

CWD informs *placement* (project-scoped vs global), never *discovery*. A user in `~/Documents/Projects/foo/` who asks "save this concept about Python" will have it routed to a global folder (e.g., `<wiki>/concepts/` for a developer notebook), not `<wiki>/projects/foo/`, because the content is global. The agent uses the user's words and content to decide placement — CWD is just a hint.

## Reference docs at the wiki-spaces repo

- `AGENTS.md` — the spec (one page; what counts as a wiki)
- `CONVENTIONS.md` — opt-in conventions catalog (frontmatter, taxonomy, linking, etc.)
- `references/EXAMPLES.md` — topology examples (six shapes spanning developer / research / writing / recipe / personal / team)
- `references/MOUNT.md` — mount external wiki as space
- `references/HARNESS_INTEGRATION.md` — Cursor/Windsurf/Copilot/Aider manual snippets
