# Promote a page into a space

`topic.md` becomes `topic/index.md`, gaining the right to hold children. This is the riskiest manual operation in the framework — it moves a file every other page may link to. Be defensive: snapshot, move, rewrite, verify, and stop at the first surprise.

## When

Any of: the page carries 3+ H2 sections that read as distinct sub-topics; siblings have accreted around it (`strategy.md`, `strategy-backtest.md`, `strategy-screening.md`); it is over cap and reads as a hub. Never promote an `index.md`, and never promote inside an external space.

## Procedure

1. **Snapshot first.** Git wiki: commit the current state (`git -C <root> add -A && git -C <root> commit -m "snapshot: before promoting <page>"`) — or, if the user minds the commit, `git stash push --include-untracked` and note the stash. No git: `cp -a <root> <root>.pre-promote` (or tar the affected space). Do not proceed without a restore path.
2. **Find every incoming link before moving anything:**
   ```sh
   python3 <skill-dir>/scripts/ws.py grep -i '\[\[<stem>|\(<stem>\.md|/<stem>\.md' --wiki <root>
   ```
   (wikilink and markdown-link forms; `<stem>` = filename without `.md` — slugs need no regex escaping). The search is trust-scoped: external mounts and `_archives/` stay out. Keep the list — it is your rewrite worklist and your verification baseline.
3. **Move:**
   ```sh
   mkdir <root>/<path>/<stem> && mv <root>/<path>/<stem>.md <root>/<path>/<stem>/index.md
   ```
   (`git mv` in a git wiki). Append a `## Spaces` heading to the new `index.md` if the page doesn't have one.
4. **Rewrite incoming links** from the worklist, one file at a time:
   - Wikilinks: `[[<stem>]]` → `[[<stem>/index|<stem>]]` (keep any existing `|display` text; keep `#heading` anchors).
   - Markdown links: `(<rel>/<stem>.md)` → `(<rel>/<stem>/index.md)` — recompute `<rel>` from each linking file's own directory; a link from a cousin directory needs a different prefix than a sibling's.
   - **Skip matches inside fenced code blocks and inline code spans** — they are examples, not links. Check each match's context before editing.
5. **Fix the moved page's own outgoing relative links** — it now lives one level deeper, so every relative markdown link in it gains a `../` prefix. Wikilinks resolve by name and usually survive unchanged.
6. **Register the new space** in the nearest ancestor's `## Spaces` — `- [<stem>/](<stem>/index.md) — description`; the audit's `missing entry` finding prints the exact line if in doubt.
7. **Verify:** `python3 <skill-dir>/scripts/ws.py audit --wiki <root>` must report no broken wikilinks and no drift; re-run the step-2 search — every remaining match should be inside code blocks or inline code spans. Then `check-size` the new `index.md`.
8. **On any surprise, restore the snapshot** (`git reset --hard` to the snapshot commit / `git stash pop` / copy back), report what blocked, and stop. A half-promoted page is worse than an over-cap one.

The move is mechanics; splitting the content is authorship. Once the structure verifies clean, carve the H2 sections into child pages as a normal `ws-update` pass.
