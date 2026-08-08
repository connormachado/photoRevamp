Do NOT commit or push.
Goal: add horizontal zoom (and pan) to the review-stage timeline so I can place cut/speed boundaries frame-accurately on long videos, where the un-zoomed timeline is ~1 second per pixel and a boundary can't land on the frame I mean.

Step 0 — Inspect and report (THE fork is here):
- The review-stage timeline / scrubber components (CutTimeline, ReviewStage, SyncedPanels) and how the playhead + boundary markers are positioned today.
- CRITICAL FORK — are edit boundaries stored as TIMESTAMPS (seconds/frames) or as PIXEL positions / percentages of the current timeline width?
    - If TIMESTAMPS: zoom is clean — it only rescales the time<->pixel mapping and boundaries re-project automatically. Proceed.
    - If PIXELS/PERCENT: STOP and report — boundaries must first migrate to a resolution-independent time base, or zooming will move existing boundaries. Propose that migration as step 1 and pause.
- How arrow-key frame-stepping computes its delta (confirm it's time-based, not pixel-based — it should get far more useful once zoomed).
- Whether there's an existing pan/scroll container to reuse.
Report, propose a plan, and PAUSE. (Read the frontend-design skill before styling the zoom control.)

Implementation (v1 — keep it small):
1. One zoom state: pixelsPerSecond (or a zoom factor). ALL boundary/playhead positions derive from it through a single time->pixel function and its inverse pixel->time. Centralize that mapping so nothing computes positions ad hoc.
2. Zoom control: a slider + "-"/"+" buttons (and, if cheap, Cmd/Ctrl+scroll-wheel over the timeline). Anchor zoom on the PLAYHEAD — the frame under the playhead stays put as you zoom, so you zoom into what you're looking at.
3. When zoomed past the viewport width, the timeline lane becomes horizontally pannable (drag/scroll) with the playhead kept in view; show a small overview/scrollbar so the big picture isn't lost. Respect Prompt 6's no-scroll contract for the EDITOR shell — only the timeline lane pans, the editor itself never grows or scrolls.
4. Do NOT build a frame-thumbnail filmstrip in v1 — that's a decode-heavy fast-follow. Zoom the abstract time ruler + boundary markers only; leave a clean hook for a filmstrip later.
5. Preserve all existing behavior: dragging boundaries, arrow-key stepping, verdict keys — they just operate at the new scale.

Pause points: if Step 0 finds pixel-stored boundaries, pause after the migration proposal before touching anything.

Verification:
- Build + lint clean; scrubbing, boundary drag, and arrow-key stepping all still work.
- On a long clip: zoom in and place a boundary on a specific frame that was unreachable at full zoom-out; zoom back out and the boundary sits at the same moment.
- Zoom is anchored on the playhead (the frame under it doesn't jump); panning keeps the playhead reachable.

Tests (conditional — the time<->pixel mapping is pure math): run /write-tests on the mapping module — time->pixel and pixel->time round-trip at several zoom levels, and a boundary's timestamp stays invariant across zoom changes.
