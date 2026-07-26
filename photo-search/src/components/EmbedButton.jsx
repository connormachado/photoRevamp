import { useEffect, useState } from "react";

const API = "http://localhost:5001";
const POLL_MS = 2000;

// Asks the backend to index any photos in the library that aren't in ChromaDB
// yet. The work happens in a separate process (it takes minutes), so this
// component starts the job and then polls for its progress.
export default function EmbedButton({ onFinished }) {
  const [status, setStatus] = useState({ state: "idle" });
  const [error, setError] = useState("");

  const state = status.state;
  const busy = state === "scanning" || state === "running";

  // One fetch on mount, so a run that was already going before this page loaded
  // (or a refresh in the middle of one) shows up instead of a stale "idle".
  useEffect(() => {
    fetch(`${API}/api/embed/status`)
      .then(r => r.json())
      .then(setStatus)
      .catch(() => {});
  }, []);

  // The polling loop. setInterval keeps firing forever on its own, so useEffect
  // returns a cleanup function that clears it — React runs that cleanup both
  // when this component unmounts and before re-running the effect, which here
  // means the timer stops the moment `busy` flips to false. Without it, the
  // timer would outlive the component and keep hitting the server.
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/embed/status`);
        setStatus(await res.json());
      } catch {
        // Server momentarily busy — the next tick retries.
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [busy]);

  // When a run finishes, hand the fresh library count up so the header updates.
  useEffect(() => {
    if (state === "done" && status.total_in_db && onFinished) {
      onFinished(status.total_in_db);
    }
  }, [state, status.total_in_db, onFinished]);

  async function handleClick() {
    setError("");
    try {
      const res = await fetch(`${API}/api/embed/start`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.reason || "Could not start indexing");
        if (data.status) setStatus(data.status);
        return;
      }
      setStatus(data.status);
    } catch {
      setError("Couldn't reach the server. Is server.py running on port 5001?");
    }
  }

  const { done = 0, total } = status;
  const pct = total ? Math.round((done / total) * 100) : 0;

  let label = "Update Library";
  if (state === "scanning") label = "Scanning…";
  else if (state === "running") label = total ? `Indexing… ${pct}%` : "Indexing…";

  // Status line to the right of the button, mirroring SyncButton's pattern.
  let message = "";
  let messageColor = "#22c55e";
  if (error) {
    message = error;
    messageColor = "#f87171";
  } else if (state === "failed") {
    message = status.error || "Indexing failed";
    messageColor = "#f87171";
  } else if (state === "running" && total) {
    message = `${done.toLocaleString()} / ${total.toLocaleString()}`;
    messageColor = "#818cf8";
  } else if (state === "scanning") {
    message = "Checking for new photos…";
    messageColor = "#818cf8";
  } else if (state === "done") {
    message = done > 0
      ? `Added ${done.toLocaleString()} photo${done === 1 ? "" : "s"}`
      : "Library already up to date";
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <button
        onClick={handleClick}
        disabled={busy}
        style={{
          padding: "6px 14px",
          borderRadius: 6,
          border: "1px solid #2a2a2a",
          background: "#141414",
          color: busy ? "#555" : "#aaa",
          fontSize: 13,
          cursor: busy ? "default" : "pointer",
          whiteSpace: "nowrap",
          transition: "all 0.15s",
        }}
      >
        {label}
      </button>

      {/* Progress bar — only once we know the total to divide by. */}
      {state === "running" && total > 0 && (
        <div style={{
          width: 90,
          height: 4,
          borderRadius: 2,
          background: "#222",
          overflow: "hidden",
        }}>
          <div style={{
            width: `${pct}%`,
            height: "100%",
            background: "#818cf8",
            transition: "width 0.3s",
          }} />
        </div>
      )}

      {message && (
        <span style={{ fontSize: 12, color: messageColor, whiteSpace: "nowrap" }}>
          {message}
        </span>
      )}
    </div>
  );
}
