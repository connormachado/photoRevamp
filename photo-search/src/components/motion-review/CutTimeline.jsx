import { useRef, useState, useLayoutEffect, useEffect } from "react";
import { getType } from "./boundaryTypes";
import { sortRegions } from "./regions";
import { timeToPixel, pixelToTime, fitZoom, clampZoom, maxZoom, niceTickInterval } from "./timelineScale";

/**
 * Scrub + edit timeline. Untouched footage is clear; each edit REGION is drawn
 * as a block in its own type's colour (see ./boundaryTypes.js — this component
 * never branches on a specific type).
 *
 * Previewing and PLACING are deliberately separate — hovering used to move the
 * playhead, which meant it ended up wherever the pointer left the bar and you
 * could never put a boundary where you actually wanted one:
 *
 * - Hover the bar → previews that frame in the Original panel (onPreview) with a
 *   guide line + timecode tooltip. The playhead does NOT move; leaving the bar
 *   (onPreviewEnd) puts the frame back on the playhead.
 * - Click the bar, or drag the playhead knob → places the playhead there
 *   (onCommit). That's the position `c` / "+ Add" cuts at.
 * - Drag a block's left/right edge handle → edits that boundary (onRegionsChange),
 *   snapped to frames and clamped so it can't invert or cross a neighbour
 *   (Phase 2.5b). The playhead follows the edge being dragged.
 * - Click a block → selects it, so the toolbar / Delete key can remove it.
 *
 * Types get two render slots, and never a special case in here: `renderBlock`
 * draws inside the clipped rounded track, `renderOverlay` draws in the unclipped
 * layer above it (where a type's interactive chrome goes, since a region is
 * often only a few pixels wide). Both receive the same per-region `ctx`.
 *
 * ZOOM: `pxPerSec` is the one time<->pixel scale for the whole component (see
 * ./timelineScale.js); `pct(t)` (kept under its old name — region types read
 * it off `ctx.pct` and only ever use it as a CSS length, so switching it from
 * a "%" to a "px" string is transparent to them) is the only place that maps a
 * timestamp to a screen position, and `timeAtClientX` the only place that maps
 * back. Nothing else may compute a position.
 *
 * The bar is two nested elements, not one, because zooming needs a horizontal
 * scroll container but several children (the playhead knob, edge handles, the
 * hover tooltip, a speed region's overlay chrome) intentionally render outside
 * the bar's own box — CSS clips those the moment their parent's overflow-x
 * becomes non-`visible`. So `viewportRef` (the scroller) is padded tall enough
 * to hold that spillover uncropped, and `barRef` (the actual visible rounded
 * track, sized to the full zoomed width) is the thing that scrolls inside it.
 * All the padding lives below the track, not split above/below — see the
 * comment on VIEWPORT_PAD_TOP/BOTTOM for why that split matters.
 */
const BAR_HEIGHT = 44;
const ZOOM_STEP_FACTOR = 1.4;
const RULER_GAP = 4;     // gap between the track's bottom edge and the ruler
const RULER_HEIGHT = 16; // tick mark + timestamp label
// The tooltip renders BELOW the track and ruler (not above) specifically so
// all of the spillover room lives on one side, below, where nothing else
// sits — the zoom controls are the row directly above. A previous version
// reserved room above too and cancelled it with a negative margin so the bar
// would sit flush under the zoom row; that negative margin pulled the
// (invisible but still hit-testable) scroll viewport up over the zoom row's
// own box, silently eating every click meant for the slider/±buttons. Keeping
// all the spillover on the side with no sibling to collide with avoids that
// class of bug rather than papering over it.
const VIEWPORT_PAD_TOP = 12; // room for the playhead knob's slight overhang above the track
// How far below the track the tooltip starts — past the ruler, with a small gap.
const TOOLTIP_TOP_OFFSET = RULER_GAP + RULER_HEIGHT + 6;
const VIEWPORT_PAD_BOTTOM = TOOLTIP_TOP_OFFSET + 23; // room for the tooltip past the ruler

export default function CutTimeline({
  duration,
  regions,
  fps = 30,
  playhead = 0,
  onCommit,
  onPreview,
  onPreviewEnd,
  onRegionsChange,
  selectedId = null,
  onSelectRegion,
  videoId = null,
}) {
  const total = duration || 1;
  const regs = sortRegions(regions);
  const viewportRef = useRef(null); // horizontally-scrollable window
  const barRef = useRef(null);      // the visible track — scrolls inside viewportRef
  const overviewRef = useRef(null); // the minimap strip, for click/drag-to-jump
  const draggingRef = useRef(null);        // { id, edge } while dragging a handle
  const scrubbingRef = useRef(false);      // true while dragging the playhead
  const [hoverX, setHoverX] = useState(null);
  const [dragKey, setDragKey] = useState(null); // `${id}-${edge}` for styling
  const [scrubbing, setScrubbing] = useState(false);

  const [pxPerSec, setPxPerSec] = useState(1);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);
  const zoomAnchorRef = useRef(null); // { time, screenX } set just before a zoom change

  // A different video (or a duration change on the same one) resets zoom to
  // fit-the-whole-clip — otherwise the previous clip's zoom/pan would carry
  // over onto one it has no relation to.
  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el || !total) return;
    const width = el.getBoundingClientRect().width;
    setViewportWidth(width);
    setPxPerSec(fitZoom(total, width));
    el.scrollLeft = 0;
    setScrollLeft(0);
  }, [total, videoId]);

  // Keeps zoom bounds/overview math current across container resizes, without
  // ever changing the user's chosen zoom on its own.
  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      if (entries[0]) setViewportWidth(entries[0].contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Change zoom while keeping `anchorTime` pinned to the same screen pixel —
  // "zoom into what you're looking at" means the playhead never jumps.
  function requestZoom(nextPxPerSec, anchorTime) {
    const el = viewportRef.current;
    const clamped = clampZoom(nextPxPerSec, total, viewportWidth);
    if (el) {
      const screenX = timeToPixel(anchorTime, pxPerSec) - el.scrollLeft;
      zoomAnchorRef.current = { time: anchorTime, screenX };
    }
    setPxPerSec(clamped);
  }

  useLayoutEffect(() => {
    const anchor = zoomAnchorRef.current;
    const el = viewportRef.current;
    if (!anchor || !el) return;
    zoomAnchorRef.current = null;
    el.scrollLeft = Math.max(0, timeToPixel(anchor.time, pxPerSec) - anchor.screenX);
    setScrollLeft(el.scrollLeft); // read back — the browser clamps to the real scrollable range
  }, [pxPerSec]);

  // Whenever the playhead is placed (click/drag/arrow-keys/edge-drag) outside
  // the visible window, pan just enough to bring it back into view.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const x = timeToPixel(playhead, pxPerSec);
    const margin = 24;
    if (x < el.scrollLeft + margin) {
      el.scrollLeft = Math.max(0, x - margin);
      setScrollLeft(el.scrollLeft);
    } else if (x > el.scrollLeft + el.clientWidth - margin) {
      el.scrollLeft = x - el.clientWidth + margin;
      setScrollLeft(el.scrollLeft);
    }
  }, [playhead, pxPerSec]);

  // Cmd/Ctrl+scroll-wheel zoom. Must be a native (non-React) listener: React
  // attaches `onWheel` as passive, so `preventDefault` inside it is silently
  // ignored and the page/pinch-zoom would fire alongside ours.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    function onWheel(e) {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.01);
      requestZoom(pxPerSec * factor, playhead);
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pxPerSec, playhead, viewportWidth, total]);

  const snap = (t) => (fps ? Math.round(t * fps) / fps : t);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  // Kept under its old name: region types read this off `ctx.pct` and only
  // ever hand it straight to a CSS `left`/`width`, so switching the unit from
  // "%" to "px" is invisible to them.
  const pct = (t) => `${timeToPixel(clamp(t, 0, total), pxPerSec)}px`;
  const minWidth = (reg) => (getType(reg.type).minWidthFrames || 2) / (fps || 30);
  const fmt = (s) => {
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(1).padStart(4, "0");
    return `${m}:${sec}`;
  };

  function timeAtClientX(clientX) {
    const rect = barRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    return { x, t: pixelToTime(x, pxPerSec) };
  }

  function onBarMouseMove(e) {
    if (draggingRef.current || scrubbingRef.current) return; // a drag ≠ a hover
    const { x, t } = timeAtClientX(e.clientX);
    setHoverX(x);
    onPreview && onPreview(t);
  }

  function onBarMouseLeave() {
    setHoverX(null);
    if (!scrubbingRef.current) onPreviewEnd && onPreviewEnd();
  }

  // ── placing / scrubbing the playhead ───────────────────────────────────────
  // Mousedown anywhere on the bar drops the playhead there and starts a scrub;
  // the listeners live on `window` so the drag survives leaving the bar.
  function startScrub(e) {
    if (!onCommit) return;
    e.preventDefault();          // don't let the browser start a text drag-select
    scrubbingRef.current = true;
    setScrubbing(true);
    setHoverX(null);
    onCommit(timeAtClientX(e.clientX).t);
    window.addEventListener("mousemove", onScrubMove);
    window.addEventListener("mouseup", endScrub);
  }

  function onScrubMove(e) {
    if (!scrubbingRef.current || !onCommit) return;
    onCommit(timeAtClientX(e.clientX).t);
  }

  function endScrub() {
    scrubbingRef.current = false;
    setScrubbing(false);
    window.removeEventListener("mousemove", onScrubMove);
    window.removeEventListener("mouseup", endScrub);
  }

  // ── edge dragging ──────────────────────────────────────────────────────────
  function startDrag(id, edge, e) {
    e.stopPropagation();
    e.preventDefault();
    draggingRef.current = { id, edge };
    setDragKey(`${id}-${edge}`);
    window.addEventListener("mousemove", onDragMove);
    window.addEventListener("mouseup", endDrag);
  }

  function onDragMove(e) {
    const drag = draggingRef.current;
    if (!drag || !onRegionsChange) return;
    const { id, edge } = drag;
    const index = regs.findIndex((r) => r.id === id);
    if (index === -1) return;
    const { t } = timeAtClientX(e.clientX);
    const nt = snap(t);
    const seg = regs[index];
    const minW = minWidth(seg);
    let next;
    if (edge === "start") {
      const lo = index > 0 ? regs[index - 1].end : 0;
      const hi = seg.end - minW;
      const start = clamp(nt, lo, hi);
      next = { ...seg, start };
      onCommit && onCommit(start);
    } else {
      const lo = seg.start + minW;
      const hi = index < regs.length - 1 ? regs[index + 1].start : total;
      const end = clamp(nt, lo, hi);
      next = { ...seg, end };
      onCommit && onCommit(end);
    }
    onRegionsChange(regs.map((r) => (r.id === id ? next : r)));
  }

  function endDrag() {
    draggingRef.current = null;
    setDragKey(null);
    window.removeEventListener("mousemove", onDragMove);
    window.removeEventListener("mouseup", endDrag);
  }

  // ── what a type's render slots get to work with ────────────────────────────
  // `onParamsChange` is how a type edits its own region (the speed magnitude,
  // say) without this component knowing what the params mean.
  function updateParams(id, patch) {
    if (!onRegionsChange) return;
    onRegionsChange(regs.map((r) =>
      r.id === id ? { ...r, params: { ...(r.params || {}), ...patch } } : r));
  }

  const blockCtx = (seg) => ({
    selected: selectedId === seg.id,
    pct,
    fmt,
    fps,
    duration: total,
    barHeight: BAR_HEIGHT,
    onParamsChange: updateParams,
    onSelectRegion,
  });

  function Handle({ id, edge, t, color }) {
    const active = dragKey === `${id}-${edge}`;
    return (
      <div
        onMouseDown={(e) => startDrag(id, edge, e)}
        style={{
          position: "absolute",
          top: -4,
          bottom: -4,
          left: pct(t),
          width: 12,
          transform: "translateX(-50%)",
          cursor: "ew-resize",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 3,
        }}
      >
        <div style={{
          width: 4,
          height: "100%",
          borderRadius: 2,
          background: active ? "#fff" : color,
          boxShadow: active ? `0 0 8px ${color}` : "none",
        }} />
      </div>
    );
  }

  // Footer follows the only type in play, so a cut-only timeline reads exactly
  // like it always has ("3 cuts", in red); mixed timelines fall back to "edits".
  const uniform = regs.length && regs.every((r) => r.type === regs[0].type)
    ? getType(regs[0].type) : null;
  const footerColor = uniform ? uniform.color : "#f87171";
  const noun = uniform ? uniform.label.toLowerCase() : "edit";

  const trackWidth = timeToPixel(total, pxPerSec);
  const contentWidth = Math.max(trackWidth, viewportWidth);
  const minPxPerSec = fitZoom(total, viewportWidth);
  const maxPxPerSec = Math.max(minPxPerSec, maxZoom(viewportWidth));
  const canZoomOut = pxPerSec > minPxPerSec + 0.001;
  const canZoomIn = pxPerSec < maxPxPerSec - 0.001;
  // Shown next to the zoom slider — "how far in" relative to the fit-the-whole-
  // clip baseline (1x), since that's the zoom users actually reason from.
  const zoomMultiplier = minPxPerSec > 0 ? pxPerSec / minPxPerSec : 1;
  const zoomLabel = `${zoomMultiplier < 10 ? zoomMultiplier.toFixed(1) : Math.round(zoomMultiplier)}x`;

  // Ruler ticks: only the ones actually in view (plus one on each side), so a
  // long clip zoomed way in doesn't render thousands of offscreen DOM nodes.
  const tickInterval = niceTickInterval(pxPerSec, 70);
  const visibleStart = pixelToTime(scrollLeft, pxPerSec);
  const visibleEnd = pixelToTime(scrollLeft + (viewportWidth || 0), pxPerSec);
  const firstTickIndex = Math.floor(visibleStart / tickInterval) - 1;
  const lastTickIndex = Math.ceil(visibleEnd / tickInterval) + 1;
  const ticks = [];
  for (let i = firstTickIndex; i <= lastTickIndex; i += 1) {
    const t = i * tickInterval;
    if (t < 0 || t > total) continue;
    ticks.push(t);
  }

  const zoomBtnStyle = (enabled) => ({
    width: 20,
    height: 20,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 0,
    borderRadius: 5,
    border: "1px solid #2dd4bf55",
    background: "rgba(10,31,28,0.85)",
    color: enabled ? "#5eead4" : "#3f6f66",
    fontSize: 13,
    fontWeight: 700,
    lineHeight: 1,
    cursor: enabled ? "pointer" : "default",
  });

  function jumpOverview(clientX) {
    const el = viewportRef.current;
    const strip = overviewRef.current;
    if (!el || !strip || trackWidth <= 0) return;
    const rect = strip.getBoundingClientRect();
    const frac = clamp((clientX - rect.left) / rect.width, 0, 1);
    const target = clamp(frac * trackWidth - viewportWidth / 2, 0, Math.max(0, trackWidth - viewportWidth));
    el.scrollLeft = target;
    setScrollLeft(target);
  }
  function onOverviewMouseDown(e) {
    jumpOverview(e.clientX);
    function move(ev) { jumpOverview(ev.clientX); }
    function up() {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  return (
    <div style={{ marginTop: 4 }}>
      {/* zoom: slider + -/+, and Cmd/Ctrl+scroll-wheel over the bar itself */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 10, color: "#4b7a5c", letterSpacing: "0.05em", marginRight: 2 }}>zoom</span>
        <button
          disabled={!canZoomOut}
          onClick={() => requestZoom(pxPerSec / ZOOM_STEP_FACTOR, playhead)}
          title="Zoom out"
          style={zoomBtnStyle(canZoomOut)}
        >
          −
        </button>
        <input
          type="range"
          min={minPxPerSec}
          max={maxPxPerSec}
          step={maxPxPerSec > minPxPerSec ? (maxPxPerSec - minPxPerSec) / 200 : 1}
          value={pxPerSec}
          onChange={(e) => requestZoom(parseFloat(e.target.value), playhead)}
          style={{ width: 110, accentColor: "#2dd4bf" }}
        />
        <button
          disabled={!canZoomIn}
          onClick={() => requestZoom(pxPerSec * ZOOM_STEP_FACTOR, playhead)}
          title="Zoom in"
          style={zoomBtnStyle(canZoomIn)}
        >
          +
        </button>
        <span style={{ fontSize: 11, color: "#5eead4", fontFamily: "monospace", fontWeight: 600, minWidth: 26, textAlign: "right" }}>
          {zoomLabel}
        </span>
      </div>

      {/* Hides the native scrollbar for the viewport below — panning still
          works via trackpad/wheel scroll and the overview strip's click-drag;
          the browser's own scrollbar chrome was redundant with the overview
          strip and rendered as a stark, unstyleable white bar on hover. */}
      <style>{".ct-timeline-viewport::-webkit-scrollbar { display: none; }"}</style>

      {/* horizontally-scrollable window — see the component doc comment for why
          this is padded rather than the thing the bar's own styling lives on */}
      <div
        ref={viewportRef}
        className="ct-timeline-viewport"
        onScroll={(e) => setScrollLeft(e.target.scrollLeft)}
        style={{
          position: "relative",
          overflowX: "auto",
          overflowY: "hidden",
          scrollbarWidth: "none",   // Firefox
          msOverflowStyle: "none",  // legacy Edge
          paddingTop: VIEWPORT_PAD_TOP,
          paddingBottom: VIEWPORT_PAD_BOTTOM,
        }}
      >
      <div
        ref={barRef}
        onMouseMove={onBarMouseMove}
        onMouseLeave={onBarMouseLeave}
        onMouseDown={(e) => {
          onSelectRegion && onSelectRegion(null);
          startScrub(e);
        }}
        style={{
          position: "relative",
          height: BAR_HEIGHT,
          width: contentWidth,
          background: "#0a1f1c",
          border: "1px solid #164e45",
          borderRadius: 8,
          overflow: "visible",
          cursor: onCommit ? "col-resize" : "default",
        }}
      >
        {/* rounded clip for the region blocks only, so handles/tooltip can overflow */}
        <div style={{ position: "absolute", inset: 0, borderRadius: 8, overflow: "hidden" }}>
          {regs.map((seg) => {
            const type = getType(seg.type);
            const selected = selectedId === seg.id;
            if (type.renderBlock) return type.renderBlock(seg, blockCtx(seg));
            return (
              <div
                key={seg.id}
                title={type.describe(seg)}
                onMouseDown={(e) => {
                  e.stopPropagation();
                  onSelectRegion && onSelectRegion(seg.id);
                }}
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  left: pct(seg.start),
                  width: pct(seg.end - seg.start),
                  background: type.fill,
                  boxShadow: selected ? `inset 0 0 0 2px ${type.glyphColor}` : "none",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 10,
                  color: type.glyphColor,
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {type.glyph(seg)}
              </div>
            );
          })}
        </div>

        {/* Type chrome that must escape the clip above — a speed region's
            controls are ~110px wide while the region itself may be ~25px, so
            this layer is unclipped and the cluster is allowed to spill past the
            block's edges. Deliberately rendered BEFORE the handles: at equal
            zIndex the later DOM node wins the hit test, so the edge handles stay
            grabbable even where an overlay covers them. */}
        {regs.map((seg) => getType(seg.type).renderOverlay?.(seg, blockCtx(seg)))}

        {/* drag handles on each region edge (above the clip so they're fully grabbable) */}
        {onRegionsChange && regs.map((seg) => (
          <span key={`h${seg.id}`}>
            <Handle id={seg.id} edge="start" t={seg.start} color={getType(seg.type).color} />
            <Handle id={seg.id} edge="end" t={seg.end} color={getType(seg.type).color} />
          </span>
        ))}

        {/* Playhead — no transition while hovering/scrubbing so it feels instant. */}
        <div style={{
          position: "absolute",
          top: -3,
          bottom: -3,
          left: pct(playhead),
          width: 2,
          background: "#2dd4bf",
          boxShadow: scrubbing ? "0 0 10px #2dd4bf" : "0 0 6px #2dd4bf",
          transition: hoverX == null && !scrubbing ? "left 0.08s linear" : "none",
          pointerEvents: "none",
          zIndex: 2,
        }}>
          <div style={{
            position: "absolute",
            top: -5,
            left: -4,
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: "#2dd4bf",
          }} />
        </div>

        {/* Grab area for the playhead — wider than the 2px line so it's actually
            catchable. zIndex 2 deliberately sits BELOW the edge handles (3):
            dragging a boundary parks the playhead right on that edge, so if this
            won the hit test the edge would become impossible to grab again. */}
        {onCommit && (
          <div
            onMouseDown={(e) => {
              e.stopPropagation();   // keep the current selection while scrubbing
              startScrub(e);
            }}
            title="Drag to move the playhead"
            style={{
              position: "absolute",
              top: -8,
              bottom: -8,
              left: pct(playhead),
              width: 16,
              transform: "translateX(-50%)",
              cursor: scrubbing ? "grabbing" : "grab",
              zIndex: 2,
            }}
          />
        )}

        {/* Hover guide line + timecode tooltip */}
        {hoverX != null && (
          <>
            <div style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: hoverX,
              width: 1,
              background: "rgba(255,255,255,0.5)",
              pointerEvents: "none",
            }} />
            <div style={{
              position: "absolute",
              top: `calc(100% + ${TOOLTIP_TOP_OFFSET}px)`,
              left: hoverX,
              transform: "translateX(-50%)",
              background: "#0a1f1c",
              border: "1px solid #2dd4bf",
              color: "#e5e5e5",
              fontSize: 11,
              fontFamily: "monospace",
              padding: "2px 6px",
              borderRadius: 4,
              whiteSpace: "nowrap",
              pointerEvents: "none",
            }}>
              {fmt(pixelToTime(hoverX, pxPerSec))}
            </div>
          </>
        )}
      </div>

      {/* timestamp ruler — ticks re-densify automatically as pxPerSec changes
          (see niceTickInterval); windowed to the visible range + 1 tick either
          side so a long, deeply-zoomed clip never renders thousands of nodes. */}
      <div style={{ position: "relative", width: contentWidth, height: RULER_HEIGHT, marginTop: RULER_GAP }}>
        {ticks.map((t) => (
          <div key={t} style={{
            position: "absolute",
            top: 0,
            left: pct(t),
            transform: "translateX(-50%)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
          }}>
            <div style={{ width: 1, height: 4, background: "#3f6f66" }} />
            <span style={{ fontSize: 9, color: "#4b7a5c", fontFamily: "monospace", marginTop: 1, whiteSpace: "nowrap" }}>
              {fmt(t)}
            </span>
          </div>
        ))}
      </div>
      </div>

      {/* overview / minimap — always present, not just once zoomed past the
          viewport, so the full-clip context never disappears; at fit zoom the
          highlight simply spans the whole strip (nothing to pan to yet). */}
      <div
        ref={overviewRef}
        onMouseDown={onOverviewMouseDown}
        title="Click or drag to jump around the full clip"
        style={{
          position: "relative",
          height: 8,
          marginTop: 6,
          borderRadius: 4,
          background: "#0a1f1c",
          border: "1px solid #164e4599",
          cursor: "pointer",
        }}
      >
        <div style={{
          position: "absolute",
          top: -1,
          bottom: -1,
          left: `${(scrollLeft / trackWidth) * 100}%`,
          width: `${Math.min(100, (viewportWidth / trackWidth) * 100)}%`,
          borderRadius: 3,
          background: "rgba(45,212,191,0.18)",
          border: "1px solid #2dd4bf",
          pointerEvents: "none",
        }} />
      </div>

      {/* summary footer: start / edit count / end */}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, color: "#4b7a5c", fontSize: 11, fontFamily: "monospace" }}>
        <span>0:00.0</span>
        <span style={{ color: footerColor }}>
          {regs.length} {noun}{regs.length === 1 ? "" : "s"} · drag edges to trim · del to remove
        </span>
        <span>{fmt(total)}</span>
      </div>
    </div>
  );
}
