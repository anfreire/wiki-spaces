# Colorize the Obsidian graph

Write color groups into `.obsidian/graph.json` so the graph view clusters visibly. Only when `.obsidian/` exists — it's the marker that the user actually opens this wiki in Obsidian.

## Hazard

Obsidian rewrites `graph.json` on exit. Ask the user to **close Obsidian before this step**, and to reload it after. Back up first; the backup protects against this skill's own writes, not Obsidian's.

## Procedure

1. Read `.obsidian/graph.json`. Missing → tell the user to open the vault in Obsidian once (which creates it), then re-run.
2. Back it up: `cp .obsidian/graph.json .obsidian/graph.json.backup-$(date -u +%Y%m%d-%H%M)`.
3. Build the groups — default mode is by tag (top 10 tags by usage when frontmatter is in use); fall back to top-level folders when there's no frontmatter. One group per tag/folder:
   ```json
   {"query": "tag:#python", "color": {"a": 1, "rgb": 5142951}}
   {"query": "path:concepts", "color": {"a": 1, "rgb": 15896107}}
   ```
4. Replace **only** the `colorGroups` key in the JSON; preserve every other key byte-for-byte.
5. Write the file, remind the user to reopen Obsidian, and report the groups in the summary.

## Palette

Ten distinguishable RGB integers, assigned in order:

```
5142951, 15896107, 14767961, 7780786, 5873999,
15583048, 11565217, 16751527, 10253663, 12234924
```
