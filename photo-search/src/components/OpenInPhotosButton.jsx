import { useState } from "react";
import { useStats } from "../context/StatsContext";

const API = "http://localhost:5001";

// A small button shown in the photo modal. Clicking it asks the backend to
// spotlight this photo in Apple Photos.app via its Apple asset UUID.
export default function OpenInPhotosButton({ id }) {
  // One state machine drives the whole button: idle → loading → success | error.
  const [status, setStatus] = useState("idle");
  const [errorMsg, setErrorMsg] = useState("");
  // Opening a photo in Photos is a strong signal the user is about to delete
  // it, so we optimistically bump the delete counter (and its bytes) on success.
  const { incrementDeleteCount } = useStats();

  async function handleClick(e) {
    e.stopPropagation(); // don't let the click bubble up and close the modal
    setStatus("loading");
    setErrorMsg("");
    try {
      const res = await fetch(`${API}/reveal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || "Couldn't show in Photos");
      // The reveal also reports the original's real size (0 if Photos wouldn't
      // say), so this deletion is credited exactly rather than at the average.
      incrementDeleteCount(data.size_bytes);
      setStatus("success");
      setTimeout(() => setStatus("idle"), 1200); // flash green, then reset
    } catch (err) {
      setErrorMsg(err.message || "Couldn't reach the server");
      setStatus("error");
      setTimeout(() => setStatus("idle"), 2800);
    }
  }

  const label =
    status === "loading" ? "Opening…" :
    status === "success" ? "Opened ✓" :
    "Show in Photos";

  const background =
    status === "success" ? "#22c55e" :
    status === "error" ? "rgba(239,68,68,0.15)" :
    "#1f1f22";

  const borderColor = status === "error" ? "rgba(239,68,68,0.4)" : "#2a2a2a";
  const color = status === "success" ? "#000" : status === "error" ? "#f87171" : "#e5e5e5";

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={handleClick}
        disabled={status === "loading"}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
          background,
          color,
          border: `1px solid ${borderColor}`,
          borderRadius: 8,
          padding: "10px 16px",
          cursor: status === "loading" ? "default" : "pointer",
          fontSize: 13,
          fontWeight: 600,
          transition: "background 0.2s ease, color 0.2s ease",
        }}
      >
        {/* simple camera glyph */}
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
          <circle cx="12" cy="13" r="4" />
        </svg>
        {label}
      </button>

      {status === "error" && (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            left: 0,
            right: 0,
            background: "#3a1212",
            border: "1px solid rgba(239,68,68,0.4)",
            color: "#f87171",
            fontSize: 11,
            padding: "6px 10px",
            borderRadius: 6,
            zIndex: 10,
          }}
        >
          {errorMsg}
        </div>
      )}
    </div>
  );
}
