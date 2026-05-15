---
trigger: always_on
---

This project has access to wiki-spaces — a personal canonical wiki located at the path defined in `~/.config/wiki-spaces/config` (key: `wiki`). Wiki-spaces tooling, references, and ops scripts live at the path in the same config (key: `repo`).

When the user asks to save / capture / search knowledge:

- **Search** ("what do I know about X"): use the `wiki-search` skill if installed, or read `<repo>/skills/wiki-search/SKILL.md` and follow it.
- **Capture** ("save this", "update wiki"): use the `wiki-update` skill or its SKILL.md procedure.
- **Maintenance** ("audit wiki", "fix tags"): use the `wiki-tend` skill or its SKILL.md procedure.

Reference docs (read on demand from `<repo>/references/`):
- `SETUP.md` — full setup briefing if the config is missing
- `EXAMPLES.md` — canonical topology examples
- `MOUNT.md` — mount external wikis as spaces

Discovery order is **explicit path → `wiki` config key → nearest CWD ancestor with `index.md`**. Once the target is resolved, CWD is only a placement hint (project-scoped vs global) within that wiki.
