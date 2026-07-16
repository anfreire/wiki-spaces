# Restructure a space: rename, move, merge, demote

Promotion's siblings — each operation relocates a space that other pages link into. Reuse [promote.md](promote.md)'s defensive skeleton: snapshot, inventory, move, rewrite, verify, and stop at the first surprise. Never restructure inside an external space, and when a reshape would touch more than the space the user named, confirm scope first.

## The shared skeleton

1. **Snapshot** exactly as [promote.md](promote.md) step 1 — no restore path, no restructure.
2. **Inventory incoming links** before moving anything — one sweep per name the operation changes (the space's own name, and each page whose path moves):
   ```sh
   python3 <skill-dir>/scripts/ws.py grep -F '<name>' --wiki <root>
   ```
   Judge each `rel:line:` hit and keep the real links — wikilinks, markdown links, embeds; a code example or a prose mention is not one. The kept union is the rewrite worklist and the verification baseline.
3. **Move** with `git mv` (a git wiki) or `mv`, then make every touched parent true again: the losing parent's `## Spaces` drops the entry, the gaining parent's adds it — the audit's `missing entry` finding prints the exact line to paste. Carry the entry's description along; update it if the reshape changed what the space holds.
4. **Rewrite links** from the worklist, one file at a time — only the listed occurrences (the worklist carries line numbers; a code example is not on it). When the space's depth changed, its own pages' relative links shift too (`../` per level, in both directions) — wikilinks resolve by name and usually survive.
5. **Verify:** `audit` must report no drift, every touched parent true; then re-run step 2's sweeps — each worklist occurrence must now speak the new name or path, and a hit still carrying an old form outside a code example is a miss to fix. `check-size` any index you grew.
6. **On any surprise, restore the snapshot and report** — a half-moved space is worse than an ill-named one.

## Rename (same parent, new name)

`mv <old-name> <new-name>`, then rewrite: the parent entry's label and href, every incoming path-form link, and the space's own `# title` if it spoke the old name. The new name follows the wiki's own naming pattern; the lowercase-hyphenated ≤50-char default applies only where none exists.

## Move (new parent)

Both parents' `## Spaces` change, and the moved space's depth usually does too — its pages' relative links gain or lose `../`. A move across a trust boundary (into or out of `shared/`) changes scope semantics — flag that to the user rather than moving silently.

## Merge (space A folds into space B)

Move A's content pages into B — on a filename collision rename to a self-standing name, never `page-2.md`. Fold anything worth keeping from A's `index.md` into B's, drop A's entry from its parent, delete the empty shell. Rewrite links that pointed at A's pages, and links to `A/index.md` itself now target B's index or the page that absorbed the content. Prefer merging thin spaces over letting near-empty shells accrete.

## Demote (space back to page)

The inverse of promotion, for structure that never earned itself: a space holding one small page or none, no sub-spaces, whose content fits one file with room to spare. Merge any stray sibling page into the index first, then:

```sh
mv <path>/<name>/index.md <path>/<name>.md && rmdir <path>/<name>
```

(`rmdir` refusing means the folder wasn't empty — stop and look.) Drop the parent's `## Spaces` entry — the demoted page is a file now, reachable by traversal without registration. Rewrite incoming `[[<name>/index]]` and `(<name>/index.md)` links to the page form, and delete the `## Spaces` heading the file carried as an index. `check-size` the result — a demotion that lands over cap wasn't a demotion candidate.
