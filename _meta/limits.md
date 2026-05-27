# Size limits

The wiki-spaces source repo is itself a wiki — `index.md` at the root plus
`## Spaces` per the v1 spec. But its content is the project's own spec
documentation (CONVENTIONS, the three reference skills, references/),
which naturally exceeds the default user-content cap. Bump the caps for
those paths so `space audit` on this repo passes; user wikis inherit the
defaults from `_limits.DEFAULTS` unless they override.

The override format and matching semantics are documented in
[`CONVENTIONS.md` § `_meta/limits.md`](../CONVENTIONS.md#metalimitsmd).

| Pattern                                        | Cap (chars) |
|------------------------------------------------|-------------|
| CONVENTIONS.md                                 |       50000 |
| skills/ws-update/SKILL.md                      |       20000 |
| skills/ws-search/SKILL.md                      |       20000 |
| skills/ws-tend/SKILL.md                        |       20000 |
| vendor/**/*.md                                 |      200000 |
| references/**/*.md                             |       30000 |
