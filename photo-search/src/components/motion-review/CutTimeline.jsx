/**
 * Read-only timeline of the whole video: keep regions are clear, cut regions are
 * red blocks positioned/scaled proportionally to their timestamps.
 *
 * Kept as its own component on purpose — Phase 2.5 turns these red blocks into
 * draggable handles without touching anything else in the review room.
 */
export default function CutTimeline({ duration, cutSegments, playhead = 0 }) {
  const total = duration || 1;
  const pct = (t) => `${Math.max(0, Math.min(100, (t / total) * 100))}%`;
  const fmt = (s) => {
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(1).padStart(4, "0");
    return `${m}:${sec}`;
  };

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{
        position: "relative",
        height: 44,
        background: "#141414",
        border: "1px solid #2a2a2a",
        borderRadius: 8,
        overflow: "hidden",
      }}>
        {(cutSegments || []).map((seg, i) => (
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
              borderLeft: "2px solid #f87171",
              borderRight: "2px solid #f87171",
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
        {/* Read-only playhead — leaps across red blocks on the trimmed panel,
            making the skip visible. Phase 2.5 adds draggable edit handles. */}
        <div style={{
          position: "absolute",
          top: -3,
          bottom: -3,
          left: pct(playhead),
          width: 2,
          background: "#2dd4bf",
          boxShadow: "0 0 6px #2dd4bf",
          transition: "left 0.08s linear",
          pointerEvents: "none",
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
      </div>
      {/* ruler: start / end */}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, color: "#555", fontSize: 11, fontFamily: "monospace" }}>
        <span>0:00.0</span>
        <span style={{ color: "#f87171" }}>
          {(cutSegments || []).length} cut{(cutSegments || []).length === 1 ? "" : "s"} · red = removed
        </span>
        <span>{fmt(total)}</span>
      </div>
    </div>
  );
}
