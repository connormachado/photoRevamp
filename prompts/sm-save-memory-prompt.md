# /sm — save durable project memory before /clear

Update project memory before I /clear. Be SELECTIVE, and route each fact to the RIGHT
file. Memory is split across several files and only some load every session, so dumping
everything into root CLAUDE.md is the exact failure mode this command exists to prevent.
When in doubt, write less.

This command is PROJECT-AGNOSTIC: it carries the discipline, but defers to THIS repo's
root CLAUDE.md "Rules for editing this file" block for the specifics — what the
roadmap/changelog file is called (if any), which nested memory files exist, the size
budget. If that block exists, it wins over anything below. Don't hardcode filenames from
one project into another; discover them from the repo.

0. READ THE MEMORY FILES FIRST, before editing anything — you can't dedupe against or
   update-in-place a file you haven't opened.
   - ALWAYS read root CLAUDE.md, including any "Rules for editing this file" block.
   - Also read the nested CLAUDE.md in any folder whose code you touched this session,
     if this repo has them.
   - If shipped/progress work happened, read this repo's roadmap/changelog file, if it
     has one.

1. VERIFY BEFORE YOU WRITE. An auto-loaded line is trusted without being re-checked, so a
   confidently wrong one is worse than no line at all — it can instruct every future
   session to leave a real defect alone. This has already happened here once.
   - Assert nothing you have not just checked against the code THIS session. If you can't
     verify it now, don't write it.
   - When updating an existing line, RE-VERIFY the whole line, not just the clause you came
     to change. Stale neighbours are how a half-true line survives.
   - Before naming a command as a source of truth, RUN IT and confirm its output actually
     covers what you claim. A broken instruction is worse than a stale number.
   - UNVERIFIABLE IS NOT DELETABLE. If you cannot verify a clause in THIS session — it's an
     ffmpeg invariant and you never ran a render, it's a backend contract and you only
     touched frontend — LEAVE IT EXACTLY AS IT IS and flag it to me as unverified. Do not
     delete it, do not soften it, do not reword it to hedge. "I couldn't check this" is not
     evidence against a line. Removing a safety-critical prohibition or a failure contract
     requires MY explicit sign-off, every time.

2. ROUTE each durable fact by WHAT IT IS — not merely "is it stable." Edit IN PLACE
   (update the relevant line/section; do NOT append a dated entry). If the fact already
   exists, REWRITE the stale line — never stack a correction beside it.

   - Gotcha, failure contract, design rationale, a convention that DIFFERS from a tool's
     default, or a safety-critical prohibition -> a CLAUDE.md, SPARINGLY.
       * If it's specific to one folder's code AND that folder has its own CLAUDE.md, it
         goes THERE, not root.
       * Otherwise root CLAUDE.md. Honor its budget. If a new line would blow it, first
         delete something stale or move it into a nested file — the root file does not
         grow unboundedly. Judge the budget in TOKENS, not lines: these files are written
         in long bullets, so a line count is a bad proxy (43 lines of prose can cost as
         much as 180 lines of list). MEASURE IT, don't eyeball it — eyeballing a token
         count is exactly the unverified assertion step 1 forbids. Use the command named in
         the repo's own "Rules for editing this file" block; if it names none, count with
         tiktoken (`cl100k_base` — a proxy that is consistent for comparison, NOT Claude's
         exact tokenizer, so treat it as a growth tracker rather than ground truth) and add
         the command to that block so the next session doesn't have to invent one. Whatever
         command you record must DISCOVER the memory files (glob/find for CLAUDE.md,
         excluding vendor dirs), never enumerate them by path — an enumerated list silently
         stops measuring the next nested file someone adds, which is the exact rot class
         this command guards against.
       * Anything long enough to skim past gets LABELLED SUB-BULLETS, not one paragraph.
         A wall of prose hides the load-bearing clause inside it.
   - Shipped feature / completed phase / progress milestone -> this repo's roadmap or
     changelog file if it has one; otherwise the session log (below). NEVER root CLAUDE.md.
   - Route tables, dependency lists, directory layouts, copied function signatures, repo
     tours, generic best practices -> write NOWHERE. A future session derives these from
     the project's own source, dependency manifests, and file tree.
   - FACTS THAT ROT -> write NOWHERE, or into the roadmap file where they cost no context.
     **The test is whether the statement stops being true when work happens.** Two kinds of
     negative fact look alike and must be routed differently:
       * "X was never built / does not exist" -> **KEEP.** A durable negative fact. Noting
         that a module was never built stops a future session burning a subagent hunting
         for it. Negative space that prevents a fruitless search earns its tokens.
       * "X is pending / not yet run / currently N of M / awaiting the next run" -> roadmap
         file, NEVER root. That's a dated status snapshot: it goes false the moment someone
         does the work, and nothing signals that it has.
     Also rotting, same treatment: exact counts, "N of M" tallies, file/library sizes. They
     read as authoritative long after they stop being true. Two exceptions, both narrow:
       * If a number is genuinely load-bearing, write it WITH an explicit "as of <date>"
         and name the command that re-measures it. Dated facts rot visibly; undated ones
         rot silently.
       * If a RULE depends on a recorded list (e.g. "flag any NEW lint category"), keep the
         list — but regenerate and re-stamp it rather than deleting it, since deleting it
         makes the rule decorative.
   - A REMINDER TO A HUMAN is not documentation -> propose a MECHANISM instead. If the note
     would read "remember to run X" or "don't forget to bump Y", the real fix is a test, a
     hook, a Makefile target, or a derived value. Say so, and flag it as a gap rather than
     wording the reminder more forcefully. Only write the reminder if no mechanism is
     possible, and say why.
   - Session narrative / "what changed today" / decisions-with-rationale -> APPEND to
     docs/session-log.md (create if missing; this file is NOT loaded into context). Give
     each entry a dated heading (e.g. "## 2026-07-27") so history stays scannable.

3. BEFORE FINISHING, re-read what you added to any CLAUDE.md and ask of each line: "does
   this earn being read at the start of EVERY session?" A line only earns it by being a
   gotcha, a failure contract, or a safety-critical prohibition. "It's true" is not
   sufficient — derivable facts belong in the source, navigation belongs in the codebase
   map, narrative belongs in the session log.
   - You may delete, on your own judgement, ONLY these three: derivable facts, navigation,
     and narrative. Everything else you think should go, you PROPOSE — you don't remove.
   - This check applies to what YOU wrote this session. It is not licence to prune lines
     you merely couldn't verify; see step 1. Failing to confirm a line is not the same as
     finding it false, and a prohibition you don't understand is the most dangerous kind
     to cut.

4. If nothing durable happened this session, write NOTHING and just say so.

5. Confirm this task's prompt is saved in prompts/; save it if missing.

6. NEVER git commit or push — I commit manually.

Output: default TERSE — one line per file touched stating what you wrote and where (or
"nothing durable to save"). EXCEPTION: if you edited ANY CLAUDE.md (root OR nested),
ALWAYS print the exact one-line change you made to each, even in terse mode — they get
committed by hand and I want eyes on every memory-file edit. If $ARGUMENTS contains
"verbose" or "-v", print the full summary of all changes. Then tell me it's safe to /clear.
