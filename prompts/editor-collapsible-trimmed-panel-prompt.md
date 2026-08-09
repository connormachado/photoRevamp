# Collapsible "Trimmed section" panel

Do NOT commit or push.
Goal: let the middle "trimmed section" panel collapse/expand on demand so it reclaims space when I'm not using it.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- The trimmed-section panel component and its place in the Prompt 6 layout.
- Is a slide-to-edge (side-dock) appropriate, or a simple center show/hide (since it's not on an edge)? Report your recommendation.
Report, propose a plan, and PAUSE.

Implementation:
1. Add a collapse toggle (small control on the panel header/corner). Collapsing hides the panel; the video/review region reclaims the space (respect Prompt 6 no-scroll). Expanding restores it.
2. If it cleanly fits the CollapsiblePanel pattern, reuse it; if a central animated show/hide is cleaner, do that — don't force the side-dock metaphor onto a center panel.
3. Persist state if trivial.

Verification:
- Panel collapses/expands; layout reflows; scrub bar stays visible, no scroll. Build + lint clean.

Save this prompt to prompts/editor-collapsible-trimmed-panel-prompt.md.
