# Topology examples

Concrete shapes the canonical wiki can take. None are special — the spec doesn't care, and the conventions catalog (`CONVENTIONS.md / Categorical layout`) lists folder names as suggestions, not requirements. Pick the one closest to your use case, mix shapes, or invent your own.

## Minimum viable (any use case)

```
~/Wiki/
└── index.md
```

A folder + `index.md`. That's a complete Tier 1 wiki. Add files at the root or under any subfolder as you go. Works for any use case where you don't yet know the shape — start here, let folders emerge from what you actually write.

The `wiki = /home/<user>/Wiki` entry in `~/.config/wiki-spaces/config` makes this discoverable from anywhere. Paths in the config are absolute (no `~/` shell expansion).

## Developer notebook

```
~/Wiki/
├── index.md
├── log.md
├── _meta/
│   └── taxonomy.md
├── .manifest.json
├── concepts/             ← ideas, patterns, decisions (python typing, postgres locks, ...)
├── entities/             ← tools, libraries, services
├── skills/               ← how-to procedures
└── projects/
    ├── proj-foo/
    │   ├── index.md      ← own log, own taxonomy possible (per-space autonomy)
    │   ├── architecture/
    │   └── experiments/
    └── proj-bar/
        └── index.md
```

Cross-project knowledge at the root; project-specific knowledge under `projects/<name>/`. `wiki-update` recognizes this shape and routes content accordingly. The opt-in bundle (`log.md`, `_meta/taxonomy.md`, `.manifest.json`) is the recommended add-on for this use case because most of it (project sync state, taxonomy-managed tags, audit log) is dev-leaning.

## Research wiki

```
~/Wiki/
├── index.md
├── log.md
├── _meta/
│   └── taxonomy.md
├── papers/               ← summarized literature
├── topics/               ← cross-paper concepts and threads
├── methods/              ← techniques, statistical patterns, experimental designs
├── datasets/             ← dataset references and notes
└── projects/
    └── thesis-2026/
        ├── index.md
        ├── experiments/
        └── lit-review/
```

Research-flavored layout. `papers/` for paper-by-paper notes; `topics/` for cross-paper synthesis; `projects/` if you have specific research projects (a thesis, a paper, a grant). Taxonomy is useful here (subject tags, method tags). `.manifest.json` typically not needed unless you're syncing notes from external project repos.

## Writing project

```
~/Wiki/
├── index.md
├── drafts/               ← in-progress chapters / posts / pieces
├── characters/           ← character bios (fiction)
├── worldbuilding/        ← setting, lore, history
├── research/             ← background notes feeding the writing
├── notes/                ← misc craft notes, feedback received
└── archive/              ← finished or shelved drafts
```

For fiction, journalism, or any structured writing project. Tags are usually free-form (no taxonomy needed). `hot.md` at the root is handy for "what I'm working on right now." Git-back this once you start to care about version history.

## Recipe collection

```
~/Wiki/
├── index.md
├── recipes/              ← one file per recipe
├── ingredients/          ← notes on ingredients, sources, substitutions
├── techniques/           ← knife skills, sourdough, fermentation, ...
└── meal-plans/           ← weekly plans, shopping lists
```

Domain-specific layout. Skills work fine — search for "what do I know about pickling," capture a new technique from a conversation, audit for orphaned recipes. No opt-in bundle needed; pure content.

## Personal knowledge

```
~/Wiki/
├── index.md
├── journal/              ← dated entries
├── learning/             ← topics I'm studying
├── contacts/             ← people, relationships, notes from conversations
├── places/               ← travel notes, restaurants, neighborhoods
├── interests/            ← hobbies, side topics
└── archive/              ← retired entries
```

A personal life wiki. Privacy matters more here — typically not git-shared, possibly encrypted at the filesystem layer. The reference skills work without any opt-in bundle.

## Team reference

```
~/Wiki/
├── index.md
├── runbooks/             ← incident response, ops procedures
├── decisions/            ← architecture decisions (ADRs)
├── services/             ← service catalog, ownership, dependencies
├── people/               ← team members, roles, on-call
└── clients/              ← per-client context (for agencies / consultancies)
```

A shared team reference. Usually git-backed and shared (see the "Adding shared content" section below). `_meta/taxonomy.md` is useful for keeping tagging consistent across contributors.

## Adding shared content (any of the above)

```
~/Wiki/
├── index.md
├── <your shape from above>
└── shared/
    ├── team-design/      ← git submodule of teammate's wiki
    │   └── index.md
    └── client-acme/      ← git clone, read-only locally
        └── index.md
```

Shared spaces under `shared/` mount as git submodules (collaborative — push your changes back) or clones (read-only — pull updates manually). The trust scope clause in `AGENTS.md` keeps tools out of `shared/` spaces by default; they're touched only when the user explicitly opts in.

See [`MOUNT.md`](MOUNT.md) for the three mount mechanisms (submodule, clone, symlink) and decision criteria.

## What doesn't change between any shape

- The two-key config (`wiki` + `repo`) at `~/.config/wiki-spaces/config` always has exactly one canonical `wiki` entry.
- `wiki-search`, `wiki-update`, `wiki-tend` operate on that canonical wiki regardless of its shape.
- CWD is a placement hint; the wiki itself is always the configured one.
- Spaces are autonomous; conventions don't propagate from parents to children.
- Opt-in conventions (`log.md`, `_meta/taxonomy.md`, `.manifest.json`, frontmatter, etc.) work the same regardless of folder layout.
