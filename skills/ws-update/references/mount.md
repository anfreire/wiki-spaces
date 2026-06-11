# Mount an external space

Someone shares a space — their whole wiki or a subtree; both are just folders with `index.md` + `## Spaces`. Mounting lands it inside the user's tree. Pick the mechanism by use case:

| Use case | Mechanism |
|---|---|
| Shared with teammates, changes flow both ways | git submodule |
| Read-only reference (someone else's wiki, a snapshot) | git clone |
| Local folder of your own, mounted for convenience | symlink |

Mount under `shared/<name>/` by convention — that path classifies as external, which is what gives the read-only-by-default semantics. A clone placed elsewhere (say `projects/<name>/`) classifies as **owned** and writable; only do that deliberately.

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
5. For a submodule, commit the pointer: `git -C <root> commit -am "mount shared/<name>"`.

## After mounting

The mounted space is autonomous: its own conventions, its own caps, its own log. Reads enter it only when the user asks (`--external` on the script); writes require explicit instruction — and for git mounts, push rights are the upstream's backstop, not the primary gate.

Pitfalls worth remembering: cloners of a wiki with submodules need `git clone --recursive`; GitHub release ZIPs ship empty submodule folders; after `git submodule update --remote`, the new SHA is local until the parent's pointer is committed and pushed.
