# Mount an external space

Three mechanisms; pick by use case. The source can be any space — someone's whole wiki, a subtree they extracted, a reference snapshot. From your perspective it lands as a space inside your wiki.

## Decision

| Use case | Mechanism |
|---|---|
| Shared with teammates, you want to push changes | git submodule |
| Read-only reference (someone else's wiki, snapshot) | git clone (single fetch) |
| Local-only convenience mount (your own folder, mounted under canonical) | symlink |

## Before mounting (any branch)

- **Trust-scope classification depends on placement.** The heuristic in `CONVENTIONS.md / Trust Scope` marks a space as external only if it's under `<wiki>/shared/`, is a git submodule with a foreign origin, or is a symlink resolving outside the wiki tree. **A plain clone under `<wiki>/projects/<name>/` is classified as *owned* by the heuristic — writes are allowed by default.** If you want the read-only / external semantics, mount under `<wiki>/shared/`. wiki-spaces stores no per-space ownership metadata; the path-based heuristic is the entire signal, and `git push` permissions are the de facto upstream gate.
- **Parent's tier matters for the `## Spaces` update step.** If the parent `index.md` has a `## Spaces` section, the mount must be listed there (the navigability contract). If the parent has no `## Spaces`, you have two choices: (a) add `## Spaces` now, upgrading the parent to Tier 2 and listing the mount; or (b) leave the parent at Tier 1 and skip the listing — the mount exists on disk but isn't navigable from the parent's index. Ask the user which they want.
- **Shortcut for the `## Spaces` update.** After the filesystem step in any branch below, run `wiki-spaces space add <relative-path>` to register the mount with the nearest ancestor. It's idempotent on an existing `index.md` (won't overwrite the mounted content) and walks up to the right ancestor space automatically. When the nearest ancestor is Tier 1 (no `## Spaces` section), the command prints a notice and leaves that ancestor unchanged — pass `--upgrade-parent` to add `## Spaces` there and list the mount in one go.

## Branch A: Git submodule (collaborative shared space)

1. Confirm the canonical wiki is itself a git repo. If not: `cd <wiki>; git init -b main; git add -A; git commit -m "initial"`.
2. Decide the mount path (typically `<wiki>/shared/<name>/`).
3. Add the submodule: `cd <wiki>; git submodule add <repo-url> shared/<name>`.
4. Verify the submodule has `index.md`. If it does NOT, the mounted repo isn't a wiki-spaces wiki — abort, or coordinate with its owner to add `index.md`.
5. Update the parent's `index.md` `## Spaces` section: add `- [<name>](shared/<name>/index.md) — short description`. (If the parent is Tier 1, see "Before mounting" above.)
6. Commit the submodule pointer in the parent: `cd <wiki>; git commit -am "add submodule shared/<name>"`.
7. Push the parent if it has a remote.

Note for cloners of your wiki: they need `git clone --recursive` (or `git submodule update --init` after a plain clone) to populate the sub-modules. If your wiki uses submodules, mention it in the wiki's `index.md`.

## Branch B: Git clone (read-only reference)

1. `git clone <repo-url> <wiki>/shared/<name>` — **place under `shared/`** to get the read-only / external trust-scope semantics. Placing a clone elsewhere (e.g., `<wiki>/projects/<name>/`) makes it *owned* by the heuristic — writes are allowed by default, which may not be what you want for a third-party reference.
2. Verify `index.md` exists in the clone.
3. Update the parent's `index.md` `## Spaces` section. (If the parent is Tier 1, see "Before mounting" above.)
4. The clone is now a space inside the wiki. Tools default to NOT writing to it (assuming `shared/` placement).
5. To pull updates later: `cd <wiki>/shared/<name>; git pull`.

## Branch C: Symlink (local mount)

1. `ln -s /absolute/path/to/source <wiki>/shared/<name>` (or wherever).
2. Verify the symlink target has `index.md`.
3. Update the parent's `index.md` `## Spaces` section. (If the parent is Tier 1, see "Before mounting" above.)
4. The symlinked folder is autonomous — operations within it stay local to the symlink target. The `realpath` resolves outside the canonical wiki tree, so the heuristic classifies the symlinked space as external regardless of where you mount it. The space IS in scope when the user explicitly targets it.

## Trust scope reminder

After mounting, the new space is autonomous: own conventions, own log (if any), own taxonomy. Tools default to writing only inside the targeted space; *external* spaces (per CONVENTIONS / Owned vs external) aren't modified unless the user explicitly opts in.

For git-backed mounts, push permissions on the upstream provide a publication backstop: local commits succeed, but push fails if you don't have access. Surfaces protection late; treat trust scope as the primary gate.

## Common pitfalls

- **Forgot to update parent's `## Spaces`.** By convention (see CONVENTIONS.md `## index.md`), the parent owns the link entry. Without the entry, the space exists on disk but isn't navigable from the parent's index. (Skip this step intentionally if the parent is Tier 1 and you don't want to upgrade it.)
- **Clone placed outside `shared/`.** Classified as owned; writes are allowed by default. Either move it under `shared/`, or accept that the read-only semantics aren't enforced.
- **Submodule cloned without `--recursive`.** Cloners need `git clone --recursive` (or `git submodule update --init`). Document this in your wiki's `index.md` if you use submodules.
- **Mount has no `index.md`.** It's not a wiki-spaces wiki — either ask the upstream owner to add one, or treat the mount as a plain folder (no space, no operations).
- **GitHub release ZIPs don't include submodule contents.** Cloners using a release ZIP get empty submodule folders.
- **After `git submodule update --remote`, the new SHA is only local until you commit the parent's updated submodule pointer (gitlink) and push.** A common gotcha when sharing.
