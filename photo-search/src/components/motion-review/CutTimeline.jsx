import { useRef, useState } from "react";
import { getType } from "./boundaryTypes";
import { sortRegions } from "./regions";

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
 */
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
}) {
  const total = duration || 1;
  const regs = sortRegions(regions);
  const barRef = useRef(null);
  const draggingRef = useRef(null);        // { id, edge } while dragging a handle
  const scrubbingRef = useRef(false);      // true while dragging the playhead
  const [hoverX, setHoverX] = useState(null);
  const [dragKey, setDragKey] = useState(null); // `${id}-${edge}` for styling
  const [scrubbing, setScrubbing] = useState(false);

  const snap = (t) => (fps ? Math.round(t * fps) / fps : t);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const pct = (t) => `${clamp((t / total) * 100, 0, 100)}%`;
  const minWidth = (reg) => (getType(reg.type).minWidthFrames || 2) / (fps || 30);
  const fmt = (s) => {
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(1).padStart(4, "0");
    return `${m}:${sec}`;
  };

  function timeAtClientX(clientX) {
    const rect = barRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    return { x, t: (x / rect.width) * total };
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

  return (
    <div style={{ marginTop: 4 }}>
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
          height: 44,
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
            const ctx = { selected, pct, fmt, fps, duration: total };
            if (type.renderBlock) return type.renderBlock(seg, ctx);
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
              bottom: "calc(100% + 6px)",
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
              {fmt((hoverX / (barRef.current ? barRef.current.getBoundingClientRect().width : 1)) * total)}
            </div>
          </>
        )}
      </div>

      {/* ruler: start / end */}
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
