import { useStats } from "../context/StatsContext";
import BulkAddPad from "./BulkAddPad";
import { formatBytes } from "./motion-review/format";

// A floating card in the top-right corner showing how many photos have been
// culled and how much space that's projected to reclaim. Both are sourced from
// StatsContext (which persists them to the backend), so manual +/− here, the
// auto-bump from "Show in Photos", and Climb Cutter's video trims all feed the
// same two numbers.
export default function DeleteCounter() {
  const { deleted, reclaimedBytes, reclaimedBreakdown, avgPhotoBytes, incrementDeleteCount, decrementDeleteCount } = useStats();

  // Spell out where the total came from, since two of its three parts are
  // estimates rather than measured bytes.
  const b = reclaimedBreakdown || {};
  const reclaimedDetail = [
    `${formatBytes(b.photos_exact)} — exact photo sizes read from Photos`,
    `${formatBytes(b.photos_estimated)} — estimated, bulk entries at ~${formatBytes(avgPhotoBytes)}/photo`,
    `${formatBytes(b.climb_cutter)} — Climb Cutter, projected if you delete the originals`,
  ].join("\n");

  const btn = {
    width: 34,
    height: 34,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#1f1f22",
    color: "#e5e5e5",
    border: "1px solid #2a2a2a",
    borderRadius: 8,
    fontSize: 20,
    lineHeight: 1,
    cursor: "pointer",
    userSelect: "none",
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 20,
        right: 20,
        zIndex: 90,
        background: "rgba(20,20,20,0.92)",
        backdropFilter: "blur(8px)",
        border: "1px solid #2a2a2a",
        borderRadius: 12,
        padding: "12px 14px",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
        textAlign: "center",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button style={btn} onClick={decrementDeleteCount} aria-label="decrement">−</button>
        <span style={{ minWidth: 48, fontSize: 28, fontWeight: 800, fontFamily: "monospace", color: "#f87171" }}>
          {deleted}
        </span>
        {/* Wrapped, not passed by reference: incrementDeleteCount's first arg is
            the photo's exact size, and a bare handler would hand it the click event. */}
        <button style={btn} onClick={() => incrementDeleteCount()} aria-label="increment">+</button>
        <BulkAddPad />
      </div>
      <div style={{ color: "#666", fontSize: 11, marginTop: 6, textTransform: "uppercase", letterSpacing: 1 }}>
        photos deleted
      </div>
      {/* One reclaimed total across photo culls and Climb Cutter trims. Hover
          for the breakdown — most of it is estimated, so don't imply precision. */}
      <div
        title={reclaimedDetail}
        style={{
          color: "#888",
          fontSize: 11,
          marginTop: 8,
          paddingTop: 8,
          borderTop: "1px solid #2a2a2a",
          letterSpacing: 0.5,
          cursor: "help",
        }}
      >
        <span style={{ color: "#4ade80", fontWeight: 700, fontFamily: "monospace" }}>
          {formatBytes(reclaimedBytes)}
        </span>
        <span style={{ textTransform: "uppercase", letterSpacing: 1 }}> reclaimed*</span>
      </div>
    </div>
  );
}
