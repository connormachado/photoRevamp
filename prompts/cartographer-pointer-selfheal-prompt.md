# Cartographer pointer — self-healing rewrite + Step-0 map-first convention

Do NOT commit or push.

Goal: the map lives at `docs/CODEBASE_MAP.md` but `docs/` is gitignored (local-only,
intentional). So the 6-line pointer just added to `CLAUDE.md` references a path a fresh
clone won't have — a phantom reference. Fix it by making the pointer **self-healing**
(an instruction to generate the map, not an assumed path), and add a Step-0 convention so
every session reads the map before exploring.

## Step 0 — Inspect and report

- Read `CLAUDE.md`, especially its "Rules for editing this file" block (line ceiling) and
  the 6-line Cartographer pointer just added. Quote the current pointer text.
- Report where a one-line "Step 0 reads the map first" convention best belongs (near the
  top / conventions section), and whether adding it risks the line ceiling — if so, name a
  stale line to trim.
- Propose the exact reworded pointer + convention line, and **PAUSE**.

## Implementation (after wording is approved)

1. Replace the 6-line pointer with a self-healing version: state that
   `docs/CODEBASE_MAP.md` is a LOCAL, gitignored, regenerable navigation map produced by
   the Cartographer plugin's `/cartographer` command; a fresh clone won't have it, so run
   `/cartographer` to (re)build it. Do NOT assert the file exists as a committed artifact.
2. Add a short convention line: at the start of any task, consult `docs/CODEBASE_MAP.md`
   for where code lives (routes, modules, state owners) BEFORE dispatching explore agents;
   the map is navigation only — `CLAUDE.md` remains the authority on gotchas/contracts, and
   if they disagree, `CLAUDE.md` wins and the map is stale (regenerate).
3. Honor the line ceiling; if a line must go to stay under it, surface which and why.

## Pause points

- Before writing to `CLAUDE.md` — show the exact diff (eyes on every `CLAUDE.md` change).

## Verification

- `CLAUDE.md` stays under its ceiling.
- The pointer no longer implies a committed file exists.
- The map-first convention is present and readable on a fresh clone.

Save this prompt to `prompts/cartographer-pointer-selfheal-prompt.md`.
