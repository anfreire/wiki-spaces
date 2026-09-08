# Conventions

This catalog lists the opt-in conventions a wiki can adopt. Every section is independent: most are markers on disk (pick the ones that fit your wiki), some are adopted by shape (a wiki opts in by already writing that way), and a few are skill defaults that say so. Skills degrade where a marker is absent. The spec defines `index.md` with a `## Spaces` heading as the required floor. Everything here is layered on top.

Skills are LLM-driven procedures that read these markers and degrade gracefully. They're self-contained after installation. A bundled read-only script `scripts/ws.py` per skill handles local operations.

Obsidian-flavored markdown is the wire format. Syntax facts (wikilinks, frontmatter, callouts, embeds, comments, Bases) live in the kepano skills.

---

## Discovery via config

The optional config file `~/.config/wiki-spaces/config` (under `$XDG_CONFIG_HOME` when that is set) is plain text. Its only key is the `wiki` pointer naming your canonical personal wiki:

```
# wiki-spaces config
wiki = /home/you/Wiki
```

The path must be absolute; relative paths and unknown lines are ignored. The config key is the final fallback in the resolution order, which — along with the ambiguity rules and trust scope — is defined in [AGENTS.md](AGENTS.md), not here. If nothing resolves, the operation cannot proceed; `ws-update` offers setup.

---

## Markers catalog

### `log.md`

Optional append-only notes. When present, skills append one ISO-8601-UTC-timestamped line per operation. No structured-field schema is enforced. Skills never rotate a log mid-operation; when the audit flags `log.md` over its cap, the repair is an archive roll — move it to `_archives/log-<YYYYMMDD>.md` and start a fresh `log.md`. `_archives/` is invisible to walks, so rolled logs stay out of search, and the history is never truncated. Concurrency locks are not guaranteed.

Example shell one-liner to append (details ride a `%s` argument, never the format string — a `%` in them would corrupt the line):
```sh
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 'UPDATE pages=2' >> log.md
```

If absent, skills skip logging.

### `_meta/taxonomy.md`

Canonical tag vocabulary for YAML frontmatter `tags:` fields. When present, skills normalize tags to this list, suggest new tags appearing on multiple pages, and reject unknown one-off tags.

Document shape:

```markdown
# Tag Taxonomy

Constraints: max 5 tags per page, lowercase or hyphenated.

## Domain Tags

| Tag | Purpose | Aliases |
|---|---|---|
| `python` | Python language, ecosystem | |

## Type Tags

| Tag | Purpose |
|---|---|
| `how-to` | Step-by-step procedure |
```

If absent, tags are free-form.

### `_meta/limits.md`

Size discipline is default-on; this file is where the user sets caps that differ from the defaults — a skill reads it and never raises a cap to fit a write. Caps are enforced in UTF-8 bytes. Frontmatter's included in the count. Basename-keyed defaults apply:

| Basename | Cap (bytes) |
|---|---|
| `index.md` | 5000 |
| `log.md` | 100000 |
| `hot.md` | 100000 |
| `*.md` | 15000 |

Shrinking writes are always allowed — `check-size --stdin` reports a planned write that shrinks an over-cap file as `ok … shrinking write is progress`. Size checks run via `python3 scripts/ws.py check-size` — planned content piped via `--stdin`, or the file on disk right after an edit, where the verdict is the cap alone. The `scripts/ws.py audit` command flags over-cap files after the fact. Plain `basename: bytes` lines configure the limits file; the literal name `*.md` re-caps the catch-all row above. Non-matching lines are ignored. No glob patterns, paths, or first-match-wins chains are supported — `*.md` is a reserved name, not a glob.

Any space can carry its own `_meta/limits.md`. The nearest one at or above a file governs it — closest ancestor wins, the same rule `_template.md` uses. The lookup never crosses a trust boundary: an external space answers to its own limits or the defaults, never the host's. A limits file under `shared/` sits on the external side of the fence and covers mounts beneath it that lack their own; a path outside the wiki answers to the defaults alone.

Example:
```
index.md: 5000
hot.md: 100000
custom-page.md: 8000
*.md: 20000
```

If absent, the defaults apply.

### `_meta/ignore.md`

Folder names the filesystem walk skips, one plain name per line — the user-extensible sibling of the built-in reserved names (`_archives/`, `_meta/`, dot-dirs). `#` lines are comments; every other non-blank line is read as a name, so keep prose behind `#`. Paths and globs are not supported, and a listed name is silenced at any depth below the declaring file — prefer a deeper `_meta/ignore.md` when the name is common. The nearest file at or above a folder governs it (the `limits.md` rule), and the lookup never crosses a trust boundary.

```
# vendor trees a repo-root wiki drags in
node_modules
target
dist
```

Ignoring silences implicit discovery only: `files`, `grep`, and `audit` stop descending into a matching folder, but a space explicitly registered in `## Spaces` is still reached through the contract. If absent, only the built-in reserved names are skipped.

### Frontmatter schema

Optional YAML metadata at the top of content pages:

```yaml
---
title: >-
  Page Title
category: concepts
tags: [python, how-to]
aliases: [alternate-name]
sources: [project-name, url]
summary: >-
  Short summary under 200 characters.
created: 2026-05-14T00:00:00Z
updated: 2026-05-14T00:00:00Z
---
```

Timestamps are UTC ISO-8601. Special files like `index.md` and `log.md` are exempt. If absent, skills write plain markdown.

### `_template.md`

When present in a folder, new pages created in that folder use this file as boilerplate. The closest ancestor template wins. The skills' link sweep skips a template's body — placeholder links are examples, and they earn no incoming credit. If absent, new pages match the wiki's existing page shape.

### `hot.md`

Free-form scratchpad for active work. Skills read it for context but never rewrite it. Users own the content. Default cap is 100,000 bytes. If absent, skills ignore it.

### `.git`

Presence of `.git` indicates the wiki is a git repository. Skills can surface git status in reports. Automatic commits or pushes are never performed. If absent, git context is skipped.

### `AGENTS.md`

A one-page contract note at the wiki root for harnesses that open the wiki without the skills installed: the `## Spaces` rule, the caps, trust scope, the dialect, and a pointer to the spec. `ws-update`'s setup offers it once; `CLAUDE.md` / `GEMINI.md` symlinks serve the harnesses that read those names. If absent, nothing changes for the skills.

---

## `## Items` sections

An optional curated section in a space's `index.md`, listing notable files as ordinary markdown links:

```markdown
## Items

- [notes.md](notes.md) — top notes
```

It is human navigation, not contract: tools traverse only `## Spaces`, and drift in `## Items` surfaces through the skills' dead-link sweep, not as registration findings. Adopted by shape, not by a marker: skills maintain one only where the index already has it. If absent, files are found by traversal alone.

---

## Page template

Body structure for content pages — adopted by shape, not by a marker: a wiki opts in by writing its pages this way, and skills keep new pages to the shape existing pages carry (`_template.md`, when present, wins):

```markdown
# Page Title

One-paragraph summary.

## Key Ideas

- A fact explicitly stated by the source or codebase.
- A generalization drawn from the source. %%inferred%%
- A claim where sources disagree. %%ambiguous%%

## Open Questions

Unresolved items.
```

If absent, pages are free-form.

---

## Provenance markers

A skill default, not a marker — nothing on disk opts in. Skills mark the claims they write; existing pages are left as they are. Inline comments indicating epistemic status:

| State | Marker | Meaning |
|---|---|---|
| Extracted | *(no marker)* | Stated directly by source |
| Inferred | `%%inferred%%` | Synthesized or implied |
| Ambiguous | `%%ambiguous%%` | Sources disagree |

Place the marker at the end of the claim.

---

## Categorical layout

Folder convention for placing pages by kind — adopted by shape, not by a marker: the folders themselves are the opt-in. Example folders:

| Use case | Common top-level folders |
|---|---|
| Developer notebook | `concepts/`, `entities/`, `skills/`, `projects/` |
| Research wiki | `papers/`, `topics/`, `methods/` |

Reserved folder names, honored at any depth:

| Name | Behavior |
|---|---|
| `shared/` | External by default (the exact lowercase name) — trust scope is defined in [AGENTS.md](AGENTS.md). |
| `.wiki-spaces/` | A folder's own space, kept at its root and resolved as the wiki from anywhere inside the folder — discovery is defined in [AGENTS.md](AGENTS.md). Dot-prefixed, so no walk enters it as a child. |
| `_archives/` | Excluded from audits and scans. |
| `_meta/` | Configuration files. |
| `.obsidian/` | Obsidian's vault configuration — never read, never written. |
| `.git/` | Hidden directory, skipped. |

Dot-prefixed names are reserved at any depth, files and folders alike — Obsidian cannot display them either. Extend the skipped set per space with [`_meta/ignore.md`](#_metaignoremd).

If absent, pages are written flat at the root.

---

## Linking rules

A skill default, not a marker — nothing on disk opts in. Skills hold to it for the links they add; a wiki that links more densely is left as it is.

- Add up to 2 relevant wikilinks per page.
- Link the first natural mention only.
- Use the shortest link that resolves unambiguously.

---

## Noise filter

A skill default, not a marker — nothing on disk opts in. Before writing a page, apply these checks:

- Code answers it? Skip.
- Quick search answers it? Skip.
- Needed in 3 months? Keep.
- Already there? Merge instead of creating a new page.

Skip this filter for content-store use cases.
