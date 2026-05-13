# wiki-spaces

## What this space is

The wiki-spaces project: a minimal nestable wiki spec, an opt-in conventions catalog, three reference skills, and a per-harness installer. This repo is itself a wiki-spaces wiki — its own canonical example.

A wiki is any folder containing `index.md`. Spaces inside it recurse. Sharing a wiki means sharing a folder; the receiver mounts it as a subdir, symlink, or submodule — and from their perspective, it becomes a space inside theirs. See [`AGENTS.md`](AGENTS.md) for the spec.

## Items

- [AGENTS.md](AGENTS.md) — the spec (LLM contract)
- [CONVENTIONS.md](CONVENTIONS.md) — opt-in conventions catalog
- [README.md](README.md) — install and orientation
- [LICENSE](LICENSE) — MIT

## Spaces

- [skills/](skills/index.md) — the three reference skills
- [vendor/](vendor/index.md) — pinned upstream (kepano/obsidian-skills)
- [bridges/](bridges/index.md) — manual integration templates for harnesses without skills
- [references/](references/index.md) — agent-facing setup, examples, mounting playbooks
- [scripts/](scripts/index.md) — Python+uv installer and sync tooling
