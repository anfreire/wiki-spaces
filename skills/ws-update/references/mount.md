# Mount an external space

Someone shares a space — their whole wiki or a subtree; both are just folders with `index.md` + `## Spaces`. Mounting lands it inside the user's tree. Pick the mechanism by use case:

| Use case | Mechanism |
|---|---|
| Shared with teammates, changes flow both ways | git submodule |
| Read-only reference (someone else's wiki, a snapshot) | git clone |
| Local folder of your own, mounted for convenience | symlink |

Mount under `shared/<name>/` by convention — a `shared/` segment classifies as external at any depth, which is what gives the read-only-by-default semantics, and it works the same inside a nested space (`projects/x/shared/<name>/`). Per mechanism:

- A **clone** placed outside any `shared/` (say `projects/<name>/`) classifies as owned and writable — only do that deliberately.
- A **submodule** classifies external wherever it sits: it names another repository by definition, so its content answers to that repository, not to this wiki. An owned mount of your own second repo is a clone, not a submodule.
- A **symlink** whose target lives outside the tree classifies external wherever it sits — there is no owned symlink mount; to own the content, move it into the tree or clone it.

## Before mounting

Verify the source is a wiki: `index.md` exists **and** carries `## Spaces`. If not, it isn't mountable as a space — coordinate with its owner. Never write into an external mount to repair its contract; that mutates someone else's repo.

## Procedure

1. Run the mechanism:
   ```sh
   cd <root> && git submodule add <repo-url> shared/<name>     # submodule (root must be a git repo)
   git clone <repo-url> <root>/shared/<name>                   # clone
   ln -s /abs/path/to/source <root>/shared/<name>              # symlink
   ```
2. Verify the mounted result has `index.md` with `## Spaces`. If it doesn't, undo the mount (`git submodule deinit` + `git rm` / `rm -rf` the clone / `unlink` the symlink) and tell the user why.
3. Register it in the parent's `## Spaces` by hand — external spaces are not auto-registered:
   ```
   - [shared/<name>/](shared/<name>/index.md) — <one-line description>
   ```
4. `python3 <skill-dir>/scripts/ws.py audit --wiki <root>` to confirm the contract is clean, then `list --external` to see the mount in the traversal.
5. For a submodule, commit the pointer — pathspec'd, so a dirty tree's unrelated changes stay out: `git -C <root> commit -m "mount shared/<name>" -- .gitmodules shared/<name>`.

## After mounting

The mounted space is autonomous: its own conventions, its own caps, its own log. Reads enter it only when the user asks (`--external` on the script); writes require explicit instruction — and for git mounts, push rights are the upstream's backstop, not the primary gate.

The default `audit` keeps watching the entry itself: a mount that stops looking like a wiki (say, upstream loses its `## Spaces` heading) is reported as a `mount` finding. `audit --external` also flags a mounted space you forgot to register (`missing entry … register mounts by hand`). Findings *inside* the mount still need `--external`, and repairs there belong to its owner.

Pitfalls worth remembering: cloners of a wiki with submodules need `git clone --recursive`; GitHub release ZIPs ship empty submodule folders; after `git submodule update --remote`, the new SHA is local until the parent's pointer is committed and pushed.
