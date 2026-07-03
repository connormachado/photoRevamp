import { useState, useRef, useEffect } from "react";
import { useStats } from "../context/StatsContext";

// A compact popover numberpad that hangs off the delete counter. Lets you log
// several deletions at once ("I just cleared 23 in Photos") instead of tapping
// + twenty-three times. It flows through the same addToDeleteCount → bump →
// /stats/increment path as the +/− buttons, so there's still one persistence path.
export default function BulkAddPad() {
  const { addToDeleteCount } = useStats();
  const [open, setOpen] = useState(false);
  const [entry, setEntry] = useState(""); // digits as a string, no leading zeros
  const wrapRef = useRef(null);

  // Close on outside-click and on Escape while the popover is open.
  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) close();
    }
    function onKey(e) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function close() {
    setOpen(false);
    setEntry("");
  }

  function pressDigit(d) {
    // Drop leading zeros so "007" can't happen.
    setEntry(prev => (prev === "" && d === "0" ? "" : prev + d));
  }

  function backspace() {
    setEntry(prev => prev.slice(0, -1));
  }

  function submit() {
    const n = parseInt(entry, 10);
    if (!Number.isFinite(n) || n <= 0) return; // ignore empty / zero / negative
    addToDeleteCount(n);
    close();
  }

  const keyBtn = {
    height: 40,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#1f1f22",
    color: "#e5e5e5",
    border: "1px solid #2a2a2a",
    borderRadius: 8,
    fontSize: 16,
    fontFamily: "monospace",
    cursor: "pointer",
    userSelect: "none",
  };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <button
        onClick={() => (open ? close() : setOpen(true))}
        aria-label="add many"
        title="Add many at once"
        style={{
          width: 26,
          height: 26,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: open ? "rgba(129,140,248,0.15)" : "#1f1f22",
          color: open ? "#818cf8" : "#888",
          border: "1px solid",
          borderColor: open ? "#818cf8" : "#2a2a2a",
          borderRadius: 7,
          fontSize: 11,
          cursor: "pointer",
          transition: "all 0.15s",
        }}
      >
        {/* chevron that flips when open */}
        <span style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s", lineHeight: 1 }}>▾</span>
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            zIndex: 95,
            background: "rgba(20,20,20,0.98)",
            backdropFilter: "blur(8px)",
            border: "1px solid #2a2a2a",
            borderRadius: 12,
            padding: 10,
            width: 172,
            boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
          }}
        >
          {/* entry display */}
          <div
            style={{
              height: 40,
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              padding: "0 12px",
              marginBottom: 8,
              background: "#141414",
              border: "1px solid #2a2a2a",
              borderRadius: 8,
              fontSize: 22,
              fontWeight: 700,
              fontFamily: "monospace",
              color: entry ? "#f87171" : "#444",
            }}
          >
            {entry || "0"}
          </div>

          {/* 3x4 keypad: 1-9, then ⌫ / 0 / Add */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
            {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map(d => (
              <button key={d} style={keyBtn} onClick={() => pressDigit(d)}>{d}</button>
            ))}
            <button style={keyBtn} onClick={backspace} aria-label="backspace">⌫</button>
            <button style={keyBtn} onClick={() => pressDigit("0")}>0</button>
            <button
              onClick={submit}
              disabled={!entry || parseInt(entry, 10) <= 0}
              style={{
                ...keyBtn,
                background: entry ? "#818cf8" : "#1a1a2a",
                color: entry ? "#fff" : "#555",
                borderColor: entry ? "#818cf8" : "#2a2a2a",
                fontWeight: 700,
                cursor: entry ? "pointer" : "default",
              }}
            >
              Add
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
