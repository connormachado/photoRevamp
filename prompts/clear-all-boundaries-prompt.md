Do NOT commit or push.
Goal: add a single "Clear all boundaries" control to the Climb Cutter review stage that removes EVERY edit boundary from the current video at once, so I stop deleting them one at a time. It must clear all boundary TYPES generically, and it must never touch the video file.

Step 0 — Inspect and report:
- Consult `docs/CODEBASE_MAP.md` FIRST for where the relevant code lives (routes, modules, state owners, the nav guide) instead of re-exploring — it's navigation; CLAUDE.md stays the authority on gotchas/contracts. If the map is MISSING, HALT and tell me to run `/cartographer` myself — NEVER run `/cartographer` yourself (it's a ~200k in-session burn). If the map is present but seems to contradict the code, trust the code and flag the drift so I can regenerate.
- Open `photo-search/src/components/motion-review/CLAUDE.md` for the timeline's boundary rules (the contiguous-piece rule especially — report whether an empty boundary set is a valid state for it, or whether something assumes at least one boundary exists).
- WHO OWNS BOUNDARY STATE: which component holds the boundaries for the current video, and what the single mutating entry point is. I want the clear to go through the SAME mutation path as removing one boundary, not a separate state reset.
- THE TYPE REGISTRY: report how the shipped edit-boundary type registry enumerates its types. The clear must iterate the registry so every current and FUTURE type is covered. If the registry can't be enumerated, say so — that's a small registry fix and part of this prompt.
- PERSISTENCE: are boundaries persisted per-video anywhere (reviews/, a JSON ledger, localStorage)? If yes, report exactly where, because clearing in-memory only would mean they reappear on reopen. If clearing persisted state is needed, flag it — I want to approve that before it's written.
- Where the control belongs: the Prompt 8 right tool rail is the natural home. Confirm it fits there and respects Prompt 6's no-scroll fixed-viewport contract.
- Does Prompt 24's undo/redo stack exist yet? (I expect not.) Report, because that decides confirm-vs-undo.
Report, propose a plan, and PAUSE.

Implementation:
1. A clear-all action that iterates the boundary type registry and removes every boundary of every type, going through the existing single mutation path. Do NOT enumerate types by name in the call site.
2. Behind a confirm ("Remove all N boundaries from this clip?") — showing the actual count — because there is no undo net yet. Leave the action factored so that when Prompt 24 ships, it can be swapped to an undoable command and the dialog dropped. Note that seam in a comment.
3. If boundaries are persisted per-video, clear the persisted copy too (after my approval in Step 0) so they don't reappear on reopen. Never delete anything other than boundary records.
4. Disable (or hide) the control when the current video has zero boundaries, so it can't fire a pointless confirm.
5. Place it in the tool rail alongside the existing controls; keep it visually quiet — it's destructive-ish, so it should not sit where Approve/Export lives and invite a mis-click.
6. The video file, the working copy, and any original are untouched. This is edit-state only.

Pause points: before clearing any PERSISTED boundary state (show me where it lives first). No file deletion of any kind in this prompt.

Verification:
- Build + lint clean.
- Add several cut boundaries AND at least one speed boundary, hit clear, confirm: all of them vanish, and the timeline renders correctly in the empty state (no crash, no phantom markers).
- Reopen the video: boundaries are still gone (if persisted) or behave as documented.
- Export after clearing produces the full untouched clip; the source file is unmodified.
- With zero boundaries the control is disabled/hidden.

Tests (conditional): SKIP if this ends up pure UI state. DO run /write-tests if it touches persisted boundary state or the registry enumeration — assert the clear removes every type present (including a synthetic third type added in the test, which proves it's type-agnostic rather than hardcoded), that it leaves other videos' boundaries alone, and that it deletes no files.

---

## Step 0 findings (resolved)

- Codebase map present, current, no drift.
- State owner: `MotionReviewApp.jsx` (`editedRegions`), threaded as `regions`/`onRegionsChange` props. Single mutation entry point is `onRegionsChange`; `removeSelected` in `ReviewStage.jsx` and the existing "↺ reset to proposed" button both funnel through it — clear-all does the same.
- Type registry: `boundaryTypes.js` exports `BOUNDARY_TYPES`/`TYPE_LIST`, enumerable via `Object.values`. But `editedRegions` is a flat, type-heterogeneous array (not partitioned by type), so `onRegionsChange([])` already covers every current and future type without inspecting any type id — more strongly type-agnostic than an explicit per-type registry loop. Decision: use the simple `[]` return (see Implementation Decisions below).
- Persistence: boundaries live only in React state until an explicit Save Draft or export/reject. `editedRegions` re-seeds from `videos` state (last-saved snapshot) on video switch regardless of edit type — this is existing behavior for every unsaved edit, not new. Decision: clear-all touches no persisted file; it behaves like any other unsaved edit and requires an explicit Save Draft afterward to stick, exactly like today.
- Tool rail: `ToolRail.jsx`, `dock="right"` in `ReviewStage.jsx`. Fixed-viewport, no-scroll — new control must fit the existing height budget. VerdictButtons (Approve/Export) live in the opposite (left) rail, so ToolRail is already spatially separated from them.
- Undo/redo (Prompt 24): confirmed absent. Confirm-dialog approach is correct.

## Implementation decisions (resolved via user Q&A)

- Empty state: **hide** the control entirely when `regionCount === 0` (not disabled-and-shown).
- Type-agnostic implementation: **simple `[]` return** from a one-line `clearAllRegions(regions)` pure helper in `regions.js` — not an explicit registry loop.
- Confirm popover copies `VerdictButtons.jsx`'s existing pattern (Esc + outside-click, absolutely-positioned panel, Cancel/Clear buttons).
- Tests: skipped — implementation ends up pure UI state, touching neither persisted state nor real registry enumeration.
