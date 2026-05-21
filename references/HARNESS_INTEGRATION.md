# Manual integration for harnesses without skills

`install.py` handles harnesses with a "skills" concept (Claude Code, Codex, Gemini CLI, Antigravity, Hermes, Kiro). For other harnesses, you supply integration manually — either pipe the bundled snippet into a project rule file, or paste the snippet body into the harness's instruction file.

The fastest path is `wiki-spaces install --bridge <key>`, which emits the snippet body to stdout. Pipe it wherever your harness expects rules — no per-project state managed by wiki-spaces, you control placement entirely.

Ready-to-copy snippets at the wiki-spaces repo:
- [`bridges/cursor/wiki-spaces.mdc`](../bridges/cursor/wiki-spaces.mdc)
- [`bridges/windsurf/wiki-spaces.md`](../bridges/windsurf/wiki-spaces.md)

The snippets all say the same thing: wiki-spaces is available; read `~/.config/wiki-spaces/config` to find the wiki and the wiki-spaces repo; use the wiki-search / wiki-update / wiki-tend skills (or follow their SKILL.md procedures if not installed).

## Cursor

```sh
mkdir -p <your-project>/.cursor/rules
wiki-spaces install --bridge cursor > <your-project>/.cursor/rules/wiki-spaces.mdc
```

Cursor's modern rule format is `.mdc` files under `.cursor/rules/`. The snippet uses `alwaysApply: true` so it loads every session.

## Windsurf

```sh
mkdir -p <your-project>/.windsurf/rules
wiki-spaces install --bridge windsurf > <your-project>/.windsurf/rules/wiki-spaces.md
```

Windsurf's rule frontmatter uses `trigger: always_on`.

## GitHub Copilot

GitHub Copilot reads `.github/copilot-instructions.md` (and as of Aug 2025, also `AGENTS.md` natively). No bridge file ships for Copilot — instead, append a short paragraph to one of those files in your project: "wiki-spaces is available — read `~/.config/wiki-spaces/config` for the canonical wiki path; use the `wiki-search` / `wiki-update` / `wiki-tend` skills, or read their SKILL.md procedures from the `repo` path in the config."

## Aider

Aider reads `AGENTS.md` (or whatever you set via `read:` in `.aider.conf.yml`). No bridge file ships for Aider — either:
- Add a paragraph (same content as the Copilot section above) to your project's `AGENTS.md`, OR
- Add `read: <path-to-snippet>.md` to your `.aider.conf.yml` pointing at any file containing that paragraph.

## Why no auto-installation

These harnesses use project-scoped rule files. wiki-spaces doesn't write per-project files — the canonical wiki is the only state it manages — so the rule snippet stays a manual copy.

If you want this automated across many projects, a shell loop does it (creates the `rules/` directory if missing):

```sh
# example: copy the cursor snippet into every project under ~/Projects/ that has .cursor/
for dir in ~/Projects/*/.cursor; do
  mkdir -p "$dir/rules"
  cp <repo>/bridges/cursor/wiki-spaces.mdc "$dir/rules/wiki-spaces.mdc"
done
```
