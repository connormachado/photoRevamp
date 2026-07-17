import { useRef, useState } from "react";

/**
 * Scrub + edit timeline. Keep regions are clear; cut regions are red blocks.
 *
 * - Hover the bar → seeks the Original panel (onSeek) to preview that frame,
 *   with a guide line + timecode tooltip (Phase 2.5a).
 * - Drag a red block's left/right edge handle → edits that cut's boundary
 *   (onCutsChange), snapped to frames and clamped so it can't invert or cross a
 *   neighbor (Phase 2.5b). Dragging also previews the boundary frame.
 */
export default function CutTimeline({ duration, cutSegments, fps = 30, playhead = 0, onSeek, onCutsChange }) {
  const total = duration || 1;
  const cuts = cutSegments || [];
  const barRef = useRef(null);
  const draggingRef = useRef(null); // { index, edge } while dragging a handle
  const [hoverX, setHoverX] = useState(null);
  const [dragKey, setDragKey] = useState(null); // `${index}-${edge}` for styling

  const minW = 2 / (fps || 30); // don't let a cut get thinner than ~2 frames
  const snap = (t) => (fps ? Math.round(t * fps) / fps : t);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const pct = (t) => `${clamp((t / total) * 100, 0, 100)}%`;
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
    if (draggingRef.current || !onSeek) return; // dragging a handle ≠ scrubbing
    const { x, t } = timeAtClientX(e.clientX);
    setHoverX(x);
    onSeek(t);
  }

  // ── edge dragging ──────────────────────────────────────────────────────────
  function startDrag(index, edge, e) {
    e.stopPropagation();
    e.preventDefault();
    draggingRef.current = { index, edge };
    setDragKey(`${index}-${edge}`);
    window.addEventListener("mousemove", onDragMove);
    window.addEventListener("mouseup", endDrag);
  }

  function onDragMove(e) {
    const drag = draggingRef.current;
    if (!drag || !onCutsChange) return;
    const { index, edge } = drag;
    const { t } = timeAtClientX(e.clientX);
    const nt = snap(t);
    const seg = cuts[index];
    let next;
    if (edge === "start") {
      const lo = index > 0 ? cuts[index - 1].end : 0;
      const hi = seg.end - minW;
      const start = clamp(nt, lo, hi);
      next = { ...seg, start };
      onSeek && onSeek(start);
    } else {
      const lo = seg.start + minW;
      const hi = index < cuts.length - 1 ? cuts[index + 1].start : total;
      const end = clamp(nt, lo, hi);
      next = { ...seg, end };
      onSeek && onSeek(end);
    }
    onCutsChange(cuts.map((s, i) => (i === index ? next : s)));
  }

  function endDrag() {
    draggingRef.current = null;
    setDragKey(null);
    window.removeEventListener("mousemove", onDragMove);
    window.removeEventListener("mouseup", endDrag);
  }

  function Handle({ index, edge, t }) {
    const active = dragKey === `${index}-${edge}`;
    return (
      <div
        onMouseDown={(e) => startDrag(index, edge, e)}
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
          background: active ? "#fff" : "#f87171",
          boxShadow: active ? "0 0 8px #f87171" : "none",
        }} />
      </div>
    );
  }

  return (
    <div style={{ marginTop: 4 }}>
      <div
        ref={barRef}
        onMouseMove={onBarMouseMove}
        onMouseLeave={() => setHoverX(null)}
        style={{
          position: "relative",
          height: 44,
          background: "#0a1f1c",
          border: "1px solid #164e45",
          borderRadius: 8,
          overflow: "visible",
          cursor: onSeek ? "col-resize" : "default",
        }}
      >
        {/* rounded clip for the cut blocks only, so handles/tooltip can overflow */}
        <div style={{ position: "absolute", inset: 0, borderRadius: 8, overflow: "hidden" }}>
          {cuts.map((seg, i) => (
            <div
              key={i}
              title={`cut ${fmt(seg.start)} → ${fmt(seg.end)}`}
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: pct(seg.start),
                width: pct(seg.end - seg.start),
                background: "rgba(248,113,113,0.35)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 10,
                color: "#fecaca",
                overflow: "hidden",
                whiteSpace: "nowrap",
              }}
            >
              ✂
            </div>
          ))}
        </div>

        {/* drag handles on each cut edge (above the clip so they're fully grabbable) */}
        {onCutsChange && cuts.map((seg, i) => (
          <span key={`h${i}`}>
            <Handle index={i} edge="start" t={seg.start} />
            <Handle index={i} edge="end" t={seg.end} />
          </span>
        ))}

        {/* Playhead — no transition while hovering/dragging so it feels instant. */}
        <div style={{
          position: "absolute",
          top: -3,
          bottom: -3,
          left: pct(playhead),
          width: 2,
          background: "#2dd4bf",
          boxShadow: "0 0 6px #2dd4bf",
          transition: hoverX == null ? "left 0.08s linear" : "none",
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
        <span style={{ color: "#f87171" }}>
          {cuts.length} cut{cuts.length === 1 ? "" : "s"} · drag red edges to trim
        </span>
        <span>{fmt(total)}</span>
      </div>
    </div>
  );
}
