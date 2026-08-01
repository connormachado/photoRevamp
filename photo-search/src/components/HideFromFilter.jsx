import { useEffect, useState } from "react";

// "Hide from this filter" action in the photo detail modal, styled to match
// OpenInPhotosButton and sitting just above it. Purely a display filter —
// dismissing never touches the photo (no /reveal, no delete-counter bump, no
// filesystem write outside dismissed.json).
//
// `onHide` does the actual work (POST /filters/dismiss + removing the photo
// from the grid behind the modal) and is awaited here so the button can show
// its own idle -> loading -> error state. On success `onHidden` closes the
// modal, since the photo it was showing no longer belongs in this view.
export function HideFromFilterButton({ onHide, onHidden }) {
  const [status, setStatus] = useState("idle"); // idle | loading | error
  const [errorMsg, setErrorMsg] = useState("");

  async function handleClick(e) {
    e.stopPropagation(); // don't let the click bubble up and close the modal
    setStatus("loading");
    setErrorMsg("");
    try {
      await onHide();
      onHidden?.();
    } catch (err) {
      setErrorMsg(err.message || "Couldn't hide this photo");
      setStatus("error");
      setTimeout(() => setStatus("idle"), 2800);
    }
  }

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
          background: status === "error" ? "rgba(239,68,68,0.15)" : "#1f1f22",
          color: status === "error" ? "#f87171" : "#e5e5e5",
          border: `1px solid ${status === "error" ? "rgba(239,68,68,0.4)" : "#2a2a2a"}`,
          borderRadius: 8,
          padding: "10px 16px",
          cursor: status === "loading" ? "default" : "pointer",
          fontSize: 13,
          fontWeight: 600,
          transition: "background 0.2s ease, color 0.2s ease",
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
          <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
          <path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
          <line x1="2" y1="2" x2="22" y2="22" />
        </svg>
        {status === "loading" ? "Hiding…" : "Hide from this filter"}
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

// A brief "Hidden from X — Undo" toast, auto-dismissing after ~5s unless
// undone or manually dismissed first.
export function UndoToast({ label, onUndo, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 5000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div style={{
      position: "fixed",
      bottom: 24,
      left: "50%",
      transform: "translateX(-50%)",
      zIndex: 200,
      display: "flex",
      alignItems: "center",
      gap: 12,
      background: "#1f1f22",
      border: "1px solid #2a2a2a",
      borderRadius: 10,
      padding: "10px 16px",
      color: "#e5e5e5",
      fontSize: 13,
      boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
    }}>
      <span>{label}</span>
      <button
        onClick={() => { onUndo(); onDismiss(); }}
        style={{
          background: "transparent",
          border: "none",
          color: "#818cf8",
          fontWeight: 600,
          fontSize: 13,
          cursor: "pointer",
        }}
      >
        Undo
      </button>
    </div>
  );
}
