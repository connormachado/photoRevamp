# Context de-bloat — trim the auto-loaded CLAUDE.md files, tighten /sm

Do NOT commit or push.
Goal: shrink the context that auto-loads on every session. The root and nested CLAUDE.md files are read at the START of every prompt, so every stale/duplicated/derivable line is a tax paid up front, every time — and a bloated file gets skimmed, hiding the load-bearing rule. Audit what actually loads, propose trims of true bloat (NEVER load-bearing contracts), and tighten /sm so the bloat doesn't come back.

Step 0 — Inspect and report (MEASURE before cutting; do not edit anything yet):
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Enumerate every file that AUTO-loads into context and when: the root CLAUDE.md (always) and each nested CLAUDE.md (only in its folder — backend/, photo-search/src/components/motion-review/). For each, report line count AND an approximate token count (use the installed tiktoken, or wc as a proxy). Give me the total "always-loaded" budget (root) and each per-folder budget — this is the number we're trying to lower.
- For files /sm maintains that may NOT auto-load (RODEMAP.md, docs/session-log.md): confirm whether anything actually pulls them into context or whether they only sit on disk. If they don't auto-load, they are NOT a context cost — say so plainly so we don't waste effort trimming them.
- Report the per-prompt Step-0 map-consult STAMP's token cost (it's pasted into every prompt). Note whether it's still worth it now that the CLAUDE.md convention + the map both exist, OR whether the convention alone can carry it. This was added deliberately after the map got ignored once (Prompt 32), so FLAG it as a judgment call — do NOT unilaterally remove it.
- Categorize bloat candidates WITHOUT deleting: (a) content duplicated across the root and a nested CLAUDE.md; (b) pure navigation/structure now covered by docs/CODEBASE_MAP.md (route tables, file lists, dir layouts) that could become a one-line pointer; (c) stale lines (paths/features that no longer exist); (d) restated invariants that already live elsewhere; (e) anything derivable from source.
- Read the root CLAUDE.md's own "Rules for editing this file" block + its line ceiling; report current lines vs the ceiling.
- Read the /sm command (~/.claude/commands/sm.md): report its routing rules and whether they're permissive enough to have caused the creep (routing derivable facts or narrative INTO CLAUDE.md instead of nowhere / session-log).
Report the budgets, the categorized candidates (each with a one-line reason + keep/trim rec), and a plan. PAUSE.

Implementation (ONLY after I approve the specific cuts):
1. Trim ONLY what I approved. Preserve every load-bearing item: safety-critical prohibitions (never touch/delete a real original), failure contracts, ffmpeg invariants (autorotate, .MOV stream-map), design rationale that prevents a real bug. When in doubt, KEEP and flag — never silently drop a rule.
2. For navigation/structure that merely duplicates the map: replace the verbose copy with a one-line pointer to docs/CODEBASE_MAP.md. The map is navigation; CLAUDE.md stays the authority on gotchas/contracts — do NOT move a gotcha into the (gitignored, disposable) map.
3. Collapse cross-file duplication: keep a shared line in the most specific file that still auto-loads for the relevant work, drop the copy.
4. Keep the root CLAUDE.md under its ceiling with margin; keep each nested file lean.
5. Tighten /sm's routing so the bloat can't return: derivable facts / route tables / dir layouts → nowhere (derive from source or the map); narrative → session-log; only true gotchas/contracts → CLAUDE.md, sparingly. Echo the /sm edit for my review.

Pause points: before writing to ANY CLAUDE.md or to the /sm command — show me the exact per-file diff (eyes on every CLAUDE.md change). Never drop a safety-critical line without explicit sign-off.

Verification:
- Report before/after line + token counts per file and the new always-loaded total — the win, quantified.
- Confirm no load-bearing prohibition/contract/invariant was removed; for anything borderline, list what was kept and why.
- Docs-only change: build + lint unaffected; sessions still auto-load the invariants they rely on. (No /write-tests — no logic touched.)

Save this prompt to prompts/context-debloat-prompt.md.

---

## Framing note (read before Step 0 — this changes what "bloat" means here)

I originally wrote this prompt to cut the token tax. That's still true, but a real incident
last week showed the sharper reason, and it should drive your judgment calls:

CLAUDE.md contained the line "Four tests are xfail(strict) on purpose; they mark open
decisions, so don't 'fix' them." That was true of exactly ONE of the four. The other three
were known DEFECTS marked rather than fixed — including one in read_all whose failure mode
was a PARTIALLY REWRITTEN library. Because that line auto-loads into every session, it
instructed every future Claude to leave a data-integrity bug alone. It is plausibly why the
bug survived as long as it did. It has since been corrected.

So the real failure mode isn't length — it's a CONFIDENTLY WRONG line being read every
session. A line that is stale, over-generalised, or asserts an exact number that has since
moved is worse than no line at all, because it earns trust it doesn't deserve and can
actively immunise defects against being fixed.

Add these to your Step 0 audit categories, ranked ABOVE pure length:
- (f) CLAIMS THAT ARE NOW FALSE OR OVER-GENERALISED. Check every factual assertion against
  the code. Specifically verify: any remaining exact-count assertions (the lint count was
  just changed from "11 errors" to a category list for exactly this reason — a tripwire that
  cries wolf gets ignored); the photo/vector count (it is 56,773 as of today and has been
  wrong twice); any claim about what tests or xfails mean; and any "don't do X" instruction —
  those are the highest-consequence lines in the file, so each one must still be true AND
  still be worth its cost.
- (g) MANUAL STEPS PRESENTED AS INVARIANTS. A line telling a human to remember to run
  something is a defect waiting to happen, not documentation. Flag any you find; the fix is
  usually to make the code enforce it, not to word the reminder better.

For every line you propose keeping, I want a one-line reason it earns being read at the start
of EVERY session. "It's true" is not sufficient — derivable facts belong in the map, narrative
belongs in the session log, and only gotchas, failure contracts, and safety-critical
prohibitions belong in an auto-loaded file.

Everything else in the prompt above stands, including the hard rule that I see every diff.

---

## Outcome (2026-08-16)

Audit findings, the approved cuts, and the resulting before/after budgets are recorded in
`docs/session-log.md` under this date. Two decisions worth carrying forward:

- The **line ceiling is a broken metric** — root was 181/250 lines yet cost 4,871 tokens,
  while `backend/CLAUDE.md` cost 4,587 tokens in 43 lines. The ceiling is now stated in
  tokens.
- The **Step-0 stamp shrank to its unique clause** (the HALT / never-run-`/cartographer`
  guard). Its other two clauses already lived in CLAUDE.md's Conventions block. Use this
  form in future prompts:

  ```
  - Read `docs/CODEBASE_MAP.md` first (see CLAUDE.md). If it is MISSING, HALT and tell me to
    run `/cartographer` myself — NEVER run it yourself, it is a ~200k in-session burn.
  ```
