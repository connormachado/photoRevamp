import { useState } from "react";

const API = "http://localhost:5001";

// Asks the backend to prune ChromaDB entries whose files no longer exist on
// disk (e.g. deleted in Photos.app), so they stop showing up in search.
export default function SyncButton() {
  const [status, setStatus] = useState("idle"); // idle | loading | done | error
  const [message, setMessage] = useState("");

  async function handleClick() {
    setStatus("loading");
    setMessage("");
    try {
      const res = await fetch(`${API}/cleanup`, { method: "POST" });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      const removed = data.removed ?? 0;
      setMessage(
        removed > 0
          ? `Removed ${removed} stale photo${removed === 1 ? "" : "s"}`
          : "Library already in sync"
      );
      setStatus("done");
      setTimeout(() => setStatus("idle"), 4000);
    } catch (e) {
      setMessage("Sync failed — is the server running?");
      setStatus("error");
      setTimeout(() => setStatus("idle"), 4000);
    }
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <button
        onClick={handleClick}
        disabled={status === "loading"}
        style={{
          padding: "6px 14px",
          borderRadius: 6,
          border: "1px solid #2a2a2a",
          background: "#141414",
          color: status === "loading" ? "#555" : "#aaa",
          fontSize: 13,
          cursor: status === "loading" ? "default" : "pointer",
          whiteSpace: "nowrap",
          transition: "all 0.15s",
        }}
      >
        {status === "loading" ? "Syncing…" : "Sync Library"}
      </button>
      {message && (
        <span
          style={{
            fontSize: 12,
            color: status === "error" ? "#f87171" : "#22c55e",
          }}
        >
          {message}
        </span>
      )}
    </div>
  );
}
