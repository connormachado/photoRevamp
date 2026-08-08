Climb Cutter editor — fixed-viewport layout, no scrolling

Context

The Climb Cutter review room scrolls, and the scrub bar is what falls off the bottom.
That makes it the fixed frame the panels and (future) tool rail snap into — so it has
to stop being a scrolling column.

Today ReviewStage.jsx:243 is one overflowY:auto column containing header → three
video panels → timeline, with CutTimeline as the last child. Everything above it
pushes it below the fold. The thing doing the pushing is maxHeight: "64vh" on each
panel frame (SyncedPanels.jsx:28) — a viewport-relative constant that knows nothing
about the ~400px of fixed chrome around it (title bar 48, stage padding 48, header 72,
hint 21, toolbar 28, timeline ~186). On portrait climbing footage the aspect is
height-bound, so the 64vh cap binds, and 400 + 0.64H > H for any window under ~1100px
tall. Guaranteed scroll.

Everything else is already correct and stays: the root is position:fixed; inset:0
(MotionReviewApp.jsx:392), the body row already has minHeight:0
(MotionReviewApp.jsx:434), and the queue already scrolls internally
(VideoQueue.jsx:79).

Outcome: the editor never scrolls at any supported size; the scrub bar is always
fully visible; the video area is the only thing that gives.

Decisions

- Right tool rail (Prompt 8): a full-height column slot inside the stage's middle
band, right of the panels. Added as an empty, zero-width flexShrink:0 slot with a
comment — nothing renders in it until Prompt 8.
- The scrub band pins to the bottom of the stage, not the full window. It spans
under the rail slot but not under the 280px queue rail. Spanning the whole window
would mean lifting playhead / seekTarget / commitPlayhead out of ReviewStage
up to MotionReviewApp, and motion-review/CLAUDE.md calls that playhead vs
seekTarget split load-bearing for preview smoothness. Out of scope for a layout fix;
easy follow-up if wanted.
- Minimum supported window: 1100 × 720. Below that, the middle band keeps shrinking
(video gets small) and nothing scrolls — the bottom band is flexShrink:0, so the
scrub bar wins every collision by construction. No min-height on the video area, on
purpose: a min there would push the scrubber under overflow:hidden and clip it.

Target structure

MotionReviewApp (position:fixed inset:0, column)   [unchanged]
├── title bar                                       flexShrink:0
└── body row (flex:1, minHeight:0)                  [unchanged]
    ├── queue rail 280px                            [unchanged, already scrolls]
    └── ReviewStage  ← column, overflow:hidden       ★ the change
        ├── header (name, before/after, savings)    flexShrink:0
        ├── middle (flex:1, minHeight:0, ROW)
        │   ├── SyncedPanels  flex:1, minWidth:0
        │   └── tool-rail slot  flexShrink:0, empty
        └── scrub band: hint + toolbar + CutTimeline flexShrink:0  ← always visible

Changes

0. Save the prompt verbatim to prompts/editor-no-scroll-layout-prompt.md
(matches the existing convention in that folder).

1. photo-search/src/components/motion-review/ReviewStage.jsx

- Root (:243): {flex:1, padding:"24px 32px", overflowY:"auto"} →
{flex:1, minWidth:0, minHeight:0, display:"flex", flexDirection:"column", overflow:"hidden", padding:"16px 24px 12px", background:"#0d3d37"}.
The trimmed padding buys ~24px back for video.
- Header block (:245): add flexShrink:0. Keep flexWrap:"wrap" — if it wraps at
narrow widths it only costs video height, never the scrub bar.
- New middle band wrapping <SyncedPanels>: {flex:1, minHeight:0, display:"flex", gap:16} with <SyncedPanels> in a {flex:1, minWidth:0, minHeight:0, display:"flex"}
wrapper, followed by the empty tool-rail slot div (flexShrink:0, width 0, commented
as the Prompt 8 seam).
- Bottom band (:286, currently marginTop:24): → {flexShrink:0, marginTop:16}.
This is the pin. flexShrink:0 on a flex item whose container is overflow:hidden
is what guarantees the scrub bar is never compressed or clipped.
- Hint line (:287): add whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" so that long string can't wrap to 2–3 lines at 1100px and
silently eat video height.
- The empty state (:236) already has flex:1 — leave it.

2. photo-search/src/components/motion-review/SyncedPanels.jsx

- Root (:84): {display:"flex", gap:16, alignItems:"flex-start"} →
{flex:1, minHeight:0, display:"flex", gap:16, alignItems:"stretch"}.
- Panel column (:11): add minHeight:0 alongside the existing flex:1, minWidth:0.
- Panel frame (:22): drop maxHeight:"64vh", add flex:"0 1 auto", minHeight:0.
Keep aspectRatio, width:"100%", margin:"0 auto".
Why this works: in a column flex container the frame's base size is its natural
aspect-derived height (width / aspect); flex-shrink:1 + min-height:0 lets it
shrink to whatever the band actually offers. Width-bound (landscape) clips fit exactly
as they do today; height-bound (portrait) clips letterbox inside the border — which is
already the behaviour at 64vh, so nothing looks different, it just stops overflowing.
- The <video> elements already carry objectFit:"contain" (:45) — no change.

3. photo-search/src/components/motion-review/MotionReviewApp.jsx (two small guards)

- Root (:392): add overflow:"hidden" — belt-and-braces so nothing can ever produce a
scrollbar on the editor root.
- Left rail (:448): add minHeight:0; verdict wrapper (:465) add flexShrink:0.
At 720px with an export progress bar showing, VerdictButtons is tall enough to
squeeze the queue; this makes the queue (which already has overflowY:auto) absorb it.

4. Not touched

- CutTimeline.jsx — no edits. Its internal overflow-x:auto viewport is the zoom pan
and must stay, and its VIEWPORT_PAD_TOP/BOTTOM padding must not be cancelled with a
negative margin (documented click-eating bug in motion-review/CLAUDE.md).
- Scrub, playhead placement, and ←/→ / shift+←/→ stepping — untouched. No changes to
commitPlayhead, seekTarget, or the keydown effect.
- timelineScale.js stays the only place time↔pixel math lives. The ResizeObserver
in CutTimeline (:109) already keeps zoom bounds correct when the band resizes.

Verification

cd photo-search && npm run build && npm run lint    # lint baseline is 10 errors — must not grow
cd .. && make test                                   # pytest + Vitest

Then run the app and check by eye (use /run, or cd photo-search && npm run dev plus
the backend), with a portrait clip selected — that's the case that overflows today:

1. Normal desktop window (~1440×900): whole editor + scrub bar visible, no scrollbar
on the stage. Confirm with document.scrollingElement.scrollHeight and by checking
the stage element's scrollHeight === clientHeight.
2. Drag the window down to 1100×720: panels and video shrink, scrub bar + minimap +
footer all still fully visible, still no scrollbar.
3. Zoom the timeline in/out and pan the minimap — still works, band height unchanged.
4. Click to place the playhead, drag it, hover to preview, then ←/→ and
shift+←/→ — stepping unchanged.
5. Add a boundary with c, drag its edges, Delete to remove — regions still land where
the playhead sits.
6. Switch between a portrait and a landscape clip: both fit, neither introduces scroll.

Finally run /summarize-changes --writeFile for the review summary.
No git commit, no git push at any point.
