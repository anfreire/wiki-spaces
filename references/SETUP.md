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

This briefing drives the **full installation** (skills + scaffold). For a no-install Tier 1 start — folder + `index.md` — see the README's `## Start` section. The skills resolve the target wiki via **explicit path → config → CWD ancestor**, so a no-install wiki still works as long as the agent invokes the skills with the wiki path or from inside it.

For the full installation, verify the user's machine has one of:
- **Recommended:** [`uv`](https://docs.astral.sh/uv/) on PATH (`curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`). Used for `uvx wiki-spaces …` (no install) or `uv tool install wiki-spaces` (permanent). uv provisions Python automatically.
- **Fallback:** plain Python `>=3.11` + `pip` (or `pipx`). Use `pip install --user wiki-spaces` (or `pipx install wiki-spaces`) if uv is unavailable.

`git` is **optional** — recommended for backing up the wiki and for the dev-from-source flow, but not required for install. If the user only wants the Tier 1 path, redirect them to the README; preflight isn't needed.

## Default path

Default to **Branch A** below — it sets up the user's one canonical wiki, whether scaffolded fresh or adopted from a folder of notes they already have. That's what almost every user wants. Switch to **Branch B** *only* if the user's request explicitly names a *separate, external* wiki to mount as a space inside theirs (e.g. *"add the team wiki at github.com/foo/wiki as a space"*). Don't show a menu — pick the branch from the user's words.

## Branch A: Set up the canonical wiki

**Keep the interview to two exchanges with the user** — one message that gathers everything, one proposal they confirm. **You run every command yourself**; the user never types one. The proposal in step 2 doubles as consent to run the setup — don't ask permission step by step, and don't walk the user through commands.

The commands below show the recommended `uvx` form (no install). If wiki-spaces is already installed (`uv tool install wiki-spaces` / `pip install wiki-spaces`), drop the `uvx` prefix — every `uvx wiki-spaces …` line is equivalent to `wiki-spaces …` once installed.

1. **Ask once — gather everything in a single message.** In one message, ask the user:
   - **What the wiki is for**, in their own words (one or two sentences) — *"recipes I'm tweaking, plus technique notes"*, *"homeschool curriculum across four kids"*.
   - **Whether they already have a folder of notes** (or an existing wiki) they want to use — and if so, its path — **or want a fresh one.** Default location for a fresh wiki: `~/Wiki`.

   Don't show a menu and don't split this across messages. Everything else — folder layout, opt-in conventions, git, display name — you infer; don't ask.

2. **Infer the layout, then present one proposal.** Use the patterns below as **internal priors**, never a user-facing list. When the description matches a pattern (cleanly or partially), take its folders, opt-in bundle, and git default. When it doesn't map (*"game design notes," "law firm casebook"*), derive 3-6 folder names from the recurring kinds of content mentioned, default to no opt-in bundle, and default git to "ask."

   | Pattern | Suggested layout | Opt-in bundle | Git |
   |---|---|---|---|
   | **Developer notebook** | `concepts/`, `entities/`, `skills/`, `projects/` | `log.md` + `_meta/taxonomy.md` + `.manifest.json` | yes |
   | **Research wiki** | `papers/`, `topics/`, `methods/`, `datasets/`, `projects/` | `log.md` + `_meta/taxonomy.md` | yes |
   | **Writing project** | `drafts/`, `characters/`, `worldbuilding/`, `notes/`, `archive/` | `hot.md` (current piece) | yes |
   | **Recipe collection** | `recipes/`, `ingredients/`, `techniques/`, `meal-plans/` | (none recommended) | optional |
   | **Personal knowledge** | `journal/`, `learning/`, `contacts/`, `places/`, `interests/` | (none recommended) | optional, often no (privacy) |
   | **Team reference** | `runbooks/`, `decisions/`, `services/`, `people/`, `clients/` | `_meta/taxonomy.md` + `log.md` | yes |

   Present the result in one plain-language block — never enumerate internal files like `log.md` or `.manifest.json` as menu items: *"I'll &lt;create a wiki at ~/Wiki | adopt your folder at ~/notes&gt;, set up &lt;folders&gt;, &lt;a tag vocabulary and an audit log / nothing extra&gt;, and &lt;initialize git / skip git&gt;, then link the skills into your AI tools. Sound right, or adjust anything?"* Take adjustments in the user's own words (*"rename recipes to desserts"*, *"skip git"*) and re-present until confirmed. If they ask what an opt-in does, answer in 1-2 sentences. Flat wikis (no folders) are fully valid — the proposal can be just `index.md`. See `references/EXAMPLES.md` for full shape examples.

   **Adopting an existing folder.** If the user pointed at a folder they already have, the wiki *is* that folder. Adopt it with `wiki-spaces init <their-path> --tier1`: it adds `index.md` only if missing and never touches existing files. `--tier1` makes that added `index.md` a plain Tier 1 root (no `## Spaces` section) — important, because any folders the user already nests would otherwise read as unregistered spaces and an audit would report immediate drift. A fresh wiki, by contrast, omits `--tier1` and gets the usual Tier 2 `## Spaces` from t=0.
   - *Port as-is* (default) — `init <path> --tier1` with no `--folders`: leaves their structure untouched.
   - *Reorganize* — only if the user asks. Add `--folders` for new folders; moving existing files into them is a follow-up you do by hand after `init`, with the user's say-so.

   Offer this choice once, inside the step-2 proposal (*"adopt it as-is, or also organize it into folders?"*) — never a separate round.

3. **Execute — you run all of it, no user commands.** In sequence:
   1. `uvx wiki-spaces install` (detected harnesses only; add `--all` only if the user wants skills pre-positioned for every supported harness). It installs `wiki-search`/`wiki-update`/`wiki-tend` plus vendored kepano skills, copies `AGENTS.md`/`CONVENTIONS.md`/`references/` to `~/.local/share/wiki-spaces/`, and writes that as `repo` in the config. Verify it printed "Wrote repo path to ...".
   2. `uvx wiki-spaces init <wiki-path> [--name <display-name>] [--description "<one-sentence purpose>"] [--with <opt-ins>] [--folders <names>] [--git] [--tier1]`. `<wiki-path>` is the new location for a fresh wiki, or the user's existing folder for an adoption — pass `--tier1` for an adoption (a Tier 1 root with no `## Spaces`, so folders the user already nests don't read as drift). Pass the user's one-sentence purpose as `--description` so it lands in `index.md`'s "What this space is" verbatim. `init` creates `index.md` (skipped if already present), writes `--with` opt-in files, creates each `--folders` directory (with a `.gitkeep` under `--git`), runs `git init -b main` under `--git`, and writes `wiki = <wiki-path>` to the config. Omit `--folders` for a flat wiki or a port-as-is adoption. Verify it printed "registered as canonical wiki in ...".
   3. `uvx wiki-spaces doctor --no-net`. Both `wiki` and `repo` should be `OK`.

4. **Confirm.** "Setup complete. Your wiki is at `<wiki-path>`. Just ask me to search, save to, or audit your wiki from anywhere — I'll find it via the config."

## Branch B: Mount an external wiki as a space

See [`MOUNT.md`](MOUNT.md) for the full playbook and trade-offs. Quick summary:

1. The user must already have a canonical wiki set up (Branch A above). Confirm `wiki` is set in the config and valid.
2. Identify the external wiki: a git URL, a local clone, or a folder with `index.md`.
3. Decide the mount mechanism with the user: submodule (collaborative; recommended), clone (read-only one-time fetch), or symlink (local convenience).
4. Decide the path inside the canonical wiki: typically `<wiki>/shared/<name>/` for team wikis.
5. Run `uvx wiki-spaces space mount <source> shared/<name> --as <mechanism>` — one command that executes the mount, verifies the result has `index.md`, and adds the `## Spaces` entry. It refuses on a Tier 1 parent (no `## Spaces`); if that happens, add `## Spaces` to the parent's `index.md` and rerun.

## Edge cases

- **Wiki path in config but folder doesn't exist on disk.** Tell the user, ask if they want to (a) re-scaffold (here or at a different path — `wiki-spaces init` updates the config) or (b) restore the folder from a backup.
- **Wiki path exists but no `index.md`.** Same — re-scaffold or restore.
- **`repo` key in config but path doesn't exist.** Re-run `uvx wiki-spaces install --all` (or `wiki-spaces install --all`) to refresh the share dir and rewrite `repo`. For dev-from-source users, ensure the clone is back at the recorded path then run `scripts/install.py --all`.
- **`wiki-spaces install` (default detection) reports "No harnesses selected".** The user has none of the 6 supported harnesses on disk. Either ask whether to pre-position skills via `--all` (creates skill dirs for every supported harness), or — if they only use Cursor / Windsurf / GitHub Copilot / Aider — point them at [`HARNESS_INTEGRATION.md`](HARNESS_INTEGRATION.md) for manual snippets and skip the skills install entirely.
- **Description doesn't cleanly match a canonical pattern.** Don't force the user into one. Identify the recurring kinds of content they mentioned and translate those into 3-6 folder names directly. Default to no opt-in bundle (offer them, but let the user opt in later as the wiki grows). Default git to "ask."
- **User wants a flat wiki (no folders).** Omit `--folders` from the `wiki-spaces init` invocation. `wiki-update` will write pages at the wiki root or ask where to place. Fully valid; `index.md` is the only required file.
- **User gives a description so short it doesn't suggest folders** (e.g., *"general notes"*). Ask one follow-up: "What recurring kinds of content do you expect — even a rough list?" If still vague, propose a flat wiki and offer to grow folders later.
- **User wants to work from a source checkout (dev).** They `git clone https://github.com/anfreire/wiki-spaces.git ~/src/wiki-spaces`, then use `~/src/wiki-spaces/scripts/install.py`, `…/init_wiki.py`, `…/doctor.py` (same code path as the console script; reads/writes from the checkout instead of `~/.local/share/wiki-spaces/`).

## After setup

The user can invoke skills via their AI coding harness:
- "What do I know about X?" → `wiki-search`
- "Save this conversation as a note" → `wiki-update`
- "Audit my wiki" → `wiki-tend`

Discovery resolution is **explicit path → config → CWD ancestor**. Skills follow that order on every invocation, so they always know which wiki to operate on — even before the config exists, as long as the agent is running from inside a folder with `index.md`. Once the wiki is resolved, CWD informs *placement* (project-scoped vs global) but doesn't override the resolved target. A user in `~/Documents/Projects/foo/` who asks "save this concept about Python" will have it routed to a global folder (e.g., `<wiki>/concepts/` for a developer notebook), not `<wiki>/projects/foo/`, because the content is global. The agent uses the user's words and content to decide placement — CWD is just a hint there.

## Reference docs at the wiki-spaces repo

- `AGENTS.md` — the spec (one page; what counts as a wiki)
- `CONVENTIONS.md` — opt-in conventions catalog (frontmatter, taxonomy, linking, etc.)
- `references/EXAMPLES.md` — topology examples (six shapes spanning developer / research / writing / recipe / personal / team)
- `references/MOUNT.md` — mount external wiki as space
- `references/HARNESS_INTEGRATION.md` — Cursor/Windsurf/Copilot/Aider manual snippets
