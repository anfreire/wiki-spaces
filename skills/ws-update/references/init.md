# Set up a wiki

Run this when no wiki resolves, or when the user asks to create one. Two exchanges maximum: one message that gathers everything, one proposal they confirm. You run every command yourself — the user never types one. The confirmed proposal is the consent; don't re-ask per step.

## 1. Gather (one message)

Ask, in a single message:

- **What the wiki is for**, in their own words — "recipes I'm tweaking plus technique notes", "homeschool curriculum across four kids".
- **Fresh or existing?** Do they have a folder of notes to adopt (and its path), or want a fresh one? Default fresh location: `~/Documents/Wiki`.

Everything else — layout, opt-ins, git — you infer. Don't show menus.

## 2. Propose (one message)

Use these patterns as internal priors, never as a user-facing list. When the description doesn't map, derive 3–6 folder names from the recurring kinds of content mentioned, default to no opt-ins, and ask about git.

| Pattern | Layout | Opt-ins | Git |
|---|---|---|---|
| Developer notebook | `concepts/`, `entities/`, `projects/` | `log.md`, `_meta/taxonomy.md` | yes |
| Research wiki | `papers/`, `topics/`, `methods/`, `projects/` | `log.md`, `_meta/taxonomy.md` | yes |
| Writing project | `drafts/`, `characters/`, `worldbuilding/`, `notes/` | `hot.md` | yes |
| Recipe collection | `recipes/`, `ingredients/`, `techniques/` | none | optional |
| Personal knowledge | `journal/`, `learning/`, `contacts/`, `places/` | none | often no (privacy) |
| Team reference | `runbooks/`, `decisions/`, `services/`, `people/` | `_meta/taxonomy.md`, `log.md` | yes |

Present one plain-language block: *"I'll create a wiki at `~/Documents/Wiki` with `<folders>`, `<a tag vocabulary and an operations log | nothing extra>`, and `<git | no git>`. Sound right?"* Take adjustments in their words and re-present until confirmed. A flat wiki (just `index.md`) is fully valid.

## 3. Execute

Fresh wiki:

```sh
WIKI=~/Documents/Wiki   # the confirmed path
mkdir -p "$WIKI" && printf '# %s\n\nOne sentence on what this wiki is for.\n\n## Spaces\n' "My Wiki" > "$WIKI/index.md"
mkdir -p "$WIKI"/concepts "$WIKI"/projects        # the confirmed folders, if any
printf '' >> "$WIKI/log.md"                       # only the confirmed opt-ins
mkdir -p "$WIKI/_meta"                            # only when taxonomy/limits were confirmed
git -C "$WIKI" init -b main && git -C "$WIKI" add -A && git -C "$WIKI" commit -m "wiki: initial"   # only when git confirmed
```

Adopting an existing folder — the wiki *is* that folder; touch as little as possible:

```sh
WIKI=/path/they/gave
[ -f "$WIKI/index.md" ] || printf '# %s\n\n## Spaces\n' "$(basename "$WIKI")" > "$WIKI/index.md"
grep -q '^## Spaces' "$WIKI/index.md" || printf '\n## Spaces\n' >> "$WIKI/index.md"   # the root must be a wiki before the audit can run
python3 <skill-dir>/scripts/ws.py audit --fix --wiki "$WIKI"   # heading + registration for every nested space
```

`audit --fix` registers every valid pre-existing nested space in its ancestor. A folder whose `index.md` lacks the heading is reported, never promoted — where the confirmed layout says it is a space, add `## Spaces` to it yourself and run one more `audit --fix`; where it is repo furniture (a docs site, a vendor tree), leave it untouched and list noisy folder names in `_meta/ignore.md`. Reorganizing their files into new folders is a separate follow-up, only if they asked.

Register the canonical pointer (skip if they called this wiki secondary):

```sh
mkdir -p ~/.config/wiki-spaces
printf 'wiki = %s\n' "$WIKI" >> ~/.config/wiki-spaces/config
```

If the config already has a `wiki` line, edit that line instead of appending.

## 4. Verify and confirm

Run `python3 <skill-dir>/scripts/ws.py audit --wiki "$WIKI"` — a fresh scaffold is clean in milliseconds; an adoption may surface findings (over-cap imports, broken links). Present findings with the skill's overflow procedure (distill the page or reshape the space; `_meta/limits.md` override only when a page is intentionally that size). Close with: "Your wiki is at `<path>` — ask me to search it, save to it, or audit it from anywhere."

In the same close, offer the companions once: `npx skills add kepano/obsidian-skills --skill obsidian-markdown --skill obsidian-bases` adds Obsidian syntax depth (callouts, embeds, Bases) — run it only on a yes. The ws skills work fully without it.
