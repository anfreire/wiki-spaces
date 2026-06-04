# Supported harnesses

wiki-spaces supports 7 verified AI coding harnesses. The installation process is handled automatically by the `wiki-spaces install` command.

## The hub model

The installer writes every skill once into the hub directory at `~/.agents/skills/`.

The following harnesses read skills directly from this hub:
- Codex
- Gemini CLI
- OpenCode
- Copilot
- Cursor

## Alias harnesses

The following harnesses do not support the hub directory. For these, the installer creates per-skill aliases in their native directories:
- Claude Code (`~/.claude/skills/`)
- Kiro (`~/.kiro/skills/`)

Run `wiki-spaces install` to set up all detected harnesses automatically.
