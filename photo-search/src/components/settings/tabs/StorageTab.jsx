import { useCallback, useEffect, useRef, useState } from "react";
import { useStats } from "../../../context/StatsContext";
import { formatBytes } from "../../motion-review/format";

const API = "http://localhost:5001";

// The first real Settings tab. Shows how much disk the app's own Climb Cutter
// working copies are using (ground-truth, read straight off the uploads dir —
// see backend/storage.py) alongside the two reclaimed-space figures Stats
// already tracks, and offers a guarded bulk purge of those working copies.
export default function StorageTab() {
  const { reclaimedBreakdown, photosReclaimedBytes, refreshStats } = useStats();
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [purging, setPurging] = useState(false);
  const [result, setResult] = useState(null);
  const wrapRef = useRef(null);

  const loadUsage = useCallback(() => {
    setLoading(true);
    fetch(`${API}/motion-review/storage`)
      .then(r => r.json())
      .then(setUsage)
      .catch(() => setUsage(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadUsage(); }, [loadUsage]);

  // Close the confirm on outside-click and Escape, same convention as
  // BulkAddPad / VerdictButtons.
  useEffect(() => {
    if (!confirming) return;
    function onDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setConfirming(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setConfirming(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [confirming]);

  function confirmPurge() {
    setConfirming(false);
    setPurging(true);
    setResult(null);
    fetch(`${API}/motion-review/storage/purge`, { method: "POST" })
      .then(r => r.json())
      .then(r => {
        setResult(r);
        loadUsage();
        refreshStats();
      })
      .catch(() => setResult({ error: true }))
      .finally(() => setPurging(false));
  }

  const b = reclaimedBreakdown || {};

  const row = { display: "flex", justifyContent: "space-between", padding: "10px 0", borderBottom: "1px solid #2a2a2a" };
  const label = { color: "#999", fontSize: 13 };
  const value = { color: "#e5e5e5", fontSize: 13, fontWeight: 700, fontFamily: "monospace" };

  return (
    <div style={{ height: "100%" }}>
      <div style={{ color: "#e5e5e5", fontSize: 15, fontWeight: 700, marginBottom: 14 }}>Storage</div>

      <div style={row}>
        <span style={label}>Working copies stored</span>
        <span style={value}>
          {loading ? "…" : usage ? `${formatBytes(usage.total_bytes)} (${usage.count} video${usage.count === 1 ? "" : "s"})` : "unavailable"}
        </span>
      </div>
      <div style={row}>
        <span style={label}>Reclaimed — Climb Cutter</span>
        <span style={{ ...value, color: "#4ade80" }}>{formatBytes(b.climb_cutter)}</span>
      </div>
      <div style={row}>
        <span style={label}>Reclaimed — Photos</span>
        <span style={{ ...value, color: "#4ade80" }}>{formatBytes(photosReclaimedBytes)}</span>
      </div>

      <div ref={wrapRef} style={{ position: "relative", marginTop: 18 }}>
        {confirming && (
          <div style={{
            position: "absolute",
            bottom: "100%",
            left: 0,
            right: 0,
            marginBottom: 8,
            zIndex: 10,
            padding: 14,
            borderRadius: 12,
            border: "1px solid #2a2a2a",
            background: "rgba(20,20,20,0.98)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
            textAlign: "left",
          }}>
            <div style={{ color: "#e5e5e5", fontSize: 12.5, fontWeight: 600, lineHeight: 1.45 }}>
              This removes those videos from the queue and deletes their stored copies.
              Your originals and your reclaimed-space total are never touched.
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button onClick={() => setConfirming(false)} style={ghostBtn}>Cancel</button>
              <button
                onClick={confirmPurge}
                style={{ ...ghostBtn, border: "1px solid rgba(248,113,113,0.4)", background: "rgba(248,113,113,0.08)", color: "#f87171", fontWeight: 700 }}
              >
                Purge all working copies
              </button>
            </div>
          </div>
        )}
        <button
          onClick={() => setConfirming(true)}
          disabled={purging || !usage || usage.count === 0}
          style={{
            ...ghostBtn,
            width: "100%",
            opacity: purging || !usage || usage.count === 0 ? 0.5 : 1,
            cursor: purging || !usage || usage.count === 0 ? "default" : "pointer",
          }}
        >
          {purging ? "Purging…" : "Purge all working copies"}
        </button>
      </div>

      {result && !result.error && (
        <div style={{ marginTop: 10, color: "#7a7a7a", fontSize: 11.5, lineHeight: 1.5 }}>
          Freed {formatBytes(result.freed_bytes)} across {result.purged} video{result.purged === 1 ? "" : "s"}.
          {result.skipped > 0 && ` Skipped ${result.skipped} — an export was running.`}
        </div>
      )}
      {result && result.error && (
        <div style={{ marginTop: 10, color: "#f87171", fontSize: 11.5 }}>
          Purge failed — try again.
        </div>
      )}
    </div>
  );
}

const ghostBtn = {
  padding: "9px 14px",
  borderRadius: 8,
  border: "1px solid #2a2a2a",
  background: "transparent",
  color: "#999",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  flex: 1,
};
