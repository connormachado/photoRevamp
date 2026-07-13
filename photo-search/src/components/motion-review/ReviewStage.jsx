import { useState } from "react";
import SyncedPanels from "./SyncedPanels";
import CutTimeline from "./CutTimeline";
import { fmtDur, formatBytes } from "./format";

const ACCENT = "#2dd4bf";

// Before → after + how much was removed (green = savings vibe).
function DurationSummary({ original, trimmed, savedBytes }) {
  const removedPct = original > 0 ? Math.round((1 - trimmed / original) * 100) : 0;
  const cell = (label, value, color) => (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 11, color: "#666", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 24 }}>
      {cell("Original", fmtDur(original), "#e5e5e5")}
      <div style={{ fontSize: 24, color: "#444" }}>→</div>
      {cell("Trimmed", fmtDur(trimmed), ACCENT)}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 4, marginLeft: 8 }}>
        <div style={{
          padding: "4px 12px",
          borderRadius: 20,
          background: "rgba(34,197,94,0.12)",
          border: "1px solid rgba(34,197,94,0.4)",
          color: "#22c55e",
          fontSize: 14,
          fontWeight: 700,
        }}>
          −{removedPct}% shorter
        </div>
        {savedBytes > 0 && (
          <span style={{ color: "#22c55e", fontSize: 12, fontWeight: 600 }}>
            ≈ {formatBytes(savedBytes)} saved for this cut
          </span>
        )}
      </div>
    </div>
  );
}

export default function ReviewStage({ video }) {
  const [playhead, setPlayhead] = useState(0);

  if (!video) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555" }}>
        Select a video from the queue to review.
      </div>
    );
  }

  return (
    <div style={{ flex: 1, padding: "24px 32px", overflowY: "auto", background: "#0d3d37" }}>
      {/* Header: filename + before/after + savings */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 600, color: "#e5e5e5" }}>{video.source_name}</div>
          <div style={{ fontSize: 12, color: "#666", fontFamily: "monospace" }}>{video.video_id}</div>
        </div>
        <DurationSummary
          original={video.original_duration}
          trimmed={video.trimmed_duration}
          savedBytes={video.estimated_saved_bytes}
        />
      </div>

      {/* Three synced panels */}
      <SyncedPanels video={video} onTime={setPlayhead} />

      {/* Read-only cut timeline with moving playhead */}
      <div style={{ marginTop: 24 }}>
        <CutTimeline duration={video.original_duration} cutSegments={video.cut_segments} playhead={playhead} />
      </div>
    </div>
  );
}
