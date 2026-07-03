# Share a space of yours

The producer side of [mounting](mount.md): a space is a folder, so sharing one means handing over that folder with its contract intact. The receiver mounts it into their tree, where it lands as an external space of theirs — exactly as their spaces would land in yours.

## Before sharing

1. **Verify the space stands alone.** Resolve it as its own wiki and audit it:
   ```sh
   python3 <skill-dir>/scripts/ws.py audit --wiki <root>/<space>
   ```
   Broken links here are references to pages that live outside the space — they will dangle for the receiver. Fix each (move the target in, drop the link, or inline the content) or name it to the user and let them accept it.
2. **Check what rides along.** Everything in the folder ships: drafts, `log.md`, `_meta/`, `_archives/`, and full git history when you share a repo. Pruning is the user's call — ask when anything looks private.

## Pick the mechanism

| Situation | Mechanism |
|---|---|
| One-off snapshot, no sync | archive and send: `tar -C <root> -czf <space>.tgz <space>` (or a plain copy) |
| Receiver follows your updates | the space becomes its own repo; they clone or submodule it |
| Both sides edit | its own repo; both sides mount it as a submodule |

## Carve a space into its own repo

Non-git wiki, or history that shouldn't travel: copy the folder, `git init` inside the copy, commit, push to a new remote. Git wiki whose space history should travel:

```sh
git -C <root> subtree split --prefix=<space-path> -b share-<name>
git -C <root> push <new-remote-url> share-<name>:main
git -C <root> branch -D share-<name>              # the split branch was scaffolding
```

If you keep editing the shared space, replace your in-tree copy with a mount of the new repo (per [mount.md](mount.md)) so one source of truth exists. It lands under `shared/` and classifies external — that is correct, not a loss: the space is shared now, and your writes to it happen the way all external writes do, on explicit targeting.

## Hand over and close out

Update your `## Spaces` for whatever moved (drop the old entry, register the mount), run `audit` to confirm the contract is clean, then hand the receiver the URL or path with one line: mount it and register it in your `## Spaces` — their `mount.md` covers the rest.
