# Mount an external space

Three mechanisms; pick by use case. The source can be any space — someone's whole wiki, a subtree they extracted, a reference snapshot. From your perspective it lands as a space inside your wiki.

**Shortcut.** `wiki-spaces space mount <source> [path] --mode {submodule|clone|symlink}` runs the branch below for you — it executes the chosen mechanism, verifies the mount has `index.md`, registers it in the nearest ancestor's `## Spaces` atomically (advisory `fcntl.flock` on the ancestor directory + tempfile + `os.replace`), and rolls back the mount if registration fails. It refuses on a Tier 1 parent (no `## Spaces`), exactly like `space add`. `path` is optional — default is `shared/<basename-of-source>/`, which gives the read-only / external trust-scope semantics by convention. If the mount turns out not to be a wiki (no `index.md`) it is not registered: a failed symlink or clone is removed, but a failed submodule must be undone by hand — the command prints the exact commands. The branch-by-branch steps below are the manual equivalent and the reference for what the command does and why.

## Decision

| Use case | Mechanism |
|---|---|
| Shared with teammates, you want to push changes | git submodule |
| Read-only reference (someone else's wiki, snapshot) | git clone (single fetch) |
| Local-only convenience mount (your own folder, mounted under canonical) | symlink |

## The fast path: `wiki-spaces space mount`

One command covers all three mechanisms. It validates the parent's tier contract, executes the mount, verifies the result has `index.md`, rolls back on failure, and registers the `## Spaces` entry:

```sh
wiki-spaces space mount <source> [path] --mode submodule|clone|symlink \
    [--name NAME] [--description DESC] [--dry-run]
```

- `<source>` — git URL (for `submodule` / `clone`) or local path (for `symlink` / `clone`).
- `[path]` — optional. Defaults to `shared/<basename-of-source-without-.git>/`, which gives the read-only / external trust-scope semantics by convention.
- `--mode` — required. Mount mechanism changes trust semantics, so the choice is explicit.
- `--dry-run` — print the plan; touch nothing.

**The CLI atomically refuses** when the nearest ancestor is Tier 1 (no `## Spaces`): exits non-zero, touches nothing. Resolution: add `## Spaces` to the ancestor's `index.md` first (a single heading line), then rerun. The mount step itself also refuses when the mounted source has no `index.md` — it rolls back per-mode and exits non-zero. If rollback fails, you get a clear "manual cleanup required" message.

Examples:

```sh
# Shared team wiki via submodule (requires the parent wiki to be a git repo).
wiki-spaces space mount https://github.com/team/wiki.git --mode submodule

# Read-only reference clone.
wiki-spaces space mount https://github.com/someone/notes.git --mode clone

# Local symlink to your own folder, mounted under shared/.
wiki-spaces space mount /home/me/personal-notes --mode symlink
```

## Before mounting (any branch)

- **Trust-scope classification depends on placement.** The heuristic in `CONVENTIONS.md / Owned vs external` marks a space as external only if it's under `<wiki>/shared/`, is a git submodule with a foreign origin, or is a symlink resolving outside the wiki tree. **A plain clone under `<wiki>/projects/<name>/` is classified as *owned* by the heuristic — writes are allowed by default.** The `space mount` default (`shared/<basename>/`) opts you into the external semantics; pass an explicit `[path]` to override.
- **Parent's tier matters.** `space mount` refuses on Tier 1 parents (no `## Spaces`). Either upgrade the parent first (add a `## Spaces` heading) or use the manual mechanism below and accept that the mount isn't navigable from the parent's index.

## Underlying mechanism (if you need to do it manually)

The `space mount` command wraps these. Drop to the raw form when you need finer control (e.g., a `git clone --depth 1` shallow clone, or pre-existing submodule config) — and call `wiki-spaces space add <relative-path>` after the filesystem step to register the `## Spaces` entry.

### Branch A — Git submodule (collaborative shared space)

1. Confirm the canonical wiki is itself a git repo. If not: `cd <wiki>; git init -b main; git add -A; git commit -m "initial"`.
2. Decide the mount path (typically `<wiki>/shared/<name>/`).
3. Add the submodule: `cd <wiki>; git submodule add <repo-url> shared/<name>`.
4. Verify the submodule has `index.md`. If it does NOT, the mounted repo isn't a wiki-spaces wiki — abort, or coordinate with its owner to add `index.md`.
5. Register the entry: `wiki-spaces space add shared/<name> --force-external` (or update the parent's `## Spaces` by hand). `--force-external` is required because `shared/` paths are classified external by the trust-scope heuristic; `space add` refuses external scopes by default.
6. Commit the submodule pointer in the parent: `cd <wiki>; git commit -am "add submodule shared/<name>"`.
7. Push the parent if it has a remote.

Note for cloners of your wiki: they need `git clone --recursive` (or `git submodule update --init` after a plain clone) to populate the submodules. If your wiki uses submodules, mention it in the wiki's `index.md`.

### Branch B — Git clone (read-only reference)

1. `git clone <repo-url> <wiki>/shared/<name>` — **place under `shared/`** to get the read-only / external trust-scope semantics. Placing a clone elsewhere (e.g., `<wiki>/projects/<name>/`) makes it *owned* by the heuristic — writes are allowed by default.
2. Verify `index.md` exists in the clone.
3. Register the entry: `wiki-spaces space add shared/<name> --force-external`. `--force-external` is required because `shared/` paths are classified external by the trust-scope heuristic.
4. To pull updates later: `cd <wiki>/shared/<name>; git pull`.

### Branch C — Symlink (local mount)

1. `ln -s /absolute/path/to/source <wiki>/shared/<name>` (or wherever).
2. Verify the symlink target has `index.md`.
3. Register the entry: `wiki-spaces space add shared/<name> --force-external`. `--force-external` is required because `shared/` paths are classified external by the trust-scope heuristic.
4. The symlinked folder is autonomous — operations within it stay local to the symlink target. The `realpath` resolves outside the canonical wiki tree, so the heuristic classifies the symlinked space as external regardless of where you mount it. The space IS in scope when the user explicitly targets it.

## Trust scope reminder

After mounting, the new space is autonomous: own conventions, own log (if any), own taxonomy. Tools default to writing only inside the targeted space; *external* spaces (per CONVENTIONS / Owned vs external) aren't modified unless the user explicitly opts in.

For git-backed mounts, push permissions on the upstream provide a publication backstop: local commits succeed, but push fails if you don't have access. Surfaces protection late; treat trust scope as the primary gate.

## Common pitfalls

- **Forgot to update parent's `## Spaces`.** `space mount` does this for you. Manual branches need `wiki-spaces space add <path>` (or hand-edit the parent's `## Spaces`) — otherwise the space exists on disk but isn't navigable from the parent's index.
- **Clone placed outside `shared/`.** Classified as owned; writes are allowed by default. Either move it under `shared/`, or accept that the read-only semantics aren't enforced.
- **Submodule cloned without `--recursive`.** Cloners need `git clone --recursive` (or `git submodule update --init`). Document this in your wiki's `index.md` if you use submodules.
- **Mount has no `index.md`.** It's not a wiki-spaces wiki — either ask the upstream owner to add one, or treat the mount as a plain folder (no space, no operations). `space mount` refuses and rolls back in this case.
- **GitHub release ZIPs don't include submodule contents.** Cloners using a release ZIP get empty submodule folders.
- **After `git submodule update --remote`, the new SHA is only local until you commit the parent's updated submodule pointer (gitlink) and push.** A common gotcha when sharing.
