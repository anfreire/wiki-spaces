# Promote a page into a space

`topic.md` becomes `topic/index.md`, gaining the right to hold children. This is the riskiest manual operation in the framework — it moves a file every other page may link to. Be defensive: snapshot, move, rewrite, verify, and stop at the first surprise.

## When

Any of: the page carries 3+ H2 sections that read as distinct sub-topics; siblings have accreted around it (`strategy.md`, `strategy-backtest.md`, `strategy-screening.md`); it is over cap and reads as a hub. Never promote an `index.md`, and never promote inside an external space.

## Procedure

1. **Snapshot first.** Git wiki: commit the current state (`git -C <root> add -A && git -C <root> commit -m "snapshot: before promoting <page>"`) — or, if the user minds the commit, `git stash push --include-untracked` and note the stash. No git: `cp -a <root> <root>.pre-promote` (or tar the affected space). Do not proceed without a restore path.
2. **Inventory every incoming link before moving anything:**
   ```sh
   python3 <skill-dir>/scripts/ws.py grep '<stem>' --wiki <root>
   ```
   The stem (filename without `.md`) rides every link form — `[[<stem>]]`, `[[dir/<stem>|alias]]`, `(<rel>/<stem>.md)`, embeds — so the sweep over-collects on purpose, trust-scoped (external mounts and `_archives/` stay out); judge each `rel:line:` hit and keep the real links (a code example or a prose mention is not one). The page's own self-links appear too; step 5 rewrites those. The kept list is your rewrite worklist and your verification baseline.
3. **Move:**
   ```sh
   mkdir <root>/<path>/<stem> && mv <root>/<path>/<stem>.md <root>/<path>/<stem>/index.md
   ```
   (`git mv` in a git wiki). Append a `## Spaces` heading to the new `index.md` if the page doesn't have one.
4. **Rewrite incoming links** from the worklist, one file at a time:
   - Wikilinks: `[[<stem>]]` → `[[<stem>/index|<stem>]]` (keep any existing `|display` text; keep `#heading` anchors).
   - Markdown links: `(<rel>/<stem>.md)` → `(<rel>/<stem>/index.md)` — recompute `<rel>` from each linking file's own directory; a link from a cousin directory needs a different prefix than a sibling's.
   - **Rewrite only the listed occurrences** — the worklist carries line numbers; anything not on it (a code example, say) stays as it is.
5. **Fix the moved page's own outgoing relative links** — it now lives one level deeper, so every relative markdown link in it gains a `../` prefix. Wikilinks resolve by name and usually survive unchanged.
6. **Register the new space** in the nearest ancestor's `## Spaces` — `- [<stem>/](<stem>/index.md) — description`; the audit's `missing entry` finding prints the exact line if in doubt.
7. **Verify:** `python3 <skill-dir>/scripts/ws.py audit --wiki <root>` must report no drift and the new space registered; then re-run step 2's sweep — every worklist occurrence must now speak the new form (`[[<stem>/index|…]]`, `(…/<stem>/index.md)`), and a hit still carrying the old bare form outside a code example is a miss to fix. Then `check-size` the new `index.md`.
8. **On any surprise, restore the snapshot** (`git reset --hard` to the snapshot commit / `git stash pop` / copy back), report what blocked, and stop. A half-promoted page is worse than an over-cap one.

The move is mechanics; splitting the content is authorship. Once the structure verifies clean, carve the H2 sections into child pages as a normal `ws-update` pass.
