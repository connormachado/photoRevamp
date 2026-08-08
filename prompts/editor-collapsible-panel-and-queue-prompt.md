# Collapsible side panel + collapse the queue rail

Do NOT commit or push.
Goal: build ONE reusable collapsible-side-panel component (curved pull-tab + flipping chevron) and use it to collapse the left video queue to the edge.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- The queue component (VideoQueue) and how Prompt 6 placed it in the layout.
- The existing "save icon" and general icon/button styling (needed for the tab chevron and for Prompt 8).
- Any existing drawer/panel/collapse pattern to extend rather than reinvent.
Report, propose a plan, and PAUSE. (Read the frontend-design skill before styling the curved tab.)

Implementation:
1. Build a reusable CollapsiblePanel component. Props at least: dock ("left" | "right"), children, defaultOpen, onToggle. Collapsing slides the panel to its dock edge; expanding slides it back. Smooth CSS transition.
2. Pull-tab: a small tab on the panel's inner edge shaped as a slight outward smooth curve (SVG or CSS radius), with a centered chevron. Chevron points INWARD (toward screen center) when the panel is expanded → click collapses it; flips to point OUTWARD (toward the dock edge) when collapsed → click expands it. (Exact arrow direction is a visual detail — confirm on the phase screenshot and flip if it reads wrong.)
3. When collapsed, the panel occupies ~0 width (just the tab peeking) so the middle band reclaims the space — must respect Prompt 6's no-scroll contract.
4. Apply CollapsiblePanel(dock="left") to the queue. Persist open/closed with whatever UI-prefs mechanism the app already uses; if none exists, plain component state is fine — do NOT overengineer a store.

Verification:
- Queue collapses/expands smoothly via the curved tab; the chevron flips direction with state.
- Collapsed queue frees space; scrub bar stays visible, no scroll (Prompt 6 holds).
- Component is genuinely reusable with dock="right" (Prompt 8 will prove it). Build + lint clean.

Save this prompt to prompts/editor-collapsible-panel-and-queue-prompt.md.
