import { useCallback, useEffect, useState } from "react";

const API = "http://localhost:5001";

// Read-only by design: no path field, no file picker, no reindex trigger.
// The escape hatch is editing photo_db/config.json directly (see
// backend/config_store.py) and restarting the server.
export default function PhotosLibraryTab() {
  const [root, setRoot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [result, setResult] = useState(null);

  const loadRoot = useCallback(() => {
    setLoading(true);
    fetch(`${API}/settings/photos-library`)
      .then(r => r.json())
      .then(d => setRoot(d.library_root))
      .catch(() => setRoot(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadRoot(); }, [loadRoot]);

  function runValidate() {
    setValidating(true);
    setResult(null);
    fetch(`${API}/settings/photos-library/validate`, { method: "POST" })
      .then(r => r.json())
      .then(setResult)
      .catch(() => setResult({ error: true }))
      .finally(() => setValidating(false));
  }

  const row = { display: "flex", justifyContent: "space-between", padding: "10px 0", borderBottom: "1px solid #2a2a2a", gap: 16 };
  const label = { color: "#999", fontSize: 13, flexShrink: 0 };
  const value = { color: "#e5e5e5", fontSize: 13, fontWeight: 700, fontFamily: "monospace", wordBreak: "break-all", textAlign: "right" };

  let message = null;
  if (result && !result.error) {
    if (result.valid) {
      message = "Looks good — resources/derivatives and originals were both found.";
    } else if (!result.exists) {
      message = "Nothing found at this path.";
    } else if (!result.is_dir) {
      message = "This path exists but isn't a directory.";
    } else {
      const missing = [];
      if (!result.has_derivatives) missing.push("resources/derivatives");
      if (!result.has_originals) missing.push("originals");
      message = `Missing expected structure: ${missing.join(", ")}.`;
    }
  }

  return (
    <div style={{ height: "100%" }}>
      <div style={{ color: "#e5e5e5", fontSize: 15, fontWeight: 700, marginBottom: 14 }}>Photos Library</div>

      <div style={row}>
        <span style={label}>Library root</span>
        <span style={value}>{loading ? "…" : root || "unavailable"}</span>
      </div>

      <button
        onClick={runValidate}
        disabled={validating || loading}
        style={{
          ...ghostBtn,
          width: "100%",
          marginTop: 18,
          opacity: validating || loading ? 0.5 : 1,
          cursor: validating || loading ? "default" : "pointer",
        }}
      >
        {validating ? "Validating…" : "Validate"}
      </button>

      {message && (
        <div style={{ marginTop: 10, color: result.valid ? "#4ade80" : "#f87171", fontSize: 11.5, lineHeight: 1.5 }}>
          {message}
        </div>
      )}
      {result?.error && (
        <div style={{ marginTop: 10, color: "#f87171", fontSize: 11.5 }}>
          Validation failed — try again.
        </div>
      )}

      <div style={{ marginTop: 18, color: "#7a7a7a", fontSize: 11.5, lineHeight: 1.5 }}>
        This is read-only here. To point the app at a different library, edit{" "}
        <code style={{ fontFamily: "monospace" }}>photo_db/config.json</code>'s{" "}
        <code style={{ fontFamily: "monospace" }}>library_root</code> and restart the server.
      </div>
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
};
