# Right tool rail (Rotate/Crop/Filters stubs + Analyze Motion)

Do NOT commit or push.
Goal: a thin, collapsible right tool rail (2 buttons wide, save-icon-sized) holding Rotate/Crop/Filters as disabled stubs plus a working on-demand Analyze Motion button.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Confirm CollapsiblePanel (Prompt 7) exists and supports dock="right".
- The save icon's exact size/styling — the tool buttons must match it.
- How motion analysis is triggered TODAY: does video_motion.py auto-run on ingest, or is there a callable path/endpoint to run it on a given queued video ON DEMAND? If it's auto-run only, expose an on-demand trigger. (This must also work on a video that opened manual-only — see Prompt 5.)
Report, propose a plan, and PAUSE.

Implementation:
1. A right tool rail using CollapsiblePanel(dock="right"): width = 2 buttons + a little padding between them; thin slice.
2. A 2×2 grid of buttons, each the save-icon size:
   - Rotate, Crop, Filters — DISABLED stubs for now (visible, greyed, "coming soon" tooltip). Leave clean hooks so each becomes its own future prompt.
   - Analyze Motion — ENABLED.
3. Analyze Motion: on click, run the motion pass on the current video (reuse the existing pipeline; add a small on-demand endpoint only if none exists), show a spinner/progress, then populate suggested cut boundaries on the timeline. Safe to run on a manual-only-opened video.
4. Respect Prompt 6 no-scroll; collapsing the rail frees space.

Verification:
- Right rail is a thin 2-wide collapsible strip; buttons match the save icon; collapses via the curved tab like the queue.
- Analyze Motion runs the pass and populates suggested cuts; the three stubs are visibly disabled. Build + lint clean.

Save this prompt to prompts/editor-right-tool-rail-prompt.md.
