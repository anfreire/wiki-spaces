# A folder's own space

Any folder can keep a space of its own: `.wiki-spaces/` at its root is an ordinary space, and the wiki every command resolves from anywhere inside the folder — a project's knowledge beside its code, say — so "save this" lands there without naming a wiki. Run this when the user asks ("set up a wiki for this project"); when a sync targets a folder that has none, offer it once — never assume.

The space lives in one of two places and a symlink joins the two. The resolver follows the link, so every command reads the same either way; what differs is what travels with what. One question, the user's call, never assumed:

- **Does the space ship with the repo?** Yes — it lives **with the folder**: committed, it reaches everyone who clones, and the wiki links to it. No — it lives **in the wiki**, with the wiki's history, and the folder carries only a link, kept out of the repo. A folder with no wiki to join keeps the space itself.

Look before creating. A space the wiki already keeps for the folder is reused, never re-made — of *In the wiki*, run the link and ignore lines alone. A `.wiki-spaces` already on disk, folder or link, is a stop: say what is there and let the user decide. A folder that is itself a wiki answers by its own contract and needs none. The blocks refuse a taken name — `mkdir` without `-p`, `test ! -e` before `ln -s`, which alone would land inside a directory — so a second run changes nothing.

## With the folder

```sh
FOLDER=/path/to/folder   # its root — the repo root, say
mkdir "$FOLDER/.wiki-spaces" && printf '# %s\n\n%s\n\n## Spaces\n' "<name>" "<what the space holds — one sentence, in their words>" > "$FOLDER/.wiki-spaces/index.md"
```

Then link it from the wiki (skip when the folder stands alone) — a symlink placed outside `shared/` is owned, like a clone:

```sh
FOLDER=/path/to/folder
WIKI=~/Documents/Wiki   # the configured wiki — the path the script notes from inside the folder
LINK="$WIKI/<the space it belongs under, else the root>/<name>"
test ! -e "$LINK" && ln -s "$FOLDER/.wiki-spaces" "$LINK"
python3 <skill-dir>/scripts/ws.py audit --wiki "$WIKI"   # paste the `missing entry` line it prints, trailing a `— description`
```

A git-tracked wiki stores the link's target, so a clone elsewhere sees a stale entry until the folder exists there too — the audit names it. From inside the folder the space answers to its own `_meta/limits.md` or the defaults; through the wiki's link, to the nearest limits above it — give the space its own when the wiki's differ.

## In the wiki

Create the space where it belongs — the skill's *Create a space* operation — then link it from the folder and, in a git repo, keep the link out of it:

```sh
FOLDER=/path/to/folder   # its root — the repo root, say
WIKI=~/Documents/Wiki    # the configured wiki
SPACE="$WIKI/<the space it belongs under, else the root>/<name>"
mkdir "$SPACE" && printf '# %s\n\n%s\n\n## Spaces\n' "<name>" "<what the space holds — one sentence, in their words>" > "$SPACE/index.md" &&
test ! -e "$FOLDER/.wiki-spaces" && ln -s "$SPACE" "$FOLDER/.wiki-spaces" &&
printf '\n/.wiki-spaces\n' >> "$FOLDER/.gitignore"   # anchored, no trailing slash: git matches a link by its bare name; the leading newline keeps a last line whole
python3 <skill-dir>/scripts/ws.py audit --wiki "$WIKI"   # paste the `missing entry` line it prints, trailing a `— description`
```

## Verify

`audit --wiki "$WIKI"` must be clean, and from anywhere inside the folder `python3 <skill-dir>/scripts/ws.py audit` — no `--wiki` — must resolve to the space. Close with: "`<name>` keeps its space at `.wiki-spaces/`; ask me to search or save from anywhere inside it."
