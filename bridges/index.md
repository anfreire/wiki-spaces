# bridges

## What this space is

Manual integration snippets for AI coding harnesses that don't have a "skills" concept (Cursor, Windsurf). These files are NOT auto-installed by `install.py` — copy them into your project's rules directory when you want that harness to be wiki-spaces aware.

Kiro is NOT here because it has a skills concept and is handled by `install.py`. GitHub Copilot and Aider use single-file instruction targets rather than rule files; they have no bridge file — see [`references/HARNESS_INTEGRATION.md`](../references/HARNESS_INTEGRATION.md) for the paragraph to paste into their instruction files.

## Items

- [cursor/wiki-spaces.mdc](cursor/wiki-spaces.mdc) — Cursor rule (copy to `.cursor/rules/wiki-spaces.mdc` in your project)
- [windsurf/wiki-spaces.md](windsurf/wiki-spaces.md) — Windsurf rule (copy to `.windsurf/rules/wiki-spaces.md`)

Why manual instead of auto-installed? wiki-spaces deliberately doesn't modify per-project state. The canonical wiki is the only state we care about. Copying a one-line rule into your project is a 30-second task; auto-installation would create per-project files that drift from the wiki-spaces source over time.
