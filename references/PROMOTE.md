# Promote a page to a space

A `.md` file that has grown into multiple distinct topics, accreted siblings, or now represents a recurring kind is a candidate for promotion to its own space. `ws-update` recognizes the trigger during its size-discipline step and defers here for the full procedure; `wiki-spaces space promote` does the mechanical move.

## When to consider promotion

Any of:

- Page exceeds ~300 lines of body content.
- Page has 3+ H2 sections covering distinct sub-topics.
- Sibling pages have accreted around the topic (e.g. `strategy.md`, `strategy-backtest.md`, `strategy-screening.md`) that would read more naturally as children of a `strategy/` space.
- New content's intent suggests the existing page has become a hub.

## Procedure

1. Identify the file. Confirm it's not already an `index.md` and not in an external space.
2. Run `wiki-spaces space promote <path>` (wiki-root-relative). Preview with `--dry-run` first if the wiki has many cross-references.
3. The CLI:
   - moves the file to `<basename>/index.md`,
   - rewrites markdown links in consumer-visible pages — the contract-reachable set plus the ancestor space's soon-visible siblings (drift / unregistered spaces, hidden, `_archives/`, `_meta/` are left to `audit --fix`, not promote); path-aware: only links resolving to the promoted file are touched, hrefs recomputed relative to each linking file's directory so deep cross-links stay correct,
   - rewrites wikilinks pointing to the promoted file (all forms — bare, display, anchored, pathful — with display preserved),
   - adjusts the promoted file's outgoing relative links for its new depth (one extra `../`),
   - adds `aliases: [<basename>]` to the new `index.md` for forward-compatible wikilink resolution (skip with `--skip-aliases` if another page already claims the alias),
   - ensures the new `index.md` has `## Spaces` from t=0 — matches `space add`,
   - registers the new space's `## Spaces` entry in the nearest ancestor (uses the file's frontmatter `summary` for the description if present).
4. Read the new `index.md`. If sections read like standalone children, capture them as separate `.md` files under the new space in a follow-up `ws-update` cycle. The CLI deliberately does not split content — that's authorship, not mechanics.

## Atomicity

The CLI snapshots every affected file to a system tempdir (outside the wiki tree) before mutating disk and restores from the snapshot if anything fails. Works on both git-tracked and untracked wikis. The snapshot is removed once the promote succeeds (or after a successful rollback); if a rollback itself fails, the snapshot is kept and its path is reported on stderr so you can recover by hand.

## Refuses if

Target dir exists with content; path is external (or descends from an external scope); another owned page already claims the alias `<basename>` case-insensitively (use `--skip-aliases` to bypass). When the parent's `index.md` lacks `## Spaces`, promote auto-inserts the heading inside the locked ancestor mutation — no refusal, no prior setup needed.
