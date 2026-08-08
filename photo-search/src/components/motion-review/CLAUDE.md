# motion-review/ — preview playback wiring

> Loaded automatically when working under `photo-search/src/components/motion-review/`.
> The render/export side these panels approximate is in `backend/CLAUDE.md`; the
> edit-boundary registry and the "saving IS approving" contract stay in `/CLAUDE.md`.

- **The preview panels approximate speed; the render is the truth.** `SegmentVideo`
  shows a speed region by setting `playbackRate`, which browsers cap at 16 (and which
  drops audio well before that), while a speed magnitude goes to 20. A 20× region
  therefore previews at 16× but exports at a true 20×. Also: seeking a segment panel
  to exactly a piece's `end` instantly trips the end-of-list check in `advance`
  and wraps playback to zero — the `seekTo` mapping parks 0.05s inside the piece to
  avoid it.
- **A seek is the expensive operation in the review room, and two things were doing it
  constantly.** Preview smoothness is governed by seek count × seek cost, not by decode
  throughput. (1) `ReviewStage` keeps `playhead` (moves continuously, drives the
  timeline) SEPARATE from `seekTarget` (`{t, seq}`, bumped only by `commitPlayhead`).
  Feeding the live playhead to the panels made both idle ones run
  `video.currentTime = …` on every `timeupdate` of whichever panel was playing — ~4
  seeks/sec, measured — and that starved the decoder the playing panel was using. The
  panels re-sync once, on a *user* pause, via `onStop` → `commitPlayhead`; `SegmentVideo`'s
  `autoPauseRef` keeps the automatic end-of-list rewind from being reported as one.
  Keyed on `seq` rather than the time so re-placing the playhead where it already sits
  still counts. (2) The preview proxy is encoded with `-g 30`; x264's default keyint of
  250 frames put keyframes ~4.2s apart at 60fps, so every cut skip decoded up to 4s of
  video to land one frame. Measured after: 0 sibling seeks during playback, seam seeks
  resolving in 8–21ms.
- **Crossing a piece boundary only seeks when the pieces are NOT contiguous.**
  `SegmentVideo.advance` compares `next.start` to `seg.end` (`CONTIGUOUS_EPS`): at a
  speed-region edge they are equal, the source runs straight on, and crossing is a
  `playbackRate` change and nothing else. Seeking there re-primed the decoder at a
  position playback had already gone past — a visible jump BACKWARDS, measured at 3
  frames at 1x and 24 at 3.5x (the overshoot scales with the rate). Only a real gap
  (a cut) gets `currentTime = next.start`. Boundary detection runs on
  `requestVideoFrameCallback` — once per presented frame — because `timeupdate`'s ~4/s
  meant noticing a piece had ended up to 250ms late; `timeupdate` is kept as a backstop
  because rVFC stops firing when the tab is hidden and the panel must not then run
  straight through a cut. Reporting the playhead upward stays on `timeupdate`: it
  drives parent state, and 4 renders a second is fine where 60 would not be.
- **A draft-save handler must fold its response back into `videos` state, not just flip a "saved" flag.** `MotionReviewApp.saveDraft` updates the matching video's `regions` in `videos` after a successful `POST /motion-review/draft`, mirroring `runExport`'s pattern. Skip this and switching to another video and back re-seeds `editedRegions` from the stale pre-save snapshot fetched at page load — the save silently looks like a no-op even though the backend persisted it correctly.
- **`ReviewStage` is a fixed-viewport column now (no scroll at any supported size), and the scrub band is deliberately scoped to the stage, not the full window.** It doesn't span under the 280px queue rail. Widening it to the full window would mean lifting `playhead`/`seekTarget` out of `ReviewStage` up to `MotionReviewApp` — don't do that as a casual layout tweak; it directly conflicts with the load-bearing playhead/seekTarget split above. Panel frames (`SyncedPanels.jsx`) size via `flex:"0 1 auto"` + `minHeight:0`, not a `vh`-based cap, so portrait clips letterbox inside the middle band instead of overflowing it.
- **`CutTimeline`'s zoom/pan has one time<->pixel mapping and one failure mode not to repeat.** `timelineScale.js` is the only place that math happens (`timeToPixel`/`pixelToTime`/`fitZoom`/`maxZoom`/`clampZoom`/`niceTickInterval`) — regions stay stored in seconds (`regions.js`), zoom only ever changes where they're drawn. Max zoom is capped so the viewport never shows less than ~3s at once (`MIN_VISIBLE_SECONDS`), not a per-frame pixel budget. The scrollable viewport and the visible track are deliberately two nested elements (`viewportRef` wraps `barRef`): giving an element `overflow-x` other than `visible` forces its `overflow-y` to clip too, which would crop the playhead knob, edge handles, and hover tooltip that intentionally render outside the track's own box, so `viewportRef` reserves padding for that spillover instead. **That padding must never be cancelled with a negative margin to look "flush" against a sibling above** — a previous version did exactly that to close a gap above the zoom controls, and the invisible-but-still-hit-testable viewport ended up sitting on top of the slider/±buttons, silently eating every click. All spillover (including the hover tooltip) now lives below the track, where nothing else competes for clicks.
- **A flex item left `position:static` can render UNDER an absolutely-positioned sibling despite coming later in the DOM.** `CollapsiblePanel.jsx`'s pull-tab button is `display:flex`; its curved background is an absolutely-positioned `<path>` while the chevron on top of it was left as a plain (static) flex item, and the chevron silently rendered invisible — confirmed via `document.elementFromPoint`, not by eyeballing a screenshot. Flex items paint as if `position:relative`, landing in the same z-index:auto stacking layer as an absolute sibling with no guaranteed source-order tiebreak. Fixed with explicit `zIndex` on both layers; don't rely on DOM order to stack a static flex item over an absolutely-positioned one.
