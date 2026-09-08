# HANDBOOK.md — The handbook for the whole repository

## Core

wiki-spaces exists to **erase the friction of adopting a wiki** — for any use case, not a fixed one. The user brings a need; the framework hands the LLM the tools and instructions to turn it into a living wiki and run the whole lifecycle — **create → populate → maintain → scale** — as the user works.

That "any use case" is only affordable through **abstraction and minimalism**. One model covers every shape the wiki takes — *gracefully*, never by branching on the case at hand — so a new shape adds no edge case. **Grace, not branches.**

Two ideas carry that weight. **Spaces** are the unit, born to solve ownership — personal, sharable, shared — and unbounded scale in one move. And **size discipline** keeps the wiki self-maintaining: hard per-file caps are a *curation forcing-function*, making the LLM weigh what is worth keeping, trimming, or promoting into a space the moment it warrants one — not a storage limit. Day-30 is not worse than day-0: more content invested means more payoff.

Every rule below is downstream of this:
- **Producer = consumer** protects the one rule — the tools can only traverse a contract they can trust.
- **Caps that refuse, never truncate** protect the self-maintenance loop — an overflow is a signal to reflect and trim, never a license to silently cut.
- **Reads-before-writes and scope-safety** protect sharing — other people's spaces are never touched without instruction.
- **Graceful over branchy** is how the tools stay abstract enough to fit any shape, and how the structure stays scalable as it grows.

Hold the drive in mind and the rules follow.

## Instructions

Every document we write for a reader or an agent — the spec, the skills and their references, this handbook — carries the Core into prose: clear, minimal, one job each.

- **One file, one job.** Define a thing once, where it belongs; everything else links to it. Two files telling the same story drift, and the reader can't tell which is true.
- **Minimal by default.** Write the rule, not the backstory — one abstract direction beats a list of cases. Cut whatever the reader can infer or reach by a link.
- **Clear and explicit.** Directions are rules, not hints: imperative, unambiguous, actable without guessing. Name the *what* and the *why*; point elsewhere for the *how*.
- **Lean entry, depth on reference.** An instruction is an entry point, not a dumping ground. The common path stays in the main file; specialized depth lives in focused files it references, read only when that path is taken — the LLM pays for what it needs, nothing more.

A file grown many jobs is a page that wants promoting: split it, and link. We keep our own instructions the way the framework keeps a wiki.

## Tools

Tools are the executable side, consisting of the single-file ws.py script. Where Instructions ask the LLM to hold the Core by judgment, Tools encode it so judgment cannot drift. Hygiene here is not taste. A sloppy tool corrupts the wiki it exists to keep.

### The line

What the tool may contain comes before how it is written: **the script parses the contract, never the content**. Structure — traversal, trust scope, caps, drift — has exactly one answer derivable from bytes on disk, and belongs in the tool. Meaning — what a page says, links, tags, what is worth keeping — is judgment, and belongs to the LLM, with `grep` as the sweep that feeds it. Every capability answers to this line before any rule below.

- No dialect semantics. How the dialect resolves a link or a YAML shape is content; re-implementing it buys false positives one patch at a time.
- No write path. Repair is judgment: a finding names its repair wherever one is safe to name, the caller applies it as an ordinary edit, and a re-run verifies. Read-only is pinned by test.
- No duplicating what existing tools do well. `grep` is in the tool for its file set — trust scope and contract reachability, which nothing else knows — never for its matching; anything richer than line hits belongs to the system's tools and the LLM's own reads.

### One source of truth

- One value, one definition. No second cap table, no parallel resolver, no quantity computed two ways.
- Two implementations that differ only a little are one implementation with a parameter. Unify instead of forking.
- Producer = consumer: the code that produces a contract line and the code that reads it travel the same path. Maintaining both sides by hand leads to drift, which makes the tool traverse a lie.
- Reuse before you add. Confirm a behavior doesn't already exist before writing it.
- The three copies of ws.py in the skill directories must remain byte-identical, and the shared blocks in the SKILL.md files — the core block, the safe-repairs set — verbatim. Tests pin these invariants and every prose restatement — cap defaults, interpreter floor, promised platforms, the log's roll destination, init.md's config block — each to the code or CI that anchors it, prose pinning prose where nothing does: CI tests exactly what the prose promises.

### Types and shapes

- Annotate every parameter and return. Precise types, never Any or type ignores to silence a real error. Fix the type.
- Dataclasses are frozen by default. Allow mutation only where the design needs it.
- Model status with precise types and orthogonal facts, not parsed strings and not branch thickets. An Optional return carries exactly one documented absence; anything richer gets its own typed channel — a second return, an enum — never an in-band sentinel. Grace, not branches, in code.
- Verdicts carry their provenance. Return why, not only what, so the edge explains itself without recomputing.
- Distrust boundary inputs like YAML, JSON, or argv. A parser can hand back any type. Assume it will, and harden against it.

### Abstraction

- Earn every layer. No speculative interface, no wrapper that only forwards, no abstraction without a second real caller.
- The core stays use-case agnostic. No domain vocabulary like project or recipe leaks into the layer built to serve every shape.

### Safety and failure

- There is no write path to make safe — that is The line. What replaces write-safety is convergence: every run re-derives from disk, a lost race between concurrent editors is just drift, and the next audit names the repair.
- Refuse, never truncate. A breached cap or invariant is an error with a clear cause, never a silent cut.
- Handle failures at boundaries like the filesystem or a parse. Never swallow an exception to limp onward.
- Errors go to stderr with a cause and a meaningful exit code. Stdout is pure data; what a walk skipped, the enclosing wiki when the root is nested, and the configured wiki when the root is another, ride stderr `note:` advisories — silence speaks for the walk's reach, never the whole disk.

### Change and refactor

- Extend with backward-compatible parameters before adding a function. Keep behavior unchanged unless the task is to change it.
- Delete superseded and dead code in the same change. Keep nothing for later. Remove orphaned imports, comments, and flags.
- Propagate a rename or signature change to every call site. Leave no half-migrated path.

### House style

- Helpers stay private (_-prefixed) to keep the public API minimal.

### Validation

- The test suite is the gate. Prove every behavior change with a targeted test before it lands. Run the unittest suite before committing.
- The invariants that matter, including producer = consumer, traversal, and caps, stay pinned by golden and end-to-end tests. Move them only on purpose.
- Skip validation only with a stated reason for exactly what you skipped.

### Anti-patterns

- A status or shape returned as a rendered string.
- The same value from two sources, or near-duplicate functions side by side.
- A single-use wrapper that only forwards.
- Dead code kept for later.
- Any or type ignores dodging a real type error.
- A broad except that buries the cause.
- print() in the data layer, or silent truncation of overflow.
- Domain vocabulary in the abstract core, or speculative abstraction with a single caller.