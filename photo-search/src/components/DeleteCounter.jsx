import { useStats } from "../context/StatsContext";

// A floating card in the top-right corner showing how many photos have been
// culled. The count is sourced from StatsContext (which persists it to the
// backend), so manual +/− here and the auto-bump from "Show in Photos" all
// share the same number.
export default function DeleteCounter() {
  const { deleted, incrementDeleteCount, decrementDeleteCount } = useStats();

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
        <button style={btn} onClick={incrementDeleteCount} aria-label="increment">+</button>
      </div>
      <div style={{ color: "#666", fontSize: 11, marginTop: 6, textTransform: "uppercase", letterSpacing: 1 }}>
        photos deleted
      </div>
    </div>
  );
}
