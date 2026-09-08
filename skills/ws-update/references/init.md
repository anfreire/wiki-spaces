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
mkdir -p "$WIKI" && printf '# %s\n\n%s\n\n## Spaces\n\n' "<title>" "<purpose — one sentence, in their words from the interview>" > "$WIKI/index.md"
# Per confirmed folder — scaffold it as a space and register it. The
# interview's own phrases become the descriptions: the placement hints
# every later operation reads. A folder the user only sketched can stay
# a plain dir (drop both lines); structure can always be promoted later.
mkdir -p "$WIKI/<folder>" && printf '# %s\n\n## Spaces\n' "<Folder title>" > "$WIKI/<folder>/index.md"
printf '%s\n' '- [<folder>/](<folder>/index.md) — <its purpose, from the interview>' >> "$WIKI/index.md"
# Each confirmed opt-in must exist on disk — a marker that was agreed to
# but never created is silently skipped by every skill, forever.
printf '' >> "$WIKI/log.md"                       # log opt-in
printf '' >> "$WIKI/hot.md"                       # scratchpad opt-in
mkdir -p "$WIKI/_meta"                            # only when taxonomy/limits were confirmed
# Custom caps opt-in: plain `basename: bytes` lines (see CONVENTIONS.md).
printf '%s\n' '<basename>.md: <bytes>' > "$WIKI/_meta/limits.md"
# Taxonomy opt-in: seed the vocabulary from the interview's recurring
# content kinds (the CONVENTIONS.md document shape); the user grows it.
cat > "$WIKI/_meta/taxonomy.md" <<'EOF'
# Tag Taxonomy

Constraints: max 5 tags per page, lowercase or hyphenated.

## Domain Tags

| Tag | Purpose | Aliases |
|---|---|---|
| `<domain-tag from the interview>` | <what it covers> | |

## Type Tags

| Tag | Purpose |
|---|---|
| `how-to` | Step-by-step procedure |
| `reference` | Facts to look up, not read through |
EOF
git -C "$WIKI" init -b main && git -C "$WIKI" add -A && git -C "$WIKI" commit -m "wiki: initial"   # only when git confirmed
```

Adopting an existing folder — the wiki *is* that folder; touch as little as possible:

```sh
WIKI=/path/they/gave
[ -f "$WIKI/index.md" ] || printf '# %s\n\n## Spaces\n' "$(basename "$WIKI")" > "$WIKI/index.md"
# Adopting a code repository? Seed the skip list with the vendor trees
# you can see BEFORE the first audit — a node_modules sweep helps nobody.
# Adjust to the tree; skip this line for a plain notes folder.
[ -f "$WIKI/_meta/ignore.md" ] || { mkdir -p "$WIKI/_meta" && printf 'node_modules\ndist\nbuild\ntarget\n' > "$WIKI/_meta/ignore.md"; }
python3 <skill-dir>/scripts/ws.py audit --wiki "$WIKI"   # refusals and findings both name their repair
```

A `not a wiki` refusal is round zero, and its message names the repair:

- An index that lacks the heading takes the append: `printf '\n## Spaces\n' >> "$WIKI/index.md"`.
- A near-miss it quotes (`## Spaces ##`, `## spaces`) takes a rename of that line to exactly `## Spaces`, its entries kept — a second heading beside a near-miss would orphan them.

Then apply the audit's findings as edits, re-running it between rounds until the structural ones are gone — each round re-derives from disk, so the repairs converge in any order:

- Paste each `missing entry` line into the index it names.
- Where the confirmed layout says a bare folder is a space, add `## Spaces` to its index — the next round then registers whatever lives beneath it, and flags any entry the new boundary invalidates.
- Where a bare folder is repo furniture (a docs site, a vendor tree the seed above missed), leave it untouched and add its name to `_meta/ignore.md`.

Reorganizing their files into new folders is a separate follow-up, only if they asked.

Register the canonical pointer (skip if they called this wiki secondary):

```sh
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"/wiki-spaces/config   # the path the resolver reads
mkdir -p "$(dirname "$CFG")"
[ -f "$CFG" ] && grep -v '^[[:space:]]*wiki[[:space:]]*=' "$CFG" > "$CFG.tmp"
printf 'wiki = %s\n' "$WIKI" >> "$CFG.tmp"
mv "$CFG.tmp" "$CFG"
```

The rewrite drops any previous `wiki` line and keeps everything else: the resolver takes the *first* valid `wiki` line, so a stale one would silently shadow an appended replacement.

## 4. Verify and confirm

Run `python3 <skill-dir>/scripts/ws.py audit --wiki "$WIKI"` — a fresh scaffold is clean in milliseconds; an adoption may surface findings (over-cap imports, contract drift). Present findings with the skill's overflow procedure (distill the page, reshape the space, or promote; a cap is the user's to change). Close with: "Your wiki is at `<path>` — ask me to search it, save to it, or audit it from anywhere."

In the same close, offer the companions once: `npx skills add kepano/obsidian-skills --skill obsidian-markdown --skill obsidian-bases` adds Obsidian syntax depth (callouts, embeds, Bases) — run it only on a yes. The ws skills work fully without it.

Offer once, likewise: a one-page `AGENTS.md` at the wiki root, so a harness that opens this wiki *without* the skills installed still learns the contract. On a yes:

```sh
cat > "$WIKI/AGENTS.md" <<'EOF'
# This folder is a wiki-spaces wiki

- Navigation: every `index.md` carries a `## Spaces` heading exhaustively listing the spaces (sub-wikis) directly inside. Keep it true when you add, move, or remove a space.
- Size caps, UTF-8 bytes (defaults; the user's `_meta/limits.md` overrides): `index.md` 5,000; content pages 15,000; `log.md`/`hot.md` 100,000. Over the cap: distill the page, reshape the space, or promote — never truncate, never raise the cap.
- Anything under `shared/` is someone else's space: read it on request, write only on explicit instruction.
- Dialect: Obsidian-flavored markdown (wikilinks, frontmatter, callouts); plain CommonMark is always valid.
- Spec, conventions, and skills: https://github.com/anfreire/wiki-spaces
EOF
```

Claude Code and Gemini CLI read the same note under their own names — on a yes for those harnesses: `ln -s AGENTS.md "$WIKI/CLAUDE.md"` (likewise `GEMINI.md`).
